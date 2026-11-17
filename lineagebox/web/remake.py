"""Pictures made again when they are asked for and found gone.

A photo's preview and thumbnail can go while the photo itself is fine: its folder moved, so for a while none of its
files was on the share and cleanup.drop_derivatives deleted them; a data disk lost and restored; a preview deleted by
hand. The worker makes them again on its next ingest (ingest._rebuild, and check.rebuild_derivatives once a day), but
the next ingest can be hours away behind the faces of a big new folder, and until then the photo shows as a broken
picture. So a thumbnail, sharp tile or preview asked for (routes/files.py) that isn't there is made again then and
there from the photo's original, when a file of it is on the share, and the photo's ingest bit is set, as _rebuild
would. A video's frames and a recording's picture take ffmpeg, minutes for a long tape: those are left to the worker,
with the ingest bit cleared so that its next ingest makes them.

Two at a time (MAKING), as sharp tiles are, each request waiting at most WAIT seconds for its turn: decoding an original
takes the router's CPU for a moment, and a page of photos asks for a screenful at once. A request that waited too long
gets a 404 for now and the picture is made the next time the page is shown. One that couldn't be read is not tried
again for AGAIN seconds, so that a damaged original isn't read whole on every view. Nothing is made while the disk is
nearly full (uploads.MARGIN).
"""
import shutil
import sqlite3
import threading
import time

from lineagebox import ingest
from lineagebox.catalog import ON_SHARE
from lineagebox.uploads import MARGIN

MAKING = threading.Semaphore(2)
WAIT = 2  # seconds
AGAIN = 600  # seconds before a photo that couldn't be made is tried again
_failed: dict[str, float] = {}  # sha256: when it last couldn't be made (time.monotonic)


def again(s, sha: str) -> bool:
    """Called when a picture of the photo with this sha256 was asked for and isn't there: its preview and thumbnail
    made again from its original, if a file of it is on the share; for a video or a recording, the worker asked to.
    True when the photo's preview and thumbnail are there now. s: the Site. The caller has checked that the viewer
    may see the photo."""
    cfg = s.cfg
    if (failed := _failed.get(sha)) is not None and time.monotonic() - failed < AGAIN:
        return False
    with s.own_con() as c:
        img = c.execute(f"SELECT i.id, i.media, i.stage FROM image i WHERE i.sha256 = ? AND {ON_SHARE}",
                        (sha,)).fetchone()
        paths = [r[0] for r in c.execute("SELECT path FROM file WHERE image_id = ? AND NOT missing ORDER BY path",
                                         (img["id"],))] if img else []
        frames = [r[0] for r in c.execute("SELECT idx FROM video_frame WHERE image_id = ?",
                                          (img["id"],))] if img else []
    if img is None:  # gone from the share too (removed, or a folder no longer watched): nothing to make it from
        return False
    if img["media"] != "photo":  # ffmpeg's work, minutes for a long tape: the worker's next ingest (ingest._rebuild)
        # only for a picture it has that is gone, not a frame asked for that the video never had
        gone = not _whole(cfg, sha) or not all(_has(cfg.frame_path(sha, i)) for i in frames)
        if gone and img["stage"] & ingest.STAGE_INGEST:
            _set_ingested(s, img["id"], False)
        return False
    if _whole(cfg, sha):  # made by another request just now
        return True
    if not MAKING.acquire(timeout=WAIT):  # others are being made: this one the next time it is asked for
        return False
    try:
        if _whole(cfg, sha):  # made while this waited
            return True
        if _room(cfg) < MARGIN:  # the room left goes to the photos themselves
            return False
        # the first copy still there: one may have moved since the last scan (the folder being sorted just now)
        path = next((p for p in map(cfg.source_path, paths) if p.is_file()), None)
        try:
            if path is None:
                raise FileNotFoundError("no file of it is on the share just now")
            ingest.remake_photo(cfg, sha, path)
        except Exception:  # damaged, unreadable, a full disk: as _rebuild, the worker tries again on its next pass
            if len(_failed) > 10_000:
                _failed.clear()
            _failed[sha] = time.monotonic()
            return False
    finally:
        MAKING.release()
    _failed.pop(sha, None)
    if not img["stage"] & ingest.STAGE_INGEST:
        _set_ingested(s, img["id"], True)
    return True


def _set_ingested(s, image_id: int, done: bool) -> None:
    """The photo's ingest bit: set once its pictures are made again; cleared to have the worker's ingest make them."""
    bit = ingest.STAGE_INGEST
    try:
        with s.own_con() as c:
            c.execute(f"UPDATE image SET stage = {f'stage | {bit}' if done else f'stage & ~{bit}'} WHERE id = ?",
                      (image_id,))
            c.commit()
    except sqlite3.OperationalError:  # the catalog busy: the worker's next ingest puts it right (and makes them again)
        pass


def _whole(cfg, sha: str) -> bool:
    return _has(cfg.preview_path(sha)) and _has(cfg.thumb_path(sha))


def _has(path) -> bool:
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def _room(cfg) -> int:
    try:
        return shutil.disk_usage(cfg.data_dir).free
    except OSError:
        return 0
