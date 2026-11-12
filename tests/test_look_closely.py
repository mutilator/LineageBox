"""Looking closely at every photo (faces.look_again_all, the worker's stage, Settings → Faces): the closer look a
photo's page gives, for every photo once and again every few months; faces of a photo on its side found by looking at
it turned; and what people said about faces left alone."""
from datetime import datetime, timedelta

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from lineagebox import faces, orientation, settings
from lineagebox.web import app as webapp
from tests.conftest import LAN
from tests.test_face_checks import A, face, like, photo

APP = {"X-Requested-With": "lineagebox"}


def test_points_found_on_a_turned_photo_are_put_back():
    # a mark on a 48 x 24 picture, found again on it turned each way
    im = Image.new("L", (48, 24), 0)
    im.putpixel((5, 7), 255)
    for turn in (90, 180, 270):
        turned = np.asarray(im.rotate(-turn, expand=True))
        v, u = np.argwhere(turned == 255)[0]
        x, y = faces.unturn(np.array([[u + 0.5, v + 0.5]]), turn, im.size)[0]
        assert (int(x), int(y)) == (5, 7), turn


def previewed(con, cfg, n, size=(48, 24), people=None, looked=True):
    """A photo with a preview to look at (dark in its top left corner, as it is), the AI counting `people` in it."""
    photo(con, n, width=size[0] * 10, height=size[1] * 10)
    im = Image.new("RGB", size, (200, 200, 200))
    im.paste((0, 0, 0), (0, 0, 6, 6))
    path = cfg.preview_path(f"{n:064x}")
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    if looked:
        con.execute("UPDATE image SET stage = stage | ? WHERE id = ?", (faces.STAGE_FACES, n))
    if people is not None:
        con.execute("""INSERT INTO analysis(image_id, model, prompt_version, created_at, people_count, raw_json)
                            VALUES (?, 'm', 1, '2026-11-07T10:00:00', ?, '{}')""", (n, people))
    return n



class Model:
    """The closer look's models: a face only on the picture turned a quarter clockwise (its dark corner then top
    right), upright there; and, as it is, a faint one (0.4) and one where a face was marked as not a face."""
    upright = (np.array([[2, 2, 12, 14, 0.9]]),
               np.array([[[5, 5], [9, 5], [7, 8], [5.5, 11], [8.5, 11]]], dtype=np.float64))

    class det_model:
        @staticmethod
        def detect(bgr, max_num, metric):
            h, w = bgr.shape[:2]
            if h > w and bgr[:3, -3:].mean() < 60:  # turned 90: the dark corner at the top right
                return Model.upright
            if w > h and bgr[:3, :3].mean() < 60:  # as it is
                return (np.array([[30, 2, 40, 12, 0.4], [0, 0, 24, 24, 0.9]]), np.zeros((2, 5, 2)))
            return np.zeros((0, 5)), np.zeros((0, 5, 2))

    class Reader:
        @staticmethod
        def get(bgr, f):
            f.embedding = 22.0 * like(A)

    models = {"detection": None, "recognition": Reader}


def test_a_photo_on_its_side_has_its_faces_found_turned(con, cfg, monkeypatch):
    side = previewed(con, cfg, 1, people=2)
    aside = face(con, side, like(A), by="not_a_face")  # a box someone said isn't a face (0..0.4 of the width)
    con.execute("UPDATE face SET x1 = 0, y1 = 0, x2 = 0.5, y2 = 1 WHERE id = ?", (aside,))
    con.commit()
    new, back = faces.look_again(con, cfg, side, app=Model(), bar=faces.ROUND_BAR, bring_back=False, turned=True)
    # the faint face isn't kept, the one marked not a face stays so, and the face found turned is put back in place
    assert back == [] and con.execute("SELECT assigned_by FROM face WHERE id = ?", (aside,)).fetchone()[0] == "not_a_face"
    assert len(new) == 1
    row = con.execute("SELECT x1, y1, x2, y2, landmarks, det_score FROM face WHERE id = ?", (new[0],)).fetchone()
    assert np.allclose(tuple(row)[:4], (2 / 48, 12 / 24, 14 / 48, 22 / 24))
    # its landmarks say the photo is on its side: Turned photos lists it, turned a quarter clockwise, as sure even when
    # the AI called it fine (it did, for most of a folder of scanned prints on their side)
    assert orientation.face_turn(row["landmarks"], 480, 240) == 90
    con.execute("UPDATE face SET quality = 22 WHERE id = ?", (new[0],))  # clear enough to count (usable_faces_sql)
    settings.put(con, orientation.AI_KEY, {str(side): "fine"})
    assert [(x["id"], x["turn"], x["doubt"]) for x in orientation.suggestions(con, "sure")] == [(side, 90, None)]
    assert con.execute("SELECT faces_again FROM image WHERE id = ?", (side,)).fetchone()[0]


def test_asked_on_its_page_the_look_is_as_before(con, cfg):
    side = previewed(con, cfg, 2, people=2)
    aside = face(con, side, like(A), by="not_a_face")
    con.execute("UPDATE face SET x1 = 0, y1 = 0, x2 = 0.5, y2 = 1 WHERE id = ?", (aside,))
    con.commit()
    new, back = faces.look_again(con, cfg, side, app=Model())  # the button: from 0.35, and someone asked
    assert back == [aside] and len(new) == 1  # the faint face is kept, no turned look (one was found)
    assert con.execute("SELECT det_score FROM face WHERE id = ?", (new[0],)).fetchone()[0] == 0.4


def test_every_photo_once_and_again_every_few_months(con, cfg, monkeypatch):
    ids = [previewed(con, cfg, n, people=p) for n, p in ((11, 0), (12, 3), (13, None))]
    previewed(con, cfg, 14, looked=False)  # its first look isn't done: not yet
    con.commit()
    clock = {"now": datetime(2026, 11, 11, 12, 0, 0)}
    asked = []

    def look(c, cfg_, image_id, app=None, bar=None, bring_back=True, turned=False):
        asked.append((image_id, bar, bring_back, turned))
        c.execute("UPDATE image SET faces_again = ? WHERE id = ?", (clock["now"].isoformat(timespec="seconds"), image_id))
        c.commit()
        return ([100 + image_id], [])
    monkeypatch.setattr(faces, "ensure_models", lambda *a: None)
    monkeypatch.setattr(faces, "closer_look_model", lambda cfg_: "models")
    monkeypatch.setattr(faces, "look_again", look)
    run = lambda every=6: faces.look_again_all(con, cfg, every, now=clock["now"])

    # the first round: every photo whose first look is done, the newest first, turned only where the AI sees people
    assert run() == (3, [113, 112, 111])
    assert asked == [(13, 0.5, False, False), (12, 0.5, False, True), (11, 0.5, False, False)]
    assert faces.round_progress(con)["left"] == 0 and faces.round_state(con)["finished"] is None
    assert run() == (0, []) and faces.round_state(con)["finished"] == "2026-11-11T12:00:00"
    # a new photo is looked at closely after its first look, whatever the round
    con.execute("UPDATE image SET stage = stage | ? WHERE id = 14", (faces.STAGE_FACES,))
    clock["now"] += timedelta(days=30)
    asked.clear()
    assert run() == (1, [114]) and [a[0] for a in asked] == [14]
    # six months after the round finished, every photo again; never, when only asked for
    clock["now"] = datetime(2027, 5, 1, 12, 0, 0)
    assert run(0) == (0, [])
    clock["now"] = datetime(2027, 5, 20, 12, 0, 0)
    assert run(6)[0] == 4 and faces.round_state(con)["started"] == "2027-05-20T12:00:00"
    run(6)
    assert faces.round_state(con)["finished"] == "2027-05-20T12:00:00"


def test_settings_say_how_far_and_start_it_again(con, cfg):
    for n in (21, 22):
        previewed(con, cfg, n)
    con.commit()
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get("/settings").text
    assert 'name="faces_again_every" value="6" checked' in page
    assert "Looking closely: 2 of" in page and "/api/faces/look-again-all" not in page
    # and the Status page counts them with what else is waiting
    status = client.get("/status").text
    row = status[status.index('<span class="work-what">Photos to look at closely for faces'):].split("</li>")[0]
    assert '<span class="work-numbers">0 of 2</span>' in row and 'aria-valuenow="0"' in row
    # under way: asking again changes nothing
    r = client.post("/api/faces/look-again-all", headers=APP).json()
    assert "being looked at already" in r["message"] and faces.round_state(con) == {}
    # done: the button starts a round from now, and a pass
    con.execute("UPDATE image SET faces_again = '2026-11-01T00:00:00'")
    settings.put(con, faces.ROUND_KEY, {"started": "2026-10-01T00:00:00", "finished": "2026-11-01T00:00:00"})
    con.commit()
    page = client.get("/settings").text
    assert "Every photo was looked at closely by" in page and 'data-api="/api/faces/look-again-all"' in page
    assert client.post("/api/faces/look-again-all", headers=APP).json()["reload"] is True
    assert faces.round_state(con)["finished"] is None and faces.round_progress(con)["left"] == 2
    assert settings.get(con, "run_requested")
    # the choice is saved, and only one of those offered
    assert client.post("/api/settings", json={"faces_again_every": "12"}, headers=APP).status_code == 200
    assert settings.current(con, cfg).faces_again_every == 12
    assert client.post("/api/settings", json={"faces_again_every": "7"}, headers=APP).status_code == 400
    assert cfg.faces_again_every == 6  # the default
