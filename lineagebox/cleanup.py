"""Cleanup: move photos, or single copies of them, to the Removed folder next to their watched folder; restore; empty.

A removed file keeps its path under the Removed folder (Photos/2012/x.jpg -> Photos-Removed/2012/x.jpg, see
removed_folder for watched folders that are a mount of their own) and is marked missing, so it drops out of every page,
the search and People. It can be restored until the Removed folders are emptied: empty_removed() is the only thing in
LineageBox that deletes a photo. Nothing here runs on its own; every removal is something the user selected on a cleanup
page.

What LineageBox made from a photo (preview, thumbnail, video frames, face crops, a video's 720p copy) is deleted once
the photo is gone for good: emptied from Removed, or deleted on the share by hand (drop_derivatives, after each scan).

A video's 720p copy (copies.py) goes wherever its video goes, unless another copy of the video stays in the library:
then the 720p copy moves next to that one, so the video still plays.
"""
import json
import os
import re
import stat
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from lineagebox import mounts, search, video
from lineagebox.catalog import VISIBLE
from lineagebox.config import Config
from lineagebox.faces import usable_faces_sql
from lineagebox.folders import FolderIndex
from lineagebox.ingest import STAGE_INGEST

CHUNK = 200  # files per write transaction
REMOVED_INSIDE = "LineageBox-Removed"  # the Removed folder of a watched folder that is a mount of its own


@dataclass
class Moved:
    moved: int = 0
    errors: list[str] = field(default_factory=list)


# ---- removing and restoring ----------------------------------------------------------------------------------------

def remove_files(con: sqlite3.Connection, cfg: Config, file_ids: list[int], reason: str,
                 keep_last_copy: bool = True) -> Moved:
    """Move these files to the Removed folder. With keep_last_copy, a photo's only remaining copy is never moved."""
    batch, now, result = time.time_ns() // 1_000_000, datetime.now().isoformat(timespec="seconds"), Moved()
    images, removed_to = set(), {}
    con.commit()
    for start in range(0, len(file_ids), CHUNK):
        con.execute("BEGIN IMMEDIATE")  # the last-copy check and the move happen while no one else can write
        try:
            for fid in file_ids[start:start + CHUNK]:
                f = con.execute("SELECT id, path, image_id FROM file WHERE id = ? AND NOT missing", (fid,)).fetchone()
                if f is None:
                    continue
                if keep_last_copy and con.execute("SELECT 1 FROM file WHERE image_id = ? AND id != ? AND NOT missing",
                                                  (f["image_id"], fid)).fetchone() is None:
                    result.errors.append(f"{f['path']}: kept, it is the only copy")
                    continue
                watched = watched_folder(cfg, f["path"])
                if watched not in removed_to:
                    removed_to[watched] = removed_folder(cfg, watched)
                target = _free_name(cfg.share_root, f"{removed_to[watched]}{f['path'][len(watched):]}")
                try:
                    _move(cfg.share_root / f["path"], cfg.share_root / target, like=cfg.share_root / watched)
                except OSError as e:
                    result.errors.append(f"{f['path']}: {e.strerror or e}")
                    continue
                staying = con.execute("""SELECT path FROM file WHERE image_id = ? AND id != ? AND NOT missing
                                         ORDER BY length(path), path LIMIT 1""", (f["image_id"], fid)).fetchone()
                _move_copy(cfg, f["path"], staying[0] if staying else target, result)
                con.execute("""UPDATE file SET missing = 1, removed_at = ?, removed_path = ?, removed_reason = ?,
                                               removed_batch = ? WHERE id = ?""", (now, target, reason, batch, fid))
                images.add(f["image_id"])
                result.moved += 1
        finally:
            con.commit()  # whatever was moved is recorded, even if something unexpected stopped the loop
    _after_change(con, images)
    return result


def remove_images(con: sqlite3.Connection, cfg: Config, image_ids: list[int], reason: str) -> Moved:
    """Remove photos entirely: every copy of each."""
    ids = [r[0] for r in _in(con, "SELECT id FROM file WHERE NOT missing AND image_id IN ({})", image_ids)]
    return remove_files(con, cfg, ids, reason, keep_last_copy=False)


def remove_copies(con: sqlite3.Connection, cfg: Config, remove_from: int, keep_in: int) -> Moved:
    """Remove the copies in folder remove_from of photos that also have a copy in folder keep_in. With both the same
    folder, remove its extra copies (one is kept per photo: the shortest name)."""
    rows = con.execute("""SELECT a.id, a.image_id FROM file a WHERE a.folder_id = ? AND NOT a.missing
                          AND EXISTS (SELECT 1 FROM file b WHERE b.image_id = a.image_id AND b.id != a.id
                                      AND b.folder_id = ? AND NOT b.missing)
                          ORDER BY a.image_id, length(a.path), a.path""", (remove_from, keep_in)).fetchall()
    if remove_from == keep_in:
        seen, ids = set(), []
        for r in rows:
            if r["image_id"] in seen:
                ids.append(r["id"])
            seen.add(r["image_id"])
    else:
        ids = [r["id"] for r in rows]
    a, b = _folder_path(con, remove_from), _folder_path(con, keep_in)
    reason = f"extra copies in {a}" if remove_from == keep_in else f"copies in {a} (kept in {b})"
    return remove_files(con, cfg, ids, reason)


def restore(con: sqlite3.Connection, cfg: Config, file_ids: list[int] | None = None, batch: int | None = None) -> Moved:
    """Move removed files back to where they were in the library."""
    if batch is not None:
        file_ids = [r[0] for r in con.execute("SELECT id FROM file WHERE removed_batch = ? AND removed_at IS NOT NULL",
                                              (batch,))]
    result, images = Moved(), set()
    con.commit()
    for start in range(0, len(file_ids or []), CHUNK):
        con.execute("BEGIN IMMEDIATE")
        try:
            for fid in file_ids[start:start + CHUNK]:
                f = con.execute("SELECT id, path, image_id, removed_path FROM file WHERE id = ? AND removed_at IS NOT NULL",
                                (fid,)).fetchone()
                if f is None:
                    continue
                try:
                    _move(cfg.share_root / f["removed_path"], cfg.share_root / f["path"],
                          like=cfg.share_root / watched_folder(cfg, f["path"]))
                except OSError as e:
                    result.errors.append(f"{f['path']}: {e.strerror or e}")
                    continue
                _move_copy(cfg, f["removed_path"], f["path"], result)
                con.execute("""UPDATE file SET missing = 0, removed_at = NULL, removed_path = NULL, removed_reason = NULL,
                                               removed_batch = NULL WHERE id = ?""", (fid,))
                images.add(f["image_id"])
                result.moved += 1
        finally:
            con.commit()
    _after_change(con, images)
    return result


def empty_removed(con: sqlite3.Connection, cfg: Config) -> Moved:
    """Delete everything cleanup moved to the Removed folders. This cannot be undone."""
    result, deleted, emptied = Moved(), [], set()
    for r in con.execute("SELECT id, removed_path FROM file WHERE removed_at IS NOT NULL").fetchall():
        try:
            (cfg.share_root / r["removed_path"]).unlink(missing_ok=True)
            video.copy_path(cfg.share_root / r["removed_path"]).unlink(missing_ok=True)
            deleted.append(r["id"])
            emptied.add((cfg.share_root / r["removed_path"]).parent)
        except OSError as e:
            result.errors.append(f"{r['removed_path']}: {e.strerror or e}")
    for start in range(0, len(deleted), CHUNK):
        _in(con, "DELETE FROM file WHERE id IN ({})", deleted[start:start + CHUNK])
        con.commit()
    for folder in sorted(emptied, key=lambda p: -len(p.parts)):  # leave no empty folders behind, but keep X-Removed
        while not folder.name.lower().endswith("-removed") and folder.is_dir() and not os.listdir(folder):
            folder.rmdir()
            folder = folder.parent
    result.moved = len(deleted)
    drop_derivatives(con, cfg)
    return result


def drop_derivatives(con: sqlite3.Connection, cfg: Config) -> int:
    """Delete what LineageBox made from photos gone for good: preview, thumbnail, video frames, face crops and a
    video's 720p copy. Gone means no file of the photo is in the library, in a Removed folder (it can be put back) or
    still on disk (a folder no longer watched). Nothing is deleted while a watched folder can't be reached. The
    photo's faces, tags and description stay; if a file of it comes back, ingest makes the rest again. Returns how
    many photos."""
    if not all((cfg.share_root / w).is_dir() for w in cfg.folders):
        return 0
    rows = con.execute(f"""SELECT i.id, i.sha256, i.media FROM image i WHERE i.stage & {STAGE_INGEST}
                           AND NOT EXISTS (SELECT 1 FROM file f WHERE f.image_id = i.id
                                           AND (NOT f.missing OR f.removed_at IS NOT NULL))""").fetchall()
    gone = []
    for r in rows:
        paths = [cfg.share_root / p for (p,) in con.execute("SELECT path FROM file WHERE image_id = ?", (r["id"],))]
        if any(p.exists() for p in paths):
            continue
        made = [cfg.preview_path(r["sha256"]), cfg.thumb_path(r["sha256"])]
        made += [cfg.frame_path(r["sha256"], idx) for (idx,) in con.execute(
            "SELECT idx FROM video_frame WHERE image_id = ?", (r["id"],))]
        for (face_id,) in con.execute("SELECT id FROM face WHERE image_id = ?", (r["id"],)):
            made += cfg.face_crops(face_id)
        if r["media"] == "video":
            made += [video.copy_path(p) for p in paths]
        for path in made:
            path.unlink(missing_ok=True)
        gone.append(r["id"])
    con.executemany(f"UPDATE image SET stage = stage & ~{STAGE_INGEST} WHERE id = ?", ((i,) for i in gone))
    con.commit()
    return len(gone)


def watched_folder(cfg: Config, path: str) -> str:
    """The watched folder a file is in ('Photos' for 'Photos/2012/x.jpg'); its Removed folder is next to it."""
    return next((w for w in cfg.folders if path.startswith(w + "/")), path.split("/")[0])


def removed_folder(cfg: Config, watched: str) -> str:
    """Where cleanup puts what it removes from a watched folder (share-relative): next to it, Photos-Removed for
    Photos. A file can only be renamed within one mount, so when the watched folder is a mount of its own (a NAS
    share or a folder of this machine, mounted into the container) it goes inside instead: Photos/LineageBox-Removed.
    Either way its name ends in -Removed, which the scan never walks."""
    if mounts.is_mount(cfg.share_root / watched):
        return f"{watched}/{REMOVED_INSIDE}"
    return f"{watched}-Removed"


def _move(src: Path, dst: Path, like: Path) -> None:
    if dst.exists():
        raise FileExistsError(0, "something else is already at that path")  # rename would silently replace it
    missing = [p for p in reversed(dst.parents) if not p.exists()]
    owner = like.stat()
    for d in missing:  # new folders get the library's owner and permissions, so the share can still manage them
        d.mkdir()
        try:
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                os.chown(d, owner.st_uid, owner.st_gid)
            d.chmod(stat.S_IMODE(owner.st_mode))
        except OSError:  # an SMB share without Unix permissions, or not running as root: the folder works as it is
            pass
    os.rename(src, dst)  # same file system: instant, and the file keeps its dates and owner


def _move_copy(cfg: Config, from_rel: str, to_rel: str, result: Moved) -> None:
    """Move the 720p copy next to from_rel (if there is one) next to to_rel. A copy already there stays."""
    src, dst = video.copy_path(cfg.share_root / from_rel), video.copy_path(cfg.share_root / to_rel)
    if not src.is_file() or dst.exists():
        return
    try:
        _move(src, dst, like=cfg.share_root / watched_folder(cfg, to_rel))
    except OSError as e:
        result.errors.append(f"{src.name}: {e.strerror or e}")


def _free_name(root: Path, rel: str) -> str:
    """rel, or 'name (2).jpg' and so on if the Removed folder already has a file there."""
    if ".." in rel.split("/"):
        raise ValueError(f"unexpected path {rel!r}")
    stem, dot, ext = rel.rpartition(".") if "." in rel.rpartition("/")[2] else (rel, "", "")
    candidate, n = rel, 1
    while (root / candidate).exists():
        n += 1
        candidate = f"{stem} ({n}){dot}{ext}"
    return candidate


# ---- keeping ------------------------------------------------------------------------------------------------------

def keep_images(con: sqlite3.Connection, image_ids: list[int], keep: bool = True) -> int:
    """Mark photos as wanted: the blurry, papers/screens/repairs and similar-shot lists stop suggesting them.
    keep=False puts them back on those lists."""
    n = 0
    for start in range(0, len(image_ids), CHUNK):
        chunk = image_ids[start:start + CHUNK]
        if keep:
            n += con.execute(f"UPDATE image SET kept_at = ? WHERE kept_at IS NULL AND id IN ({','.join('?' * len(chunk))})",
                             (_now(), *chunk)).rowcount
        else:
            n += con.execute(f"UPDATE image SET kept_at = NULL WHERE id IN ({','.join('?' * len(chunk))})",
                             chunk).rowcount
    con.commit()
    return n


def remove_similar(con: sqlite3.Connection, cfg: Config, kind: str, keep_raw: bool = True) -> Moved:
    """Move what this kind of similar-shot group ticks, in every group at once (copies of one picture only)."""
    if kind not in _COPY_KINDS:
        raise ValueError(f"{kind}: only copies of one picture are removed in bulk")
    label = SIMILAR_KINDS[kind] + (" (kept the RAW)" if kind == "raw" and keep_raw else
                                   " (kept the JPEG)" if kind == "raw" else "")
    return remove_images(con, cfg, similar_ticked(con, kind, keep_raw), f"similar shots: {label.lower()}")


def keep_group(con: sqlite3.Connection, group_id: int) -> int:
    """Keep every photo of a similar-shot group, so the group is not listed again (until a new shot joins it)."""
    ids = [r[0] for r in con.execute(f"SELECT i.id FROM image i WHERE i.dup_group_id = ? AND {VISIBLE}", (group_id,))]
    return keep_images(con, ids)


def keep_copies(con: sqlite3.Connection, a: int, b: int, keep: bool = True) -> None:
    """Stop (or start again) listing this pair of folders under Duplicate copies."""
    a, b = min(a, b), max(a, b)
    if keep:
        con.execute("INSERT OR IGNORE INTO kept_copies(folder_a, folder_b, kept_at) VALUES (?, ?, ?)", (a, b, _now()))
    else:
        con.execute("DELETE FROM kept_copies WHERE folder_a = ? AND folder_b = ?", (a, b))
    con.commit()


def kept(con: sqlite3.Connection) -> tuple[list[sqlite3.Row], list[dict]]:
    """Photos and folder pairs marked to keep, most recent first."""
    images = con.execute(f"""SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.kept_at FROM image i
                             WHERE i.kept_at IS NOT NULL AND {VISIBLE} ORDER BY i.kept_at DESC, i.id""").fetchall()
    pairs = [{"a": r["folder_a"], "b": r["folder_b"], "kept_at": r["kept_at"],
              "a_path": _folder_path(con, r["folder_a"]), "b_path": _folder_path(con, r["folder_b"])}
             for r in con.execute("SELECT * FROM kept_copies ORDER BY kept_at DESC")]
    return images, pairs


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _after_change(con: sqlite3.Connection, image_ids: set[int]) -> None:
    """Keep near-duplicate groups, face groups and the search index right after photos leave or return."""
    if not image_ids:
        return
    gone = [r[0] for r in _in(con, f"SELECT i.id FROM image i WHERE i.id IN ({{}}) AND NOT ({VISIBLE})", image_ids)]
    _in(con, "UPDATE face SET cluster_id = NULL WHERE image_id IN ({})", gone)  # out of the People page's groups
    folders = FolderIndex(con)
    groups = {r[0] for r in _in(con, "SELECT dup_group_id FROM image WHERE id IN ({}) AND dup_group_id IS NOT NULL",
                                image_ids)}
    for rep in groups:
        # Search shows a group once, through its representative; if that photo was removed, promote the largest
        # photo left. Descriptions are shared by the whole group, so nothing needs describing again.
        members = con.execute(f"""SELECT i.id FROM image i WHERE i.dup_group_id = ? AND {VISIBLE}
                                  ORDER BY coalesce(i.width * i.height, 0) DESC, i.sharpness DESC, i.id""",
                              (rep,)).fetchall()
        if members and members[0][0] != rep and not any(m[0] == rep for m in members):
            con.execute("UPDATE image SET dup_group_id = ? WHERE dup_group_id = ?", (members[0][0], rep))
            rep = members[0][0]
        search.index_group(con, rep, folders)
    for image_id in image_ids:
        search.index_image(con, image_id, folders)
    con.commit()


def _in(con: sqlite3.Connection, sql: str, ids) -> list[sqlite3.Row]:
    ids = list(ids)
    return con.execute(sql.format(",".join("?" * len(ids))), ids).fetchall() if ids else []


def _folder_path(con: sqlite3.Connection, folder_id: int) -> str:
    row = con.execute("SELECT path FROM folder WHERE id = ?", (folder_id,)).fetchone()
    return (row[0] or "the top folder") if row else "?"


# ---- what to review ------------------------------------------------------------------------------------------------

def summary(con: sqlite3.Connection, blurry_below: float, only: set[str] | None = None) -> dict:
    """The Cleanup page's numbers, or only those named: its cards each ask for their own, as some take seconds on a
    big library (the similar groups, the reference photos)."""
    one = lambda sql, *a: con.execute(sql, a).fetchone()[0]
    counts = {
        "redundant_copies": lambda: one("""SELECT coalesce(sum(n - 1), 0) FROM (SELECT count(*) n FROM file
                                           WHERE NOT missing AND image_id IS NOT NULL GROUP BY image_id HAVING n > 1)"""),
        "similar_groups": lambda: len(_similar_group_ids(con)),
        "blurry": lambda: one(f"""SELECT count(*) FROM image i WHERE i.sharpness >= 0 AND i.sharpness < ?
                                  AND i.kept_at IS NULL AND i.media = 'photo' AND {VISIBLE}""", blurry_below),
        "unmeasured": lambda: one(f"""SELECT count(*) FROM image i WHERE i.sharpness IS NULL AND i.media = 'photo'
                                      AND {VISIBLE}"""),
        "reference": lambda: {k: len(v) for k, v in reference_photos(con).items()},
        "removed": lambda: one("SELECT count(*) FROM file WHERE removed_at IS NOT NULL"),
        "kept": lambda: one(f"SELECT count(*) FROM image i WHERE i.kept_at IS NOT NULL AND {VISIBLE}")
                        + one("SELECT count(*) FROM kept_copies"),
    }
    return {k: count() for k, count in counts.items() if only is None or k in only}


def copy_pairs(con: sqlite3.Connection, offset: int = 0, limit: int = 30) -> tuple[list[dict], int]:
    """Pairs of folders holding identical copies of the same photos, most shared first."""
    pairs = con.execute("""
        WITH present AS (SELECT id, image_id, folder_id FROM file WHERE NOT missing AND image_id IS NOT NULL)
        SELECT min(a.folder_id, b.folder_id) AS fa, max(a.folder_id, b.folder_id) AS fb,
               count(DISTINCT a.image_id) AS shared
        FROM present a JOIN present b ON b.image_id = a.image_id AND b.id > a.id
        GROUP BY fa, fb ORDER BY shared DESC, fa, fb""").fetchall()
    dismissed = {(r[0], r[1]) for r in con.execute("SELECT folder_a, folder_b FROM kept_copies")}
    pairs = [p for p in pairs if (p["fa"], p["fb"]) not in dismissed]
    sizes = dict(con.execute("SELECT folder_id, count(DISTINCT image_id) FROM file WHERE NOT missing GROUP BY 1"))
    out = []
    for p in pairs[offset:offset + limit]:
        samples = [r[0] for r in con.execute("""
            SELECT DISTINCT i.sha256 FROM file a JOIN file b ON b.image_id = a.image_id AND b.id != a.id
            JOIN image i ON i.id = a.image_id
            WHERE a.folder_id = ? AND b.folder_id = ? AND NOT a.missing AND NOT b.missing LIMIT 8""",
                                             (p["fa"], p["fb"]))]
        out.append({"a": p["fa"], "b": p["fb"], "shared": p["shared"], "samples": samples,
                    "a_path": _folder_path(con, p["fa"]), "b_path": _folder_path(con, p["fb"]),
                    "a_size": sizes.get(p["fa"], 0), "b_size": sizes.get(p["fb"], 0)})
    return out, len(pairs)


def _similar_group_ids(con: sqlite3.Connection) -> list[int]:
    return [r[0] for r in con.execute(f"""SELECT i.dup_group_id FROM image i WHERE i.dup_group_id IS NOT NULL
                                          AND {VISIBLE} GROUP BY i.dup_group_id
                                          HAVING count(*) > 1 AND count(*) > count(i.kept_at)
                                          ORDER BY min(i.taken_at), i.dup_group_id""")]


# Kinds of similar-shot group, as filters on the Similar shots page. Measured on the groups of the library LineageBox
# was developed on: over 40% hold a smaller copy of a picture (web, email and phone-app copies), about 30% the same
# picture saved twice at the same size, a quarter burst shots, about 1 in 6 a copy that lost its camera date and 1 in 7
# a RAW file and its JPEG. Most groups are more than one kind. Each kind ticks only the photos it is about, so a
# burst shot in the same group stays unticked.
SIMILAR_KINDS = {
    "all": "All groups",
    "smaller": "Smaller copies",
    "same": "Same-size copies",
    "raw": "RAW + JPEG",
    "undated": "Copies without the camera date",
    "burst": "Burst shots",
    "big": "4 or more photos",
}
SIMILAR_HINTS = {
    "all": "Every group. The suggested photo to keep (the sharpest of the largest, with its camera date) is unticked "
           "and the rest are ticked.",
    "smaller": "The same picture again at a lower resolution, such as copies from email, Facebook or a phone app. "
               "The smaller copies are ticked; other shots in the group are not. Look at the folders: the small copy "
               "is often the one filed in a named album (and folder names feed the search), while the original sits "
               "in a camera or date folder.",
    "same": "The same picture saved twice at the same size: re-saved, renamed or copied without its camera date. "
            "One is kept (the one with the camera date, then the sharpest); the others are ticked.",
    "raw": "A Nikon RAW file (.NEF) and a JPEG of the same shot. Choose which to keep; the other is ticked.",
    "undated": "Copies that lost the camera's date (they are dated by folder, file name or file time) next to one "
               "that has it. The undated copies are ticked, unless they are larger.",
    "burst": "Shots taken seconds apart. The sharpest of the largest is suggested; look before you move.",
    "big": "Groups of 4 or more photos, usually bursts.",
}
_COPY_KINDS = ("smaller", "same", "raw", "undated")  # copies of one picture: safe to clear on every page at once


def similar_groups(con: sqlite3.Connection, kind: str = "all", keep_raw: bool = True, offset: int = 0,
                   limit: int = 20) -> tuple[list[dict], int]:
    """Near-duplicate groups of this kind, each member marked "keep" (the suggested photo to keep) and "tick" (what
    this kind suggests removing), with "why" saying what each ticked photo is."""
    groups = [g for g in _similar_members(con) if _mark(kind, g["members"], keep_raw)]
    return groups[offset:offset + limit], len(groups)


def similar_counts(con: sqlite3.Connection) -> dict[str, int]:
    groups = _similar_members(con)
    return {kind: sum(1 for g in groups if _mark(kind, g["members"], True)) for kind in SIMILAR_KINDS}


def similar_ticked(con: sqlite3.Connection, kind: str, keep_raw: bool = True) -> list[int]:
    """Every photo this kind ticks, in every group: what "Move all of them" removes."""
    return [m["id"] for g in _similar_members(con) if _mark(kind, g["members"], keep_raw)
            for m in g["members"] if m["tick"]]


def _similar_members(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute(f"""
        SELECT i.id, i.dup_group_id, i.sha256, i.width, i.height, i.format, i.phash, i.sharpness, i.taken_at,
               i.taken_at_source, i.kept_at,
               (SELECT f.path FROM file f WHERE f.image_id = i.id AND NOT f.missing
                ORDER BY length(f.path), f.path LIMIT 1) AS path,
               (SELECT max(f.size) FROM file f WHERE f.image_id = i.id AND NOT f.missing) AS bytes
        FROM image i WHERE i.dup_group_id IS NOT NULL AND {VISIBLE} ORDER BY i.taken_at, i.id""").fetchall()
    by_group: dict[int, dict[int, dict]] = {}
    for r in rows:
        m = dict(r)
        m["pixels"] = (m["width"] or 0) * (m["height"] or 0)
        m["dated"] = m["taken_at_source"] == "exif"
        # keyed by id: a damaged index (lost writes on the share's disk) returned photos twice
        by_group.setdefault(r["dup_group_id"], {})[m["id"]] = m
    by_group = {g: list(ms.values()) for g, ms in by_group.items()}
    groups = [{"id": g, "members": ms} for g, ms in by_group.items()
              if len(ms) > 1 and any(m["kept_at"] is None for m in ms)]
    groups.sort(key=lambda g: (min(m["taken_at"] or "" for m in g["members"]), g["id"]))
    return groups


def _preference(m: dict) -> tuple:
    """Of two photos of the same size: the one with the camera date, then the sharper, then the larger file."""
    return m["dated"], m["sharpness"] if m["sharpness"] is not None else -1, m["bytes"] or 0


def _best(members: list[dict]) -> dict:
    """The photo to keep: the most preferred of the largest (within 5%)."""
    biggest = max(m["pixels"] for m in members)
    return max((m for m in members if m["pixels"] >= 0.95 * biggest), key=_preference)


def _copy_of(a: dict, b: dict) -> bool:
    """The same picture, not two shots of a burst: the camera times agree to the second or, when one has no camera time,
    the fingerprints are within 4 bits (97% of the resized copies and RAW/JPEG pairs in the library LineageBox was
    developed on are; most burst shots are 4 to 10 apart)."""
    if a["dated"] and b["dated"]:
        return a["taken_at"] == b["taken_at"]
    return None not in (a["phash"], b["phash"]) and bin((a["phash"] ^ b["phash"]) & 0xFFFFFFFFFFFFFFFF).count("1") <= 4


def _mark(kind: str, members: list[dict], keep_raw: bool) -> bool:
    """Set keep/tick/why on the members for this kind; False if the group is not of this kind. Each kind compares
    every photo with every other, so in a group mixing a burst with copies, only the copies are ticked."""
    ticks: dict[int, str] = {}
    if kind in ("all", "burst", "big"):
        if kind == "burst" and len({m["taken_at"] for m in members if m["dated"]}) < 2:
            return False
        if kind == "big" and len(members) < 4:
            return False
        best = _best(members)
        ticks = {m["id"]: "" for m in members if m is not best}
    elif kind == "smaller":
        for m in members:
            larger = [b for b in members if b is not m and 0 < m["pixels"] <= b["pixels"] * 2 / 3 and _copy_of(m, b)]
            if larger:
                ticks[m["id"]] = f"smaller copy ({m['pixels'] / max(b['pixels'] for b in larger):.0%} of the pixels)"
    elif kind == "same":
        kept: list[dict] = []
        for m in sorted(members, key=_preference, reverse=True):
            if any(abs(m["pixels"] - k["pixels"]) <= k["pixels"] / 100 and _copy_of(m, k) for k in kept):
                ticks[m["id"]] = "same picture, same size"
            else:
                kept.append(m)
    elif kind == "raw":
        keeps = [m for m in members if (m["format"] == "nef") == keep_raw]
        ticks = {m["id"]: "the JPEG" if keep_raw else "the RAW file" for m in members
                 if (m["format"] == "nef") != keep_raw and any(_copy_of(m, k) for k in keeps)}
    elif kind == "undated":
        dated = [m for m in members if m["dated"]]
        ticks = {m["id"]: "no camera date" for m in members
                 if not m["dated"] and any(m["pixels"] <= d["pixels"] * 1.05 and _copy_of(m, d) for d in dated)}
    else:
        return False
    ticks = {i: why for i, why in ticks.items() if not any(m["id"] == i and m["kept_at"] for m in members)}
    if not ticks:  # nothing of this kind, or only photos someone chose to keep
        return False
    unticked = [m for m in members if m["id"] not in ticks]
    keep = _best(unticked) if unticked else None
    for m in members:
        m["keep"], m["tick"], m["why"] = m is keep, m["id"] in ticks, ticks.get(m["id"], "")
    return True


def blurry(con: sqlite3.Connection, below: float, offset: int = 0, limit: int = 120) -> tuple[list[sqlite3.Row], int]:
    where = f"i.sharpness >= 0 AND i.sharpness < ? AND i.kept_at IS NULL AND i.media = 'photo' AND {VISIBLE}"
    total = con.execute(f"SELECT count(*) FROM image i WHERE {where}", (below,)).fetchone()[0]
    rows = con.execute(f"""SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.sharpness FROM image i
                           WHERE {where} ORDER BY i.sharpness, i.id LIMIT ? OFFSET ?""", (below, limit, offset)).fetchall()
    return rows, total


# Phrases in the AI descriptions that mark pictures taken to remember something rather than to keep. Checked against
# a few thousand descriptions: single words like "part", "label", "damage" or "form" ("buds beginning to form")
# mostly hit ordinary photos, so only specific phrases are used, and only on photos without people.
REFERENCE = {
    "papers": ["receipt", "receipts", "invoice", "paperwork", "printed page", "printed document", "document",
               "documents", "handwritten note", "sticky note", "whiteboard", "business card", "price tag",
               "instruction manual", "user manual", "shipping label", "packing slip", "bill of sale"],
    "screens": ["screenshot", "computer screen", "laptop screen", "phone screen", "tablet screen", "tv screen",
                "television screen", "monitor showing", "monitor displaying", "screen showing", "screen displaying",
                "error message", "blue screen"],
    "repairs": ["wiring", "exposed wires", "circuit board", "circuit boards", "electrical panel", "electrical box",
                "electrical outlet", "breaker box", "breaker panel", "fuse box", "junction box", "serial number",
                "model number", "barcode", "server rack", "water heater", "furnace", "thermostat", "plumbing",
                "water damage", "disassembled", "internal components"],
}
REFERENCE_LABELS = {"papers": "Papers and documents", "screens": "Screens and screenshots",
                    "repairs": "Wiring, parts and repairs"}
_REFERENCE_RE = {k: re.compile(r"\b(" + "|".join(map(re.escape, v)) + r")\b") for k, v in REFERENCE.items()}


def reference_photos(con: sqlite3.Connection) -> dict[str, list[dict]]:
    """Described photos without people whose description matches a REFERENCE phrase, by category."""
    out: dict[str, list[dict]] = {k: [] for k in REFERENCE}
    seen = set()
    for r in con.execute(f"""
            SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, a.caption, a.raw_json FROM analysis a
            JOIN image i ON i.id = a.image_id
            WHERE coalesce(a.people_count, 0) = 0 AND i.kept_at IS NULL AND i.media = 'photo' AND {VISIBLE}
              AND NOT EXISTS (SELECT 1 FROM face f WHERE f.image_id = i.id AND {usable_faces_sql()})
            ORDER BY i.taken_at, i.id, a.created_at DESC"""):
        if r["id"] in seen:  # an older description of the same photo
            continue
        seen.add(r["id"])
        raw = json.loads(r["raw_json"])
        text = " ".join([r["caption"] or "", *raw.get("objects", []), *raw.get("tags", [])]).lower()
        for kind, rx in _REFERENCE_RE.items():
            if m := rx.search(text):
                out[kind].append({**{k: r[k] for k in ("id", "sha256", "taken_at", "taken_at_source", "caption")},
                                  "matched": m.group(1)})
                break
    return out


def removed_batches(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute("""SELECT removed_batch AS batch, removed_reason AS reason, min(removed_at) AS at,
                                 count(*) AS files, COALESCE(SUM(size), 0) AS bytes
                          FROM file WHERE removed_at IS NOT NULL
                          GROUP BY removed_batch ORDER BY removed_batch DESC""").fetchall()
    out = []
    for r in rows:
        samples = con.execute("""SELECT DISTINCT i.id, i.sha256 FROM file f JOIN image i ON i.id = f.image_id
                                 WHERE f.removed_batch = ? AND f.removed_at IS NOT NULL LIMIT 12""",
                              (r["batch"],)).fetchall()
        out.append({**dict(r), "samples": samples})
    return out
