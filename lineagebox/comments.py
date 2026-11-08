"""The family's own words on a photo: its caption, which anyone who can see the photo may write or change, and the
comments under it ("I remember this day"). People's words rank above the AI's: the caption shows above the AI's
description, is searched first (search.py), and is what a tour's slide says.

A comment may be changed or taken away by whoever wrote it, or by an admin. Members' writes are kept for undo by the
routes (changes.tracking), like their other changes.
"""
import json
import sqlite3
from datetime import datetime

from lineagebox import errors, outbox

MOST = 2000  # characters in one caption or comment: a memory, not a document


class Problem(errors.Problem):
    """A caption or comment that can't be saved; the message is shown to whoever wrote it."""


def _text(text: str) -> str:
    text = "\n".join(" ".join(line.split()) for line in (text or "").strip().splitlines()).strip()
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    if len(text) > MOST:
        raise Problem(f"That is {len(text):,} characters: keep it under {MOST:,}.")
    return text


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def caption(con: sqlite3.Connection, image_id: int) -> str | None:
    row = con.execute("SELECT text FROM comment WHERE image_id = ? AND kind = 'caption'", (image_id,)).fetchone()
    return row[0] if row else None


def captions(con: sqlite3.Connection, image_ids) -> dict[int, str]:
    """image id -> its caption, for those that have one (a tour's slides)."""
    return dict(con.execute("""SELECT image_id, text FROM comment WHERE kind = 'caption'
                               AND image_id IN (SELECT value FROM json_each(?))""", (json.dumps(list(image_ids)),)))


def for_image(con: sqlite3.Connection, image_id: int) -> dict:
    """The caption and the comments, oldest first, each with who wrote it (their name in the family, else the start
    of their email address; None at home without family accounts). con: a connection that sees every account."""
    rows = con.execute("""SELECT c.*, p.name AS person_name, a.person_id, a.email FROM main.comment c
                          LEFT JOIN main.account a ON a.id = c.account_id LEFT JOIN main.person p ON p.id = a.person_id
                          WHERE c.image_id = ? ORDER BY c.at, c.id""", (image_id,)).fetchall()
    out = {"caption": None, "comments": []}
    for r in rows:
        item = {"id": r["id"], "text": r["text"], "account_id": r["account_id"], "person_id": r["person_id"],
                "at": r["at"], "edited_at": r["edited_at"],
                "by": r["person_name"] or (r["email"].split("@")[0] if r["email"] else None)}
        if r["kind"] == "caption":
            out["caption"] = item
        else:
            out["comments"].append(item)
    return out


def set_caption(con: sqlite3.Connection, image_id: int, text: str, account_id: int | None) -> str | None:
    """Write, change or (with no text) take off a photo's caption. Returns the caption now. Doesn't commit."""
    text = _text(text)
    if not text:
        con.execute("DELETE FROM main.comment WHERE image_id = ? AND kind = 'caption'", (image_id,))
        return None
    if con.execute("SELECT 1 FROM main.comment WHERE image_id = ? AND kind = 'caption'", (image_id,)).fetchone():
        con.execute("""UPDATE main.comment SET text = ?, account_id = ?, edited_at = ?
                       WHERE image_id = ? AND kind = 'caption' AND text != ?""",
                    (text, account_id, _now(), image_id, text))
    else:
        con.execute("""INSERT INTO main.comment(image_id, kind, text, account_id, at)
                       VALUES (?, 'caption', ?, ?, ?)""", (image_id, text, account_id, _now()))
    return text


def add_comment(con: sqlite3.Connection, image_id: int, text: str, account_id: int | None) -> int:
    """A comment under a photo. Returns its id. Doesn't commit."""
    text = _text(text)
    if not text:
        raise Problem("Type what you'd like to say first.")
    comment_id = con.execute("""INSERT INTO main.comment(image_id, kind, text, account_id, at)
                                VALUES (?, 'comment', ?, ?, ?)""", (image_id, text, account_id, _now())).lastrowid
    outbox.commented(con, comment_id)  # news for whoever uploaded it, is in it or wrote on it before (New for you)
    return comment_id


def get(con: sqlite3.Connection, comment_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM comment WHERE id = ? AND kind = 'comment'", (comment_id,)).fetchone()


def may_change(row, account_id: int | None, admin: bool) -> bool:
    """A comment is changed or taken away by whoever wrote it, or by an admin."""
    return admin or (account_id is not None and row["account_id"] == account_id)


def edit_comment(con: sqlite3.Connection, comment_id: int, text: str) -> None:
    text = _text(text)
    if not text:
        raise Problem("A comment needs some words: Delete takes it away.")
    con.execute("UPDATE main.comment SET text = ?, edited_at = ? WHERE id = ? AND text != ?",
                (text, _now(), comment_id, text))


def delete_comment(con: sqlite3.Connection, comment_id: int) -> None:
    con.execute("DELETE FROM main.comment WHERE id = ? AND kind = 'comment'", (comment_id,))
