import os
from pathlib import Path

import pytest
from PIL import Image

from lineagebox import media
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
