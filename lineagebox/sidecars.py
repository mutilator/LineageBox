"""XMP sidecars: what the family gave a photo, in a small file beside it that other programs read (digiKam, darktable,
ExifTool, Lightroom for raw files), so the names, captions and places outlive LineageBox and travel with the photos.

Off until an admin turns it on (Settings → Other programs): writing beside the photos changes the photo folders,
which LineageBox otherwise only reads. The photo files themselves are never changed for this; an admin can have them
written into a JPEG, TIFF or NEF on its page (writeback.write_family). Downloads (zips) and the export carry the same
sidecars whether this is on or not.

Only what people made is written (xmp.py says how each is written): names given to faces by hand (not those
recognition guessed), people added to a photo by hand, the family's caption, tags given by hand, a date set by hand
and a place put on the map by hand. A photo with none of those gets no sidecar, and the sidecar LineageBox wrote is
taken away once it has none.

The name is the photo's whole name with .xmp after it (IMG_1234.JPG.xmp), as digiKam and darktable write them. The
other convention, IMG_1234.xmp (Lightroom's, for raw files), would give a raw file and the JPEG the camera made with
it, or a Live Photo's still and its clip, one sidecar between them. digiKam reads both names; Lightroom reads
IMG_1234.xmp beside a raw file, and a JPEG's own XMP, which writing into the file gives it.

A sidecar another program made at that name is left as it is (darktable keeps its edits in one), and counted on the
Settings page. LineageBox knows its own by what it writes in them (lineagebox:Written), and keeps in the catalog what it
wrote (sidecar), so a sidecar is written again only when what it says changes. One another program has written into
since (darktable and digiKam keep what was in a sidecar when they add to it, LineageBox's mark too) is theirs from
then on (as_written).

They are kept up to date in the background, never by a request: the catalog itself notes which photos changed
(sidecar_stale, filled by triggers, 029), and the sidecars stage writes those, a few minutes at a time. The worker runs
it on each pass; in Docker the worker only reads the photos (compose.yaml), so the copies service, which already writes
720p copies beside the videos, runs it too, and a worker that finds the photo folders read-only leaves it to that. A
sidecar that can't be written just now (a share gone for a moment) is tried again later (retry.py). Cleanup moves a
photo's sidecar with it to the Removed folder and back (cleanup.py).
"""
import contextlib
import errno
import hashlib
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from lineagebox import retry, settings, xmp
from lineagebox.config import Config
from lineagebox.places import HandPlaces

SUFFIX = ".xmp"
STAGE = "sidecars"
SWEPT = "sidecars_swept"   # settings: when every photo was last queued, after sidecars were turned on
MINUTES = 10               # each pass writes sidecars for this long at most: a first pass over a big library takes several
OTHER = "other"            # sidecar.digest of one another program made
OWN_MARK = xmp.OURS.encode()
LOOK_BYTES = 1 << 16       # of a sidecar already there, the part looked through for LineageBox's mark


@dataclass
class Stats:
    written: int = 0
    removed: int = 0
    others: int = 0       # sidecars another program made, left as they are
    failed: int = 0
    left: int = 0
    read_only: bool = False  # the photo folders can't be written here (the worker's read-only mount in Docker)


def path_for(photo: Path) -> Path:
    """IMG_1234.JPG -> IMG_1234.JPG.xmp"""
    return photo.with_name(photo.name + SUFFIX)


def metadata(con: sqlite3.Connection, image_id: int, places: HandPlaces | None = None) -> xmp.Metadata | None:
    """What a photo's sidecar says, or None when people gave it nothing to write. On a member's connection, only the
    people they can see are named."""
    img = con.execute("""SELECT id, media, width, height, taken_at, taken_at_source, date_text, date_from, date_to,
                                live_of, gps_lat FROM image WHERE id = ?""", (image_id,)).fetchone()
    if img is None or img["live_of"] is not None:  # a Live Photo's clip: its still has the names
        return None
    meta = xmp.Metadata(width=img["width"], height=img["height"])
    for f in con.execute("""SELECT p.name, f.x1, f.y1, f.x2, f.y2, f.frame FROM face f JOIN person p ON p.id = f.person_id
                            WHERE f.image_id = ? AND f.assigned_by = 'user' ORDER BY f.x1, f.id""", (image_id,)):
        meta.people.append(f["name"])
        if f["frame"] is None and img["media"] == "photo" and None not in (f["x1"], f["y1"], f["x2"], f["y2"]):
            meta.faces.append((f["name"], f["x1"], f["y1"], f["x2"], f["y2"]))
    meta.people += [r[0] for r in con.execute("""SELECT p.name FROM image_person l JOIN person p ON p.id = l.person_id
                                                 WHERE l.image_id = ? ORDER BY p.name""", (image_id,))]
    meta.people = list(dict.fromkeys(meta.people))
    row = con.execute("SELECT text FROM comment WHERE image_id = ? AND kind = 'caption'", (image_id,)).fetchone()
    meta.caption = row[0] if row else None
    meta.tags = [r[0] for r in con.execute("""SELECT t.name FROM image_tag it JOIN tag t ON t.id = it.tag_id
                                              WHERE it.image_id = ? AND it.source = 'user' ORDER BY t.name""",
                                           (image_id,))]
    if img["taken_at_source"] == "user" and img["taken_at"]:
        meta.taken_at, meta.date_text = img["taken_at"], img["date_text"]
        meta.date_created = xmp.date_created(img["date_from"], img["date_to"], img["taken_at"])
    spot = (places or HandPlaces(con)).of(image_id)
    # a folder's place is for its photos without GPS of their own (places.py): the camera's stays, in the file
    if spot and (spot[2] == "photo" or img["gps_lat"] is None):
        meta.gps = (spot[0], spot[1])
    return None if meta.empty() else meta


def packet(con: sqlite3.Connection, image_id: int, places: HandPlaces | None = None) -> bytes | None:
    """A photo's sidecar as it would be written now, or None when it has nothing to say."""
    meta = metadata(con, image_id, places)
    return xmp.packet(meta) if meta else None


def _digest(meta: xmp.Metadata) -> str:
    """What a sidecar says, without when it was written."""
    return hashlib.sha256(xmp.packet(meta, written_at="-")).hexdigest()


OWN_KIT = b'x:xmptk="LineageBox'  # the packet's maker, as xmp.packet writes it


def as_written(path: Path) -> bool:
    """Whether the sidecar is still one LineageBox wrote. darktable and digiKam read a sidecar of the photo's name and
    write their own work into it, keeping what was there, LineageBox's mark too: one written since by another program
    is theirs now (darktable keeps a raw file's edits in it), never to be written over or deleted. Every XMP library
    (Exiv2 in darktable and digiKam, Adobe's, ExifTool) puts its own name in x:xmptk as it writes the packet, so that
    says, without the catalog's record, which could be behind (the service stopped between writing and recording)."""
    try:
        with open(path, "rb") as f:
            data = f.read(LOOK_BYTES)
    except OSError:
        return False
    return OWN_MARK in data and OWN_KIT in data


def _write(photo: Path, sidecar: Path, data: bytes) -> None:
    """Put the sidecar in place in one step, as the photo's owner's file (when running as root) with its permissions,
    less any execute bit. The name starts with a dot and ends in .part while it is written."""
    st = photo.stat()
    part = sidecar.with_name(f".lineagebox-new-{sidecar.name}.part")
    try:
        with open(part, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())  # a power cut must not leave an empty file in its place, taken for another program's
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            with contextlib.suppress(OSError):
                os.chown(part, st.st_uid, st.st_gid)
        with contextlib.suppress(OSError):  # a share mounted with fixed modes refuses this, and needs nothing
            os.chmod(part, st.st_mode & 0o666)
        os.replace(part, sidecar)
    except BaseException:
        with contextlib.suppress(OSError):
            part.unlink(missing_ok=True)
        raise


def write_image(con: sqlite3.Connection, cfg: Config, image_id: int, places: HandPlaces | None = None,
                stats: Stats | None = None) -> Stats:
    """Bring the sidecars of one photo's files up to date: written where they say something new, taken away where
    LineageBox's says nothing now, left alone where another program made one. Raises OSError when a file can't be
    written; what was done before it is recorded."""
    stats = stats or Stats()
    meta = metadata(con, image_id, places)
    data = xmp.packet(meta) if meta else None
    digest = _digest(meta) if meta else None
    rows = con.execute("""SELECT f.id, f.path, f.missing, f.removed_at, f.image_id, s.digest FROM file f
                          LEFT JOIN sidecar s ON s.file_id = f.id
                          WHERE f.image_id = :i OR f.id IN (SELECT file_id FROM sidecar WHERE image_id = :i)
                          ORDER BY f.id""", {"i": image_id}).fetchall()
    now = datetime.now().isoformat(timespec="seconds")
    for r in rows:
        photo = cfg.source_path(r["path"])
        sidecar = path_for(photo)
        if r["image_id"] != image_id:  # the file is another photo now: that photo's sidecar replaces this one
            continue
        if r["removed_at"]:  # in the Removed folder, with its sidecar (cleanup moved them together)
            continue
        if r["missing"]:  # deleted on the share, or in a folder no longer watched
            if r["digest"] is not None and not photo.exists():
                if r["digest"] != OTHER and as_written(sidecar):
                    sidecar.unlink(missing_ok=True)
                    stats.removed += 1
                con.execute("DELETE FROM sidecar WHERE file_id = ?", (r["id"],))
            continue
        there = sidecar.exists()
        if there and (r["digest"] == OTHER or not as_written(sidecar)):
            if r["digest"] != OTHER:
                con.execute("""INSERT OR REPLACE INTO sidecar(file_id, image_id, digest, written_at)
                               VALUES (?, ?, ?, ?)""", (r["id"], image_id, OTHER, now))
            stats.others += 1
            continue
        if data is None:
            if there:
                sidecar.unlink(missing_ok=True)
                stats.removed += 1
            con.execute("DELETE FROM sidecar WHERE file_id = ?", (r["id"],))
            continue
        if there and r["digest"] == digest:
            continue
        _write(photo, sidecar, data)
        con.execute("INSERT OR REPLACE INTO sidecar(file_id, image_id, digest, written_at) VALUES (?, ?, ?, ?)",
                    (r["id"], image_id, digest, now))
        stats.written += 1
    return stats


def queue_all(con: sqlite3.Connection) -> int:
    """Every photo that may need a sidecar, or has one: after sidecars are turned on, once."""
    n = con.execute("""INSERT OR IGNORE INTO sidecar_stale
                       SELECT image_id FROM face WHERE assigned_by = 'user'
                       UNION SELECT image_id FROM image_person
                       UNION SELECT image_id FROM comment WHERE kind = 'caption'
                       UNION SELECT image_id FROM image_tag WHERE source = 'user'
                       UNION SELECT id FROM image WHERE taken_at_source = 'user'
                       UNION SELECT image_id FROM image_place
                       UNION SELECT image_id FROM sidecar""").rowcount
    for (path,) in con.execute("SELECT d.path FROM folder_place p JOIN folder d ON d.id = p.folder_id").fetchall():
        like = path.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "/%"
        n += con.execute("""INSERT OR IGNORE INTO sidecar_stale SELECT f.image_id FROM file f
                            JOIN folder d ON d.id = f.folder_id
                            WHERE f.image_id IS NOT NULL AND (d.path = ? OR d.path LIKE ? ESCAPE '!')""",
                         (path, like)).rowcount
    return n


def run(con: sqlite3.Connection, cfg: Config, stop: Callable[[], bool] | None = None,
        minutes: float = MINUTES) -> Stats:
    """The worker's stage: write the sidecars of photos that changed, for `minutes` at most."""
    stats = Stats()
    if not cfg.sidecars_enabled:
        # the triggers note changes either way; turning sidecars on goes through every photo instead
        if con.execute("SELECT 1 FROM sidecar_stale LIMIT 1").fetchone() or settings.get(con, SWEPT):
            con.execute("DELETE FROM sidecar_stale")
            con.execute("DELETE FROM settings WHERE key = ?", (SWEPT,))
            con.commit()
        return stats
    if not settings.get(con, SWEPT):
        queue_all(con)
        settings.put(con, SWEPT, datetime.now().isoformat(timespec="seconds"))  # commits the queue with it
    until = time.monotonic() + minutes * 60
    places = HandPlaces(con)
    ids = [r[0] for r in con.execute(f"""SELECT image_id FROM sidecar_stale s
                                         WHERE {retry.waiting(STAGE, 's.image_id')} ORDER BY image_id DESC""")]
    refused = 0  # permission refusals in a row
    for n, image_id in enumerate(ids):
        if time.monotonic() > until or (stop is not None and stop()):
            stats.left = len(ids) - n
            break
        # taken off the list first: a change made while it is written puts it back on, for the next pass
        con.execute("DELETE FROM sidecar_stale WHERE image_id = ?", (image_id,))
        con.commit()
        try:
            write_image(con, cfg, image_id, places, stats)
            refused = 0
            retry.done(con, STAGE, [image_id])
            con.commit()
        except OSError as e:
            con.commit()  # what was written before it
            con.execute("INSERT OR IGNORE INTO sidecar_stale VALUES (?)", (image_id,))
            con.commit()
            refused = refused + 1 if e.errno in (errno.EACCES, errno.EPERM) else 0
            # the whole mount, or folders LineageBox's user may not write in (one refusal after another, nothing
            # written): nothing to try again photo by photo, one retry line each
            if e.errno == errno.EROFS or (refused >= REFUSALS and not stats.written):
                stats.read_only, stats.left = True, len(ids) - n
                break
            retry.later(con, STAGE, image_id, f"{type(e).__name__}: {e.strerror or e}")
            stats.failed += 1
    return stats


READ_ONLY = ("The photo folders are read-only here, or LineageBox may not write in them, so the XMP sidecars wait: "
             "let LineageBox write in them, or turn sidecars off (Settings → Other photo programs).")
REFUSALS = 20  # permission refused this many times in a row, with nothing written: the folders aren't LineageBox's


def summary(con: sqlite3.Connection) -> dict:
    """For the Settings page: sidecars written, others' left alone, photos waiting for theirs."""
    written, others = con.execute(f"""SELECT coalesce(sum(digest != '{OTHER}'), 0), coalesce(sum(digest = '{OTHER}'), 0)
                                      FROM sidecar""").fetchone()
    waiting = con.execute("SELECT count(*) FROM sidecar_stale").fetchone()[0]
    return {"written": written, "others": others, "waiting": waiting}


def for_files(con: sqlite3.Connection, image_ids) -> dict[int, bytes]:
    """image id -> its sidecar, for a zip of photos (on the downloader's own connection)."""
    places = HandPlaces(con)
    out = {}
    for image_id in image_ids:
        if (data := packet(con, image_id, places)) is not None:
            out[image_id] = data
    return out


def everything(con: sqlite3.Connection) -> Iterator[tuple[str, bytes]]:
    """(path from the media root + .xmp, sidecar) for every file of every photo with something to say: the export."""
    places = HandPlaces(con)
    queue = con.execute("""SELECT image_id FROM face WHERE assigned_by = 'user'
                           UNION SELECT image_id FROM image_person
                           UNION SELECT image_id FROM comment WHERE kind = 'caption'
                           UNION SELECT image_id FROM image_tag WHERE source = 'user'
                           UNION SELECT id FROM image WHERE taken_at_source = 'user'
                           UNION SELECT image_id FROM image_place
                           UNION SELECT DISTINCT f.image_id FROM file f WHERE f.image_id IS NOT NULL AND NOT f.missing
                             AND EXISTS (SELECT 1 FROM folder_place) ORDER BY 1""").fetchall()
    for (image_id,) in queue:
        data = packet(con, image_id, places)
        if data is None:
            continue
        for (path,) in con.execute("SELECT path FROM file WHERE image_id = ? AND NOT missing ORDER BY path",
                                   (image_id,)):
            yield path + SUFFIX, data


def move_along(cfg: Config, from_rel: str, to_rel: str, move: Callable[[Path, Path], None]) -> str | None:
    """Move a photo's sidecar (LineageBox's or another program's) with it: to the Removed folder, or back. A sidecar
    already at the other end stays. Returns the problem, if it couldn't be moved."""
    src, dst = path_for(cfg.share_root / from_rel), path_for(cfg.share_root / to_rel)
    if not src.is_file() or dst.exists():
        return None
    try:
        move(src, dst)
    except OSError as e:
        return f"{src.name}: {e.strerror or e}"
    return None
