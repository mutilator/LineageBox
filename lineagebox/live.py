"""Live Photos: the still an iPhone takes and the short clip it records with it, kept as one item.

On the phone they are one photo; copied off it they are two files with the same name in the same folder: IMG_1234.HEIC
(or .JPG) and IMG_1234.MOV (.MP4 from some exports). The clip stays in the catalog like any video: the scan follows its
file, it is checked for damage, and a 720p copy is made of it when browsers can't play it as it is. But its row points
at its still (image.live_of), and catalog.VISIBLE leaves it out, so it is in no grid, count, search, memory, tour or
Cleanup list, and the worker spends no faces, descriptions or transcripts on it. The still's page plays it
(catalog.live_clip), whoever may see the still may play it (access.py), and Cleanup moves it wherever its still goes
(cleanup.remove_files, cleanup.restore), but for its only copy when a copy of the still stays in the library: emptying
the Removed folders would delete it for good.

A clip is paired when it is a short video (LIVE_SECONDS) with a HEIC or JPEG of the same name beside it. Phones and
cameras number their photos and videos in one run, so a video of its own doesn't share a photo's name, and anything
longer is a video someone made. A clip the family already worked on as a video of its own (a face named in it,
someone or a tag added to it, its date set by hand) stays one: what people did by hand wins. pair() looks again on
every ingest pass: a clip whose still is gone from beside it (deleted on the share) is a video of its own again.
"""
import json
import posixpath
import sqlite3
from collections import defaultdict
from pathlib import Path

LIVE_SECONDS = 4.0  # an iPhone's Live Photo clip is about 3 seconds
CLIP_EXTENSIONS = (".mov", ".mp4")
STILL_EXTENSIONS = (".heic", ".heif", ".jpg", ".jpeg")


def stem(path: str) -> str:
    """'lib/2021/IMG_1234.HEIC' -> 'lib/2021/img_1234': what a still and its clip have in common."""
    return posixpath.splitext(path)[0].lower()


def looks_like_clip(path: Path, duration: float | None) -> bool:
    """Whether a video just read will be paired with a photo beside it, for ingest (which has no database while it
    reads): then one frame of it is enough."""
    if duration is None or duration > LIVE_SECONDS or path.suffix.lower() not in CLIP_EXTENSIONS:
        return False
    return any(path.with_suffix(e).is_file() for ext in STILL_EXTENSIONS for e in (ext.upper(), ext))


def pair(con: sqlite3.Connection) -> int:
    """Point each short clip at the still beside it, and let go of those whose still is no longer there. Returns how
    many changed. A clip with no file on the share is left as it is: removed in Cleanup with its still, it comes back
    with it. Only stills that could be read count: a clip beside one that can't is a video of its own, and so is one
    worked on by hand (_by_hand)."""
    clips = con.execute("""SELECT i.id, i.live_of, f.path, f.folder_id FROM image i JOIN file f ON f.image_id = i.id
                           WHERE i.media = 'video' AND i.error IS NULL AND i.duration <= ? AND NOT f.missing""",
                        (LIVE_SECONDS,)).fetchall()
    if not clips:
        return 0
    stills = defaultdict(set)  # stem -> the photos with that name
    for r in con.execute("""SELECT f.path, f.image_id FROM file f JOIN image i ON i.id = f.image_id
                            WHERE i.media = 'photo' AND i.error IS NULL AND NOT f.missing
                            AND f.folder_id IN (SELECT value FROM json_each(?))""",
                         (json.dumps(sorted({r["folder_id"] for r in clips})),)):
        if posixpath.splitext(r["path"])[1].lower() in STILL_EXTENSIONS:
            stills[stem(r["path"])].add(r["image_id"])
    found: dict[int, set[int]] = defaultdict(set)  # clip -> the stills beside any of its files
    now = {}
    for r in clips:
        now[r["id"]] = r["live_of"]
        if posixpath.splitext(r["path"])[1].lower() in CLIP_EXTENSIONS:
            found[r["id"]] |= stills.get(stem(r["path"]), set())
    changes, by_hand = [], _by_hand(con, [c for c, live_of in now.items() if live_of is None and found.get(c)])
    for clip, live_of in now.items():
        beside = found.get(clip, set()) if clip not in by_hand else set()
        still = live_of if live_of in beside else min(beside, default=None)  # the same still while it is there
        if still != live_of:
            changes.append((still, clip))
    if not changes:
        return 0
    try:
        con.executemany("UPDATE image SET live_of = ? WHERE id = ?", changes)
        # out of the People page's groups of unnamed faces, as a removed photo's faces are (cleanup._after_change)
        con.executemany("UPDATE face SET cluster_id = NULL WHERE image_id = ?",
                        ((clip,) for still, clip in changes if still is not None))
        con.commit()
    except sqlite3.OperationalError as e:  # the catalog busy: they are paired on the next pass
        con.rollback()
        if "locked" not in str(e):
            raise
        return 0
    return len(changes)


def _by_hand(con: sqlite3.Connection, image_ids: list[int]) -> set[int]:
    """Of these videos, those someone worked on by hand: a face named, someone or a tag added, the date set."""
    if not image_ids:
        return set()
    return {r[0] for r in con.execute("""
        WITH v(id) AS (SELECT value FROM json_each(?))
        SELECT image_id FROM face WHERE assigned_by = 'user' AND image_id IN v
        UNION SELECT image_id FROM image_person WHERE image_id IN v
        UNION SELECT image_id FROM image_tag WHERE source = 'user' AND image_id IN v
        UNION SELECT id FROM image WHERE taken_at_source = 'user' AND id IN v""", (json.dumps(image_ids),))}


def clips_beside(con: sqlite3.Connection, file_id: int, removed: bool = False) -> list[int]:
    """The files of a still's clip next to one file of the still (same folder, same name), for Cleanup to move with
    it: those on the share, or (removed) those removed with it, to restore."""
    f = con.execute("SELECT path, folder_id, image_id, removed_batch FROM file WHERE id = ?", (file_id,)).fetchone()
    if f is None or f["image_id"] is None:
        return []
    where = "c.removed_at IS NOT NULL AND c.removed_batch IS ?" if removed else "NOT c.missing"
    rows = con.execute(f"""SELECT c.id, c.path FROM file c JOIN image i ON i.id = c.image_id
                           WHERE i.live_of = ? AND c.folder_id = ? AND {where}""",
                       (f["image_id"], f["folder_id"], *((f["removed_batch"],) if removed else ()))).fetchall()
    return [r["id"] for r in rows if stem(r["path"]) == stem(f["path"])]
