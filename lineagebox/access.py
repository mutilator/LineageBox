"""What a family member who signs in may see: their part of the family tree, and the photos those people are in.

For a member who is person P (docs/access-control.md, "Who a member sees"):
- people: P, P's ancestors, every blood relative (everyone descended from those ancestors), the partners of all of
  them (former partners only when they share a child with someone in the tree) and those partners' children; the same
  again from P's current partner, so a couple sees the same family; people the member added themselves, and those in
  groups they made; then the admin's exceptions on the account (someone, alone or with their family, also shown or
  not shown). Friends, pets and other relationships are outside it.
- photos: those someone they can see is in (a face named by hand or recognised, or added to the photo by hand), and
  their own uploads. A photo an admin hid, or one in a hidden folder, is left out unless they uploaded it.
Admins see everything; so does everyone while accounts are off.

The web app keeps a member to that in two ways: every route says who may use it, and a member's connection only
holds what they can see. scope() adds temporary views named like the tables they hide; SQLite looks in the temp schema
first, so every query of the app reads only the member's rows without being written for it, and a write to one of the
views fails instead of reaching hidden rows (members' changes are checked against the Viewer and written on a
connection of their own, web/app.py).
"""
import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from lineagebox import family
from lineagebox.catalog import under

REFRESH = 30  # seconds a viewer is kept before it is worked out again (the worker names faces as it goes)

# table -> the rows of it a member sees, as the condition of its view. {account} is the member's account id.
SCOPED = {
    "image": "id IN temp.seen_image",
    "file": "image_id IN temp.seen_image",
    "face": "image_id IN temp.seen_image",
    "analysis": "image_id IN temp.seen_image",
    "image_tag": "image_id IN temp.seen_image",
    "image_person": "image_id IN temp.seen_image",
    "video_frame": "image_id IN temp.seen_image",
    "transcript": "image_id IN temp.seen_image",
    "document": "image_id IN temp.seen_image",
    "folder": "id IN temp.seen_folder",
    "person": "id IN temp.seen_person",
    "person_name": "person_id IN temp.seen_person",
    "person_group": "person_id IN temp.seen_person",
    "external_id": "person_id IN temp.seen_person",
    "face_not_person": "person_id IN temp.seen_person",
    "event": "person_id IN temp.seen_person OR (couple_a IN temp.seen_person AND couple_b IN temp.seen_person)",
    "citation": "image_id IN temp.seen_image AND (person_id IN temp.seen_person OR event_id IN (SELECT id FROM temp.event))",
    "parent": "child_id IN temp.seen_person AND parent_id IN temp.seen_person",
    "sibling": "a IN temp.seen_person AND b IN temp.seen_person",
    "partner": "a IN temp.seen_person AND b IN temp.seen_person",
    "relation": "person_id IN temp.seen_person AND of_id IN temp.seen_person",
    "tour": "account_id = {account} OR (account_id IS NULL AND shared)",
    "upload": "account_id = {account}",
    "account": "id = {account}",
    "session": "account_id = {account}",
    "access_rule": "account_id = {account}",
    "change_log": "account_id = {account}",
    "face_question": "face_id IN (SELECT id FROM temp.face)",
    "face_question_skip": "account_id = {account}",
    "gedcom_import": "0",
    "activity": "0",
    "kept_copies": "0",
}
WITH_ROWID = {"image_tag"}  # read by rowid (the order tags were given in): a view has none unless it says so


@dataclass(frozen=True)
class Viewer:
    """Who is looking, and what they may see. account None: accounts are off, and everyone sees everything."""
    account: dict | None = None
    admin: bool = True
    people: frozenset[int] = frozenset()
    images: frozenset[int] = frozenset()
    shas: frozenset[str] = frozenset()
    folders: frozenset[int] = frozenset()
    seeing_as: bool = False  # an admin looking through this member's eyes: changes are refused
    lists: dict = field(default_factory=dict, compare=False, repr=False)  # the sets as JSON lists, for scope()

    @property
    def limited(self) -> bool:
        return not self.admin

    @property
    def account_id(self) -> int | None:
        return self.account["id"] if self.account else None

    @property
    def me(self) -> int | None:
        return self.account["person_id"] if self.account else None

    def sees_person(self, pid: int | None) -> bool:
        return self.admin or pid in self.people

    def sees_image(self, image_id: int | None) -> bool:
        return self.admin or image_id in self.images


EVERYONE = Viewer()
NOBODY = Viewer(admin=False, lists={"person": "[]", "image": "[]", "folder": "[]"})  # accounts on, not signed in


def admin(account: dict) -> Viewer:
    return Viewer(account=account, admin=True)


# ---- the rules -----------------------------------------------------------------------------------------------------

def family_side(g: family.Graph, pid: int) -> set[int]:
    """pid, their ancestors and everyone descended from those (blood relatives), the partners of all of them (former
    ones only when they share a child with someone here) and those partners' children. Stand-in parents (negative ids,
    for siblings with no parents entered) are walked through, and left in: people_for drops them."""
    blood, todo = set(), list(g.ancestors.get(pid, {pid: 0}))
    while todo:
        p = todo.pop()
        if p not in blood:
            blood.add(p)
            todo.extend(g.effective_children.get(p, ()))
    out = set(blood)
    for b in blood:
        for partner, (current, _married) in g.partners.get(b, {}).items():
            children = g.effective_children.get(partner, set())
            if current or children & blood:
                out.add(partner)
                out |= children
    return out


def people_for(g: family.Graph, pid: int | None, rules=(), added=()) -> set[int]:
    """The people a member who is pid sees (see the module's docstring). rules: (person, show, branch) exceptions;
    added: people they added themselves."""
    seen = set()
    if pid is not None and pid in g.people:
        seen = family_side(g, pid)
        for partner, (current, _married) in g.partners.get(pid, {}).items():
            if current:
                seen |= family_side(g, partner)
    seen |= set(added)
    for person, show, branch in sorted(rules, key=lambda r: -r[1]):  # shown first, so "not shown" wins
        if person in g.people:
            them = family_side(g, person) if branch else {person}
            seen = seen | them if show else seen - them
    return {p for p in seen if p in g.people}


def images_for(con: sqlite3.Connection, people: set[int], account_id: int | None, upload_folder: str | None) -> set[int]:
    """The photos a member sees: someone they see is in it, by a face named by hand or recognised or added by hand,
    unless hidden by an admin; and their own uploads, hidden or not."""
    hidden_folders = [r[0] for r in con.execute("SELECT path FROM folder WHERE hidden")]
    hidden_files = " OR ".join("v.path = ? OR v.path LIKE ? ESCAPE '!'" for _ in hidden_folders)
    hidden_args = [a for p in hidden_folders for a in (p, under(p))]
    of_people = con.execute(f"""
        WITH seen(id) AS (SELECT value FROM json_each(?))
        SELECT image_id FROM face WHERE person_id IN seen AND assigned_by IN ('user', 'auto')
        UNION SELECT image_id FROM image_person WHERE person_id IN seen
        EXCEPT SELECT id FROM image WHERE hidden
        {f"EXCEPT SELECT v.image_id FROM file v WHERE v.image_id IS NOT NULL AND ({hidden_files})"
         if hidden_folders else ""}""", (json.dumps(sorted(people)), *hidden_args))
    out = {r[0] for r in of_people}
    if account_id is not None:
        out |= {r[0] for r in con.execute("SELECT image_id FROM upload WHERE account_id = ?", (account_id,))}
    if upload_folder:
        out |= {r[0] for r in con.execute("""SELECT image_id FROM file WHERE image_id IS NOT NULL
                                             AND path LIKE ? ESCAPE '!'""", (under(upload_folder),))}
    return out


def folders_for(con: sqlite3.Connection, images: set[int], upload_folder: str | None) -> set[int]:
    """The folders a member's photos are in, the folders above those (where places in folder names come from), and
    their own uploads folder and everything in it. Members browse only their uploads folder (web/app.py)."""
    parents = dict(con.execute("SELECT id, parent_id FROM folder"))
    out = set()
    for (fid,) in con.execute("""SELECT DISTINCT folder_id FROM file WHERE image_id IN (SELECT value FROM json_each(?))""",
                              (json.dumps(sorted(images)),)):
        while fid is not None and fid not in out:
            out.add(fid)
            fid = parents.get(fid)
    if upload_folder:
        for (fid,) in con.execute("SELECT id FROM folder WHERE path = ? OR path LIKE ? ESCAPE '!'",
                                  (upload_folder, under(upload_folder))):
            while fid is not None and fid not in out:
                out.add(fid)
                fid = parents.get(fid)
    return out


def compute(con: sqlite3.Connection, account: dict) -> Viewer:
    """Work out what an account sees, on a connection that sees everything."""
    if account["role"] == "admin":
        return admin(account)
    g = family.load(con)
    rules = con.execute("SELECT person_id, show, branch FROM access_rule WHERE account_id = ?",
                        (account["id"],)).fetchall()
    added = {r[0] for r in con.execute("""SELECT id FROM person WHERE added_by = :a
                                          UNION SELECT person_id FROM person_group WHERE added_by = :a""",
                                       {"a": account["id"]})}
    people = people_for(g, account["person_id"], [tuple(r) for r in rules], added)
    images = images_for(con, people, account["id"], account["upload_folder"])
    shas = {r[0] for r in con.execute("SELECT sha256 FROM image WHERE id IN (SELECT value FROM json_each(?))",
                                      (json.dumps(sorted(images)),))}
    folders = folders_for(con, images, account["upload_folder"])
    return Viewer(account=account, admin=False, people=frozenset(people), images=frozenset(images),
                  shas=frozenset(shas), folders=frozenset(folders),
                  lists={"person": json.dumps(sorted(people)), "image": json.dumps(sorted(images)),
                         "folder": json.dumps(sorted(folders))})


class Viewers:
    """Each account's Viewer, kept REFRESH seconds, and worked out again sooner after any change made through the web
    app (changed())."""

    def __init__(self):
        self.kept: dict[tuple, tuple[Viewer, float, int]] = {}
        self.generation = 0
        self.lock = threading.Lock()

    def changed(self) -> None:
        with self.lock:
            self.generation += 1

    def get(self, con: sqlite3.Connection, account: dict) -> Viewer:
        key = (account["id"], account["role"], account["person_id"], account["upload_folder"])
        with self.lock:
            found = self.kept.get(key)
            generation = self.generation
        if found and time.monotonic() - found[1] < REFRESH and found[2] == generation:
            return found[0]
        viewer = compute(con, account)
        with self.lock:
            self.kept = {k: v for k, v in self.kept.items() if k[0] != account["id"]}
            self.kept[key] = (viewer, time.monotonic(), generation)
        return viewer


# ---- a member's connection -----------------------------------------------------------------------------------------

def scope(con: sqlite3.Connection, viewer: Viewer) -> sqlite3.Connection:
    """Make a connection the viewer's: "me" is them, and for a member only what they may see is there to read."""
    con.me = viewer.me
    if viewer.admin:
        return con
    for name in ("person", "image", "folder"):
        con.execute(f"CREATE TEMP TABLE seen_{name}(id INTEGER PRIMARY KEY)")
        con.execute(f"INSERT INTO temp.seen_{name} SELECT value FROM json_each(?)", (viewer.lists[name],))
    for table, where in SCOPED.items():
        columns = "rowid AS rowid, *" if table in WITH_ROWID else "*"
        con.execute(f"CREATE TEMP VIEW {table} AS SELECT {columns} FROM main.{table} WHERE {where}".replace(
            "{account}", str(int(viewer.account_id or 0))))
    return con


def names_of(con: sqlite3.Connection, person_ids) -> dict[int, str]:
    """The names of people, on a connection that sees everyone: for the names of people outside a member's part of
    the family who are in a photo they can see (shown without a link)."""
    ids = sorted({p for p in person_ids if p is not None})
    return dict(con.execute("SELECT id, name FROM person WHERE id IN (SELECT value FROM json_each(?))",
                            (json.dumps(ids),))) if ids else {}
