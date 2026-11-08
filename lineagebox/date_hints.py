"""When a photo with no date of its own was probably taken: a scan, or a copy dated by its file's time or its folder's
year. Shown on its page under Taken ("Probably about 1987", and why), to take with one tap, which dates it as if by
hand (tagging.set_date): people's dates always win, and nothing here changes a photo by itself.

Measured on the library LineageBox was developed on (2026-11-07), on its 36,342 photos with a camera date and 3 or more
others with one in their folder, each dated from the others as if it had none:
- The others in its folder: when the middle 80% of their dates were within a month, the photo was within 0.6 of a
  month of their middle 99% of the time; within two years, 7.5 months. So within a month gives the month, within two
  years "about" the year (a year either side).
- The dated photos just before and after it by file name, when they were within a year of each other: within 0.34
  years of their middle 99% of the time, also in folders whose photos span many years.
- Who is in it: a photo isn't from before the birth of anyone in it. That held for 96.6% of 15,571 photos; nearly all
  the rest were from one camera with its clock wrong. It is a lower bound, not a year: a face's estimated age is far
  too rough to say how old anyone was (face_ages.py), except that a face estimated under 15 is a child of 12 or less.
- The AI's season can't be measured there (the AI is told the dates it has), so it is only mentioned.
Of that library's 1,615 photos dated by file time or folder, 651 had 3 or more dated photos in their folder.
"""
import sqlite3
from datetime import datetime

from lineagebox.catalog import VISIBLE
from lineagebox.face_ages import CHILD_AT_MOST, CHILD_LOOKS, SURE, births
from lineagebox.faces import usable_faces_sql

NEEDS = ("mtime", "folder")  # photos dated only by these get a suggestion
NEIGHBOURS = 3               # dated photos in its folder needed
SAME_MONTH = 31              # days the middle 80% of them, or the two beside it, span at most for the month
SAME_YEARS = 2 * 365         # ...and for about the year of their middle
BESIDE_YEAR = 365            # days apart the two dated photos beside it by file name may be, for about the year
PEOPLE_SPAN = 4              # years at most between who is in it allows, to suggest those years


def _when(iso: str) -> datetime:
    return datetime.fromisoformat(iso[:19])


def _middle(a: datetime, b: datetime) -> datetime:
    return a + (b - a) / 2


def _quantile(values: list[datetime], q: float) -> datetime:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))]


def _month(d: datetime) -> str:
    return f"{d:%B} {d.year}"


def suggest(con: sqlite3.Connection, image_id: int) -> dict | None:
    """What the photo's date probably is, for a photo dated only by its file's time or a folder (NEEDS), or None:
    {"text": what to date it as (dates.photo_date reads it), or None when there is only a bound; "label": what to show;
    "why": the reasons, as sentences}. On a member's connection, from the photos and people they see."""
    img = con.execute(f"SELECT i.id, i.taken_at_source FROM image i WHERE i.id = ? AND {VISIBLE}",
                      (image_id,)).fetchone()
    if img is None or img["taken_at_source"] not in NEEDS:
        return None
    why, text, label = [], None, None
    found = _from_neighbours(con, image_id)
    lower, upper, people_why = _from_people(con, image_id)
    if found:
        text, last_year, reason = found
        if lower is not None and last_year < lower:  # the folder says before someone in it was born: not that
            text = None
        else:
            why.append(reason)
    if text is None:  # who is in it says the rest
        if lower is not None and upper is not None and upper - lower <= PEOPLE_SPAN:
            text = str(lower) if lower == upper else f"between {lower} and {upper}"
        why += people_why
    if text:
        label = f"Probably {text}"
    elif lower is not None:
        label = f"Not before {lower}"
    else:
        return None
    if img["taken_at_source"] == "mtime" and (season := _season(con, image_id)):
        why.append(f"It looks like {season} to the AI.")
    return {"text": text, "label": label, "why": why}


def _from_neighbours(con: sqlite3.Connection, image_id: int) -> tuple[str, int, str] | None:
    """(the date to suggest, the last year it allows, why) from the dated photos in its folders, or None."""
    rows = con.execute(f"""
        SELECT f.folder_id, f.path, f.image_id, i.taken_at, i.taken_at_source FROM file f JOIN image i ON i.id = f.image_id
        WHERE f.folder_id IN (SELECT folder_id FROM file WHERE image_id = ? AND NOT missing) AND NOT f.missing
          AND {VISIBLE} ORDER BY f.folder_id, f.path""", (image_id,)).fetchall()
    dated = {r["image_id"]: _when(r["taken_at"]) for r in rows
             if r["image_id"] != image_id and r["taken_at_source"] in SURE and r["taken_at"]}
    # the dated photos just before and after it by file name, in the first folder it is in
    folder = min(r["folder_id"] for r in rows if r["image_id"] == image_id)
    own = [r for r in rows if r["folder_id"] == folder]
    at = next(n for n, r in enumerate(own) if r["image_id"] == image_id)
    before = next((dated[r["image_id"]] for r in reversed(own[:at]) if r["image_id"] in dated), None)
    after = next((dated[r["image_id"]] for r in own[at + 1:] if r["image_id"] in dated), None)
    beside = abs((after - before).days) if before and after else None
    times = sorted(dated.values())
    spread = (_quantile(times, 0.9) - _quantile(times, 0.1)).days if len(times) >= NEIGHBOURS else None
    if beside is not None and beside <= SAME_MONTH:
        mid = _middle(before, after)
        return _month(mid), mid.year, f"The photos just before and after it in its folder were taken in {_month(mid)}."
    if spread is not None and spread <= SAME_MONTH:
        mid = _quantile(times, 0.5)
        return _month(mid), mid.year, f"{len(times):,} photos beside it in its folder were taken in {_month(mid)}."
    if beside is not None and beside <= BESIDE_YEAR:
        mid = _middle(before, after)
        return (f"about {mid.year}", mid.year + 1,
                f"The photos just before and after it in its folder were taken in {_years(before, after)}.")
    if spread is not None and spread <= SAME_YEARS:
        mid = _quantile(times, 0.5)
        low, high = _quantile(times, 0.1), _quantile(times, 0.9)
        return (f"about {mid.year}", mid.year + 1,
                f"Most of the {len(times):,} photos beside it in its folder were taken in {_years(low, high)}.")
    return None


def _years(a: datetime, b: datetime) -> str:
    a, b = min(a, b), max(a, b)
    return str(a.year) if a.year == b.year else f"{a.year} and {b.year}"


def _from_people(con: sqlite3.Connection, image_id: int) -> tuple[int | None, int | None, list[str]]:
    """(the first year it can be from, the last, why) by who is in it: not before anyone in it was born, nor after
    anyone in it died, nor more than CHILD_AT_MOST years after the birth of someone whose face looks like a young
    child's in it (CHILD_LOOKS)."""
    born = births(con)
    deaths = dict(con.execute("SELECT person_id, sort_date FROM event WHERE tag = 'DEAT' AND sort_date IS NOT NULL"))
    rows = con.execute(f"""
        SELECT p.id, p.name, f.est_age, ({usable_faces_sql()}) AS usable FROM face f JOIN person p ON p.id = f.person_id
        JOIN image i ON i.id = f.image_id WHERE f.image_id = :i AND f.assigned_by IN ('user', 'auto')
        UNION ALL SELECT p.id, p.name, NULL, 0 FROM image_person l JOIN person p ON p.id = l.person_id
        WHERE l.image_id = :i""", {"i": image_id}).fetchall()
    lower = upper = None
    why_lower = why_upper = None
    for r in rows:
        if (b := born.get(r["id"])) is not None:
            year = int(b[:4])
            if lower is None or year > lower:
                lower, why_lower = year, f"{r['name']}, in it, was born in {year}."
            if r["usable"] and r["est_age"] is not None and r["est_age"] < CHILD_LOOKS:
                if upper is None or year + CHILD_AT_MOST < upper:
                    upper, why_upper = year + CHILD_AT_MOST, f"{r['name']} looks like a young child in it."
        # a death bounds it whether or not the birth is known (often not, for the oldest relatives in old photos)
        if (d := deaths.get(r["id"])) and (upper is None or int(d[:4]) < upper):
            upper, why_upper = int(d[:4]), f"{r['name']}, in it, died in {d[:4]}."
    if lower is not None and upper is not None and upper < lower:  # a wrong name or date: say nothing of it
        return None, None, []
    return lower, upper, [w for w in (why_lower, why_upper) if w]


def _season(con: sqlite3.Connection, image_id: int) -> str | None:
    from lineagebox.catalog import analysis_for
    a = analysis_for(con, image_id)
    season = a["season"] if a is not None else None
    return season if season in ("winter", "spring", "summer", "fall") else None
