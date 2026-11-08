"""Device links: a photo frame, a TV or a Home Assistant dashboard that shows one thing without anyone signing in on it.

An admin makes one on Settings → Photo frames: a name, the one view it shows (memories as one account sees them, a
tour or an album, VIEWS) and whether it works only on the home network. The link carries a random
token; the catalog keeps only its SHA-256, as for sessions (accounts.py), so a copy of the catalog opens no frame.
Revoking a link turns it off at once.

What a link may open is decided in the web app (web/frames.py): the frame page, its list of photos, and the pictures
and videos on that list. Every other page and API refuses it, as if it weren't there.
"""
import secrets
import sqlite3
from datetime import datetime

from lineagebox import errors
from lineagebox.accounts import _hash

# what a link can show; each has a picker for its photos in web/frames.PICKERS
VIEWS = {"memories": "Memories", "tour": "A tour", "album": "An album"}
SECONDS = (8, 15, 20, 30, 60)  # how long each photo stays, as offered
TOKEN_BYTES = 16  # 128 bits: as hard to guess as a session's, and short enough to type into a TV
SEEN_EVERY = 600  # seconds between writing a link's last use: the frame asks for a picture every few seconds


class Problem(errors.Problem):
    """Something that keeps a link from being made; the message is shown."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def make(con: sqlite3.Connection, name: str, view: str, *, account_id: int | None = None, tour_id: int | None = None,
         album_id: int | None = None, home_only: bool = True, seconds: int = 20,
         made_by: int | None = None) -> tuple[int, str]:
    """A new link: (its id, the token for its address, which is shown this once). Commits."""
    name = " ".join(name.split())[:80]
    if not name:
        raise Problem("Give the frame a name, such as Kitchen tablet, so you know which link is which.")
    if view not in VIEWS:
        raise Problem("Pick what the frame shows.")
    if view == "tour":
        if tour_id is None or not con.execute("SELECT 1 FROM main.tour WHERE id = ?", (tour_id,)).fetchone():
            raise Problem("Pick the tour it shows.")
        account_id = album_id = None  # a tour shows what its maker sees
    elif view == "album":
        if album_id is None or not con.execute("SELECT 1 FROM main.album WHERE id = ?", (album_id,)).fetchone():
            raise Problem("Pick the album it shows.")
        account_id = tour_id = None  # an album shows what its maker may pass on
    elif view == "memories":
        tour_id = album_id = None
        if account_id is not None and not con.execute("SELECT 1 FROM main.account WHERE id = ?",
                                                      (account_id,)).fetchone():
            raise Problem("No such account.")
    if seconds not in SECONDS:
        seconds = 20
    token = secrets.token_urlsafe(TOKEN_BYTES)
    link_id = con.execute("""INSERT INTO main.device_link(name, token_hash, view, account_id, tour_id, album_id,
                                                          home_only, seconds, made_by, made_at)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                          (name, _hash(token), view, account_id, tour_id, album_id, int(bool(home_only)), seconds,
                           made_by, _now())).lastrowid
    con.commit()
    return link_id, token


def revoke(con: sqlite3.Connection, link_id: int) -> bool:
    """Turn a link off for good. True if it was on. Commits."""
    n = con.execute("UPDATE main.device_link SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                    (_now(), link_id)).rowcount
    con.commit()
    return n == 1


def remove(con: sqlite3.Connection, link_id: int) -> None:
    """Take a link off the list. Commits."""
    con.execute("DELETE FROM main.device_link WHERE id = ?", (link_id,))
    con.commit()


def get(con: sqlite3.Connection, link_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM main.device_link WHERE id = ?", (link_id,)).fetchone()


def every(con: sqlite3.Connection) -> list[dict]:
    """Every link, the ones still on first, newest first, with what it shows in words."""
    rows = con.execute("""SELECT d.*, a.email AS account_email, p.name AS account_name, a.disabled_at AS account_off,
                                 t.name AS tour_name, al.name AS album_name, mp.name AS made_by_name,
                                 m.email AS made_by_email
                          FROM main.device_link d LEFT JOIN main.account a ON a.id = d.account_id
                          LEFT JOIN main.person p ON p.id = a.person_id LEFT JOIN main.tour t ON t.id = d.tour_id
                          LEFT JOIN main.album al ON al.id = d.album_id AND al.created_at <= d.made_at
                          LEFT JOIN main.account m ON m.id = d.made_by LEFT JOIN main.person mp ON mp.id = m.person_id
                          ORDER BY d.revoked_at IS NOT NULL, d.id DESC""").fetchall()
    return [{**dict(r), "shows": shows(r)} for r in rows]


def shows(row) -> str:
    """What a link shows, in words for the list on Settings."""
    if row["view"] == "tour":
        return f"the tour “{row['tour_name']}”" if row["tour_name"] else "a tour"
    if row["view"] == "album":
        return f"the album “{row['album_name']}”" if row["album_name"] else "an album that was deleted"
    if row["view"] == "memories":
        if row["account_id"] is None:
            return "memories from the whole library"
        return f"memories as {row['account_name'] or row['account_email']} sees them"
    return VIEWS.get(row["view"], row["view"]).lower()


def by_token(con: sqlite3.Connection, token: str | None) -> dict | None:
    """The link a token is, while it is on: not revoked, and the account it shows memories as not switched off."""
    if not token or len(token) > 100:
        return None
    row = con.execute("""SELECT d.* FROM main.device_link d LEFT JOIN main.account a ON a.id = d.account_id
                         WHERE d.token_hash = ? AND d.revoked_at IS NULL
                           AND (d.account_id IS NULL OR a.disabled_at IS NULL)""", (_hash(token),)).fetchone()
    return dict(row) if row else None


def seen(con: sqlite3.Connection, link_id: int, ip: str) -> None:
    """When and from where a link was last used, for the list on Settings. Commits; busy is no matter."""
    try:
        con.execute("UPDATE main.device_link SET last_seen_at = ?, last_seen_ip = ? WHERE id = ?",
                    (_now(), ip[:64], link_id))
        con.commit()
    except sqlite3.OperationalError:
        con.rollback()
