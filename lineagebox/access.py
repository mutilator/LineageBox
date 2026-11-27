"""What a family member who signs in may see: their part of the family tree, and the photos those people are in.

For a member who is person P (docs/access-control.md, "Who a member sees"):
- people: P, P's ancestors, every blood relative (everyone descended from those ancestors), the partners of all of
  them (someone else's former partner only when they share a child with someone in the tree, but P's own always: an
  ex-wife is in the photos of the years they were together) and those partners' children; the same again from P's
  current partner, so a couple sees the same family; people the member added themselves, and those in
  groups they made; then the admin's exceptions on the account (someone, alone or with their family, also shown or
  not shown). Friends, pets and other relationships are outside it.
- photos: those someone they can see is in (a face named by hand or recognized, or added to the photo by hand), and
  their own uploads. A photo an admin hid, or one in a hidden folder, is left out unless they uploaded it.
- and what another account shared with them, or with everyone (sharing.py): a folder, a photo, a tour with the photos
  it shows its maker, or an album with the photos its maker may pass on (albums.py). Only shares that still hold count:
  from an account not switched off, of what is still theirs to share (a member's own uploads, tours and albums), and
  never a photo an admin hid.
- and what their partner adds, without either of them having to share it (partner_shares; Settings → Family accounts):
  a couple see each other's uploads, albums and tours, and so each other's journeys, as long as the tree has them as
  current partners -- married or not. Switched off, or in a demo, there is none of this.
Admins see everything; so does everyone while accounts are off.

The web app keeps a member to that in two ways: every route says who may use it, and a member's connection only
holds what they can see. scope() adds temporary views named like the tables they hide; SQLite looks in the temp schema
first, so every query of the app reads only the member's rows without being written for it, and a write to one of the
views fails instead of reaching hidden rows (members' changes are checked against the Viewer and written on a
connection of their own, web/app.py).
"""
import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from lineagebox import family, settings
from lineagebox.catalog import under
from lineagebox.config import Config

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
    # a historical paper's pages and words (historical.py), and whether a photo is one: with the paper's first page
    "paper_page": "paper_id IN temp.seen_image",
    "paper_check": "image_id IN temp.seen_image",
    # who a paper names, of those they see (paper_cards.py); the papers they opened and were offered (introductions.py)
    "paper_person": "paper_id IN temp.seen_image AND person_id IN temp.seen_person",
    "paper_seen": "account_id = {account}",
    "paper_offer": "account_id = {account}",
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
    "memories_kept": "account_id = {account}",  # Home's memories, as each person saw them (memories.kept)
    "device_link": "0",
    # Search by meaning, describing again and tags across the library (embeddings.py, describe.py, tagging.py): the
    # meaning of the photos they see, their own asks, and none of the tags' renames (an admin's, about every photo)
    "embedding": "image_id IN temp.seen_image",
    "headcount": "image_id IN temp.seen_image",  # the AI's careful count of the people in a photo (headcount.py)
    "describe_request": "account_id = {account}",
    "tag_alias": "0",
    "reorganize_run": "0",  # how the photo folders were reorganized, and every file moved (reorganize.py): admins'
    "reorganize_move": "0",
}
WITH_ROWID = {"image_tag"}  # read by rowid (the order tags were given in): a view has none unless it says so
# Each "x IN temp.seen_..." of a view is written "+x IN ...", which only checks the rows a query finds, never finds
# them. SQLite takes any IN (a list) to hold 25 rows, so for "image_id IN (SELECT value FROM json_each(?))" over a view
# it picked the view's own list (every photo the member sees) to look rows up by, and checked each against the few
# asked for: 0.14 s a query whatever its size. Home's memories, a hundred such queries, took 168 s for a member
# instead of 3 (measured on a library of 55,000 photos, 2026-11-17); a count of a whole table is a little slower.
CHECK_ONLY = re.compile(r"(?<![\w.+])(\w+) IN temp\.seen_")


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
    partners: frozenset[int] = frozenset()  # the accounts of their partners, whose albums, tours and uploads they see
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
        for partner, (current, _bond) in g.partners.get(b, {}).items():
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
        # Their own partners, current or former. family_side keeps someone else's ex out unless a child ties them to
        # the family (a cousin's ex-girlfriend is not the family's business), but one's own ex is: they are in the
        # photos of the years the two were together, with or without children (the user's ask, 2026-11-16).
        seen |= set(g.partners.get(pid, {}))
        for partner, (current, _bond) in g.partners.get(pid, {}).items():
            if current:
                seen |= family_side(g, partner)
    seen |= set(added)
    for person, show, branch in sorted(rules, key=lambda r: -r[1]):  # shown first, so "not shown" wins
        if person in g.people:
            them = family_side(g, person) if branch else {person}
            seen = seen | them if show else seen - them
    return {p for p in seen if p in g.people}


def hidden(con: sqlite3.Connection) -> tuple[str, list]:
    """SQL for the photos an admin hid from members, one by one or by hiding a folder they have a copy in (or had,
    before a reorganization moved it into a year and month folder: reorganize.py), and its arguments. One SELECT, so
    that it can follow EXCEPT."""
    folders = [r[0] for r in con.execute("SELECT path FROM main.folder WHERE hidden")]
    sql = "SELECT id FROM main.image WHERE hidden"
    if folders:
        sql += (" OR id IN (SELECT v.image_id FROM main.file v WHERE "
                + " OR ".join("v.path = ? OR v.path LIKE ? ESCAPE '!'" for _ in folders) + ")"
                + " OR id IN (SELECT v.image_id FROM main.file v JOIN main.folder d ON d.id = v.from_folder_id WHERE "
                + " OR ".join("d.path = ? OR d.path LIKE ? ESCAPE '!'" for _ in folders) + ")")
    args = [a for p in folders for a in (p, under(p))]
    return sql, args + args


def images_for(con: sqlite3.Connection, people: set[int], account_id: int | None, upload_folder: str | None,
               alone: bool = False) -> set[int]:
    """The photos a member sees: someone they see is in it, by a face named by hand or recognized or added by hand,
    or (a historical paper) named in it, linked by its card or by hand, unless hidden by an admin; and their own uploads, hidden or not. alone (a demo, demo.py): never what other
    accounts uploaded, whoever is in it."""
    not_shown, args = hidden(con)
    of_people = con.execute(f"""
        WITH seen(id) AS (SELECT value FROM json_each(?))
        SELECT image_id FROM face WHERE person_id IN seen AND assigned_by IN ('user', 'auto')
        UNION SELECT image_id FROM image_person WHERE person_id IN seen
        UNION SELECT paper_id FROM paper_person WHERE person_id IN seen AND state IN ('auto', 'user')
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
    # and the folders those files came from, before a reorganization moved them (catalog.ORIGIN): what their names
    # say still goes for the photos (folders.FolderIndex)
    found = [r[0] for r in con.execute("""SELECT folder_id FROM file WHERE image_id IN (SELECT value FROM json_each(?))
                                          UNION SELECT from_folder_id FROM file WHERE from_folder_id IS NOT NULL
                                          AND image_id IN (SELECT value FROM json_each(?))""",
                                       (json.dumps(sorted(images)),) * 2)]
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
    partners: frozenset[int] = frozenset()  # the accounts whose share needed no asking (partner_shares)


def within(path: str | None, folder: str | None) -> bool:
    """path is folder or somewhere in it."""
    return path is not None and bool(folder) and (path == folder or path.startswith(folder + "/"))


def couples_share(con: sqlite3.Connection) -> bool:
    """Whether what one of a couple adds is seen by the other without either having to share it (Settings → Family
    accounts). Kept in the catalog, so turning it off takes hold as soon as each view is worked out again."""
    return bool(settings.get(con, "partners_share", Config.partners_share))


def partner_accounts(con: sqlite3.Connection, account_id: int) -> list[sqlite3.Row]:
    """The accounts of this account's current partners, married or not, as the family tree has them. Empty while
    couples_share is off, for an account that is nobody in the tree, and for a partner with no account or one that is
    switched off. On a connection that sees everyone."""
    if not couples_share(con):
        return []
    return con.execute("""SELECT o.id, o.role, o.person_id, o.upload_folder, p.name
                          FROM main.account me
                          JOIN main.partner r ON r.current AND me.person_id IN (r.a, r.b)
                          JOIN main.account o ON o.person_id = CASE WHEN r.a = me.person_id THEN r.b ELSE r.a END
                          LEFT JOIN main.person p ON p.id = o.person_id
                          WHERE me.id = ? AND me.person_id IS NOT NULL AND o.id != me.id AND o.disabled_at IS NULL
                          ORDER BY o.id""", (account_id,)).fetchall()


def partner_shares(con: sqlite3.Connection, account_id: int) -> list[dict]:
    """What this account's partner adds, shared with them without either of them having to: the partner's uploads
    folder, and every album and tour of theirs (an album's journey is the album, so it comes with it). An album or
    tour with no account on it is the admins' own, nobody's in particular, and goes to no partner. Shaped like
    the rows of active_shares, with no id of their own: they are worked out afresh every time, so a couple who part
    stop seeing each other's at once and there is nothing left behind to take back."""
    out = []
    for them in partner_accounts(con, account_id):
        theirs = {"id": None, "account_id": them["id"], "with_id": account_id, "created_at": None,
                  "by_role": them["role"], "by_uploads": them["upload_folder"], "automatic": True,
                  "folder_id": None, "image_id": None, "tour_id": None, "album_id": None,
                  "folder_path": None, "tour_maker": None, "album_maker": None}
        if them["upload_folder"]:
            row = con.execute("SELECT id, path FROM main.folder WHERE path = ?", (them["upload_folder"],)).fetchone()
            if row is not None:
                out.append({**theirs, "folder_id": row["id"], "folder_path": row["path"]})
        for row in con.execute("SELECT id FROM main.album WHERE account_id = ? ORDER BY id",
                               (them["id"],)).fetchall():
            out.append({**theirs, "album_id": row["id"], "album_maker": them["id"]})
        for row in con.execute("SELECT id FROM main.tour WHERE account_id = ? ORDER BY id",
                               (them["id"],)).fetchall():
            out.append({**theirs, "tour_id": row["id"], "tour_maker": them["id"]})
    return out


def automatic(share) -> bool:
    """Whether a share row is one a partner never had to make (partner_shares), rather than one from the share
    table. Takes a row of either (both answer keys())."""
    return "automatic" in share.keys() and bool(share["automatic"])


def active_shares(con: sqlite3.Connection, account_id: int, partners: bool = True) -> list:
    """The shares with this account, or with everyone, that still hold: from another account that isn't switched off,
    of what is still that account's to share. A member shares only their own uploads (in their uploads folder, or
    recorded as theirs) and their own tours; an admin, anything. So an admin made a member, or an upload an admin moved
    into the library, shares no more than it may. What their partner adds (partner_shares) follows, needing no share
    of its own; partners False leaves it out, as a demo does."""
    rows = con.execute("""
        SELECT s.*, a.role AS by_role, a.upload_folder AS by_uploads, f.path AS folder_path,
               t.account_id AS tour_maker, al.account_id AS album_maker
        FROM main.share s JOIN main.account a ON a.id = s.account_id
        LEFT JOIN main.folder f ON f.id = s.folder_id LEFT JOIN main.tour t ON t.id = s.tour_id
        LEFT JOIN main.album al ON al.id = s.album_id
        WHERE a.disabled_at IS NULL AND s.account_id != :me AND (s.with_id = :me OR s.with_id IS NULL)
        ORDER BY s.id""", {"me": account_id}).fetchall()
    held = [r for r in rows if r["by_role"] == "admin" or theirs_to_share(con, r)]
    return held + (partner_shares(con, account_id) if partners else [])


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


def shared_with(con: sqlite3.Connection, account_id: int, partners: bool = True) -> Shared:
    """What other accounts shared with this one (active_shares), on a connection that sees everything: a folder's
    photos (in it or below, now and later), one photo, or the photos a tour showed its maker when it was last saved,
    shared or opened (tour_photo). Never a photo an admin hid. partners False: without what a partner's account
    passes on without being asked (a demo, where a visitor's uploads are theirs alone)."""
    shares = active_shares(con, account_id, partners)
    images = {r["image_id"] for r in shares if r["image_id"] is not None}
    folders = {r["folder_path"] for r in shares if r["folder_id"] is not None}
    tours = {r["tour_id"] for r in shares if r["tour_id"] is not None}
    for path in folders:  # what is in it, and what was, before a reorganization moved it (reorganize.py)
        images |= {r[0] for r in con.execute("""SELECT image_id FROM main.file WHERE NOT missing AND image_id IS NOT NULL
                                                AND path LIKE ? ESCAPE '!'""", (under(path) if path else "%",))}
        images |= {r[0] for r in con.execute("""SELECT v.image_id FROM main.file v
                                                JOIN main.folder d ON d.id = v.from_folder_id
                                                WHERE NOT v.missing AND v.image_id IS NOT NULL
                                                AND (d.path = ? OR d.path LIKE ? ESCAPE '!')""",
                                             (path, under(path) if path else "%"))}
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
    # a partner's uploads wherever they lie: one recorded as theirs that a reorganization moved out of their uploads
    # folder, or an admin partner's, who may have no such folder at all
    partner_ids = {r["account_id"] for r in shares if automatic(r)}
    if partner_ids:
        images |= {r[0] for r in con.execute("""SELECT image_id FROM main.upload
                                                WHERE account_id IN (SELECT value FROM json_each(?))""",
                                             (json.dumps(sorted(partner_ids)),))}
    if images:
        not_shown, args = hidden(con)
        images -= {r[0] for r in con.execute(not_shown, args)}
    return Shared(frozenset(images), frozenset(tours), frozenset(folders), frozenset(albums), frozenset(partner_ids))


def compute(con: sqlite3.Connection, account: dict, shared: bool = True, alone: bool = False) -> Viewer:
    """Work out what an account sees, on a connection that sees everything. shared False: without what others shared
    with them, which is what a tour of theirs passes on when they share it (routes/tours.py). alone: without what
    other accounts uploaded, nor what a partner adds (a demo)."""
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
    others = shared_with(con, account["id"], partners=not alone) if shared else Shared()
    granted = others.images - images
    images |= granted
    # the clip of each Live Photo they see, which its page plays (live.py), and the later pages of each paper they
    # see, which its page shows (historical.py); no page lists one
    images |= {clip for clip, still in con.execute("SELECT id, live_of FROM image WHERE live_of IS NOT NULL"
                                                   " AND NOT hidden") if still in images}
    images |= {page for page, first in con.execute("SELECT id, page_of FROM image WHERE page_of IS NOT NULL"
                                                   " AND NOT hidden") if first in images}
    shas = {r[0] for r in con.execute("SELECT sha256 FROM image WHERE id IN (SELECT value FROM json_each(?))",
                                      (json.dumps(sorted(images)),))}
    folders = folders_for(con, images, account["upload_folder"], others.folders)
    return Viewer(account=account, admin=False, people=frozenset(people), images=frozenset(images),
                  shas=frozenset(shas), folders=frozenset(folders), granted=frozenset(granted), tours=others.tours,
                  shared_folders=others.folders, albums=others.albums, partners=others.partners,
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
        where = CHECK_ONLY.sub(r"+\1 IN temp.seen_", where).replace("{account}", str(int(viewer.account_id or 0)))
        con.execute(f"CREATE TEMP VIEW {table} AS SELECT {columns} FROM main.{table} WHERE {where}")
    return con


def names_of(con: sqlite3.Connection, person_ids) -> dict[int, str]:
    """The names of people, on a connection that sees everyone: for the names of people outside a member's part of
    the family who are in a photo they can see (shown without a link)."""
    ids = sorted({p for p in person_ids if p is not None})
    return dict(con.execute("SELECT id, name FROM person WHERE id IN (SELECT value FROM json_each(?))",
                            (json.dumps(ids),))) if ids else {}
