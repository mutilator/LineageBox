"""Damaged files (check.py): photos a disk spoilt are found by reading them whole, previews that are gone are made
again, and Cleanup → Damaged files removes or keeps them."""
import os

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from lineagebox import check, cli
from lineagebox.ingest import STAGE_INGEST, ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests.conftest import LAN, rel

APP = {"X-Requested-With": "lineagebox"}


def noisy_jpeg(path, seed, comment=b""):
    """A photo whose picture data is large (noise compresses badly), as a real one is."""
    pixels = np.random.default_rng(seed).integers(0, 255, (400, 600, 3), dtype=np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(pixels).save(path, "JPEG", quality=90, comment=comment, exif=Image.Exif())
    return path


def spoil(path, change):
    """Change a file's bytes in place, keeping its size and date, as a disk does."""
    st = path.stat()
    data = bytearray(path.read_bytes())
    change(data)
    path.write_bytes(bytes(data))
    os.utime(path, (st.st_atime, st.st_mtime))


@pytest.fixture
def damaged(con, cfg, library):
    d = library / "Damaged"
    for name, seed in (("good", 1), ("blank", 2), ("changed", 3), ("short", 4)):
        noisy_jpeg(d / f"{name}.jpg", seed, comment=b"a comment of the camera" * 400 if name == "changed" else b"")
    whole = (d / "short.jpg").read_bytes()
    (d / "short.jpg").write_bytes(whole[:len(whole) * 2 // 3])  # cut short before it was ever catalogued
    scan(con, cfg)
    ingest(con, cfg)

    def blank(data):  # a disk block that couldn't be read, in the middle of the picture
        middle = len(data) // 2
        data[middle:middle + 8192] = bytes(8192)
    spoil(d / "blank.jpg", blank)
    spoil(d / "changed.jpg", lambda data: data.__setitem__(slice(data.find(b"a comment"), data.find(b"a comment") + 1),
                                                           b"A"))
    return {n: con.execute("SELECT id FROM file WHERE path = ?", (rel("Damaged", f"{n}.jpg"),)).fetchone()[0]
            for n in ("good", "blank", "changed", "short")}


def test_damage_is_found(con, cfg, damaged):
    checked, found = check.check(con, cfg)
    assert found == 3
    damage = dict(con.execute("SELECT id, damage FROM file WHERE damage IS NOT NULL"))
    assert "blank stretch of 8 KB" in damage[damaged["blank"]]
    assert "bytes changed since it was catalogued" in damage[damaged["changed"]]
    assert "cut short" in damage[damaged["short"]]
    assert damaged["good"] not in damage  # its EXIF and comment are not taken for its picture
    assert check.check(con, cfg) == (0, 0)  # each file once
    assert check.check(con, cfg, every=True)[1] == 3
    assert check.progress(con)["damaged"] == 3


def test_a_changed_file_is_the_scans(con, cfg, damaged, library):
    path = library / "Damaged" / "good.jpg"
    path.write_bytes(path.read_bytes() + b"more")  # its size changed: read again as a new photo, not "damaged"
    check.check(con, cfg)
    assert con.execute("SELECT checked_at, damage FROM file WHERE id = ?", (damaged["good"],)).fetchone()[:] == (None,
                                                                                                                 None)


def test_previews_that_are_gone_are_made_again(con, cfg, damaged):
    sha = con.execute("SELECT i.sha256 FROM image i JOIN file f ON f.image_id = i.id WHERE f.id = ?",
                      (damaged["good"],)).fetchone()[0]
    cfg.preview_path(sha).unlink()
    assert check.rebuild_derivatives(con, cfg) == 1
    assert not con.execute("SELECT stage & ? FROM image WHERE sha256 = ?", (STAGE_INGEST, sha)).fetchone()[0]
    ingest(con, cfg)  # the worker's next pass
    assert cfg.preview_path(sha).is_file() and check.missing_derivatives(con, cfg) == []


def test_the_damaged_files_page(con, cfg, damaged):
    check.check(con, cfg)
    c = TestClient(webapp.create_app(cfg), client=LAN)
    page = c.get("/cleanup/damaged").text
    assert "blank stretch" in page and "the only copy" in page and "Damaged files" in c.get("/cleanup").text
    assert c.post("/api/damaged/ok", json={"image_ids": [damaged["changed"]]}, headers=APP).status_code == 200
    assert "changed.jpg" not in c.get("/cleanup/damaged").text
    assert check.check(con, cfg, every=True)[1] == 2  # fine as it is: not checked again
    r = c.post("/api/damaged/remove", json={"image_ids": [damaged["blank"], damaged["good"]], "reason": "damaged"},
               headers=APP)
    assert r.json()["moved"] == 1  # only damaged files are removed, even when others are sent
    assert (cfg.share_root / f"{cfg.folders[0]}-Removed" / "Damaged" / "blank.jpg").is_file()


def test_the_check_files_command(con, cfg, damaged, capsys, monkeypatch):
    monkeypatch.setattr(cli.config, "load", lambda path=None: cfg)
    cli.main(["check-files"])
    out = capsys.readouterr().out
    assert "3 damaged" in out and "previews: none gone" in out


def test_videos_are_hashed_a_piece_at_a_time(tmp_path, monkeypatch):
    """A video can be gigabytes: it is never read into memory whole, and still found changed."""
    import hashlib
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 400)
    sha = hashlib.sha256(video.read_bytes()).hexdigest()
    monkeypatch.setattr(check, "PIECE", 1000)
    monkeypatch.setattr(type(video), "read_bytes", lambda self: pytest.fail("read whole"))
    assert check.problem(video, sha, "mp4") is None
    with open(video, "r+b") as f:
        f.seek(50_000)
        f.write(b"\xff")
    assert "bytes changed" in check.problem(video, sha, "mp4")
