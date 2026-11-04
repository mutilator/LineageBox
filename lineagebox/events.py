"""Life events of people and couples (birth, death, marriage, divorce, graduation, moving house...), with dates and
places as GEDCOM writes them, and people's names in parts (given name and surname) with their other names.

Dates are kept as GEDCOM date values, so what is known is kept as it is known: "12 JUN 1984", "JUN 1984", "1984",
"ABT 1950", "BEF 1960", "BET 1950 AND 1955". Each also has a sort date (ISO: "1984-06-12", "1984-06", "1984"), for
ordering and ages. People type dates however they like (see parse_date).

person.birth_date and person.deceased are kept in step with the BIRT and DEAT events (see _sync_person): ages in
tours, the family tree's order and the partner rules read them.
"""
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date

from lineagebox import errors

# GEDCOM event tags people can add, with what the pages call them. BIRT and DEAT are a person's; MARR and DIV a
# couple's; EVEN is anything else, described by its type.
PERSON_EVENTS = {"BIRT": "Born", "DEAT": "Died", "BAPM": "Baptized", "CHR": "Christened", "BURI": "Buried",
                 "CREM": "Cremated", "GRAD": "Graduated", "RESI": "Lived in", "OCCU": "Worked as", "EDUC": "Studied",
                 "RETI": "Retired", "EMIG": "Emigrated", "IMMI": "Immigrated", "NATU": "Naturalized",
                 "MILI": "Military service", "EVEN": "Other event"}
COUPLE_EVENTS = {"MARR": "Married", "ENGA": "Engaged", "DIV": "Divorced", "EVEN": "Other event"}
ONE_EACH = {"BIRT", "DEAT", "BURI", "CREM"}  # a person has at most one of these
NAME_TYPES = {"birth": "Birth name", "married": "Married name", "aka": "Also known as", "nickname": "Nickname"}
# name particles that belong to the surname: "Anna Van Dyke"
PARTICLES = {"van", "von", "de", "der", "den", "del", "della", "di", "da", "du", "la", "le", "st", "st.", "mac", "ter"}

MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_MONTH_NAMES = {m.lower(): i for i, m in enumerate(MONTHS, 1)}
_MONTH_NAMES.update({name: i for i, name in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
     "december"), 1)})
_QUALIFIERS = {"about": "ABT", "abt": "ABT", "around": "ABT", "circa": "ABT", "ca": "ABT", "c": "ABT", "~": "ABT",
               "estimated": "EST", "est": "EST", "calculated": "CAL", "cal": "CAL",
               "before": "BEF", "bef": "BEF", "after": "AFT", "aft": "AFT"}
_WORDS = {"ABT": "about", "EST": "estimated", "CAL": "calculated", "BEF": "before", "AFT": "after"}


class Problem(errors.Problem):
    """A date, place or event that cannot be used; the message is shown to the person who typed it."""


@dataclass(frozen=True)
class Date:
    gedcom: str       # "12 JUN 1984", "ABT 1950", "BET 1950 AND 1955"
    sort: str         # "1984-06-12", "1950", "1950"


# ---- dates ---------------------------------------------------------------------------------------------------------

def day_first(con) -> bool:
    """Whether dates written all in numbers are day first, 25/12/1990 (Settings → Whose photos), not month first."""
    row = con.execute("SELECT value FROM settings WHERE key = 'day_first'").fetchone()
    return bool(row and json.loads(row[0]))


def parse_date(text: str, day_first: bool = False) -> Date | None:
    """A date as people type it, or as GEDCOM writes it: "1984-06-12", "June 12, 1984", "12 Jun 1984", "6/12/1984"
    (month first, or with day_first day first), "Jun 1984", "1984", "about 1950", "before 1960", "between 1950 and
    1955", "1950-1955", "ABT 1950". None for an empty one. Raises Problem for one it can't read, or a range that ends
    before it starts."""
    text = " ".join(str(text or "").replace(",", " ").split())
    if not text:
        return None
    low = text.lower()
    if m := re.fullmatch(r"(?:bet|between)\s+(.+?)\s+(?:and|-)\s+(.+)", low) or re.fullmatch(r"(\d{4})\s*-\s*(\d{4})", low):
        a, b = _in_order(exact_date(m[1], day_first), exact_date(m[2], day_first), m)
        return Date(f"BET {a.gedcom} AND {b.gedcom}", a.sort)
    if m := re.fullmatch(r"from\s+(.+?)\s+to\s+(.+)", low):
        a, b = _in_order(exact_date(m[1], day_first), exact_date(m[2], day_first), m)
        return Date(f"FROM {a.gedcom} TO {b.gedcom}", a.sort)
    first, _, rest = low.partition(" ")
    if first.rstrip(".") in _QUALIFIERS and rest:
        d = exact_date(rest, day_first)
        return Date(f"{_QUALIFIERS[first.rstrip('.')]} {d.gedcom}", d.sort)
    return exact_date(low, day_first)


def _in_order(a: Date, b: Date, m) -> tuple[Date, Date]:
    if b.sort < a.sort[:len(b.sort)]:
        raise Problem(f"{m[1]} comes after {m[2]}: put the earlier one first.")
    return a, b


def exact_date(text: str, day_first: bool = False) -> Date:
    """One day, month or year as people type it (no "about", no range)."""
    t = text.strip().lower()
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t):
        return _ymd(int(m[1]), int(m[2]), int(m[3]))
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})", t):
        return _ym(int(m[1]), int(m[2]))
    if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", t):  # month first in the US, day first in most places
        return _ymd(int(m[3]), int(m[2]), int(m[1])) if day_first else _ymd(int(m[3]), int(m[1]), int(m[2]))
    if m := re.fullmatch(r"(\d{4})", t):
        if m[1] == "0000":
            raise Problem("There was no year 0.")
        return Date(m[1], m[1])
    parts = t.replace(".", " ").split()
    nums = [p for p in parts if p.isdigit()]
    months = [_MONTH_NAMES[p] for p in parts if p in _MONTH_NAMES]
    if len(months) == 1 and len(parts) == len(nums) + 1:
        if len(nums) == 1 and len(nums[0]) == 4:
            return _ym(int(nums[0]), months[0])
        if len(nums) == 2:
            day, year = (nums if len(nums[1]) == 4 else nums[::-1])
            if len(year) == 4:
                return _ymd(int(year), months[0], int(day))
    raise Problem(f"Can't read the date {text!r}. Write it like 1984-06-12, June 1984, 1984 or about 1950.")


def _ymd(y: int, m: int, d: int) -> Date:
    try:
        date(y, m, d)
    except ValueError:
        raise Problem(f"There is no {y}-{m:02d}-{d:02d}.") from None
    return Date(f"{d} {MONTHS[m - 1]} {y}", f"{y:04d}-{m:02d}-{d:02d}")


def _ym(y: int, m: int) -> Date:
    if not 1 <= m <= 12:
        raise Problem(f"There is no month {m}.")
    return Date(f"{MONTHS[m - 1]} {y}", f"{y:04d}-{m:02d}")


def show_date(gedcom: str | None) -> str:
    """A GEDCOM date for the pages: "12 JUN 1984" -> "June 12, 1984", "ABT 1950" -> "about 1950"."""
    if not gedcom:
        return ""
    words = []
    for token in gedcom.split():
        if token in _WORDS:
            words.append(_WORDS[token])
        elif token in ("BET", "AND", "FROM", "TO"):
            words.append(token.lower().replace("bet", "between"))
        elif token in MONTHS:
            words.append(date(2000, MONTHS.index(token) + 1, 1).strftime("%B"))
        else:
            words.append(token)
    text = " ".join(words)
    return re.sub(r"(\d{1,2}) ([A-Z][a-z]+) (\d{4})", r"\2 \1, \3", text)


def birth_iso(sort: str | None) -> str | None:
    """A sort date as person.birth_date (a whole date, for ages): only when the day is known."""
    return sort if sort and re.fullmatch(r"\d{4}-\d{2}-\d{2}", sort) else None


# ---- events --------------------------------------------------------------------------------------------------------

def for_person(con: sqlite3.Connection, pid: int) -> list[sqlite3.Row]:
    """pid's own events and those of their couples, in date order (undated last)."""
    return con.execute("""SELECT e.*, CASE WHEN e.person_id IS NULL
                                       THEN (SELECT name FROM person WHERE id = CASE e.couple_a WHEN :p THEN e.couple_b
                                                                                 ELSE e.couple_a END) END AS partner
                          FROM event e WHERE e.person_id = :p OR :p IN (e.couple_a, e.couple_b)
                          ORDER BY e.sort_date IS NULL, e.sort_date, e.id""", {"p": pid}).fetchall()


def save(con: sqlite3.Connection, event_id: int | None, *, tag: str, person_id: int | None = None,
         couple: tuple[int, int] | None = None, date_text: str = "", place: str = "", kind: str = "",
         note: str = "") -> int:
    """Add or change an event. Raises Problem. Doesn't commit."""
    tag = tag.upper()
    allowed = PERSON_EVENTS if person_id is not None else COUPLE_EVENTS
    if tag not in allowed or (person_id is None) == (couple is None):
        raise Problem("Unknown kind of event.")
    if couple is not None:
        a, b = min(couple), max(couple)
        if not con.execute("SELECT 1 FROM partner WHERE a = ? AND b = ?", (a, b)).fetchone():
            raise Problem("They are not partners: add the partner first.")
    d = parse_date(date_text, day_first(con))
    if d and not 1000 <= int(d.sort[:4]) <= date.today().year:  # typed: an imported tree may go back further
        raise Problem(f"{date_text.strip()}: a year from 1000 to this one, please." if int(d.sort[:4]) < 1000 else
                      f"{date_text.strip()} hasn't come yet.")
    place, kind, note = " ".join(place.split())[:200], " ".join(kind.split())[:80], note.strip()[:2000]
    if tag == "EVEN" and not kind:
        raise Problem("Say what the event was.")
    if event_id is None and person_id is not None and tag in ONE_EACH:
        if row := con.execute("SELECT id FROM event WHERE person_id = ? AND tag = ?", (person_id, tag)).fetchone():
            event_id = row[0]  # there is one already: this changes it
    a, b = (min(couple), max(couple)) if couple else (None, None)
    values = (person_id, a, b, tag, kind or None, d.gedcom if d else None, d.sort if d else None, place or None,
              note or None)
    if event_id is None:
        event_id = con.execute("""INSERT INTO event(person_id, couple_a, couple_b, tag, type, date, sort_date, place,
                                                    note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", values).lastrowid
    else:
        con.execute("""UPDATE event SET person_id = ?, couple_a = ?, couple_b = ?, tag = ?, type = ?, date = ?,
                       sort_date = ?, place = ?, note = ? WHERE id = ?""", (*values, event_id))
    if person_id is not None:
        _sync_person(con, person_id)
    return event_id


def delete(con: sqlite3.Connection, event_id: int) -> None:
    row = con.execute("SELECT person_id FROM event WHERE id = ?", (event_id,)).fetchone()
    con.execute("DELETE FROM event WHERE id = ?", (event_id,))
    if row and row[0] is not None:
        _sync_person(con, row[0])


def _sync_person(con: sqlite3.Connection, pid: int) -> None:
    birth = con.execute("SELECT sort_date FROM event WHERE person_id = ? AND tag = 'BIRT'", (pid,)).fetchone()
    died = con.execute("SELECT 1 FROM event WHERE person_id = ? AND tag IN ('DEAT', 'BURI', 'CREM')", (pid,)).fetchone()
    con.execute("UPDATE person SET birth_date = ?, deceased = ? WHERE id = ?",
                (birth_iso(birth[0]) if birth else None, int(died is not None), pid))


def set_deceased(con: sqlite3.Connection, pid: int, deceased: bool) -> None:
    """The "passed away" box: a death with no date, or none."""
    if deceased:
        if not con.execute("SELECT 1 FROM event WHERE person_id = ? AND tag = 'DEAT'", (pid,)).fetchone():
            con.execute("INSERT INTO event(person_id, tag) VALUES (?, 'DEAT')", (pid,))
    else:
        con.execute("DELETE FROM event WHERE person_id = ? AND tag IN ('DEAT', 'BURI', 'CREM')", (pid,))
    _sync_person(con, pid)


def move(con: sqlite3.Connection, old: int, new: int) -> None:
    """old is merged into new: old's events become new's, except those new already has one of (a birth...)."""
    for row in con.execute("SELECT id, tag FROM event WHERE person_id = ?", (old,)).fetchall():
        if row["tag"] in ONE_EACH and con.execute("SELECT 1 FROM event WHERE person_id = ? AND tag = ?",
                                                  (new, row["tag"])).fetchone():
            con.execute("DELETE FROM event WHERE id = ?", (row["id"],))
        else:
            con.execute("UPDATE event SET person_id = ? WHERE id = ?", (new, row["id"]))
    _sync_person(con, new)


def move_couple(con: sqlite3.Connection, old_pair: tuple[int, int], new_pair: tuple[int, int]) -> None:
    con.execute("UPDATE OR IGNORE event SET couple_a = ?, couple_b = ? WHERE couple_a = ? AND couple_b = ?",
                (min(new_pair), max(new_pair), min(old_pair), max(old_pair)))


# ---- names ---------------------------------------------------------------------------------------------------------

def split_name(name: str) -> tuple[str, str | None]:
    """(given names, surname) suggested for a name: the last word is the surname, with the particles before it
    ("Anna Van Dyke" -> Anna, Van Dyke). One word is a given name only (Grandpa, Rex)."""
    words = name.split()
    if len(words) < 2:
        return name.strip(), None
    start = len(words) - 1
    while start > 1 and words[start - 1].lower() in PARTICLES:
        start -= 1
    return " ".join(words[:start]), " ".join(words[start:])


def set_name_parts(con: sqlite3.Connection, pid: int, given: str, surname: str, name_type: str) -> None:
    if name_type not in ("birth", "married", "aka"):
        raise Problem("Unknown kind of name.")
    con.execute("""UPDATE person SET given_name = ?, surname = ?, name_type = ?, names_checked = 1 WHERE id = ?""",
                (" ".join(given.split()) or None, " ".join(surname.split()) or None, name_type, pid))


def add_name(con: sqlite3.Connection, pid: int, name: str, name_type: str) -> None:
    """Another name: a maiden name, a nickname, a name someone was also known as."""
    name = " ".join(name.split())
    if name_type not in NAME_TYPES:
        raise Problem("Unknown kind of name.")
    if not name or len(name) > 120:
        raise Problem("Give the name.")
    given, surname = (name, None) if name_type == "nickname" else split_name(name)
    con.execute("""INSERT OR IGNORE INTO person_name(person_id, name, given_name, surname, type)
                   VALUES (?, ?, ?, ?, ?)""", (pid, name, given, surname, name_type))


def other_names(con: sqlite3.Connection, pid: int) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM person_name WHERE person_id = ? ORDER BY type, name", (pid,)).fetchall()


def new_uid() -> str:
    return str(uuid.uuid4())


def ages(con: sqlite3.Connection, img, person_ids, births: dict[int, str] | None = None) -> dict[int, str]:
    """person id -> their age when the photo was taken, for the people in it, when its date is known (a camera date,
    or one set by hand: "0", or "0–1" when the date set spans a birthday). img: its taken_at, taken_at_source,
    date_from and date_to. births: their births' sort dates, if the caller has them already."""
    if not img["taken_at"] or img["taken_at_source"] == "mtime":
        return {}
    if births is None:
        births = dict(con.execute("SELECT person_id, sort_date FROM event WHERE tag = 'BIRT' AND sort_date IS NOT NULL"))
    if img["taken_at_source"] == "user" and img["date_from"] != img["date_to"]:
        first, last = date.fromisoformat(img["date_from"]), date.fromisoformat(img["date_to"])
        out = {}
        for pid in person_ids:
            a, b = age(births.get(pid), first), age(births.get(pid), last)
            if a and b:
                out[pid] = a if a == b else f"{a}–{b.removeprefix('about ')}"
            elif b:  # born during it
                out[pid] = "0"
        return out
    taken = date.fromisoformat(img["taken_at"][:10])
    return {pid: a for pid in person_ids if pid and (a := age(births.get(pid), taken))}


def age(birth_sort: str | None, on: date) -> str | None:
    """Someone's age on a day, from their birth's sort date: "11", or "about 11" when only the year (or the month,
    in that month) is known. None when unknown, or before they were born."""
    if not birth_sort:
        return None
    y = int(birth_sort[:4])
    if len(birth_sort) == 10:
        born = date.fromisoformat(birth_sort)
        years, exact = on.year - born.year - ((on.month, on.day) < (born.month, born.day)), True
    elif len(birth_sort) == 7:
        m = int(birth_sort[5:7])
        years, exact = on.year - y - (on.month < m), on.month != m
    else:
        years, exact = on.year - y, False
    if years < 0:
        return None
    return str(years) if exact else f"about {years}"
