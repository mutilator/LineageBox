import pytest
from PIL import ExifTags, Image

from lineagebox import db
from lineagebox.config import Config

LAN = ("192.168.7.50", 50000)  # TestClient's own default client ("testclient") is not an address, so it is refused


def rel(*parts: str) -> str:
    """A path in the test library as the catalog stores it: from the share, '/' on every system."""
    if parts and (parts[0] + "/").startswith("lib/"):  # already one: rel(ZOO, "x.jpg")
        return "/".join(parts)
    return "/".join(("lib", *parts))


def write_jpeg(path, size=(60, 30), color="red", orientation=None, taken=None, make=None, model=None, gps=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", size, color)
    im.paste((0, 0, 0), (0, 0, 6, 6))  # marks the top-left corner so rotation can be checked
    exif = Image.Exif()
    if orientation:
        exif[ExifTags.Base.Orientation] = orientation
    if make:
        exif[ExifTags.Base.Make] = make
    if model:
        exif[ExifTags.Base.Model] = model
    if taken:
        exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.DateTimeOriginal] = taken
    if gps:
        exif.get_ifd(ExifTags.IFD.GPSInfo).update(gps)
    im.save(path, "JPEG", exif=exif, quality=95)
    return path


@pytest.fixture
def library(tmp_path):
    """A small library covering rotation, duplicates, ignored folders, PNG alpha, a corrupt file and junk."""
    root = tmp_path / "lib"
    write_jpeg(root / "2016" / "IMG_20160811_182239.jpg", color="blue")
    write_jpeg(root / "2012" / "Zoo" / "DSC_0001.jpg", color="green", orientation=6,
               taken="2012:06:03 10:00:00", make="NIKON CORPORATION", model="NIKON D7000")
    (root / "Owen" / "Zoo").mkdir(parents=True)
    (root / "Owen" / "Zoo" / "DSC_0001.jpg").write_bytes((root / "2012" / "Zoo" / "DSC_0001.jpg").read_bytes())
    write_jpeg(root / "Portraits" / "Thumbs" / "small.jpg", color="yellow")
    Image.new("RGBA", (20, 20), (255, 0, 0, 0)).save(root / "transparent.png")
    (root / "broken.jpg").write_bytes(b"this is not an image")
    (root / "notes.txt").write_text("not a photo")
    return root


@pytest.fixture
def cfg(tmp_path, library):
    # no repository: a worker pass in a test never asks GitHub for releases (test_updates.py names one, and a fake)
    return Config(share_root=library.parent, folders=(library.name,), data_dir=tmp_path / "data", ingest_threads=2,
                  preview_size=48, thumb_size=16, update_repository="")


@pytest.fixture
def con(cfg):
    c = db.connect(cfg)
    yield c
    c.close()
