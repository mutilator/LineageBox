"""Cleanup → Turned photos: photos on their side, found by their faces, and turned in their files too."""
import hashlib
import io
import sqlite3
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from lineagebox import orientation, settings, writeback
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests.conftest import LAN, rel, write_jpeg

APP = {"X-Requested-With": "lineagebox"}
# a face's landmarks (eyes, nose, mouth corners) as fractions of the picture, head up, around (0.5, 0.5)
UPRIGHT = np.array([[-.05, -.05], [.05, -.05], [0, 0], [-.04, .05], [.04, .05]])


def landmarks(head: int, at=(0.5, 0.5)) -> bytes:
    """A face whose head points `head` degrees clockwise from up (90: to the right), in a square picture."""
    a = np.radians(head)
    turn = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    return ((UPRIGHT @ turn.T) + at).astype(np.float32).tobytes()


def add_face(con, image_id, head, at=(0.5, 0.5)):
    con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, landmarks, det_score, quality, embedding)
                   VALUES (?, ?, ?, ?, ?, ?, 0.9, 22, ?)""",
                (image_id, at[0] - .15, at[1] - .15, at[0] + .15, at[1] + .15, landmarks(head, at),
                 np.zeros(512, np.float32).tobytes()))


@pytest.fixture
def turned(con, cfg, library):
    """Two people with their heads to the left (taken sideways; its left half is dark, to see where it goes), one
    face alone, and a child upside down among people standing."""
    sideways = Image.new("RGB", (300, 200), (220, 220, 220))
    sideways.paste((0, 0, 0), (0, 0, 150, 200))
    (library / "Turned").mkdir()
    sideways.save(library / "Turned" / "sideways.jpg", "JPEG")
    for name, color in (("alone", (90, 120, 150)), ("monkey_bars", (150, 120, 90))):  # two photos, not one
        write_jpeg(library / "Turned" / f"{name}.jpg", size=(300, 300), color=color)
    scan(con, cfg)
    ingest(con, cfg)
    ids = {name: con.execute("SELECT image_id FROM file WHERE path = ?", (rel("Turned", f"{name}.jpg"),)).fetchone()[0]
           for name in ("sideways", "alone", "monkey_bars")}
    add_face(con, ids["sideways"], 270, (0.3, 0.5))
    add_face(con, ids["sideways"], 270, (0.7, 0.5))
    add_face(con, ids["alone"], 90)
    add_face(con, ids["monkey_bars"], 180, (0.3, 0.5))
    add_face(con, ids["monkey_bars"], 0, (0.7, 0.5))
    con.commit()
    return ids


def test_which_way_a_face_is():
    assert [orientation.face_turn(landmarks(h), 100, 100) for h in (0, 90, 180, 270, 20)] == [0, 270, 180, 90, 0]
    assert orientation.face_turn(landmarks(45), 100, 100) is None  # a head tilted halfway says nothing


def test_only_faces_that_agree_suggest_a_turn(con, turned):
    sure = orientation.suggestions(con, "sure")
    assert [(s["id"], s["turn"], s["doubt"]) for s in sure] == [(turned["sideways"], 90, None)]
    more = {s["id"]: s for s in orientation.suggestions(con, "more")}
    assert more[turned["alone"]]["doubt"] == "only one face says so" and more[turned["alone"]]["turn"] == 270
    assert turned["monkey_bars"] not in more  # someone upright in it: it is the right way up
    assert orientation.suggestions(con, "off") == []

    settings.put(con, orientation.AI_KEY, {str(turned["sideways"]): "fine"})  # the AI sees people lying down
    assert orientation.suggestions(con, "sure") == []
    more = {s["id"]: s for s in orientation.suggestions(con, "more")}
    assert more[turned["sideways"]]["doubt"] == "the AI thinks it is the right way up"
    orientation.mark_fine(con, [turned["sideways"], turned["alone"]])
    assert orientation.suggestions(con, "more") == []


def test_the_ai_is_asked_once_with_the_photo_both_ways(con, cfg, turned):
    class FakeAI:  # the dark half of the sideways photo is the sky: the right way up has it on top
        asked = 0

        def chat_json(self, system, user, schema, images):
            FakeAI.asked += 1
            dark_on_top = [Image.open(io.BytesIO(b)).convert("L").getpixel((im_w // 2, 2)) < 100
                           for b in images for im_w in [Image.open(io.BytesIO(b)).width]]
            return {"upright": "first" if dark_on_top[0] else "second"}, None

    assert orientation.check_with_ai(con, cfg, FakeAI(), "sure") == 2  # one face alone is asked about too
    said = settings.get(con, orientation.AI_KEY)
    assert said[str(turned["sideways"])] == "turned" and str(turned["alone"]) in said
    assert orientation.check_with_ai(con, cfg, FakeAI(), "sure") == 0 and FakeAI.asked == 2


def test_one_face_is_sure_when_the_ai_agrees(con, turned):
    alone = turned["alone"]
    assert alone not in {s["id"] for s in orientation.suggestions(con, "sure")}  # one face alone: only "more"
    settings.put(con, orientation.AI_KEY, {str(alone): "turned"})
    sure = {s["id"]: s for s in orientation.suggestions(con, "sure")}
    assert sure[alone]["doubt"] is None and sure[alone]["turn"] == 270
    settings.put(con, orientation.AI_KEY, {str(alone): "fine"})
    more = {s["id"]: s for s in orientation.suggestions(con, "more")}
    assert alone not in {s["id"] for s in orientation.suggestions(con, "sure")}
    assert more[alone]["doubt"] == "the AI thinks it is the right way up"


@pytest.mark.skipif(writeback.exiftool() is None, reason="needs ExifTool")
def test_turning_writes_the_file_and_the_catalog_follows(con, cfg, library, turned):
    image_id = turned["sideways"]
    path = library / "Turned" / "sideways.jpg"
    old = con.execute("SELECT sha256 FROM image WHERE id = ?", (image_id,)).fetchone()[0]
    orientation.turn(con, cfg, image_id, 90)
    with Image.open(path) as im:
        assert im.getexif()[0x0112] == 6 and im.size == (300, 200)  # the tag says turn it; the pixels are as they were
    row = con.execute("SELECT * FROM image WHERE id = ?", (image_id,)).fetchone()
    assert row["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest() != old
    assert (row["width"], row["height"]) == (200, 300)
    assert not cfg.preview_path(old).exists() and Image.open(cfg.preview_path(row["sha256"])).size[1] > \
        Image.open(cfg.preview_path(row["sha256"])).size[0]
    boxes = con.execute("SELECT x1, y1 FROM face WHERE image_id = ? ORDER BY x1, y1", (image_id,)).fetchall()
    assert [tuple(round(v, 2) for v in b) for b in boxes] == [(0.35, 0.15), (0.35, 0.55)]  # turned with the photo
    assert image_id not in {s["id"] for s in orientation.suggestions(con, "more")}  # its faces are upright now
    assert scan(con, cfg).changed == 0  # the catalog already knows the changed file

    with pytest.raises(writeback.Problem, match="quarter"):
        orientation.turn(con, cfg, image_id, 45)


def fake_exiftool(monkeypatch):
    """ExifTool's output for -Orientation#=N, made with Pillow: what writeback does around it is what is tested."""
    def changed(path, tags):
        with Image.open(path) as im:
            exif = im.getexif()
            exif[0x0112] = int(tags[0].rsplit("=", 1)[1])
            out = io.BytesIO()
            im.save(out, "JPEG", exif=exif)
        return out.getvalue()
    monkeypatch.setattr(writeback, "exiftool", lambda: "exiftool")
    monkeypatch.setattr(writeback, "_changed", changed)


def test_turning_is_all_or_nothing(con, cfg, library, turned, monkeypatch):
    """A failure once the file is swapped puts it back and leaves the catalog as it was, so trying again turns the
    photo once, not twice."""
    fake_exiftool(monkeypatch)
    image_id, path = turned["sideways"], library / "Turned" / "sideways.jpg"
    before, sha = path.read_bytes(), con.execute("SELECT sha256 FROM image WHERE id = ?", (image_id,)).fetchone()[0]
    boxes = con.execute("SELECT x1, y1 FROM face WHERE image_id = ? ORDER BY id", (image_id,)).fetchall()

    def broken(*args):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(writeback, "_turn_faces", broken)
    with pytest.raises(writeback.Problem, match="No space left"):
        orientation.turn(con, cfg, image_id, 90)
    assert path.read_bytes() == before
    assert con.execute("SELECT sha256 FROM image WHERE id = ?", (image_id,)).fetchone()[0] == sha
    assert con.execute("SELECT x1, y1 FROM face WHERE image_id = ? ORDER BY id", (image_id,)).fetchall() == boxes
    assert not list(path.parent.glob(".lineagebox-*"))  # nothing left beside it
    assert cfg.preview_path(sha).exists()  # its preview is still the one shown

    # the catalog busy: refused before anything is touched
    monkeypatch.undo()
    fake_exiftool(monkeypatch)
    other = sqlite3.connect(cfg.db_path)
    other.execute("BEGIN IMMEDIATE")
    con.execute("PRAGMA busy_timeout = 100")
    with pytest.raises(writeback.Problem, match="busy"):
        orientation.turn(con, cfg, image_id, 90)
    other.rollback()
    other.close()
    assert path.read_bytes() == before

    # and when nothing fails, the file and the catalog change together
    orientation.turn(con, cfg, image_id, 90)
    with Image.open(path) as im:
        assert im.getexif()[0x0112] == 6
    assert con.execute("SELECT sha256 FROM image WHERE id = ?", (image_id,)).fetchone()[0] == \
        hashlib.sha256(path.read_bytes()).hexdigest()
    assert not list(path.parent.glob(".lineagebox-*")) and scan(con, cfg).changed == 0


def test_a_copy_that_cant_be_swapped_leaves_every_copy_as_it_was(con, cfg, library, monkeypatch):
    """A photo in two folders, the second refusing the swap (a file another program holds open, say): the first copy
    is put back, the catalog is unchanged, and nothing is left beside either."""
    fake_exiftool(monkeypatch)
    paths = [library / folder / "sideways.jpg" for folder in ("Turned", "Turned again")]
    for path in paths:
        path.parent.mkdir()
        Image.new("RGB", (300, 200), (200, 200, 200)).save(path, "JPEG")
    scan(con, cfg)
    ingest(con, cfg)
    image_id = con.execute("SELECT image_id FROM file WHERE path = ?", (rel("Turned", "sideways.jpg"),)).fetchone()[0]
    before, sha = [p.read_bytes() for p in paths], con.execute("SELECT sha256 FROM image WHERE id = ?",
                                                               (image_id,)).fetchone()[0]
    swap = writeback.os.replace

    def refused(src, dst):
        if "Turned again" in str(dst) and ".lineagebox-original" in str(dst):
            raise OSError(13, "Permission denied")
        return swap(src, dst)
    monkeypatch.setattr(writeback.os, "replace", refused)
    r = TestClient(webapp.create_app(cfg), client=LAN).post(f"/api/images/{image_id}/turn", json={"degrees": 90},
                                                            headers=APP)
    assert r.status_code == 409 and "Permission denied" in r.json()["detail"]
    assert [p.read_bytes() for p in paths] == before
    assert con.execute("SELECT sha256 FROM image WHERE id = ?", (image_id,)).fetchone()[0] == sha
    assert not [f for p in paths for f in p.parent.glob(".lineagebox-*")]


def test_a_turned_file_stays_its_owners(con, cfg, library, turned, monkeypatch):
    """Running as root, the changed file is given back to whoever owned the original, as a 720p copy is."""
    fake_exiftool(monkeypatch)
    path = library / "Turned" / "sideways.jpg"
    given = []
    monkeypatch.setattr(writeback.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(writeback.os, "chown", lambda p, uid, gid: given.append((Path(p).name, uid, gid)),
                        raising=False)
    st = path.stat()
    orientation.turn(con, cfg, turned["sideways"], 90)
    assert given == [(".lineagebox-new-sideways.jpg.part", st.st_uid, st.st_gid)]


def test_turned_photos_pages(con, cfg, turned, monkeypatch):
    monkeypatch.setattr(writeback, "exiftool", lambda: "exiftool")  # the pages offer turning where it can be done
    client = TestClient(webapp.create_app(cfg), client=LAN)
    assert 'href="/cleanup/turned"' in client.get("/cleanup").text
    page = client.get("/cleanup/turned").text
    assert f'value="{turned["sideways"]}"' in page and 'class="turn-90"' in page
    assert f'value="{turned["alone"]}"' not in page  # one face: only when the setting asks for more
    client.post("/api/settings", json={"turn_suggest": "more"}, headers=APP)
    assert "Worth a look" in client.get("/cleanup/turned").text
    assert client.post("/api/images/turn-ok", json={"image_ids": [turned["alone"]]}, headers=APP).status_code == 200
    assert f'value="{turned["alone"]}"' not in client.get("/cleanup/turned").text

    done = []
    monkeypatch.setattr(orientation, "turn", lambda c, cfg, image_id, degrees: done.append((image_id, degrees)))
    r = client.post("/api/images/turn", json={"image_ids": [turned["sideways"], turned["monkey_bars"]]}, headers=APP)
    assert done == [(turned["sideways"], 90)] and r.json()["moved"] == 1 and "no longer" in r.json()["errors"][0]
    assert 'data-body=\'{"degrees": 90}\'' in client.get(f"/image/{turned['monkey_bars']}").text
    client.post(f"/api/images/{turned['monkey_bars']}/turn", json={"degrees": 180}, headers=APP)
    assert done[-1] == (turned["monkey_bars"], 180)
