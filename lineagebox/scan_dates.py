"""Scans dated by the day they were scanned, with what can tell their real date (Cleanup → Suggested dates, admins).

A scanner, or a phone's scanning app, dates what it scans by the day it was scanned: an obituary from 1952 scanned in
2026 is dated 2026, by the scanner's own date or by a file name such as Image_20261107_143000.tif. So are the old
prints scanned with it. This finds them:

- documents: photos marked as a document (documents.py), or that the AI describes as one, that were scanned or have
  no date at all but their file's time. A document someone marked is listed whatever date it has: a phone's photo of
  a certificate is dated the day of the photo, not of the certificate. A folder's date is left alone: someone named
  that folder (on the library this was made with, 51 documents were dated by folders such as "2014/Ultrasound Feb",
  rightly).
- scanned photos: the others that were scanned.

Scanned means a scanner's camera name (HP ScanJet, Canon CanoScan, Fujitsu ScanSnap, Epson Perfection), or "scan" in
the name of its folder or file (ScannedPhotos, CamScanner, PhotoScan, Scan_0001). A date set by hand is the family's
and takes it off the list, as does an admin saying its date is right (OK_KEY); a copy's camera date (dedup) is trusted.

What can tell the real date, best first (suggestions()), each to take with one click as a date set by hand:
1. A date written on it, as the AI copies it (read(), kept in scan_reading): the worker reads every document once,
   when the describing hours allow it (read_waiting); anyone can ask again from the list.
2. A date among the words the AI's description saw on it (analysis.text_visible).
3. The family tree: a document cited for someone's event, or a marriage certificate, birth or death certificate,
   obituary or diploma of someone, takes that event's date.
4. The photos beside it in its folder, by file name. For a scan, those someone dated by hand: an album is scanned in
   its order, so the scans just before and after it are from about its time (their own dates say nothing: they were
   scanned the same day). For a document with no date at all, the camera-dated photos beside it, as on its page
   (date_hints).
5. Who is in it (date_hints): not before anyone in it was born, nor after anyone in it died; a year or a few when the
   two come close, else only "not before".

The worker has the AI read every scan on the list once, documents first (a print's date stamp, or what was written on
its back, dates a photo as well).
"""
import io
import json
import re
import sqlite3
from datetime import datetime

from lineagebox import date_hints, dates, events, retry, settings
from lineagebox.catalog import VISIBLE

SCANNER = re.compile(r"scan|perfection|doxie|flatbed", re.I)  # in a camera's name: a scanner
SCAN_PATH = "%scan%"  # in a folder's or file's name (LIKE, any case)
NO_DATE = ("mtime",)  # dated only by its file's time: no date of its own (a folder's is someone's word)
DOCUMENT = "document/screenshot"  # the AI's photo_type for a picture of a document
TRUSTED = ("user", "copy")  # a date set by hand, or a copy's camera date: not suggested over
OK_KEY = "scan_dates_ok"  # settings: scans an admin said are dated right
STAGE = "dates"  # retry.py
PROMPT_VERSION = 1
READ_SIZE = 1600  # longest side of a scan sent to the AI: small print needs more than a description
MOST_DATES = 6
PEOPLE_SPAN = date_hints.PEOPLE_SPAN
BESIDE = 25      # files either side of it in its folder to look through for scans dated by hand...
BESIDE_ONE = 5   # ...and how near one must be when there is one on one side only
# a document of someone, by its kind: the life event it records, and how that is said
KIND_EVENTS = {"Birth certificate": ("BIRT", "the birth it records"),
               "Death certificate": ("DEAT", "the death it records"),
               "Obituary": ("DEAT", "the death (an obituary is printed within days)"),
               "Marriage certificate": ("MARR", "the marriage it records"), "Diploma": ("GRAD", "the graduation")}
retry.STAGES[STAGE] = "documents to read the dates on"

READ_SYSTEM = """You find the dates written on a scanned document or photo: a letter, a certificate, a newspaper
clipping, an obituary, a card, a form, a page of a family Bible, a date printed on a photo's border or written on its
back. List every date written on it, each copied exactly as written, with a few words on what it is the date of ("the
letter's date", "date of birth", "issued", "the newspaper's date", "printed on the photo"). Put first the date the
document itself was written, printed or issued, or the photo taken. Leave out a year that is only part of a name or a
number (a company's "since 1902", a form's code). If no date is written on it, answer with an empty list."""
_A_DATE = {"type": "object", "required": ["written", "what"],
           "properties": {"written": {"type": "string"}, "what": {"type": "string"}}}
READ_SCHEMA = {"type": "object", "required": ["dates"],
               "properties": {"dates": {"type": "array", "maxItems": MOST_DATES, "items": _A_DATE}}}


class Problem(Exception):
    """The AI couldn't read a scan: the message says why, for the person who asked."""


# ---- which scans --------------------------------------------------------------------------------------------------

def scans(con: sqlite3.Connection) -> dict[str, list[dict]]:
    """The scans whose date may be the day they were scanned: {"documents": [...], "photos": [...]}, each with its
    id, sha256, taken_at, taken_at_source, camera, path (its first file), kind (a document's, marked by someone) and
    how it was found ("scanner", "folder" for a scan's folder or file name, "no date")."""
    cameras = [c for (c,) in con.execute("SELECT DISTINCT camera FROM image WHERE camera IS NOT NULL")
               if SCANNER.search(c)]
    ok = set(settings.get(con, OK_KEY, []))
    rows = con.execute(f"""
        SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.camera, d.kind,
               (SELECT f.path FROM file f WHERE f.image_id = i.id AND NOT f.missing ORDER BY f.id LIMIT 1) AS path,
               EXISTS (SELECT 1 FROM file f WHERE f.image_id = i.id AND NOT f.missing AND f.path LIKE :scan) AS named,
               (SELECT a.photo_type FROM analysis a WHERE a.image_id = i.id ORDER BY a.created_at DESC LIMIT 1) AS type
        FROM image i LEFT JOIN document d ON d.image_id = i.id
        WHERE {VISIBLE} AND i.media = 'photo' AND i.taken_at_source NOT IN ('user', 'copy')
          AND (i.dup_group_id IS NULL OR i.dup_group_id = i.id)
          AND (d.image_id IS NOT NULL OR i.taken_at_source IN (SELECT value FROM json_each(:no_date))
               OR i.camera IN (SELECT value FROM json_each(:cameras))
               OR EXISTS (SELECT 1 FROM file f WHERE f.image_id = i.id AND NOT f.missing AND f.path LIKE :scan))
        ORDER BY path, i.id""", {"scan": SCAN_PATH, "cameras": json.dumps(cameras),
                                 "no_date": json.dumps(NO_DATE)}).fetchall()
    out: dict[str, list[dict]] = {"documents": [], "photos": []}
    for r in rows:
        if r["id"] in ok:
            continue
        found = ("scanner" if r["camera"] in cameras else "folder" if r["named"]
                 else "no date" if r["taken_at_source"] in NO_DATE else None)
        item = {k: r[k] for k in ("id", "sha256", "taken_at", "taken_at_source", "camera", "path", "kind")}
        if r["kind"] is not None or r["type"] == DOCUMENT:
            if found or r["kind"] is not None:
                out["documents"].append({**item, "found": found or "marked"})
        elif found in ("scanner", "folder"):
            out["photos"].append({**item, "found": found})
    return out


def counts(con: sqlite3.Connection) -> dict[str, int]:
    found = scans(con)
    return {"documents": len(found["documents"]), "photos": len(found["photos"])}


# ---- what can tell its date ---------------------------------------------------------------------------------------

_MONTH = (r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
          r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_DAY = r"(?P<day>\d{1,2})(?:st|nd|rd|th)?"
_YEAR = r"(?P<year>1[89]\d\d|20\d\d)"
_FOUND = [  # date-like words in a text, the most exact first
    re.compile(rf"\b{_MONTH}\s+{_DAY},?\s+{_YEAR}\b", re.I),  # June 3, 1952
    re.compile(rf"\b{_DAY}\s+(?:of\s+)?{_MONTH},?\s+{_YEAR}\b", re.I),  # 3rd of June 1952
    re.compile(r"\b(?P<a>\d{1,2})[/.-](?P<b>\d{1,2})[/.-](?P<year>(?:1[89]|20)\d\d)\b"),  # 6/3/1952
    re.compile(rf"\b{_MONTH},?\s+{_YEAR}\b", re.I),  # June 1952
]
_YEAR_ALONE = re.compile(rf"\b{_YEAR}\b")
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december")


def _said(m: re.Match) -> str:
    """A date found in a text, as the date box reads it."""
    g = m.groupdict()
    if g.get("a"):
        return f"{g['a']}/{g['b']}/{g['year']}"
    month = next(name for name in _MONTHS if name.startswith(g["mon"].lower()[:3]))
    return f"{month} {g['day']}, {g['year']}" if g.get("day") else f"{month} {g['year']}"


def found_dates(text: str, south: bool = False, day_first: bool = False, years: bool = True) -> list[dates.Span]:
    """The dates a text says, as the date box reads them, the most exact first (a day, then a month): a whole text
    that is one date ("about 1950", "Sept. 3rd, 1952"), or those in it. years: a year standing alone counts when it
    is the only one in the text (several are more likely a life's span, or numbers)."""
    out: dict[str, dates.Span] = {}

    def add(said: str) -> None:
        try:
            span = dates.photo_date(said, south, day_first)
        except dates.Problem:
            return
        if span is not None:
            out.setdefault(span.text, span)

    whole = re.sub(r"\b(?:mon|tues|wednes|thurs|fri|satur|sun)day\b,?", " ", text or "", flags=re.I)
    whole = re.sub(r"\b(\d{1,2})(?:st|nd|rd|th)\b", r"\1", whole, flags=re.I)
    if len(whole) <= 40:  # a date on its own, as the AI copies one
        add(re.sub(r"\b([A-Za-z]{3,9})\.", r"\1", whole))
    taken = []
    for rx in _FOUND:
        for m in rx.finditer(whole):
            if not any(a <= m.start() < b for a, b in taken):  # not a part of a more exact one already found
                taken.append(m.span())
                add(_said(m))
    if years and not out:
        alone = sorted(set(_YEAR_ALONE.findall(whole)))
        if len(alone) == 1:
            add(alone[0])
    return sorted(out.values(), key=lambda s: (s.last - s.first, s.first))


def _event_said(sort: str) -> str | None:
    """An event's sort date ("1952-06-03", "1952-06", "1952") as the date box reads it."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", sort or ""):
        return sort
    if re.fullmatch(r"\d{4}-\d{2}", sort or ""):
        return f"{_MONTHS[int(sort[5:7]) - 1]} {sort[:4]}"
    return sort if re.fullmatch(r"\d{4}", sort or "") else None


def _from_tree(con: sqlite3.Connection, image_id: int, kind: str | None) -> list[tuple[str, str]]:
    """(date, why) from the family tree: the events the document is cited for, then, for a kind that records one
    (KIND_EVENTS), that event of each person it is cited for."""
    out = []
    for e in con.execute("""SELECT e.tag, e.sort_date, e.type, p.name FROM citation c JOIN event e ON e.id = c.event_id
                            LEFT JOIN person p ON p.id = e.person_id
                            WHERE c.image_id = ? AND e.sort_date IS NOT NULL""", (image_id,)):
        if said := _event_said(e["sort_date"]):
            what = (events.PERSON_EVENTS | events.COUPLE_EVENTS).get(e["tag"]) or e["type"] or e["tag"]
            out.append((said, f"It is cited for {e['name'] or 'an event'}: {what.lower()}."))
    if kind in KIND_EVENTS:
        tag, how = KIND_EVENTS[kind]
        for e in con.execute("""SELECT p.name, e.sort_date FROM citation c JOIN person p ON p.id = c.person_id
                                JOIN event e ON e.tag = ? AND e.sort_date IS NOT NULL
                                  AND (e.person_id = p.id OR p.id IN (e.couple_a, e.couple_b))
                                WHERE c.image_id = ? ORDER BY e.sort_date""", (tag, image_id)):
            if said := _event_said(e["sort_date"]):
                out.append((said, f"It is {e['name']}’s {kind.lower()}: the tree has the date of {how}."))
    return out


def _dated_by_hand(row) -> bool:
    return row["taken_at_source"] == "user" and bool(row["date_text"] and row["date_from"] and row["date_to"])


def _beside(con: sqlite3.Connection, image_id: int) -> tuple[str, int, int, str] | None:
    """(the date to suggest, its first year, its last year, why) from the scans just before and after it in its folder
    by file name that someone dated by hand: both within BESIDE files and a few years of each other, or one alone
    within BESIDE_ONE. None when they say nothing."""
    rows = con.execute(f"""
        SELECT f.image_id, i.taken_at_source, i.date_text, i.date_from, i.date_to
        FROM file f JOIN image i ON i.id = f.image_id
        WHERE f.folder_id = (SELECT folder_id FROM file WHERE image_id = ? AND NOT missing ORDER BY id LIMIT 1)
          AND NOT f.missing AND {VISIBLE} ORDER BY f.path, f.id""", (image_id,)).fetchall()
    at = next((n for n, r in enumerate(rows) if r["image_id"] == image_id), None)
    if at is None:
        return None
    before = next(((at - n, rows[n]) for n in range(at - 1, max(-1, at - 1 - BESIDE), -1) if _dated_by_hand(rows[n])),
                  None)
    after = next(((n - at, rows[n]) for n in range(at + 1, min(len(rows), at + 1 + BESIDE)) if _dated_by_hand(rows[n])),
                 None)
    if before and after:
        a, b = before[1], after[1]
        low = min(int(a["date_from"][:4]), int(b["date_from"][:4]))
        high = max(int(a["date_to"][:4]), int(b["date_to"][:4]))
        if a["date_text"] == b["date_text"]:
            return (a["date_text"], low, high,
                    f"The scans before and after it in its folder were both dated {a['date_text']} by hand.")
        if high - low <= PEOPLE_SPAN:
            return (str(low) if low == high else f"between {low} and {high}", low, high,
                    f"The scans before and after it in its folder were dated {a['date_text']} and {b['date_text']} "
                    "by hand.")
        return None
    side, near = ("before", before) if before and before[0] <= BESIDE_ONE else ("after", after)
    if near is None or near[0] > BESIDE_ONE:
        return None
    r = near[1]
    return (r["date_text"], int(r["date_from"][:4]), int(r["date_to"][:4]),
            f"The scan just {side} it in its folder was dated {r['date_text']} by hand.")


def suggestions(con: sqlite3.Connection, item: dict, south: bool = False, day_first: bool = False) -> list[dict]:
    """What can tell a scan's real date, best first: [{"text": the date to give it (as the date box reads it), or
    None for a bound only; "label": what to show; "why": the reason}], each date once."""
    out: list[dict] = []
    seen = set()

    def add(span: dates.Span | None, why: str, label: str | None = None) -> None:
        if span is not None and span.text not in seen:
            seen.add(span.text)
            out.append({"text": span.text, "label": label or span.text, "why": why})

    if row := con.execute("SELECT dates FROM scan_reading WHERE image_id = ?", (item["id"],)).fetchone():
        for d in json.loads(row["dates"]):
            written, what = str(d.get("written") or "").strip(), str(d.get("what") or "").strip()
            for span in found_dates(written, south, day_first)[:1]:
                add(span, f"It says “{written}”" + (f" ({what})." if what else "."))
    analysis = con.execute("SELECT text_visible FROM analysis WHERE image_id = ? ORDER BY created_at DESC LIMIT 1",
                           (item["id"],)).fetchone()
    if analysis and analysis["text_visible"]:
        for span in found_dates(analysis["text_visible"], south, day_first)[:3]:
            add(span, "The AI's description saw it written on it.")
    for said, why in _from_tree(con, item["id"], item.get("kind")):
        try:
            add(dates.photo_date(said, south, day_first), why)
        except dates.Problem:
            pass
    lower, upper, why = date_hints._from_people(con, item["id"])
    if item.get("found") == "no date" and (near := date_hints._from_neighbours(con, item["id"])):
        said, last, reason = near  # not a scan: the camera-dated photos beside it are from its time (date_hints)
        if lower is None or last >= lower:
            try:
                add(dates.photo_date(said, south, day_first), reason)
            except dates.Problem:
                pass
    if beside := _beside(con, item["id"]):
        said, first, last, reason = beside
        if (lower is None or last >= lower) and (upper is None or first <= upper):  # not against who is in it
            try:
                add(dates.photo_date(said, south, day_first), reason)
            except dates.Problem:
                pass
    if lower is not None and upper is not None and upper - lower <= PEOPLE_SPAN:
        add(dates.photo_date(str(lower) if lower == upper else f"between {lower} and {upper}", south), " ".join(why))
    elif lower is not None and f"not before {lower}" not in seen:
        seen.add(f"not before {lower}")
        out.append({"text": None, "label": f"Not before {lower}", "why": " ".join(why)})
    return out


def reading(con: sqlite3.Connection, image_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM scan_reading WHERE image_id = ?", (image_id,)).fetchone()


# ---- the AI reads the dates written on it -------------------------------------------------------------------------

def _picture(cfg, sha256: str) -> bytes:
    from PIL import Image
    with Image.open(cfg.preview_path(sha256)) as im:
        im = im.convert("RGB")
        im.thumbnail((READ_SIZE, READ_SIZE))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=90)
        return out.getvalue()


def read(con: sqlite3.Connection, cfg, image_id: int, client=None) -> list[dict]:
    """Ask the AI for the dates written on a scan, and keep its answer. Returns the dates ([{"written", "what"}]).
    Raises Problem when it can't be read (no picture of it, the AI away or confused)."""
    import httpx

    from lineagebox.llm import CutOff, Ollama, OllamaError

    row = con.execute("SELECT sha256 FROM image WHERE id = ?", (image_id,)).fetchone()
    if row is None:
        raise Problem("No such photo.")
    try:
        picture = _picture(cfg, row["sha256"])
    except OSError:
        raise Problem("Its preview isn't made yet: the worker makes it on its next pass.") from None
    client = client or Ollama(cfg)
    try:
        answer, _ = client.chat_json(READ_SYSTEM, "What dates are written on it?", READ_SCHEMA, images=[picture],
                                     temperature=0.0, max_tokens=600)
    except (CutOff, ValueError):
        raise Problem("The AI's answer didn't make sense. Try again.") from None
    except OllamaError as e:
        raise Problem(f"The AI couldn't read it: {e}") from None
    except httpx.HTTPError as e:
        raise Problem(f"The AI isn't answering ({type(e).__name__}). Is the computer it runs on switched on?") from None
    found = [{"written": str(d.get("written") or "").strip()[:120], "what": str(d.get("what") or "").strip()[:120]}
             for d in (answer.get("dates") or [])[:MOST_DATES]
             if isinstance(d, dict) and str(d.get("written") or "").strip()]
    con.execute("""INSERT INTO scan_reading(image_id, model, version, read_at, dates) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(image_id) DO UPDATE SET model = excluded.model, version = excluded.version,
                                                       read_at = excluded.read_at, dates = excluded.dates""",
                (image_id, client.model, PROMPT_VERSION, datetime.now().isoformat(timespec="seconds"),
                 json.dumps(found)))
    retry.done(con, STAGE, [image_id])
    con.commit()
    return found


def read_waiting(con: sqlite3.Connection, cfg, client, stop=None) -> int:
    """The worker's pass: the AI reads the dates on each scan of the list it hasn't read yet (with this model and
    prompt), the documents first. A document it can't read waits to be tried again (retry.py); the AI not answering ends
    the pass. Returns how many were read."""
    import httpx

    read_now = {r[0] for r in con.execute("SELECT image_id FROM scan_reading WHERE model = ? AND version = ?",
                                          (client.model, PROMPT_VERSION))}
    held = {r[0] for r in con.execute("""SELECT item_id FROM retry WHERE stage = ?
                                         AND next_at > CAST(strftime('%s', 'now') AS INTEGER)""", (STAGE,))}
    done = 0
    found = scans(con)
    for item in found["documents"] + found["photos"]:  # documents first
        if item["id"] in read_now or item["id"] in held:
            continue
        if stop is not None and stop():
            break
        try:
            read(con, cfg, item["id"], client)
            done += 1
        except Problem as e:
            if "isn't answering" in str(e):
                break
            retry.later(con, STAGE, item["id"], str(e))
        except httpx.HTTPError:
            break
    return done
