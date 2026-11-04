"""What family members changed, and putting it back (Cleanup → Recent changes, for admins).

Every change a member makes (a face named, a tag, a date, someone added to a photo, a family link, a person's details)
is kept with the rows it could touch as they were before and after. Undoing it puts the "before" rows back, but only
while those rows are still as the change left them: a change made over it since is never overwritten (the undo says
so instead). Rows a change added are found as the ids above the largest one before it, so someone who already existed
is never taken for someone new.

A family change that lets an account see someone it couldn't before (a false link can: marking a brother's wife as
your mother makes her parents your grandparents) is still made at once, by design, and marked on the list with
whom each account gained.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from lineagebox import access, errors, family, search
from lineagebox.folders import FolderIndex

KEYS = {"face": ("id",), "image": ("id",), "image_tag": ("image_id", "tag_id"), "tag": ("id",),
        "image_person": ("image_id", "person_id"), "face_not_person": ("face_id", "person_id"),
        "parent": ("child_id", "parent_id"), "sibling": ("a", "b"), "partner": ("a", "b"),
        "relation": ("person_id", "of_id", "kind"), "person": ("id",), "event": ("id",), "person_name": ("id",),
        "person_group": ("person_id", "name"), "document": ("image_id",), "citation": ("id",)}
# what a change can alter of faces and photos (the rest is the worker's: embeddings, stage, sharpness...)
COLUMNS = {"face": "id, image_id, person_id, assigned_by, assign_score, cluster_id, named_by",
           "image": "id, taken_at, taken_at_source, date_text, date_from, date_to, auto_taken_at, auto_taken_at_source"}
UPDATE_ONLY = {"face", "image"}  # never added or deleted by a change
# rows that others point to come back first and go last
ORDER = ["person", "tag", "partner", "event", "document", "citation", "person_name", "person_group", "parent",
         "sibling", "relation", "image", "face", "face_not_person", "image_tag", "image_person"]
FAMILY = {"parent", "sibling", "partner", "relation", "person"}


def newest(con: sqlite3.Connection, table: str) -> int:
    """The largest id in a table: rows a change adds have larger ones."""
    return con.execute(f"SELECT coalesce(max(rowid), 0) FROM main.{table}").fetchone()[0]


def capture(con: sqlite3.Connection, specs: list[tuple[str, str, tuple]]) -> dict[str, dict]:
    """The rows each (table, condition, arguments) picks: {table: {key: row}}, keys as JSON text."""
    out: dict[str, dict] = {}
    for table, where, args in specs:
        rows = con.execute(f"SELECT {COLUMNS.get(table, '*')} FROM main.{table} WHERE {where}", args).fetchall()
        for r in rows:
            out.setdefault(table, {})[json.dumps([r[k] for k in KEYS[table]])] = dict(r)
        out.setdefault(table, {})
    return out


def _accounts_people(con: sqlite3.Connection) -> dict[int, set[int]]:
    """Each member account's people (access.people_for), on a connection that sees everyone."""
    g = family.load(con)
    out = {}
    for a in con.execute("SELECT * FROM main.account WHERE role = 'member' AND disabled_at IS NULL").fetchall():
        rules = [tuple(r) for r in con.execute("SELECT person_id, show, branch FROM main.access_rule WHERE account_id = ?",
                                               (a["id"],))]
        added = {r[0] for r in con.execute("""SELECT id FROM main.person WHERE added_by = :a
                                              UNION SELECT person_id FROM main.person_group WHERE added_by = :a""",
                                           {"a": a["id"]})}
        out[a["id"]] = access.people_for(g, a["person_id"], rules, added)
    return out


@contextmanager
def tracking(con: sqlite3.Connection, viewer: access.Viewer, kind: str, summary: str, specs):
    """Keep a member's change for Recent changes (admins' changes are not kept). specs() gives the rows the change
    may touch, as (table, condition, arguments); it is asked before and after, so it may name ids the change made.
    The change itself runs inside the with block, on con (a connection that sees everything)."""
    if not viewer.limited:
        yield
        con.commit()
        return
    newest_before = {t: newest(con, t) for t in ("person", "tag", "event", "person_name", "citation")}
    picked = specs(newest_before)
    before = capture(con, picked)
    people_before = _accounts_people(con) if FAMILY & {t for t, _, _ in picked} else None
    yield
    con.commit()
    after = capture(con, specs(newest_before))
    for table in set(before) | set(after):  # only what the change altered: the rest needn't stay as it was to undo it
        was, now = before.setdefault(table, {}), after.setdefault(table, {})
        for key in [k for k in was if was[k] == now.get(k)]:
            del was[key], now[key]
    if not any(before.values()) and not any(after.values()):
        return
    widened = {}
    if people_before is not None:
        made_by_them = {int(json.loads(k)[0]) for k in after.get("person", {}) if k not in before.get("person", {})}
        for account_id, now in _accounts_people(con).items():
            gained = now - people_before.get(account_id, set()) - (made_by_them if account_id == viewer.account_id
                                                                   else set())
            if gained:
                widened[str(account_id)] = sorted(gained)
    con.execute("""INSERT INTO main.change_log(account_id, at, kind, summary, undo, widened)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (viewer.account_id, datetime.now().isoformat(timespec="seconds"), kind, summary[:300],
                 json.dumps({"before": before, "after": after}), json.dumps(widened) if widened else None))
    con.commit()


class Conflict(errors.Problem):
    """The rows a change touched were changed again since: undoing it would overwrite that."""
    status = 409


def undo(con: sqlite3.Connection, change_id: int) -> str:
    """Put back what one change altered. Returns its summary; raises Conflict when it was changed again since."""
    row = con.execute("SELECT * FROM main.change_log WHERE id = ?", (change_id,)).fetchone()
    if row is None or row["undo"] is None:
        raise Conflict("No such change.")
    if row["undone_at"]:
        raise Conflict("Already undone.")
    saved = json.loads(row["undo"])
    before, after = saved["before"], saved["after"]
    tables = [t for t in ORDER if t in before or t in after]
    for table in tables:
        keys = set(before.get(table, {})) | set(after.get(table, {}))
        now = _rows(con, table, keys)
        if now != after.get(table, {}):
            raise Conflict(f"“{row['summary']}” was changed again since, so it was left as it is.")
    touched_images = set()
    try:
        for table in tables:  # back first: what others point to
            for key, values in before.get(table, {}).items():
                if key not in after.get(table, {}):
                    _insert(con, table, values)
                elif after[table][key] != values:
                    _update(con, table, values)
                touched_images.add(values.get("image_id") if table != "image" else values["id"])
        for table in reversed(tables):  # gone last
            for key, values in after.get(table, {}).items():
                if key not in before.get(table, {}) and table not in UPDATE_ONLY:
                    con.execute(f"DELETE FROM main.{table} WHERE {_where(table)}", json.loads(key))
                touched_images.add(values.get("image_id") if table != "image" else values["id"])
    except sqlite3.IntegrityError:  # someone it added is used by something the change didn't record
        con.rollback()
        raise Conflict(f"“{row['summary']}” can't be undone: what it added is used elsewhere since, so it was left "
                       f"as it is.") from None
    con.execute("UPDATE main.change_log SET undone_at = ? WHERE id = ?",
                (datetime.now().isoformat(timespec="seconds"), change_id))
    con.commit()
    people = {v["id"] for v in before.get("person", {}).values()} | {v["id"] for v in after.get("person", {}).values()}
    if people:  # a name put back: the photos of whoever it was are found by it again
        touched_images |= {r[0] for r in con.execute("""SELECT image_id FROM main.face WHERE person_id IN
                                                        (SELECT value FROM json_each(?))""", (json.dumps(sorted(people)),))}
    folders = FolderIndex(con)
    for image_id in sorted(i for i in touched_images if i):
        search.index_image(con, image_id, folders)
    con.commit()
    return row["summary"]


def undo_since(con: sqlite3.Connection, account_id: int, since_id: int) -> tuple[int, list[str]]:
    """Undo a member's changes from one on, newest first. Returns (how many were undone, why the others weren't)."""
    done, skipped = 0, []
    for (change_id,) in con.execute("""SELECT id FROM main.change_log WHERE account_id = ? AND id >= ? AND undone_at IS NULL
                                       ORDER BY id DESC""", (account_id, since_id)).fetchall():
        try:
            undo(con, change_id)
            done += 1
        except Conflict as e:
            skipped.append(str(e))
    return done, skipped


def recent(con: sqlite3.Connection, limit: int = 300) -> list[dict]:
    """Members' changes, newest first, with who made them and whom each widened access to."""
    names = dict(con.execute("SELECT id, name FROM main.person"))
    out = []
    for r in con.execute("""SELECT c.*, a.email, p.name AS who FROM main.change_log c
                            LEFT JOIN main.account a ON a.id = c.account_id LEFT JOIN main.person p ON p.id = a.person_id
                            ORDER BY c.id DESC LIMIT ?""", (limit,)):
        widened = {int(k): [names.get(p, "?") for p in v] for k, v in json.loads(r["widened"] or "{}").items()}
        out.append({**{k: r[k] for k in ("id", "account_id", "at", "kind", "summary", "undone_at", "email", "who")},
                    "widened": widened})
    return out


def _where(table: str) -> str:
    return " AND ".join(f"{k} = ?" for k in KEYS[table])


def _rows(con: sqlite3.Connection, table: str, keys) -> dict:
    out = {}
    for key in keys:
        r = con.execute(f"SELECT {COLUMNS.get(table, '*')} FROM main.{table} WHERE {_where(table)}",
                        json.loads(key)).fetchone()
        if r is not None:
            out[key] = dict(r)
    return out


def _insert(con: sqlite3.Connection, table: str, values: dict) -> None:
    con.execute(f"INSERT INTO main.{table}({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
                tuple(values.values()))


def _update(con: sqlite3.Connection, table: str, values: dict) -> None:
    keys = KEYS[table]
    rest = [k for k in values if k not in keys]
    con.execute(f"UPDATE main.{table} SET {', '.join(f'{k} = ?' for k in rest)} WHERE {_where(table)}",
                (*[values[k] for k in rest], *[values[k] for k in keys]))
