"""Faces -> people: cluster unnamed faces, name clusters, and recognise more faces of named people."""
import json
import math
import sqlite3
from collections import Counter, defaultdict

import numpy as np
from sklearn.cluster import HDBSCAN
from sklearn.decomposition import PCA

from lineagebox import errors, events, family, search, settings, tours
from lineagebox.catalog import VISIBLE
from lineagebox.faces import MIN_QUALITY, usable_faces_sql
from lineagebox.folders import FolderIndex

# Recognition: a face is labelled automatically only when its best person clearly beats the runner-up.
# Measured on the library LineageBox was developed on: the same adult across decades scores 0.6-0.75; two young
# brothers score ~0.5. Their ages don't tell them apart either: the face's estimated age (face_ages.py) put the older
# brother as the older one in 49% of the 1,357 photos with both, so it is no part of recognition. Their birth dates do
# where they can: nobody is recognised in a photo taken before they were born (_before_birth).
TOP_K = 3
AUTO_SIMILARITY = 0.5
AUTO_MARGIN = 0.1

# Suggestions (People → Seen together): an unnamed face in a photo where others are already named is most likely
# someone else, so its closest match is looked for among everyone not in the photo. Measured on that library, on about
# 4,000 hand-named faces in such photos, each compared with the faces of other days only: the closest person not in
# the photo was right 99.7% of the time when they scored 0.35 and beat the next by 0.05, which 91% of the faces did
# (recognition's own bar reached 68%). Being often photographed with the others made it no more right.
SUGGEST_SIMILARITY = 0.35
SUGGEST_MARGIN = 0.05

# A video keeps one face per person (faces.one_per_person, at 0.6 alike), but someone turned away or in other light
# scores less and is kept as a face of their own, often unnamed beside their own named face. Such a face is named
# after whoever on the video's other frames it is most like (nobody named on its own frame: they are someone else
# there). Measured on that library by leaving each named video face out: at 0.5, beating the next person by 0.05, 51%
# of the faces whose person was named elsewhere in their video were named, none as the wrong one of them, and 1.1% of
# those whose person was not got the name of someone who was (brothers, mostly). From 0.35 (93% right) it is only
# suggested, on Cleanup → Confirm faces.
IN_VIDEO_SIMILARITY = 0.5
IN_VIDEO_SUGGEST = 0.35
IN_VIDEO_MARGIN = 0.05

# How sure a name is (People → Name faces, Check faces, yes to the surest at once): the share of faces with its score
# for someone, and its lead over the next likeliest person who can be it, that are them. Measured on the library
# LineageBox was developed on by scoring 5,868 faces named by hand against the hand-named faces of other days, as a new
# photo is, and fitted as a logistic curve in the score and the lead: where it says 96% sure, 95.7% were right; 98.6%,
# 99.0%. Checked against people's own answers about 1,483 faces recognised there: as right up to 98.6% sure (it said
# 93.7%, 93.3% were; 98.6%, 98.7%), but of the faces it was surer of than that, 1.4% were someone else all the same (a
# brother, a face too small to tell), so it never says more than SURE_MOST. Below the lead it was measured from
# (SUGGEST_MARGIN), a name can't be told from the next likeliest: a tie is even, and a face that looks more like someone
# else (a recognised face, after more were named) is less than that, down to none.
SURE_BASE, SURE_SCORE, SURE_LEAD = -2.78, 10.48, 17.06
SURE_MOST = 0.98
SURE_BARS = (0.98, 0.95, 0.9)  # what "yes to every face at least this sure" offers
MEASURED_KEY = "recognised_measured"  # settings: the faces named by hand when the recognised ones were last scored


def how_sure(score: float | None, lead: float | None) -> float | None:
    """How likely a face scoring this for someone, this far ahead of the next likeliest, is them: 0 to SURE_MOST (None
    when either isn't known)."""
    if score is None or lead is None:
        return None
    curve = 1 / (1 + math.exp(-(SURE_BASE + SURE_SCORE * score + SURE_LEAD * lead)))
    return max(0.0, min(SURE_MOST, curve, 0.5 + lead / (2 * SUGGEST_MARGIN)))


def _sure_sql(bar: float, f: str = "f") -> str:
    """SQL: a recognised face f is at least bar sure (how_sure, for a bar from 0.5 up to SURE_MOST)."""
    edge = math.log(bar / (1 - bar)) - 1e-9
    return (f"({f}.assign_lead IS NOT NULL AND {SURE_BASE} + {SURE_SCORE} * {f}.assign_score "
            f"+ {SURE_LEAD} * {f}.assign_lead >= {edge} AND {f}.assign_lead >= {(bar - 0.5) * 2 * SUGGEST_MARGIN - 1e-9})")


# the least sure first: one not scored again yet counts as ahead by recognition's bar
_LEAST_SURE_FIRST = (f"min(coalesce(f.assign_lead, {AUTO_MARGIN}), {SUGGEST_MARGIN}), "
                     f"{SURE_SCORE} * f.assign_score + {SURE_LEAD} * coalesce(f.assign_lead, {AUTO_MARGIN}), f.id")


def _embeddings(rows) -> np.ndarray:
    return np.frombuffer(b"".join(r["embedding"] for r in rows), dtype=np.float32).reshape(len(rows), -1)


def cluster(con: sqlite3.Connection) -> tuple[int, int]:
    """Re-cluster faces that nobody has named or set aside. Returns (clusters, faces in clusters)."""
    rows = con.execute(f"""SELECT f.id, f.image_id, f.embedding FROM face f JOIN image i ON i.id = f.image_id
                           WHERE f.person_id IS NULL AND f.assigned_by IS NULL AND {usable_faces_sql()} AND {VISIBLE}""").fetchall()
    labels = np.full(len(rows), -1)
    if len(rows) >= 4:
        X = _embeddings(rows)
        if len(X) > 2000:
            # Measured on about 8,000 faces of that library: 128 PCA dimensions gave the purest groups (1.9% of pairs
            # inside a group looked like different people, vs 2.6% at 64 dims, which also merged people into giant
            # groups, and 3.7% unreduced) and ran 7x faster than the full 512 dimensions.
            X = PCA(n_components=128, random_state=0).fit_transform(X)
            X /= np.linalg.norm(X, axis=1, keepdims=True)
        labels = HDBSCAN(min_cluster_size=4, min_samples=2, copy=True).fit_predict(X)
    if len(rows) >= 2:
        labels = _small_groups(rows, labels)
    # Written only once the (minute-long) clustering is done, so the database is not locked meanwhile. A face named
    # in the web app during that minute keeps its name and stays out of the groups.
    con.execute("UPDATE face SET cluster_id = NULL WHERE person_id IS NULL")
    con.executemany("UPDATE face SET cluster_id = ? WHERE id = ? AND person_id IS NULL AND assigned_by IS NULL",
                    ((int(label), r["id"]) for r, label in zip(rows, labels) if label >= 0))
    con.commit()
    clustered = int((labels >= 0).sum())
    return len(set(labels[labels >= 0].tolist())), clustered


# Groups of 2 or 3: HDBSCAN groups 4 faces or more (smaller made it split people's groups up: at 2, 1,649 groups of
# that library's 7,716 hand-named faces instead of 347), so someone in only two or three photos was never grouped.
# Among the faces it leaves out, each is linked to its closest other (never one of its own photo: nobody is in a photo
# twice) when they score SMALL_GROUP_SIMILARITY, and what those links join, if 2 or 3 faces, is a group. Measured on
# those hand-named faces (1,354 left out by HDBSCAN): at 0.5, 135 pairs were one person 99.3% of the time and 72
# threes 100% (joined fours and more were 92%, so they are left out); at 0.45, 99.2% and 98.6%.
SMALL_GROUP_SIMILARITY = 0.5
SMALL_GROUP_MOST = 3


def _small_groups(rows, labels: np.ndarray) -> np.ndarray:
    """labels with the faces HDBSCAN left out (-1) grouped in twos and threes where they are plainly one person."""
    left = np.nonzero(labels < 0)[0]
    if len(left) < 2:
        return labels
    X = _embeddings([rows[i] for i in left])
    photo = np.array([rows[i]["image_id"] for i in left])
    closest, score = np.empty(len(left), dtype=int), np.empty(len(left), dtype=np.float32)
    for start in range(0, len(left), 1024):  # 1,024 rows at a time: a big library leaves out thousands
        sims = X[start:start + 1024] @ X.T
        sims[photo[start:start + 1024, None] == photo[None, :]] = -1.0  # itself, and its photo's other faces
        closest[start:start + 1024], score[start:start + 1024] = sims.argmax(axis=1), sims.max(axis=1)
    parent = list(range(len(left)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(left)):
        if score[i] >= SMALL_GROUP_SIMILARITY:
            parent[root(i)] = root(int(closest[i]))
    joined = defaultdict(list)
    for i in range(len(left)):
        joined[root(i)].append(i)
    out, label = labels.copy(), int(labels.max()) + 1
    for members in sorted(joined.values(), key=min):
        if 2 <= len(members) <= SMALL_GROUP_MOST:
            out[left[members]] = label
            label += 1
    return out


def existing_person_id(con: sqlite3.Connection, name: str, pid: int | None = None,
                       among: set[int] | None = None) -> int | None:
    """person_id's answer when the name box means someone already there, else None (nobody new is made)."""
    name = " ".join(name.split())
    if not name:
        raise Problem("Type a name.")
    if pid is not None and (among is None or pid in among):
        row = con.execute("SELECT name FROM person WHERE id = ?", (pid,)).fetchone()
        if row and " ".join(row[0].split()).lower() == name.lower():
            return pid
    found = [r[0] for r in con.execute("SELECT id FROM person WHERE lower(name) = lower(?) ORDER BY id", (name,))
             if among is None or r[0] in among]
    if len(found) == 1:
        return found[0]
    if found:
        raise Problem(f"{len(found)} people are called {name}: pick the one you mean from the names suggested as you "
                      "type.")
    return None


def person_id(con: sqlite3.Connection, name: str, pid: int | None = None, among: set[int] | None = None,
              added_by: int | None = None) -> int:
    """The person a name box means: the one picked from its suggestions (pid, while the box still holds their name),
    else the one person with that name, else someone new (with the name split for GEDCOM, to be checked).

    Two people may have the same name, so a name two people have must be picked from the suggestions. among: the
    people whoever typed it can see (None: everyone). A family member's name that matches nobody they can see is
    someone new, even when someone outside their part of the family has it; added_by records their account on them.
    """
    found = existing_person_id(con, name, pid, among)
    if found is not None:
        return found
    name = " ".join(name.split())
    given, surname = events.split_name(name)
    return con.execute("""INSERT INTO person(name, uid, given_name, surname, name_type, added_by)
                          VALUES (?, ?, ?, ?, 'birth', ?)""",
                       (name, events.new_uid(), given, surname, added_by)).lastrowid


def name_faces(con: sqlite3.Connection, face_ids: list[int], name: str, pid: int | None = None,
               among: set[int] | None = None, by: int | None = None) -> int:
    """Name faces by hand (see person_id for name, pid and among, and name_as for what naming does); by is the account
    naming them, if any."""
    return name_as(con, face_ids, person_id(con, name, pid, among, by), by)


def name_as(con: sqlite3.Connection, face_ids: list[int], pid: int, by: int | None = None) -> int:
    """Name faces as someone already chosen, by hand: a name typed, or yes to "Is this them?" (is_them). A face that
    had someone else's name (put right with Fix on its photo) is noted as not them, so it is never suggested or
    recognised as them again; one noted as not this person before is them after all. A face recognised as them that
    is now named by hand counts for recognition, which learns only from faces named by hand. Returns pid."""
    ids = json.dumps(face_ids)
    renamed = [r[0] for r in con.execute("""SELECT id FROM face WHERE id IN (SELECT value FROM json_each(?))
                                            AND person_id IS NOT ?""", (ids, pid))]
    con.execute("""INSERT OR IGNORE INTO face_not_person(face_id, person_id)
                   SELECT id, person_id FROM face WHERE id IN (SELECT value FROM json_each(?))
                     AND person_id IS NOT NULL AND person_id != ?""", (ids, pid))
    con.execute("DELETE FROM face_not_person WHERE face_id IN (SELECT value FROM json_each(?)) AND person_id = ?",
                (ids, pid))
    con.executemany("""UPDATE face SET person_id = ?, assigned_by = 'user', assign_score = NULL, assign_lead = NULL, cluster_id = NULL,
                                       named_by = ? WHERE id = ?""", ((pid, by, f) for f in face_ids))
    _reindex_faces(con, renamed)  # only photos whose names changed: the search doesn't tell by hand from recognised
    name_in_videos(con, _videos_of(con, renamed))
    return pid


def cluster_faces(con: sqlite3.Connection, cluster_id: int) -> list[int]:
    """The unnamed faces of a group (on a family member's connection, those in photos they can see)."""
    return [r[0] for r in con.execute("SELECT id FROM face WHERE cluster_id = ? AND person_id IS NULL", (cluster_id,))]


def suggest_name(folders: FolderIndex, image_ids: list[int], taken: set[str]) -> str | None:
    """A name from folder names ("Tom/2013", "Sis") when most of a group's photos are filed under it.

    Only a suggestion for the person naming the group: in the test run each person-named folder had one dominant
    face group, but other people appear in those folders too. Names already given to someone are skipped.
    """
    if not image_ids:
        return None
    counts: dict[str, int] = defaultdict(int)
    for image_id in image_ids:
        for name in folders.context(image_id).people:
            counts[name] += 1
    for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if n / len(image_ids) >= 0.5 and name.lower() not in taken:
            return name
    return None


def rename(con: sqlite3.Connection, pid: int, new_name: str, merge_same: bool = True) -> int:
    """Rename a person. With merge_same, when one other person already has the name, they are the same person typed
    twice ("jak" and "Jake") and are merged. Family members' renames never merge: they don't see everyone, and two
    people may have the same name. Returns the surviving id."""
    new_name = " ".join(new_name.split())
    if not new_name:
        raise Problem("Type a name.")
    if merge_same and (into := same_name(con, pid, new_name)) is not None:
        return merge(con, pid, into)
    given, surname = events.split_name(new_name)  # the parts follow, to be checked again
    con.execute("UPDATE person SET name = ?, given_name = ?, surname = ?, names_checked = 0 WHERE id = ?",
                (new_name, given, surname, pid))  # kept in place, so tours still find them
    _reindex_faces(con, [r[0] for r in con.execute("SELECT id FROM face WHERE person_id = ?", (pid,))])
    _reindex_images(con, _added_to(con, pid))  # and photos they were added to by hand, found by the name too
    return pid


def same_name(con: sqlite3.Connection, pid: int, new_name: str) -> int | None:
    """Whom renaming pid to new_name would merge them into (rename's merge_same): the one other person with that
    name, unless they are a relative (John Smith and his grandson John Smith are two people). None: nobody."""
    others = [r[0] for r in con.execute("SELECT id FROM person WHERE lower(name) = lower(?) AND id != ?",
                                        (" ".join(new_name.split()), pid))]
    if len(others) == 1 and family.load(con).blood(pid, others[0]) is None:
        return others[0]
    return None


def merge(con: sqlite3.Connection, pid: int, into: int) -> int:
    """One person named twice ("jak" and "Jake"): pid's faces, photos, details, family and tours go to into, and pid
    is removed. Details only into left blank are taken from pid. Returns into."""
    if pid == into:
        return into
    moved = [r[0] for r in con.execute("SELECT id FROM face WHERE person_id = ?", (pid,))]
    added = _added_to(con, pid)  # photos pid was added to by hand: found by the name kept, after
    con.execute("UPDATE face SET person_id = ? WHERE person_id = ?", (into, pid))
    con.execute("""UPDATE person SET sex = coalesce(sex, (SELECT sex FROM person WHERE id = :old)),
                                     notes = coalesce(notes, (SELECT notes FROM person WHERE id = :old)),
                                     biography = coalesce(biography, (SELECT biography FROM person WHERE id = :old)),
                                     cover_face_id = coalesce(cover_face_id,
                                                              (SELECT cover_face_id FROM person WHERE id = :old))
                   WHERE id = :new""", {"old": pid, "new": into})
    events.move(con, pid, into)  # births, deaths and the like; also person.birth_date and deceased
    # A group a member put pid in no longer counts as theirs: whether they go on seeing into is the admin's choice
    # (merge_same's keep_seeing), not a side effect of the merge.
    con.execute("UPDATE person_group SET added_by = NULL WHERE person_id = ?", (pid,))
    # Every other row that refers to a person comes along: deleting pid would drop it (ON DELETE CASCADE) or unlink it
    # (an account's person, ON DELETE SET NULL). family.merge and tours.merge do the family links and tours.
    for table in ("person_name", "person_group", "image_person", "face_not_person", "external_id", "citation",
                  "source_citation", "tree_media", "access_rule", "account", "story", "imported"):
        con.execute(f"UPDATE OR IGNORE {table} SET person_id = ? WHERE person_id = ?", (into, pid))
    con.execute("DELETE FROM image_person WHERE person_id = ?", (pid,))  # photos into was already added to
    try:
        family.merge(con, pid, into)
    except family.Problem as e:  # nothing is saved: the caller's transaction is not committed
        raise Problem(str(e)) from None
    tours.merge(con, pid, into)
    con.execute("DELETE FROM person WHERE id = ?", (pid,))
    _reindex_faces(con, moved)
    _reindex_images(con, added)
    return into


def why_kept(con: sqlite3.Connection, pid: int) -> str | None:
    """Why someone can't be removed (remove), or None when they can: someone with an account stays, as the account
    shows the family from them."""
    name = (con.execute("SELECT name FROM person WHERE id = ?", (pid,)).fetchone() or ["They"])[0]
    account = con.execute("SELECT email FROM account WHERE person_id = ?", (pid,)).fetchone()
    if account:
        return f"{name} has an account ({account[0]}): remove it first (Settings, Accounts)."
    told = con.execute("SELECT count(*) FROM story WHERE person_id = ? AND removed_at IS NULL", (pid,)).fetchone()[0]
    if told:  # the family's own voices: never lost with a person removed by mistake
        return (f"{name} has {told} recorded stor{'y' if told == 1 else 'ies'}: take "
                f"{'it' if told == 1 else 'them'} away first, or merge {name} into whoever it really is.")
    return None


def in_photos(con: sqlite3.Connection, pid: int) -> bool:
    """Whether someone is named in any photo, by a face or added by hand: removing them takes their name off those."""
    return con.execute("""SELECT EXISTS (SELECT 1 FROM face WHERE person_id = :p)
                                 OR EXISTS (SELECT 1 FROM image_person WHERE person_id = :p)""",
                       {"p": pid}).fetchone()[0]


def remove(con: sqlite3.Connection, pid: int) -> tuple[str, int]:
    """Take someone out of the catalog: added by mistake (by a reading, an import or by hand), or a name nobody wants
    any more. Every face named as them, by hand or recognised, loses the name and is unnamed again, as if it had never
    been named: it is grouped with the other unnamed faces, recognised as whoever it clearly is, or named by hand.
    Photos they were added to by hand leave them out. Their family links, life events, other names, groups,
    citations and "not them" marks go with them (ON DELETE CASCADE); tours that picked them leave them out, and
    "This is me" is cleared if it was them. Returns (their name, how many faces lost it). Commits."""
    row = con.execute("SELECT name FROM person WHERE id = ?", (pid,)).fetchone()
    if row is None:
        raise Problem("They were removed already.")
    if why := why_kept(con, pid):
        raise Problem(why)
    photos = [r[0] for r in con.execute("""SELECT image_id FROM face WHERE person_id = :p
                                           UNION SELECT image_id FROM image_person WHERE person_id = :p""", {"p": pid})]
    tours.forget(con, pid)
    con.execute("DELETE FROM settings WHERE key = 'me' AND value = ?", (str(pid),))
    unnamed = con.execute("""UPDATE face SET person_id = NULL, assigned_by = NULL, assign_score = NULL, assign_lead = NULL,
                                             cluster_id = NULL, named_by = NULL WHERE person_id = ?""", (pid,)).rowcount
    con.execute("DELETE FROM person WHERE id = ?", (pid,))  # face.person_id has no ON DELETE: the faces go first
    _reindex_images(con, photos)  # commits the removal first: their name is no longer found in those photos
    return row[0], unnamed


# ---- groups: people who aren't family, by what they are ------------------------------------------------------------

GROUP_SUGGESTIONS = ("Family friends", "Friends", "Kids' friends", "Teachers", "Coaches", "Classmates", "Coworkers",
                     "Neighbors", "Babysitters", "Band members")


class Problem(errors.Problem):
    """A change that cannot be made; the message is shown to the person who asked for it."""


def add_to_group(con: sqlite3.Connection, pid: int, group: str, added_by: int | None = None) -> str:
    """Put someone in a group (teachers, the kids' friends, a band seen at a concert). A group's name is written the
    way it was first written: 'teachers' joins 'Teachers'. added_by: the family member's account that did it (they
    go on seeing the people of their groups)."""
    group = group_name(con, group)
    if not group or len(group) > 60:
        raise Problem("Give the group a short name, like Teachers or Foo Fighters.")
    con.execute("INSERT OR IGNORE INTO person_group(person_id, name, added_by) VALUES (?, ?, ?)", (pid, group, added_by))
    return group


# Someone's own family shares a group of theirs: a family friend's wife and children are the family's friends too.
# Who counts: those family.relations_to finds under these headings (never the far ones, "wife's great-aunt", nor former
# family), and never the viewer's own family under them: a friend's son who married into the family is family.
FAMILY_OF = ("partner", "parents", "siblings", "children", "grandchildren", "grandparents", "aunts and uncles",
             "nieces and nephews", "cousins", "in-laws", "step-family")


def group_family(con: sqlite3.Connection, pid: int, group: str) -> list[dict]:
    """Someone's family (FAMILY_OF) not in a group yet, to ask whether they go in it too: [{"id", "name", "how"}],
    the closest first, "how" what they are to them ("wife", "son"). On a member's connection, only people they see."""
    g = family.load(con)
    if pid not in g.people:
        return []
    ego = family.me(con)
    ours = family.relations_to(g, ego) if ego is not None else {}
    already = {r[0] for r in con.execute("SELECT person_id FROM person_group WHERE lower(name) = lower(?)",
                                         (group_name(con, group),))}
    out = []
    for x, k in sorted(family.relations_to(g, pid).items(), key=lambda kv: (kv[1].distance, g.name(kv[0]).lower())):
        if x == pid or x == ego or k.group not in FAMILY_OF or x in already:
            continue
        if x in ours and ours[x].group in FAMILY_OF:
            continue
        out.append({"id": x, "name": g.name(x), "how": k.label})
    return out


def group_name(con: sqlite3.Connection, group: str) -> str:
    """A group's name as it was first written ('teachers' is 'Teachers'), else as given, its spaces tidied."""
    group = " ".join(group.split())
    same = con.execute("SELECT name FROM person_group WHERE lower(name) = lower(?) LIMIT 1", (group,)).fetchone()
    return same[0] if same else group


def remove_from_group(con: sqlite3.Connection, pid: int, group: str) -> None:
    con.execute("DELETE FROM person_group WHERE person_id = ? AND name = ?", (pid, group))


def groups(con: sqlite3.Connection) -> dict[str, list[int]]:
    """Group name -> its people, by name."""
    out: dict[str, list[int]] = {}
    for name, pid in con.execute("""SELECT g.name, g.person_id FROM person_group g JOIN person p ON p.id = g.person_id
                                    ORDER BY g.name COLLATE NOCASE, p.name COLLATE NOCASE"""):
        out.setdefault(name, []).append(pid)
    return out


def person_groups(con: sqlite3.Connection, pid: int) -> list[str]:
    return [r[0] for r in con.execute("SELECT name FROM person_group WHERE person_id = ? ORDER BY name COLLATE NOCASE",
                                      (pid,))]


def also_known(con: sqlite3.Connection) -> dict[int, str]:
    """person id -> their other names and groups, as one line of text (the People page's filter looks in it)."""
    out: dict[int, list[str]] = {}
    for pid, text in con.execute("SELECT person_id, name FROM person_name UNION ALL SELECT person_id, name FROM person_group"):
        out.setdefault(pid, []).append(text)
    return {pid: " · ".join(texts) for pid, texts in out.items()}


def set_aside(con: sqlite3.Connection, face_ids: list[int], reason: str = "rejected") -> None:
    """Unassign faces and keep them out of clustering and recognition (strangers, statues, wrong matches)."""
    con.executemany("""UPDATE face SET person_id = NULL, assigned_by = ?, assign_score = NULL, assign_lead = NULL, cluster_id = NULL
                       WHERE id = ?""", ((reason, f) for f in face_ids))
    _reindex_faces(con, face_ids)


def not_them(con: sqlite3.Connection, face_ids: list[int]) -> None:
    """Faces named as the wrong person: their name comes off and it is noted they are not that person, so they are
    neither suggested nor recognised as them again, and stay on their photo to be named as whoever they are."""
    con.execute("""INSERT OR IGNORE INTO face_not_person(face_id, person_id)
                   SELECT id, person_id FROM face WHERE id IN (SELECT value FROM json_each(?)) AND person_id IS NOT NULL""",
                (json.dumps(face_ids),))
    con.executemany("""UPDATE face SET person_id = NULL, assigned_by = NULL, assign_score = NULL, assign_lead = NULL, cluster_id = NULL
                       WHERE id = ?""", ((f,) for f in face_ids))
    _reindex_faces(con, face_ids)


def least_typical_first(con: sqlite3.Connection, pid: int) -> list[sqlite3.Row]:
    """The faces named by hand as this person, those least like the rest first, so a wrong one (a stranger named along
    with a whole group) is easy to spot. Each face is scored by its TOP_K closest other faces, so a childhood face is
    compared with the other childhood faces rather than with the adult ones."""
    rows = con.execute("SELECT id, image_id, embedding FROM face WHERE person_id = ? AND assigned_by = 'user' ORDER BY id",
                       (pid,)).fetchall()
    if len(rows) < 3:
        return rows
    X = _embeddings(rows)
    k = min(TOP_K, len(rows) - 1)
    fit = np.empty(len(rows), dtype=np.float32)
    for start in range(0, len(X), 1024):
        sims = X[start:start + 1024] @ X.T
        sims[np.arange(len(sims)), np.arange(start, start + len(sims))] = -1.0  # not with itself
        fit[start:start + 1024] = np.partition(sims, -k, axis=1)[:, -k:].mean(axis=1)
    return [rows[i] for i in np.argsort(fit, kind="stable")]


def _person_scores(con: sqlite3.Connection, faces: list[sqlite3.Row]) -> tuple[list[int], np.ndarray]:
    """How like each person named by hand every face is: (those people, a row of scores per face). A face's score for
    someone is the mean of its TOP_K closest faces of theirs, so a childhood face is compared with their childhood
    faces."""
    known = con.execute("SELECT person_id, embedding FROM face WHERE assigned_by = 'user'").fetchall()
    people = sorted({r["person_id"] for r in known})
    scores = np.full((len(faces), len(people)), -1.0, dtype=np.float32)
    if not known or not faces:
        return people, scores
    K, U = _embeddings(known), _embeddings(faces)
    column = {p: n for n, p in enumerate(people)}
    owner = np.array([column[r["person_id"]] for r in known])
    for start in range(0, len(U), 4096):
        sims = U[start:start + 4096] @ K.T
        for p in range(len(people)):
            s = sims[:, owner == p]
            k = min(TOP_K, s.shape[1])
            scores[start:start + 4096, p] = np.sort(s, axis=1)[:, -k:].mean(axis=1)
    return people, scores


def _named_in(con: sqlite3.Connection) -> defaultdict[int, set[int]]:
    """image id -> the people named there by face (by hand or recognised)."""
    out = defaultdict(set)
    for image_id, pid in con.execute("SELECT image_id, person_id FROM face WHERE person_id IS NOT NULL"):
        out[image_id].add(pid)
    return out


def _not_them(con: sqlite3.Connection) -> defaultdict[int, set[int]]:
    """face id -> the people it was said not to be."""
    out = defaultdict(set)
    for face_id, pid in con.execute("SELECT face_id, person_id FROM face_not_person"):
        out[face_id].add(pid)
    return out


def _births(con: sqlite3.Connection) -> dict[int, str]:
    return dict(con.execute("SELECT person_id, sort_date FROM event WHERE tag = 'BIRT' AND sort_date IS NOT NULL"))


def identify(con: sqlite3.Connection, face_ids: list[int] | None = None) -> int:
    """Label unassigned faces (all, or those given) that clearly match one named person. Returns the number
    labelled."""
    only = "AND f.id IN (SELECT value FROM json_each(:ids))" if face_ids is not None else ""
    unknown = con.execute(f"""SELECT f.id, f.image_id, f.embedding, i.taken_at, i.taken_at_source, i.date_to FROM face f
                              JOIN image i ON i.id = f.image_id
                              WHERE f.person_id IS NULL AND f.assigned_by IS NULL AND {usable_faces_sql()} AND {VISIBLE}
                                {only}""", {"ids": json.dumps(face_ids or [])}).fetchall()
    people, scores = _person_scores(con, unknown)
    if not people or not unknown:
        return 0
    order = np.argsort(-scores, axis=1)
    best, second = order[:, 0], order[:, 1] if len(people) > 1 else None
    rows = np.arange(len(unknown))
    best_score = scores[rows, best]
    margin = best_score - (scores[rows, second] if second is not None else -1.0)
    # one person appears at most once per photo, including people already named there; and never as someone the face
    # was said not to be
    taken, not_them, births = _named_in(con), _not_them(con), _births(con)
    labelled = []
    for i in np.argsort(-best_score):
        if best_score[i] < AUTO_SIMILARITY or margin[i] < AUTO_MARGIN:
            continue
        r, pid = unknown[i], people[best[i]]
        if pid in taken[r["image_id"]] or pid in not_them[r["id"]] or _before_birth(r, births.get(pid)):
            continue
        taken[r["image_id"]].add(pid)
        labelled.append((pid, float(best_score[i]), float(margin[i]), r["id"]))
    con.executemany("""UPDATE face SET person_id = ?, assigned_by = 'auto', assign_score = ?, assign_lead = ?,
                       cluster_id = NULL WHERE id = ?""", labelled)
    _reindex_faces(con, [f for *_, f in labelled])
    return len(labelled) + name_in_videos(con, _videos_of(con, face_ids) if face_ids is not None else None)


def name_in_videos(con: sqlite3.Connection, video_ids: list[int] | None = None) -> int:
    """Name the unnamed faces of videos (all, or these) that are plainly someone named on another of the video's frames
    (IN_VIDEO_SIMILARITY). Returns how many were named."""
    if video_ids is not None and not video_ids:
        return 0
    not_them, births, labelled = _not_them(con), _births(con), []
    for faces in _video_faces(con, video_ids).values():
        for f in faces:
            if f["person_id"] is None and (found := _in_video(faces, f, not_them, IN_VIDEO_SIMILARITY)):
                if not _before_birth(f, births.get(found[0])):
                    labelled.append((found[0], found[1], f["id"]))
    con.executemany("""UPDATE face SET person_id = ?, assigned_by = 'auto', assign_score = ?, cluster_id = NULL
                       WHERE id = ? AND person_id IS NULL AND assigned_by IS NULL""", labelled)
    _reindex_faces(con, [f for _, _, f in labelled])
    return len(labelled)


def measure_recognised(con: sqlite3.Connection) -> int:
    """Score each face recognised automatically again, against the faces named by hand now, with its lead over the next
    likeliest person who can be it (not named in its photo already, not said not to be it), so how sure it is
    (how_sure) follows what was named since: one that now looks more like someone else is asked about first. Only when
    the faces named by hand changed since the last time, or some recognised faces have no lead yet (one named after the
    same person on a video's other frames). Returns how many were scored."""
    mark = list(con.execute("""SELECT count(*), coalesce(max(id), 0), total(person_id) FROM face
                               WHERE assigned_by = 'user'""").fetchone())
    if settings.get(con, MEASURED_KEY) == mark and not con.execute(
            "SELECT 1 FROM face WHERE assigned_by = 'auto' AND assign_lead IS NULL LIMIT 1").fetchone():
        return 0
    faces = con.execute("SELECT id, image_id, person_id, embedding FROM face WHERE assigned_by = 'auto'").fetchall()
    who, scores = _person_scores(con, faces)
    column = {p: n for n, p in enumerate(who)}
    named_in, not_them = _named_in(con), _not_them(con)
    out = []
    for f, s in zip(faces, scores):
        own = column.get(f["person_id"])
        if own is None:  # none of their faces is named by hand any more: nothing to be sure of
            out.append((-1.0, -1.0, f["id"]))
            continue
        others = s.copy()
        others[own] = -1.0
        for pid in (named_in[f["image_id"]] | not_them[f["id"]]) - {f["person_id"]}:
            if pid in column:
                others[column[pid]] = -1.0
        out.append((float(s[own]), float(s[own] - others.max()) if len(who) > 1 else 1.0, f["id"]))
    con.executemany("UPDATE face SET assign_score = ?, assign_lead = ? WHERE id = ? AND assigned_by = 'auto'", out)
    settings.put(con, MEASURED_KEY, mark)
    con.commit()
    return len(out)


def _videos_of(con: sqlite3.Connection, face_ids: list[int]) -> list[int]:
    return [r[0] for r in con.execute("""SELECT DISTINCT image_id FROM face WHERE frame IS NOT NULL
                                         AND id IN (SELECT value FROM json_each(?))""", (json.dumps(face_ids),))]


def _video_faces(con: sqlite3.Connection, video_ids: list[int] | None) -> dict[int, list[sqlite3.Row]]:
    """video -> its faces good enough to name, named (by hand or recognised) or not named yet, of videos where someone
    is named."""
    if video_ids is not None and not video_ids:
        return {}
    only = "AND f.image_id IN (SELECT value FROM json_each(:ids))" if video_ids is not None else ""
    out = defaultdict(list)
    for r in con.execute(f"""
            SELECT f.id, f.image_id, f.person_id, f.embedding, f.frame, i.taken_at, i.taken_at_source, i.date_to
            FROM face f JOIN image i ON i.id = f.image_id
            WHERE f.frame IS NOT NULL AND i.media = 'video' AND {usable_faces_sql()} AND {VISIBLE} {only}
              AND (f.person_id IS NULL AND f.assigned_by IS NULL OR f.assigned_by IN ('user', 'auto'))
              AND EXISTS (SELECT 1 FROM face n WHERE n.image_id = f.image_id AND n.assigned_by IN ('user', 'auto'))""",
                         {"ids": json.dumps(video_ids or [])}):
        out[r["image_id"]].append(r)
    return out


def _in_video(faces: list[sqlite3.Row], f: sqlite3.Row, not_them, least: float) -> tuple[int, float] | None:
    """(person, score) when an unnamed face of a video is most like someone named on another of its frames, they score
    `least` and beat the next such person by IN_VIDEO_MARGIN; else None. Nobody named on its own frame can be it, nor
    anyone it was said not to be."""
    e = np.frombuffer(f["embedding"], np.float32)
    beside = {n["person_id"] for n in faces if n["person_id"] is not None and n["frame"] == f["frame"]}
    best = {}
    for n in faces:
        pid = n["person_id"]
        if pid is not None and n["frame"] != f["frame"] and pid not in beside and pid not in not_them[f["id"]]:
            best[pid] = max(best.get(pid, -1.0), float(e @ np.frombuffer(n["embedding"], np.float32)))
    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    if not ranked or ranked[0][1] < least or ranked[0][1] - (ranked[1][1] if len(ranked) > 1 else 0) < IN_VIDEO_MARGIN:
        return None
    return ranked[0]


def to_finish(con: sqlite3.Connection, offset: int = 0, limit: int = 25) -> tuple[list[dict], int]:
    """Photos and videos with two or more people named and faces still unnamed (Cleanup → Confirm faces), the most
    people named first: each with who is named in it, and its unnamed faces with who each most likely is (in a photo,
    the likeliest person not named in it yet; in a video, the likeliest named on its other frames). Returns (the page,
    how many there are). On a member's connection, only people they see are suggested, and those named in a photo
    whom they don't see come with the name None."""
    rows = con.execute(f"""
        WITH named AS (SELECT image_id, person_id FROM face WHERE person_id IS NOT NULL AND assigned_by IN ('user', 'auto')
                       UNION SELECT image_id, person_id FROM image_person)
        SELECT i.id, i.sha256, i.media, i.taken_at, count(DISTINCT n.person_id) AS named
        FROM image i JOIN named n ON n.image_id = i.id
        WHERE {VISIBLE} AND EXISTS (SELECT 1 FROM face f WHERE f.image_id = i.id AND f.person_id IS NULL
                                    AND f.assigned_by IS NULL AND {usable_faces_sql()})
        GROUP BY i.id HAVING named >= 2 ORDER BY named DESC, i.taken_at DESC, i.id""").fetchall()
    shown = rows[offset:offset + limit]
    ids = json.dumps([r["id"] for r in shown])
    names = dict(con.execute("SELECT id, name FROM person"))
    named = defaultdict(set)
    for image_id, pid in con.execute("""
            SELECT image_id, person_id FROM face WHERE person_id IS NOT NULL AND assigned_by IN ('user', 'auto')
              AND image_id IN (SELECT value FROM json_each(:ids))
            UNION SELECT image_id, person_id FROM image_person WHERE image_id IN (SELECT value FROM json_each(:ids))""",
                                     {"ids": ids}):
        named[image_id].add(pid)
    unnamed = con.execute(f"""SELECT f.id, f.image_id, f.embedding, f.frame, i.taken_at, i.taken_at_source, i.date_to
                              FROM face f JOIN image i ON i.id = f.image_id
                              WHERE f.image_id IN (SELECT value FROM json_each(?)) AND f.person_id IS NULL
                                AND f.assigned_by IS NULL AND {usable_faces_sql()} ORDER BY f.frame, f.x1""",
                          (ids,)).fetchall()
    guess = {f["id"]: pid for (_, pid), (_, f, _) in
             _likeliest(con, [f for f in unnamed if f["frame"] is None], _named_in(con)).items()}
    videos, not_them = _video_faces(con, [r["id"] for r in shown if r["media"] == "video"]), _not_them(con)
    for f in unnamed:
        if f["frame"] is not None and (found := _in_video(videos.get(f["image_id"], []), f, not_them,
                                                          IN_VIDEO_SUGGEST)):
            guess[f["id"]] = found[0]
    out = []
    for r in shown:
        faces = [{"id": f["id"], "suggest": (guess[f["id"]], names[guess[f["id"]]])
                  if guess.get(f["id"]) in names else None} for f in unnamed if f["image_id"] == r["id"]]
        out.append({"id": r["id"], "sha": r["sha256"], "media": r["media"], "taken_at": r["taken_at"], "faces": faces,
                    "named": sorted(((p, names.get(p)) for p in named[r["id"]]), key=lambda pn: pn[1] or "")})
    return out, len(rows)


# Unnamed faces good enough to name (f, joined to its image i) in photos where someone is named by face
_BESIDE_NAMED = f"""f.person_id IS NULL AND f.assigned_by IS NULL AND {usable_faces_sql()} AND {VISIBLE}
                    AND EXISTS (SELECT 1 FROM face n WHERE n.image_id = f.image_id AND n.person_id IS NOT NULL)"""


def beside_named(con: sqlite3.Connection) -> int:
    """How many unnamed faces are in photos where someone is named, for which together() looks for who they are."""
    sql = f"SELECT count(*) FROM face f JOIN image i ON i.id = f.image_id WHERE {_BESIDE_NAMED}"
    return con.execute(sql).fetchone()[0]


def together(con: sqlite3.Connection) -> list[dict]:
    """Unnamed faces in photos where someone is already named, by who they most likely are (see _likeliest). Each
    group has the person, their faces (the likeliest first) and who else is in those photos as (person id, photos), the
    largest group first. On a member's connection, only people they see are in it, in the groups and beside them."""
    faces = con.execute(f"""SELECT f.id, f.image_id, f.embedding, i.taken_at, i.taken_at_source, i.date_to FROM face f
                            JOIN image i ON i.id = f.image_id WHERE {_BESIDE_NAMED}""").fetchall()
    named_in = _named_in(con)
    shown = {pid for (pid,) in con.execute("SELECT id FROM person")}
    groups = {}
    for (image_id, pid), (score, f, lead) in _likeliest(con, faces, named_in).items():
        if pid not in shown:
            continue
        g = groups.setdefault(pid, {"person_id": pid, "faces": [], "seen_with": Counter()})
        g["faces"].append({"id": f["id"], "image_id": image_id, "score": score, "sure": how_sure(score, lead)})
        g["seen_with"].update(named_in[image_id] & shown)
    for g in groups.values():
        g["faces"].sort(key=lambda f: -f["score"])
        g["seen_with"] = g["seen_with"].most_common()
    return sorted(groups.values(), key=lambda g: (-len(g["faces"]), g["person_id"]))


def suggest_for_photo(con: sqlite3.Connection, image_id: int) -> dict[int, tuple[int, float, float]]:
    """face id -> (person id, score, how sure) for the unnamed faces of a photo that look enough like someone not named in it yet
    (see _likeliest), for its page. Any face clear enough to compare counts, even a small one: the person looking at
    the photo decides."""
    faces = con.execute(f"""SELECT f.id, f.image_id, f.embedding, i.taken_at, i.taken_at_source, i.date_to FROM face f
                            JOIN image i ON i.id = f.image_id
                            WHERE f.image_id = ? AND f.person_id IS NULL AND f.quality >= {MIN_QUALITY}
                              AND (f.assigned_by IS NULL OR f.assigned_by = 'rejected')""", (image_id,)).fetchall()
    if not faces:
        return {}
    named_in = defaultdict(set, {image_id: {pid for (pid,) in con.execute(
        "SELECT person_id FROM face WHERE image_id = ? AND person_id IS NOT NULL", (image_id,))}})
    return {f["id"]: (pid, score, how_sure(score, lead))
            for (_, pid), (score, f, lead) in _likeliest(con, faces, named_in).items()}


def _likeliest(con: sqlite3.Connection, faces: list[sqlite3.Row],
               named_in: defaultdict[int, set[int]]) -> dict[tuple[int, int], tuple[float, sqlite3.Row, float]]:
    """Who each face most likely is: the closest person not named in its photo yet (nobody is in a photo twice), not
    born after it and not said to be someone else, when they score SUGGEST_SIMILARITY and beat the next such person by
    SUGGEST_MARGIN. (photo, person) -> (score, face, its lead over the next); of two faces in one photo, only the
    likelier."""
    people, scores = _person_scores(con, faces)
    not_them, births = _not_them(con), _births(con)
    likeliest = {}
    for f, s in zip(faces, scores):
        found = []  # the closest two who can be this face
        for p in np.argsort(-s):
            pid = people[p]
            if pid in named_in[f["image_id"]] or pid in not_them[f["id"]] or _before_birth(f, births.get(pid)):
                continue
            found.append(p)
            if len(found) == 2:
                break
        if not found:
            continue
        score, runner_up = float(s[found[0]]), float(s[found[1]]) if len(found) > 1 else -1.0
        key = (f["image_id"], people[found[0]])
        if score < SUGGEST_SIMILARITY or score - runner_up < SUGGEST_MARGIN:
            continue
        if score > likeliest.get(key, (-1.0,))[0]:
            likeliest[key] = (score, f, score - runner_up)
    return likeliest


def confirm(con: sqlite3.Connection, pid: int, yes: list[int], no: list[int], by: int | None = None) -> int:
    """Faces suggested as someone (People → Seen together): those ticked are named as them, as if by hand; those
    unticked are noted as not them, so they are neither suggested nor recognised as them again and stay free to be
    named as anyone else. Faces named meanwhile keep their name. Returns how many were named."""
    named = con.executemany("""UPDATE face SET person_id = ?, assigned_by = 'user', assign_score = NULL, assign_lead = NULL,
                                              cluster_id = NULL, named_by = ?
                               WHERE id = ? AND person_id IS NULL""", ((pid, by, f) for f in yes)).rowcount
    con.executemany("INSERT OR IGNORE INTO face_not_person(face_id, person_id) SELECT id, ? FROM face WHERE id = ?",
                    ((pid, f) for f in no))
    _reindex_faces(con, yes)
    return named + name_in_videos(con, _videos_of(con, yes))


# ---- one face at a time (People → Name faces, Check faces, a face tapped on its photo) -----------------------------

def is_them(con: sqlite3.Connection, pid: int, face_ids: list[int], by: int | None = None) -> list[int]:
    """Yes to "Is this them?": the faces are named as them by hand (name_as), whether they were unnamed, recognised
    automatically (as them, or wrongly as someone else) or set aside as a stranger. A face someone named by hand as
    someone else, or said isn't a face, keeps that: an answer from a page opened before never undoes it (Fix, on the
    photo, renames it); one named by hand as them already stays as it is. Returns the faces that are them now."""
    rows = con.execute("SELECT id, person_id, assigned_by FROM face WHERE id IN (SELECT value FROM json_each(?))",
                       (json.dumps(face_ids),)).fetchall()
    change = [r["id"] for r in rows if r["assigned_by"] in (None, "auto", "rejected")]
    if change:
        name_as(con, change, pid, by)
    return change + [r["id"] for r in rows if r["assigned_by"] == "user" and r["person_id"] == pid]


def not_this_person(con: sqlite3.Connection, pid: int, face_ids: list[int]) -> list[int]:
    """No to "Is this them?": noted, so the faces are neither suggested nor recognised as them again, and the next
    likeliest person can be suggested instead. One named as them, recognised or by hand, loses the name and waits to
    be named as whoever it is; one named as someone else keeps that name. Returns the faces that lost the name."""
    ids = json.dumps(face_ids)
    con.execute("""INSERT OR IGNORE INTO face_not_person(face_id, person_id)
                   SELECT id, ? FROM face WHERE id IN (SELECT value FROM json_each(?))""", (pid, ids))
    theirs = [r[0] for r in con.execute("""SELECT id FROM face WHERE id IN (SELECT value FROM json_each(?))
                                           AND person_id = ?""", (ids, pid))]
    con.executemany("""UPDATE face SET person_id = NULL, assigned_by = NULL, assign_score = NULL, assign_lead = NULL, cluster_id = NULL,
                                       named_by = NULL WHERE id = ?""", ((f,) for f in theirs))
    _reindex_faces(con, theirs)
    return theirs


def suggest_for_face(con: sqlite3.Connection, face_id: int) -> tuple[int, float, float] | None:
    """Who one unnamed face most likely is, worked out as for its photo's page (suggest_for_photo): (person, score,
    how sure), or None when nobody is close enough, or clearly ahead of the next."""
    row = con.execute("SELECT image_id FROM face WHERE id = ?", (face_id,)).fetchone()
    return suggest_for_photo(con, row[0]).get(face_id) if row else None


def recognised_to_check(con: sqlite3.Connection) -> list[tuple[int, int]]:
    """Faces recognised automatically that nobody has said yes or no to, as (face, person), the least sure first
    (how_sure): the likeliest to be wrong, and those that teach recognition the most once confirmed (it learns only
    from faces named by hand). On a member's connection, those in photos they see, of people they see."""
    return [(r[0], r[1]) for r in con.execute(f"""
        SELECT f.id, f.person_id FROM face f JOIN image i ON i.id = f.image_id
        WHERE f.assigned_by = 'auto' AND {VISIBLE} AND f.person_id IN (SELECT id FROM person)
        ORDER BY {_LEAST_SURE_FIRST}""")]


def recognised_sure(con: sqlite3.Connection, bar: float) -> dict[int, list[int]]:
    """Of the faces recognised_to_check lists, those at least bar sure (how_sure), by person."""
    out = defaultdict(list)
    for fid, pid in con.execute(f"""SELECT f.id, f.person_id FROM face f JOIN image i ON i.id = f.image_id
                                    WHERE f.assigned_by = 'auto' AND {VISIBLE} AND f.person_id IN (SELECT id FROM person)
                                      AND {_sure_sql(bar)} ORDER BY f.id"""):
        out[pid].append(fid)
    return dict(out)


def least_sure_first(faces) -> list[dict]:
    """Recognised faces (rows with assign_score and assign_lead), each with how sure it is ("sure"), in the order
    recognised_to_check gives them."""
    lead = lambda f: f["assign_lead"] if f["assign_lead"] is not None else AUTO_MARGIN
    out = [{**dict(f), "sure": how_sure(f["assign_score"], f["assign_lead"])} for f in faces]
    return sorted(out, key=lambda f: (how_sure(f["assign_score"] or 0, lead(f)),
                                      SURE_SCORE * (f["assign_score"] or 0) + SURE_LEAD * lead(f), f["id"]))


def unnamed_groups(con: sqlite3.Connection) -> list[int]:
    """The groups of faces nobody has named (cluster), the largest first."""
    return [r[0] for r in con.execute("""SELECT cluster_id FROM face WHERE cluster_id IS NOT NULL AND person_id IS NULL
                                         GROUP BY cluster_id ORDER BY count(*) DESC, cluster_id""")]


def group_faces(con: sqlite3.Connection, cluster_id: int) -> list[sqlite3.Row]:
    """A group's unnamed faces (id, image_id), the clearest first."""
    return con.execute("""SELECT id, image_id FROM face WHERE cluster_id = ? AND person_id IS NULL
                          ORDER BY quality DESC, id""", (cluster_id,)).fetchall()


# "Looks like …" on a group nobody has named (Cleanup → Confirm faces → Unnamed groups, People → Name faces). Its faces
# fell short of recognition one by one, but together they may still plainly be someone. Measured on that library by
# taking each person's hand-named faces of one day as a group (1,299 groups) against everyone's faces of other days:
# the person its faces scored best for on average was right 97.7% of the time. Scoring GROUP_SIMILARITY and
# GROUP_MARGIN ahead of the next, 92% of the groups had a name and 99.9% of those were right; of the 369 groups most of
# whose faces recognition would not have named alone, 71% had one and 99.6% were right, and the young brothers'
# groups, which recognition can't tell apart face by face, were all right. Faces set aside as strangers, grouped the
# same way, were taken for someone in 36 of 554 groups, so it is a suggestion that a person says yes to.
GROUP_SIMILARITY = 0.4
GROUP_MARGIN = 0.05
GROUP_FACES = 12  # a group's clearest faces, compared


def looks_like(con: sqlite3.Connection, cluster_ids) -> dict[int, tuple[int, float, int]]:
    """cluster -> (person, score, how many of its faces are in photos taken before they were born) for unnamed groups
    that, their faces taken together, look like someone named by hand (see GROUP_SIMILARITY). Someone counts for a face
    only if they can be it, as for one face (_likeliest): not named in its photo already, not said not to be it, not
    born after it; and for a group only if they can be at least half of its faces. Worked out against everyone: show a
    member only the people they see."""
    if not cluster_ids:
        return {}
    rows = con.execute("""SELECT f.id, f.image_id, f.cluster_id, f.embedding, i.taken_at, i.taken_at_source, i.date_to
                          FROM face f JOIN image i ON i.id = f.image_id
                          WHERE f.cluster_id IN (SELECT value FROM json_each(?)) AND f.person_id IS NULL
                          ORDER BY f.cluster_id, f.quality DESC, f.id""", (json.dumps(list(cluster_ids)),)).fetchall()
    groups = defaultdict(list)
    for r in rows:
        if len(groups[r["cluster_id"]]) < GROUP_FACES:
            groups[r["cluster_id"]].append(r)
    faces = [f for g in groups.values() for f in g]
    people, scores = _person_scores(con, faces)
    if len(people) < 1 or not faces:
        return {}
    column = {p: n for n, p in enumerate(people)}
    named_in, not_them, births = _named_in(con), _not_them(con), _births(con)
    able = np.ones(scores.shape, dtype=bool)
    unborn = np.zeros(scores.shape, dtype=bool)
    for n, f in enumerate(faces):
        before = {p for p, b in births.items() if p in column and _before_birth(f, b)}
        for pid in before:
            unborn[n, column[pid]] = True
        for pid in named_in[f["image_id"]] | not_them[f["id"]] | before:
            if pid in column:
                able[n, column[pid]] = False
    out, start = {}, 0
    for cid, group in groups.items():
        s, ok = scores[start:start + len(group)], able[start:start + len(group)]
        start += len(group)
        counts = ok.sum(axis=0)
        mean = np.where(counts * 2 >= len(group), (s * ok).sum(axis=0) / np.maximum(counts, 1), -1.0)
        order = np.argsort(-mean)
        best = float(mean[order[0]])
        runner_up = float(mean[order[1]]) if len(order) > 1 else -1.0
        if best >= GROUP_SIMILARITY and best - runner_up >= GROUP_MARGIN:
            out[cid] = (people[order[0]], best, int(unborn[start - len(group):start, order[0]].sum()))
    return out


def _before_birth(face, born: str | None) -> bool:
    """Whether the photo was taken before the person was born (by its camera date, or by the last day the date set by
    hand allows): then it isn't them."""
    if not born:
        return False
    if face["taken_at_source"] == "user":
        latest = face["date_to"]
    elif face["taken_at_source"] in ("exif", "camera", "filename", "copy"):
        latest = face["taken_at"]
    else:
        return False
    return bool(latest) and latest[:len(born)] < born


def _reindex_faces(con: sqlite3.Connection, face_ids: list[int]) -> None:
    con.commit()  # the names first
    if not face_ids:
        return
    image_ids = set()
    for start in range(0, len(face_ids), 900):  # stay under SQLite's bound-parameter limit
        chunk = face_ids[start:start + 900]
        image_ids |= {r[0] for r in con.execute(
            f"SELECT image_id FROM face WHERE id IN ({','.join('?' * len(chunk))})", chunk)}
    _reindex_images(con, image_ids)


def _added_to(con: sqlite3.Connection, pid: int) -> list[int]:
    """The photos someone was added to by hand (their face wasn't found): search finds those by their name too."""
    return [r[0] for r in con.execute("SELECT image_id FROM image_person WHERE person_id = ?", (pid,))]


def _reindex_images(con: sqlite3.Connection, image_ids) -> None:
    """The search index of photos whose names changed, after committing that change: a few photos per write, so
    nobody waits on it."""
    con.commit()
    folders = FolderIndex(con)
    for n, image_id in enumerate(image_ids, 1):
        search.index_image(con, image_id, folders)
        if n % search.COMMIT_EVERY == 0:
            con.commit()
    con.commit()


# ---- the same person, added twice --------------------------------------------------------------------------------

SAME_OK_KEY = "same_person_ok"  # settings: pairs ("12-40") an admin said are different people


def maybe_same(con: sqlite3.Connection, different: set[str] = frozenset()) -> list[dict]:
    """People family members added who may already be in the catalog (Cleanup → Same person?): the same name, one's
    other name, or the same first and last name with a middle name left out; with their birth years and how alike
    their hand-named faces are. A member who can't see someone types their name as someone new, so these are
    likely."""
    persons = {r["id"]: r for r in con.execute("SELECT id, name, added_by FROM person")}

    def keys(name: str):
        words = name.lower().split()
        yield " ".join(words)
        if len(words) > 2:
            yield f"{words[0]} {words[-1]}"

    by_key = defaultdict(set)
    for pid, r in persons.items():
        for k in keys(r["name"]):
            by_key[k].add(pid)
    for pid, name in con.execute("SELECT person_id, name FROM person_name"):
        for k in keys(name):
            by_key[k].add(pid)
    pairs = set()
    for ids in by_key.values():
        for a in ids:
            for b in ids:
                if a != b and persons[a]["added_by"] and (not persons[b]["added_by"] or a > b):
                    if f"{min(a, b)}-{max(a, b)}" not in different:
                        pairs.add((a, b))
    births = {pid: when[:4] for pid, when in con.execute(
        "SELECT person_id, sort_date FROM event WHERE tag = 'BIRT' AND sort_date IS NOT NULL")}
    faces = _hand_named(con)
    by_name = {frozenset(pair) for pair in pairs}
    found = [("name", a, b, _alike(faces, a, b) if a in faces and b in faces else None) for a, b in pairs]
    found += [("face", small, big, alike) for small, big, alike in same_by_face(con, faces)
              if frozenset((small, big)) not in by_name and f"{min(small, big)}-{max(small, big)}" not in different]
    out = []
    for why, a, b, alike in found:
        out.append({"added": a, "other": b, "added_name": persons[a]["name"], "other_name": persons[b]["name"],
                    "added_by": persons[a]["added_by"], "born": (births.get(a), births.get(b)), "alike": alike,
                    "look_alike": alike is not None and alike >= DUPLICATE_SIMILARITY, "why": why})
    return sorted(out, key=lambda m: (m["why"] != "name", -(m["alike"] or 0), m["added_name"].lower()))


# The same person named twice, found by their faces (Cleanup → Same person?), whoever added them. Two people are
# compared from the one with fewer faces named by hand: for each of their faces, its 3 closest of the other's, the
# median of that over their faces. Measured on that library: one person's faces of one day against their other faces
# (as if named again on that day) scored 0.4 or more 94% of the time, while of 5,836 pairs of different people never
# in one photo together, the closest scored 0.35. Two people in one photo, or related in the family tree, are two
# people and never compared. Comparing everyone's every face would take too long for a page, so a first look compares
# DUPLICATE_SAMPLE faces of each, spread over the years, and only pairs scoring DUPLICATE_FIRST_LOOK (which kept 99.2%
# of those found) are compared face by face.
DUPLICATE_SIMILARITY = 0.4
DUPLICATE_FIRST_LOOK = 0.2
DUPLICATE_SAMPLE = 12


def _hand_named(con: sqlite3.Connection) -> dict[int, np.ndarray]:
    """person -> their faces named by hand (embeddings), oldest photo first."""
    rows = con.execute("""SELECT f.person_id, f.embedding FROM face f JOIN image i ON i.id = f.image_id
                          WHERE f.assigned_by = 'user' ORDER BY f.person_id, i.taken_at, f.id""").fetchall()
    out = defaultdict(list)
    for r in rows:
        out[r["person_id"]].append(r)
    return {pid: _embeddings(rs) for pid, rs in out.items()}


def _alike(faces: dict[int, np.ndarray], a: int, b: int) -> float:
    """How alike two people's faces are (see DUPLICATE_SIMILARITY), from the one with fewer faces."""
    small, big = (faces[a], faces[b]) if len(faces[a]) <= len(faces[b]) else (faces[b], faces[a])
    sims = small @ big.T
    k = min(TOP_K, sims.shape[1])
    return float(np.median(np.sort(sims, axis=1)[:, -k:].mean(axis=1)))


def same_by_face(con: sqlite3.Connection, faces: dict[int, np.ndarray] | None = None) -> list[tuple[int, int, float]]:
    """Pairs of people whose faces named by hand look like one person (DUPLICATE_SIMILARITY), as (the one with fewer
    faces, the other, how alike), the most alike first. Two people seen in one photo, or related in the family tree,
    are left out."""
    faces = {p: f for p, f in (faces if faces is not None else _hand_named(con)).items() if len(f) >= 2}
    if len(faces) < 2:
        return []
    together = set()
    for (image_id,), people_there in _groupby(con.execute(
            """SELECT image_id, person_id FROM face WHERE person_id IS NOT NULL
               UNION SELECT image_id, person_id FROM image_person ORDER BY 1""")):
        for a in people_there:
            for b in people_there:
                if a < b:
                    together.add((a, b))
    g = family.load(con)
    pids = sorted(faces)
    # the first look: DUPLICATE_SAMPLE faces of each, spread over their photos, all at once
    spread = {p: faces[p][np.unique(np.linspace(0, len(faces[p]) - 1, DUPLICATE_SAMPLE).round().astype(int))]
              for p in pids}
    have = np.array([len(spread[p]) for p in pids])
    sample = np.zeros((len(pids), DUPLICATE_SAMPLE, faces[pids[0]].shape[1]), dtype=np.float32)
    for n, p in enumerate(pids):
        sample[n, :have[n]] = spread[p]
    k, flat = np.minimum(TOP_K, have), sample.reshape(-1, sample.shape[2])
    found = []
    for n, a in enumerate(pids):
        sims = (spread[a] @ flat.T).reshape(len(spread[a]), len(pids), DUPLICATE_SAMPLE)  # a's x everyone's
        sims[:, np.arange(DUPLICATE_SAMPLE)[None, :] >= have[:, None]] = -2.0  # rows past their faces
        top = -np.sort(-sims, axis=2).cumsum(axis=2)[:, np.arange(len(pids)), k - 1] / k
        first_look = np.median(top, axis=0)
        for m in np.nonzero(first_look >= DUPLICATE_FIRST_LOOK)[0]:
            b = pids[m]
            small, big = (a, b) if (len(faces[a]), a) < (len(faces[b]), b) else (b, a)
            if b == a or small != a or (min(a, b), max(a, b)) in together:
                continue
            if g.blood(a, b) is not None or b in g.partners.get(a, {}):
                continue
            if (alike := _alike(faces, a, b)) >= DUPLICATE_SIMILARITY:
                found.append((a, b, alike))
    return sorted(found, key=lambda x: -x[2])


def _groupby(rows):
    """(key,), [values] for rows of (key, value) ordered by key."""
    from itertools import groupby
    for key, items in groupby(rows, key=lambda r: r[0]):
        yield (key,), {r[1] for r in items}
