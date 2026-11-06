"""How full the disks are that hold the photos and LineageBox's own files, for Home and the Status page.

Each disk is shown once, however many watched folders are on it (in Docker each photo source is its own mount, but
several are often folders of one disk): how big it is, what is free, and how much of it is the library's photos and
videos, the files Cleanup moved to the Removed folders (room that Empty gives back), LineageBox's previews, thumbnails
and catalog, and other files. The data folder holds tens of thousands of
small files, too many to add up while a page waits, so the worker measures it once a day (measure_data).
"""
import os
import shutil
from datetime import datetime
from pathlib import Path

from lineagebox import catalog, cleanup, settings
from lineagebox.config import Config

KEY = "storage"  # settings key: {"data_bytes", "measured_at"}, written by the worker
LOW_SHARE = 0.10  # a disk with less than this much of it free is running low...
LOW_BYTES = 20 * 2**30  # ...or with less than this left, whatever its size


def measure_data(con, cfg: Config) -> int:
    """Add up the data folder (previews, thumbnails, video frames, face crops, the catalog and its backups) and keep
    the total for the pages. Returns it."""
    total = _size(cfg.data_dir)
    settings.put(con, KEY, {"data_bytes": total, "measured_at": datetime.now().isoformat(timespec="seconds")})
    return total


def _size(folder: Path) -> int:
    total, todo = 0, [folder]
    while todo:
        try:
            with os.scandir(todo.pop()) as entries:
                for e in entries:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            todo.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:  # deleted while counted
                        continue
        except OSError:
            continue
    return total


def disks(con, cfg: Config) -> list[dict]:
    """The disks the watched folders and the data folder are on, the fullest first: total, used and free bytes, what
    it holds (photos and videos, removed files, LineageBox's own files, other files) and the watched folders on it.
    A watched folder's Removed folder is on its disk: cleanup moves files there by renaming them, which only works
    within one."""
    found: dict = {}

    def disk(path: Path) -> dict | None:
        try:
            device, usage = path.stat().st_dev, shutil.disk_usage(path)
        except OSError:  # a source that isn't mounted just now
            return None
        return found.setdefault(device, {"total": usage.total, "used": usage.used, "free": usage.free,
                                         "photos": 0, "removed": 0, "ours": None, "folders": []})

    for folder in cfg.folders:
        d = disk(cfg.share_root / folder)
        if d is not None:
            d["folders"].append(folder)
            d["photos"] += con.execute("SELECT COALESCE(SUM(size), 0) FROM main.file WHERE NOT missing "
                                       "AND path LIKE ? ESCAPE '!'", (catalog.under(folder),)).fetchone()[0]
            removed = catalog.under(cleanup.removed_folder(cfg, folder))
            d["removed"] += con.execute("SELECT COALESCE(SUM(size), 0) FROM main.file WHERE removed_at IS NOT NULL "
                                        "AND removed_path LIKE ? ESCAPE '!'", (removed,)).fetchone()[0]
    if (d := disk(cfg.data_dir)) is not None:
        d["ours"] = (settings.get(con, KEY) or {}).get("data_bytes")
        d["data"] = True
    out = []
    for d in found.values():
        d["other"] = max(0, d["used"] - d["photos"] - d["removed"] - (d["ours"] or 0))
        d["low"] = d["free"] < LOW_BYTES or d["total"] and d["free"] / d["total"] < LOW_SHARE
        d["share"] = d["used"] / d["total"] if d["total"] else 0
        # the parts of the bar, as shares of the disk (photos, removed, LineageBox's files, the rest of what is used)
        d["parts"] = [(key, min(1.0, n / d["total"]) if d["total"] else 0)
                      for key, n in (("photos", d["photos"]), ("removed", d["removed"]), ("ours", d["ours"] or 0),
                                     ("other", d["other"]))]
        out.append(d)
    return sorted(out, key=lambda d: -d["share"])


def fmt_size(n: int | None) -> str:
    """Bytes as people read them: 812 MB, 74.8 GB, 3.6 TB (by 1024s, as the upload limits are)."""
    if n is None:
        return ""
    for unit, size in (("TB", 2**40), ("GB", 2**30), ("MB", 2**20)):
        if n >= size:
            value = n / size
            return f"{value:,.1f} {unit}" if value < 100 else f"{value:,.0f} {unit}"
    return f"{n / 2**10:,.0f} KB"
