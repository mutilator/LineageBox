"""What a family member who signs in may see: their part of the family tree, and the photos those people are in.

For a member who is person P (docs/access-control.md, "Who a member sees"):
- people: P, P's ancestors, every blood relative (everyone descended from those ancestors), the partners of all of
  them (former partners only when they share a child with someone in the tree) and those partners' children; the same
  again from P's current partner, so a couple sees the same family; people the member added themselves, and those in
  groups they made; then the admin's exceptions on the account (someone, alone or with their family, also shown or
  not shown). Friends, pets and other relationships are outside it.
- photos: those someone they can see is in (a face named by hand or recognised, or added to the photo by hand), and
  their own uploads. A photo an admin hid, or one in a hidden folder, is left out unless they uploaded it.
- and what another account shared with them, or with everyone (sharing.py): a folder, a photo, a tour with the photos
  it shows its maker, or an album with the photos its maker may pass on (albums.py). Only shares that still hold count:
  from an account not switched off, of what is still theirs to share (a member's own uploads, tours and albums), and
  never a photo an admin hid.
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
    "comment": "image_id IN temp.seen_image",
    "video_chapters": "image_id IN temp.seen_image",
    "image_place": "image_id IN temp.seen_image",  # placed on the map by hand (places.py)
    "folder_place": "folder_id IN temp.seen_folder",
    "folder": "id IN temp.seen_folder",
    "person": "id IN temp.seen_person",
    "person_name": "person_id IN temp.seen_person",
    "person_group": "person_id IN temp.seen_person",
    "external_id": "person_id IN temp.seen_person",
    "face_not_person": "person_id IN temp.seen_person",
    "event": "person_id IN temp.seen_person OR (couple_a IN temp.seen_person AND couple_b IN temp.seen_person)",
    "citation": "image_id IN temp.seen_image AND (person_id IN temp.seen_person OR event_id IN (SELECT id FROM temp.event))",
    # recorded stories (stories.py): a photo's with the photo; a person's with them, and an event's with the event
    "story": "image_id IN temp.seen_image OR (person_id IN temp.seen_person AND (event_id IS NULL OR event_id IN "
             "(SELECT id FROM temp.event)))",
    # written sources: those cited for people and events they see, and those they added themselves
    "source_citation": "person_id IN temp.seen_person OR event_id IN (SELECT id FROM temp.event)",
    "source": "id IN (SELECT source_id FROM temp.source_citation) OR added_by = {account}",
    "tree_media": "person_id IN temp.seen_person OR source_id IN (SELECT id FROM temp.source)",
    "parent": "child_id IN temp.seen_person AND parent_id IN temp.seen_person",
    "sibling": "a IN temp.seen_person AND b IN temp.seen_person",
    "partner": "a IN temp.seen_person AND b IN temp.seen_person",
    "relation": "person_id IN temp.seen_person AND of_id IN temp.seen_person",
    "tour": "account_id = {account} OR id IN temp.seen_tour",
    "tour_photo": "0",
    # albums (albums.py): their own and those shared with them; of an album's photos, those they see
    "album": "account_id = {account} OR id IN temp.seen_album",
    "album_photo": "album_id IN (SELECT id FROM temp.album) AND image_id IN temp.seen_image",
    "album_passed": "0",
    "favourite": "account_id = {account}",
    "slideshow_video": "account_id = {account}",
    "share": "account_id = {account}",
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
    # New for you (outbox.py): events for them or everyone, of photos they see, not their own doing; each is checked
    # again as it is shown. Alerts are admins'; device links too, and a connection of no account (0) has none.
    "outbox": "{account} != 0 AND NOT admins AND (to_account IS NULL OR to_account = {account})"
              " AND coalesce(by_account, 0) != {account} AND (image_id IS NULL OR image_id IN temp.seen_image)",
    "outbox_seen": "account_id = {account}",
    "device_link": "0",
    # Search by meaning, describing again and tags across the library (embeddings.py, describe.py, tagging.py): the
    # meaning of the photos they see, their own asks, and none of the tags' renames (an admin's, about every photo)
    "embedding": "image_id IN temp.seen_image",
    "headcount": "image_id IN temp.seen_image",  # the AI's careful count of the people in a photo (headcount.py)
    "describe_request": "account_id = {account}",
    "tag_alias": "0",
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
    granted: frozenset[int] = frozenset()  # of the photos, those they see only because they were shared with them
    tours: frozenset[int] = frozenset()  # tours others shared with them
    albums: frozenset[int] = frozenset()  # albums others shared with them
    shared_folders: frozenset[str] = frozenset()  # paths of the folders shared with them, to browse
    seeing_as: bool = False  # an admin looking through this member's eyes: changes are refused
    lists: dict = field(default_factory=dict, compare=False, repr=False)  # the sets as JSON lists, for scope()
    # a photo frame's device link (devices.py), which sees only the photos of its one view and opens only the frame
    # (web/frames.py ROUTES); None for everyone else
    device: dict | None = field(default=None, compare=False, repr=False)

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
NOBODY = Viewer(admin=False,  # accounts on, not signed in
                lists={"person": "[]", "image": "[]", "folder": "[]", "tour": "[]", "album": "[]"})


def admin(account: dict) -> Viewer:
    return Viewer(account=account, admin=True)


def device(link: dict, images, shas) -> Viewer:
    """A photo frame's device link (devices.py): nobody's account, nobody's names, only the photos and videos its one
    view shows. web/frames.py works those out and says which pages it may open."""
    return Viewer(account=None, admin=False, images=frozenset(images), shas=frozenset(shas), device=link,
                  lists={"person": "[]", "image": json.dumps(sorted(images)), "folder": "[]", "tour": "[]",
                         "album": "[]"})


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


def hidden(con: sqlite3.Connection) -> tuple[str, list]:
    """SQL for the photos an admin hid from members, one by one or by hiding a folder they have a copy in, and its
    arguments. One SELECT, so that it can follow EXCEPT."""
    folders = [r[0] for r in con.execute("SELECT path FROM main.folder WHERE hidden")]
    sql = "SELECT id FROM main.image WHERE hidden"
    if folders:
        sql += (" OR id IN (SELECT v.image_id FROM main.file v WHERE "
                + " OR ".join("v.path = ? OR v.path LIKE ? ESCAPE '!'" for _ in folders) + ")")
    return sql, [a for p in folders for a in (p, under(p))]


def images_for(con: sqlite3.Connection, people: set[int], account_id: int | None, upload_folder: str | None,
               alone: bool = False) -> set[int]:
    """The photos a member sees: someone they see is in it, by a face named by hand or recognised or added by hand,
    unless hidden by an admin; and their own uploads, hidden or not. alone (a demo, demo.py): never what other
    accounts uploaded, whoever is in it."""
    not_shown, args = hidden(con)
    of_people = con.execute(f"""
        WITH seen(id) AS (SELECT value FROM json_each(?))
        SELECT image_id FROM face WHERE person_id IN seen AND assigned_by IN ('user', 'auto')
        UNION SELECT image_id FROM image_person WHERE person_id IN seen
        EXCEPT {not_shown}""", (json.dumps(sorted(people)), *args))
    out = {r[0] for r in of_people}
    if alone:
        out -= others_uploads(con, account_id)
    if account_id is not None:
        out |= {r[0] for r in con.execute("SELECT image_id FROM upload WHERE account_id = ?", (account_id,))}
    if upload_folder:
        out |= {r[0] for r in con.execute("""SELECT image_id FROM file WHERE image_id IS NOT NULL
                                             AND path LIKE ? ESCAPE '!'""", (under(upload_folder),))}
    return out


def others_uploads(con: sqlite3.Connection, account_id: int | None) -> set[int]:
    """The photos other accounts uploaded: recorded as theirs, or in their uploads folders. Not a photo the library has
    a copy of outside every uploads folder: uploading a copy of it doesn't hide it from everyone else."""
    folders = [r[0] for r in con.execute("SELECT upload_folder FROM main.account WHERE upload_folder IS NOT NULL "
                                         "AND id IS NOT ?", (account_id,))]
    mine = con.execute("SELECT upload_folder FROM main.account WHERE id IS ?", (account_id,)).fetchone()
    every = folders + ([mine[0]] if mine and mine[0] else [])
    theirs = {r[0] for r in con.execute("SELECT image_id FROM main.upload WHERE account_id IS NOT ?", (account_id,))}
    library = set()
    files = con.execute("SELECT image_id, path FROM main.file WHERE image_id IS NOT NULL AND NOT missing")
    for image_id, path in files:
        if any(within(path, f) for f in folders):
            theirs.add(image_id)
        elif not any(within(path, f) for f in every):
            library.add(image_id)
    return theirs - library


def folders_for(con: sqlite3.Connection, images: set[int], upload_folder: str | None, shared=()) -> set[int]:
    """The folders a member's photos are in, the folders above those (where places in folder names come from), their
    own uploads folder and everything in it, and the folders shared with them (`shared`, paths; the folders in those
    come with their photos). Members browse only their uploads folder and those shared with them (routes/browse.py)."""
    parents = dict(con.execute("SELECT id, parent_id FROM folder"))
    found = [r[0] for r in con.execute("""SELECT DISTINCT folder_id FROM file
                                          WHERE image_id IN (SELECT value FROM json_each(?))""",
                                       (json.dumps(sorted(images)),))]
    if upload_folder:
        found += [r[0] for r in con.execute("SELECT id FROM folder WHERE path = ? OR path LIKE ? ESCAPE '!'",
                                            (upload_folder, under(upload_folder)))]
    found += [r[0] for r in con.execute("SELECT id FROM folder WHERE path IN (SELECT value FROM json_each(?))",
                                        (json.dumps(sorted(shared)),))]
    out = set()
    for fid in found:
        while fid is not None and fid not in out:
            out.add(fid)
            fid = parents.get(fid)
    return out


@dataclass(frozen=True)
class Shared:
    """What other accounts shared with one: the photos, the tours, and the folders' paths."""
    images: frozenset[int] = frozenset()
    tours: frozenset[int] = frozenset()
    folders: frozenset[str] = frozenset()
    albums: frozenset[int] = frozenset()


def within(path: str | None, folder: str | None) -> bool:
    """path is folder or somewhere in it."""
    return path is not None and bool(folder) and (path == folder or path.startswith(folder + "/"))


def active_shares(con: sqlite3.Connection, account_id: int) -> list[sqlite3.Row]:
    """The shares with this account, or with everyone, that still hold: from another account that isn't switched off,
    of what is still that account's to share. A member shares only their own uploads (in their uploads folder, or
    recorded as theirs) and their own tours; an admin, anything. So an admin made a member, or an upload an admin moved
    into the library, shares no more than it may."""
    rows = con.execute("""
        SELECT s.*, a.role AS by_role, a.upload_folder AS by_uploads, f.path AS folder_path,
               t.account_id AS tour_maker, al.account_id AS album_maker
        FROM main.share s JOIN main.account a ON a.id = s.account_id
        LEFT JOIN main.folder f ON f.id = s.folder_id LEFT JOIN main.tour t ON t.id = s.tour_id
        LEFT JOIN main.album al ON al.id = s.album_id
        WHERE a.disabled_at IS NULL AND s.account_id != :me AND (s.with_id = :me OR s.with_id IS NULL)
        ORDER BY s.id""", {"me": account_id}).fetchall()
    return [r for r in rows if r["by_role"] == "admin" or theirs_to_share(con, r)]


def theirs_to_share(con: sqlite3.Connection, share: sqlite3.Row) -> bool:
    """Whether a member's share is of their own: a folder of their uploads, their own tour, or a photo they uploaded.
    share has the sharer's uploads folder (by_uploads), the folder's path and the tour's maker (as active_shares)."""
    if share["folder_id"] is not None:
        return within(share["folder_path"], share["by_uploads"])
    if share["tour_id"] is not None:
        return share["tour_maker"] == share["account_id"]
    if "album_id" in share.keys() and share["album_id"] is not None:  # their own album (what it passes on: shared_with)
        return share["album_maker"] == share["account_id"]
    return con.execute("""SELECT 1 FROM main.upload WHERE image_id = :i AND account_id = :a
                          UNION SELECT 1 FROM main.file WHERE image_id = :i AND NOT missing
                                                          AND path LIKE :under ESCAPE '!'""",
                       {"i": share["image_id"], "a": share["account_id"],
                             "under": under(share["by_uploads"]) if share["by_uploads"] else ""}).fetchone() is not None


def shared_with(con: sqlite3.Connection, account_id: int) -> Shared:
    """What other accounts shared with this one (active_shares), on a connection that sees everything: a folder's
    photos (in it or below, now and later), one photo, or the photos a tour showed its maker when it was last saved,
    shared or opened (tour_photo). Never a photo an admin hid."""
    shares = active_shares(con, account_id)
    images = {r["image_id"] for r in shares if r["image_id"] is not None}
    folders = {r["folder_path"] for r in shares if r["folder_id"] is not None}
    tours = {r["tour_id"] for r in shares if r["tour_id"] is not None}
    for path in folders:
        images |= {r[0] for r in con.execute("""SELECT image_id FROM main.file WHERE NOT missing AND image_id IS NOT NULL
                                                AND path LIKE ? ESCAPE '!'""", (under(path) if path else "%",))}
    if tours:
        images |= {r[0] for r in con.execute("""SELECT image_id FROM main.tour_photo
                                                WHERE tour_id IN (SELECT value FROM json_each(?))""",
                                             (json.dumps(sorted(tours)),))}
    # an album's photos as its maker may pass them on, kept when it was last changed, shared or opened (album_passed)
    albums = {r["album_id"] for r in shares if r["album_id"] is not None}
    if albums:
        images |= {r[0] for r in con.execute("""SELECT image_id FROM main.album_passed
                                                WHERE album_id IN (SELECT value FROM json_each(?))""",
                                             (json.dumps(sorted(albums)),))}
    if images:
        not_shown, args = hidden(con)
        images -= {r[0] for r in con.execute(not_shown, args)}
    return Shared(frozenset(images), frozenset(tours), frozenset(folders), frozenset(albums))


def compute(con: sqlite3.Connection, account: dict, shared: bool = True, alone: bool = False) -> Viewer:
    """Work out what an account sees, on a connection that sees everything. shared False: without what others shared
    with them, which is what a tour of theirs passes on when they share it (routes/tours.py). alone: without what
    other accounts uploaded (a demo)."""
    if account["role"] == "admin":
        return admin(account)
    g = family.load(con)
    rules = con.execute("SELECT person_id, show, branch FROM access_rule WHERE account_id = ?",
                        (account["id"],)).fetchall()
    added = {r[0] for r in con.execute("""SELECT id FROM person WHERE added_by = :a
                                          UNION SELECT person_id FROM person_group WHERE added_by = :a""",
                                       {"a": account["id"]})}
    people = people_for(g, account["person_id"], [tuple(r) for r in rules], added)
    images = images_for(con, people, account["id"], account["upload_folder"], alone)
    others = shared_with(con, account["id"]) if shared else Shared()
    granted = others.images - images
    images |= granted
    # the clip of each Live Photo they see, which its page plays (live.py); no page lists one
    images |= {clip for clip, still in con.execute("SELECT id, live_of FROM image WHERE live_of IS NOT NULL"
                                                   " AND NOT hidden") if still in images}
    shas = {r[0] for r in con.execute("SELECT sha256 FROM image WHERE id IN (SELECT value FROM json_each(?))",
                                      (json.dumps(sorted(images)),))}
    folders = folders_for(con, images, account["upload_folder"], others.folders)
    return Viewer(account=account, admin=False, people=frozenset(people), images=frozenset(images),
                  shas=frozenset(shas), folders=frozenset(folders), granted=frozenset(granted), tours=others.tours,
                  shared_folders=others.folders, albums=others.albums,
                  lists={"person": json.dumps(sorted(people)), "image": json.dumps(sorted(images)),
                         "folder": json.dumps(sorted(folders)), "tour": json.dumps(sorted(others.tours)),
                         "album": json.dumps(sorted(others.albums))})


class Viewers:
    """Each account's Viewer, kept REFRESH seconds, and worked out again sooner after any change made through the web
    app (changed()). Worked out once at a time for each account: after a change, the dozens of thumbnails and face
    crops a page asks for at once all need it, and each working it out for itself (0.4 s for a member who sees 40,000
    photos) kept every one of them waiting several seconds on the others."""

    def __init__(self, alone: bool = False):
        """alone: no member sees what other accounts uploaded (a demo, demo.py)."""
        self.alone = alone
        self.kept: dict[tuple, tuple[Viewer, float, int]] = {}
        self.generation = 0
        self.lock = threading.Lock()
        self.working: dict[tuple, threading.RLock] = {}  # key -> held while its Viewer is worked out

    def changed(self) -> None:
        with self.lock:
            self.generation += 1

    def fresh(self, account: dict, shared: bool = True) -> Viewer | None:
        """The account's Viewer if one is kept and still current: no connection needed (each thumbnail asks)."""
        key = (account["id"], account["role"], account["person_id"], account["upload_folder"], shared)
        with self.lock:
            found = self.kept.get(key)
            if found and time.monotonic() - found[1] < REFRESH and found[2] == self.generation:
                return found[0]
        return None

    def get(self, con: sqlite3.Connection, account: dict, shared: bool = True) -> Viewer:
        """shared False: without what others shared with them (compute)."""
        if (found := self.fresh(account, shared)) is not None:
            return found
        key = (account["id"], account["role"], account["person_id"], account["upload_folder"], shared)
        with self.lock:
            working = self.working.setdefault(key, threading.RLock())
        with working:  # one request works it out; the others wait, then take it
            if (found := self.fresh(account, shared)) is not None:
                return found
            with self.lock:
                generation = self.generation
            viewer = compute(con, account, shared, self.alone)
            with self.lock:
                self.kept = {k: v for k, v in self.kept.items() if k[0] != account["id"] or k[-1] != shared}
                self.kept[key] = (viewer, time.monotonic(), generation)
        return viewer


# ---- a member's connection -----------------------------------------------------------------------------------------

def scope(con: sqlite3.Connection, viewer: Viewer) -> sqlite3.Connection:
    """Make a connection the viewer's: "me" is them, and for a member only what they may see is there to read."""
    con.me = viewer.me
    if viewer.admin:
        return con
    for name in ("person", "image", "folder", "tour", "album"):
        con.execute(f"CREATE TEMP TABLE seen_{name}(id INTEGER PRIMARY KEY)")
        con.execute(f"INSERT INTO temp.seen_{name} SELECT value FROM json_each(?)", (viewer.lists.get(name, "[]"),))
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
