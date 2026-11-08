"""Sharing: an account lets other accounts see a folder, a photo, a tour or an album.

A member shares what is theirs: a folder of their uploads (everything in it and below, now and later), a photo they
uploaded, a tour or an album they made; an admin, any of those. Each is shared with one member, or with everyone who
has an account. Those it is shared with see its photos as they see their own family's (access.shared_with): on Folders,
the tour's or album's page, Home, search, the timeline and the map. People in them from outside their part of the family are named
without a link, as on any photo they see.

A tour picks its photos again each time it is opened, from what its maker sees. Sharing it shares those photos: they are
kept in tour_photo whenever it is saved, shared or opened (remember), and that is what the others may see. Photos
shared with the maker by someone else are not passed on: a tour is shared as its maker's own part of the family and
uploads have it (routes/tours.py). An album is shared the same way: its photos are picked by hand, and those its maker
may pass on are kept in album_passed (remember_album, routes/albums.py). A photo an admin hid is never shared, and what
an account shared stops while it is switched off.
"""
import sqlite3
from datetime import datetime

from lineagebox import access, outbox
from lineagebox.catalog import under

KINDS = ("folder", "image", "tour", "album")
EVERYONE = "everyone with an account"


def share(con: sqlite3.Connection, account_id: int, kind: str, item_id: int, with_id: int | None) -> None:
    """Share one folder, photo, tour or album (kind, its id) with an account (None: everyone). One already shared so
    stays as it is."""
    assert kind in KINDS
    made = con.execute(f"""INSERT OR IGNORE INTO main.share(account_id, {kind}_id, with_id, created_at)
                           VALUES (?, ?, ?, ?)""",
                       (account_id, item_id, with_id, datetime.now().isoformat(timespec="seconds")))
    if made.rowcount:  # news for whoever it is shared with (New for you)
        outbox.shared(con, made.lastrowid)


def is_shared(con: sqlite3.Connection, kind: str, item_id: int) -> bool:
    assert kind in KINDS
    return con.execute(f"SELECT 1 FROM main.share WHERE {kind}_id = ? LIMIT 1", (item_id,)).fetchone() is not None


def of(con: sqlite3.Connection, kind: str, item_id: int) -> list[dict]:
    """Who one folder, photo, tour or album is shared with, and by whom, on a connection that sees everyone."""
    assert kind in KINDS
    return [dict(r) for r in con.execute(f"""
        SELECT s.id, s.account_id AS by_id, s.with_id, s.created_at,
               w.person_id AS with_person, wp.name AS with_name, w.email AS with_email,
               b.person_id AS by_person, bp.name AS by_name, b.email AS by_email
        FROM main.share s JOIN main.account b ON b.id = s.account_id LEFT JOIN main.person bp ON bp.id = b.person_id
        LEFT JOIN main.account w ON w.id = s.with_id LEFT JOIN main.person wp ON wp.id = w.person_id
        WHERE s.{kind}_id = ? ORDER BY s.with_id IS NOT NULL, coalesce(wp.name, w.email) COLLATE NOCASE""",
                                         (item_id,))]


def everything(con: sqlite3.Connection, account_id: int | None = None) -> list[dict]:
    """What an account shares (None: what everyone shares), each with what it is (kind, item_id, its name) and with
    whom, on a connection that sees everything. Newest first."""
    rows = con.execute(f"""
        SELECT s.id, s.folder_id, s.image_id, s.tour_id, s.album_id, s.account_id AS by_id, s.with_id, s.created_at,
               f.name AS folder_name, f.path AS folder_path, t.name AS tour_name, al.name AS album_name, i.sha256,
               w.person_id AS with_person, wp.name AS with_name, w.email AS with_email,
               b.person_id AS by_person, bp.name AS by_name, b.email AS by_email, b.disabled_at AS by_disabled
        FROM main.share s JOIN main.account b ON b.id = s.account_id LEFT JOIN main.person bp ON bp.id = b.person_id
        LEFT JOIN main.account w ON w.id = s.with_id LEFT JOIN main.person wp ON wp.id = w.person_id
        LEFT JOIN main.folder f ON f.id = s.folder_id LEFT JOIN main.tour t ON t.id = s.tour_id
        LEFT JOIN main.album al ON al.id = s.album_id LEFT JOIN main.image i ON i.id = s.image_id
        {"WHERE s.account_id = ?" if account_id is not None else ""}
        ORDER BY s.id DESC""", (account_id,) if account_id is not None else ()).fetchall()
    out = []
    for r in rows:
        kind = next(k for k in KINDS if r[f"{k}_id"] is not None)
        out.append({**dict(r), "kind": kind, "item_id": r[f"{kind}_id"]})
    return out


def with_me(con: sqlite3.Connection, account_id: int) -> list[dict]:
    """What others share with this account that still holds (access.active_shares), with who shared it, on a
    connection that sees everything. Newest first."""
    names = dict(con.execute("""SELECT a.id, p.name FROM main.account a JOIN main.person p ON p.id = a.person_id"""))
    out = []
    for r in reversed(access.active_shares(con, account_id)):
        kind = next(k for k in KINDS if r[f"{k}_id"] is not None)
        out.append({"id": r["id"], "kind": kind, "item_id": r[f"{kind}_id"], "by_id": r["account_id"],
                    "by_name": names.get(r["account_id"]) or ("an admin" if r["by_role"] == "admin" else "someone"),
                    "by_uploads": r["by_uploads"], "with_everyone": r["with_id"] is None,
                    "created_at": r["created_at"]})
    return out


def who(viewer: access.Viewer, person_id: int | None, name: str | None, email: str | None) -> str:
    """An account, as the viewer is shown it on what they share: an admin sees its name and email; a member its name,
    while the person is in their part of the family."""
    if viewer.admin:
        return f"{name} ({email})" if name else email or "?"
    return name if name and viewer.sees_person(person_id) else "someone outside your part of the family"


def recipients(con: sqlite3.Connection, viewer: access.Viewer) -> list[dict]:
    """The accounts the viewer may share with, besides everyone: for a member, those of the people in their part of
    the family; for an admin, every member. Never themselves, admins (who see everything already) or anyone switched
    off. On a connection that sees everyone."""
    rows = con.execute("""SELECT a.id, a.email, a.person_id, p.name FROM main.account a
                          LEFT JOIN main.person p ON p.id = a.person_id
                          WHERE a.disabled_at IS NULL AND a.role = 'member' AND a.id != ?
                          ORDER BY coalesce(p.name, a.email) COLLATE NOCASE""", (viewer.account_id or 0,))
    return [{"id": r["id"], "name": who(viewer, r["person_id"], r["name"], r["email"])} for r in rows
            if viewer.admin or (r["person_id"] is not None and viewer.sees_person(r["person_id"]))]


def remember(con: sqlite3.Connection, tour_id: int, image_ids) -> bool:
    """Keep the photos a shared tour shows its maker: what sharing it lets the others see. True when they changed. It
    is kept again the next time, should the catalog be busy just now."""
    now = set(image_ids)
    was = {r[0] for r in con.execute("SELECT image_id FROM main.tour_photo WHERE tour_id = ?", (tour_id,))}
    if now == was:
        return False
    try:
        con.execute("DELETE FROM main.tour_photo WHERE tour_id = ?", (tour_id,))
        con.executemany("INSERT INTO main.tour_photo(tour_id, image_id) VALUES (?, ?)", ((tour_id, i) for i in now))
        con.commit()
    except sqlite3.OperationalError:  # the worker is writing: the page is shown all the same
        con.rollback()
        return False
    return True


def remember_album(con: sqlite3.Connection, album_id: int, image_ids) -> bool:
    """Keep the photos of an album its maker may pass on: what sharing it lets the others see. True when they changed.
    As remember, it is kept again the next time should the catalog be busy just now."""
    now = set(image_ids)
    was = {r[0] for r in con.execute("SELECT image_id FROM main.album_passed WHERE album_id = ?", (album_id,))}
    if now == was:
        return False
    try:
        con.execute("DELETE FROM main.album_passed WHERE album_id = ?", (album_id,))
        con.executemany("INSERT INTO main.album_passed(album_id, image_id) VALUES (?, ?)",
                        ((album_id, i) for i in now))
        con.commit()
    except sqlite3.OperationalError:  # the worker is writing: the page is shown all the same
        con.rollback()
        return False
    return True


def drop_folder(con: sqlite3.Connection, path: str) -> int:
    """Stop sharing a folder and the folders in it (they were moved to Removed). Returns how many shares went."""
    return con.execute("""DELETE FROM main.share WHERE folder_id IN
                          (SELECT id FROM main.folder WHERE path = ? OR path LIKE ? ESCAPE '!')""",
                       (path, under(path))).rowcount
