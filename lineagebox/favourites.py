"""Favourites: a heart on a photo, each account its own (♡ on the photo's page).

A heart says "this one" among many: tours pick a photo with one before the others like it (tours.photos), and Home's
memories show one as their cover. Each account keeps its own hearts; at home without family accounts there is one set,
kept with no account (NULL). Those given at home, before accounts were turned on, count as every admin's.

Whose hearts count where follows what each connection holds: on a member's, only their own (access.SCOPED), so their
tours and memories prefer what they love; on an admin's, everyone's, so the tours the admins make for the family
prefer what the family loves, and a photo several people love comes first.
"""
import json
import sqlite3
from datetime import datetime

WEIGHT = 100  # what a heart adds to a photo's score in a tour: more than faces and sharpness ever give (tours.photos)


def _whose(viewer) -> tuple[str, tuple]:
    """SQL for the viewer's own hearts: their account's, and for an admin also those given at home."""
    if viewer.account_id is None:
        return "account_id IS NULL", ()
    if viewer.admin:
        return "(account_id = ? OR account_id IS NULL)", (viewer.account_id,)
    return "account_id = ?", (viewer.account_id,)


def has(con: sqlite3.Connection, viewer, image_id: int) -> bool:
    """Whether the viewer gave this photo a heart."""
    who, args = _whose(viewer)
    return con.execute(f"SELECT 1 FROM main.favourite WHERE image_id = ? AND {who}", (image_id, *args)).fetchone() \
        is not None


def put(con: sqlite3.Connection, viewer, image_id: int, on: bool) -> None:
    """Give the photo the viewer's heart, or take it off. On a connection that sees everything; the caller checked
    that the viewer sees the photo."""
    if on:
        con.execute("INSERT OR IGNORE INTO main.favourite(account_id, image_id, at) VALUES (?, ?, ?)",
                    (viewer.account_id, image_id, datetime.now().isoformat(timespec="seconds")))
    else:
        who, args = _whose(viewer)
        con.execute(f"DELETE FROM main.favourite WHERE image_id = ? AND {who}", (image_id, *args))


def mine(con: sqlite3.Connection, viewer) -> list[int]:
    """The photos the viewer gave a heart, the latest first."""
    who, args = _whose(viewer)
    return [r[0] for r in con.execute(f"""SELECT image_id FROM main.favourite WHERE {who}
                                          GROUP BY image_id ORDER BY max(at) DESC, image_id DESC""", args)]


def counts(con: sqlite3.Connection, image_ids) -> dict[int, int]:
    """image id -> how many hearts it has, of those the connection holds (a member's own; everyone's for an admin)."""
    ids = sorted(set(image_ids))
    if not ids:
        return {}
    return dict(con.execute("""SELECT image_id, count(*) FROM favourite
                               WHERE image_id IN (SELECT value FROM json_each(?)) GROUP BY image_id""",
                            (json.dumps(ids),)))
