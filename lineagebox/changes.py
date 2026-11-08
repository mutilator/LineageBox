"""What was changed, and putting it back (Cleanup → Recent changes, for admins).

Every change made through the site (a face named, a tag, a date, someone added to a photo, a family link, a person's
details, two people merged or someone removed, something shared or no longer shared) is kept with the rows it could
touch as they were before and after, whoever made it: a family member, an admin, or anyone at home without family
accounts. Undoing it puts the "before" rows back, but only while those rows are still as the change left them: a change
made over it since is never overwritten (the undo says so instead). What the worker does in the meantime (grouping and
recognising unnamed faces) doesn't count as a change over it. Rows a change added are found as the ids above the
largest one before it, so someone who already existed is never taken for someone new.

A family change that lets an account see someone it couldn't before (a false link can: marking a brother's wife as
your mother makes her parents your grandparents) is still made at once, by design, and marked on the list with
whom each account gained.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

from lineagebox import access, errors, family, search
from lineagebox.folders import FolderIndex, reflow

KEYS = {"face": ("id",), "image": ("id",), "image_tag": ("image_id", "tag_id"), "tag": ("id",),
        "image_person": ("image_id", "person_id"), "face_not_person": ("face_id", "person_id"),
        "parent": ("child_id", "parent_id"), "sibling": ("a", "b"), "partner": ("a", "b"),
        "relation": ("person_id", "of_id", "kind"), "person": ("id",), "event": ("id",), "person_name": ("id",),
        "person_group": ("person_id", "name"), "document": ("image_id",), "citation": ("id",), "share": ("id",),
        "comment": ("id",), "external_id": ("uid",), "access_rule": ("account_id", "person_id"), "account": ("id",),
        "tour": ("id",), "tour_type": ("id",), "settings": ("key",), "folder": ("id",),
        "source": ("id",), "source_citation": ("id",), "tree_media": ("id",),
        "album": ("id",), "album_photo": ("album_id", "image_id"), "album_passed": ("album_id", "image_id"),
        "camera_clock": ("id",)}
# what a change can alter of faces and photos (the rest is the worker's: embeddings, stage, sharpness...), and of the
# rows a merge points at the person kept
COLUMNS = {"face": "id, image_id, person_id, assigned_by, assign_score, cluster_id, named_by",
           "image": ("id, taken_at, taken_at_source, date_text, date_from, date_to, auto_taken_at, auto_taken_at_source, "
                     "camera_taken_at"),
           "account": "id, person_id", "tour": "id, spec", "tour_type": "id, spec",
           "folder": "id, kind, place, place_type, event, people_hint, year, month, day, source"}
UPDATE_ONLY = {"face", "image", "account", "tour", "tour_type", "folder"}  # never added or deleted by a change
# rows that others point to come back first and go last
ORDER = ["person", "tag", "partner", "event", "document", "citation", "source", "source_citation", "tree_media",
         "person_name", "person_group", "parent",
         "sibling", "relation", "external_id", "access_rule", "account", "settings", "tour", "tour_type", "folder",
         "album", "album_photo", "album_passed", "camera_clock",
         "image",
         "face", "face_not_person", "image_tag", "image_person", "share", "comment"]
# Recorded stories (stories.py; one taken away is only hidden, so undoing that shows it again) and transcripts people
# corrected (speech.correct): what Whisper heard is the worker's, the corrections and the text made with them are theirs
KEYS.update(story=("id",), transcript=("image_id",))
COLUMNS["story"] = ("id, image_id, person_id, event_id, file, mime, bytes, seconds, title, account_id, at, removed_at, "
                    "words")  # what Whisper heard is the worker's: hearing it doesn't keep a recording from Undo
COLUMNS["transcript"] = "image_id, text, edits, edited_at"
UPDATE_ONLY.add("transcript")
ORDER += ["story", "transcript"]
# Photos and folders put on the map by hand (places.py)
KEYS.update(image_place=("image_id",), folder_place=("folder_id",))
ORDER += ["image_place", "folder_place"]
# The AI's words that mean another tag now (tags renamed, merged or taken off every photo: tagging.rename): after the
# tags they point at
KEYS["tag_alias"] = ("name",)
ORDER.insert(ORDER.index("tag") + 1, "tag_alias")
FAMILY ={"parent", "sibling", "partner", "relation", "person"}
# A face's group and score are the worker's, and so is its recognising an unnamed face: neither keeps a change from
# being undone. A name given by a person, or a face set aside by one, does.
WORKERS = {"cluster_id", "assign_score"}
CHOSEN = {"user", "rejected", "not_a_face"}
OWN_UNDO_MINUTES = 30  # how long the Undo beside a change works for the member who made it


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


class Kept:
    """What tracking() yields: once the with block is done, id is the change kept (None when nothing changed), for an
    Undo button beside it."""
    id: int | None = None


@contextmanager
def tracking(con: sqlite3.Connection, viewer: access.Viewer, kind: str, summary: str, specs):
    """Keep a change for Recent changes, whoever made it. specs() gives the rows the change may touch, as (table,
    condition, arguments); it is asked before and after, so it may name ids the change made. The change itself runs
    inside the with block, on con (a connection that sees everything). Only a member's changes are checked for whom
    they let see more of the family: an admin's are theirs to make."""
    kept = Kept()
    newest_before = {t: newest(con, t) for t in ("person", "tag", "event", "person_name", "citation", "share",
                                                 "comment", "source", "source_citation", "story")}
    picked = specs(newest_before)
    before = capture(con, picked)
    people_before = _accounts_people(con) if viewer.limited and FAMILY & {t for t, _, _ in picked} else None
    yield kept
    con.commit()
    after = capture(con, specs(newest_before))
    for table, rows in before.items():  # still there, though specs() no longer picks it (a source a removed person
        # was cited by, cited by others too): kept as it is now, or the undo would take it for one the change deleted
        after.setdefault(table, {}).update(_rows(con, table, [k for k in rows if k not in after.get(table, {})]))
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
    kept.id = con.execute("""INSERT INTO main.change_log(account_id, at, kind, summary, undo, widened)
                             VALUES (?, ?, ?, ?, ?, ?)""",
                          (viewer.account_id, datetime.now().isoformat(timespec="seconds"), kind, summary[:300],
                           json.dumps({"before": before, "after": after}), json.dumps(widened) if widened else None)
                          ).lastrowid
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
        now, then = _rows(con, table, keys), after.get(table, {})
        if not all(_unchanged(table, _as_kept(now.get(k), then.get(k)), then.get(k)) for k in keys):
            raise Conflict(f"“{row['summary']}” was changed again since, so it was left as it is.")
    touched_images = set()
    try:
        for table in tables:  # back first: what others point to
            for key, values in before.get(table, {}).items():
                if key not in after.get(table, {}):
                    _insert(con, table, values)
                elif after[table][key] != values or table == "face":  # the worker may have regrouped it since
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

    def renamed(table: str) -> str:
        """The people or tags whose name the undo put back, brought back or took away, as a JSON list: only their
        photos are found by other words now (a life story or a portrait put back changes nothing search knows, and
        someone may be in thousands of photos)."""
        was, now = before.get(table, {}), after.get(table, {})
        return json.dumps(sorted(json.loads(k)[0] for k in set(was) | set(now)
                                 if (was.get(k) or {}).get("name") != (now.get(k) or {}).get("name")))

    if (people := renamed("person")) != "[]":  # the photos of whoever it was are found by their name again
        touched_images |= {r[0] for r in con.execute("""SELECT image_id FROM main.face WHERE person_id IN
                                                        (SELECT value FROM json_each(:p))
                                                        UNION SELECT image_id FROM main.image_person WHERE person_id IN
                                                        (SELECT value FROM json_each(:p))""", {"p": people})}
    if (tags := renamed("tag")) != "[]":  # a tag's name put back: the photos with it are found by it again
        touched_images |= {r[0] for r in con.execute("""SELECT image_id FROM main.image_tag WHERE tag_id IN
                                                        (SELECT value FROM json_each(?))""", (tags,))}
    for folder_id in sorted({v["id"] for v in before.get("folder", {}).values()}):  # what a folder means, put back
        reflow(con, folder_id)
    from lineagebox import camera_clock  # a camera's move undone: its photos read since the move go back too
    for camera in sorted({v["camera"] for side in (before, after) for v in side.get("camera_clock", {}).values()}):
        camera_clock.redate(con, camera)
    # the family's words on a copy are found on the photo of its group that search shows (search.index_group)
    words = {v.get("image_id") for t in ("comment", "story", "document") for side in (before, after)
             for v in side.get(t, {}).values()}
    folders = FolderIndex(con)
    for n, image_id in enumerate(sorted(i for i in touched_images if i), 1):
        (search.index_group if image_id in words else search.index_image)(con, image_id, folders)
        if n % search.COMMIT_EVERY == 0:  # a big merge's photos: the web app doesn't wait for all of them at once
            con.commit()
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


def recent(con: sqlite3.Connection, limit: int = 300, members_only: bool = False) -> list[dict]:
    """Changes, newest first, with who made them (role 'member' or 'admin'; None without family accounts) and whom
    each widened access to. members_only: family members' changes alone."""
    names = dict(con.execute("SELECT id, name FROM main.person"))
    out = []
    only = "WHERE a.role = 'member'" if members_only else ""
    # not c.undo: the rows kept to undo a big change (a merge, a camera's dates) are megabytes, and the list needs none
    for r in con.execute(f"""SELECT c.id, c.account_id, c.at, c.kind, c.summary, c.undone_at, c.widened, a.email,
                                    a.role, p.name AS who FROM main.change_log c
                             LEFT JOIN main.account a ON a.id = c.account_id LEFT JOIN main.person p ON p.id = a.person_id
                             {only} ORDER BY c.id DESC LIMIT ?""", (limit,)):
        widened = {int(k): [names.get(p, "?") for p in v] for k, v in json.loads(r["widened"] or "{}").items()}
        out.append({**{k: r[k] for k in ("id", "account_id", "at", "kind", "summary", "undone_at", "email", "who",
                                          "role")},
                    "widened": widened})
    return out


def undo_own(con: sqlite3.Connection, viewer: access.Viewer, change_id: int) -> str:
    """Undo a change from the Undo shown just after it was made. A member may put back only their own, and only for
    OWN_UNDO_MINUTES: after that it is for an admin, on Recent changes."""
    row = con.execute("SELECT account_id, at FROM main.change_log WHERE id = ?", (change_id,)).fetchone()
    if row is None:
        raise Conflict("No such change.")
    if viewer.limited:
        late = datetime.fromisoformat(row["at"]) < datetime.now() - timedelta(minutes=OWN_UNDO_MINUTES)
        if row["account_id"] != viewer.account_id or late:
            raise Conflict("Only an admin can undo that now, on Cleanup → Recent changes.")
    return undo(con, change_id)


def _unchanged(table: str, now: dict | None, then: dict | None) -> bool:
    """Whether a row is still as a change left it. A face is, when only the worker has been at it since: its group
    and score, or a recognised name on a face that no person had named or set aside then, nor since."""
    if now == then:
        return True
    if table != "face" or now is None or then is None:
        return False
    if now["assigned_by"] not in CHOSEN and then["assigned_by"] not in CHOSEN:
        return True
    return {k: v for k, v in now.items() if k not in WORKERS} == {k: v for k, v in then.items() if k not in WORKERS}


def _as_kept(now: dict | None, kept: dict | None) -> dict | None:
    """A row as it is now, in the columns the change kept of it: a column a later version added (a migration since
    the change was made) was none of the change's, and doesn't keep it from being undone."""
    if now is None or kept is None:
        return now
    return {k: v for k, v in now.items() if k in kept}


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
