"""Photos and videos family members upload from the browser, into their own uploads folder in the photo folders.

Each member's folder is <watched folder>/<uploads folder>/<their person's id> (accounts.upload_folder), so the worker
finds the files like any others. A file comes as the body of one request and is written as it arrives, as
.<name>.part beside where it goes, and renamed once complete: the scan only takes known photo and video
extensions, so a half-written file is never read. Names are cleaned part by part (no folders above, no characters
Windows can't have, nothing hidden, no folder the scan skips), only photo and video types the catalog reads are
taken, their first bytes must look like that type (an .html file named .jpg is refused), and a name already there gets
" (2)".
"""
import os
import re
import shutil
import time
import zipfile
from pathlib import Path

from lineagebox import errors
from lineagebox.scan import is_ignored

MARGIN = 2 * 2**30  # free space the photo storage keeps after an upload
CHUNK = 1 << 20
ROOM_EVERY = 64 * CHUNK  # how often the free space is looked at again while a file is written
NAME_BYTES = 200  # the longest name kept, in bytes as file systems count them (most allow 255)
FULL = "The photo storage is nearly full: nothing more can be uploaded for now."
_BAD = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
# what the first bytes of each kind of file look like: (offset, bytes) any of which will do
_SIGNATURES = {
    ".jpg": [(0, b"\xff\xd8\xff")], ".jpeg": [(0, b"\xff\xd8\xff")], ".png": [(0, b"\x89PNG\r\n\x1a\n")],
    # TIFF, and BigTIFF (over 4 GB: big scans)
    **{ext: [(0, b"II*\x00"), (0, b"MM\x00*"), (0, b"II+\x00"), (0, b"MM\x00+")] for ext in (".tif", ".tiff")},
    ".heic": [(4, b"ftyp")], ".heif": [(4, b"ftyp")], ".hif": [(4, b"ftyp")], ".cr3": [(4, b"ftyp")],
    ".orf": [(0, b"IIRO"), (0, b"IIRS"), (0, b"MMOR")], ".rw2": [(0, b"IIU\x00")], ".rwl": [(0, b"IIU\x00")],
    ".raf": [(0, b"FUJIFILM")], ".mrw": [(0, b"\x00MRM")], ".crw": [(6, b"HEAPCCDR")],
    ".iiq": [(0, b"II*\x00"), (0, b"MM\x00*"), (0, b"IIII")],
    # the other raw files are TIFF files
    **{ext: [(0, b"II*\x00"), (0, b"MM\x00*")] for ext in (".nef", ".nrw", ".cr2", ".arw", ".srf", ".sr2", ".dng",
                                                         ".pef", ".srw", ".3fr", ".mos", ".mef", ".erf", ".kdc",
                                                         ".dcr")},
    ".mp4": [(4, b"ftyp")], ".m4v": [(4, b"ftyp")], ".mov": [(4, b"ftyp"), (4, b"moov"), (4, b"wide"), (4, b"mdat"),
                                                             (4, b"free"), (4, b"skip"), (4, b"pnot")],
    ".3gp": [(4, b"ftyp")], ".3g2": [(4, b"ftyp")], ".mts": [(4, b"\x47")], ".m2ts": [(4, b"\x47")],
    ".avi": [(0, b"RIFF")], ".wmv": [(0, b"\x30\x26\xb2\x75\x8e\x66\xcf\x11")], ".mkv": [(0, b"\x1a\x45\xdf\xa3")],
    ".mpg": [(0, b"\x00\x00\x01\xba"), (0, b"\x00\x00\x01\xb3")], ".mpeg": [(0, b"\x00\x00\x01\xba"),
                                                                            (0, b"\x00\x00\x01\xb3")],
}


class Problem(errors.Problem):
    """An upload that can't be taken; the message is shown next to the file."""


class TooBig(Problem):
    status = 413


def clean_parts(text: str) -> list[str]:
    """A folder or file path as typed or dropped, as safe names: no '..', nothing hidden, no characters Windows
    can't have in a name, each shortened to NAME_BYTES with its extension kept."""
    out = []
    for part in re.split(r"[\\/]+", text):
        if part.strip().startswith(".") and part.strip() != ".":  # "..": a folder above; ".x": a hidden file
            raise Problem("A name can't start with a dot.")
        part = _BAD.sub("", part).strip().rstrip(". ")
        if not part:
            continue
        if part.split(".")[0].lower() in _RESERVED:
            part = f"_{part}"
        out.append(_shorten(part))
    return out


def _shorten(name: str) -> str:
    if len(name.encode()) <= NAME_BYTES:
        return name
    stem, suffix = os.path.splitext(name)
    if len(suffix.encode()) > 16:  # not an extension
        stem, suffix = name, ""
    return stem.encode()[:NAME_BYTES - len(suffix.encode())].decode(errors="ignore").rstrip(". ") + suffix


def destination(share_root: Path, upload_folder: str, folder: str, path: str, extensions,
                skipped: tuple[str, ...] = ()) -> tuple[Path, str]:
    """Where an uploaded file goes: (the path on the share, the same from the share's root, '/' separated). folder:
    the name the uploader typed for this lot (may be empty); path: the file's own path as picked or dropped. A folder
    the scan would skip (named like one of `skipped`, the ignore_dirs setting, or ending in -Removed) is refused."""
    parts = clean_parts(folder) + clean_parts(path)
    if not parts:
        raise Problem("The file has no name.")
    if Path(parts[-1]).suffix.lower() not in extensions:
        raise Problem(f"{parts[-1]}: only photos and videos ({', '.join(sorted(extensions))}).")
    for part in parts[:-1]:
        if is_ignored(part, skipped) or part.lower().endswith("-removed"):
            raise Problem(f"Photos in a folder called {part} are never shown here: use another name for it.")
    root = (share_root / upload_folder).resolve()
    target = root.joinpath(*parts).resolve()
    if root not in target.parents:
        raise Problem("That file can't go there.")
    target = free_name(target)
    return target, f"{upload_folder}/{target.relative_to(root).as_posix()}"


def free_name(target: Path) -> Path:
    """target, or "name (2).jpg" and so on when it is taken."""
    n, found = 1, target
    while found.exists() or found.with_name(f".{found.name}.part").exists():
        n += 1
        found = target.with_name(f"{target.stem} ({n}){target.suffix}")
    return found


def looks_like(name: str, head: bytes) -> bool:
    return any(head[at:at + len(sig)] == sig for at, sig in _SIGNATURES.get(Path(name).suffix.lower(), []))


def _room(folder: Path, need: int) -> None:
    if shutil.disk_usage(folder).free < need + MARGIN:
        raise Problem(FULL)


async def receive(chunks, target: Path, most: int, expected: int = 0) -> int:
    """Write the chunks (an async iterator of bytes) to target, through .<name>.part. Returns the size; raises
    Problem (nothing kept) when it is too big, not the type its name says, or the disk it goes on is too full for
    `expected` bytes (the size the browser gave, if any) and MARGIN, then or while it is written."""
    target.parent.mkdir(parents=True, exist_ok=True)
    _room(target.parent, expected)
    part = target.with_name(f".{target.name}.part")
    size, looked, head, checked = 0, 0, b"", False
    try:
        with open(part, "xb") as f:
            async for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > most:
                    raise TooBig(f"{target.name} is over {most // 2**30} GB, the most one upload may be.")
                if not checked:
                    head += chunk[:64]
                    if len(head) >= 16:
                        if not looks_like(target.name, head):
                            raise Problem(f"{target.name} isn't a {target.suffix.lstrip('.').upper()} file.")
                        checked = True
                f.write(chunk)
                if size - looked >= ROOM_EVERY:
                    _room(part.parent, 0)
                    looked = size
        if size == 0:
            raise Problem(f"{target.name} is empty.")
        if not checked and not looks_like(target.name, head):
            raise Problem(f"{target.name} isn't a {target.suffix.lstrip('.').upper()} file.")
        os.replace(part, target if not target.exists() else free_name(target))  # another came meanwhile: kept
        return size
    except BaseException:
        part.unlink(missing_ok=True)
        raise


class _Pipe:
    """What zipfile writes into: kept until taken, so the zip goes out as it is made."""

    def __init__(self):
        self.buf, self.pos = bytearray(), 0

    def write(self, data) -> int:
        self.buf += data
        self.pos += len(data)
        return len(data)

    def tell(self) -> int:
        return self.pos

    def flush(self) -> None:
        pass

    def take(self) -> bytes:
        out = bytes(self.buf)
        self.buf.clear()
        return out


def zip_stream(files, first: tuple[str, bytes] | None = None, zip64: bool = True):
    """A zip of (path, name in the zip) made as it is sent: stored, not compressed (photos and videos are already),
    ZIP64 so that videos over 4 GB fit (zip64 False: for genealogy programs, which may not read it). first: a file
    made in memory, (name, contents), put before the others. Files gone meanwhile are left out. A path that is bytes is
    a file made in memory too (a photo's XMP sidecar, beside it)."""
    pipe = _Pipe()
    with zipfile.ZipFile(pipe, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
        if first:
            z.writestr(first[0], first[1])
            yield pipe.take()
        for path, name in files:
            if isinstance(path, (bytes, bytearray)):
                z.writestr(zipfile.ZipInfo(name, date_time=time.localtime()[:6]), path)
                yield pipe.take()
                continue
            try:
                src = open(path, "rb")
            except OSError:
                continue
            with src:
                when = time.localtime(max(os.fstat(src.fileno()).st_mtime, 315532800))[:6]  # zip dates start in 1980
                with z.open(zipfile.ZipInfo(name, date_time=when), "w", force_zip64=zip64) as dst:
                    while chunk := src.read(CHUNK):
                        dst.write(chunk)
                        yield pipe.take()
            yield pipe.take()
    yield pipe.take()
