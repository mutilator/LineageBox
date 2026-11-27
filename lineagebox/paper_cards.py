"""The AI's card for each historical paper (historical.py), once its pages are read: what kind of paper it is, a title,
when it was written, by whom and to whom, a sentence or two of what it says, the places it names, and the people it
names, matched to the family tree (paper_person).

The AI only reads (SYSTEM): one text question about the paper's words, to the model that describes photos (it needs
no pictures). Matching its names to people is done here, where it can be tested (match_name): a name the tree has once
in full, of someone alive when it was written, is linked at once ('auto', as a recognized face is: the paper is then
in that person's part of the family, for who sees it); a likely one is suggested, for the paper's page to ask about
('suggested'). A person's word wins: one confirmed ('user') or said not to be them ('not') stays as it is.

A card is made once a paper's pages all have words, again when someone corrects them (the trigger in migration 042
clears card_at), and with a new VERSION. Details someone saved (document.source 'user') are never written over.
"""
import json
import re
import sqlite3
import unicodedata
from datetime import date

from lineagebox import clock, documents, historical, retry
from lineagebox.catalog import VISIBLE

VERSION = 1
STAGE = "cards"  # retry.py
retry.STAGES[STAGE] = "documents to sum up"
MOST_TEXT = 12_000   # characters of a paper read at once: its first pages, which say what it is
MOST_PEOPLE = 40
MOST_PLACES = 20
ANSWER_TOKENS = 2048
GIVE_UP_AFTER = 5
SURE, LIKELY = 0.9, 0.6  # match scores: linked at once / suggested
ROLES = ("wrote", "received", "about", "named")
# a person's birth and death (or burial) dates, as person p: the birth date kept on them, else their life events'
LIFE = """coalesce(p.birth_date, (SELECT min(e.sort_date) FROM event e WHERE e.person_id = p.id AND e.tag = 'BIRT'))
            AS birth_date,
          (SELECT min(e.sort_date) FROM event e WHERE e.person_id = p.id AND e.tag IN ('DEAT', 'BURI', 'CREM'))
            AS death_date"""

SYSTEM = """You catalog a family's historical papers. You are given the words of one paper (a letter, a card, a
certificate, an award, a deed, a will, a clipping, notes), as copied out page by page. Fill in the JSON fields.
- kind: what kind of paper it is.
- title: a short title a family would give it ("Letter to her sister Ruth", "Deed to the Mill Road farm"), with
  names as they are written in it.
- written: the date it was written, sent or issued, exactly as written in it; "" if it has none.
- sender: who wrote, sent or issued it, as named in it; "" if it doesn't say.
- recipient: whom it was written or sent to, or issued to; "" if it doesn't say.
- summary: one or two plain sentences of what it says, for someone of the family who hasn't read it, with names as
  written in it. Say only what it says.
- people: everyone named in it: each one's name as written (the fullest form it uses), and their role: "wrote",
  "received", "about" (whom it is mainly about: the one born, married, awarded, buried, who bought or sold), or
  "named" (anyone else).
- places: the towns, farms, streets, churches and cemeteries it names, as written.
- language: the language it is written in, in English."""
_PERSON = {"type": "object", "required": ["name", "role"],
           "properties": {"name": {"type": "string"}, "role": {"type": "string", "enum": list(ROLES)}}}
SCHEMA = {"type": "object",
          "required": ["kind", "title", "written", "sender", "recipient", "summary", "people", "places", "language"],
          "properties": {"kind": {"type": "string", "enum": list(documents.KINDS)}, "title": {"type": "string"},
                         "written": {"type": "string"}, "sender": {"type": "string"},
                         "recipient": {"type": "string"}, "summary": {"type": "string"},
                         "people": {"type": "array", "maxItems": MOST_PEOPLE, "items": _PERSON},
                         "places": {"type": "array", "maxItems": MOST_PLACES, "items": {"type": "string"}},
                         "language": {"type": "string"}}}


class Problem(Exception):
    """A paper the AI couldn't sum up just now: tried again later (retry.py)."""


def waiting(con: sqlite3.Connection) -> list[int]:
    """Papers whose pages all have words (or are blank), with some words, and no card yet (or one of an older
    VERSION, or words corrected since), newest first."""
    return [r[0] for r in con.execute(f"""
        SELECT d.image_id FROM document d JOIN image i ON i.id = d.image_id
        WHERE {VISIBLE} AND d.unreadable IS NULL AND (d.card_at IS NULL OR coalesce(d.card_version, 0) < ?)
          AND NOT EXISTS (SELECT 1 FROM paper_page p WHERE p.paper_id = d.image_id AND p.text IS NULL AND NOT p.blank)
          AND EXISTS (SELECT 1 FROM paper_page p WHERE p.paper_id = d.image_id AND trim(coalesce(p.text, '')) != '')
          AND {retry.waiting(STAGE, "d.image_id")}
        ORDER BY d.image_id DESC""", (VERSION,))]


def words_of(con: sqlite3.Connection, paper_id: int) -> str:
    """A paper's words as the AI reads them: page by page, up to MOST_TEXT."""
    parts = [f"[Page {r['n']}]\n{r['text']}" for r in con.execute(
        "SELECT n, text FROM paper_page WHERE paper_id = ? AND trim(coalesce(text, '')) != '' ORDER BY n",
        (paper_id,))]
    text = "\n\n".join(parts)
    return text[:MOST_TEXT] + ("\n[...]" if len(text) > MOST_TEXT else "")


def make(con: sqlite3.Connection, client, paper_id: int) -> dict:
    """Ask for a paper's card and keep it. Returns the AI's answer. Raises Problem; httpx.HTTPError when the AI
    doesn't answer."""
    from lineagebox.llm import CutOff, OllamaError

    text = words_of(con, paper_id)
    paper = historical.get(con, paper_id)
    if paper is None or not text.strip():
        return {}
    named = f"Its file or folder is called: {paper['title']}\n\n" if paper["source"] == "auto" else ""
    try:
        answer, _ = client.chat_json(SYSTEM, f"{named}The paper's words:\n\n{text}", SCHEMA, temperature=0.0,
                                     max_tokens=ANSWER_TOKENS)
    except CutOff:
        raise Problem("The AI's answer ran too long.") from None
    except ValueError:
        raise Problem("The AI's answer wasn't JSON.") from None
    except OllamaError as e:
        raise Problem(f"The AI couldn't read it: {e}") from None
    keep(con, paper_id, answer, client.model)
    return answer


def _s(value, most: int = 200) -> str | None:
    return " ".join(str(value or "").split())[:most] or None


def keep(con: sqlite3.Connection, paper_id: int, answer: dict, model: str) -> None:
    """The card as the AI gave it: what it says of the paper where nobody has said otherwise, its summary and places,
    and the people it names matched to the tree. Commits."""
    paper = historical.get(con, paper_id)
    if paper is None:
        return
    if paper["source"] == "auto":
        kind = answer.get("kind") if answer.get("kind") in documents.KINDS else paper["kind"]
        written = _s(answer.get("written"), 120)
        first, last = historical.written_span(con, written)
        con.execute("""UPDATE document SET kind = ?, title = coalesce(?, title), written = coalesce(?, written),
                                           written_from = coalesce(?, written_from), written_to = coalesce(?, written_to),
                                           sender = coalesce(?, sender), recipient = coalesce(?, recipient),
                                           language = coalesce(?, language)
                       WHERE image_id = ? AND source = 'auto'""",
                    (kind, _s(answer.get("title")), written, first if written else None, last if written else None,
                     _s(answer.get("sender")), _s(answer.get("recipient")), _s(answer.get("language"), 40), paper_id))
    places = [p for p in dict.fromkeys(_s(x, 120) for x in answer.get("places") or []) if p][:MOST_PLACES]
    con.execute("""UPDATE document SET summary = CASE WHEN source = 'auto' OR summary IS NULL THEN ? ELSE summary END,
                                       places = ?, card_model = ?, card_version = ?, card_at = ?
                   WHERE image_id = ?""",
                (_s(answer.get("summary"), 1000), json.dumps(places) if places else None, model, VERSION,
                 clock.now(), paper_id))
    _link_people(con, paper_id, answer.get("people") or [])
    retry.done(con, STAGE, [paper_id])
    con.commit()


# ---- who is who ---------------------------------------------------------------------------------------------------------

def fold(text: str) -> str:
    """A name as names are compared: lowercase, no accents, no dots or titles, single spaces."""
    t = "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c)).lower()
    t = re.sub(r"[.,]", " ", t)
    t = re.sub(r"\b(?:mr|mrs|ms|miss|dr|rev|sr|jr|ii|iii|the late)\b", " ", t)
    return " ".join(t.split())


def _year(text: str | None) -> int | None:
    m = re.match(r"\s*(\d{4})", text or "")
    return int(m[1]) if m else None


def match_name(con: sqlite3.Connection, name: str, written_year: int | None = None) -> list[tuple[int, float]]:
    """The people a name in a paper may be, with how likely: [(person id, score)], the likeliest first. The whole
    name as the tree has it (or one of their other names) 0.95; first and last names alike, the middle ones not
    said, 0.85; the first name's initial and the surname 0.6. Someone born after the paper was written can't be in
    it; someone who had died years before is less likely, unless it is a record of them (left to the caller)."""
    wanted = fold(name).split()
    if len(wanted) < 2:  # a first name alone ("Ruth") could be anyone: never matched
        return []
    scores: dict[int, float] = {}
    for pid, other in con.execute("SELECT id, name FROM person UNION ALL SELECT person_id, name FROM person_name"):
        theirs = fold(other or "").split()
        if len(theirs) < 2:
            continue
        if theirs == wanted:
            score = 0.95
        elif theirs[0] == wanted[0] and theirs[-1] == wanted[-1]:
            score = 0.85
        elif len(wanted[0]) == 1 and theirs[0][0] == wanted[0] and theirs[-1] == wanted[-1]:
            score = 0.6
        else:
            continue
        scores[pid] = max(scores.get(pid, 0), score)
    if written_year and scores:
        lives = {r[0]: (r[1], r[2]) for r in con.execute(
            f"SELECT p.id, {LIFE} FROM person p WHERE p.id IN (SELECT value FROM json_each(?))",
            (json.dumps(sorted(scores)),))}
        for pid in list(scores):
            born, died = (_year(x) for x in lives.get(pid, (None, None)))
            if born and born > written_year:
                del scores[pid]
            elif died and died + 2 < written_year:
                scores[pid] -= 0.15
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def _link_people(con: sqlite3.Connection, paper_id: int, named: list) -> None:
    """The people the card names, as links to the tree: sure ones at once, likely ones suggested; those a person
    confirmed or set aside stay as they are. Part of keep's write."""
    paper = historical.get(con, paper_id)
    year = _year(paper["written_from"]) if paper else None
    kept = {r[0]: r[1] for r in con.execute("SELECT person_id, state FROM paper_person WHERE paper_id = ?",
                                            (paper_id,))}
    con.execute("DELETE FROM paper_person WHERE paper_id = ? AND state IN ('auto', 'suggested')", (paper_id,))
    rank = {role: n for n, role in enumerate(ROLES)}
    seen: dict[int, tuple[str, str, str]] = {}
    for item in named[:MOST_PEOPLE]:
        if not isinstance(item, dict) or not _s(item.get("name")):
            continue
        name, role = _s(item.get("name"), 120), item.get("role") if item.get("role") in ROLES else "named"
        found = match_name(con, name, year)
        if not found or any(kept.get(pid) == "user" for pid, _ in found):
            continue  # nobody of the name, or a person said already which one it is
        best = found[0][1]
        sure = best >= SURE and (len(found) == 1 or found[1][1] < LIKELY)
        # sure: that one at once; else each as likely as the best, to ask which (two Ben Bakers)
        picked = [found[0]] if sure else [(pid, s) for pid, s in found if s >= LIKELY and s >= best - 0.05][:3]
        for pid, _ in picked:
            state = "auto" if sure else "suggested"
            if kept.get(pid) == "not":
                continue
            if pid in seen and (seen[pid][2] == "auto" or rank[seen[pid][1]] <= rank[role]):
                continue
            seen[pid] = (name, role, state)
    con.executemany("""INSERT INTO paper_person(paper_id, person_id, role, name, state, at) VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(paper_id, person_id) DO NOTHING""",
                    ((paper_id, pid, role, name, state, clock.now()) for pid, (name, role, state) in seen.items()))


def people(con: sqlite3.Connection, paper_id: int) -> list[dict]:
    """Who a paper names, as linked: [{person_id, name (theirs), as_written, role, state}], linked ones first."""
    return [dict(r) for r in con.execute(f"""
        SELECT x.person_id, p.name, x.name AS as_written, x.role, x.state, {LIFE}
        FROM paper_person x JOIN person p ON p.id = x.person_id WHERE x.paper_id = ? AND x.state != 'not'
        ORDER BY x.state = 'suggested', CASE x.role WHEN 'about' THEN 0 WHEN 'wrote' THEN 1 WHEN 'received' THEN 2
                                                    ELSE 3 END, p.name""", (paper_id,))]


def confirm(con: sqlite3.Connection, paper_id: int, person_id: int, yes: bool, role: str = "named") -> None:
    """A person's word on who is named in a paper: it is them, or not. Doesn't commit."""
    if role not in ROLES:
        role = "named"
    con.execute("""INSERT INTO paper_person(paper_id, person_id, role, state, at) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(paper_id, person_id) DO UPDATE SET state = excluded.state, at = excluded.at""",
                (paper_id, person_id, role, "user" if yes else "not", clock.now()))


def make_waiting(con: sqlite3.Connection, client, stop=None) -> int:
    """The worker's pass: a card for each paper waiting (waiting()), until stop() says so. Returns how many."""
    import httpx

    done = failed = 0
    for paper_id in waiting(con):
        if stop is not None and stop():
            break
        try:
            make(con, client, paper_id)
            done += 1
            failed = 0
        except Problem as e:
            retry.later(con, STAGE, paper_id, str(e))
            failed += 1
            if failed >= GIVE_UP_AFTER:
                break
        except httpx.HTTPError:
            break
    return done


def life_years(born: str | None, died: str | None) -> str:
    """'1900–1980', '1900–', '–1980' or ''."""
    b, d = _year(born), _year(died)
    return f"{b or ''}–{d or ''}" if b or d else ""


def today_year() -> int:
    return date.today().year
