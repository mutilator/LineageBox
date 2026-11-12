import io
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import rawpy
from PIL import ExifTags, Image

from lineagebox import cleanup, media, uploads, writeback
from lineagebox.config import Config
from tests.conftest import write_jpeg

# A real Nikon raw file to decode, when you have one: LINEAGEBOX_SAMPLE_NEF=/path/to/DSC_0001.NEF
SAMPLE_NEF = Path(os.environ.get("LINEAGEBOX_SAMPLE_NEF") or "no-sample.nef")


def test_rotated_jpeg_is_upright_with_metadata(tmp_path):
    p = write_jpeg(tmp_path / "a.jpg", size=(60, 30), orientation=6, taken="2012:06:03 10:00:00",
                   make="NIKON CORPORATION", model="NIKON D7000",
                   gps={1: "N", 2: (45.0, 30.0, 0.0), 3: "W", 4: (122.0, 30.0, 0.0)})
    d = media.decode(p.read_bytes(), ".jpg", max_side=100)
    assert (d.width, d.height) == (30, 60)
    assert d.image.size == (30, 60)
    # orientation 6 = rotate 90 clockwise: the marked top-left pixel ends up top-right
    assert sum(d.image.getpixel((27, 2))) < 120
    assert sum(d.image.getpixel((2, 2))) > 120
    assert d.format == "jpeg"
    assert d.taken_at == "2012-06-03T10:00:00"
    assert d.camera == "NIKON D7000"
    assert d.gps == (45.5, -122.5)


def test_downscales_to_max_side(tmp_path):
    p = write_jpeg(tmp_path / "big.jpg", size=(800, 400))
    d = media.decode(p.read_bytes(), ".jpg", max_side=100)
    assert max(d.image.size) == 100
    assert (d.width, d.height) == (800, 400)


def test_truncated_jpeg_still_decodes(tmp_path):
    data = write_jpeg(tmp_path / "t.jpg", size=(400, 300)).read_bytes()
    d = media.decode(data[:-66], ".jpg", max_side=100)
    assert (d.width, d.height) == (400, 300)


def test_mpo_is_reported_as_jpeg(tmp_path):
    p = tmp_path / "P1040195.JPG"
    Image.new("RGB", (400, 300), "red").save(p, "MPO", save_all=True, append_images=[Image.new("RGB", (400, 300))])
    assert Image.open(p).format == "MPO"
    d = media.decode(p.read_bytes(), ".jpg", max_side=100)
    assert d.format == "jpeg" and max(d.image.size) == 100


def test_png_alpha_becomes_white_rgb(tmp_path):
    p = tmp_path / "t.png"
    Image.new("RGBA", (10, 10), (255, 0, 0, 0)).save(p)
    d = media.decode(p.read_bytes(), ".png", max_side=100)
    assert d.image.mode == "RGB"
    assert d.image.getpixel((5, 5)) == (255, 255, 255)
    assert d.taken_at is None and d.camera is None and d.gps is None


@pytest.mark.parametrize("make, model, expected", [
    ("Canon", "Canon EOS 450D", "Canon EOS 450D"),
    ("Motorola", "MOTOG 1bc00001ffd800000a3a9a910501902c", "Motorola MOTOG"),
    ("Motorola\x00   ", "  MOTOG   ", "Motorola MOTOG"),
    ("samsung", "SM-G930V", "samsung SM-G930V"),
    (None, None, None),
])
def test_camera_name(make, model, expected):
    exif = Image.Exif()
    if make:
        exif[271] = make
    if model:
        exif[272] = model
    assert media.camera_name(exif) == expected


def test_raw_dates_from_either_rawpy():
    # LibRaw's date for a raw file: seconds since 1970 from rawpy up to 0.26, a datetime from 0.27 (turning a raw photo
    # failed on it); a file with no date is 0, or 1970 as a datetime
    taken = datetime(2012, 6, 3, 10, 5, 0)
    assert media._local_time(taken) == media._local_time(taken.timestamp()) == "2012-06-03T10:05:00"
    assert media._local_time(0) is None and media._local_time(datetime.fromtimestamp(0)) is None
    assert media._local_time(None) is None


def test_phash_fits_signed_64_bit():
    h = media.phash(Image.effect_noise((64, 64), 80).convert("RGB"))
    assert -(1 << 63) <= h < (1 << 63)


def test_save_tolerates_locked_existing_file(tmp_path, monkeypatch):
    def refuse(src, dst):
        raise PermissionError(5, "Access is denied")
    monkeypatch.setattr(media.os, "replace", refuse)
    monkeypatch.setattr(media.time, "sleep", lambda s: None)
    im = Image.new("RGB", (8, 8), "red")
    existing = tmp_path / "ab" / "ab12.webp"
    existing.parent.mkdir()
    existing.write_bytes(b"identical derivative already written")
    media._atomic_save(im, existing, "WEBP")
    assert list(existing.parent.iterdir()) == [existing]  # temp file cleaned up

    with pytest.raises(PermissionError):
        media._atomic_save(im, tmp_path / "ab" / "cd34.webp", "WEBP")


@pytest.mark.skipif(not SAMPLE_NEF.is_file(), reason="set LINEAGEBOX_SAMPLE_NEF to a real .NEF file")
def test_real_nef_uses_full_size_embedded_preview():
    d = media.decode(SAMPLE_NEF.read_bytes(), ".nef", max_side=2048)
    assert d.format == "nef"
    assert max(d.width, d.height) > 2048  # the full-size embedded JPEG, not the small thumbnail beside it
    assert max(d.image.size) == 2048 and abs(d.image.size[0] / d.image.size[1] - d.width / d.height) < 0.01
    assert d.camera.startswith("NIKON") and d.taken_at  # EXIF read from the raw file's TIFF container


# ---- HEIC ----------------------------------------------------------------------------------------------------------

def heic(size=(64, 32), orientation=None, taken=None, make=None, model=None) -> bytes:
    """A small HEIC made with pillow-heif, its top-left corner marked. Given an EXIF orientation, pillow-heif also
    writes it as HEIF's own turn of the picture, as phones do."""
    im = Image.new("RGB", size, "white")
    im.paste((0, 0, 0), (0, 0, 8, 8))
    exif = Image.Exif()
    if orientation:
        exif[ExifTags.Base.Orientation] = orientation
    if make:
        exif[ExifTags.Base.Make], exif[ExifTags.Base.Model] = make, model
    if taken:
        exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.DateTimeOriginal] = taken
    out = io.BytesIO()
    im.save(out, "HEIF", exif=exif.tobytes(), quality=95)
    return out.getvalue()


def test_heic_is_read_with_its_metadata():
    d = media.decode(heic(taken="2021:07:04 10:11:12", make="Apple", model="iPhone 12"), ".HEIC", max_side=100)
    assert d.format == "heic" and (d.width, d.height) == (64, 32) and d.image.mode == "RGB"
    assert (d.taken_at, d.camera) == ("2021-07-04T10:11:12", "Apple iPhone 12")


def test_a_turned_heic_is_turned_once():
    data = heic(orientation=6)
    assert Image.open(io.BytesIO(data)).info["original_orientation"] == 6  # the file says it is on its side
    d = media.decode(data, ".heic", max_side=100)
    # libheif turns it upright; turning it again by its EXIF would put it upside down
    assert (d.width, d.height) == (32, 64) and d.image.size == (32, 64)
    assert sum(d.image.getpixel((29, 2))) < 120 and sum(d.image.getpixel((2, 2))) > 600  # the mark is top-right


def test_a_heic_isnt_turned_in_its_file():
    # HEIF keeps its turn apart from the EXIF orientation that turning writes: it is refused, and says so
    assert writeback.why_not({"media": "photo", "format": "heic"}).startswith("HEIC files can't be changed here")


def test_every_kind_catalogued_is_one_media_reads():
    defaults = set(Config.__dataclass_fields__["image_extensions"].default)
    assert defaults == media.JPEG_EXTENSIONS | {".png"} | media.TIFF_EXTENSIONS | media.HEIF_EXTENSIONS | media.RAW_EXTENSIONS
    assert all(e == e.lower() for e in defaults)  # the scan and uploads match them case-insensitively
    # an upload's first bytes are checked against its kind: photos, videos and recordings alike
    for kind in ("image_extensions", "video_extensions", "audio_extensions"):
        assert set(Config.__dataclass_fields__[kind].default) <= set(uploads._SIGNATURES), kind
    assert uploads.looks_like("IMG_0001.HEIC", heic()[:64])


def test_a_motion_photo_reads_as_its_photo(tmp_path):
    # Google's and Samsung's motion photos are JPEGs with the clip after the picture
    path = write_jpeg(tmp_path / "PXL_20200524_135649_MP.jpg", size=(400, 300), taken="2020:05:24 13:56:49")
    d = media.decode(path.read_bytes() + b"\x00\x00\x00\x18ftypmp42" + bytes(5000), ".jpg", max_side=100)
    assert (d.format, d.width, d.height, d.taken_at) == ("jpeg", 400, 300, "2020-05-24T13:56:49")


# ---- other cameras' raw files --------------------------------------------------------------------------------------

def tiff(tags: dict, exif: dict | None = None, gps: dict | None = None) -> bytes:
    """A little-endian TIFF header and directories, as a raw file starts: tags in its main directory, and its Exif
    and GPS ones."""
    t = Image.Exif()
    t.endian = "<"
    t.update(tags)
    if exif:
        t.get_ifd(ExifTags.IFD.Exif).update(exif)
    if gps:
        t.get_ifd(ExifTags.IFD.GPSInfo).update(gps)
    return t.tobytes()[6:]  # without "Exif\0\0"


def jpeg(tmp_path, size=(1200, 800), **exif) -> bytes:
    return write_jpeg(tmp_path / f"preview{len(list(tmp_path.iterdir()))}.jpg", size=size, **exif).read_bytes()


class FakeRaw:
    """What rawpy gives for a raw file: its embedded preview, LibRaw's turn (flip), sensor size and date."""

    def __init__(self, preview: bytes | None, flip=0, size=(6000, 4000), timestamp=0):
        self.preview = preview
        self.sizes = SimpleNamespace(flip=flip, width=size[0], height=size[1])
        self.other = SimpleNamespace(timestamp=timestamp)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_thumb(self):
        if self.preview is None:
            raise rawpy.LibRawNoThumbnailError()
        return SimpleNamespace(format=rawpy.ThumbFormat.JPEG, data=self.preview)

    def postprocess(self, **_):  # half size, and already upright
        w, h = self.sizes.width // 200, self.sizes.height // 200
        return np.zeros((w, h, 3) if self.sizes.flip >= 5 else (h, w, 3), np.uint8)


def box(name: bytes, body: bytes) -> bytes:
    return (8 + len(body)).to_bytes(4, "big") + name + body


CAMERA = {ExifTags.Base.Make: "Canon", ExifTags.Base.Model: "Canon EOS R6", ExifTags.Base.Orientation: 6}
TAKEN = {ExifTags.Base.DateTimeOriginal: "2019:08:17 15:30:00"}
GPS = {1: "N", 2: (45.0, 30.0, 0.0), 3: "W", 4: (122.0, 30.0, 0.0)}


def raw_file(kind: str, tmp_path) -> bytes:
    """The start of a raw file of each kind, holding CAMERA, TAKEN and GPS where that kind keeps them."""
    if kind == "tiff":  # Nikon, Canon's CR2, Sony, Pentax, DNG...
        return tiff(CAMERA, TAKEN, GPS) + bytes(100)
    if kind in ("olympus", "panasonic"):  # TIFF with a magic number of their own
        return (b"IIRO" if kind == "olympus" else b"IIU\x00") + tiff(CAMERA, TAKEN, GPS)[4:] + bytes(100)
    if kind == "cr3":  # Canon's newer cameras: each directory a TIFF of its own, in a box
        cmt = box(b"CMT1", tiff(CAMERA)) + box(b"CMT2", tiff(TAKEN)) + box(b"CMT4", tiff(GPS))
        return box(b"ftyp", b"crx " + bytes(8)) + box(b"moov", box(b"uuid", bytes(16) + cmt))
    # Fujifilm: the JPEG inside has it, its place given at byte 84
    inside = jpeg(tmp_path, orientation=6, taken="2019:08:17 15:30:00", make="Canon", model="Canon EOS R6", gps=GPS)
    head = b"FUJIFILMCCD-RAW 0201FF383501".ljust(84, b"\x00")
    return head + (100).to_bytes(4, "big") + len(inside).to_bytes(4, "big") + bytes(8) + inside


@pytest.mark.parametrize("ext, kind", [(".cr2", "tiff"), (".dng", "tiff"), (".orf", "olympus"),
                                       (".rw2", "panasonic"), (".cr3", "cr3"), (".raf", "raf")])
def test_raw_files_read_their_exif(monkeypatch, tmp_path, ext, kind):
    preview = jpeg(tmp_path)
    monkeypatch.setattr(media.rawpy, "imread", lambda f: FakeRaw(preview))
    d = media.decode(raw_file(kind, tmp_path), ext.upper(), max_side=100)
    assert d.format == ext[1:] and d.format in media.RAW_FORMATS
    assert (d.taken_at, d.camera, d.gps) == ("2019-08-17T15:30:00", "Canon EOS R6", (45.5, -122.5))
    # the sensor's size, not the preview's, turned by the EXIF orientation (6)
    assert (d.width, d.height) == (4000, 6000) and d.image.size == (67, 100)


def test_a_raw_file_whose_exif_isnt_found(monkeypatch, tmp_path):
    data = b"\x00MRM" + bytes(200)  # a kind whose EXIF isn't looked for: its preview's is used
    preview = jpeg(tmp_path, taken="2008:01:02 03:04:05", make="Minolta", model="DYNAX 7D")
    monkeypatch.setattr(media.rawpy, "imread", lambda f: FakeRaw(preview))
    d = media.decode(data, ".mrw", max_side=100)
    assert (d.taken_at, d.camera) == ("2008-01-02T03:04:05", "Minolta DYNAX 7D")
    # none there either: LibRaw's turn and date
    preview, when = jpeg(tmp_path), datetime(2008, 1, 2, 3, 4, 5).timestamp()
    monkeypatch.setattr(media.rawpy, "imread", lambda f: FakeRaw(preview, flip=6, timestamp=when))
    d = media.decode(data, ".mrw", max_side=1200)
    assert d.taken_at == "2008-01-02T03:04:05" and (d.width, d.height) == (4000, 6000)
    assert d.image.size == (800, 1200) and sum(d.image.getpixel((797, 2))) < 120  # the mark is top-right


@pytest.mark.parametrize("small", [True, False])
def test_a_raw_file_with_only_a_thumbnail_is_demosaiced(monkeypatch, tmp_path, small):
    preview = jpeg(tmp_path, size=(160, 120)) if small else None
    monkeypatch.setattr(media.rawpy, "imread", lambda f: FakeRaw(preview, flip=5))
    d = media.decode(tiff(CAMERA) + bytes(100), ".arw", max_side=100)
    # LibRaw turns its own picture upright, so the EXIF orientation isn't applied again
    assert (d.width, d.height) == (4000, 6000) and d.image.size == (20, 30)


def test_any_raw_kind_is_raw_in_similar_shots():
    members = [{"id": i, "format": fmt, "pixels": 6000 * 4000, "dated": True, "taken_at": "2019-08-17T15:30:00",
                "phash": 0, "kept_at": None, "sharpness": 50.0, "bytes": 1} for i, fmt in ((1, "cr3"), (2, "jpeg"))]
    assert cleanup._mark("raw", members, keep_raw=True)
    assert [m["id"] for m in members if m["tick"]] == [2]


# ---- TIFF: scanners' and photo editors' files, shown through a JPEG preview ----------------------------------------

def _tiff(im, **save) -> bytes:
    out = io.BytesIO()
    im.save(out, "TIFF", **save)
    return out.getvalue()


def test_a_16_bit_scan_is_scaled_not_cut_off_at_white():
    """A 16-bit grey TIFF (what scanners save): mid-grey stays mid-grey, it isn't turned white."""
    grey = Image.new("I;16", (40, 30), 32768)
    d = media.decode(_tiff(grey), ".tif", 2048)
    assert d.format == "tiff" and d.image.mode == "RGB" and (d.width, d.height) == (40, 30)
    assert 110 < d.image.getpixel((5, 5))[0] < 145


def test_cmyk_alpha_and_a_turned_tiff():
    cmyk = media.decode(_tiff(Image.new("CMYK", (30, 20), (0, 255, 255, 0))), ".tiff", 2048)
    r, g, b = cmyk.image.getpixel((3, 3))
    assert r > 200 and g < 60 and b < 60  # red
    see_through = media.decode(_tiff(Image.new("RGBA", (10, 10), (0, 0, 0, 0))), ".tif", 2048)
    assert see_through.image.getpixel((1, 1)) == (255, 255, 255)  # on white, as PNGs are
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = 6
    exif[ExifTags.Base.Make], exif[ExifTags.Base.Model] = "EPSON", "Perfection V600"
    turned = media.decode(_tiff(Image.new("RGB", (60, 30), "blue"), exif=exif), ".tif", 2048)
    assert turned.image.size == (30, 60) and (turned.width, turned.height) == (30, 60)  # upright
    assert "Perfection V600" in (turned.camera or "")


def test_a_tiff_of_several_pages_is_shown_by_its_first():
    first, second = Image.new("RGB", (20, 20), "green"), Image.new("RGB", (20, 20), "red")
    out = io.BytesIO()
    first.save(out, "TIFF", save_all=True, append_images=[second])
    d = media.decode(out.getvalue(), ".tiff", 2048)
    assert d.image.getpixel((2, 2))[1] > 100 and d.image.getpixel((2, 2))[0] < 60  # green


def test_a_tiff_is_catalogued_with_jpeg_preview_and_thumbnail(cfg, con, library):
    """Browsers can't show TIFF: the photo page and grids use the JPEG preview and thumbnail ingest makes."""
    from fastapi.testclient import TestClient
    from lineagebox.ingest import ingest
    from lineagebox.scan import scan
    from lineagebox.web import app as webapp
    from tests.conftest import LAN
    (library / "Scans").mkdir()
    Image.new("RGB", (300, 200), (200, 120, 40)).save(library / "Scans" / "grandma 1952.TIF", "TIFF",
                                                       compression="tiff_lzw")
    scan(con, cfg)
    ingest(con, cfg)
    row = con.execute("""SELECT i.* FROM image i JOIN file f ON f.image_id = i.id
                         WHERE f.path LIKE '%grandma 1952.TIF'""").fetchone()
    assert row is not None and row["format"] == "tiff" and (row["width"], row["height"]) == (300, 200)
    client = TestClient(webapp.create_app(cfg), client=LAN)
    r = client.get(f"/p/{row['sha256']}")  # the preview the photo page shows: a JPEG
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:3] == bytes([255, 216, 255])
    r = client.get(f"/t/{row['sha256']}")  # the grids' thumbnail, as for every photo
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert f'src="/p/{row["sha256"]}"' in client.get(f"/image/{row['id']}").text
    assert writeback.why_not(dict(row)) is None  # it can be turned, in its file too


def test_a_jfif_is_catalogued_as_a_jpeg(cfg, con, library):
    """Windows and browsers save many pictures from the web as .jfif: a JPEG by another name."""
    from fastapi.testclient import TestClient
    from lineagebox.ingest import ingest
    from lineagebox.scan import scan
    from lineagebox.web import app as webapp
    from tests.conftest import LAN
    (library / "Saved").mkdir()
    Image.new("RGB", (300, 200), (40, 120, 200)).save(library / "Saved" / "reunion.JFIF", "JPEG")
    scan(con, cfg)
    ingest(con, cfg)
    row = con.execute("""SELECT i.*, f.id AS file_id FROM image i JOIN file f ON f.image_id = i.id
                         WHERE f.path LIKE '%reunion.JFIF'""").fetchone()
    assert row is not None and row["format"] == "jpeg" and (row["width"], row["height"]) == (300, 200)
    r = TestClient(webapp.create_app(cfg), client=LAN).get(f"/original/{row['file_id']}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"  # shown, not a download of no kind
    assert uploads.looks_like("reunion.jfif", b"\xff\xd8\xff\xe0") and not uploads.looks_like("x.jfif", b"<html>")


def test_a_tiff_upload_is_checked_by_its_first_bytes():
    assert uploads.looks_like("scan.tif", b"II*\x00\x08\x00\x00\x00") and uploads.looks_like("scan.TIFF", b"MM\x00*")
    assert uploads.looks_like("big.tif", b"II+\x00\x08\x00")  # BigTIFF
    assert not uploads.looks_like("scan.tif", b"\xff\xd8\xff\xe0")


@pytest.mark.parametrize("compression", ["tiff_lzw", "tiff_adobe_deflate", "jpeg", "packbits"])
@pytest.mark.parametrize("orientation, corner", [(1, "top left"), (3, "bottom right"), (6, "top right"),
                                                 (8, "bottom left")])
def test_a_compressed_tiff_is_turned_upright_once(compression, orientation, corner):
    """Pillow turns a TIFF upright as it reads it, whatever its compression: decode() mustn't turn it again."""
    marked = Image.new("RGB", (60, 30), "white")
    marked.paste((0, 0, 0), (0, 0, 8, 8))  # its top left corner, as it is stored
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = orientation
    d = media.decode(_tiff(marked, compression=compression, exif=exif), ".tif", 2048)
    w, h = d.image.size
    assert (w, h) == ((60, 30) if orientation < 5 else (30, 60)) == (d.width, d.height)
    at = {"top left": (2, 2), "top right": (w - 3, 2), "bottom left": (2, h - 3), "bottom right": (w - 3, h - 3)}
    dark = [where for where, xy in at.items() if sum(d.image.getpixel(xy)) < 200]
    assert dark == [corner]


def test_greys_of_more_than_16_bits_and_floats():
    """32-bit greys within 16 bits' range are read as 16-bit; wider ones by their brightest; floats from 0 to 1."""
    assert media.decode(_tiff(Image.new("I", (20, 10), 32768)), ".tif", 2048).image.getpixel((2, 2))[0] in (127, 128)
    wide = Image.new("I", (20, 10), 1 << 20)
    wide.putpixel((0, 0), 1 << 21)  # the brightest: white, the rest half as bright
    d = media.decode(_tiff(wide), ".tif", 2048)
    assert d.image.getpixel((0, 0))[0] == 255 and 120 < d.image.getpixel((5, 5))[0] < 135
    ramp = Image.new("F", (20, 10), 0.5)
    ramp.putpixel((0, 0), 1.0)
    d = media.decode(_tiff(ramp), ".tif", 2048)
    assert d.image.mode == "RGB" and d.image.getpixel((0, 0))[0] == 255 and 120 < d.image.getpixel((5, 5))[0] < 135


def test_a_tiff_too_large_to_read_is_refused_before_it_is_decoded(monkeypatch):
    """A scan can say it is huge (a TIFF isn't decoded at a reduced scale, as a JPEG is): refused by its size alone."""
    data = _tiff(Image.new("RGB", (300, 200)), compression="tiff_lzw")
    monkeypatch.setattr(media, "MAX_FULL_PIXELS", 300 * 200 - 1)
    from PIL import TiffImagePlugin
    monkeypatch.setattr(TiffImagePlugin.TiffImageFile, "load", lambda self: pytest.fail("decoded"))
    with pytest.raises(ValueError, match="too large a TIFF"):
        media.decode(data, ".tif", 2048)


def test_big_scans_are_decoded_a_couple_at_a_time(monkeypatch):
    """Ingest decodes on several threads: a big picture that isn't a JPEG takes one of a few places first."""
    import threading
    taken = []

    class Places:
        def __init__(self):
            self.lock = threading.BoundedSemaphore(2)

        def __enter__(self):
            taken.append(True)
            return self.lock.__enter__()

        def __exit__(self, *exc):
            return self.lock.__exit__(*exc)

    monkeypatch.setattr(media, "BIG_PIXELS", 100)
    monkeypatch.setattr(media, "_BIG", Places())
    media.decode(_tiff(Image.new("RGB", (40, 30), "red")), ".tif", 2048)
    assert taken == [True]
    small = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(small, "JPEG")
    media.decode(small.getvalue(), ".jpg", 2048)  # JPEGs are read shrunk: no place needed
    assert taken == [True]
    # ... except a progressive one: the whole picture's coefficients are kept while it is read
    progressive = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(progressive, "JPEG", progressive=True)
    media.decode(progressive.getvalue(), ".jpg", 2048)
    assert taken == [True, True]
    monkeypatch.setattr(media, "MAX_PROGRESSIVE_PIXELS", 40 * 30 - 1)
    with pytest.raises(ValueError, match="too large a progressive JPEG"):
        media.decode(progressive.getvalue(), ".jpg", 2048)
    media.decode(small.getvalue(), ".jpg", 2048)  # a baseline one that size is still read
