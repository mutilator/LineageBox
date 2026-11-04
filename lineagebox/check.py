"""Damaged files: originals a disk has spoilt, and previews or thumbnails that are gone (Cleanup → Damaged files).

A failing drive, or a copy made from one, can leave a photo cut short, with a blank (zero-filled) stretch where a
block couldn't be read, or with other bytes than were read when it was catalogued, while its size and date stay the
same, so the scan sees nothing. check() reads every file whole and records what is wrong with it:
- it can't be read;
- a JPEG or PNG is cut short (no end marker after its picture), or doesn't decode (JPEGs are decoded at an eighth
  of their size: every byte of the picture is still read, in a fraction of the time);
- a JPEG's picture data holds BLANK zero bytes in a row: a compressed picture never does, a lost disk block does;
- its bytes differ from those it was catalogued with (image.sha256), though its size and date didn't change.
Videos and raw files are checked by their bytes only. A file whose size or date changed is the scan's: it is read as
a new photo.

Previews, thumbnails and video frames come from the originals, so one that is gone (a catalog restored without some of
them) is made again on the worker's next pass: rebuild_derivatives() clears the photo's ingest bit, which the ingest
stage looks for (ingest._rebuild), as cleanup.drop_derivatives does.
"""
import hashlib
import io
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from PIL import Image

from lineagebox import retry, settings
from lineagebox.catalog import VISIBLE
from lineagebox.config import Config
from lineagebox.ingest import STAGE_INGEST

BLANK = 4096  # zero bytes in a row in a JPEG's picture data: a disk block that couldn't be read
WHOLE = 256 * 2**20  # photos up to this size are read into memory whole; anything else a PIECE at a time
PIECE = 2**20
OK_KEY = "damage_ok"  # settings: files someone said are fine as they are
COMMIT_EVERY = 50
_ZEROS = re.compile(rb"\x00{%d,}" % BLANK)


def problem(path: Path, sha256: str, fmt: str) -> str | None:
    """What is wrong with a catalogued file, in words, or None. Photos are read into memory to look at their insides;
    videos and raw files, which can be gigabytes, are hashed a piece at a time. Raises OSError when it can't be read:
    a disk that can't read a block, or a share that dropped meanwhile, which check() tells apart."""
    if fmt in ("jpeg", "png") and path.stat().st_size <= WHOLE:
        data = path.read_bytes()
        if found := _jpeg(data) if fmt == "jpeg" else _png(data):
            return found
        digest = hashlib.sha256(data).hexdigest()
    else:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while piece := f.read(PIECE):
                h.update(piece)
        digest = h.hexdigest()
    if digest != sha256:
        return "its bytes changed since it was catalogued, though its size and date didn't"
    return None


def _scan_data(data: bytes) -> tuple[int, int] | None:
    """Where a JPEG's main picture data is: (start, end) from its first scan to its end marker (end -1 when it has
    none). Segments are walked by their lengths, so the small picture in its EXIF is not taken for it."""
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:  # markers with no length
            i += 2
            continue
        start = i + 2 + int.from_bytes(data[i + 2:i + 4], "big")
        if marker == 0xDA:  # start of scan
            return start, data.find(b"\xff\xd9", start)
        i = start
    return None


def _jpeg(data: bytes) -> str | None:
    if not data.startswith(b"\xff\xd8"):
        return "isn't a JPEG any more"
    found = _scan_data(data)
    if found is None:
        return "is cut short, before its picture"
    start, end = found
    if end < 0:
        return "is cut short"
    if (zeros := _ZEROS.search(data, start, end)) is not None:
        return f"has a blank stretch of {len(zeros.group()) // 1024} KB in its picture, as a failing disk leaves"
    return _decodes(data, draft=True)


def _png(data: bytes) -> str | None:
    if b"IEND" not in data[-64:]:
        return "is cut short"
    return _decodes(data)


def _decodes(data: bytes, draft: bool = False) -> str | None:
    """Why the picture doesn't decode, or None. (Being cut short is told by the end marker: Pillow is set to show what
    there is of such a picture, media.py, process-wide, so it is not switched here while other threads decode.)"""
    try:
        with Image.open(io.BytesIO(data)) as im:
            if draft:
                im.draft("RGB", (max(1, im.width // 8), max(1, im.height // 8)))
            im.load()
    except Exception as e:
        return f"doesn't decode ({e})"
    return None


def check(con: sqlite3.Connection, cfg: Config, stop=None, every: bool = False, threads: int = 4,
          progress=None) -> tuple[int, int]:
    """Read and check files not checked yet (every: all of them again). Returns (checked, damaged among them).
    stop() is asked between files: what was checked is kept. progress(done, total) is told as it goes."""
    fine = set(settings.get(con, OK_KEY, []))
    rows = [r for r in con.execute(f"""SELECT f.id, f.path, f.size, f.mtime, i.sha256, i.format FROM file f
                                       JOIN image i ON i.id = f.image_id WHERE NOT f.missing AND i.error IS NULL
                                       {'' if every else 'AND f.checked_at IS NULL'}
                                       AND {retry.waiting("check", "f.id")} ORDER BY f.id""")
            if r["id"] not in fine]
    checked = damaged = 0

    def one(r):
        path = cfg.source_path(r["path"])
        try:
            st = path.stat()
        except OSError:
            return r, None, False  # gone: the scan marks it missing
        if st.st_size != r["size"] or abs(st.st_mtime - r["mtime"]) > 2:
            return r, None, False  # changed: the scan reads it as a new photo
        try:
            return r, problem(path, r["sha256"], r["format"]), True
        except OSError as e:
            return r, f"can't be read ({e.strerror or e})", None

    with ThreadPoolExecutor(max_workers=threads) as pool:
        for start in range(0, len(rows), COMMIT_EVERY):
            if stop is not None and stop():
                break
            # read first, then write in one short transaction: reading 50 videos takes minutes, and holding the write
            # lock that long would lock out the web app and the worker's other stages
            results = list(pool.map(one, rows[start:start + COMMIT_EVERY]))
            now = datetime.now().isoformat(timespec="seconds")
            unread = [(r, found) for r, found, done in results if done is None]
            again = {i for (i,) in con.execute("SELECT item_id FROM retry WHERE stage = 'check'")} if unread else set()
            for r, found in unread:  # damage only when it can't be read a second time, a while later
                if r["id"] in again:
                    results.append((r, found, True))
            for r, found, done in results:
                if done:
                    con.execute("UPDATE file SET checked_at = ?, damage = ? WHERE id = ?", (now, found, r["id"]))
                    checked += 1
                    damaged += found is not None
            retry.done(con, "check", [r["id"] for r, _, done in results if done])
            con.commit()
            for r, found in unread:
                if r["id"] not in again:
                    retry.later(con, "check", r["id"], found)
            if progress is not None:
                progress(min(start + COMMIT_EVERY, len(rows)), len(rows))
            if len(unread) > len(results) // 2:  # most of them: the share is away, not the files damaged
                break
    return checked, damaged


def missing_derivatives(con: sqlite3.Connection, cfg: Config) -> list[int]:
    """Photos and videos in the library whose preview, thumbnail or video frames are gone or empty."""
    frames = {}
    for image_id, idx in con.execute("SELECT image_id, idx FROM video_frame"):
        frames.setdefault(image_id, []).append(idx)
    out = []
    for r in con.execute(f"""SELECT i.id, i.sha256, i.media FROM image i WHERE i.stage & {STAGE_INGEST}
                             AND {VISIBLE}""").fetchall():
        paths = [cfg.preview_path(r["sha256"]), cfg.thumb_path(r["sha256"])]
        if r["media"] == "video":
            paths += [cfg.frame_path(r["sha256"], idx) for idx in frames.get(r["id"], [])]
        if any(not _has(p) for p in paths):
            out.append(r["id"])
    return out


def _has(path: Path) -> bool:
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def rebuild_derivatives(con: sqlite3.Connection, cfg: Config) -> int:
    """Have the worker's next ingest pass make again the previews, thumbnails and frames that are gone. Returns how
    many photos and videos."""
    ids = missing_derivatives(con, cfg)
    con.executemany(f"UPDATE image SET stage = stage & ~{STAGE_INGEST} WHERE id = ?", ((i,) for i in ids))
    con.commit()
    return len(ids)


def damaged(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Damaged files still on the share, with their photo and how many other good copies it has."""
    return con.execute("""SELECT f.id, f.path, f.damage, f.checked_at, f.image_id, i.sha256, i.media, i.taken_at,
                                 (SELECT count(*) FROM file o WHERE o.image_id = f.image_id AND o.id != f.id
                                  AND NOT o.missing AND o.damage IS NULL) AS good_copies
                          FROM file f JOIN image i ON i.id = f.image_id
                          WHERE f.damage IS NOT NULL AND NOT f.missing ORDER BY f.path""").fetchall()


def mark_fine(con: sqlite3.Connection, file_ids: list[int]) -> None:
    """Files someone looked at and found fine: not listed, nor checked again."""
    settings.put(con, OK_KEY, sorted({*settings.get(con, OK_KEY, []), *file_ids}))
    con.executemany("UPDATE file SET damage = NULL WHERE id = ?", ((f,) for f in file_ids))
    con.commit()


def progress(con: sqlite3.Connection) -> dict:
    """How far the checking is: files checked, of how many, and how many are damaged."""
    row = con.execute("""SELECT count(*), count(f.checked_at), count(f.damage) FROM file f JOIN image i ON i.id = f.image_id
                         WHERE NOT f.missing AND i.error IS NULL""").fetchone()
    return {"files": row[0], "checked": row[1], "damaged": row[2]}
