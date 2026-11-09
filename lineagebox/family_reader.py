"""Reading family facts from writing: an obituary, a death or funeral notice, a birth, wedding or anniversary
announcement, a letter, a certificate, a page of a family Bible, or notes someone types; pasted in, or photos of it.
What it says becomes a list of changes to the family tree, each ticked or not, for someone to check before anything
changes.

1. transcribe: photos of the text are copied out by the vision model, word for word (one request a photo), into the
   text that is then read, which the person can check and correct.
2. extract: the AI lists the people the text names and what it says of each, in the text's own words
   (EXTRACT_SYSTEM). That is all the AI does: it reads. Everything after it is worked out here, where it can be tested.
   A text naming more people than one answer has room for is read in two halves.
3. tidy: dates and places as the catalog keeps them, and what follows from how family notices are written: those
   "preceded in death" died before it was written, "his wife of 65 years" married about 65 years before, a son named
   without a surname has his father's, a married sister was born with her brother's. And what a small model gets
   wrong however it is told, checked against the text where it can be (_from_text, _mend, _in_its_place).
4. match: who in the text is who in the tree. Relatives are found through those already found: "his wife Kathy" is
   the one the tree has as his wife whose names include Kathy, his "son Rob" the son named Rob, however the surnames
   are written. Others are found by name, as a GEDCOM import finds them, and less surely.
5. propose: the changes one by one (a person to add, their names, sex, life events, family links), each saying what
   the tree has now. What is here is never changed silently: where the text and the tree differ, the change is
   offered unticked, with what the tree has. Relatives the tree can't hold without more ("his grandchildren Linda and
   Randy", whose parents aren't named) are listed, not added.
6. apply: the ticked changes, people first, then their names, links and events. Each event's note says where it
   came from.
"""
import hashlib
import io
import json
import re
import sqlite3
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from difflib import SequenceMatcher

import httpx
from PIL import Image, ImageOps

from lineagebox import documents, errors, events, family, gedcom_import
from lineagebox.config import Config

PROMPT_VERSION = 4
MOST_TEXT = 20_000  # characters read at once (in halves when long); an obituary is 300 to 4,000
MOST_PHOTOS = 6
MOST_TICKS = 5_000  # changes ticked or not, kept per reading: a long obituary lists a few hundred
PHOTO_SIZE = 1600   # longest side of a photo of text sent to the model: small print needs more than descriptions
# The answer's room: the model's context is 8,000 tokens with Ollama's usual settings, and the instructions and the
# text take 3,000 to 4,000 of them. An obituary naming 47 people took 3,600.
ANSWER_TOKENS = 4096
SURE, LIKELY, UNSURE = 0.9, 0.8, 0.65  # match scores: sure and likely are chosen; unsure ones are offered, not chosen
# Words with a capital, not starting a sentence: about how many names a text holds. Measured on 21 obituaries, an
# answer takes about 30 tokens for each: the longest that fit ANSWER_TOKENS had 116, and one of 110 didn't.
NAMES_AT_ONCE = 100
_NAME = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z]+")

DOCUMENT_KINDS = ["obituary", "death notice", "funeral notice", "birth announcement", "wedding announcement",
                  "engagement announcement", "anniversary announcement", "letter", "certificate", "family record",
                  "notes", "other"]
EVENT_KINDS = {"worked as": "OCCU", "retired": "RETI", "military service": "MILI", "graduated": "GRAD",
               "studied": "EDUC", "moved": "RESI", "baptized": "BAPM"}
# what small models put among the events however they are told: kept out
_NOT_EVENTS = re.compile(r"\b(?:member|enjoy|love|loved|hobby|hobbies|hunting|fishing|volunteer|fan of|gardening|"
                         r"crochet|church|attended|played|coached|coaching|collect)", re.I)

# What someone can be to someone else in the text, read "person is the <word> of <other>": the words the tree's
# worked-out relationships (family.Kin.words) use for it, those for the other way round, and the link it makes.
_FAMILY = [
    (("husband", "male"), ("wife", "female"), ("spouse", None), ("partner", None), ("fiance", "male"),
     ("fiancee", "female")),
    (("former husband", "male"), ("former wife", "female"), ("former partner", None)),
    (("father", "male"), ("mother", "female"), ("parent", None)),
    (("son", "male"), ("daughter", "female"), ("child", None), ("adopted son", "male"), ("adopted daughter", "female"),
     ("adopted child", None), ("foster child", None)),
    (("brother", "male"), ("sister", "female"), ("sibling", None)),
    (("half-brother", "male"), ("half-sister", "female")),
    (("stepfather", "male"), ("stepmother", "female")),
    (("stepson", "male"), ("stepdaughter", "female"), ("stepchild", None)),
    (("stepbrother", "male"), ("stepsister", "female")),
    (("grandfather", "male"), ("grandmother", "female"), ("grandparent", None)),
    (("grandson", "male"), ("granddaughter", "female"), ("grandchild", None)),
    (("great-grandfather", "male"), ("great-grandmother", "female"), ("great-grandparent", None)),
    (("great-grandson", "male"), ("great-granddaughter", "female"), ("great-grandchild", None)),
    (("great-great-grandchild", None),),
    (("uncle", "male"), ("aunt", "female")),
    (("nephew", "male"), ("niece", "female")),
    (("cousin", None),),
    (("son-in-law", "male"), ("daughter-in-law", "female")),
    (("father-in-law", "male"), ("mother-in-law", "female")),
    (("brother-in-law", "male"), ("sister-in-law", "female")),
    (("other relative", None),),
]
_KEYS = {  # by the first word of each group: what the tree calls it, what the tree calls the other way, the link
    "husband": ({"partner", "spouse", "husband", "wife", "exhusband", "exwife", "exspouse", "expartner"}, "husband",
                "partner"),
    "former husband": ({"former", "exspouse", "expartner", "exhusband", "exwife"}, "former husband", "former"),
    "father": ({"parent", "father", "mother"}, "son", "parent"),
    "son": ({"child", "son", "daughter"}, "father", "child"),
    "brother": ({"sibling", "brother", "sister", "halfbrother", "halfsister", "halfsibling"}, "brother", "sibling"),
    "half-brother": ({"halfsibling", "halfbrother", "halfsister", "sibling", "brother", "sister"}, "half-brother",
                     None),
    "stepfather": ({"stepparent", "stepfather", "stepmother"}, "stepson", None),
    "stepson": ({"stepchild", "stepson", "stepdaughter"}, "stepfather", None),
    "stepbrother": ({"stepsibling", "stepbrother", "stepsister"}, "stepbrother", None),
    "grandfather": ({"grandparent", "grandfather", "grandmother"}, "grandson", None),
    "grandson": ({"grandchild", "grandson", "granddaughter"}, "grandfather", None),
    "great-grandfather": ({"greatgrandparent", "greatgrandfather", "greatgrandmother"}, "great-grandson", None),
    "great-grandson": ({"greatgrandchild", "greatgrandson", "greatgranddaughter"}, "great-grandfather", None),
    "great-great-grandchild": ({"greatgreatgrandchild", "greatgreatgrandson", "greatgreatgranddaughter"}, None, None),
    "uncle": ({"auntoruncle", "aunt", "uncle"}, "nephew", None),
    "nephew": ({"nieceornephew", "niece", "nephew"}, "uncle", None),
    "cousin": ({"cousin"}, "cousin", None),
    "son-in-law": ({"childinlaw", "soninlaw", "daughterinlaw"}, "father-in-law", "child-in-law"),
    "father-in-law": ({"parentinlaw", "fatherinlaw", "motherinlaw"}, "son-in-law", None),
    "brother-in-law": ({"siblinginlaw", "brotherinlaw", "sisterinlaw"}, "brother-in-law", None),
    "other relative": (set(), None, None),
}
KIN = {}  # word -> (group's first word, sex)
for _group in _FAMILY:
    for _word, _sex in _group:
        KIN[_word] = (_group[0][0], _sex)
RELATION_WORDS = list(KIN)

_FACT = {"type": "string"}
_RELATION = {"type": "string", "enum": RELATION_WORDS}
_ABOUT = {"born": _FACT, "born_place": _FACT, "died": _FACT, "died_place": _FACT, "died_at": _FACT,
          "age": {"type": "integer"}, "buried": _FACT, "buried_place": _FACT, "cremated": {"type": "boolean"}}
# Each person carries their own relationship (is, of) rather than a list of its own, and no name besides its parts:
# measured on a 47-person obituary, that is a third fewer tokens, and an answer that fits the model's 8,000.
SCHEMA = {
    "type": "object",
    "required": ["kind", "written", "about", "people", "marriages"],
    "properties": {
        "kind": {"type": "string", "enum": DOCUMENT_KINDS},
        "written": _FACT,
        # The life of the one the text is about, every field to be answered: given among the person's optional
        # fields only, the model left them out for 3 of 16 obituaries, dates of birth and death included.
        "about": {"type": "object", "required": list(_ABOUT), "properties": _ABOUT},
        "people": {"type": "array", "items": {
            "type": "object",
            "required": ["id", "given", "surname", "sex", "living"],
            "properties": {
                "id": {"type": "string"}, "given": _FACT, "nickname": _FACT, "surname": _FACT, "birth_surname": _FACT,
                "suffix": _FACT, "sex": {"type": "string", "enum": ["male", "female", "unknown"]},
                "living": {"type": "string", "enum": ["living", "deceased", "unknown"]},
                "is": _RELATION, "of": {"type": "string"},
                "also": {"type": "array", "items": {"type": "object", "required": ["is", "of"],
                                                    "properties": {"is": _RELATION, "of": {"type": "string"}}}},
                "born": _FACT, "born_place": _FACT, "died": _FACT, "died_place": _FACT, "died_at": _FACT,
                "age": {"type": "integer"}, "buried": _FACT, "buried_place": _FACT, "cremated": {"type": "boolean"},
                "lived_in": _FACT,
                "events": {"type": "array", "items": {
                    "type": "object", "required": ["kind", "what"],
                    "properties": {"kind": {"type": "string", "enum": list(EVENT_KINDS)}, "what": _FACT,
                                   "date": _FACT, "place": _FACT}}},
            }}},
        "marriages": {"type": "array", "items": {
            "type": "object", "required": ["a", "b"],
            "properties": {"a": {"type": "string"}, "b": {"type": "string"}, "date": _FACT, "place": _FACT,
                           "years": {"type": "integer"}}}},
    },
}

EXTRACT_SYSTEM = """You read family history: obituaries, death and funeral notices, birth, wedding and anniversary
announcements, letters, certificates, pages of family Bibles, and notes people type about their family. You record,
as JSON, every family member the text names and what it says about each of them, for a family tree. Someone checks
each fact against the text before it goes in the tree, so record only what the text says. Never guess, and never add
anything you know from elsewhere.

THE TEXT
- kind: what the text is. written: the date it was written, if the text says or makes it plain (for an obituary,
  the date of death).
- about: the birth, death and burial of the person the text is mainly about (p1), each field as described under FOR
  EACH PERSON. Answer every one: "" for a date or place the text doesn't give, 0 for an age it doesn't give, false
  for cremated when it doesn't say.

PEOPLE
- Every family member named, even by a first name only, gets one entry in people, with an id: "p1", "p2" and so on,
  in the order the text names them. p1 is the person the text is mainly about: in an obituary, the one who died.
- One entry per person, however often they are named: Henry L. "Hank" Miller, then "Hank", then "her father Hank"
  are one person. Two people with the same first name (a father and a son both named Thomas) are two entries.
- Leave out people who are not family: pastors and officiants, pallbearers, doctors, the funeral home, friends,
  employers, and people the text only thanks. Leave out relatives who are only counted, with no names ("16
  grandchildren", "many nieces and nephews"); but those named after a count are named: "his 3 grandchildren: Emma,
  Jake and Lily" are three entries.

NAMES
- given: the first and middle names or initials, as written ("Robert Lee", "Henry L.", "Mary Ann"), without
  nicknames, suffixes or titles (Mr., Dr., Rev.).
- nickname: a name in quotation marks ("Hank"), or in parentheses right after the full name of the person the text
  is about ("Robert Miller (Bob)"), or the short name the text calls them by ("Peggy" for Margaret).
- surname: the surname they had when the text was written (a married woman's married name). Leave it empty when the
  text gives no surname for them.
- birth_surname: only a surname from birth that the text gives (a maiden name): in parentheses between the first
  name and the surname ("Ruth (Klein) Baker": Klein), after "nee" or "born" ("Mary Smith, nee Jones"), or the one a
  wife has where her wedding is told ("He married Ann Jones in 1950": her birth_surname is Jones, her surname his).
- suffix: Jr., Sr., II or III, when written.

HOW FAMILIES ARE LISTED
- Relatives are listed for the person whose family it is: in an obituary, the one who died. "His grandsons Tom Carter
  and Ben Miller" are p1's grandsons, whatever their surnames, and "his sons-in-law" are p1's sons-in-law.
- A first name in parentheses inside a relative's name is that relative's husband or wife, sharing the surname:
  "her daughter Susan (David) Carter" is Susan Carter and her husband David Carter, two entries; "her son James (Kim)
  Miller" is James Miller and his wife Kim Miller. After a son's or a brother's name it is his wife; after a
  daughter's or a sister's, her husband.
- A full name in parentheses is a partner with their own surname: "Jane (Tom Baker) Miller" is Jane Miller and her
  partner Tom Baker.
- A surname in parentheses is a maiden name, not a person: in "born to Walter and Ruth (Klein) Baker", Klein is Ruth's.
- Names joined by "and" or "&" share the surname that ends them: "Walter and Ruth (Klein) Baker" are Walter Baker and
  Ruth Baker, born Klein; "Tim & Kate Miller" are Tim Miller and Kate Miller.
- "Survived by" lists those living; "preceded in death by", "predeceased by" and "the late" list those who had died.

RELATIONSHIPS
- is, of: how each person but p1 is related to someone named before them, read "is the <is> of <of>":
  {"is": "daughter", "of": "p1"} is p1's daughter, {"is": "husband", "of": "p5"} is p5's husband. Use the text's word;
  a child, a brother or sister, or a grandchild whose sex the text doesn't give is a "child", "sibling" or
  "grandchild".
- also: any other relationship the text states for them, in the same form. A child of a couple ("Clara, daughter
  of William and Martha", "Joe and Maria had four children") is the child of both: one in is and of, the other in
  also.
- A couple who divorced or separated are each other's "former husband" and "former wife". An engaged couple are each
  other's "fiance" and "fiancee", and an engagement or a wedding still to come is not a marriage.
- Record only the relationships the text states. Never work out others: don't say whose children the grandchildren
  are unless the text says so. When the text lists someone where they seem not to belong (a son-in-law among the
  children), record them as listed.

FOR EACH PERSON
- sex: from the words the text uses for them (husband, daughter, he, her), else from a first name that is plainly a
  man's or a woman's, else "unknown".
- living: "deceased" for the one who died and everyone the text says had died; "living" for those who survive them;
  else "unknown".
- Dates: write each as the text gives it, with its year: "January 21, 1938", "June 1955", "1997". When the text gives
  only a weekday, or a day and month, add the year from the dates around it. Leave a date out when the text doesn't
  give one; never work one out from an age.
- Places: a town with its state or country, the state written out: "Lima, OH" is "Lima, Ohio". A town given without
  its state is in the state of the other places in the text.
- born, born_place: the date and town of birth.
- died, died_place, died_at: the date and town of death, and where in that town they died ("home", "Mercy Hospital").
- age: the age they died at, if the text gives it.
- buried, buried_place: the date of the burial (or of the funeral it followed), and the cemetery with its town ("Oak
  Hill Cemetery, Lakeview, Ohio"). Never a church or a funeral home: leave both out when no cemetery is named.
- cremated: true when the text says they were or will be cremated ("cremation will follow the service").
- lived_in: where they lived when the text was written ("her daughter Susan Carter of Dayton": Dayton, Ohio).
- events: only these facts of their life, each as {kind, what, date, place}: their work ("worked as"), retiring
  ("retired": what = what they retired from), military service, school ("graduated", "studied"), moving ("moved":
  what = where from; place = where to) and baptism. what: a short phrase in the text's words ("dairy farmer on the
  family farm", "the Lima Road Commission", "US Army in Korea"). date: a year or a date, or leave it out ("for many
  years" goes in what). Nothing else is an event: never hobbies, sports, church or club memberships, volunteering,
  or what they enjoyed or were known for.

MARRIAGES
- marriages: each wedding the text tells of, as {a, b, date, place, years}: "She married Thomas Miller on May 5, 1951
  in Lima" is date "May 5, 1951", place "Lima, Ohio". years: how many years they had been married when the text was
  written, if it says ("his wife of 65 years": 65).

EXAMPLE
Text:
Margaret "Peggy" (Baker) Miller, 84, of Lakeview, Ohio, died Tuesday, March 3, 2015 at Mercy Hospital in Lima. She
was born June 2, 1930 in Lima, OH to Walter and Ruth (Klein) Baker. She married Thomas Miller on May 5, 1951; he died
in 2001. She was a school cook for 25 years. She is survived by her children Susan (David) Carter of Dayton and James
(Kim) Miller of Lakeview; her grandchildren Emma and Jake Carter; and her brother Paul Baker. She was preceded in death
by her husband, her parents and her sister Alice. Burial will be at Oak Hill Cemetery, Lakeview.
Answer:
{"kind": "obituary", "written": "March 3, 2015",
"about": {"born": "June 2, 1930", "born_place": "Lima, Ohio", "died": "March 3, 2015", "died_place": "Lima, Ohio", "died_at": "Mercy Hospital", "age": 84, "buried": "", "buried_place": "Oak Hill Cemetery, Lakeview, Ohio", "cremated": false},
"people": [
{"id": "p1", "given": "Margaret", "nickname": "Peggy", "surname": "Miller", "birth_surname": "Baker", "sex": "female", "living": "deceased", "lived_in": "Lakeview, Ohio", "events": [{"kind": "worked as", "what": "school cook for 25 years"}]},
{"id": "p2", "given": "Walter", "surname": "Baker", "sex": "male", "living": "deceased", "is": "father", "of": "p1", "also": [{"is": "husband", "of": "p3"}]},
{"id": "p3", "given": "Ruth", "surname": "Baker", "birth_surname": "Klein", "sex": "female", "living": "deceased", "is": "mother", "of": "p1"},
{"id": "p4", "given": "Thomas", "surname": "Miller", "sex": "male", "living": "deceased", "is": "husband", "of": "p1", "died": "2001"},
{"id": "p5", "given": "Susan", "surname": "Carter", "sex": "female", "living": "living", "is": "daughter", "of": "p1", "lived_in": "Dayton, Ohio"},
{"id": "p6", "given": "David", "surname": "Carter", "sex": "male", "living": "living", "is": "husband", "of": "p5"},
{"id": "p7", "given": "James", "surname": "Miller", "sex": "male", "living": "living", "is": "son", "of": "p1", "lived_in": "Lakeview, Ohio"},
{"id": "p8", "given": "Kim", "surname": "Miller", "sex": "female", "living": "living", "is": "wife", "of": "p7"},
{"id": "p9", "given": "Emma", "surname": "Carter", "sex": "female", "living": "living", "is": "grandchild", "of": "p1"},
{"id": "p10", "given": "Jake", "surname": "Carter", "sex": "male", "living": "living", "is": "grandchild", "of": "p1"},
{"id": "p11", "given": "Paul", "surname": "Baker", "sex": "male", "living": "living", "is": "brother", "of": "p1"},
{"id": "p12", "given": "Alice", "surname": "", "sex": "female", "living": "deceased", "is": "sister", "of": "p1"}],
"marriages": [{"a": "p1", "b": "p4", "date": "May 5, 1951"}]}"""

TRANSCRIBE_SYSTEM = """You copy out the text in a photo of a document: a newspaper clipping, an obituary, a funeral
card, a letter, a certificate, a page of a family Bible, the back of a photo. Copy it word for word, as written, in
reading order (column by column in a newspaper). Keep every name, date and number exactly as written, even where they
look wrong. Leave out what isn't part of the document, such as other articles cut off at the edge. Write [?] for a
word you can't read. If there is no text, answer with an empty text."""
TRANSCRIBE_SCHEMA = {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}}


class Problem(errors.Problem):
    """Something to read that can't be read, or a change that can't be made; the message is for the person."""


class TooLong(Problem):
    """The AI's answer reached ANSWER_TOKENS before it was finished: the text names too many people for one answer."""


# ---- 1, 2. reading -------------------------------------------------------------------------------------------------

def photo_for_model(data: bytes) -> bytes:
    """A photo as the model is sent it: turned upright, at most PHOTO_SIZE on its longest side, as a JPEG."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((PHOTO_SIZE, PHOTO_SIZE))
            out = io.BytesIO()
            im.save(out, "JPEG", quality=90)
            return out.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise Problem("One of the photos can't be opened: send it as a JPEG or PNG.") from None


def transcribe(cfg: Config, photos: list[bytes]) -> str:
    """The text in the photos, one after another. Raises Problem when the model can't look at pictures."""
    from lineagebox.llm import Ollama

    client = Ollama(cfg)
    problem = client.model_problem()
    if problem:
        raise Problem(problem.message)
    parts = []
    for photo in photos:
        answer, _ = _ask(client, TRANSCRIBE_SYSTEM, "Copy out the text in this photo.", TRANSCRIBE_SCHEMA,
                         [photo_for_model(photo)], 3000)
        parts.append(str(answer.get("text") or "").strip())
    return "\n\n".join(p for p in parts if p)


def extract(cfg: Config, text: str, _depth: int = 0) -> dict:
    """The AI's reading of the text (SCHEMA), with how long it took: {'answer', 'seconds', 'tokens'}. A text naming
    more people than one answer has room for (a long obituary names fifty) is read in two halves, each with the
    sentence that names whom it is about, and the halves' answers made one."""
    from lineagebox.llm import Ollama

    text = clean_text(text)
    if not text:
        raise Problem("There is nothing to read: paste the text, or add photos of it.")
    if len(text) > MOST_TEXT:
        raise Problem(f"That is too long to read at once ({len(text):,} characters; up to {MOST_TEXT:,}). Read it in "
                      f"parts, one notice at a time.")
    halves = _halves(text) if _depth < 2 and len(_NAME.findall(text)) > NAMES_AT_ONCE else None
    try:
        if halves:  # plainly too many for one answer: asking would only waste the time an answer takes
            raise TooLong("too many names")
        answer, usage = _ask(Ollama(cfg), EXTRACT_SYSTEM, f"Text:\n{text}\nAnswer:", SCHEMA, [], ANSWER_TOKENS)
    except TooLong:
        halves = halves or _halves(text)
        if halves is None or _depth >= 2:
            raise
        parts = [extract(cfg, half, _depth + 1) for half in halves]
        return {"answer": merge_answers([p["answer"] for p in parts]), "seconds": sum(p["seconds"] for p in parts),
                "tokens": [sum(p["tokens"][0] for p in parts), sum(p["tokens"][1] for p in parts)],
                "parts": len(parts)}
    return {"answer": answer, "seconds": round(usage.seconds, 1), "tokens": [usage.prompt_tokens, usage.output_tokens]}


def _halves(text: str) -> list[str] | None:
    """The text in two, at the sentence nearest its middle; the second half begins with the first sentence too, so
    that it says whom it is about. None for a text too short to cut."""
    sentences = [s for s in re.split(r"(?<=[.;!?])\s+(?=[A-Z\"“(])", text) if s.strip()]
    if len(sentences) < 4:
        return None
    middle, run, cut = len(text) / 2, 0, 1
    for i, s in enumerate(sentences[:-1]):
        run += len(s) + 1
        if run >= middle:
            cut = max(i + 1, 2)
            break
    return [" ".join(sentences[:cut]), " ".join([sentences[0], *sentences[cut:]])]


def merge_answers(answers: list[dict]) -> dict:
    """The answers for the parts of one text, as one: the person it is about (p1 in each) once, with all each part
    says of them; the others renumbered, and someone named in two parts (the same names, related to the same person in
    the same way) once."""
    out = {"kind": next((a.get("kind") for a in answers if a.get("kind")), "other"),
           "written": next((a.get("written") for a in answers if _s(a.get("written"))), ""), "about": {},
           "people": [], "marriages": []}
    for a in answers:  # the life of whom it is about: what either half says
        for key, value in (a.get("about") if isinstance(a.get("about"), dict) else {}).items():
            if value not in (None, "", 0, False) and out["about"].get(key) in (None, "", 0, False):
                out["about"][key] = value
    first: dict = {}
    new_id, count, seen = {}, 1, {}
    for n, a in enumerate(answers):
        for p in a.get("people") or []:
            pid = _s(p.get("id"))
            if pid == "p1":
                for key, value in p.items():
                    if key == "events":
                        first.setdefault("events", []).extend(value or [])
                    elif value not in (None, "", []) and first.get(key) in (None, "", []):
                        first[key] = value
                new_id[(n, pid)] = "p1"
            elif pid:
                count += 1
                new_id[(n, pid)] = f"p{count}"
    out["people"].append({**first, "id": "p1"})
    for n, a in enumerate(answers):
        for p in a.get("people") or []:
            pid = _s(p.get("id"))
            if pid in ("", "p1"):
                continue
            q = {**p, "id": new_id[(n, pid)], "of": new_id.get((n, _s(p.get("of"))), "")}
            q["also"] = [{**r, "of": new_id.get((n, _s(r.get("of"))), "")} for r in p.get("also") or []
                         if isinstance(r, dict)]
            key = (gedcom_import._norm(_s(q.get("given"))).split()[:1], gedcom_import._norm(_s(q.get("surname"))),
                   _s(q.get("is")), q["of"])
            if key[0] and str(key) in seen:  # named in both halves
                new_id[(n, pid)] = seen[str(key)]
                continue
            seen[str(key)] = q["id"]
            out["people"].append(q)
        for m in a.get("marriages") or []:
            pair = (new_id.get((n, _s(m.get("a")))), new_id.get((n, _s(m.get("b")))))
            if all(pair) and not any({x["a"], x["b"]} == set(pair) for x in out["marriages"]):
                out["marriages"].append({**m, "a": pair[0], "b": pair[1]})
    return out


def _ask(client, system: str, user: str, schema: dict, images: list[bytes], max_tokens: int):
    """One question, asked again once (warmer) when the answer isn't whole JSON."""
    from lineagebox.llm import CutOff, OllamaError

    for temperature in (0.0, 0.4):
        try:
            return client.chat_json(system, user, schema, images=images, temperature=temperature,
                                    max_tokens=max_tokens)
        except CutOff:  # asking again gives as long an answer
            raise TooLong("The AI's answer ran too long: it names too many people for one go. Read the text in "
                          "parts (the family list on its own, say).") from None
        except ValueError:
            if temperature:
                raise Problem("The AI's answer didn't make sense, twice. Try again, or with less text.") from None
        except OllamaError as e:
            raise Problem(f"The AI couldn't read it: {e}") from None
        except httpx.HTTPError as e:
            raise Problem(f"The AI isn't answering ({type(e).__name__}). Is the computer it runs on switched on?"
                          ) from None


def clean_text(text: str) -> str:
    """Pasted text as it is read: web pages' doubled quotation marks and broken apostrophes put right, spaces tidied."""
    text = str(text or "").replace("\r\n", "\n").replace('""', '"')
    text = re.sub(r"�\?\?|�", "'", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---- 3. tidying ----------------------------------------------------------------------------------------------------

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming"}
# newspapers' abbreviations (AP style), besides the postal ones
_AP = {"ala": "AL", "ariz": "AZ", "ark": "AR", "calif": "CA", "cal": "CA", "colo": "CO", "conn": "CT", "del": "DE",
       "fla": "FL", "ga": "GA", "ill": "IL", "ind": "IN", "kan": "KS", "kans": "KS", "ky": "KY", "la": "LA",
       "md": "MD", "mass": "MA", "mich": "MI", "minn": "MN", "miss": "MS", "mo": "MO", "mont": "MT", "neb": "NE",
       "nebr": "NE", "nev": "NV", "okla": "OK", "ore": "OR", "oreg": "OR", "pa": "PA", "penn": "PA", "tenn": "TN",
       "tex": "TX", "va": "VA", "vt": "VT", "wash": "WA", "wis": "WI", "wisc": "WI", "wyo": "WY"}
_STATE = {name.lower(): name for name in US_STATES.values()}
_STATE.update({k.lower(): v for k, v in US_STATES.items()})
_STATE.update({k: US_STATES[v] for k, v in _AP.items()})
_USA = {"usa", "us", "u.s.", "u.s.a.", "united states", "united states of america", "america"}
_NO_DATE = {"", "unknown", "none", "null", "n/a", "not given", "not stated"}
_WEEKDAY = re.compile(r"\b(?:mon|tues|wednes|thurs|fri|satur|sun)day\b", re.I)


def _state(word: str) -> str | None:
    return _STATE.get(word.strip().rstrip(".").lower())


def tidy_place(text: str | None, default_state: str | None = None) -> str | None:
    """'Lima, OH' -> 'Lima, Ohio, USA', 'Lima OH' too; 'Lima' with default_state Ohio as well. Others as written."""
    parts = [p.strip() for p in str(text or "").split(",") if p.strip()]
    if parts and parts[-1].lower() in _USA:
        parts.pop()
    if not parts or parts[0].lower() in _NO_DATE:
        return None
    state = None
    if len(parts) > 1 and _state(parts[-1]):
        state = parts[-1] = _state(parts[-1])
    elif (m := re.fullmatch(r"(.+?)\s+([A-Z]{2}|[A-Z][a-z]{1,4}\.)", parts[-1])) and _state(m[2]):
        parts[-1:] = [m[1], state := _state(m[2])]  # "Lima OH", "Lima Mich."
    elif len(parts) == 1 and parts[0].lower() in _STATE and (parts[0].isupper() or len(parts[0]) > 4):
        state = parts[0] = _state(parts[0])  # a state alone
    elif len(parts) == 1 and default_state:
        parts.append(state := default_state)
    return ", ".join(parts + (["USA"] if state else []))[:200]


def tidy_date(text, day_first: bool = False) -> events.Date | None:
    """A date as the AI copied it ('Saturday, January 30, 2021', 'the 5th of July 1955', 'about 1950') as the catalog
    keeps it; a year at least, when that is all that can be made of it. None when there is none, or it is still to
    come."""
    t = str(text or "").strip()
    if t.lower().strip(". ") in _NO_DATE:
        return None
    t = _WEEKDAY.sub(" ", t)
    t = re.sub(r"\b(\d{1,2})(?:st|nd|rd|th)\b", r"\1", t, flags=re.I)
    t = re.sub(r"\b(?:on|the|of|in|sometime)\b", " ", t, flags=re.I)
    t = re.sub(r"\b([A-Za-z]{3,9})\.", r"\1", t)  # "Jan. 5"
    try:
        d = events.parse_date(t, day_first)
    except events.Problem:
        years = re.findall(r"\b(1[0-9]{3}|20[0-9]{2})\b", t)
        d = events.Date(years[0], years[0]) if len(years) == 1 else None
    if d is None or int(d.sort[:4]) > date.today().year or int(d.sort[:4]) < 1000:
        return None
    return d


def _fact(tag: str, d: events.Date | None = None, place: str | None = None, note: str = "", kind: str = "") -> dict:
    return {"tag": tag, "date": d.gedcom if d else None, "sort": d.sort if d else None, "place": place or None,
            "note": " ".join(note.split())[:600] or None, "type": " ".join(kind.split())[:80] or None}


def _s(value) -> str:
    """The AI's text, tidied: '' for nothing ('unknown', 'none' and the like)."""
    text = " ".join(str(value or "").split()).strip(" ,;")
    return "" if text.lower() in _NO_DATE else text


def tidy(answer: dict, day_first: bool = False, text: str = "") -> dict:
    """The AI's answer as the rest of this works with it: names in parts with what was worked out marked, dates and
    places as the catalog keeps them, and the life facts each person's events would be. See the module's step 3.
    text: what it read, to check the answer against (_from_text)."""
    answer = _one_each(answer)
    about_raw = answer.get("about") if isinstance(answer.get("about"), dict) else {}
    people, seen = [], set()
    for raw in answer.get("people") or []:
        x = _s(raw.get("id"))
        if not x or x in seen:
            continue
        if not seen:  # p1: the life facts asked for on their own
            raw = {**raw, **{k: v for k, v in about_raw.items() if v not in (None, "", 0, False)}}
        seen.add(x)
        given = re.sub(r"^(?:mr|mrs|ms|miss|dr|rev|pastor)\.?\s+", "", _s(raw.get("given")), flags=re.I)
        nickname = _s(raw.get("nickname")).strip("\"'“”()")
        if nickname.lower() in given.lower().split():
            nickname = ""  # the AI's nickname that is one of the given names ("Bob" for Bob)
        sex = raw.get("sex") if raw.get("sex") in family.SEXES else None
        p = {"id": x, "name": "", "given": given or _s(raw.get("name")), "surname": _s(raw.get("surname")),
             "birth_surname": _s(raw.get("birth_surname")), "nickname": nickname, "suffix": _s(raw.get("suffix")),
             "sex": sex, "living": {"living": True, "deceased": False}.get(raw.get("living")), "guessed": [],
             "raw": raw}
        p["name"] = _s(raw.get("name")) or shown_name(p) or "?"
        people.append(p)
    by_id = {p["id"]: p for p in people}
    about = people[0]["id"] if people else None
    relatives = []

    def relate(a, word, b):
        word = word.lower()
        if a in by_id and b in by_id and a != b and word in KIN and (a, word, b) not in relatives:
            relatives.append((a, word, b))

    for raw in answer.get("people") or []:  # each person's own relationships
        relate(_s(raw.get("id")), _s(raw.get("is")), _s(raw.get("of")))
        for r in raw.get("also") or []:
            if isinstance(r, dict):
                relate(_s(raw.get("id")), _s(r.get("is")), _s(r.get("of")))
    for r in answer.get("relatives") or []:  # or as a list of their own
        relate(_s(r.get("person")), _s(r.get("is")), _s(r.get("of")))
    text = clean_text(text)
    obituary = answer.get("kind") in ("obituary", "death notice", "funeral notice")
    _as_written(people, text)
    people, relatives = _middle_names(people, relatives, text, about)
    people, relatives = _from_text(people, relatives, text, about)
    relatives = _told_relationships(people, relatives, text, about, answer.get("marriages") or [])
    by_id = {p["id"]: p for p in people}
    wed = {frozenset((_s(m.get("a")), _s(m.get("b")))) for m in answer.get("marriages") or []
           if _wedding_told(text, m)}
    relatives = _mend(by_id, relatives, about, answer.get("kind") in ("obituary", "death notice", "funeral notice"),
                      wed)
    for a, word, b in relatives:  # the words say the sex better than a first name does
        sex = KIN[word][1]
        if sex and not by_id[a]["sex"] and not by_id[a].get("sex_unsure"):
            by_id[a]["sex"] = sex
    _surnames(by_id, relatives)
    for p in people:
        if p["birth_surname"].lower() == p["surname"].lower():
            p["birth_surname"] = ""
        p.pop("sex_unsure", None)

    # the state most places are in, for towns given without one
    states = Counter()
    for raw in answer.get("people") or []:
        for key in ("born_place", "died_place", "buried_place", "lived_in"):
            parts = [s.strip() for s in _s(raw.get(key)).split(",")]
            if len(parts) > 1 and _state(parts[-1] if parts[-1].lower() not in _USA else parts[-2]):
                states[_state(parts[-1] if parts[-1].lower() not in _USA else parts[-2])] += 1
    home = states.most_common(1)[0][0] if states else None
    place = lambda text: tidy_place(text, home)

    written = tidy_date(answer.get("written"), day_first)
    first = by_id.get(about, {}).get("raw", {})
    if about and (d := tidy_date(first.get("died"), day_first)) and answer.get("kind") in (
            "obituary", "death notice", "funeral notice"):
        written = d  # an obituary is about a death, written then
    when = written.sort if written else None

    for p in people:
        raw = p.pop("raw")
        facts = []
        born = tidy_date(raw.get("born"), day_first)
        died = tidy_date(raw.get("died"), day_first)
        age = raw.get("age") if isinstance(raw.get("age"), int) and 0 <= raw.get("age") < 125 else None
        if not born and died and age is not None and p["living"] is False:
            y = int(died.sort[:4]) - age
            born = events.Date(f"BET {y - 1} AND {y}", str(y - 1))
            p["guessed"].append("born")
        if born or _s(raw.get("born_place")):
            facts.append(_fact("BIRT", born, place(raw.get("born_place"))))
        at = re.sub(r"^(?:at\s+)?(?:(?:his|her|their|the family)\s+)?home$", "home",
                    re.sub(r"^at\s+", "", _s(raw.get("died_at")), flags=re.I), flags=re.I)
        died_place, building = _town_of(_s(raw.get("died_place")))
        at = at or building  # "West Branch Medical Center" given as the place
        if died or died_place or at:
            facts.append(_fact("DEAT", died, place(died_place), f"At {at}" if at else ""))
        elif p["living"] is False:
            before = events.Date(f"BEF {written.gedcom}", written.sort) if written and p["id"] != about else None
            facts.append(_fact("DEAT", before))
        buried = tidy_date(raw.get("buried"), day_first)
        buried_place = _s(raw.get("buried_place"))
        if buried_place and not _CEMETERY.search(buried_place) and _CHURCH.search(buried_place):
            buried_place = ""  # the church or funeral home the service was at
        cremated = raw.get("cremated") is True or (p["id"] == about and obituary and re.search(r"\bcremat", text,
                                                                                                re.I))
        if buried_place or (buried and not cremated and (not text or _BURIAL.search(text))):
            facts.append(_fact("BURI", buried, place(buried_place)))
        if cremated:
            facts.append(_fact("CREM"))  # an obituary's cremation is the deceased's
        if _s(raw.get("lived_in")) and p["living"] is not False and written:
            year = events.Date(when[:4], when[:4])
            facts.append(_fact("RESI", year, place(raw.get("lived_in"))))
        for e in raw.get("events") or []:
            kind, what = _s(e.get("kind")).lower(), _s(e.get("what"))
            tag = EVENT_KINDS.get(kind)
            if not tag or not (what or _s(e.get("place"))):
                continue
            if _NOT_EVENTS.search(what) and (tag not in ("OCCU", "RETI") or re.match(r"(?:member|enjoy|lov)", what,
                                                                                    re.I)):
                continue
            if not _in_its_place(text, kind, what, _s(e.get("place"))):
                continue
            d = tidy_date(e.get("date"), day_first)
            if not d and re.search(r"[a-z]", _s(e.get("date")), re.I):
                what = f"{what}, {_s(e.get('date'))}" if what else _s(e.get("date"))  # "for many years"
            town, building = _town_of(_s(e.get("place")))
            if building and building.lower() not in what.lower():
                what = f"{what}, {building}" if what else building  # "Mio High School, Mio" given as the place
            where = place(town)
            if tag == "RESI" and not where and not d:
                continue
            if tag == "RETI" and what and not what.lower().startswith("from"):
                note = f"From {what}"
            elif kind == "moved":
                note = f"Moved {what}" if what else "Moved"
            else:
                note = what[0].upper() + what[1:] if what else ""
            facts.append(_fact(tag, d, where, note if tag != "EVEN" else "", what if tag == "EVEN" else ""))
        p["facts"] = facts
    marriages = []
    for m in answer.get("marriages") or []:
        a, b = _s(m.get("a")), _s(m.get("b"))
        if a not in by_id or b not in by_id or a == b or not _wedding_told(text, m):
            continue
        d = tidy_date(m.get("date"), day_first)
        years = m.get("years") if isinstance(m.get("years"), int) and 0 < m.get("years") < 90 else None
        note = ""
        if not d and years and written:
            d = events.Date(f"ABT {int(when[:4]) - years}", str(int(when[:4]) - years))
            note = f"{years} years of marriage by {events.show_date(written.gedcom)}"
        marriages.append({"a": a, "b": b, **_fact("MARR", d, place(m.get("place")), note)})
    return {"kind": answer.get("kind") if answer.get("kind") in DOCUMENT_KINDS else "other", "about": about,
            "written": written.gedcom if written else None, "written_sort": when, "people": people,
            "relatives": [list(r) for r in relatives], "marriages": marriages}


_LIST_WORD = re.compile(r"\b(?:friends?|sons?|daughters?|children|child|sisters?|brothers?|siblings|grand\w+|"
                        r"great[- ]?\w+|wife|husband|spouse|partner|parents|mother|father|nieces?|nephews?|cousins?|"
                        r"aunts?|uncles?|in-laws?|step\w+)\b", re.I)
_WORD = re.compile(r"[^\W\d_][\w'’-]*")


def _as_written(people: list, text: str) -> None:
    """Names spelled as the text spells them, which a small model 'corrects' however it is told ("bakker" read as
    Baker): a word of a name that the text doesn't have, where the text has a word a letter or two from it, beginning
    with the same letter, is that word; capitalised like a name when the text has it all in one case."""
    if not text:
        return
    written = Counter(_WORD.findall(text))
    lower = {w.lower() for w in written}

    def spelled(word: str) -> str:
        bare = word.rstrip(".")
        if len(bare) < 3 or bare.lower() in lower:
            return word
        ratio, _, best = max(((SequenceMatcher(None, bare.lower(), w.lower()).ratio(), n, w)
                              for w, n in written.items()
                              if w[0].lower() == bare[0].lower() and abs(len(w) - len(bare)) <= 2), default=(0, 0, ""))
        if ratio < 0.8:
            return word
        if best.islower() or best.isupper():  # typed in lower case, or a heading in capitals: O'Brien, Smith-Jones
            best = re.sub(r"(^|['’-])(\w)", lambda m: m[1] + m[2].upper(), best.lower())
        return best + word[len(bare):]

    for p in people:
        was = shown_name(p)
        for key in ("given", "surname", "birth_surname", "nickname"):
            p[key] = " ".join(spelled(w) for w in p[key].split())
        if p["name"] == was:
            p["name"] = shown_name(p)


def _middle_names(people: list, relatives: list, text: str, about: str | None) -> tuple[list, list]:
    """Middle names a small model makes into people of their own ("Wayne Thomas Baker, Karen Ann Baker" read as
    Wayne, Thomas, Karen and Ann): someone the AI lists just after another of the same surname, related to no one
    the other isn't, whose given name follows the other's in the text with the surname after, is the other's middle
    name. Unless the text names someone else by it as well: "Frederick Walter Baker" beside his father "Walter Baker"
    makes one Walter a middle name, not both."""
    if not text:
        return people, relatives
    norm = gedcom_import._norm
    first = lambda p: norm((p["given"].split() or [""])[0])

    def ties(x: str, besides: str) -> set:
        return ({(KIN[w][0], b, 0) for a, w, b in relatives if a == x and b != besides}
                | {(KIN[w][0], a, 1) for a, w, b in relatives if b == x and a != besides})

    out, gone = [], set()
    for p in people:
        q = out[-1] if out else None
        if (q and p["id"] != about and p["given"] and p["surname"] and norm(p["surname"]) == norm(q["surname"])
                and ties(p["id"], q["id"]) <= ties(q["id"], p["id"])):
            run = r"\s+".join(map(re.escape, q["given"].split() + p["given"].split()))
            if re.search(rf"\b{run}(?:\s+(?!and\b|or\b)[^\W\d_][\w.'’-]*){{0,2}}\s+{re.escape(p['surname'])}\b",
                         text, re.I):
                named = len(re.findall(rf"\b{re.escape(p['given'].split()[0])}\b", text, re.I))
                within = len(re.findall(rf"\b{run}\b", text, re.I))
                others = sum(1 for o in people if o is not p and o["id"] not in gone and first(o) == first(p))
                if others <= named - within:
                    was = shown_name(q)
                    q["given"] = f"{q['given']} {p['given']}"
                    for key in ("nickname", "birth_surname", "suffix"):
                        q[key] = q[key] or p[key]
                    if q["name"] == was:
                        q["name"] = shown_name(q)
                    gone.add(p["id"])
                    continue
        out.append(p)
    return out, [r for r in relatives if r[0] not in gone and r[2] not in gone]


def _from_text(people: list, relatives: list, text: str, about: str | None) -> tuple[list, list]:
    """The AI's people checked against the text itself, for what a small model gets wrong however it is told:
    - someone named only in a list of friends ("her dear friends Madonna, June and Anna") is a friend, not family,
      however the AI listed them: left out;
    - a full name in parentheses ("Jane (Tom Baker) Miller") is a partner of their own surname, which the AI gave
      as the relative's;
    - a husband or wife the AI made of a surname ("his sister Joyce Spoor" read as Joyce and her husband, Spoor), with
      no surname, whose given name is their partner's surname: left out."""
    if not text:
        return people, relatives
    norm = gedcom_import._norm
    by_id = {p["id"]: p for p in people}
    drop = set()
    for p in people:
        first = (p["given"].split() or [""])[0].strip(".")
        if len(first) < 2:
            continue
        # a maiden name the AI gave as a nickname ("Luella Mae (Handrich) Esch") or a birthplace ("Adelade Perle
        # (Lewis) Smith" born in Lewis): a name in parentheses just before the surname is one
        raw = p["raw"]
        for key in ("nickname", "born_place"):
            value = _s(p.get(key) if key == "nickname" else raw.get(key)).split(",")[0].strip()
            if value and p["surname"] and re.search(rf"\({re.escape(value)}\)\s+{re.escape(p['surname'])}\b", text):
                p["birth_surname"] = p["birth_surname"] or value
                if key == "nickname":
                    p["nickname"] = ""
                else:
                    p["raw"] = {**raw, "born_place": ""}
        if p["id"] == about:
            continue
        spots = [m.start() for m in re.finditer(rf"\b{re.escape(first)}\b(?!\s+\d)", text)]
        if spots and all(_list_word(text, i) == "friend" for i in spots):
            drop.add(p["id"])
        surname = rf"(?:\s+{re.escape(p['surname'])})?" if p["surname"] else ""
        if re.search(rf"\b{re.escape(first)}{surname}\s*,?\s*(?:an?|his|her|their|the)\s+(?:\w+\s+)?friend\b", text):
            drop.add(p["id"])  # "Kevin Lowe, a friend of the groom"
        given_names = {norm(w) for q in people for w in q["given"].split()[:1]}
        maiden = rf"\b{re.escape(first)}(?:\s+\w\.?|\s+\w+)?\s+\(([A-Z][\w'’-]+)\)\s+{re.escape(p['surname'])}\b"
        m = re.search(maiden, text) if p["surname"] and not p["birth_surname"] else None
        if m and norm(m[1]) not in given_names:  # "Sarah (Nolan) Brennan"; not "Susan (David) Carter"
            p["birth_surname"] = m[1]
        partnered = any(a == p["id"] and KIN[w][0] == "husband" for a, w, b in relatives)
        m = re.search(rf"\(\s*{re.escape(first)}\s+([A-Z][\w'’-]+)\s*\)", text)
        if partnered and m and not (p["surname"] and re.search(rf"\b{re.escape(first)}\s+{re.escape(p['surname'])}\b",
                                                                text)):
            p["surname"] = m[1]
            relatives = [(a, "partner" if a == p["id"] and KIN[w][0] == "husband" else w, b) for a, w, b in relatives]
    for a, w, b in relatives:
        if KIN[w][0] == "husband" and not by_id[a]["surname"] and norm(by_id[a]["given"]) == norm(by_id[b]["surname"]):
            drop.add(a)
    return ([p for p in people if p["id"] not in drop],
            [r for r in relatives if r[0] not in drop and r[2] not in drop])


def _told_relationships(people: list, relatives: list, text: str, about: str | None, marriages: list) -> list:
    """Relationships the text states in so many words, which small models leave out: "Clara, daughter of William and
    Martha"; "Carl and Rita had two sons, Erik and Lars"; "their children" of the couple a notice is about; and "they
    divorced". Only between people the AI named."""
    if not text:
        return relatives
    norm = gedcom_import._norm
    out = list(relatives)
    firsts = {}
    for p in people:
        for name in (p["given"].split()[:1] + p["nickname"].split()[:1]):
            firsts.setdefault(norm(name), []).append(p)
    one = lambda name: firsts[norm(name)][0] if len(firsts.get(norm(name), [])) == 1 else None
    by_id = {p["id"]: p for p in people}
    parents = lambda x: {a for a, w, b in out if b == x and KIN[w][0] == "father"} | {
        b for a, w, b in out if a == x and KIN[w][0] == "son"}
    couples = {frozenset((a, b)) for a, w, b in out if KIN[w][0] in ("husband", "former husband")} | {
        frozenset((_s(m.get("a")), _s(m.get("b")))) for m in marriages}
    divorced: set = set()

    def child_of(child: dict, parent: dict) -> None:
        if parent["id"] != child["id"] and parent["id"] not in parents(child["id"]):
            word = {"male": "son", "female": "daughter"}.get(child["sex"], "child")
            out.append((child["id"], word, parent["id"]))

    of_whom = (r"(?:son|daughter|child)\s+of\s+(?:Mr\.\s+and\s+Mrs\.\s+|Dr\.\s+)?([A-Z][a-z]+)"
               r"(?:\s+(?:and|&)\s+([A-Z][a-z]+))?")
    for p in people:  # "Clara May Foster, daughter of William and Martha"; "Joe Romano was born..., the son of"
        first = (p["given"].split() or [""])[0]
        if not first:
            continue
        name = rf"\b{re.escape(first)}(?:\s+(?:\"\w+\"|[A-Z][\w.'’-]*)){{0,3}}"
        m = re.search(rf"{name},?\s+(?:the\s+)?{of_whom}", text) or re.search(
            rf"(?:^|(?<=[.!?]\s)){name}\b[^.!?]*?,\s+the\s+{of_whom}", text)
        for parent in (m.groups() if m else ()):
            if parent and (q := one(parent)):
                child_of(p, q)
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        pair = None
        if m := re.search(r"\b([A-Z][a-z]+)\s+(?:and|&)\s+([A-Z][a-z]+)\s+(?:had|have|raised)\b", sentence):
            a, b = one(m[1]), one(m[2])
            pair = (a, b) if a and b and frozenset((a["id"], b["id"])) in couples else None
            rest = sentence[m.end():]
        elif m := re.search(r"\b[Tt]heir\s+(?:children|sons|daughters|kids|son|daughter)\b", sentence):
            spouses = [c for c in couples if about in c]
            if len(spouses) == 1 and len(spouses[0]) == 2:
                pair = tuple(by_id[i] for i in sorted(spouses[0], key=lambda i: i != about) if i in by_id)
            rest = sentence[m.end():]
        if pair and len(pair) == 2:
            elders = {a for a, w, b in out if KIN[w][0] in ("father", "grandfather", "great-grandfather")} | {
                b for a, w, b in out if KIN[w][0] in ("son", "grandson", "great-grandson")}
            # a name's first word only: "Owen Patrick Brennan" names Owen, not a Patrick
            names = [m[0] for m in re.finditer(r"\b[A-Z][a-z]+\b", rest)
                     if not re.search(r"[A-Z][a-z]+\s+$", rest[:m.start()])]
            for name in names:
                child = one(name)
                if child and child["id"] not in (pair[0]["id"], pair[1]["id"]) and child["id"] not in elders and (
                        parents(child["id"]) & {pair[0]["id"], pair[1]["id"]} or not parents(child["id"])):
                    if not any(KIN[w][0] == "husband" and child["id"] in (x, y) for x, w, y in out):
                        child_of(child, pair[0])
                        child_of(child, pair[1])
        if re.search(r"\b(?:divorc\w*|separated)\b", sentence, re.I):  # "they divorced in 1975"
            named = {q["id"] for q in people for w in q["given"].split()[:1] if re.search(rf"\b{re.escape(w)}\b",
                                                                                       sentence)}
            for c in couples:
                if len(c) == 2 and c <= named:
                    divorced.add(c)
    for c in divorced:
        a, b = sorted(c)
        out = [(x, w, y) for x, w, y in out if not ({x, y} == c and KIN[w][0] == "husband")]
        for x, y in ((a, b), (b, a)):
            word = {"male": "former husband", "female": "former wife"}.get(by_id[x]["sex"], "former partner")
            out.append((x, word, y))
    current = {frozenset((x, y)) for x, w, y in out if KIN[w][0] == "husband"}
    out = [(x, w, y) for x, w, y in out if not (KIN[w][0] == "former husband" and frozenset((x, y)) in current
                                                 and frozenset((x, y)) not in divorced)]  # both, with no divorce told
    return list(dict.fromkeys(out))


def _list_word(text: str, at: int) -> str:
    """The family word nearest before a place in the text: which list a name there is in ('friend' for friends)."""
    found = list(_LIST_WORD.finditer(text, max(0, at - 160), at))
    word = found[-1][0].lower() if found else ""
    return "friend" if word.startswith("friend") else word


_THE_DECEASEDS = {"grandson", "great-grandson", "great-great-grandchild", "son-in-law"}  # groups, in an obituary


def _mend(by_id: dict, relatives: list, about: str | None, obituary: bool, wed: set) -> list:
    """Put right what small models get wrong however they are told, where the way family notices are written says
    how. Measured on real obituaries (ministral-3, 9B), it:
    - gave grandchildren and sons-in-law to the daughter whose married name they have; in an obituary they are the
      deceased's, as listed;
    - made the husband or wife in "John (Marty) Smith" a husband, from the first name: where the two would then be of
      one sex, one is a guess, so they are each other's spouse, and neither's sex goes in the tree;
    - kept the maiden name a wife is given where her wedding is told ("He married Ann Jones") as her surname: she has
      his, and was born Jones;
    and it names two parents of someone without saying they were a couple: they are taken as husband and wife."""
    out = []
    for a, word, b in relatives:
        pa, pb, group = by_id[a], by_id[b], KIN[word][0]
        if obituary and about and b != about and group in _THE_DECEASEDS:
            b = about
        elif word in ("husband", "wife"):
            said = KIN[word][1]
            sa, sb = pa["sex"], pb["sex"]
            if sa and sb and sa != sb:
                word = "husband" if sa == "male" else "wife"  # their sexes agree: the word follows them
            elif (sa and sb and sa == sb) or (sb == said and not sa):
                word = "spouse"
                for p in (pa, pb):
                    if p["id"] != about:
                        p["sex"], p["sex_unsure"] = None, True
            elif sa and sa != said:
                word = "husband" if sa == "male" else "wife"
        if (a, word, b) not in out:
            out.append((a, word, b))
    parents_of = lambda child: {a for a, w, b in out if b == child and KIN[w][0] == "father"} | {
        b for a, w, b in out if a == child and KIN[w][0] == "son"}
    for child in by_id:  # two parents of one child are a couple too
        two = parents_of(child)
        if len(two) == 2:
            wed = wed | {frozenset(two)}
    for pair in wed:
        if len(pair) != 2 or not pair <= by_id.keys():
            continue
        x, y = (by_id[i] for i in sorted(pair))
        wife, husband = (x, y) if x["sex"] == "female" and y["sex"] == "male" else (
            (y, x) if y["sex"] == "female" and x["sex"] == "male" else (None, None))
        if wife and husband["surname"] and wife["surname"] and not wife["birth_surname"] and (
                gedcom_import._norm(wife["surname"]) != gedcom_import._norm(husband["surname"])):
            wife["birth_surname"], wife["surname"] = wife["surname"], husband["surname"]
            wife["guessed"].append("surname")
    couples = {frozenset((a, b)) for a, w, b in out if KIN[w][0] in ("husband", "former husband")}
    for child in by_id:
        parents = sorted(parents_of(child))
        if len(parents) == 2 and frozenset(parents) not in couples:
            a, b = sorted(parents, key=lambda q: by_id[q]["sex"] != "male")
            out.append((a, "husband" if by_id[a]["sex"] == "male" and by_id[b]["sex"] != "male" else "spouse", b))
            couples.add(frozenset(parents))
    return out


_BUILDING = re.compile(r"\b(?:hospital|medical|health|nursing|hospice|care|rehabilitation|assisted|manor|home|"
                       r"center|centre|industries|company|co|inc|corporation|corp|commission|department|schools?|"
                       r"college|university|academy|factory|mill|plant|store|bank|church|association|services|"
                       r"office|base|station|team)\b", re.I)
_WED = re.compile(r"\b(?:married|marriage|marry|wed|wedding|united in|vows)\b", re.I)
_CEMETERY = re.compile(r"\b(?:cemetery|cemeteries|memorial gardens?|memorial park|burial|graveyard|mausoleum|"
                       r"columbarium)\b", re.I)
_CHURCH = re.compile(r"\b(?:church|chapel|funeral home|parish|temple|synagogue|mosque)\b", re.I)
_BURIAL = re.compile(r"\b(?:buried|burial|interment|interred|entombment|laid to rest|cemetery|graveside)\b", re.I)
_WORK = re.compile(r"\b(?:work\w*|employ\w*|career|jobs?|retir\w*|owned|operated|served|business|profession|"
                   r"trade|occupation|hired)\b", re.I)
_PLEASURE = re.compile(r"\b(?:enjoy\w*|lov\w*|hobb\w*|volunteer\w*|involved|member|fan|passion|pastime|liked|"
                       r"delighted|avid|supporter)\b", re.I)
_MOVED = re.compile(r"\b(?:moved|moving|relocated|came to|settled)\b", re.I)


def _in_its_place(text: str, kind: str, what: str, where: str) -> bool:
    """Whether the text tells of a life event as the AI gives it. Small models list hobbies and volunteering as
    work ("avid crocheter", "coordinating fellowship dinners", after "she enjoyed"), and make a move of "he was a
    driver for the Lima School"; told by the words before it in the text, the nearer of work and pleasure."""
    if not text or kind not in ("worked as", "moved"):
        return True
    if kind == "worked as" and _PLEASURE.match(what):
        return False
    words = re.findall(r"[A-Za-z]{3,}", what if kind == "worked as" else (where or what))[:2]
    if not words:
        return True
    m = re.search(r"\b" + r"\W+(?:\w+\W+){0,3}".join(map(re.escape, words)), text, re.I)
    if not m:
        return True  # not as written: left for the person to judge
    before = text[max(0, m.start() - 120):m.start()]
    if kind == "moved":
        return bool(_MOVED.search(text[max(0, m.start() - 80):m.end() + 40]))
    last = lambda pattern: max((x.end() for x in pattern.finditer(before)), default=-1)
    return last(_PLEASURE) <= last(_WORK)


def _one_each(answer: dict) -> dict:
    """The answer with anyone listed twice (in two halves of a long text, or twice in one) listed once: the same
    first name and surname (or none), related to the same person in the same way, or not related at all."""
    people = [p for p in answer.get("people") or [] if isinstance(p, dict)]
    norm = gedcom_import._norm
    kept, same = [], {}
    for p in people:
        pid = _s(p.get("id"))
        first = norm(_s(p.get("given"))).split()[:1]
        twin = None
        if pid != "p1" and first:
            for q in kept:
                q_surname, p_surname = norm(_s(q.get("surname"))), norm(_s(p.get("surname")))
                rel_p = (KIN.get(_s(p.get("is")).lower(), ("",))[0], same.get(_s(p.get("of")), _s(p.get("of"))))
                rel_q = (KIN.get(_s(q.get("is")).lower(), ("",))[0], _s(q.get("of")))
                if (_s(q.get("id")) != "p1" and norm(_s(q.get("given"))).split()[:1] == first
                        and (not q_surname or not p_surname or q_surname == p_surname)
                        and (rel_p == rel_q or not rel_p[0] or not rel_q[0])):
                    twin = q
                    break
        if twin is None:
            kept.append(dict(p))
            continue
        same[pid] = _s(twin.get("id"))
        for key, value in p.items():
            if key == "also":
                twin["also"] = [*(twin.get("also") or []), *(value or [])]
            elif value not in (None, "", []) and twin.get(key) in (None, "", []):
                twin[key] = value
    if not same:
        return answer
    point = lambda x: same.get(_s(x), _s(x))
    for p in kept:
        p["of"] = point(p.get("of"))
        p["also"] = [{**r, "of": point(r.get("of"))} for r in p.get("also") or [] if isinstance(r, dict)]
    marriages = [{**m, "a": point(m.get("a")), "b": point(m.get("b"))} for m in answer.get("marriages") or []]
    return {**answer, "people": kept, "marriages": marriages}


def _town_of(place: str) -> tuple[str, str]:
    """A place the AI gave as (town, building): 'Mercy Hospital, Lima, Ohio' -> ('Lima, Ohio', 'Mercy Hospital');
    'West Branch Medical Center, Michigan' -> ('', 'West Branch Medical Center'), the town not being given."""
    parts = [p.strip() for p in place.split(",") if p.strip()]
    if not parts or not _BUILDING.search(parts[0]):
        return place, ""
    rest = parts[1:]
    town = ", ".join(rest) if rest and not (len(rest) == 1 and (_state(rest[0]) or rest[0].lower() in _USA)) else ""
    return town, parts[0]


def _wedding_told(text: str, m: dict) -> bool:
    """Whether the text tells of the wedding the AI gives: with its year or its length near a word for marrying.
    Small models make one of "her husband, who died in 1988"."""
    if not text:
        return True
    years = m.get("years")
    if isinstance(years, int) and re.search(rf"\b{years}\s+years", text):
        return True
    year = re.search(r"\b(1\d{3}|20\d{2})\b", _s(m.get("date")))
    if not year:
        return False
    return any(year[1] in text[w.start():w.start() + 160] or year[1] in text[max(0, w.start() - 60):w.start()]
               for w in _WED.finditer(text))


def _surnames(by_id: dict, relatives: list) -> None:
    """Surnames that family notices leave out, as they are usually meant: a husband in parentheses shares his wife's,
    a son his father's, a brother the family's; and a married daughter or sister was born with the family's."""

    def family_name(p) -> str:  # the surname someone was born with, when it can be told: theirs, or their father's
        if p["birth_surname"]:
            return p["birth_surname"]
        father = next((by_id[a] for a, w, b in relatives if b == p["id"] and w == "father"), None)
        if father and father["surname"]:
            return father["surname"]
        return p["surname"] if p["sex"] == "male" else ""

    def guess(p, key, value):
        if value and not p[key]:
            p[key] = value
            p["guessed"].append(key)

    def husband_of(x):
        return next((by_id[a] for a, w, b in relatives if b == x and KIN[w][0] == "husband"
                     and by_id[a]["sex"] == "male"), None)

    for _ in range(3):  # a son's surname can come from his father's, found from his own father in the step before
        for a, word, b in relatives:
            pa, pb, group = by_id[a], by_id[b], KIN[word][0]
            if group == "husband":
                guess(pa, "surname", pb["surname"])
            elif group == "son":
                father = pb if pb["sex"] == "male" else husband_of(b)
                guess(pa, "surname", (father or pb)["surname"])
                if pa["sex"] == "female" and father and family_name(father) != pa["surname"]:
                    guess(pa, "birth_surname", family_name(father))
            elif group == "father":
                if pa["sex"] == "male":
                    guess(pa, "surname", family_name(pb))
                    if pb["sex"] == "female" and pa["surname"] and pa["surname"] != pb["surname"]:
                        guess(pb, "birth_surname", pa["surname"])  # born to Charles and Mabel Gero: born Gero
                else:
                    guess(pa, "surname", (husband_of(a) or {}).get("surname") or family_name(pb))
            elif group == "brother":
                guess(pa, "surname", family_name(pb))
                if pa["sex"] == "female" and family_name(pb) and family_name(pb) != pa["surname"]:
                    guess(pa, "birth_surname", family_name(pb))


# ---- 4. matching ---------------------------------------------------------------------------------------------------

@dataclass
class Tree:
    """What matching and proposing need of the catalog, read once."""
    g: family.Graph
    ours: dict[int, dict]                            # gedcom_import._ours: names, variants, birth years
    firsts: dict[int, set[str]] = field(default_factory=dict)  # every first and middle name each has
    born: dict[int, str] = field(default_factory=dict)  # sort dates
    died: dict[int, str] = field(default_factory=dict)
    _kin: dict = field(default_factory=dict)

    def kin(self, pid: int) -> dict[int, family.Kin]:
        if pid not in self._kin:
            self._kin[pid] = family.relations_to(self.g, pid)
        return self._kin[pid]


def load_tree(con: sqlite3.Connection) -> Tree:
    t = Tree(family.load(con), gedcom_import._ours(con))
    for pid, o in t.ours.items():
        t.firsts[pid] = set(_words(o["name"])[:-1] or _words(o["name"]))
    for pid, given, name in con.execute("SELECT id, given_name, name FROM person"):
        t.firsts[pid] |= set(_words(given or ""))
    for pid, name, given in con.execute("SELECT person_id, name, given_name FROM person_name"):
        t.firsts.setdefault(pid, set()).update(_words(given or name)[:2])
    for pid, tag, sort in con.execute("SELECT person_id, tag, sort_date FROM event WHERE tag IN ('BIRT', 'DEAT') "
                                      "AND sort_date IS NOT NULL AND date NOT LIKE 'BEF%' AND date NOT LIKE 'AFT%'"):
        (t.born if tag == "BIRT" else t.died)[pid] = sort
    return t


def _words(text: str) -> list[str]:
    """Name words, lowercase, without initials: 'Henry L. Miller' -> ['henry', 'miller']."""
    return [w for w in gedcom_import._norm(text).split() if len(w) > 1]


def _first_score(theirs: set[str], ours: set[str]) -> float:
    """How alike two people's first names are, over all the given names and nicknames of each: 1 the same name, 0.85
    a usual short form (Bob for Robert), less for one spelled differently (Alan, Allen), 0 for none alike."""
    best = 0.0
    for a in theirs:
        for b in ours:
            if a == b:
                return 1.0
            if b in gedcom_import._SAME_NAME.get(a, ()):
                best = max(best, 0.85)
            elif min(len(a), len(b)) >= 2 and (a.startswith(b) or b.startswith(a)):
                best = max(best, 0.8)  # a name cut short: Kayti for Kaytilynn
            elif len(a) > 2 and len(b) > 2 and a[0] == b[0]:
                r = SequenceMatcher(None, a, b).ratio()
                if r >= 0.75:
                    best = max(best, 0.9 * r)
    return best


def their_firsts(p: dict) -> set[str]:
    return set(_words(p["given"])) | set(_words(p["nickname"])) | set(_words(p["name"])[:1])


def _names_of(p: dict) -> list[dict]:
    """A person in the text as gedcom_import's matching takes them: each name they may be known by."""
    first = (_words(p["given"]) or [""])[0]
    out = []
    for given in dict.fromkeys(g for g in (p["given"], first, p["nickname"]) if g):
        for surname in dict.fromkeys(s for s in (p["surname"], p["birth_surname"]) if s) or [None]:
            out.append({"given": given, "surname": surname, "full": " ".join(f"{given} {surname or ''}".split())})
    return out or [{"given": p["name"], "surname": None, "full": p["name"]}]


def _fits(tree: Tree, p: dict, pid: int, written: str | None, by_sex: bool = True) -> bool:
    """Whether someone in the tree can be this person at all: the same sex (unless by_sex is off: the AI's sex for a
    relative is often a guess from the first name), born within two years, not dead before the text has them
    living."""
    sex = tree.g.sex(pid)
    if by_sex and p["sex"] and sex and p["sex"] != sex:
        return False
    born = next((f["sort"] for f in p["facts"] if f["tag"] == "BIRT" and f["sort"]), None)
    if born and pid in tree.born and abs(int(born[:4]) - int(tree.born[pid][:4])) > 2:
        return False
    died = next((f["sort"] for f in p["facts"] if f["tag"] == "DEAT" and f["sort"] and f["date"][:3] != "BEF"), None)
    if died and pid in tree.died and abs(int(died[:4]) - int(tree.died[pid][:4])) > 1:
        return False
    if p["living"] and written and pid in tree.died and tree.died[pid] < written:
        return False
    return True


def _role_word(role: str, sex: str | None) -> str:
    return {"husband": {"male": "husband", "female": "wife"}, "father": {"male": "father", "female": "mother"}}[
        role].get(sex, "spouse" if role == "husband" else "parent")


def _forward(word: str) -> set[str]:
    return _KEYS[KIN[word][0]][0]


def _backward(word: str) -> set[str]:
    other = _KEYS[KIN[word][0]][1]
    return _KEYS[KIN[other][0]][0] if other else set()


def match(con: sqlite3.Connection, found: dict, keep: dict | None = None, tree: Tree | None = None) -> dict:
    """Text person id -> {'choice': person id or None, 'how': sure|likely|unsure|new|chosen|added|skip, 'why',
    'candidates': [[person id, score, why]]}. keep: choices made by hand, and people added by applying, which stay."""
    tree = tree or load_tree(con)
    people = {p["id"]: p for p in found["people"]}
    written = found.get("written_sort")
    out = {x: dict(m) for x, m in (keep or {}).items()
           if x in people and m.get("how") in ("chosen", "added", "skip") and (
               m.get("choice") is None or m["choice"] in tree.g.people)}
    chosen = {x: m["choice"] for x, m in out.items() if m.get("choice")}
    by_name = {}
    for x, p in people.items():
        theirs = {"names": _names_of(p)}
        cands = {}
        for pid, o in tree.ours.items():
            if not _fits(tree, p, pid, written):
                continue
            score, why = gedcom_import._name_score(theirs, o)
            if score >= 0.5:
                born = next((f["sort"] for f in p["facts"] if f["tag"] == "BIRT" and f["sort"]), None)
                if born and len(born) == 10 and tree.born.get(pid) == born:
                    score, why = min(1.0, score + 0.1), why + ", born the same day"
                cands[pid] = (score, why)
        by_name[x] = cands
    links = [(a, w, b) for a, w, b in found["relatives"]]

    def candidates(x: str) -> dict[int, tuple[float, str]]:
        """Who in the tree x may be: by name (by_name), and among those the tree has as related to the people already
        found as the text says x is (their first names alike, whatever the surnames). Each relationship that agrees
        makes a match surer; a close one the tree has otherwise makes it less sure."""
        p, cands = people[x], dict(by_name[x])
        firsts = their_firsts(p)
        support: dict[int, list[str]] = {}
        against: dict[int, str] = {}
        by_relation = set()
        for a, word, b in links:
            if x not in (a, b) or (b if a == x else a) not in chosen:
                continue
            o = chosen[b if a == x else a]
            keys = _forward(word) if a == x else _backward(word)
            if not keys:
                continue
            label = f"{tree.g.name(o)}'s {word}" if a == x else f"{word} of {tree.g.name(o)}"
            unnamed = []  # in that place in the tree, of another first name
            for pid, k in tree.kin(o).items():
                if pid == o or not _fits(tree, p, pid, written, by_sex=False):
                    continue
                other_sex = bool(p["sex"] and tree.g.sex(pid) and p["sex"] != tree.g.sex(pid))
                if keys & k.words:
                    fs = _first_score(firsts, tree.firsts.get(pid, set()))
                    if fs < 0.75:
                        if not other_sex:
                            unnamed.append(pid)
                        continue
                    support.setdefault(pid, []).append(label)
                    score = 0.7 + 0.25 * fs - (0.1 if other_sex else 0)
                    if score > cands.get(pid, (0, ""))[0]:
                        by_relation.add(pid)
                        cands[pid] = (score, f"{label} in the tree" + ("" if fs == 1 else f", {tree.g.name(pid)}"))
                elif k.distance <= 2 and k.group not in ("former family", "extended family"):
                    against.setdefault(pid, f"the tree has them as {tree.g.name(o)}'s {k.label}")
            # a wife, a husband, a father or a mother is one: the only one the tree has is likely them, by another
            # first name (Emily for the Marie whose name was Emily Marie), when the surnames don't say otherwise
            role = KIN[word][0] if a == x else _KEYS[KIN[word][0]][1]
            if role in ("husband", "father") and len(unnamed) == 1 and unnamed[0] not in cands:
                pid = unnamed[0]
                ours = {s for _, s in tree.ours[pid]["parts"] if s}
                theirs = {gedcom_import._norm(s) for s in (p["surname"], p["birth_surname"]) if s}
                if not theirs or not ours or theirs & ours:
                    support.setdefault(pid, []).append(label)
                    by_relation.add(pid)
                    cands[pid] = (0.82, f"the only {_role_word(role, p['sex'])} of {tree.g.name(o)} in the tree, "
                                        f"{tree.g.name(pid)}, by another first name")
        for pid, (score, why) in list(cands.items()):
            agree = support.get(pid, [])
            extra = agree[1:] if pid in by_relation else agree
            if extra:
                score += (0.05 if pid in by_relation else 0.1) * len(extra)
                why += (", and " if pid in by_relation else ", and the tree has them as ") + " and ".join(extra)
            if pid in against and not agree:
                score, why = score - 0.15, f"{why}; but {against[pid]}"
            cands[pid] = (round(score, 3), why)  # over 1 with relatives that agree: above a namesake
        return cands

    for _ in range(len(people)):  # the surest match first, then again with what it tells about the others
        best = None
        for x in people:
            if x in out:
                continue
            ranked = sorted(((s, w, pid) for pid, (s, w) in candidates(x).items() if pid not in chosen.values()),
                            reverse=True)
            if not ranked or ranked[0][0] < LIKELY:
                continue
            second = ranked[1][0] if len(ranked) > 1 else 0.0
            if ranked[0][0] - second < 0.05:
                continue  # two alike: left for the person to pick
            if best is None or ranked[0][0] > best[0]:
                best = (ranked[0][0], x, ranked[0][2], ranked[0][1], second)
        if best is None:
            break
        score, x, pid, why, second = best
        chosen[x] = pid
        out[x] = {"choice": pid, "how": "sure" if score >= SURE and score - second >= 0.1 else "likely",
                  "why": why, "candidates": []}
    for x in people:
        others = set(chosen.values()) - {chosen.get(x)}  # someone else's already
        ranked = sorted(((s, w, pid) for pid, (s, w) in candidates(x).items() if pid not in others),
                        reverse=True)[:5]
        cands = [[pid, s, w] for s, w, pid in ranked]
        if x in out:
            out[x]["candidates"] = cands
            continue
        top = ranked[0] if ranked else None
        out[x] = {"choice": None, "how": "unsure" if top and top[0] >= UNSURE else "new",
                  "why": top[1] if top and top[0] >= UNSURE else "nobody in the tree like them", "candidates": cands}
    return out


# ---- 5. proposing --------------------------------------------------------------------------------------------------

@dataclass
class Change:
    key: str                    # stable while the reading, the choices and the tree stay the same
    who: str                    # the text person it is listed under
    kind: str                   # person, name, sex, event, link, marriage
    text: str                   # what it does, for the page
    on: bool = True             # ticked to begin with
    now: str = ""               # what the tree has, where it differs
    why: str = ""               # how it was worked out, when it wasn't read as such
    needs: list[str] = field(default_factory=list)  # changes it can't be made without (a person to add first)
    data: dict = field(default_factory=dict)


@dataclass
class Proposal:
    people: list[dict]          # each text person: their reading, match and changes, in the text's order
    changes: list[Change]
    unplaced: list[dict]        # named, but with nothing the tree can hold: [{'who', 'name', 'why'}]


def _key(kind: str, who: str, data: dict) -> str:
    return f"{kind}-{who}-" + hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()[:8]


def display_name(p: dict) -> str:
    """How a new person is called: by the name they went by and their surname ('Hank Miller' for Henry L. "Hank"
    Miller)."""
    first = p["nickname"] or (p["given"].split() or [p["name"]])[0]
    return " ".join(f"{first} {p['surname']}".split())[:120] or p["name"][:120]


def free_name(con: sqlite3.Connection, p: dict, taken: set[str]) -> str:
    """display_name, or one told apart from someone of that name already here (names are unique): with all the given
    names, then the suffix, the birth year, a number."""
    born = next((f["sort"][:4] for f in p["facts"] if f["tag"] == "BIRT" and f["sort"]), None)
    tries = [display_name(p), " ".join(f"{p['given']} {p['surname']}".split()),
             " ".join(f"{display_name(p)} {p['suffix']}".split())]
    tries += [f"{display_name(p)} ({born})"] if born else []
    tries += [f"{display_name(p)} ({n})" for n in range(2, 50)]
    for name in tries:
        if name.lower() not in taken and not con.execute("SELECT 1 FROM person WHERE lower(name) = lower(?)",
                                                         (name,)).fetchone():
            return name
    return f"{display_name(p)} ({uuid.uuid4().hex[:6]})"


def _parts(p: dict) -> dict:
    """The person's main name and other names, as the tree keeps them."""
    married = p["sex"] == "female" and bool(p["birth_surname"]) and p["birth_surname"] != p["surname"]
    others = []
    if p["birth_surname"] and p["birth_surname"] != p["surname"]:
        others.append({"name": " ".join(f"{p['given']} {p['birth_surname']}".split()), "type": "birth",
                       "guessed": "birth_surname" in p["guessed"]})
    if p["nickname"]:
        others.append({"name": p["nickname"], "type": "nickname", "guessed": False})
    return {"given": p["given"], "surname": p["surname"], "type": "married" if married else "birth",
            "others": others}


def propose(con: sqlite3.Connection, found: dict, choices: dict, source: str = "", tree: Tree | None = None,
            ) -> Proposal:
    """The changes the reading suggests, given who is who (choices, as match makes them and the page changes them)."""
    tree = tree or load_tree(con)
    people = {p["id"]: p for p in found["people"]}
    written = found.get("written_sort")
    who = {x: (choices.get(x) or {}).get("choice") for x in people}
    who = {x: pid if pid in tree.g.people else None for x, pid in who.items()}  # merged or removed since: not found
    skip = {x for x in people if (choices.get(x) or {}).get("how") == "skip"}
    note = source_note(found, source)
    changes: list[Change] = []
    names: dict[str, str] = {}  # text id -> how the change list calls them
    taken: set[str] = set()
    for x, p in people.items():
        if who[x]:
            names[x] = tree.g.name(who[x])
        else:
            names[x] = free_name(con, p, taken)
            taken.add(names[x].lower())

    placed = _placed(found, who, skip)
    unplaced = []
    for x, p in people.items():
        if x in skip:
            continue
        if not who[x] and x not in placed:
            unplaced.append({"who": x, "name": p["name"], "why": _why_unplaced(found, x, names)})
            continue
        if not who[x]:
            parts = _parts(p)
            also = [f"{'born' if o['type'] == 'birth' else 'called'} {o['name']}" for o in parts["others"]]
            guessed = [w for w in ("surname", "birth_surname") if w in p["guessed"]]
            changes.append(Change(
                f"person-{x}", x, "person", f"Add {names[x]}" + (f" ({', '.join(also)})" if also else ""),
                why=("The surname" if "surname" in guessed else "The birth name") + " is worked out from the family"
                if guessed else "",
                data={"name": names[x], **parts, "sex": p["sex"]}))
        else:
            changes += _name_changes(con, tree, x, p, who[x], names[x])
            if p["sex"] and not tree.g.sex(who[x]):
                changes.append(Change(_key("sex", x, {"sex": p["sex"]}), x, "sex", f"{names[x]} is {p['sex']}",
                                      data={"sex": p["sex"]}))
        changes += _fact_changes(con, x, p, who[x], names[x], note)
    changes += _link_changes(con, tree, found, who, names, placed, skip, note)
    ticked = found.get("ticked") or {}
    for c in changes:  # as ticked on the page, which kept them (save_ticks) while it loaded again
        c.on = bool(ticked.get(c.key, c.on))
    have = {c.key for c in changes}
    for c in changes:  # a change for a new person needs them added first
        c.needs = [k for k in dict.fromkeys(c.needs + [f"person-{y}" for y in c.data.get("_people", [])
                                                       if not who.get(y)]) if k != c.key and k in have]
        c.data.pop("_people", None)
    rows = []
    for x, p in people.items():
        m = choices.get(x) or {"choice": None, "how": "new", "why": "", "candidates": []}
        warnings = []
        if who[x] and p["living"] and written and tree.died.get(who[x], "9999") < written:
            warnings.append("The text has them living, and the tree as having died: check this is the same person.")
        said = [f"{names[b]}'s {w}" for a, w, b in found["relatives"] if a == x]
        rows.append({"id": x, "reading": p, "shown": shown_name(p), "said": said, "name": names[x], "match": m,
                     "skip": x in skip, "warnings": warnings, "changes": [c for c in changes if c.who == x],
                     "unplaced": any(u["who"] == x for u in unplaced)})
    return Proposal(rows, changes, unplaced)


def shown_name(p: dict) -> str:
    """Someone as the text names them: 'Norma K. "Kathy" Gusler (born Smith)'."""
    name = " ".join(filter(None, [p["given"], f'"{p["nickname"]}"' if p["nickname"] else "", p["surname"],
                                  p["suffix"]]))
    born = f" (born {p['birth_surname']})" if p["birth_surname"] else ""
    return (name or p["name"]) + born


def source_note(found: dict, source: str) -> str:
    """Where a fact came from, for the notes of the events it adds: 'Obituary: https://...'."""
    kind = found.get("kind") or "notes"
    label = kind[0].upper() + kind[1:] if kind not in ("notes", "other") else "From"
    source = " ".join(source.split())[:300]
    if source:
        return f"{label}: {source}" if label != "From" else f"From: {source}"
    about = next((p for p in found["people"] if p["id"] == found.get("about")), None)
    if kind in ("notes", "other") or not about:
        return ""
    return f"From the {kind} of {about['name']}".replace('"', "")


def _with_note(*parts: str) -> str:
    out = " ".join(p.strip().rstrip(".") + "." for p in parts if p and p.strip())
    return out.replace("..", ".")[:2000]


def _name_changes(con, tree: Tree, x: str, p: dict, pid: int, name: str) -> list[Change]:
    """Names the text gives that the tree lacks: the full given name (Robert Merle for Robert, Bradley for Brad), a
    birth name, a nickname; and, unticked, a first name or surname the tree spells otherwise, as another name."""
    norm = gedcom_import._norm
    row = con.execute("SELECT * FROM person WHERE id = ?", (pid,)).fetchone()
    have = {norm(r[0]) for r in con.execute("SELECT name FROM person_name WHERE person_id = ?", (pid,))}
    have |= {norm(row["name"]), norm(f"{row['given_name'] or ''} {row['surname'] or ''}")}
    given_here, surname_here = ((row["given_name"], row["surname"] or "") if row["given_name"]
                                else events.split_name(row["name"]))
    surname_here = surname_here or ""
    ours, theirs = _words(given_here), _words(p["given"])
    out = []
    if ours and theirs:
        alike = theirs[0] == ours[0] or theirs[0] in gedcom_import._SAME_NAME.get(ours[0], ())
        fuller = len(p["given"].split()) > len(given_here.split()) and (alike or ours[0] in theirs)
        formal = theirs[0] != ours[0] and (alike and len(theirs[0]) > len(ours[0]) or norm(p["nickname"]) == ours[0])
        surname = surname_here or ("" if "surname" in p["guessed"] else p["surname"])
        if fuller or formal:
            new = " ".join(f"{p['given']} {surname}".split())
            out.append(Change(_key("parts", x, {"g": p["given"], "s": surname}), x, "name", f"Full name: {new}",
                              now=f"the tree has {' '.join(f'{given_here} {surname_here}'.split())}",
                              data={"what": "parts", "given": p["given"], "surname": surname,
                                    "type": row["name_type"] or _parts(p)["type"]}))
        elif not alike and ours[0] not in theirs and theirs[0] not in tree.firsts.get(pid, set()):
            aka = " ".join(f"{p['given']} {surname}".split())
            if norm(aka) not in have:
                out.append(Change(_key("aka", x, {"n": aka}), x, "name", f"Also known as {aka}", on=False,
                                  why=f"The text calls them {p['given']}",
                                  data={"what": "other", "name": aka, "type": "aka"}))
    born_as = {norm(r[0] or "") for r in con.execute(
        "SELECT coalesce(surname, name) FROM person_name WHERE person_id = ? AND type = 'birth'", (pid,))}
    for o in _parts(p)["others"]:
        if norm(o["name"]) in have or (o["type"] == "nickname" and norm(o["name"]) in tree.firsts.get(pid, set())):
            continue  # a nickname they go by here already (Peggy Miller)
        if o["type"] == "birth" and (norm(p["birth_surname"]) in born_as or any(
                b.endswith(" " + norm(p["birth_surname"])) for b in born_as)):
            continue  # a birth name of that surname, with the given names as another text had them
        label = {"birth": "Birth name", "nickname": "Nickname"}[o["type"]]
        out.append(Change(_key("other", x, o), x, "name", f"{label}: {o['name']}",
                          why="The surname is worked out from the family" if o["guessed"] else "",
                          data={"what": "other", "name": o["name"], "type": o["type"]}))
    theirs_surname = norm(p["surname"])
    if theirs_surname and surname_here and "surname" not in p["guessed"] and theirs_surname not in {
            norm(surname_here), norm(p["birth_surname"])}:
        # a birth name can be kept whole, with no surname apart (typed in, or from a GEDCOM without SURN)
        was_born = any(norm(r[0] or events.split_name(r[1])[1] or "") == norm(surname_here) for r in con.execute(
            "SELECT surname, name FROM person_name WHERE person_id = ? AND type = 'birth'", (pid,)))
        married = p["sex"] == "female" and norm(p["birth_surname"] or surname_here) == norm(surname_here) and (
            row["name_type"] != "married" or was_born)
        other = " ".join(f"{given_here} {p['surname']}".split())
        if norm(other) not in have:
            out.append(Change(_key("surname", x, {"n": other}), x, "name",
                              f"{'Married name' if married else 'Also known as'}: {other}", on=married,
                              why="" if married else f"The text spells the surname {p['surname']}",
                              now="" if married else f"the tree has {surname_here}",
                              data={"what": "other", "name": other, "type": "married" if married else "aka"}))
    return out


def _same_place(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    na, nb = gedcom_import._norm(a).replace(" usa", ""), gedcom_import._norm(b).replace(" usa", "")
    return na == nb or na in nb or nb in na


def _dates_agree(text: dict, here) -> str:
    """How the text's date stands to the tree's: 'same', 'finer' (the text's says more: 21 Jan 1938 for 1938),
    'coarser' (the tree's says more), or 'differ'."""
    a, b = text["date"], here["date"]
    if a == b:
        return "same"
    sa, sb = text["sort"] or "", here["sort_date"] or ""
    qa, qb = a.split()[0] in ("ABT", "BEF", "AFT", "BET", "EST", "CAL"), b.split()[0] in (
        "ABT", "BEF", "AFT", "BET", "EST", "CAL")
    if qa and not qb:
        return "coarser" if a.startswith("BEF") and sb <= sa or not a.startswith(("BEF", "AFT")) and abs(
            int(sa[:4]) - int(sb[:4])) <= 2 or a.startswith("AFT") and sb >= sa else "differ"
    if qb and not qa:
        return "finer"
    if sa.startswith(sb) and len(sa) > len(sb):
        return "finer"
    if sb.startswith(sa):
        return "coarser"
    return "differ"


def _fact_changes(con, x: str, p: dict, pid: int | None, name: str, note: str) -> list[Change]:
    """Life events the text gives that the tree lacks, or knows less of (no date, no place, the year where the text
    has the day), as changes; and, unticked, where the two differ."""
    out = []
    here = [] if pid is None else con.execute("SELECT * FROM event WHERE person_id = ? ORDER BY id", (pid,)).fetchall()
    deceased = bool(pid) and bool(con.execute("SELECT deceased FROM person WHERE id = ?", (pid,)).fetchone()[0])
    for f in p["facts"]:
        label = events.PERSON_EVENTS.get(f["tag"], f["tag"]) if f["tag"] != "EVEN" else f["type"]
        data = {"tag": f["tag"], "date": f["date"], "place": f["place"], "type": f["type"],
                "note": _with_note(f["note"] or "", note), "_people": [x]}
        same_tag = [e for e in here if e["tag"] == f["tag"]]
        if f["tag"] in events.ONE_EACH and same_tag:
            e = same_tag[0]
            noted = lambda: ({"note": _with_note(e["note"] or "", note)} if note and note not in (e["note"] or "")
                             else {})
            fill = {}
            if f["date"]:
                agree = _dates_agree(f, e) if e["date"] else "finer"
                if agree == "finer":
                    fill["date"] = f["date"]
                elif agree == "differ":
                    out.append(Change(_key("date", x, data), x, "event", f"{label} {events.show_date(f['date'])}",
                                      on=False, now=f"the tree has {events.show_date(e['date'])}",
                                      data={**data, "event": e["id"], "fill": {"date": f["date"], **noted()}}))
            if f["place"]:
                if not e["place"] or (len(f["place"]) > len(e["place"]) and _same_place(f["place"], e["place"])):
                    fill["place"] = f["place"]  # Oak Hill Cemetery, Lakeview where the tree has Lakeview
                elif not _same_place(f["place"], e["place"]):
                    out.append(Change(_key("place", x, data), x, "event", f"{label} in {f['place']}", on=False,
                                      now=f"the tree has {e['place']}",
                                      data={**data, "event": e["id"], "fill": {"place": f["place"], **noted()}}))
            told = f["note"] and f["note"].rstrip(".") not in (e["note"] or "")
            if fill or (told and not e["note"]):
                fill["note"] = _with_note(e["note"] or "", f["note"] if told else "",
                                          note if note not in (e["note"] or "") else "")
                shown = {**f, "date": fill.get("date") or e["date"], "place": fill.get("place") or e["place"],
                         "note": f["note"] if told else None}
                now = " ".join(filter(None, [events.show_date(e["date"]), e["place"]])) or "no date or place"
                out.append(Change(_key("fill", x, data), x, "event", f"{label}: {_fact_text(shown)}",
                                  now=f"the tree has {now}" if set(fill) - {"note"} else "",
                                  data={**data, "event": e["id"], "fill": fill}))
            continue
        if f["tag"] == "DEAT" and not f["date"] and not f["place"] and not f["note"] and deceased:
            continue
        if f["tag"] not in events.ONE_EACH and any(_same_event(f, e) for e in same_tag):
            continue
        if f["tag"] == "DEAT" and not f["place"] and (not f["date"] or f["date"].startswith("BEF")):
            text = f"{name} died {events.show_date(f['date'])}" if f["date"] else f"{name} has died"
        else:
            text = f"{label}: {_fact_text(f)}" if _fact_text(f) else label
        out.append(Change(_key("event", x, data), x, "event", text,
                          why="Worked out from their age when they died" if f["tag"] == "BIRT" and "born" in p[
                              "guessed"] else "", data=data))
    return out


def _fact_text(f: dict) -> str:
    """'June 2, 1930 in Lima, Ohio, USA (At Mercy Hospital)'."""
    text = " ".join(filter(None, [events.show_date(f["date"]) if f["date"] else "",
                                  f"in {f['place']}" if f["place"] else ""]))
    extra = (f.get("note") or "").rstrip(".")
    return f"{text} ({extra})" if text and extra else text or extra


def _same_event(f: dict, e) -> bool:
    """Whether one of the tree's events (of the same kind) is the one the text tells of: the same date, the same
    place in the same year, or the same words (the tree's note also says where it came from)."""
    if f["date"] and e["date"] and f["date"] == e["date"]:
        return True
    if f["place"] and _same_place(f["place"], e["place"]) and (not f["date"] or not e["date"] or f["sort"][:4] == (
            e["sort_date"] or "")[:4]):
        return True
    a, b = gedcom_import._norm(f["note"] or f["type"] or ""), gedcom_import._norm(e["note"] or e["type"] or "")
    if not a or not b:
        return False
    return a in b or b in a or SequenceMatcher(None, a[:80], b[:len(a) + 10][:80]).ratio() > 0.6


def _links(found: dict) -> list[tuple[str, str, str]]:
    """The text's relationships that the tree keeps as links, (a, kind, b), in the order they are best made: parent
    (a is b's parent), then partner and former (a married in), inlaw (a son-in-law with a daughter's surname is
    probably her husband), then sibling (made through the parents when they are known)."""
    out = []
    for a, word, b in found["relatives"]:
        link = _KEYS[KIN[word][0]][2]
        if link == "child":
            out.append((b, "parent", a))
        elif link in ("parent", "partner", "former", "sibling"):
            out.append((a, link, b))
    for m in found["marriages"]:
        if not any({m["a"], m["b"]} == {a, b} and k in ("partner", "former") for a, k, b in out):
            out.append((m["a"], "partner", m["b"]))
    people = {p["id"]: p for p in found["people"]}
    norm = gedcom_import._norm
    for a, word, b in found["relatives"]:
        if KIN[word][0] != "son-in-law" or any(a in (x, y) and k in ("partner", "former") for x, k, y in out):
            continue
        kids = [c for c, w, of in found["relatives"] if of == b and KIN[w][0] == "son" and people[a]["surname"]
                and norm(people[c]["surname"]) == norm(people[a]["surname"]) and people[c]["sex"] != people[a]["sex"]]
        if len(kids) == 1:
            out.append((a, "inlaw", kids[0]))
    order = {"parent": 0, "partner": 1, "former": 1, "inlaw": 2, "sibling": 3}
    links, pairs = [], set()
    for a, kind, b in sorted(dict.fromkeys(out), key=lambda link: order[link[1]]):
        pair = (frozenset((a, b)), kind == "sibling")  # "his wife Kim" and "Kim's husband": one couple
        if kind != "parent" and pair in pairs:
            continue
        pairs.add(pair)
        links.append((a, kind, b))
    return links


def _placed(found: dict, who: dict, skip: set) -> set[str]:
    """The text's people the tree can hold: those in it, the person it is about, and those linked to them through
    parents, children, partners and brothers and sisters."""
    placed = ({x for x, pid in who.items() if pid} | {found.get("about")}) - skip - {None}
    links = _links(found)
    for _ in range(len(who)):
        more = {y for a, _, b in links for x, y in ((a, b), (b, a)) if x in placed and y not in skip}
        if more <= placed:
            break
        placed |= more
    return placed


def _why_unplaced(found: dict, x: str, names: dict) -> str:
    words = [(w, b) for a, w, b in found["relatives"] if a == x] + [
        (_KEYS[KIN[w][0]][1] or w, a) for a, w, b in found["relatives"] if b == x]
    if not words:
        return "the text doesn't say how they are related"
    w, other = words[0]
    return f"{names.get(other, '?')}'s {w}: the text doesn't say whose child or partner they are"


def _link_changes(con, tree: Tree, found: dict, who: dict, names: dict, placed: set, skip: set,
                  note: str) -> list[Change]:
    out: list[Change] = []
    people = {p["id"]: p for p in found["people"]}
    g = tree.g
    planned: dict[str, set[str]] = {}  # child -> the parents this adds (text ids, or 'id:<person id>' from the tree)
    links = _links(found)

    def ok(*xs) -> bool:
        return all(x in placed and x not in skip for x in xs)

    def parents_of(x) -> set[str]:
        here = {f"id:{q}" for q in g.parents.get(who[x], ())} if who[x] else set()
        return here | planned.get(x, set())

    def dead(x) -> bool:
        return people[x]["living"] is False or bool(who[x] and g.people[who[x]]["deceased"])

    def partners_now(x, besides) -> list[str]:
        """x's other partners, living: in the text, then in the tree."""
        said = [names[y if z == x else z] for z, k, y in links if k in ("partner", "inlaw") and x in (z, y)
                and besides not in (z, y) and people[y if z == x else z]["living"] is not False]
        here = [g.name(q) for q, (cur, _) in g.partners.get(who[x], {}).items()
                if cur and q != who.get(besides) and not g.people[q]["deceased"]] if who[x] else []
        return said + here

    for a, kind, b in links:
        if not ok(a, b):
            continue
        pa, pb = who[a], who[b]
        if kind == "parent":  # a is b's parent
            how = _pedi(found, a, b)  # "her adopted son": kept as adopted, not as born to her
            word = f"{how} {_child_word(people[b])}" if how else _child_word(people[b])
            if pa and pb and pa in g.parents.get(pb, ()):
                if how and g.how(pb, pa) != how:  # the tree has them by birth (or the other way)
                    out.append(Change(_key("link", b, {"p": a, "c": b, "how": how}), b, "link",
                                      f"{names[b]} is {names[a]}'s {word}", on=False,
                                      now=f"the tree has {names[b]} as {names[a]}'s child {_HOW_NOW[g.how(pb, pa)]}",
                                      data={"what": "parent", "child": b, "parent": a, "pedi": how,
                                            "_people": [a, b]}))
                continue
            k = tree.kin(pb).get(pa) if pa and pb else None
            if k is not None and k.distance <= 3 and "parent" not in k.words:
                now = f"the tree has {names[a]} as {names[b]}'s {k.label}"
            elif not how and len(parents_of(b) - {a}) >= 2:
                now = f"the tree already has two parents for {names[b]}"
            else:
                now = ""
            if not now:
                planned.setdefault(b, set()).add(a)
            out.append(Change(_key("link", b, {"p": a, "c": b}), b, "link",
                              f"{names[b]} is {names[a]}'s {word}", on=not now, now=now,
                              data={"what": "parent", "child": b, "parent": a, "pedi": how, "_people": [a, b]}))
        elif kind in ("partner", "former", "inlaw"):
            current = kind != "former"
            married = kind == "inlaw" or any({m["a"], m["b"]} == {a, b} for m in found["marriages"]) or any(
                {x, y} == {a, b} and w not in ("partner", "former partner", "fiance", "fiancee")
                and KIN[w][0] in ("husband", "former husband") for x, w, y in found["relatives"])
            if pa and pb and pb in g.partners.get(pa, {}):
                if current and not g.partners[pa][pb][0] and kind == "partner":
                    out.append(Change(_key("current", a, {"a": a, "b": b}), a, "link",
                                      f"{names[a]} and {names[b]} were together", on=False,
                                      now="the tree has them as former partners",
                                      why=f"The text calls them {names[b]}'s "
                                          f"{_partner_word(people[a], married, True)}",
                                      data={"what": "current", "a": a, "b": b, "_people": [a, b]}))
                continue
            why = f"{names[b]}'s parent's son-in-law, with {names[b]}'s surname" if kind == "inlaw" else ""
            for x, y in ((a, b), (b, a)):  # someone widowed with a partner since: the one who died was a former one
                if current and dead(y) and (others := partners_now(x, y)):
                    current, why = False, (why + "; " if why else "") + f"{names[x]}'s partner since is {others[0]}"
            problem = ""
            if current:
                for x, y in ((a, b), (b, a)):
                    for q, (cur, _) in g.partners.get(who[x], {}).items() if who[x] else ():
                        if cur and q != who[y] and not g.people[q]["deceased"] and not dead(x):
                            problem = f"the tree has {g.name(q)} as {names[x]}'s partner now"
            out.append(Change(_key("link", a, {"a": a, "b": b, "k": kind}), a, "link",
                              f"{names[a]} is {names[b]}'s {_partner_word(people[a], married, current)}",
                              on=not problem, now=problem, why=why[:1].upper() + why[1:],
                              data={"what": "partner", "a": a, "b": b, "current": current, "married": married,
                                    "_people": [a, b]}))
        elif kind == "sibling":
            if pa and pb and pb in g.all_siblings(pa):
                continue
            through = next(((x, y) for x, y in ((a, b), (b, a)) if parents_of(y) and not parents_of(x)), None)
            if through:  # through the parents one of them has
                x, y = through
                ps = sorted(parents_of(y))
                labels = [g.name(int(q[3:])) if q.startswith("id:") else names[q] for q in ps]
                planned.setdefault(x, set()).update(ps)
                out.append(Change(_key("link", x, {"s": ps, "c": x}), x, "link",
                                  f"{names[x]} is {' and '.join(labels)}'s {_child_word(people[x])}",
                                  why=f"{names[y]}'s {_sibling_word(people[x])}",
                                  data={"what": "parents", "child": x, "parents": ps,
                                        "_people": [x] + [q for q in ps if not q.startswith("id:")]}))
            elif not (parents_of(a) and parents_of(b)):
                out.append(Change(_key("link", a, {"a": a, "b": b, "k": "sib"}), a, "link",
                                  f"{names[a]} is {names[b]}'s {_sibling_word(people[a])}",
                                  data={"what": "sibling", "a": a, "b": b, "_people": [a, b]}))
    for m in found["marriages"]:
        a, b = m["a"], m["b"]
        if not ok(a, b) or not (m["date"] or m["place"]):
            continue
        here = None
        if who[a] and who[b]:
            lo, hi = sorted((who[a], who[b]))
            here = con.execute("SELECT * FROM event WHERE couple_a = ? AND couple_b = ? AND tag = 'MARR'",
                               (lo, hi)).fetchone()
        data = {"what": "marriage", "a": a, "b": b, "date": m["date"], "place": m["place"],
                "note": _with_note(m["note"] or "", note), "_people": [a, b]}
        text = f"{names[a]} and {names[b]} married {_fact_text({**m, 'note': None})}"
        why = m["note"][:1].upper() + m["note"][1:] if m["note"] else ""
        if here is None:
            out.append(Change(_key("marriage", a, data), a, "marriage", text, why=why, data=data))
            continue
        fill = {}
        agree = _dates_agree(m, here) if m["date"] and here["date"] else ("finer" if m["date"] else "same")
        if agree == "finer":
            fill["date"] = m["date"]
        elif agree == "differ":
            out.append(Change(_key("marriage-date", a, data), a, "marriage", text, on=False,
                              now=f"the tree has {events.show_date(here['date'])}",
                              data={**data, "event": here["id"], "fill": {"date": m["date"]}}))
        if m["place"] and not here["place"]:
            fill["place"] = m["place"]
        if fill:
            out.append(Change(_key("marriage", a, data), a, "marriage", text, why=why,
                              now=f"the tree has {events.show_date(here['date']) or 'no date'}",
                              data={**data, "event": here["id"], "fill": fill}))
    return out


_HOW_NOW = {"birth": "by birth", "adopted": "by adoption", "foster": "fostered"}


def _pedi(found: dict, parent: str, child: str) -> str | None:
    """How the text says a child belongs to a parent: 'adopted' ("her adopted son"), 'foster' ("their foster
    child"), or None: by birth, as children are unless it says otherwise."""
    for a, word, b in found["relatives"]:
        if (a, b) == (child, parent) and word.startswith("adopted"):
            return "adopted"
        if (a, b) == (child, parent) and word.startswith("foster"):
            return "foster"
    return None


def _child_word(p: dict) -> str:
    return {"male": "son", "female": "daughter"}.get(p["sex"], "child")


def _sibling_word(p: dict) -> str:
    return {"male": "brother", "female": "sister"}.get(p["sex"], "brother or sister")


def _partner_word(p: dict, married: bool, current: bool) -> str:
    word = {"male": "husband", "female": "wife"}.get(p["sex"], "spouse") if married else "partner"
    return word if current else "former " + word


# ---- 6. applying ---------------------------------------------------------------------------------------------------

def apply(con: sqlite3.Connection, found: dict, choices: dict, keys: set[str], source: str = "",
          image_id: int | None = None) -> dict:
    """Make the changes whose keys are given, as propose lists them now. Returns {'done': [{'text', 'person'}],
    'skipped': [...], 'added': {text id: person id}}, where 'person' is the one in the tree each change was listed
    under; choices gains the people added. Commits."""
    tree = load_tree(con)
    proposal = propose(con, found, choices, source, tree)
    people = {p["id"]: p for p in found["people"]}
    who = {x: (choices.get(x) or {}).get("choice") for x in people}
    who = {x: pid if pid in tree.g.people else None for x, pid in who.items()}
    done_keys, report = set(), {"done": [], "skipped": [], "added": {}}
    order = {"person": 0, "name": 1, "sex": 1, "link": 2, "event": 3, "marriage": 4}
    touched = set()
    for c in sorted((c for c in proposal.changes if c.key in keys), key=lambda c: order.get(c.kind, 9)):
        missing = [k for k in c.needs if k not in done_keys]
        if missing:
            report["skipped"].append(f"{c.text}: only with {', '.join(_text_of(proposal, k) for k in missing)}")
            continue
        try:
            with con:  # each change on its own: one that can't be made leaves the others
                con.execute("SAVEPOINT change")
                try:
                    _apply_one(con, c, who, report)
                    con.execute("RELEASE change")
                except Exception:
                    con.execute("ROLLBACK TO change")
                    con.execute("RELEASE change")
                    raise
        except (family.Problem, events.Problem, documents.Problem, Problem, sqlite3.IntegrityError) as e:
            report["skipped"].append(f"{c.text}: {e}")
            continue
        done_keys.add(c.key)
        report["done"].append({"text": c.text, "person": who.get(c.who)})
        touched |= {y for y in [c.who, *(c.data.get(k) for k in ("a", "b", "child", "parent"))] if y in people}
    for x, pid in report["added"].items():
        choices[x] = {"choice": pid, "how": "added", "why": "added from this", "candidates": []}
    with con:
        _note_sources(con, found, who, touched, source)
        if image_id is not None and touched:
            _cite(con, found, who, touched, image_id)
    return report


def _text_of(proposal: Proposal, key: str) -> str:
    return next((c.text for c in proposal.changes if c.key == key), "another change")


def _apply_one(con, c: Change, who: dict, report: dict) -> None:
    d = c.data

    def pid(x: str) -> int:
        if not who.get(x):
            raise Problem("they weren't added")
        return who[x]

    if c.kind == "person":
        name = d["name"]
        if con.execute("SELECT 1 FROM person WHERE lower(name) = lower(?)", (name,)).fetchone():
            raise Problem(f"someone called {name} was added meanwhile; reload the page")
        new = con.execute("""INSERT INTO person(name, uid, given_name, surname, name_type, names_checked, sex)
                             VALUES (?, ?, ?, ?, ?, 1, ?)""",
                          (name, str(uuid.uuid4()), d["given"] or None, d["surname"] or None, d["type"],
                           d["sex"])).lastrowid
        for o in d["others"]:
            events.add_name(con, new, o["name"], o["type"])
        who[c.who] = new
        report["added"][c.who] = new
    elif c.kind == "name":
        if d["what"] == "parts":
            events.set_name_parts(con, pid(c.who), d["given"], d["surname"], d["type"])
        else:
            events.add_name(con, pid(c.who), d["name"], d["type"])
    elif c.kind == "sex":
        family.set_sex(con, pid(c.who), d["sex"])
    elif c.kind == "event":
        person = pid(c.who)
        if "event" in d:
            e = con.execute("SELECT * FROM event WHERE id = ? AND person_id = ?", (d["event"], person)).fetchone()
            if e is None:
                raise Problem("that event was changed or removed meanwhile")
            new = {**dict(e), **d["fill"]}
            events.save(con, e["id"], tag=e["tag"], person_id=person, date_text=new["date"] or "",
                        place=new["place"] or "", kind=new["type"] or "", note=new["note"] or "")
        else:
            events.save(con, None, tag=d["tag"], person_id=person, date_text=d["date"] or "", place=d["place"] or "",
                        kind=d["type"] or "", note=d["note"] or "")
    elif c.kind == "link":
        if d["what"] == "parent":
            _parent(con, pid(d["child"]), pid(d["parent"]), d.get("pedi"))
        elif d["what"] == "parents":
            for q in d["parents"]:
                _parent(con, pid(d["child"]), int(q[3:]) if q.startswith("id:") else pid(q))
        elif d["what"] == "sibling":
            a, b = sorted((pid(d["a"]), pid(d["b"])))
            con.execute("INSERT OR IGNORE INTO sibling(a, b) VALUES (?, ?)", (a, b))
        elif d["what"] == "partner":
            a, b = pid(d["a"]), pid(d["b"])
            if d["current"]:
                family._no_other_current_partner(con, a, b)
                family._no_other_current_partner(con, b, a)
            con.execute("""INSERT INTO partner(a, b, current, married, uid) VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT(a, b) DO NOTHING""",
                        (min(a, b), max(a, b), int(d["current"]), int(d["married"]), str(uuid.uuid4())))
        elif d["what"] == "current":
            a, b = pid(d["a"]), pid(d["b"])
            family._no_other_current_partner(con, a, b)
            family._no_other_current_partner(con, b, a)
            con.execute("UPDATE partner SET current = 1 WHERE a = ? AND b = ?", (min(a, b), max(a, b)))
    elif c.kind == "marriage":
        a, b = pid(d["a"]), pid(d["b"])
        if "event" in d:
            e = con.execute("SELECT * FROM event WHERE id = ?", (d["event"],)).fetchone()
            if e is None:
                raise Problem("that marriage was changed or removed meanwhile")
            new = {**dict(e), **d["fill"]}
            events.save(con, e["id"], tag="MARR", couple=(a, b), date_text=new["date"] or "",
                        place=new["place"] or "", note=new["note"] or "")
        else:
            if not con.execute("SELECT 1 FROM partner WHERE a = ? AND b = ?", (min(a, b), max(a, b))).fetchone():
                raise Problem("they aren't partners in the tree: tick that change too")
            events.save(con, None, tag="MARR", couple=(a, b), date_text=d["date"] or "", place=d["place"] or "",
                        note=d["note"] or "")
            con.execute("UPDATE partner SET married = 1 WHERE a = ? AND b = ?", (min(a, b), max(a, b)))


def _parent(con, child: int, parent: int, pedi: str | None = None) -> None:
    """A parent link, by birth unless the text said adopted or foster (pedi), which also changes one entered."""
    if pedi or not con.execute("SELECT 1 FROM parent WHERE child_id = ? AND parent_id = ?", (child, parent)).fetchone():
        family._add_parent(con, child, parent, pedi=pedi)


def _note_sources(con, found: dict, who: dict, touched: set, source: str) -> None:
    """The source on the person's notes too (GEDCOM exports them): 'Obituary: <link>' for the person it is about,
    'Named in: <link>' for the others."""
    source = " ".join(source.split())[:300]
    if not source:
        return
    for x in touched:
        pid = who.get(x)
        if not pid:
            continue
        notes = con.execute("SELECT notes FROM person WHERE id = ?", (pid,)).fetchone()[0] or ""
        if source in notes:
            continue
        kind = (found.get("kind") or "source").capitalize() if x == found.get("about") else "Named in"
        line = f"{kind}: {source}"
        con.execute("UPDATE person SET notes = ? WHERE id = ?", ((notes + "\n" if notes else "") + line, pid))


_DOCUMENT_KIND = {"obituary": "Obituary", "death notice": "Obituary", "funeral notice": "Obituary",
                  "letter": "Letter", "certificate": "Other", "family record": "Other", "notes": "Other"}


def _cite(con, found: dict, who: dict, touched: set, image_id: int) -> None:
    """A photo read from the library becomes a document, a record of the people it changed."""
    if documents.get(con, image_id) is None:
        about = next((p for p in found["people"] if p["id"] == found.get("about")), None)
        kind = _DOCUMENT_KIND.get(found.get("kind"), "Newspaper clipping")
        documents.save(con, image_id, kind, f"{found.get('kind', 'document').capitalize()}" + (
            f" of {about['name']}" if about else ""))
    for x in sorted(touched):
        if who.get(x):
            documents.cite(con, image_id, f"p:{who[x]}")


# ---- kept readings -------------------------------------------------------------------------------------------------

def title_of(found: dict) -> str:
    about = next((p for p in found["people"] if p["id"] == found.get("about")), None)
    kind = found.get("kind") or "notes"
    if not about:
        return kind.capitalize()
    return f"{kind.capitalize()} of {display_name(about) if about['surname'] else about['name']}"[:200]


def start(con: sqlite3.Connection, text: str, source: str = "", image_id: int | None = None,
          account_id: int | None = None, photos: int = 0) -> int:
    """A reading to do: kept at once, so the page can follow it while the AI reads (run, in a thread)."""
    rid = con.execute("""INSERT INTO family_reading(created_at, account_id, title, source, image_id, text, state)
                         VALUES (?, ?, ?, ?, ?, ?, 'reading')""",
                      (_now(), account_id, "Reading the photos…" if photos else "Reading…",
                       " ".join(source.split())[:300] or None, image_id, clean_text(text))).lastrowid
    con.commit()
    return rid


def run(con: sqlite3.Connection, cfg: Config, reading_id: int, photos: list[bytes] = ()) -> None:
    """Read it: copy out the photos' text, if any, then have the AI read it, and match its people with the tree's.
    Runs beside the web app, with a connection of its own, which it closes."""
    try:
        row = con.execute("SELECT * FROM family_reading WHERE id = ?", (reading_id,)).fetchone()
        text = row["text"]
        if photos:
            text = "\n\n".join(t for t in (text, transcribe(cfg, list(photos))) if t)
            con.execute("UPDATE family_reading SET text = ? WHERE id = ?", (text, reading_id))
            con.commit()
        started = datetime.now()
        result = extract(cfg, text)
        found = tidy(result["answer"], events.day_first(con), text)
        if not found["people"]:
            raise Problem("The AI found nobody in it. Is it about a family?")
        found["ai"] = {"answer": result["answer"], "seconds": result["seconds"], "tokens": result["tokens"],
                       "model": cfg.ollama_model, "prompt": PROMPT_VERSION}
        choices = match(con, found)
        con.execute("""UPDATE family_reading SET state = 'ready', title = ?, found = ?, choices = ?, seconds = ?,
                       error = NULL WHERE id = ?""",
                    (title_of(found), json.dumps(found), json.dumps(choices),
                     round((datetime.now() - started).total_seconds(), 1), reading_id))
        con.commit()
    except Problem as e:
        _fail(con, reading_id, str(e))
    except Exception as e:  # whatever it is, the page must not wait for ever
        _fail(con, reading_id, f"Something went wrong: {type(e).__name__}: {e}"[:400])
        raise
    finally:
        con.close()


def _fail(con, reading_id: int, message: str) -> None:
    try:
        con.rollback()
        con.execute("UPDATE family_reading SET state = 'failed', error = ? WHERE id = ?", (message, reading_id))
        con.commit()
    except sqlite3.Error:
        pass


def load(con: sqlite3.Connection, reading_id: int) -> tuple[sqlite3.Row, dict | None, dict] | None:
    row = con.execute("SELECT * FROM family_reading WHERE id = ?", (reading_id,)).fetchone()
    if row is None:
        return None
    return row, json.loads(row["found"]) if row["found"] else None, json.loads(row["choices"] or "{}")


def set_choices(con: sqlite3.Connection, reading_id: int, picked: dict[str, str]) -> None:
    """Who is who, as picked on the page: text id -> 'new', 'skip', a person id, or a person's name (one person's).
    Raises Problem, saving nothing, for a name that is nobody's or several people's."""
    row, found, choices = load(con, reading_id)
    people = {p["id"] for p in found["people"]}
    for x, value in picked.items():
        value = str(value or "").strip()
        if x not in people or not value or value == "keep":
            continue
        if value in ("new", "skip"):
            if (choices.get(x) or {}).get("how") == "added":
                continue  # added already: they are in the tree now
            choices[x] = {**(choices.get(x) or {"candidates": []}), "choice": None, "how": value,
                          "why": "left out" if value == "skip" else "to add as new"}
            if value == "new":
                choices[x]["how"] = "chosen"
            continue
        if value.isdecimal():
            pid = int(value)
            if not con.execute("SELECT 1 FROM person WHERE id = ?", (pid,)).fetchone():
                raise Problem("Someone picked was merged or removed since the page was opened: reload it.")
        else:
            found_ids = [r[0] for r in con.execute("SELECT id FROM person WHERE lower(name) = lower(?)", (value,))]
            if len(found_ids) != 1:
                raise Problem(f"{len(found_ids)} people are called {value}: pick one from the suggestions as you type."
                              if found_ids else f"Nobody in the tree is called {value}.")
            pid = found_ids[0]
        if (choices.get(x) or {}).get("choice") != pid:
            choices[x] = {**(choices.get(x) or {"candidates": []}), "choice": pid, "how": "chosen",
                          "why": "picked by you"}
    choices = match(con, found, keep=choices)  # the others again, with what the picks tell
    con.execute("UPDATE family_reading SET choices = ? WHERE id = ?", (json.dumps(choices), reading_id))
    con.commit()


def save_ticks(con: sqlite3.Connection, reading_id: int, ticked: dict[str, bool]) -> None:
    """The changes ticked and unticked on the page, as they are clicked: picking who someone is loads the page
    again, which shows them as they were left. Kept with what was read, so reading it again starts them over."""
    row, found, _ = load(con, reading_id)
    if found is None:
        raise Problem("It hasn't been read yet.")
    found["ticked"] = {**(found.get("ticked") or {}),
                       **{str(k)[:100]: bool(v) for k, v in list(ticked.items())[:MOST_TICKS]}}
    con.execute("UPDATE family_reading SET found = ? WHERE id = ?", (json.dumps(found), reading_id))
    con.commit()


def apply_kept(con: sqlite3.Connection, reading_id: int, keys: set[str]) -> dict:
    row, found, choices = load(con, reading_id)
    if found is None:
        raise Problem("It hasn't been read yet.")
    report = apply(con, found, choices, keys, row["source"] or "", row["image_id"])
    choices = match(con, found, keep=choices)
    con.execute("UPDATE family_reading SET choices = ?, report = ?, applied_at = ? WHERE id = ?",
                (json.dumps(choices), json.dumps(report), _now(), reading_id))
    con.commit()
    return report


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
