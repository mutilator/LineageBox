"""Documents: photos and scans that are records of something (a birth certificate, an obituary, a letter), and what
each one is a record of: a person, or one of their events.

In GEDCOM exports a document is a source (SOUR), cited by the person or event it is about. A document is kept (see
cleanup.keep_images): a scanned certificate is exactly what the papers and blurry lists would otherwise suggest
removing.
"""
import sqlite3
from datetime import datetime

from lineagebox import errors, events

KINDS = ("Birth certificate", "Death certificate", "Marriage certificate", "Obituary", "Newspaper clipping", "Letter",
         "Census record", "Military record", "Diploma", "Passport or ID", "Photo record", "Other")


class Problem(errors.Problem):
    """A change that cannot be made; the message is shown to the person who asked for it."""


def get(con: sqlite3.Connection, image_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM document WHERE image_id = ?", (image_id,)).fetchone()


def save(con: sqlite3.Connection, image_id: int, kind: str, title: str) -> None:
    kind, title = " ".join(kind.split())[:60], " ".join(title.split())[:200]
    if not kind:
        raise Problem("Say what kind of document it is.")
    now = datetime.now().isoformat(timespec="seconds")
    con.execute("""INSERT INTO document(image_id, kind, title, created_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT(image_id) DO UPDATE SET kind = excluded.kind, title = excluded.title""",
                (image_id, kind, title or kind, now))
    con.execute("UPDATE image SET kept_at = coalesce(kept_at, ?) WHERE id = ?", (now, image_id))


def remove(con: sqlite3.Connection, image_id: int) -> None:
    con.execute("DELETE FROM document WHERE image_id = ?", (image_id,))


def cite(con: sqlite3.Connection, image_id: int, target: str) -> None:
    """target: 'p:<person id>' (a record of the person) or 'e:<event id>' (of one of their events)."""
    if get(con, image_id) is None:
        raise Problem("Mark the photo as a document first.")
    kind, _, number = target.partition(":")
    if not number.isdecimal() or kind not in ("p", "e"):
        raise Problem("Pick a person or an event.")
    column = "person_id" if kind == "p" else "event_id"
    table = "person" if kind == "p" else "event"
    if not con.execute(f"SELECT 1 FROM {table} WHERE id = ?", (int(number),)).fetchone():
        raise Problem("No such person or event.")
    if not con.execute(f"SELECT 1 FROM citation WHERE image_id = ? AND {column} = ?", (image_id, int(number))).fetchone():
        con.execute(f"INSERT INTO citation(image_id, {column}) VALUES (?, ?)", (image_id, int(number)))


def uncite(con: sqlite3.Connection, citation_id: int) -> None:
    con.execute("DELETE FROM citation WHERE id = ?", (citation_id,))


def citations(con: sqlite3.Connection, image_id: int) -> list[dict]:
    """What a document is a record of, for its page: [{'id', 'person_id', 'label'}]."""
    out = []
    for r in con.execute("""SELECT c.id, c.person_id, c.event_id, p.name FROM citation c
                            LEFT JOIN person p ON p.id = c.person_id WHERE c.image_id = ? ORDER BY c.id""", (image_id,)):
        if r["person_id"]:
            out.append({"id": r["id"], "person_id": r["person_id"], "label": r["name"]})
        elif e := con.execute("SELECT * FROM event WHERE id = ?", (r["event_id"],)).fetchone():
            whose = _whose(con, e)
            out.append({"id": r["id"], "person_id": e["person_id"] or e["couple_a"],
                        "label": f"{whose}: {_event_label(e)}"})
    return out


def targets(con: sqlite3.Connection) -> list[tuple[int, str, list[tuple[int, str]]]]:
    """Everyone, with their events, for choosing what a document is a record of: (person id, name, [(event id,
    label)])."""
    evts: dict[int, list[tuple[int, str]]] = {}
    for e in con.execute("SELECT * FROM event ORDER BY sort_date IS NULL, sort_date, id"):
        for pid in (e["person_id"], e["couple_a"], e["couple_b"]):
            if pid is not None:
                evts.setdefault(pid, []).append((e["id"], _event_label(e)))
    return [(r["id"], r["name"], evts.get(r["id"], []))
            for r in con.execute("SELECT id, name FROM person ORDER BY name COLLATE NOCASE")]


def for_person(con: sqlite3.Connection, pid: int) -> list[sqlite3.Row]:
    """The documents that are records of someone, or of one of their events."""
    return con.execute("""SELECT DISTINCT d.image_id, d.kind, d.title, i.sha256 FROM document d
                          JOIN image i ON i.id = d.image_id JOIN citation c ON c.image_id = d.image_id
                          LEFT JOIN event e ON e.id = c.event_id
                          WHERE c.person_id = :p OR e.person_id = :p OR :p IN (e.couple_a, e.couple_b)
                          ORDER BY d.title""", {"p": pid}).fetchall()


def _event_label(e) -> str:
    names = events.COUPLE_EVENTS if e["person_id"] is None else events.PERSON_EVENTS
    label = e["type"] if e["tag"] == "EVEN" and e["type"] else names.get(e["tag"], e["tag"])
    return label + (f" {events.show_date(e['date'])}" if e["date"] else "")


def _whose(con, e) -> str:
    ids = [e["person_id"]] if e["person_id"] is not None else [e["couple_a"], e["couple_b"]]
    names = [con.execute("SELECT name FROM person WHERE id = ?", (i,)).fetchone()[0] for i in ids]
    return " and ".join(names)
