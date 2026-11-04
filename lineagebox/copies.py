"""720p copies of the videos browsers cannot play, next to the original on the share (00005.MTS.720p.mp4).

They are made by their own Docker service (`python -m lineagebox copies`), which can be held to a few CPUs (`cpus:` in a
compose.override.yaml), because on a small ARM board a copy takes about as long as the video lasts (about half the
hours of video in the library LineageBox was developed on need one), and the worker must not wait on that.

A copy is never a catalog entry of its own: the scan skips its name (video.is_copy), so it is in no folder, count,
duplicate list or similar-shot group. image.playable says what the web page plays:
- original: the file itself (H.264 and AAC in MP4/MOV);
- needed: a copy is still to be made; copy: one is next to one of the video's files; failed: <why> ffmpeg could not.
Cleanup moves a copy along with its video (see cleanup._move_copy).
"""
import sqlite3
import time
import traceback
from pathlib import Path
from typing import Callable

from lineagebox import retry, video
from lineagebox.catalog import VISIBLE
from lineagebox.config import Config

IDLE = 300  # seconds between looks for videos that need a copy
BATCH = 25


def present_files(con: sqlite3.Connection, image_id: int) -> list[str]:
    return [r[0] for r in con.execute("""SELECT path FROM file WHERE image_id = ? AND NOT missing
                                         ORDER BY length(path), path""", (image_id,))]


def find_copy(con: sqlite3.Connection, cfg: Config, image_id: int) -> Path | None:
    """The copy next to one of the video's files on the share, if there is one."""
    for rel in present_files(con, image_id):
        if (copy := video.copy_path(cfg.share_root / rel)).is_file():
            return copy
    return None


def check(con: sqlite3.Connection, cfg: Config) -> int:
    """Videos marked as having a copy whose copy is gone (deleted by hand) need one again. Returns how many. When the
    video itself can't be seen either, its disk or share is offline: it is left as it is."""
    gone = [r["id"] for r in con.execute(f"SELECT i.id FROM image i WHERE i.playable = 'copy' AND {VISIBLE}")
            if find_copy(con, cfg, r["id"]) is None
            and any((cfg.share_root / rel).is_file() for rel in present_files(con, r["id"]))]
    con.executemany("UPDATE image SET playable = 'needed' WHERE id = ?", ((i,) for i in gone))
    con.commit()
    return len(gone)


def count_pending(con: sqlite3.Connection) -> int:
    return con.execute(f"""SELECT count(*) FROM image i WHERE i.playable = 'needed' AND {VISIBLE}
                           AND {retry.waiting("copies")}""").fetchone()[0]


def make(con: sqlite3.Connection, cfg: Config, limit: int | None = None, stop: Callable[[], bool] | None = None,
         failed: Callable[[str], None] = print) -> int:
    """Make the copies that are needed, newest video first, one at a time. Returns how many were made. Each copy is
    printed (the container's log); failures also go to failed()."""
    rows = con.execute(f"""SELECT i.id, i.taken_at, i.duration FROM image i WHERE i.playable = 'needed' AND {VISIBLE}
                           AND {retry.waiting("copies")} ORDER BY i.id DESC""").fetchall()[:limit]
    made = 0
    for r in rows:
        if stop is not None and stop():
            break
        files = present_files(con, r["id"])
        if not files:
            continue
        target = video.copy_path(cfg.share_root / files[0])
        t0 = time.monotonic()
        try:
            video.make_copy(cfg.source_path(files[0]), r["taken_at"], cfg.copy_threads, target)
        except (video.Unavailable, video.TimedOut, OSError) as e:  # not the video: ffmpeg, the disk, the share
            retry.later(con, "copies", r["id"], str(e))
            failed(f"720p copy of {files[0]} waits for a later try: {e}")
            if isinstance(e, video.TimedOut):
                continue
            break  # ffmpeg isn't there, or the share or disk is away: the next videos would fail the same way
        except video.Error as e:  # ffmpeg can't read this one
            con.execute("UPDATE image SET playable = ? WHERE id = ?", (f"failed: {str(e)[:200]}", r["id"]))
            con.commit()
            failed(f"720p copy of {files[0]} failed: {e}")
            continue
        con.execute("UPDATE image SET playable = 'copy' WHERE id = ?", (r["id"],))
        retry.done(con, "copies", [r["id"]])
        con.commit()
        made += 1
        seconds = time.monotonic() - t0
        print(f"720p copy of {files[0]}: {seconds / 60:.1f} min"
              + (f" for {r['duration'] / 60:.1f} min of video" if r["duration"] else ""), flush=True)
    return made


def run_forever(con: sqlite3.Connection, base: Config) -> None:
    from lineagebox import settings
    from lineagebox.activity import log, note_problem

    log(con, "720p copies: started")
    while True:
        try:
            cfg = settings.current(con, base)
            if not video.available():  # the worker says so on the Status page too
                note_problem(con, "ffmpeg", video.MISSING)
                time.sleep(IDLE)
                continue
            check(con, cfg)
            t0 = time.monotonic()
            # in rounds of BATCH, so the Status page hears how it is going without one line per copy
            while made := make(con, cfg, limit=BATCH, failed=lambda m: log(con, m)):
                log(con, f"720p copies: made {made} in {(time.monotonic() - t0) / 60:.0f} min; "
                         f"{count_pending(con)} still to make")
                t0 = time.monotonic()
        except sqlite3.OperationalError:  # the database stayed busy; try again on the next round
            con.rollback()
        except Exception:
            con.rollback()
            print(traceback.format_exc(), flush=True)
            log(con, f"720p copies failed: {traceback.format_exc().strip().splitlines()[-1]}")
        time.sleep(IDLE)
