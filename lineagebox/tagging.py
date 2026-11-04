"""Tags, people and dates given to photos by hand, on a photo's page or to many photos at once.

The AI's tags are image_tag rows with source 'ai' on the photo it described; similar shots share that photo's
description, and so its tags. A tag added by hand is source 'user'. One taken off is kept as source 'not': describing
the photo again leaves it off, and a similar shot's description no longer gives it to this photo.

People are found by their faces; image_person holds those added by hand, for photos where their face was not found.

A date set by hand replaces the automatic one (camera, file name, folder name or file time) as taken_at_source 'user'.
"""
import json
import sqlite3
from datetime import datetime

from lineagebox import catalog, dates, errors, people, search
from lineagebox.catalog import tag_name
from lineagebox.folders import FolderIndex


class Problem(errors.Problem):
    """A change that cannot be made; the message is shown to the person who asked for it."""


def for_image(con: sqlite3.Connection, image_id: int) -> list[dict]:
    """A photo's tags, the AI's in the order it gave them and then those added by hand: [{"name", "mine"}]."""
    a = catalog.analysis_for(con, image_id)
    described = a["image_id"] if a else image_id
    rows = con.execute("""SELECT t.name, it.source, it.image_id FROM image_tag it JOIN tag t ON t.id = it.tag_id
                          WHERE it.image_id IN (?, ?) ORDER BY it.source = 'user', it.rowid""",
                       (image_id, described)).fetchall()
    off = {r["name"] for r in rows if r["image_id"] == image_id and r["source"] == "not"}
    out, seen = [], set()
    for r in rows:
        if r["source"] == "not" or r["name"] in off or r["name"] in seen:
            continue
        if r["image_id"] != image_id and r["source"] != "ai":  # the described photo's own hand-made tags
            continue
        seen.add(r["name"])
        out.append({"name": r["name"], "mine": r["source"] == "user"})
    return out


def add(con: sqlite3.Connection, image_ids: list[int], name: str) -> int:
    """Give photos a tag. Returns how many photos."""
    name = tag_name(name)
    if not name:
        raise Problem("Type a tag")
    ids = _existing(con, image_ids)
    con.execute("INSERT OR IGNORE INTO tag(name) VALUES (?)", (name,))
    tag_id = con.execute("SELECT id FROM tag WHERE name = ?", (name,)).fetchone()[0]
    con.executemany("""INSERT INTO image_tag(image_id, tag_id, source) VALUES (?, ?, 'user')
                       ON CONFLICT(image_id, tag_id) DO UPDATE SET source = 'user'""", ((i, tag_id) for i in ids))
    _reindex(con, ids)
    return len(ids)


def remove(con: sqlite3.Connection, image_ids: list[int], name: str) -> int:
    """Take a tag off photos, for good. Returns how many photos."""
    row = con.execute("SELECT id FROM tag WHERE name = ?", (tag_name(name),)).fetchone()
    if row is None:
        return 0
    ids = _existing(con, image_ids)
    con.executemany("""INSERT INTO image_tag(image_id, tag_id, source) VALUES (?, ?, 'not')
                       ON CONFLICT(image_id, tag_id) DO UPDATE SET source = 'not'""", ((i, row[0]) for i in ids))
    _reindex(con, ids)
    return len(ids)


def names(con: sqlite3.Connection, limit: int = 1000) -> list[str]:
    """Tags to suggest while typing one, most used first."""
    return [r["name"] for r in catalog.tags(con, limit=limit)[0]]


def linked_people(con: sqlite3.Connection, image_id: int) -> list[sqlite3.Row]:
    """People added to a photo by hand, but not those whose face in it has their name."""
    return con.execute("""SELECT p.id, p.name FROM image_person l JOIN person p ON p.id = l.person_id
                          WHERE l.image_id = :i AND p.id NOT IN (SELECT person_id FROM face
                                                                WHERE image_id = :i AND person_id IS NOT NULL)
                          ORDER BY p.name COLLATE NOCASE""", {"i": image_id}).fetchall()


def add_person(con: sqlite3.Connection, image_ids: list[int], name: str, pid: int | None = None,
               among: set[int] | None = None, added_by: int | None = None) -> tuple[int, int]:
    """Add someone to photos (added to the catalog if the name is new; see people.person_id for pid, among and
    added_by), except photos where a face already has their name. Returns (their id, how many photos they were added
    to)."""
    if not name.split():
        raise Problem("Type a name")
    try:
        pid = people.person_id(con, name, pid, among, added_by)
    except people.Problem as e:
        raise Problem(str(e)) from None
    now = datetime.now().isoformat(timespec="seconds")
    ids = [i for i in _existing(con, image_ids)
           if not con.execute("SELECT 1 FROM face WHERE image_id = ? AND person_id = ?", (i, pid)).fetchone()]
    added = sum(con.execute("INSERT OR IGNORE INTO image_person(image_id, person_id, added_at) VALUES (?, ?, ?)",
                            (i, pid, now)).rowcount for i in ids)
    _reindex(con, ids)
    return pid, added


def remove_person(con: sqlite3.Connection, image_id: int, person_id: int) -> None:
    con.execute("DELETE FROM image_person WHERE image_id = ? AND person_id = ?", (image_id, person_id))
    _reindex(con, [image_id])


def set_date(con: sqlite3.Connection, image_ids: list[int], text: str) -> tuple[int, dates.Span | None]:
    """Date photos by hand, as exactly as it is known (see dates.photo_date), or with no text give them back their
    automatic date. Each is placed at the middle of the date's span; a day given for a photo with a camera time keeps
    that time (a camera with its clock wrong). The automatic date is kept, to go back to. Returns (photos changed, the
    date)."""
    try:
        span = dates.photo_date(text, dates.south(con), dates.day_first(con))
    except dates.Problem as e:
        raise Problem(str(e)) from None
    ids = _existing(con, image_ids)
    rows = con.execute("""SELECT id, taken_at, taken_at_source, auto_taken_at, auto_taken_at_source FROM image
                          WHERE id IN (SELECT value FROM json_each(?))""", (json.dumps(ids),)).fetchall()
    changed = []
    for r in rows:
        if span is None:
            if r["taken_at_source"] == "user":
                con.execute("""UPDATE image SET taken_at = auto_taken_at, taken_at_source = auto_taken_at_source,
                                                date_text = NULL, date_from = NULL, date_to = NULL,
                                                auto_taken_at = NULL, auto_taken_at_source = NULL WHERE id = ?""",
                            (r["id"],))
                changed.append(r["id"])
            continue
        auto, source = ((r["auto_taken_at"], r["auto_taken_at_source"]) if r["taken_at_source"] == "user"
                        else (r["taken_at"], r["taken_at_source"]))
        taken = f"{span.middle.isoformat()}T00:00:00"
        if span.exact and auto and source in ("exif", "camera", "filename"):
            taken = f"{span.first.isoformat()}T{auto[11:19] or '00:00:00'}"
        con.execute("""UPDATE image SET taken_at = ?, taken_at_source = 'user', date_text = ?, date_from = ?,
                                        date_to = ?, auto_taken_at = ?, auto_taken_at_source = ? WHERE id = ?""",
                    (taken, span.text, span.first.isoformat(), span.last.isoformat(), auto, source, r["id"]))
        changed.append(r["id"])
    _reindex(con, changed)
    return len(changed), span


def _existing(con: sqlite3.Connection, image_ids: list[int]) -> list[int]:
    return [r[0] for r in con.execute("SELECT id FROM image WHERE id IN (SELECT value FROM json_each(?)) ORDER BY id",
                                      (json.dumps(list(image_ids)),))]


def _reindex(con: sqlite3.Connection, ids: list[int]) -> None:
    folders = FolderIndex(con)
    for i in ids:
        search.index_image(con, i, folders)
