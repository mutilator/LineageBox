"""Through the years: someone's face a year at a time, lined up on the eyes (face_years.py, the person page's
/person/{id}/years, the pictures at /face/{id}/eyes, a panel on their Home)."""
import html
import json
import re
from math import hypot

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from lineagebox import db, face_years, people
from lineagebox.config import Config
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests.conftest import LAN, write_jpeg


def landmarks(cx: float, cy: float, apart: float, turn: float = 0.0, nose: float = 0.62, mouth: float = 0.95,
              size=(400, 300)) -> bytes:
    """A face's landmarks as shares of a photo of this size: eyes `apart` (a share of its width) either side of
    (cx, cy); the mouth `mouth` eye-distances below them, the nose `nose` of the way down to it, and `turn` of the way
    towards the eye on the right (0.5: over it, a face in profile). The defaults look straight at the camera."""
    w, h = size
    below = mouth * apart * w / h  # eye-distances down, as a share of the height
    pts = [(cx - apart / 2, cy), (cx + apart / 2, cy), (cx + turn * apart, cy + nose * below),
           (cx - apart * 0.4, cy + below), (cx + apart * 0.4, cy + below)]
    return np.array(pts, dtype=np.float32).tobytes()


def test_measuring_a_face():
    # on a 400 x 300 photo with a 600 px preview, the preview is the photo: eyes 0.1 apart are 40 px apart
    apart, facing = face_years.measure(landmarks(0.5, 0.4, 0.1), 400, 300, 600)
    assert apart == pytest.approx(40, abs=0.01) and facing == 1
    # a big photo: measured on its preview, as the picture is made from it
    assert face_years.measure(landmarks(0.5, 0.4, 0.1, size=(4000, 3000)), 4000, 3000, 2048)[0] ==         pytest.approx(204.8, abs=0.01)
    # turned aside, looking down (the nose by the mouth, the mouth up by the eyes), photographed from below (the nose
    # up by the eyes): as measured on real faces; a little turned costs a little
    assert face_years.measure(landmarks(0.5, 0.4, 0.1, turn=0.5), 400, 300, 600)[1] == 0.05
    down = face_years.measure(landmarks(0.5, 0.4, 0.1, nose=0.91, mouth=0.62), 400, 300, 600)[1]
    below = face_years.measure(landmarks(0.5, 0.4, 0.1, nose=-0.07, mouth=0.64), 400, 300, 600)[1]
    slightly = face_years.measure(landmarks(0.5, 0.4, 0.1, turn=0.03, nose=0.7, mouth=0.9), 400, 300, 600)[1]
    assert below < 0.2 < down < 0.5 < slightly < 1
    # the same face upside down (a photo not turned yet) faces the camera as well
    upside = np.frombuffer(landmarks(0.5, 0.4, 0.1), dtype=np.float32).reshape(5, 2)
    upside = (1 - upside).astype(np.float32)
    assert face_years.measure(upside.tobytes(), 400, 300, 600)[1] == pytest.approx(1)
    # too small to line up; no landmarks or no size
    assert face_years.measure(landmarks(0.5, 0.4, 0.04), 400, 300, 600) is None
    assert face_years.measure(None, 400, 300, 600) is None and face_years.measure(landmarks(.5, .4, .1), 0, 0, 600) is None
    # larger, facing the camera, sharper and named by hand are each better
    best = face_years.score(60, 1, 26, True)
    assert best > face_years.score(60, 1, 26, False) > face_years.score(30, 1, 26, False)
    assert face_years.score(60, 1, 26, False) > face_years.score(60, 0.4, 26, False) > 0
    assert face_years.score(60, 1, 26, False) > face_years.score(60, 1, 15, False)


def test_a_face_is_lined_up_on_its_eyes(tmp_path):
    # a photo with a face tilted: the eye on the left red, the other blue, higher up
    im = Image.new("RGB", (400, 300), (200, 200, 200))
    draw = ImageDraw.Draw(im)
    left, right = (100, 150), (180, 110)
    for (x, y), colour in ((left, (255, 0, 0)), (right, (0, 0, 255))):
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=colour)
    picture = tmp_path / "preview.png"
    im.save(picture)
    marks = np.array([(left[0] / 400, left[1] / 300), (right[0] / 400, right[1] / 300), (0.35, 0.5), (0.3, 0.6),
                      (0.4, 0.6)], dtype=np.float32).tobytes()
    out = face_years.aligned(picture, marks)
    width, height = face_years.SIZE
    assert out.size == (width, height)
    wanted = face_years.EYES_APART * width
    eye_y = round(face_years.EYES_AT * height)
    red, blue = out.getpixel((round((width - wanted) / 2), eye_y)), out.getpixel((round((width + wanted) / 2), eye_y))
    assert red[0] > 200 and red[2] < 60, red     # level, at the same place in every picture
    assert blue[2] > 200 and blue[0] < 60, blue
    assert all(abs(c - 199) <= 3 for c in out.getpixel((2, 2)))  # past the photo's edge: its average colour
    # a huge face is made smaller first, and still lands on the same places
    big = Image.new("RGB", (2000, 1500), (200, 200, 200))
    draw = ImageDraw.Draw(big)
    for (x, y), colour in (((600, 700), (255, 0, 0)), ((1400, 700), (0, 0, 255))):
        draw.ellipse((x - 30, y - 30, x + 30, y + 30), fill=colour)
    big.save(picture)
    marks = np.array([(0.3, 700 / 1500), (0.7, 700 / 1500), (0.5, 0.6), (0.4, 0.7), (0.6, 0.7)],
                     dtype=np.float32).tobytes()
    out = face_years.aligned(picture, marks)
    assert out.getpixel((round((width - wanted) / 2), eye_y))[0] > 200
    assert out.getpixel((round((width + wanted) / 2), eye_y))[2] > 200
    with pytest.raises(ValueError):
        face_years.aligned(picture, np.zeros(10, dtype=np.float32).tobytes())


@pytest.fixture
def lib_cfg(tmp_path):
    """A library of its own, with previews big enough to line faces up on (the usual test one makes 48 px ones)."""
    root = tmp_path / "lib"
    root.mkdir()
    return Config(share_root=tmp_path, folders=("lib",), data_dir=tmp_path / "data", ingest_threads=2,
                  preview_size=600, thumb_size=100, update_repository="")


@pytest.fixture
def emma(lib_cfg):
    """Emma Miller, born 1985, in photos from 1990 to 1993 (and some that mustn't count)."""
    root = lib_cfg.share_root / "lib"
    photos = {  # name: (taken, size)
        "1990_small.jpg": ("1990:06:01 10:00:00", (400, 300)),
        "1990_big.jpg": ("1990:07:01 10:00:00", (400, 300)),
        "1991_profile.jpg": ("1991:03:01 10:00:00", (400, 300)),
        "1991_facing.jpg": ("1991:04:01 10:00:00", (400, 300)),
        "1992_portrait.jpg": ("1992:05:01 10:00:00", (300, 400)),
        "1992_group.jpg": ("1992:05:02 10:00:00", (400, 300)),
        "1993_tagged.jpg": ("1993:01:01 10:00:00", (300, 400)),
        "1993_not.jpg": ("1993:02:01 10:00:00", (300, 400)),
    }
    for name, (taken, size) in photos.items():
        write_jpeg(root / "Emma" / name, size=size, taken=taken)
    write_jpeg(root / "misc" / "copied.jpg", size=(400, 300))  # no date of its own: the file's time
    write_jpeg(root / "Emma" / "1994_video.jpg", size=(400, 300), taken="1994:01:01 10:00:00")
    write_jpeg(root / "Emma" / "1960s.jpg", size=(400, 300), taken="1995:01:01 10:00:00")
    con = db.connect(lib_cfg)
    scan(con, lib_cfg)
    ingest(con, lib_cfg)
    ids = {p.split("/")[-1]: i for p, i in con.execute("SELECT path, image_id FROM file")}
    pid = people.person_id(con, "Emma Miller")
    other = people.person_id(con, "Jack Baker")
    con.execute("INSERT INTO event(person_id, tag, date, sort_date) VALUES (?, 'BIRT', '1985', '1985')", (pid,))
    v = (np.ones(512) / np.sqrt(512)).astype(np.float32).tobytes()

    def face(name, cx, cy, apart, who=pid, by="user", turn=0.0, quality=26):
        box = (cx - apart, cy - apart, cx + apart, cy + apart * 1.5)
        return con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, landmarks, det_score, quality, embedding,
                                               person_id, assigned_by) VALUES (?, ?, ?, ?, ?, ?, 0.9, ?, ?, ?, ?)""",
                           (ids[name], *box, landmarks(cx, cy, apart, turn), quality, v, who, by)).lastrowid

    faces = {
        "1990_small": face("1990_small.jpg", 0.5, 0.4, 0.06),
        "1990_big": face("1990_big.jpg", 0.5, 0.4, 0.15, by="auto"),  # larger wins, though only recognised
        "1991_profile": face("1991_profile.jpg", 0.5, 0.4, 0.15, turn=0.5),
        "1991_facing": face("1991_facing.jpg", 0.5, 0.4, 0.15),
        "1992_portrait": face("1992_portrait.jpg", 0.5, 0.3, 0.12),
        "1992_group": face("1992_group.jpg", 0.3, 0.4, 0.2),
        "1993_tagged": face("1993_tagged.jpg", 0.5, 0.3, 0.12),
        "1993_not": face("1993_not.jpg", 0.5, 0.3, 0.2),
        "copied": face("copied.jpg", 0.5, 0.4, 0.2),
        "video": face("1994_video.jpg", 0.5, 0.4, 0.2),
        "1960s": face("1960s.jpg", 0.5, 0.4, 0.2),
        "set aside": face("1990_big.jpg", 0.2, 0.4, 0.2, by="rejected"),
    }
    face("1992_group.jpg", 0.7, 0.4, 0.15, who=other)  # Jack beside her, as near the camera
    con.execute("UPDATE image SET media = 'video' WHERE id = ?", (ids["1994_video.jpg"],))
    con.execute("""UPDATE image SET taken_at_source = 'user', date_text = '1960s', date_from = '1960-01-01',
                   date_to = '1969-12-31' WHERE id = ?""", (ids["1960s.jpg"],))
    for name, kind in (("1992_portrait.jpg", "portrait"), ("1992_group.jpg", "portrait")):
        con.execute("""INSERT INTO analysis(image_id, model, prompt_version, created_at, photo_type, raw_json)
                       VALUES (?, 'test', 1, '2026-01-01', ?, '{}')""", (ids[name], kind))
    tag = con.execute("INSERT INTO tag(name) VALUES ('portrait')").lastrowid
    con.execute("INSERT INTO image_tag(image_id, tag_id, source) VALUES (?, ?, 'user')", (ids["1993_tagged.jpg"], tag))
    con.execute("INSERT INTO image_tag(image_id, tag_id, source) VALUES (?, ?, 'not')", (ids["1993_not.jpg"], tag))
    con.execute("""INSERT INTO analysis(image_id, model, prompt_version, created_at, photo_type, raw_json)
                   VALUES (?, 'test', 1, '2026-01-01', 'selfie', '{}')""", (ids["1993_not.jpg"],))
    con.commit()
    yield {"con": con, "pid": pid, "faces": faces, "ids": ids}
    con.close()


def test_a_face_for_each_year(lib_cfg, emma):
    con, pid, f = emma["con"], emma["pid"], emma["faces"]
    found = face_years.years(con, pid, lib_cfg.preview_size)
    assert [y.year for y in found] == ["1990", "1991", "1992", "1993"]  # not the copied, video or 1960s photos
    best = {y.year: [x["id"] for x in y.faces] for y in found}
    assert best["1990"][0] == f["1990_big"] and f["set aside"] not in best["1990"]
    assert best["1991"] == [f["1991_facing"], f["1991_profile"]]  # facing the camera first; the other kept
    assert best["1993"][0] == f["1993_not"]  # every photo: a portrait or not
    first = found[0].faces[0]
    assert first["age"] == "about 5" and first["taken_at"].startswith("1990-07-01")
    # portraits: what the AI called one, or tagged one, with nobody else as near the camera; a portrait tag taken off
    # leaves a photo out, whatever the AI said
    posed = face_years.years(con, pid, lib_cfg.preview_size, portraits=True)
    assert {y.year: [x["id"] for x in y.faces] for y in posed} == {"1992": [f["1992_portrait"]],
                                                                     "1993": [f["1993_tagged"]]}
    assert len(face_years.years(con, pid, lib_cfg.preview_size, per_year=1)[1].faces) == 1


def test_the_page_and_its_pictures(lib_cfg, emma):
    con, pid, f = emma["con"], emma["pid"], emma["faces"]
    client = TestClient(webapp.create_app(lib_cfg), client=LAN)
    assert f'href="/person/{pid}/years"' in client.get(f"/person/{pid}").text
    page = client.get(f"/person/{pid}/years").text
    assert "Every photo <small>4 years</small>" in page and "Portraits <small>2 years</small>" in page
    assert re.findall(r'data-year="(\d+)"', page) == ["1990", "1991", "1992", "1993"]
    assert f'src="/face/{f["1990_big"]}/eyes"' in page and "age about 5" in page
    posed = client.get(f"/person/{pid}/years?mode=portraits").text
    assert re.findall(r'data-year="(\d+)"', posed) == ["1992", "1993"]
    assert client.get(f"/person/{pid}/years?mode=other").status_code == 400
    assert client.get("/person/99999/years").status_code == 404
    # the picture: made once from the preview, then kept beside the face's crops
    r = client.get(f"/face/{f['1990_big']}/eyes")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    row = con.execute("SELECT x1, y1 FROM face WHERE id = ?", (f["1990_big"],)).fetchone()
    kept = lib_cfg.aligned_face_path(f["1990_big"], row["x1"], row["y1"])
    assert kept.is_file() and Image.open(kept).size == face_years.SIZE
    assert kept in lib_cfg.face_crops(f["1990_big"])  # removed with them when the photo goes
    # a face with no landmarks, or on a video's frame, isn't lined up
    con.execute("UPDATE face SET landmarks = NULL WHERE id = ?", (f["1991_profile"],))
    con.execute("UPDATE face SET frame = 0 WHERE id = ?", (f["1991_facing"],))
    con.commit()
    assert client.get(f"/face/{f['1991_profile']}/eyes").status_code == 404
    assert client.get(f"/face/{f['1991_facing']}/eyes").status_code == 404


def test_nobody_to_show(lib_cfg, emma):
    con = emma["con"]
    pid = people.person_id(con, "Ruth Miller")
    con.commit()
    page = TestClient(webapp.create_app(lib_cfg), client=LAN).get(f"/person/{pid}/years").text
    assert "No dated photo shows Ruth Miller" in page and "data-years-player" not in page


def test_a_panel_on_their_home(lib_cfg, emma):
    con, pid, f = emma["con"], emma["pid"], emma["faces"]
    client = TestClient(webapp.create_app(lib_cfg), client=LAN)
    # their Home loads it after the page; it plays their face a year and opens the whole of it
    assert f'data-later="/home/person/{pid}/years"' in client.get(f"/home/person/{pid}").text
    panel = client.get(f"/home/person/{pid}/years").text
    assert f'href="/person/{pid}/years"' in panel and "data-years-panel" in panel
    assert f'src="/face/{f["1990_big"]}/eyes"' in panel and "Emma from 1990 to 1993" in panel
    frames = json.loads(html.unescape(re.search(r"data-frames='([^']*)'", panel).group(1)))
    assert [x["year"] for x in frames] == ["1990", "1991", "1992", "1993"]
    assert frames[1]["faces"] == [f["1991_facing"], f["1991_profile"]]  # the best first, as on its page
    # someone with fewer than two years shows nothing; nobody there, not found
    con.execute("UPDATE face SET person_id = NULL, assigned_by = NULL WHERE person_id = ? AND image_id IN "
                "(SELECT id FROM image WHERE substr(taken_at, 1, 4) != '1990')", (pid,))
    con.commit()
    fresh = TestClient(webapp.create_app(lib_cfg), client=LAN)  # its own app: nothing kept from before
    assert "data-years-panel" not in fresh.get(f"/home/person/{pid}/years").text
    assert fresh.get("/home/person/99999/years").status_code == 404
