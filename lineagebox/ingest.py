"""Stage 2: read each pending file once, dedupe by SHA-256, extract metadata, write preview and thumbnail.

A video is read once to hash it (in chunks: some are 4 GB), then ffmpeg takes frames from it: one every 10 s or so,
leaving out blank and near-identical ones. The clearest frame becomes its preview and thumbnail, so every page shows
videos like photos; faces and descriptions use all the frames.
"""
import hashlib
import os
import sqlite3
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, fields, replace
from typing import Callable

from tqdm import tqdm

from lineagebox import dates, media, quality, video
from lineagebox.catalog import VISIBLE, under
from lineagebox.config import Config

STAGE_INGEST = 1
MAX_PHOTO_BYTES = 500 << 20  # a photo is read whole into memory, several at a time; no camera makes one this big
SAVE_EVERY, SAVE_SECONDS = 200, 3  # read files are recorded together, at most this many or this often
_FORMATS = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".nef": "nef"}
BLANK_FRAME = 5.0  # sharpness below this: a black, white or completely blurred frame (see quality.py)
SAME_FRAME = 4     # frames whose fingerprints are this close show the same thing; only the first is kept


@dataclass
class IngestStats:
    pending: int = 0
    processed: int = 0
    new_images: int = 0
    duplicates: int = 0
    decode_errors: int = 0
    read_errors: int = 0
    interrupted: bool = False
    stopped: bool = False  # stop() asked to (a setting was saved, ...); the rest is read on the next pass
    seconds: float = 0.0
    rebuilt: int = 0  # photos back on the share whose preview had been deleted (cleanup.drop_derivatives)
    no_ffmpeg: int = 0  # videos left for now: ffmpeg isn't there to read them


@dataclass
class _Result:
    file_id: int
    sha256: str | None = None
    image: dict | None = None  # columns for a new image row; None when the content is already known
    frames: list[tuple[int, float]] = field(default_factory=list)  # a new video's frames: (idx, seconds)
    read_error: str | None = None


def ingest(con: sqlite3.Connection, cfg: Config, limit: int | None = None, retry_errors: bool = False,
           show_progress: bool = True, stop: Callable[[], bool] | None = None,
           folder: str | None = None) -> IngestStats:
    """Read every pending file (only those under folder, if given), cfg.ingest_threads at a time. stop() is asked as
    files finish; once it says so, the files already being read are finished and recorded, and no more are started."""
    if retry_errors:  # e.g. after a decoder fix: forget undecodable images so their files are read again
        con.execute("UPDATE file SET image_id = NULL WHERE image_id IN (SELECT id FROM image WHERE error IS NOT NULL)")
        con.execute("DELETE FROM video_frame WHERE image_id IN (SELECT id FROM image WHERE error IS NOT NULL)")
        con.execute("DELETE FROM image WHERE error IS NOT NULL")
        con.commit()
    only = ""
    if folder:
        folder = folder.replace("\\", "/").strip("/")
        only = "AND f.path LIKE ? ESCAPE '!'"
    rows = con.execute(f"""SELECT f.id, f.path, f.mtime FROM file f JOIN folder d ON d.id = f.folder_id
                           WHERE f.image_id IS NULL AND f.missing = 0 AND d.ignored = 0 {only}
                           ORDER BY f.path""", (under(folder),) if folder else ()).fetchall()
    left = 0
    if not video.available():  # each video would be read whole for nothing: they wait until ffmpeg is installed
        kept = [r for r in rows if os.path.splitext(r["path"])[1].lower() not in cfg.video_extensions]
        rows, left = kept, len(rows) - len(kept)
    if limit is not None:
        rows = rows[:limit]
    stats = IngestStats(pending=len(rows), rebuilt=_rebuild(con, cfg, stop), no_ffmpeg=left)
    if not rows:
        return stats
    # Content hashes already in the catalog or being decoded by a worker. Claiming under the lock means each
    # unique image is decoded once even when copies of it are in flight on several threads at the same time.
    claimed = {r[0] for r in con.execute("SELECT sha256 FROM image")}
    lock = threading.Lock()
    waiting: dict[str, list[int]] = {}  # copies whose image row has not been recorded yet
    todo = iter(rows)
    window = cfg.ingest_threads * 4
    results: list[_Result] = []  # read, waiting to be saved
    saved_at = t0 = time.monotonic()
    # videos have their own few threads: a photo is read in a second, a video can take minutes
    with ThreadPoolExecutor(cfg.ingest_threads) as pool, ThreadPoolExecutor(cfg.video_threads) as videos, \
            tqdm(total=len(rows), unit="file", disable=not show_progress) as bar:
        running = set()
        try:
            while True:
                if not stats.stopped and stop is not None and stop():
                    stats.stopped = True
                while not stats.stopped and len(running) < window and (row := next(todo, None)) is not None:
                    if os.path.splitext(row["path"])[1].lower() in cfg.video_extensions:
                        running.add(videos.submit(_process_video, cfg, claimed, lock, row))
                    else:
                        running.add(pool.submit(_process, cfg, claimed, lock, row))
                if not running:
                    break
                done, running = wait(running, return_when=FIRST_COMPLETED)
                for fut in done:
                    results.append(fut.result())
                    bar.update()
                if len(results) >= SAVE_EVERY or time.monotonic() - saved_at >= SAVE_SECONDS:
                    if _save(con, results, waiting, stats):
                        results = []
                    saved_at = time.monotonic()
        except KeyboardInterrupt:
            stats.interrupted = True
            for fut in running:
                fut.cancel()
        finally:
            if results:
                _save(con, results, waiting, stats)  # if even this fails, those files are read again next time
    stats.seconds = time.monotonic() - t0
    return stats


def _save(con: sqlite3.Connection, results: list[_Result], waiting: dict[str, list[int]], stats: IngestStats) -> bool:
    """Record a batch of read files in one short transaction: reading holds no lock, so the web app never waits on
    a slow disk. False, with nothing recorded, if the database stayed locked: the caller keeps the batch."""
    waiting_before, stats_before = {k: list(v) for k, v in waiting.items()}, replace(stats)
    try:
        for r in results:
            _record(con, r, waiting, stats)
        con.commit()
        return True
    except sqlite3.OperationalError as e:
        con.rollback()
        if "locked" not in str(e):
            raise
        waiting.clear()
        waiting.update(waiting_before)
        for f in fields(stats):
            setattr(stats, f.name, getattr(stats_before, f.name))
        return False


def _process(cfg: Config, claimed: set[str], lock: threading.Lock, row) -> _Result:
    """Runs in a worker thread: no database access."""
    try:
        path = cfg.source_path(row["path"])
        if path.stat().st_size > MAX_PHOTO_BYTES:  # read whole into memory, on several threads at once
            return _Result(row["id"], read_error=f"over {MAX_PHOTO_BYTES >> 20} MB: too large to be read as a photo")
        data = path.read_bytes()
    except OSError as e:
        return _Result(row["id"], read_error=f"{type(e).__name__}: {e}")
    sha = hashlib.sha256(data).hexdigest()
    with lock:
        if sha in claimed:
            return _Result(row["id"], sha)
        claimed.add(sha)
    ext = os.path.splitext(row["path"])[1].lower()
    try:
        d = media.decode(data, ext, cfg.preview_size)
    except Exception as e:  # corrupt or unsupported content; recorded on the image so it is not retried
        return _Result(row["id"], sha, {"format": _FORMATS.get(ext, ext.lstrip(".")),
                                        "error": f"{type(e).__name__}: {e}"})
    # Write failures (disk full, permissions) are not caught: they should stop the run, not mark the image bad.
    thumb = media.save_derivatives(d.image, cfg.preview_path(sha), cfg.thumb_path(sha), cfg.thumb_size)
    taken_at, source = d.taken_at, "exif"
    if not taken_at:
        taken_at, source = dates.from_filename(os.path.basename(row["path"])), "filename"
    if not taken_at:
        taken_at, source = dates.from_mtime(row["mtime"]), "mtime"
    return _Result(row["id"], sha, {
        "format": d.format, "width": d.width, "height": d.height, "phash": media.phash(thumb),
        "taken_at": taken_at, "taken_at_source": source, "camera": d.camera,
        "gps_lat": d.gps[0] if d.gps else None, "gps_lon": d.gps[1] if d.gps else None,
    })


def _process_video(cfg: Config, claimed: set[str], lock: threading.Lock, row) -> _Result:
    """Runs in a worker thread: no database access."""
    path = cfg.source_path(row["path"])
    digest, head = hashlib.sha256(), bytearray()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(4 << 20):
                digest.update(chunk)
                if len(head) < video.HEAD_BYTES:
                    head += chunk[:video.HEAD_BYTES - len(head)]
    except OSError as e:
        return _Result(row["id"], read_error=f"{type(e).__name__}: {e}")
    sha = digest.hexdigest()
    with lock:
        if sha in claimed:
            return _Result(row["id"], sha)
        claimed.add(sha)
    ext = os.path.splitext(row["path"])[1].lower()
    try:
        p = video.probe(path)
        frames = _video_frames(cfg, path, p, sha)
    except (video.Unavailable, video.TimedOut) as e:  # nothing known to be wrong with it: read again next time
        with lock:
            claimed.discard(sha)
        return _Result(row["id"], read_error=f"video: {e}")
    except video.Error as e:  # damaged, or not a video ffmpeg knows: recorded on the image so it is not read again
        return _Result(row["id"], sha, {"format": ext.lstrip("."), "media": "video", "error": f"video: {e}"})
    taken_at, source = video.taken_at(p, bytes(head), os.path.basename(row["path"]))
    if not taken_at:
        taken_at, source = dates.from_mtime(row["mtime"]), "mtime"
    return _Result(row["id"], sha, {
        "format": ext.lstrip("."), "media": "video", "width": p.width, "height": p.height, "duration": p.duration,
        "codecs": p.codecs, "playable": "original" if video.plays_in_browsers(ext, p) else "needed",
        "taken_at": taken_at, "taken_at_source": source, "camera": p.camera,
        "gps_lat": p.gps[0] if p.gps else None, "gps_lon": p.gps[1] if p.gps else None,
    }, frames)


def _video_frames(cfg: Config, path, p: video.Probe, sha: str) -> list[tuple[int, float]]:
    """Take and save a video's frames; the clearest becomes its preview and thumbnail. Returns (idx, seconds)."""
    kept: list[tuple[float, int]] = []  # (seconds, fingerprint) of the frames saved
    first = poster = None
    for at in video.sample_times(p.duration):
        try:
            im = video.frame(path, at, cfg.preview_size, p)
        except video.Error:
            continue  # past the real end (durations are estimates), or a damaged stretch
        first = first or (at, im)
        sharp, fingerprint = quality.sharpness_of(im), media.phash(im)
        if sharp < BLANK_FRAME or any(_bits(fingerprint ^ k[1]) <= SAME_FRAME for k in kept):
            continue
        media._atomic_save(im, cfg.frame_path(sha, len(kept)), "JPEG", quality=85)
        if poster is None or sharp > poster[0]:
            poster = (sharp, im)
        kept.append((at, fingerprint))
    if not kept:  # only blank frames: keep one anyway, so the video can be seen and played
        if first is None:
            raise video.Error("no frame could be read")
        media._atomic_save(first[1], cfg.frame_path(sha, 0), "JPEG", quality=85)
        kept, poster = [(first[0], 0)], (0.0, first[1])
    media.save_derivatives(poster[1], cfg.preview_path(sha), cfg.thumb_path(sha), cfg.thumb_size)
    return [(idx, at) for idx, (at, _) in enumerate(kept)]


def _rebuild(con: sqlite3.Connection, cfg: Config, stop: Callable[[], bool] | None) -> int:
    """Make the preview, thumbnail and video frames again for photos that were gone (their files deleted, see
    cleanup.drop_derivatives) and are back on the share. Returns how many. A file that can't be read is tried again
    on the next pass."""
    rows = con.execute(f"""SELECT i.id, i.sha256, i.media, (SELECT min(f.path) FROM file f
                                                         WHERE f.image_id = i.id AND NOT f.missing) AS path
                           FROM image i WHERE NOT (i.stage & {STAGE_INGEST}) AND {VISIBLE}""").fetchall()
    done = 0
    for r in rows:
        if stop is not None and stop():
            break
        path = cfg.source_path(r["path"])
        try:
            if r["media"] == "video":
                p = video.probe(path)
                poster = None
                for idx, at in con.execute("SELECT idx, at FROM video_frame WHERE image_id = ? ORDER BY idx",
                                           (r["id"],)).fetchall():
                    im = video.frame(path, at, cfg.preview_size, p)
                    media._atomic_save(im, cfg.frame_path(r["sha256"], idx), "JPEG", quality=85)
                    sharp = quality.sharpness_of(im)
                    if poster is None or sharp > poster[0]:
                        poster = (sharp, im)
                if poster is None:
                    raise video.Error("no frame could be read")
                media.save_derivatives(poster[1], cfg.preview_path(r["sha256"]), cfg.thumb_path(r["sha256"]),
                                       cfg.thumb_size)
            else:
                d = media.decode(path.read_bytes(), os.path.splitext(r["path"])[1].lower(), cfg.preview_size)
                media.save_derivatives(d.image, cfg.preview_path(r["sha256"]), cfg.thumb_path(r["sha256"]),
                                       cfg.thumb_size)
        except Exception:  # unreadable just now (a share hiccup, a file being copied): left for the next pass
            continue
        con.execute(f"UPDATE image SET stage = stage | {STAGE_INGEST} WHERE id = ?", (r["id"],))
        con.commit()
        done += 1
    return done


def _bits(x: int) -> int:
    return bin(x & 0xFFFFFFFFFFFFFFFF).count("1")


def _record(con: sqlite3.Connection, r: _Result, waiting: dict[str, list[int]], stats: IngestStats) -> None:
    stats.processed += 1
    if r.read_error:
        con.execute("UPDATE file SET error = ? WHERE id = ?", (r.read_error, r.file_id))
        stats.read_errors += 1
        return
    if r.image is None:
        stats.duplicates += 1
        found = con.execute("SELECT id FROM image WHERE sha256 = ?", (r.sha256,)).fetchone()
        if found is None:  # the copy that claimed this content is still being decoded
            waiting.setdefault(r.sha256, []).append(r.file_id)
            return
        _link(con, found[0], [r.file_id])
        return
    row = {"media": "photo", "width": None, "height": None, "phash": None, "taken_at": None, "taken_at_source": None,
           "camera": None, "gps_lat": None, "gps_lon": None, "duration": None, "codecs": None, "playable": None,
           "error": None, **r.image, "sha256": r.sha256, "stage": STAGE_INGEST}
    image_id = con.execute("""
        INSERT INTO image(sha256, media, format, width, height, phash, taken_at, taken_at_source, camera,
                          gps_lat, gps_lon, duration, codecs, playable, stage, error)
        VALUES (:sha256, :media, :format, :width, :height, :phash, :taken_at, :taken_at_source, :camera,
                :gps_lat, :gps_lon, :duration, :codecs, :playable, :stage, :error)""", row).lastrowid
    con.executemany("INSERT INTO video_frame(image_id, idx, at) VALUES (?, ?, ?)",
                    ((image_id, idx, at) for idx, at in r.frames))
    stats.new_images += 1
    stats.decode_errors += row["error"] is not None
    _link(con, image_id, [r.file_id, *waiting.pop(r.sha256, [])])


def _link(con: sqlite3.Connection, image_id: int, file_ids: list[int]) -> None:
    con.executemany("UPDATE file SET image_id = ?, error = NULL WHERE id = ?", ((image_id, f) for f in file_ids))
