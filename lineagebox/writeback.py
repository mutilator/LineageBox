"""Writing what people confirm into the photo files themselves, so that every other program shows the photo the same
way: a rotation, and when an admin asks on a photo's page, what the family gave it (write_family): the names on its
faces, the caption, tags given by hand, a date set by hand and a place put on the map by hand, as the photo's XMP
sidecar holds them (sidecars.py), plus the date and place in its EXIF, where most programs look for them.

ExifTool does the writing: it changes only the tags asked for and keeps everything else in the file, the camera's own
notes included, without compressing the picture again. Only JPEG, TIFF and NEF files are written; other programs read
these tags from them.

The catalog knows a photo by the SHA-256 of its bytes, so a written file has a new one. The photo's row takes it, keeping
its faces, names, tags and description, and its preview and thumbnail are made again under the new name. Every copy of
the photo on the share is written the same way, so the copies stay one photo.

A change is all or nothing: ExifTool's changed file is made first, without touching the original; then, under the
catalog's write lock (so nothing after it can be refused as busy), each copy is swapped for it, the original kept beside
it until the catalog has the change. Anything that fails puts the originals back, so trying again is always safe.
"""
import contextlib
import hashlib
import os
import shutil
import sqlite3
from pathlib import Path

import numpy as np

from lineagebox import errors, media, tools
from lineagebox.config import Config

WRITABLE = {"jpeg", "tiff", "nef"}


class Problem(errors.Problem):
    """A file that cannot be written; the message is shown to the person who asked."""
    status = 409


def exiftool() -> str | None:
    return shutil.which("exiftool")


def why_not(image: sqlite3.Row) -> str | None:
    """Why this photo's file can't be written, in words for the page; None when it can."""
    if image["media"] != "photo" or image["format"] not in WRITABLE:
        return f"{(image['format'] or 'this kind of').upper()} files can't be changed here, only JPEG, TIFF and NEF"
    if not exiftool():
        return "LineageBox needs ExifTool to change photo files, and it isn't installed here"
    return None


def write(con: sqlite3.Connection, cfg: Config, image_id: int, tags: list[str], turn: int = 0) -> str:
    """Write these ExifTool tag assignments ("-Orientation#=6") into every copy of the photo, then move the catalog over
    to the changed file. turn: the quarter turns clockwise (in degrees) this makes to the picture as shown, which its
    faces and preview are turned by. Returns the photo's new SHA-256. Raises Problem, with the files and the catalog
    unchanged."""
    image = con.execute("SELECT * FROM image WHERE id = ?", (image_id,)).fetchone()
    if image is None:
        raise Problem("No such photo.")
    if reason := why_not(image):
        raise Problem(reason)
    files = con.execute("SELECT id, path FROM file WHERE image_id = ? AND NOT missing ORDER BY id", (image_id,)).fetchall()
    if not files:
        raise Problem("Its file is no longer in the photo folders.")
    paths = {f["id"]: cfg.source_path(f["path"]) for f in files}
    changed = {fid: _changed(path, tags) for fid, path in paths.items()}  # nothing on the share is touched yet

    first = paths[files[0]["id"]]
    data = changed[files[0]["id"]]
    sha = hashlib.sha256(data).hexdigest()
    decoded = media.decode(data, os.path.splitext(first.name)[1].lower(), cfg.preview_size)
    thumb = media.save_derivatives(decoded.image, cfg.preview_path(sha), cfg.thumb_path(sha), cfg.thumb_size)
    if con.in_transaction:
        con.commit()
    try:
        con.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError:
        _drop_unused(con, cfg, sha)
        raise Problem("The catalog is busy just now. Try again in a minute.") from None
    swapped = []  # (the file, its original kept beside it)
    try:
        for f in files:
            path = paths[f["id"]]
            st = path.stat()
            swapped.append((path, _swap(path, changed[f["id"]], st)))
            same = hashlib.sha256(changed[f["id"]]).hexdigest() == sha
            # a copy that came out different is read again as a photo of its own on the next pass
            con.execute("UPDATE file SET size = ?, mtime = ?, image_id = CASE WHEN ? THEN image_id END WHERE id = ?",
                        (len(changed[f["id"]]), st.st_mtime, same, f["id"]))
        if other := con.execute("SELECT id FROM image WHERE sha256 = ? AND id != ?", (sha, image_id)).fetchone():
            # the changed file is byte for byte a photo already in the catalog: its copies join that one
            con.execute("UPDATE file SET image_id = ? WHERE image_id = ?", (other[0], image_id))
        else:
            con.execute("UPDATE image SET sha256 = ?, width = ?, height = ?, phash = ? WHERE id = ?",
                        (sha, decoded.width, decoded.height, media.phash(thumb), image_id))
        if turn:
            _turn_faces(con, image_id, turn)
        con.commit()
    except BaseException as e:
        con.rollback()
        for path, original in reversed(swapped):
            os.replace(original, path)
        _drop_unused(con, cfg, sha)
        if isinstance(e, OSError):
            raise Problem(f"The file could not be changed: {e.strerror or e}. Nothing was changed.") from None
        raise
    for _, original in swapped:
        original.unlink(missing_ok=True)
    if sha != image["sha256"]:
        for old in (cfg.preview_path(image["sha256"]), cfg.thumb_path(image["sha256"]),
                    cfg.sharp_thumb_path(image["sha256"])):
            old.unlink(missing_ok=True)
    return sha


def write_family(con: sqlite3.Connection, cfg: Config, image_id: int) -> str:
    """Write what the family gave a photo into its file (every copy), as its sidecar says it (sidecars.metadata):
    keywords and names are added to those already in the file, never duplicated; the caption and the face regions
    replace the file's, as people here have the last word; a date set by hand and a place put on the map by hand also
    go into the EXIF, which the camera wrote (the catalog keeps the camera's date). Returns the new SHA-256."""
    from lineagebox import sidecars, xmp  # sidecars reads the map's places, which need the settings

    meta = sidecars.metadata(con, image_id)
    if meta is None:
        raise Problem("There is nothing to write: no names given by hand, caption, tags, date or place by hand.")
    work = cfg.data_dir / "tmp"
    work.mkdir(parents=True, exist_ok=True)
    side, args = work / f"write-{image_id}-{os.getpid()}.xmp", work / f"write-{image_id}-{os.getpid()}.args"
    lines = []
    for k in dict.fromkeys([*meta.tags, *meta.people]):  # -= then += : added once, whatever the file had
        lines += [f"-XMP-dc:Subject-={k}", f"-XMP-dc:Subject+={k}"]
    for p in meta.people:
        lines += [f"-XMP-iptcExt:PersonInImage-={p}", f"-XMP-iptcExt:PersonInImage+={p}"]
    copied = []  # tags copied from the sidecar
    if meta.caption:
        copied.append("-XMP-dc:Description<XMP-dc:Description")  # copied from the sidecar: a caption may have lines
    if meta.faces:
        copied.append("-XMP-mwg-rs:RegionInfo<XMP-mwg-rs:RegionInfo")
    if meta.taken_at:
        copied += ["-XMP-exif:DateTimeOriginal<XMP-exif:DateTimeOriginal",
                   "-XMP-photoshop:DateCreated<XMP-photoshop:DateCreated"]
        lines.append(f"-EXIF:DateTimeOriginal={meta.taken_at[:19].replace('-', ':').replace('T', ' ')}")
    if meta.gps:
        lat, lon = meta.gps
        copied += ["-XMP-exif:GPSLatitude<XMP-exif:GPSLatitude", "-XMP-exif:GPSLongitude<XMP-exif:GPSLongitude"]
        lines += [f"-EXIF:GPSLatitude={abs(lat):.7f}", f"-EXIF:GPSLatitudeRef={'N' if lat >= 0 else 'S'}",
                  f"-EXIF:GPSLongitude={abs(lon):.7f}", f"-EXIF:GPSLongitudeRef={'E' if lon >= 0 else 'W'}"]
    if copied:  # never -tagsfromfile alone: with no tags named after it, ExifTool copies every tag of the sidecar,
        lines += ["-tagsfromfile", str(side), *copied]  # and its lists replace the file's keywords and people
    try:
        side.write_bytes(xmp.packet(meta))
        # through a file of arguments, in UTF-8: names with accents reach ExifTool as they are on every system
        args.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        return write(con, cfg, image_id, ["-@", str(args)])
    finally:
        side.unlink(missing_ok=True)
        args.unlink(missing_ok=True)


def _changed(path: Path, tags: list[str]) -> bytes:
    """The file's bytes with the tags written, from ExifTool's output: the file itself is not touched."""
    try:
        out = tools.run([exiftool(), "-q", "-q", *tags, "-o", "-", str(path)], timeout=300)
    except tools.Unavailable as e:
        raise Problem(f"The file could not be changed ({e}).") from None  # "exiftool could not be run: ..."
    except tools.TimedOut:
        raise Problem("The file could not be changed: ExifTool took more than 5 minutes.") from None
    except tools.Error as e:
        raise Problem(f"The file could not be changed: {e}") from None
    if not out:
        raise Problem("The file could not be changed: ExifTool wrote nothing.")
    return out


def _swap(path: Path, data: bytes, st: os.stat_result) -> Path:
    """Put data in path's place in one step, keeping the original beside it (returned). The names start with a dot and
    end in .part, which the scan never reads, should LineageBox be stopped half way."""
    new, original = (path.with_name(f".lineagebox-{what}-{path.name}.part") for what in ("new", "original"))
    try:
        with open(new, "wb") as f:
            f.write(data)
            f.flush()
            # on the disk before the original makes way, as that is deleted once the catalog has the change: a file
            # renamed into a free name may otherwise sit in memory a while, and a power cut would leave neither
            os.fsync(f.fileno())
        os.utime(new, ns=(st.st_atime_ns, st.st_mtime_ns))  # the file's own time, as ExifTool's -P keeps it
        if hasattr(os, "geteuid") and os.geteuid() == 0:  # as root: still the share's owner's file, as a 720p copy is
            with contextlib.suppress(OSError):
                os.chown(new, st.st_uid, st.st_gid)
        with contextlib.suppress(OSError):  # a share mounted with fixed modes refuses this, and needs nothing
            os.chmod(new, st.st_mode & 0o7777)
        os.replace(path, original)
    except BaseException:  # nothing swapped yet: only the new file to clear away
        with contextlib.suppress(OSError):
            new.unlink(missing_ok=True)
        raise
    try:
        os.replace(new, path)
    except BaseException:
        os.replace(original, path)
        with contextlib.suppress(OSError):
            new.unlink(missing_ok=True)
        raise
    return original


def _drop_unused(con: sqlite3.Connection, cfg: Config, sha: str) -> None:
    """The preview and thumbnail made for a change that didn't happen, unless another photo has those bytes."""
    if not con.execute("SELECT 1 FROM image WHERE sha256 = ?", (sha,)).fetchone():
        for made in (cfg.preview_path(sha), cfg.thumb_path(sha)):
            made.unlink(missing_ok=True)


def _turn_faces(con: sqlite3.Connection, image_id: int, turn: int) -> None:
    """Turn the faces' boxes and landmarks (fractions of the picture's width and height) with the picture."""
    def point(x, y):  # where (x, y) is after the turn, clockwise
        return {90: (1 - y, x), 180: (1 - x, 1 - y), 270: (y, 1 - x)}[turn]

    for f in con.execute("SELECT id, x1, y1, x2, y2, landmarks FROM face WHERE image_id = ?", (image_id,)).fetchall():
        (ax, ay), (bx, by) = point(f["x1"], f["y1"]), point(f["x2"], f["y2"])
        landmarks = f["landmarks"]
        if landmarks is not None:
            k = np.frombuffer(landmarks, np.float32).reshape(-1, 2)
            landmarks = np.array([point(x, y) for x, y in k], np.float32).tobytes()
        con.execute("UPDATE face SET x1 = ?, y1 = ?, x2 = ?, y2 = ?, landmarks = ? WHERE id = ?",
                    (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by), landmarks, f["id"]))

