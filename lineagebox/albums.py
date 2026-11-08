"""Albums: photos picked by hand, kept in the order the family wants them, with a cover and a caption for each.

A wedding or a reunion kept the way the family wants it, while tours stay rule-based (tours.py). An account makes an
album from the photos it sees (a member: their part of the family, their uploads and what others shared with them; an
admin: anything), and at home without family accounts anyone does. Its maker, or an admin, adds and takes out photos,
puts them in order, picks the cover and writes the album's caption for each photo.

The caption in an album is the album's own: "Grandma, who flew in from Ohio" belongs to the wedding album, while the
photo's own caption (comments.py) is the one everyone who sees the photo shares, on its page, in search and in tours,
and anyone who sees it may change it. Where the album has no words for a photo, the photo's own caption shows instead,
so nothing has to be written twice.

An album is shared like a tour (sharing.py): those it is shared with see it, with the photos its maker may pass on
(album_passed: those of the maker's own part of the family and uploads, never what others shared with the maker),
worked out again whenever it is changed, shared or opened (routes/albums.py). A member's connection has only the
albums that are theirs or shared with them, and of each only the photos they see (access.SCOPED).
"""
import json
import sqlite3
from datetime import datetime

from lineagebox import errors
from lineagebox.catalog import VISIBLE

MOST = 2000      # photos in one album: a slideshow and a page that still open quickly
NAME = 120       # characters in an album's name
ABOUT = 500      # in the words under its name
CAPTION = 500    # in its caption for one photo


class Problem(errors.Problem):
    """What was asked of an album can't be done; the message says why, to whoever asked."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _words(text, most: int) -> str:
    return " ".join(str(text or "").split())[:most]


def theirs(viewer, album) -> bool:
    """Whether the viewer may change the album: an admin any, a member their own."""
    return viewer.admin or (album is not None and album["account_id"] is not None
                            and album["account_id"] == viewer.account_id)


def create(con: sqlite3.Connection, name: str, account_id: int | None, about: str = "") -> int:
    """A new, empty album; account_id None: the admins' (or, at home, everyone's). Returns its id."""
    name = _words(name, NAME)
    if not name:
        raise Problem("Give the album a name.")
    now = _now()
    return con.execute("""INSERT INTO main.album(name, about, account_id, created_at, changed_at)
                          VALUES (?, ?, ?, ?, ?)""", (name, _words(about, ABOUT), account_id, now, now)).lastrowid


def get(con: sqlite3.Connection, album_id: int) -> dict | None:
    """One album, as the connection sees it (a member's: only their own and those shared with them)."""
    row = con.execute("SELECT * FROM album WHERE id = ?", (album_id,)).fetchone()
    return dict(row) if row else None


def by_name(con: sqlite3.Connection, name: str, viewer) -> dict | None:
    """The album of this name the viewer may add to, whatever the case of its letters: a member's own; for an admin,
    the admins' own first, then anyone's (as changeable lists them)."""
    if viewer.admin:
        row = con.execute("""SELECT * FROM main.album WHERE lower(name) = lower(?)
                             ORDER BY account_id IS NOT NULL, changed_at DESC, id DESC LIMIT 1""",
                          (_words(name, NAME),)).fetchone()
    else:
        row = con.execute("""SELECT * FROM main.album WHERE lower(name) = lower(?) AND account_id = ?
                             ORDER BY changed_at DESC, id DESC LIMIT 1""",
                          (_words(name, NAME), viewer.account_id)).fetchone()
    return dict(row) if row else None


def changeable(con: sqlite3.Connection, viewer) -> list[dict]:
    """The albums the viewer may add photos to, newest changed first: an admin's are every album (the admins' own
    first), a member's their own."""
    if viewer.admin:
        rows = con.execute("""SELECT id, name, account_id FROM main.album
                              ORDER BY account_id IS NOT NULL, changed_at DESC, id DESC""")
    else:
        rows = con.execute("""SELECT id, name, account_id FROM main.album WHERE account_id = ?
                              ORDER BY changed_at DESC, id DESC""", (viewer.account_id,))
    return [dict(r) for r in rows]


def listing(con: sqlite3.Connection) -> list[dict]:
    """Every album the connection sees, newest changed first, each with how many of its photos are there to see and
    its cover (the one picked, else the first)."""
    out = []
    counts = dict(con.execute(f"""SELECT a.album_id, count(*) FROM album_photo a JOIN image i ON i.id = a.image_id
                                  WHERE {VISIBLE} GROUP BY a.album_id"""))
    for r in con.execute("SELECT * FROM album ORDER BY changed_at DESC, id DESC").fetchall():
        out.append({**dict(r), "count": counts.get(r["id"], 0), "cover": cover(con, r)})
    return out


def cover(con: sqlite3.Connection, album) -> str | None:
    """The sha256 of the album's cover, as the connection sees its photos: the one picked, else its first."""
    row = None
    if album["cover_id"] is not None:
        row = con.execute(f"""SELECT i.sha256 FROM album_photo a JOIN image i ON i.id = a.image_id
                              WHERE a.album_id = ? AND a.image_id = ? AND {VISIBLE}""",
                          (album["id"], album["cover_id"])).fetchone()
    if row is None:
        row = con.execute(f"""SELECT i.sha256 FROM album_photo a JOIN image i ON i.id = a.image_id
                              WHERE a.album_id = ? AND {VISIBLE} ORDER BY a.position LIMIT 1""",
                          (album["id"],)).fetchone()
    return row[0] if row else None


def photos(con: sqlite3.Connection, album_id: int) -> list[dict]:
    """The album's photos in their order, as the connection sees them, each with the album's caption for it
    ("caption") and the photo's own family caption ("own")."""
    rows = con.execute(f"""
        SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.date_text, i.date_from, i.date_to, i.media,
               i.duration, i.playable, a.position, a.caption,
               (SELECT c.text FROM comment c WHERE c.image_id = i.id AND c.kind = 'caption') AS own
        FROM album_photo a JOIN image i ON i.id = a.image_id
        WHERE a.album_id = ? AND {VISIBLE} ORDER BY a.position, a.added_at""", (album_id,)).fetchall()
    return [dict(r) for r in rows]


def photo_ids(con: sqlite3.Connection, album_id: int) -> list[int]:
    """The ids of the album's photos in their order, as the connection sees them (a photo page's ← →)."""
    return [r[0] for r in con.execute(f"""SELECT i.id FROM album_photo a JOIN image i ON i.id = a.image_id
                                          WHERE a.album_id = ? AND {VISIBLE} ORDER BY a.position, a.added_at""",
                                      (album_id,))]


def _all_ids(con: sqlite3.Connection, album_id: int) -> list[int]:
    """Every photo of the album in its order, also those the one changing it can't see: on a connection that sees
    everything."""
    return [r[0] for r in con.execute("""SELECT image_id FROM main.album_photo WHERE album_id = ?
                                         ORDER BY position, added_at""", (album_id,))]


def _touched(con: sqlite3.Connection, album_id: int) -> None:
    con.execute("UPDATE main.album SET changed_at = ? WHERE id = ?", (_now(), album_id))


def add(con: sqlite3.Connection, album_id: int, image_ids) -> int:
    """Add photos at the end of the album, in the order given; those in it already stay where they are. Returns how
    many were added. Raises Problem past MOST photos. On a connection that sees everything; the caller checked that
    the photos are the viewer's to see."""
    there = set(_all_ids(con, album_id))
    new = [i for i in dict.fromkeys(image_ids) if i not in there]
    if len(there) + len(new) > MOST:
        raise Problem(f"An album holds up to {MOST:,} photos; this one has {len(there):,}.")
    start = con.execute("SELECT coalesce(max(position) + 1, 0) FROM main.album_photo WHERE album_id = ?",
                        (album_id,)).fetchone()[0]
    now = _now()
    con.executemany("INSERT INTO main.album_photo(album_id, image_id, position, added_at) VALUES (?, ?, ?, ?)",
                    ((album_id, i, start + k, now) for k, i in enumerate(new)))
    if new:
        _touched(con, album_id)
    return len(new)


def remove(con: sqlite3.Connection, album_id: int, image_ids) -> int:
    """Take photos out of the album (the photos themselves stay where they are). Returns how many went."""
    gone = con.execute("""DELETE FROM main.album_photo WHERE album_id = ?
                          AND image_id IN (SELECT value FROM json_each(?))""",
                       (album_id, json.dumps(list(image_ids)))).rowcount
    if gone:
        con.execute("UPDATE main.album SET cover_id = NULL WHERE id = ? AND cover_id NOT IN "
                    "(SELECT image_id FROM main.album_photo WHERE album_id = ?)", (album_id, album_id))
        _touched(con, album_id)
    return gone


def reorder(con: sqlite3.Connection, album_id: int, image_ids) -> None:
    """Put the album's photos in this order. Photos left out of the list (those the one ordering can't see, or added
    meanwhile) keep their order after the others."""
    now = _all_ids(con, album_id)
    there = set(now)
    first = [i for i in dict.fromkeys(image_ids) if i in there]
    order = first + [i for i in now if i not in set(first)]
    con.executemany("UPDATE main.album_photo SET position = ? WHERE album_id = ? AND image_id = ?",
                    ((k, album_id, i) for k, i in enumerate(order)))
    _touched(con, album_id)


def set_cover(con: sqlite3.Connection, album_id: int, image_id: int) -> None:
    if image_id not in _all_ids(con, album_id):
        raise Problem("That photo isn't in the album.")
    con.execute("UPDATE main.album SET cover_id = ? WHERE id = ?", (image_id, album_id))
    _touched(con, album_id)


def set_caption(con: sqlite3.Connection, album_id: int, image_id: int, text: str) -> str:
    """The album's words for one of its photos ("" takes them off: the photo's own caption shows again)."""
    text = " ".join(str(text or "").split())[:CAPTION]
    if not con.execute("UPDATE main.album_photo SET caption = ? WHERE album_id = ? AND image_id = ?",
                       (text, album_id, image_id)).rowcount:
        raise Problem("That photo isn't in the album.")
    _touched(con, album_id)
    return text


def rename(con: sqlite3.Connection, album_id: int, name: str, about: str) -> None:
    name = _words(name, NAME)
    if not name:
        raise Problem("Give the album a name.")
    con.execute("UPDATE main.album SET name = ?, about = ?, changed_at = ? WHERE id = ?",
                (name, _words(about, ABOUT), _now(), album_id))


def delete(con: sqlite3.Connection, album_id: int) -> None:
    """The album goes, with its order and captions and whom it was shared with; its photos stay where they are."""
    con.execute("DELETE FROM main.album WHERE id = ?", (album_id,))


def containing(con: sqlite3.Connection, image_id: int) -> list[dict]:
    """The albums the connection sees that hold this photo (its page lists them)."""
    return [dict(r) for r in con.execute("""SELECT al.id, al.name FROM album al JOIN album_photo a ON a.album_id = al.id
                                            WHERE a.image_id = ? ORDER BY al.name COLLATE NOCASE""", (image_id,))]
