"""Decode originals into upright RGB images plus metadata, and write thumbnails and previews."""
import contextlib
import io
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import imagehash
import pillow_heif
import rawpy
from PIL import ExifTags, Image, ImageFile

from lineagebox import dates

# A JPEG missing its last few bytes still holds nearly the whole picture; show it rather than drop it.
ImageFile.LOAD_TRUNCATED_IMAGES = True
# A JPEG is decoded at a reduced scale (draft), so even a huge one takes little memory; other kinds are decoded whole,
# and a small PNG can say it is 13,000 pixels square (2 GB to decode). Larger than this, they are refused.
MAX_FULL_PIXELS = 64_000_000

# HEIC (iPhones' photos since 2017, and some other phones' and cameras'), read by libheif. HEIF keeps a photo's turn
# apart from its EXIF: libheif turns the picture upright itself, and pillow-heif then gives the EXIF orientation as 1,
# so decode() never turns one twice. The small thumbnails, depth maps and other pictures inside aren't needed.
pillow_heif.register_heif_opener(thumbnails=False, depth_images=False, aux_images=False)
HEIF_EXTENSIONS = {".heic", ".heif", ".hif"}
# TIFF: what scanners and photo editors save. Browsers can't show it, so like every kind read here it is shown
# through the JPEG preview and thumbnail ingest makes; a file of several pages is shown by its first.
TIFF_EXTENSIONS = {".tif", ".tiff"}
# Cameras' raw files, read by LibRaw (rawpy): Nikon, Canon, Sony, Fujifilm, Olympus and OM System, Panasonic and
# Leica, Pentax, Samsung, DNG (Adobe's, which some cameras and phones write), Hasselblad, Phase One, Leaf, Mamiya,
# Minolta, Epson and Kodak.
RAW_EXTENSIONS = {".nef", ".nrw", ".cr2", ".cr3", ".crw", ".arw", ".srf", ".sr2", ".dng", ".raf", ".orf", ".rw2",
                  ".rwl", ".pef", ".srw", ".3fr", ".iiq", ".mos", ".mef", ".mrw", ".erf", ".kdc", ".dcr"}
RAW_FORMATS = {e.lstrip(".") for e in RAW_EXTENSIONS}  # image.format of a raw file: its extension (nef, cr2, ...)
# A raw file's embedded preview smaller than this (some cameras keep only a thumbnail) is not used: the raw data is
# turned into a picture instead, which takes seconds on a small board.
MIN_RAW_PREVIEW = 1000
# Pillow's name for JPEGs that embed a second image (Panasonic, Fuji); to everyone else they are JPEGs
_JPEG_FORMATS = {"JPEG", "MPO"}
_FORMAT_NAMES = {"HEIF": "heic"}  # as people know them: an iPhone's photos are HEIC files
_TIFF = (b"II*\x00", b"MM\x00*")
# Olympus and Panasonic raw files are TIFF files with a magic number of their own
_TIFF_LIKE = {b"IIRO": b"II*\x00", b"IIRS": b"II*\x00", b"MMOR": b"MM\x00*", b"IIU\x00": b"II*\x00"}
# LibRaw's turn of a raw file (sizes.flip) as the EXIF orientation that says the same
_FLIP = {0: 1, 3: 3, 5: 8, 6: 6}

# EXIF orientation -> the transpose that makes the image upright
_ORIENTATION = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}
_SERIAL = re.compile(r"^[0-9a-f]{16,}$", re.I)


@dataclass
class Decoded:
    image: Image.Image   # upright RGB, scaled to fit max_side
    format: str          # jpeg|png|heic, or a raw file's extension: nef|cr2|arw|dng|...
    width: int           # full size, upright
    height: int
    taken_at: str | None
    camera: str | None
    gps: tuple[float, float] | None


BIG_PIXELS = 20_000_000  # a picture this big, not a JPEG, is decoded only a couple at a time (_BIG)
_BIG = threading.BoundedSemaphore(2)


def decode(data: bytes, ext: str, max_side: int) -> Decoded:
    """Decode a file's bytes. JPEGs are decoded at a reduced DCT scale when that still covers max_side."""
    taken = None
    if ext.lower() in RAW_EXTENSIONS:
        im, exif, orientation, (full_w, full_h), taken = _raw(data)
        fmt = ext.lower().lstrip(".")
    else:
        im = Image.open(io.BytesIO(data))
        exif = im.getexif()
        orientation = exif.get(ExifTags.Base.Orientation, 1)
        fmt = "jpeg" if im.format in _JPEG_FORMATS else (_FORMAT_NAMES.get(im.format) or im.format
                                                         or ext.lstrip(".")).lower()
        if im.format not in _JPEG_FORMATS and im.width * im.height > MAX_FULL_PIXELS:
            raise ValueError(f"{im.width} x {im.height} pixels: too large a {fmt.upper()} to read")
        full_w, full_h = im.size
    # a big scan or PNG is decoded whole, then copied as its colours are converted (a 64 MP TIFF took 0.5-0.7 GB):
    # only a couple at once, so a folder of them can't use up a small box's memory with ingest's threads
    big = im.format is not None and im.format not in _JPEG_FORMATS and im.width * im.height > BIG_PIXELS
    with _BIG if big else contextlib.nullcontext():
        if im.format == "TIFF":  # Pillow turns a TIFF upright as it reads it, then drops its orientation: not twice
            im.load()
            orientation = im.getexif().get(ExifTags.Base.Orientation, 1)
            full_w, full_h = im.size
        if im.format in _JPEG_FORMATS:
            im.draft("RGB", _fit(im.size, max_side))
        im = _to_rgb(im)
        if max(im.size) > max_side:
            im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    if op := _ORIENTATION.get(orientation):
        im = im.transpose(op)
        if orientation >= 5:
            full_w, full_h = full_h, full_w
    return Decoded(im, fmt, full_w, full_h, exif_taken_at(exif) or taken, camera_name(exif), gps_coords(exif))


def _raw(data: bytes):
    """A raw file's picture, EXIF, the orientation that turns the picture upright, its full size before that turn,
    and LibRaw's own date for it (for files whose EXIF isn't read here).

    The camera's embedded preview is used (full size on many cameras), unless it is only a thumbnail: then the raw
    data is turned into a picture at half size, which LibRaw turns upright itself. The full size is the sensor's,
    not the preview's, so a raw file is never taken for a smaller copy of its JPEG (Cleanup → Similar shots)."""
    exif = raw_exif(data)
    with rawpy.imread(io.BytesIO(data)) as raw:
        when = _local_time(raw.other.timestamp)
        flip = _FLIP.get(raw.sizes.flip, 1)
        size = raw.sizes.width, raw.sizes.height  # as stored, before the camera's turn
        try:
            thumb = raw.extract_thumb()
        except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
            thumb = None
        im = None
        if thumb is not None and thumb.format == rawpy.ThumbFormat.JPEG:
            try:
                im = Image.open(io.BytesIO(thumb.data))
            except (OSError, ValueError):  # a preview that can't be read: the raw data is turned into a picture
                thumb = None
        if im is not None:
            if not len(exif):  # a kind of raw file whose own EXIF isn't read here: its preview mostly has it
                exif = im.getexif()
        elif thumb is not None:
            im = Image.fromarray(thumb.data)
        if im is not None and max(im.size) >= MIN_RAW_PREVIEW:
            return im, exif, exif.get(ExifTags.Base.Orientation) or flip, size, when
        upright = size[::-1] if flip >= 5 else size
        return Image.fromarray(raw.postprocess(half_size=True, use_camera_wb=True)), exif, 1, upright, when


def raw_exif(data: bytes):
    """A raw file's EXIF, from wherever its kind keeps it: most are TIFF files (or TIFF with another magic number,
    Olympus and Panasonic); Canon's CR3 keeps it in boxes of its own; Fujifilm's RAF in the JPEG inside. Empty when
    it can't be found or read."""
    try:
        if data[:4] in _TIFF:
            return _tiff_exif(data)
        if data[:4] in _TIFF_LIKE:
            return _tiff_exif(_TIFF_LIKE[data[:4]] + data[4:])
        if data[4:12] == b"ftypcrx ":
            return _cr3_exif(data)
        if data.startswith(b"FUJIFILM"):
            at, length = int.from_bytes(data[84:88], "big"), int.from_bytes(data[88:92], "big")
            return Image.open(io.BytesIO(data[at:at + length])).getexif()
    except Exception:  # an EXIF this can't follow: the date comes from LibRaw, the file name or the file's time
        pass
    return Image.Exif()


def _tiff_exif(data: bytes) -> Image.Exif:
    exif = Image.Exif()
    exif.load(data)
    return exif


class _Tags(dict):
    """EXIF kept in separate blocks (a CR3 file's main, Exif and GPS directories), read the way Image.Exif is."""

    def __init__(self, main: dict, ifds: dict[int, dict]):
        super().__init__(main)
        self.ifds = ifds

    def get_ifd(self, tag: int) -> dict:
        return self.ifds.get(tag, {})


def _cr3_exif(data: bytes) -> _Tags:
    """A CR3 file's EXIF: its boxes CMT1 (the main directory), CMT2 (Exif) and CMT4 (GPS) are each a small TIFF file,
    near the start of the file."""
    blocks = {}
    for name in (b"CMT1", b"CMT2", b"CMT4"):
        at = data.find(name, 0, 1 << 20)
        if at >= 4:
            blocks[name] = dict(_tiff_exif(data[at + 4:at - 4 + int.from_bytes(data[at - 4:at], "big")]))
    return _Tags(blocks.get(b"CMT1", {}), {ExifTags.IFD.Exif: blocks.get(b"CMT2", {}),
                                          ExifTags.IFD.GPSInfo: blocks.get(b"CMT4", {})})


def _local_time(timestamp) -> str | None:
    """LibRaw's date for a raw file (read from it as local time, and given as seconds since 1970), as ISO time."""
    try:
        return dates.from_parts(*datetime.fromtimestamp(timestamp).timetuple()[:6]) if timestamp else None
    except (OSError, OverflowError, ValueError):
        return None


def _fit(size: tuple[int, int], max_side: int) -> tuple[int, int]:
    scale = max_side / max(size)
    if scale >= 1:
        return size
    return math.ceil(size[0] * scale), math.ceil(size[1] * scale)


def _to_rgb(im: Image.Image) -> Image.Image:
    if im.mode.startswith("I;16") or im.mode in ("I", "F"):
        # greys of 16 bits or more (scanners' TIFFs): scaled to 8 bits by the range they are stored in, not cut off at
        # white: 16 bits by 257 (65535 is white), floats from 0-1, wider ones by their own brightest
        depth16 = im.mode.startswith("I;16")
        im = im.convert("F" if im.mode == "F" else "I")
        _, top = im.getextrema()
        if depth16 or 255 < top <= 65535:
            scale = 255 / 65535
        elif im.mode == "F" and top <= 1:
            scale = 255
        else:
            scale = 255 / top if top > 255 else 1
        return im.point(lambda v: v * scale).convert("L").convert("RGB")
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=im.getchannel("A"))
        return bg
    return im.convert("RGB")


def exif_taken_at(exif: Image.Exif) -> str | None:
    sub = exif.get_ifd(ExifTags.IFD.Exif)
    for value in (sub.get(ExifTags.Base.DateTimeOriginal), sub.get(ExifTags.Base.DateTimeDigitized),
                  exif.get(ExifTags.Base.DateTime)):
        if iso := dates.parse_exif(value):
            return iso
    return None


def _clean(value) -> str:
    if isinstance(value, bytes):
        value = value.decode("ascii", "ignore")
    words = str(value or "").replace("\x00", " ").split()
    return " ".join(w for w in words if not _SERIAL.match(w))


def camera_name(exif: Image.Exif) -> str | None:
    make, model = _clean(exif.get(ExifTags.Base.Make)), _clean(exif.get(ExifTags.Base.Model))
    if make and model.lower().startswith(make.split()[0].lower()):
        return model  # "NIKON CORPORATION" + "NIKON D7000" -> "NIKON D7000"
    return " ".join(p for p in (make, model) if p) or None


def gps_coords(exif: Image.Exif) -> tuple[float, float] | None:
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    try:
        lat = _degrees(gps[2]) * (-1 if _ref(gps.get(1)) == "S" else 1)
        lon = _degrees(gps[4]) * (-1 if _ref(gps.get(3)) == "W" else 1)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    if (lat == 0 and lon == 0) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return round(lat, 6), round(lon, 6)


def _degrees(dms) -> float:
    d, m, s = (float(x) for x in dms)
    return d + m / 60 + s / 3600


def _ref(value) -> str:
    return value.decode("ascii", "ignore") if isinstance(value, bytes) else str(value or "")


def phash(im: Image.Image) -> int:
    """64-bit perceptual hash as a signed int (SQLite integers are signed)."""
    h = int(str(imagehash.phash(im)), 16)
    return h - (1 << 64) if h >= 1 << 63 else h


def save_derivatives(im: Image.Image, preview_path: Path, thumb_path: Path, thumb_size: int) -> Image.Image:
    """Write the preview (im as-is) and a thumbnail; returns the thumbnail."""
    _atomic_save(im, preview_path, "JPEG", quality=85)
    thumb = im.copy()
    thumb.thumbnail((thumb_size, thumb_size), Image.Resampling.LANCZOS)
    _atomic_save(thumb, thumb_path, "WEBP", quality=80)
    return thumb


def _atomic_save(im: Image.Image, path: Path, fmt: str, **kwargs) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
    im.save(tmp, fmt, **kwargs)
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            # Windows refuses while another process (antivirus, search indexer) has either file open
            time.sleep(0.1 * (attempt + 1))
    if not path.exists():
        os.replace(tmp, path)  # still failing: let the error surface
    # Derivatives are named by content hash, so the existing file is identical to ours.
    tmp.unlink(missing_ok=True)
