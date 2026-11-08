"""Documents: photos and scans that are records of something (a birth certificate, an obituary, a letter), and what
each one is a record of: a person, or one of their events.

In GEDCOM exports a document is a source (SOUR), cited by the person or event it is about. A document is kept (see
cleanup.keep_images): a scanned certificate is exactly what the papers and blurry lists would otherwise suggest
removing.

Written sources are the other kind: a book, a website, a census page, a parish register, with no photo of it here
(the source and source_citation tables). Each is cited by the people and life events it bears out, with where in it
("page 12", the record's web address). They are SOUR records in GEDCOM too.
"""
import sqlite3
import uuid
from datetime import datetime

from lineagebox import errors, events

SOURCE_LIMITS = {"title": 200, "author": 120, "publication": 200, "note": 4000, "page": 300}

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


# ---- written sources -----------------------------------------------------------------------------------------------

def _clean(text: str | None, field: str) -> str | None:
    text = (text or "").strip() if field == "note" else " ".join((text or "").split())
    return text[:SOURCE_LIMITS[field]] or None


def save_source(con: sqlite3.Connection, source_id: int | None, title: str, author: str = "", publication: str = "",
                note: str = "", added_by: int | None = None, uid: str | None = None) -> int:
    """Add a written source, or change one. Returns its id. Doesn't commit."""
    title = _clean(title, "title")
    if not title:
        raise Problem("Give the source a title: the book, the website, the record.")
    values = (title, _clean(author, "author"), _clean(publication, "publication"), _clean(note, "note"))
    if source_id is not None:
        con.execute("UPDATE source SET title = ?, author = ?, publication = ?, note = ? WHERE id = ?",
                    (*values, source_id))
        return source_id
    return con.execute("""INSERT INTO source(title, author, publication, note, uid, added_by, created_at)
                          VALUES (?, ?, ?, ?, ?, ?, ?)""",
                       (*values, uid or str(uuid.uuid4()), added_by,
                        datetime.now().isoformat(timespec="seconds"))).lastrowid


def find_source(con: sqlite3.Connection, title: str, author: str = "") -> int | None:
    """A source already here with this title (and author, when it has one): typed again, it is the same one."""
    row = con.execute("""SELECT id FROM source WHERE lower(title) = lower(?)
                         AND (author IS NULL OR ? = '' OR lower(author) = lower(?)) ORDER BY id LIMIT 1""",
                      (" ".join(title.split()), author.strip(), " ".join(author.split()))).fetchone()
    return row[0] if row else None


def cite_source(con: sqlite3.Connection, source_id: int, target: str, page: str = "") -> int:
    """target: 'p:<person id>' (the source is about the person) or 'e:<event id>' (bears out one of their events).
    The same source cited again at the same place is one citation. Returns its id. Doesn't commit."""
    kind, _, number = target.partition(":")
    if not number.isdecimal() or kind not in ("p", "e"):
        raise Problem("Pick the person or one of their life events.")
    column, table = ("person_id", "person") if kind == "p" else ("event_id", "event")
    if not con.execute(f"SELECT 1 FROM {table} WHERE id = ?", (int(number),)).fetchone():
        raise Problem("No such person or event.")
    page = _clean(page, "page")
    if row := con.execute(f"SELECT id FROM source_citation WHERE source_id = ? AND {column} = ? AND page IS ?",
                          (source_id, int(number), page)).fetchone():
        return row[0]
    return con.execute(f"INSERT INTO source_citation(source_id, {column}, page) VALUES (?, ?, ?)",
                       (source_id, int(number), page)).lastrowid


def uncite_source(con: sqlite3.Connection, citation_id: int) -> None:
    """Take a citation off; a source nothing cites any more goes with it (nor anything it holds from another tree).
    Doesn't commit."""
    row = con.execute("SELECT source_id FROM source_citation WHERE id = ?", (citation_id,)).fetchone()
    con.execute("DELETE FROM source_citation WHERE id = ?", (citation_id,))
    if row and not con.execute("SELECT 1 FROM source_citation WHERE source_id = ?", (row[0],)).fetchone():
        con.execute("DELETE FROM source WHERE id = ?", (row[0],))


def sources_for(con: sqlite3.Connection, pid: int) -> list[dict]:
    """The written sources cited for someone or one of their (or their couples') life events, for their page: each
    citation with its source and what it bears out, those of the person first."""
    out = []
    for r in con.execute("""SELECT c.id, c.page, c.person_id, c.event_id, s.id AS source_id, s.title, s.author,
                                   s.publication, s.note
                            FROM source_citation c JOIN source s ON s.id = c.source_id
                            LEFT JOIN event e ON e.id = c.event_id
                            WHERE c.person_id = :p OR e.person_id = :p OR :p IN (e.couple_a, e.couple_b)
                            ORDER BY c.event_id IS NOT NULL, e.sort_date, s.title COLLATE NOCASE, c.id""", {"p": pid}):
        what = "them"
        if r["event_id"] and (e := con.execute("SELECT * FROM event WHERE id = ?", (r["event_id"],)).fetchone()):
            what = _event_label(e)
        out.append({**dict(r), "what": what})
    return out


def source_choices(con: sqlite3.Connection, limit: int = 500) -> list[sqlite3.Row]:
    """Sources one may cite again (those the viewer's connection holds), most cited first."""
    return con.execute("""SELECT s.id, s.title, s.author, count(c.id) AS cited FROM source s
                          LEFT JOIN source_citation c ON c.source_id = s.id
                          GROUP BY s.id ORDER BY cited DESC, s.title COLLATE NOCASE LIMIT ?""", (limit,)).fetchall()


def event_citations(con: sqlite3.Connection, event_ids) -> dict[int, dict]:
    """event id -> {'documents': [(image id, sha256, title)], 'sources': [(title, page)]}: what bears each out, for
    the life timeline."""
    ids = sorted({i for i in event_ids if i})
    out = {i: {"documents": [], "sources": []} for i in ids}
    if not ids:
        return out
    marks = ",".join("?" * len(ids))
    for eid, image_id, sha, title in con.execute(f"""SELECT c.event_id, d.image_id, i.sha256, d.title FROM citation c
                                                     JOIN document d ON d.image_id = c.image_id
                                                     JOIN image i ON i.id = d.image_id
                                                     WHERE c.event_id IN ({marks}) ORDER BY d.title""", ids):
        out[eid]["documents"].append((image_id, sha, title))
    for eid, title, page in con.execute(f"""SELECT c.event_id, s.title, c.page FROM source_citation c
                                            JOIN source s ON s.id = c.source_id WHERE c.event_id IN ({marks})
                                            ORDER BY s.title""", ids):
        out[eid]["sources"].append((title, page))
    return out
