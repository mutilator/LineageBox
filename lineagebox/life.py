"""Someone's life as their page tells it: their life events, what bears each out (documents, written sources), and
their photos, grouped by how old they were. Without a birth date, by year instead.

The groups are worked out from the photos' dates alone (one query), so the page stays quick for someone in thousands
of photos; each group's photos are fetched when it is opened (photos), at most PER_LOAD at a time.

A group runs from one birthday to the next, as precisely as the birth date is known: from the day for a whole date,
the month for "June 1984", the year for "1984" (the year someone turns that age; "about 11"). Photos taken before
the birth, after the year of the death, and those with no date of their own (only a file's time) have a group each.
Ages are worked out as events.age does, so a photo's group and the age its page shows agree.
"""
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from lineagebox import documents, events
from lineagebox.catalog import VISIBLE

PER_LOAD = 240  # photos shown when a group is opened; the rest load with Show more

_THEIRS = """i.id IN (SELECT image_id FROM face WHERE person_id = :p
                    UNION SELECT image_id FROM image_person WHERE person_id = :p)"""  # as catalog.person_images
_DATED = "i.taken_at IS NOT NULL AND i.taken_at_source != 'mtime'"


@dataclass
class Period:
    key: str                       # "age:12", "year:1996", "before", "after", "undated"
    label: str                     # "Age 12", "About 12", "1996", "Before they were born"
    when: str = ""                 # the years it spans: "1996–97"
    photos: int = 0
    events: list[dict] = field(default_factory=list)


@dataclass
class Life:
    birth: str | None              # the birth's sort date: "1984-06-12", "1984-06", "1984"
    death: str | None
    periods: list[Period]
    undated_events: list[dict]     # life events with no date, to show apart


def _start(birth: str, n: int) -> date:
    """The day someone turns n, as precisely as their birth is known (for a birth in a month or a year only, the
    month's or the year's first day: what events.age counts from)."""
    y = int(birth[:4]) + n
    if len(birth) == 10:
        m, d = int(birth[5:7]), int(birth[8:10])
        try:
            return date(y, m, d)
        except ValueError:  # born on February 29th: March 1st in other years, as events.age has it
            return date(y, 3, 1)
    return date(y, int(birth[5:7]) if len(birth) == 7 else 1, 1)


def _usable(sort: str | None) -> bool:
    """A sort date the calendar can hold. Trees imported before the import checked it may hold year 0000 or 9999
    (some programs' "not known"): such a date is shown as written but placed nowhere."""
    return bool(sort) and sort[:4].isdecimal() and 1 <= int(sort[:4]) <= 9998


def _end_of(sort: str) -> date:
    """The day after a sort date's day, month or year: everything before it is no later than it."""
    if len(sort) == 10:
        return date.fromisoformat(sort) + timedelta(days=1)
    if len(sort) == 7:
        y, m = int(sort[:4]), int(sort[5:7])
        return date(y + m // 12, m % 12 + 1, 1)
    return date(int(sort[:4]) + 1, 1, 1)


def _mid(sort: str) -> date:
    """The middle of what a sort date says, to place an event known to a month or a year."""
    if len(sort) == 10:
        return date.fromisoformat(sort)
    if len(sort) == 7:
        return date(int(sort[:4]), int(sort[5:7]), 15)
    return date(int(sort[:4]), 7, 1)


def _key(on: date, birth: str | None, death: str | None) -> str:
    if death and on >= _end_of(death[:4]):  # after the year they died: a grave, a memorial, scans dated later
        return "after"
    if birth is None:
        return f"year:{on.year}"
    if on < _start(birth, 0):
        return "before"
    years = events.age(birth, on)
    return f"age:{int(years.removeprefix('about '))}"


def _range(key: str, birth: str | None, death: str | None) -> tuple[str | None, str | None]:
    """The dates (ISO, the end left out) of a group's photos; None where it is open."""
    kind, _, n = key.partition(":")
    after = _end_of(death[:4]).isoformat() if death else None
    if kind == "after":
        return after, None
    if kind == "before":
        return None, _start(birth, 0).isoformat() if birth else None
    if kind == "year":
        return f"{int(n):04d}-01-01", min(f"{int(n) + 1:04d}-01-01", after or "9999")
    start, end = _start(birth, int(n)).isoformat(), _start(birth, int(n) + 1).isoformat()
    return start, min(end, after or "9999")


def _label(key: str, birth: str | None) -> tuple[str, str]:
    kind, _, n = key.partition(":")
    if kind == "before":
        return "Before they were born", ""
    if kind == "after":
        return "After they died", ""
    if kind == "undated":
        return "Date not known", ""
    if kind == "year":
        return n, ""
    n = int(n)
    first = _start(birth, n)
    last = _start(birth, n + 1) - timedelta(days=1)
    when = str(first.year) if first.year == last.year else f"{first.year}–{str(last.year)[-2:]}"
    if len(birth) == 4:
        return (f"About {n}" if n else "Born"), str(first.year)
    return (f"Age {n}" if n else "First year"), when


def life(con: sqlite3.Connection, pid: int) -> Life:
    """Someone's groups, oldest first, each with its photo count and its life events."""
    birth, death = life_dates(con, pid)
    counts: dict[str, int] = {}
    for taken, n in con.execute(f"""SELECT substr(i.taken_at, 1, 10), count(*) FROM image i
                                    WHERE {_THEIRS} AND {VISIBLE} AND {_DATED} GROUP BY 1""", {"p": pid}):
        k = _key(date.fromisoformat(taken), birth, death)
        counts[k] = counts.get(k, 0) + n
    undated = con.execute(f"""SELECT count(*) FROM image i WHERE {_THEIRS} AND {VISIBLE} AND NOT ({_DATED})""",
                          {"p": pid}).fetchone()[0]
    found = events.for_person(con, pid)
    cited = documents.event_citations(con, [e["id"] for e in found])
    by_key: dict[str, list[dict]] = {}
    loose = []
    for e in found:
        item = {"id": e["id"], "tag": e["tag"], "label": _event_label(e), "date": e["date"], "place": e["place"],
                "note": e["note"], "partner": e["partner"], **cited.get(e["id"], {"documents": [], "sources": []})}
        if not _usable(e["sort_date"]):
            loose.append(item)
            continue
        on = _mid(e["sort_date"])
        k = "age:0" if e["tag"] == "BIRT" and birth else _key(on, birth, None if e["tag"] in ("DEAT",) else death)
        by_key.setdefault(k, []).append(item)
    keys = set(counts) | set(by_key)
    order = lambda k: (0, "") if k == "before" else (2, "") if k == "after" else (1, f"{int(k.partition(':')[2]):06d}")
    periods = []
    for k in sorted(keys, key=order):
        label, when = _label(k, birth)
        periods.append(Period(k, label, when, counts.get(k, 0), by_key.get(k, [])))
    if undated:
        periods.append(Period("undated", "Date not known", "", undated))
    return Life(birth, death, periods, loose)


def _event_label(e) -> str:
    names = events.COUPLE_EVENTS if e["person_id"] is None else events.PERSON_EVENTS
    label = names.get(e["tag"], e["tag"])
    if e["type"] and (e["tag"] == "EVEN" or e["tag"] in events.ATTRIBUTES):
        label = e["type"] if e["tag"] == "EVEN" else f"{label} {e['type']}"
    return label + (f" with {e['partner']}" if e["partner"] else "")


def photos(con: sqlite3.Connection, pid: int, key: str, skip: int = 0, limit: int = PER_LOAD) -> tuple[list, int]:
    """One group's photos, in date order, from the skip-th on: (at most limit of them, how many the group has)."""
    birth, death = life_dates(con, pid)
    if key == "undated":
        where, args = f"NOT ({_DATED})", {}
    else:
        if not _valid(key) or (key == "after" and not death) or (key.startswith(("age", "before")) and not birth):
            return [], 0
        try:
            start, end = _range(key, birth, death)
        except ValueError:  # an age past the year 9999 (an address typed by hand): no such group
            return [], 0
        where = _DATED + (" AND i.taken_at >= :start" if start else "") + (" AND i.taken_at < :end" if end else "")
        args = {"start": start, "end": end}
    rows = con.execute(f"""SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.media, i.duration FROM image i
                           WHERE {_THEIRS} AND {VISIBLE} AND {where} ORDER BY i.taken_at, i.id""",
                       {"p": pid, **args}).fetchall()
    return rows[skip:skip + limit], len(rows)


def life_dates(con: sqlite3.Connection, pid: int) -> tuple[str | None, str | None]:
    """(birth, death) sort dates; a death before the birth is left out (one of them is wrong, which Cleanup → Family
    tree says), so the ages still read."""
    birth = con.execute("SELECT sort_date FROM event WHERE person_id = ? AND tag = 'BIRT' AND sort_date IS NOT NULL",
                        (pid,)).fetchone()
    death = con.execute("""SELECT min(sort_date) FROM event WHERE person_id = ? AND tag IN ('DEAT', 'BURI', 'CREM')
                           AND sort_date IS NOT NULL""", (pid,)).fetchone()
    birth, death = (birth[0] if birth else None), (death[0] if death else None)
    birth, death = (birth if _usable(birth) else None), (death if _usable(death) else None)
    return birth, (None if birth and death and death < birth else death)


def _valid(key: str) -> bool:
    kind, _, n = key.partition(":")
    return key in ("before", "after") or (kind in ("age", "year") and n.isdecimal() and int(n) < 10000)


def portrait(con: sqlite3.Connection, pid: int) -> int | None:
    """The face shown for someone, as catalog.people picks it: the one chosen, while still theirs, else the clearest
    named by hand, else the clearest recognised."""
    row = con.execute(f"""SELECT f.id FROM face f JOIN image i ON i.id = f.image_id
                          WHERE f.person_id = :p AND {VISIBLE}
                          ORDER BY f.id IS (SELECT cover_face_id FROM person WHERE id = :p) DESC,
                                   f.assigned_by = 'user' DESC, f.quality DESC LIMIT 1""", {"p": pid}).fetchone()
    return row[0] if row else None


def portrait_choices(con: sqlite3.Connection, pid: int, limit: int = 60, year: str = "") -> list[sqlite3.Row]:
    """Faces to choose someone's portrait from: named as them by hand first, then recognised, the clearest first;
    those of photos from one year only, if one is given."""
    return con.execute(f"""SELECT f.id, f.image_id, f.assigned_by, i.taken_at FROM face f
                           JOIN image i ON i.id = f.image_id
                           WHERE f.person_id = :p AND f.assigned_by IN ('user', 'auto') AND {VISIBLE}
                             AND (:year = '' OR substr(i.taken_at, 1, 4) = :year)
                           ORDER BY f.assigned_by = 'user' DESC, f.quality DESC LIMIT :n""",
                       {"p": pid, "n": limit, "year": year}).fetchall()


def portrait_years(con: sqlite3.Connection, pid: int) -> list[tuple[str, int]]:
    """The years of the photos someone is named in, with how many of their faces each, to choose a portrait from."""
    return [tuple(r) for r in con.execute(f"""SELECT substr(i.taken_at, 1, 4) AS y, count(*) FROM face f
                                              JOIN image i ON i.id = f.image_id
                                              WHERE f.person_id = ? AND f.assigned_by IN ('user', 'auto') AND {VISIBLE}
                                                AND i.taken_at IS NOT NULL
                                              GROUP BY y ORDER BY y""", (pid,))]
