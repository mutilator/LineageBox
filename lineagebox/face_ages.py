"""What the faces' ages can and can't say: names that don't fit a person's age, and a hint of when someone with no
birth date was born.

InsightFace estimates an age for every face (face.est_age). Measured on the library LineageBox was developed on
(2026-11-07: 3,612 faces named by hand of 16 people with a birth date, on photos with a camera date, plus 16,292
recognised), the estimate is far too rough to tell anyone's age:

- Children are estimated as adults: faces of children under 2 came out 28 years old on average (90% within 47
  years of the truth), of 4 to 6 year-olds 27, of 10 to 12 year-olds 21. Adults are 10 years out on average, with
  one face in ten 20 to 26 years out.
- It doesn't follow one person as they grow: across a person's own photos, from babyhood to their teens or over 20
  years of adulthood, it rose with their age no more than chance (rank correlation -0.36 to 0.49, near 0 for most).
- So it can't tell two young brothers apart: in 1,357 photos with both, the older looked older 49% of the time.
  Recognition doesn't use it (people.py).

What it does say: a face estimated under 15 is a young child. All 134 such faces named by hand were children of 10 or
less (the oldest 10.8), and 2 of 1,563 recognised ones were 12 or 13. Only about one face in seven of a child under 7
is estimated that young, so it is a hint now and then, never a measure. And whatever the faces look like, a photo
taken before someone was born isn't of them: on that library, 535 named faces were in photos dated before the person
was born, 533 of them from one camera whose clock had been wrong for a while (two years behind, and reset to its
first day), which is what camera_clock.py puts right.
"""
import calendar
import json
import sqlite3
from collections import defaultdict
from datetime import date

from lineagebox.catalog import VISIBLE
from lineagebox.faces import usable_faces_sql

CHILD_LOOKS = 15     # a face estimated under this age is a young child's...
CHILD_AT_MOST = 12   # ...of at most this age then: a year more than the oldest such face measured
GROWN_UP = 18        # named as someone at least this old then, such a face is likely someone else
CHILD_DAYS = 2       # days with such a face needed for a birth-year hint: one could be a wrong name
OK_KEY = "ages_ok"   # settings: faces an admin said are named right whatever their age says
# Dates a photo's day can be trusted to: the camera's (or a copy's), a file name's, or one set by hand
SURE = ("exif", "camera", "filename", "copy")


def _first(born: str) -> date:
    """The earliest day a birth date (ISO, as much as is known: "1984-06-12", "1984-06", "1984") allows."""
    return date(int(born[:4]), int(born[5:7]) if len(born) >= 7 else 1, int(born[8:10]) if len(born) >= 10 else 1)


def _last(born: str) -> date:
    """The latest day a birth date allows."""
    if len(born) >= 10:
        return date.fromisoformat(born[:10])
    if len(born) >= 7:
        y, m = int(born[:4]), int(born[5:7])
        return date(y, m, calendar.monthrange(y, m)[1])
    return date(int(born[:4]), 12, 31)


def photo_days(row) -> tuple[date, date] | None:
    """The first and last day a photo may have been taken, when its date can be trusted (SURE, or set by hand); else
    None (a file's time, a folder's year)."""
    if row["taken_at_source"] in SURE and row["taken_at"]:
        day = date.fromisoformat(row["taken_at"][:10])
        return day, day
    if row["taken_at_source"] == "user" and row["date_from"] and row["date_to"]:
        return date.fromisoformat(row["date_from"]), date.fromisoformat(row["date_to"])
    return None


def births(con: sqlite3.Connection) -> dict[int, str]:
    return dict(con.execute("SELECT person_id, sort_date FROM event WHERE tag = 'BIRT' AND sort_date IS NOT NULL"))


def age_checks(con: sqlite3.Connection, ok: set[int] = frozenset()) -> list[dict]:
    """Faces named as someone (by hand or recognised) that can't be them by their age (Cleanup → Confirm faces →
    Ages to check): the photo was taken before they were born (often a camera's clock: see camera_clock.py), or the
    face looks like a young child's while they were grown up then, on a photo whose date someone set or said is right
    (otherwise the date is the likelier mistake: Suggested dates → Looks too young, scan_dates.young). ok: faces said
    to be right. Each with the person, how it was named, why, and the photo's date and camera; by person, then date.
    On a member's connection, the faces and people they see."""
    from lineagebox import scan_dates, settings

    born = births(con)
    if not born:
        return []
    dated_right = set(settings.get(con, scan_dates.OK_KEY, []))
    rows = con.execute(f"""
        SELECT f.id, f.image_id, f.person_id, f.assigned_by, f.est_age, ({usable_faces_sql()}) AS usable,
               i.taken_at, i.taken_at_source, i.date_from, i.date_to, i.camera, p.name
        FROM face f JOIN image i ON i.id = f.image_id JOIN person p ON p.id = f.person_id
        WHERE f.assigned_by IN ('user', 'auto') AND {VISIBLE}
          AND f.person_id IN (SELECT value FROM json_each(?))""", (_ids(born),)).fetchall()
    out = []
    for r in rows:
        if r["id"] in ok or (days := photo_days(r)) is None:
            continue
        b = born[r["person_id"]]
        why = None
        if days[1] < _first(b):
            why = "born after"
        elif (r["usable"] and r["est_age"] is not None and r["est_age"] < CHILD_LOOKS
              and (days[0] - _last(b)).days / 365.25 >= GROWN_UP
              and (r["taken_at_source"] == "user" or r["image_id"] in dated_right)):
            why = "child"
        if why:
            out.append({"face_id": r["id"], "image_id": r["image_id"], "person_id": r["person_id"], "name": r["name"],
                        "by": r["assigned_by"], "why": why, "taken_at": r["taken_at"], "camera": r["camera"],
                        "born": b, "source": r["taken_at_source"]})
    return sorted(out, key=lambda x: (x["name"].lower(), x["person_id"], x["taken_at"] or "", x["face_id"]))


def before_birth_by_camera(con: sqlite3.Connection, checks: list[dict] | None = None) -> dict[str, int]:
    """camera -> how many named faces are in its photos taken before the person was born: a sign its clock was wrong
    (the camera clock page lists the cameras with them first). checks: age_checks(con), when already worked out."""
    out: dict[str, int] = defaultdict(int)
    for c in age_checks(con) if checks is None else checks:
        if c["why"] == "born after" and c["camera"] and c["source"] in ("exif", "camera"):
            out[c["camera"]] += 1
    return dict(out)


def birth_hint(con: sqlite3.Connection, pid: int) -> tuple[int, int] | None:
    """The years someone with no birth date was probably born between (their page shows it by Born), or None. Only
    from what the faces can say (see the module's notes): their face looks like a young child's (CHILD_LOOKS) in
    photos of CHILD_DAYS days or more, so they were born at most CHILD_AT_MOST years before the second-latest of
    those days, and not after their first photo. Nothing when the two disagree (a wrong name, a wrong clock)."""
    if con.execute("SELECT 1 FROM event WHERE person_id = ? AND tag = 'BIRT' AND sort_date IS NOT NULL",
                   (pid,)).fetchone():
        return None
    first, child = None, set()
    for r in con.execute(f"""SELECT f.est_age, ({usable_faces_sql()}) AS usable, i.taken_at, i.taken_at_source,
                                    i.date_from, i.date_to
                             FROM face f JOIN image i ON i.id = f.image_id
                             WHERE f.person_id = ? AND f.assigned_by = 'user' AND {VISIBLE}""", (pid,)):
        if (days := photo_days(r)) is None:
            continue
        first = min(first or days[1], days[1])
        if r["usable"] and r["est_age"] is not None and r["est_age"] < CHILD_LOOKS and days[0] == days[1]:
            child.add(days[0])
    if first is None or len(child) < CHILD_DAYS:
        return None
    low = sorted(child)[-2].year - CHILD_AT_MOST
    return (low, first.year) if low <= first.year else None


def _ids(values) -> str:
    return json.dumps(sorted(values))
