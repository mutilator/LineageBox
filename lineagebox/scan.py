"""Stage 1: walk the watched folders and record folders, photos and videos. No file contents are read.

Paths are relative to the share, so every watched folder's files sit in one folder tree: the share is the root folder
(''), with the watched folders, and any folders on the way to them, below it.

Folders ending in -Removed hold what cleanup removed (cleanup.removed_folder) and are never walked. A watched folder
that had files and now lists none, or hardly any, is taken to be offline (an unmounted drive or share looks like an
empty folder, and a file put there while it was gone must not make all the others look deleted), so its files are not
marked missing.
"""
import fnmatch
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from lineagebox import video
from lineagebox.catalog import under
from lineagebox.config import Config

# The same file time read on a Linux server (nanoseconds) and on a Windows PC over the network (100 ns) differs in the
# last digits; measured on the library LineageBox was developed on, over a hundred photos looked changed when the
# catalog moved from one to the other.
MTIME_SLACK = 0.001


@dataclass
class ScanStats:
    folders: int = 0
    files: int = 0
    new: int = 0
    changed: int = 0
    missing: int = 0
    errors: list[str] = field(default_factory=list)


def is_ignored(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(name.lower(), p.lower()) for p in patterns)


def _own_folders(cfg: Config) -> set[str]:
    """LineageBox's own folders as paths of the photo folders ('/' separated), when they are inside them: never
    scanned, or its previews, frames and face crops would become photos (settings.folder_path refuses to watch them)."""
    out = set()
    root = cfg.share_root.resolve()
    for own in (cfg.data_dir, cfg.models_dir):
        try:
            out.add(Path(own).resolve().relative_to(root).as_posix())
        except ValueError:  # elsewhere, as it should be
            pass
    return out


def _own_ids(cfg: Config) -> set[tuple[int, int]]:
    """The same folders as (device, inode). In Docker the catalog is /data, and the folder holding it can be mounted as
    a photo folder too (PHOTOS set to one that holds everything): there it has another path, but is the same folder."""
    out = set()
    for own in (cfg.data_dir, cfg.models_dir):
        try:
            st = os.stat(own)
        except OSError:
            continue
        if st.st_ino:  # a file system without inode numbers can't tell folders apart this way
            out.add((st.st_dev, st.st_ino))
    return out


def _is_own(entry: os.DirEntry, ids: set[tuple[int, int]]) -> bool:
    if not ids or entry.inode() not in {ino for _, ino in ids}:  # inode() is free on Linux, so only a match costs a stat
        return False
    st = os.stat(entry.path, follow_symlinks=False)
    return (st.st_dev, st.st_ino) in ids


def scan(con: sqlite3.Connection, cfg: Config) -> ScanStats:
    if not cfg.share_root.is_dir():
        raise FileNotFoundError(f"the photo folders can't be reached: {cfg.share_root}")
    if not cfg.folders:
        raise ValueError("no folders are watched")
    now = datetime.now().isoformat(timespec="seconds")
    stats = ScanStats()
    # Walk first, then write: a walk of a slow disk takes minutes, and writing as it went held the database (and
    # every change made on the web pages) for all of that time.
    found_folders = [("", None, "", False)]  # (path with '/' on every system, parent path, name, ignored)
    own = _own_folders(cfg)  # the catalog's and the face models' folders, if they are in the photo folders
    own_ids = _own_ids(cfg)
    found_files = []                         # (path, folder path, size, mtime)
    wanted = {*cfg.image_extensions, *cfg.video_extensions}
    for watched in cfg.folders:
        parent, parts = "", watched.split("/")
        for depth in range(1, len(parts)):  # folders between the share and the watched folder
            found_folders.append(("/".join(parts[:depth]), parent, parts[depth - 1], False))
            parent = "/".join(parts[:depth])
        if not (cfg.share_root / watched).is_dir():
            stats.errors.append(f"{watched}: not found")
            continue
        before = len(found_files)
        stack = [(watched, parent, False)]  # (path, parent path, parent ignored)
        while stack:
            rel, parent, parent_ignored = stack.pop()
            name = rel.rpartition("/")[2]
            ignored = parent_ignored or (rel != watched and is_ignored(name, cfg.ignore_dirs))
            found_folders.append((rel, parent, name, ignored))
            stats.folders += 1
            try:
                entries = list(os.scandir(cfg.share_root / rel))
            except OSError as e:
                stats.errors.append(f"{rel}: {e}")
                continue
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if (not entry.name.lower().endswith("-removed") and f"{rel}/{entry.name}" not in own
                                and not _is_own(entry, own_ids)):
                            stack.append((f"{rel}/{entry.name}", rel, ignored))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    if os.path.splitext(entry.name)[1].lower() not in wanted or video.is_copy(entry.name):
                        continue  # not a photo or video, or the 720p copy LineageBox made of one
                    st = entry.stat()
                except OSError as e:
                    stats.errors.append(f"{entry.path}: {e}")
                    continue
                stats.files += 1
                found_files.append((f"{rel}/{entry.name}", rel, st.st_size, st.st_mtime))
        found = len(found_files) - before
        if looks_offline(found, known := _known_files(con, watched)):
            seen = (f"only {found:,} of the {known:,} photos and videos it had" if found
                    else f"no photos or videos found, but {known:,} were there before")
            stats.errors.append(f"{watched}: {seen}. Is its disk or share connected? Nothing is marked missing until "
                                "it is back.")

    folders = {r["path"]: r for r in con.execute("SELECT id, path, ignored, source FROM folder")}
    files = {r["path"]: r for r in con.execute("SELECT id, path, size, mtime FROM file")}
    folder_ids, seen = {}, []
    for path, parent, name, ignored in found_folders:  # parents come before their folders
        folder_ids[path] = _upsert_folder(con, folders, path, folder_ids.get(parent), name, ignored)
    for path, folder, size, mtime in found_files:
        row = files.get(path)
        if row is None:
            seen.append(con.execute("INSERT INTO file(path, folder_id, size, mtime, last_seen) VALUES (?, ?, ?, ?, ?)",
                                    (path, folder_ids[folder], size, mtime, now)).lastrowid)
            stats.new += 1
            continue
        seen.append(row["id"])
        if row["size"] != size or abs(row["mtime"] - mtime) > MTIME_SLACK:
            con.execute("""UPDATE file SET size = ?, mtime = ?, folder_id = ?, image_id = NULL, error = NULL
                           WHERE id = ?""", (size, mtime, folder_ids[folder], row["id"]))
            stats.changed += 1

    con.execute("CREATE TEMP TABLE IF NOT EXISTS scan_seen (id INTEGER PRIMARY KEY)")
    con.execute("DELETE FROM scan_seen")
    con.executemany("INSERT INTO scan_seen VALUES (?)", ((i,) for i in seen))
    # A file missing or removed that is back at its path (put back by hand) is simply in the library again. Only those
    # rows are written: rewriting every row each pass, for when it was last seen, wrote megabytes for nothing.
    con.execute("""UPDATE file SET last_seen = ?, missing = 0, removed_at = NULL, removed_path = NULL,
                   removed_reason = NULL, removed_batch = NULL
                   WHERE id IN (SELECT id FROM scan_seen) AND (missing OR removed_at IS NOT NULL)""", (now,))
    # A folder we could not list would make its files look deleted, so only mark missing after a clean walk. Files of
    # a folder that is no longer watched go missing too: they leave the catalog's pages, and come back if it is
    # watched again.
    if not stats.errors:
        stats.missing = con.execute("""UPDATE file SET missing = 1
                                       WHERE missing = 0 AND id NOT IN (SELECT id FROM scan_seen)""").rowcount
    con.commit()
    return stats


def looks_offline(found: int, known: int) -> bool:
    """A watched folder whose disk or share is gone: none of the files it had, or under a tenth of many."""
    return known > 0 and (found == 0 or (known >= 50 and found < known // 10))


def reachable(con: sqlite3.Connection, cfg: Config, watched: str) -> bool:
    """Whether a watched folder that had files can be written to now: an unmounted drive or share is an empty folder,
    and what is written there is hidden once it is mounted again."""
    try:
        with os.scandir(cfg.share_root / watched) as entries:
            empty = next(entries, None) is None
    except OSError:
        return False
    return not (empty and _known_files(con, watched))


def _known_files(con, folder: str) -> int:
    return con.execute("SELECT count(*) FROM file WHERE NOT missing AND path LIKE ? ESCAPE '!'",
                       (under(folder),)).fetchone()[0]


def _upsert_folder(con, folders: dict, path, parent_id, name, ignored) -> int:
    row = folders.get(path)
    if row is None:
        folder_id = con.execute("INSERT INTO folder(path, parent_id, name, ignored) VALUES (?, ?, ?, ?)",
                                (path, parent_id, name, int(ignored))).lastrowid
        folders[path] = {"id": folder_id, "path": path, "ignored": int(ignored), "source": "auto"}
        return folder_id
    if row["source"] != "user" and bool(row["ignored"]) != ignored:
        con.execute("UPDATE folder SET ignored = ? WHERE id = ?", (int(ignored), row["id"]))
    return row["id"]
