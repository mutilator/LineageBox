"""Faces -> people: cluster unnamed faces, name clusters, and recognise more faces of named people."""
import json
import sqlite3
from collections import Counter, defaultdict

import numpy as np
from sklearn.cluster import HDBSCAN
from sklearn.decomposition import PCA

from lineagebox import errors, events, family, search, tours
from lineagebox.catalog import VISIBLE
from lineagebox.faces import MIN_QUALITY, usable_faces_sql
from lineagebox.folders import FolderIndex

# Recognition: a face is labelled automatically only when its best person clearly beats the runner-up.
# Measured on the library LineageBox was developed on: the same adult across decades scores 0.6-0.75; two young
# brothers score ~0.5.
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


def _embeddings(rows) -> np.ndarray:
    return np.frombuffer(b"".join(r["embedding"] for r in rows), dtype=np.float32).reshape(len(rows), -1)


def cluster(con: sqlite3.Connection) -> tuple[int, int]:
    """Re-cluster faces that nobody has named or set aside. Returns (clusters, faces in clusters)."""
    rows = con.execute(f"""SELECT f.id, f.embedding FROM face f JOIN image i ON i.id = f.image_id
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
    # Written only once the (minute-long) clustering is done, so the database is not locked meanwhile. A face named
    # in the web app during that minute keeps its name and stays out of the groups.
    con.execute("UPDATE face SET cluster_id = NULL WHERE person_id IS NULL")
    con.executemany("UPDATE face SET cluster_id = ? WHERE id = ? AND person_id IS NULL AND assigned_by IS NULL",
                    ((int(label), r["id"]) for r, label in zip(rows, labels) if label >= 0))
    con.commit()
    clustered = int((labels >= 0).sum())
    return len(set(labels[labels >= 0].tolist())), clustered


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
    """Name faces by hand (see person_id for name, pid and among); by is the account naming them, if any. A face that
    had someone else's name (put right with Fix on its photo) is noted as not them, so it is never suggested or
    recognised as them again; one noted as not this person before is them after all."""
    pid = person_id(con, name, pid, among, by)
    ids = json.dumps(face_ids)
    con.execute("""INSERT OR IGNORE INTO face_not_person(face_id, person_id)
                   SELECT id, person_id FROM face WHERE id IN (SELECT value FROM json_each(?))
                     AND person_id IS NOT NULL AND person_id != ?""", (ids, pid))
    con.execute("DELETE FROM face_not_person WHERE face_id IN (SELECT value FROM json_each(?)) AND person_id = ?",
                (ids, pid))
    con.executemany("""UPDATE face SET person_id = ?, assigned_by = 'user', assign_score = NULL, cluster_id = NULL,
                                       named_by = ? WHERE id = ?""", ((pid, by, f) for f in face_ids))
    _reindex_faces(con, face_ids)
    name_in_videos(con, _videos_of(con, face_ids))
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
    others = [r[0] for r in con.execute("SELECT id FROM person WHERE lower(name) = lower(?) AND id != ?",
                                        (new_name, pid))]
    # but a relative with the same name (John Smith and his grandson John Smith) is someone else
    if merge_same and len(others) == 1 and family.load(con).blood(pid, others[0]) is None:
        return merge(con, pid, others[0])
    given, surname = events.split_name(new_name)  # the parts follow, to be checked again
    con.execute("UPDATE person SET name = ?, given_name = ?, surname = ?, names_checked = 0 WHERE id = ?",
                (new_name, given, surname, pid))  # kept in place, so tours still find them
    _reindex_faces(con, [r[0] for r in con.execute("SELECT id FROM face WHERE person_id = ?", (pid,))])
    return pid


def merge(con: sqlite3.Connection, pid: int, into: int) -> int:
    """One person named twice ("jak" and "Jake"): pid's faces, photos, details, family and tours go to into, and pid
    is removed. Details only into left blank are taken from pid. Returns into."""
    if pid == into:
        return into
    moved = [r[0] for r in con.execute("SELECT id FROM face WHERE person_id = ?", (pid,))]
    con.execute("UPDATE face SET person_id = ? WHERE person_id = ?", (into, pid))
    con.execute("""UPDATE person SET sex = coalesce(sex, (SELECT sex FROM person WHERE id = :old)),
                                     notes = coalesce(notes, (SELECT notes FROM person WHERE id = :old))
                   WHERE id = :new""", {"old": pid, "new": into})
    events.move(con, pid, into)  # births, deaths and the like; also person.birth_date and deceased
    # A group a member put pid in no longer counts as theirs: whether they go on seeing into is the admin's choice
    # (merge_same's keep_seeing), not a side effect of the merge.
    con.execute("UPDATE person_group SET added_by = NULL WHERE person_id = ?", (pid,))
    # Every other row that refers to a person comes along: deleting pid would drop it (ON DELETE CASCADE) or unlink it
    # (an account's person, ON DELETE SET NULL). family.merge and tours.merge do the family links and tours.
    for table in ("person_name", "person_group", "image_person", "face_not_person", "external_id", "citation",
                  "access_rule", "account"):
        con.execute(f"UPDATE OR IGNORE {table} SET person_id = ? WHERE person_id = ?", (into, pid))
    con.execute("DELETE FROM image_person WHERE person_id = ?", (pid,))  # photos into was already added to
    try:
        family.merge(con, pid, into)
    except family.Problem as e:  # nothing is saved: the caller's transaction is not committed
        raise Problem(str(e)) from None
    tours.merge(con, pid, into)
    con.execute("DELETE FROM person WHERE id = ?", (pid,))
    _reindex_faces(con, moved)
    return into


# ---- groups: people who aren't family, by what they are ------------------------------------------------------------

GROUP_SUGGESTIONS = ("Family friends", "Friends", "Kids' friends", "Teachers", "Coaches", "Classmates", "Coworkers",
                     "Neighbors", "Babysitters", "Band members")


class Problem(errors.Problem):
    """A change that cannot be made; the message is shown to the person who asked for it."""


def add_to_group(con: sqlite3.Connection, pid: int, group: str, added_by: int | None = None) -> str:
    """Put someone in a group (teachers, the kids' friends, a band seen at a concert). A group's name is written the
    way it was first written: 'teachers' joins 'Teachers'. added_by: the family member's account that did it (they
    go on seeing the people of their groups)."""
    group = " ".join(group.split())
    if not group or len(group) > 60:
        raise Problem("Give the group a short name, like Teachers or Foo Fighters.")
    same = con.execute("SELECT name FROM person_group WHERE lower(name) = lower(?) LIMIT 1", (group,)).fetchone()
    group = same[0] if same else group
    con.execute("INSERT OR IGNORE INTO person_group(person_id, name, added_by) VALUES (?, ?, ?)", (pid, group, added_by))
    return group


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
    con.executemany("""UPDATE face SET person_id = NULL, assigned_by = ?, assign_score = NULL, cluster_id = NULL
                       WHERE id = ?""", ((reason, f) for f in face_ids))
    _reindex_faces(con, face_ids)


def not_them(con: sqlite3.Connection, face_ids: list[int]) -> None:
    """Faces named as the wrong person: their name comes off and it is noted they are not that person, so they are
    neither suggested nor recognised as them again, and stay on their photo to be named as whoever they are."""
    con.execute("""INSERT OR IGNORE INTO face_not_person(face_id, person_id)
                   SELECT id, person_id FROM face WHERE id IN (SELECT value FROM json_each(?)) AND person_id IS NOT NULL""",
                (json.dumps(face_ids),))
    con.executemany("""UPDATE face SET person_id = NULL, assigned_by = NULL, assign_score = NULL, cluster_id = NULL
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
        labelled.append((pid, float(best_score[i]), r["id"]))
    con.executemany("""UPDATE face SET person_id = ?, assigned_by = 'auto', assign_score = ?, cluster_id = NULL
                       WHERE id = ?""", labelled)
    _reindex_faces(con, [f for _, _, f in labelled])
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
    guess = {f["id"]: pid for (_, pid), (_, f) in
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
    for (image_id, pid), (score, f) in _likeliest(con, faces, named_in).items():
        if pid not in shown:
            continue
        g = groups.setdefault(pid, {"person_id": pid, "faces": [], "seen_with": Counter()})
        g["faces"].append({"id": f["id"], "image_id": image_id, "score": score})
        g["seen_with"].update(named_in[image_id] & shown)
    for g in groups.values():
        g["faces"].sort(key=lambda f: -f["score"])
        g["seen_with"] = g["seen_with"].most_common()
    return sorted(groups.values(), key=lambda g: (-len(g["faces"]), g["person_id"]))


def suggest_for_photo(con: sqlite3.Connection, image_id: int) -> dict[int, tuple[int, float]]:
    """face id -> (person id, score) for the unnamed faces of a photo that look enough like someone not named in it yet
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
    return {f["id"]: (pid, score) for (_, pid), (score, f) in _likeliest(con, faces, named_in).items()}


def _likeliest(con: sqlite3.Connection, faces: list[sqlite3.Row],
               named_in: defaultdict[int, set[int]]) -> dict[tuple[int, int], tuple[float, sqlite3.Row]]:
    """Who each face most likely is: the closest person not named in its photo yet (nobody is in a photo twice), not
    born after it and not said to be someone else, when they score SUGGEST_SIMILARITY and beat the next such person by
    SUGGEST_MARGIN. (photo, person) -> (score, face); of two faces in one photo, only the likelier."""
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
            likeliest[key] = (score, f)
    return likeliest


def confirm(con: sqlite3.Connection, pid: int, yes: list[int], no: list[int], by: int | None = None) -> int:
    """Faces suggested as someone (People → Seen together): those ticked are named as them, as if by hand; those
    unticked are noted as not them, so they are neither suggested nor recognised as them again and stay free to be
    named as anyone else. Faces named meanwhile keep their name. Returns how many were named."""
    named = con.executemany("""UPDATE face SET person_id = ?, assigned_by = 'user', assign_score = NULL,
                                              cluster_id = NULL, named_by = ?
                               WHERE id = ? AND person_id IS NULL""", ((pid, by, f) for f in yes)).rowcount
    con.executemany("INSERT OR IGNORE INTO face_not_person(face_id, person_id) SELECT id, ? FROM face WHERE id = ?",
                    ((pid, f) for f in no))
    _reindex_faces(con, yes)
    return named + name_in_videos(con, _videos_of(con, yes))


def _before_birth(face, born: str | None) -> bool:
    """Whether the photo was taken before the person was born (by its camera date, or by the last day the date set by
    hand allows): then it isn't them."""
    if not born:
        return False
    if face["taken_at_source"] == "user":
        latest = face["date_to"]
    elif face["taken_at_source"] in ("exif", "camera", "filename"):
        latest = face["taken_at"]
    else:
        return False
    return bool(latest) and latest[:len(born)] < born


def _reindex_faces(con: sqlite3.Connection, face_ids: list[int]) -> None:
    con.commit()  # the names first; then the search, a few photos per write so nobody waits on it
    if not face_ids:
        return
    folders = FolderIndex(con)
    image_ids = set()
    for start in range(0, len(face_ids), 900):  # stay under SQLite's bound-parameter limit
        chunk = face_ids[start:start + 900]
        image_ids |= {r[0] for r in con.execute(
            f"SELECT image_id FROM face WHERE id IN ({','.join('?' * len(chunk))})", chunk)}
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
    looks = {}
    for pid in {p for pair in pairs for p in pair}:
        rows = con.execute("SELECT embedding FROM face WHERE person_id = ? AND assigned_by = 'user'", (pid,)).fetchall()
        if rows:
            v = _embeddings(rows).mean(axis=0)
            looks[pid] = v / np.linalg.norm(v)
    out = []
    for a, b in pairs:
        alike = float(looks[a] @ looks[b]) if a in looks and b in looks else None
        out.append({"added": a, "other": b, "added_name": persons[a]["name"], "other_name": persons[b]["name"],
                    "added_by": persons[a]["added_by"], "born": (births.get(a), births.get(b)), "alike": alike})
    return sorted(out, key=lambda m: (-(m["alike"] or 0), m["added_name"].lower()))
