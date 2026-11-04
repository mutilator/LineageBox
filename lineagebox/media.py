"""Decode originals into upright RGB images plus metadata, and write thumbnails and previews."""
import io
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import imagehash
import rawpy
from PIL import ExifTags, Image, ImageFile

from lineagebox import dates

# A JPEG missing its last few bytes still holds nearly the whole picture; show it rather than drop it.
ImageFile.LOAD_TRUNCATED_IMAGES = True
# A JPEG is decoded at a reduced scale (draft), so even a huge one takes little memory; other kinds are decoded whole,
# and a small PNG can say it is 13,000 pixels square (2 GB to decode). Larger than this, they are refused.
MAX_FULL_PIXELS = 64_000_000

RAW_EXTENSIONS = {".nef"}
# Pillow's name for JPEGs that embed a second image (Panasonic, Fuji); to everyone else they are JPEGs
_JPEG_FORMATS = {"JPEG", "MPO"}

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
    format: str          # jpeg|png|nef
    width: int           # full size, upright
    height: int
    taken_at: str | None
    camera: str | None
    gps: tuple[float, float] | None


def decode(data: bytes, ext: str, max_side: int) -> Decoded:
    """Decode a file's bytes. JPEGs are decoded at a reduced DCT scale when that still covers max_side."""
    src = Image.open(io.BytesIO(data))  # for NEF this is the TIFF container, read for its EXIF
    exif = src.getexif()
    orientation = exif.get(ExifTags.Base.Orientation, 1)
    if ext.lower() in RAW_EXTENSIONS:
        im, orientation = _raw_image(data, orientation)
        fmt = "nef"
    else:
        im = src
        fmt = "jpeg" if src.format in _JPEG_FORMATS else (src.format or ext.lstrip(".")).lower()
        if src.format not in _JPEG_FORMATS and src.width * src.height > MAX_FULL_PIXELS:
            raise ValueError(f"{src.width} x {src.height} pixels: too large a {fmt.upper()} to read")
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
    return Decoded(im, fmt, full_w, full_h, exif_taken_at(exif), camera_name(exif), gps_coords(exif))


def _raw_image(data: bytes, orientation: int) -> tuple[Image.Image, int]:
    """Embedded camera preview (full size on many Nikon cameras); demosaic only when there is none."""
    with rawpy.imread(io.BytesIO(data)) as raw:
        try:
            thumb = raw.extract_thumb()
        except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
            thumb = None
        if thumb is not None and thumb.format == rawpy.ThumbFormat.JPEG:
            return Image.open(io.BytesIO(thumb.data)), orientation
        if thumb is not None:
            return Image.fromarray(thumb.data), orientation
        # libraw applies the camera rotation itself when demosaicing
        return Image.fromarray(raw.postprocess(half_size=True, use_camera_wb=True)), 1


def _fit(size: tuple[int, int], max_side: int) -> tuple[int, int]:
    scale = max_side / max(size)
    if scale >= 1:
        return size
    return math.ceil(size[0] * scale), math.ceil(size[1] * scale)


def _to_rgb(im: Image.Image) -> Image.Image:
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
