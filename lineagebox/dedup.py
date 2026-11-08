"""Near-duplicate groups (edited copies, burst shots) by perceptual hash.

Every member must be within MAX_DISTANCE of the group's representative (the largest image), so groups cannot
chain across a slowly changing sequence. image.dup_group_id is the representative's id for every member;
singletons stay NULL. Descriptions are made once per group and shared.
"""
import sqlite3

import numpy as np

from lineagebox.catalog import VISIBLE

# Measured on the library LineageBox was developed on: edited copies (X.jpg / X-2.jpg) are at distance 0-2, burst shots
# of one scene at 3-10, unrelated photos almost never below 18.
MAX_DISTANCE = 8
CHUNK = 2000


def group(con: sqlite3.Connection) -> tuple[int, int]:
    """Recompute all groups of photos still in the library (removed ones drop out). Returns (groups, images)."""
    rows = con.execute(f"""SELECT i.id, i.phash, substr(i.taken_at, 1, 10) AS day, i.taken_at_source,
                                  coalesce(i.width * i.height, 0) AS pixels
                           FROM image i WHERE i.phash IS NOT NULL AND i.media = 'photo' AND {VISIBLE}""").fetchall()
    if not rows:
        return 0, 0
    ids = np.array([r["id"] for r in rows])
    hashes = np.array([r["phash"] for r in rows], dtype=np.int64).view(np.uint64)
    # file-time dates say when a copy was made, not when the photo was taken, so they cannot rule a pair out
    days = [r["day"] if r["taken_at_source"] != "mtime" else None for r in rows]

    neighbours: dict[int, list[int]] = {}
    for start in range(0, len(hashes), CHUNK):
        dist = np.bitwise_count(hashes[start:start + CHUNK, None] ^ hashes[None, :])
        for i, j in zip(*np.nonzero(dist <= MAX_DISTANCE)):
            i += start
            if i != j and (days[i] is None or days[j] is None or days[i] == days[j]):
                neighbours.setdefault(i, []).append(j)

    # largest image first: it becomes the representative of every unassigned neighbour
    order = sorted(neighbours, key=lambda i: (-rows[i]["pixels"], rows[i]["taken_at_source"] != "exif", ids[i]))
    rep_of: dict[int, int] = {}
    for i in order:
        if i in rep_of:
            continue
        members = [j for j in neighbours[i] if j not in rep_of]
        if members:
            rep_of[i] = i
            for j in members:
                rep_of[j] = i

    con.execute("UPDATE image SET dup_group_id = NULL WHERE dup_group_id IS NOT NULL")
    con.executemany("UPDATE image SET dup_group_id = ? WHERE id = ?",
                    ((int(ids[rep]), int(ids[i])) for i, rep in rep_of.items()))
    con.commit()
    share_dates(con)
    return len(set(rep_of.values())), len(rep_of)


# Dates a copy takes from its group: a file's time or a folder's year, or the one it took before ('copy'), which
# follows the camera's date when that is put right (camera_clock.py). Never a date set by hand, a camera's own, nor
# one from the file name.
TAKES_DATE = ("mtime", "folder", "copy")


def share_dates(con: sqlite3.Connection) -> int:
    """Copies of a photo dated only by their file's time (when the copy was made) or a folder take the camera's date
    of their near-duplicate group (edited, resized or scanned-again copies that lost theirs), as an automatic date
    ('copy'): the representative's when the camera dated it, else the group's earliest camera date. Commits. Returns
    how many photos took a date; the search finds them by it on the worker's next pass (search_stale)."""
    dated: dict[int, str] = {}
    for group_id, rep, taken in con.execute(f"""
            SELECT i.dup_group_id, i.id = i.dup_group_id, i.taken_at FROM image i
            WHERE i.dup_group_id IS NOT NULL AND i.taken_at_source IN ('exif', 'camera') AND {VISIBLE}
            ORDER BY i.dup_group_id, i.id = i.dup_group_id, i.taken_at DESC"""):
        dated[group_id] = taken  # ordered so the representative's, else the earliest, comes last and stays
    changed = [(dated[r["dup_group_id"]], r["id"]) for r in con.execute(f"""
        SELECT i.id, i.dup_group_id, i.taken_at, i.taken_at_source FROM image i
        WHERE i.dup_group_id IS NOT NULL AND i.taken_at_source IN ({",".join("?" * len(TAKES_DATE))}) AND {VISIBLE}""",
                                                                   TAKES_DATE)
               if r["dup_group_id"] in dated and (r["taken_at"], r["taken_at_source"]) != (dated[r["dup_group_id"]],
                                                                                          "copy")]
    con.executemany("UPDATE image SET taken_at = ?, taken_at_source = 'copy' WHERE id = ?", changed)
    con.executemany("INSERT OR IGNORE INTO search_stale(image_id) VALUES (?)", ((i,) for _, i in changed))
    con.commit()
    return len(changed)
