"""Cleanup → No faces found (faces.no_faces, routes/face_checks.py): photos where the AI's description sees people and
no face was found, looked at again by the worker or set aside for good; and Faces missed keeping to photos with some
faces found."""
from fastapi.testclient import TestClient

from lineagebox import faces, people, settings
from lineagebox.web import app as webapp
from tests.conftest import LAN
from tests.test_face_checks import A, face, like, photo

APP = {"X-Requested-With": "lineagebox"}


def seen(con, image_id, people_count, caption="Two people walking away along a beach", looked=True, sure=0.9):
    """The AI's description of a photo, counting people, and its careful count of them (headcount.py), as sure as
    this; looked: its faces were looked for."""
    con.execute("""INSERT INTO analysis(image_id, model, prompt_version, created_at, caption, people_count, raw_json)
                   VALUES (?, 'm', 1, '2026-11-07T10:00:00', ?, ?, '{}')""", (image_id, caption, people_count))
    counted(con, image_id, people_count, sure)
    if looked:
        con.execute("UPDATE image SET stage = stage | ? WHERE id = ?", (faces.STAGE_FACES, image_id))


def counted(con, image_id, people, sure=0.9, faces=None, faces_sure=None):
    """The AI's careful count of the people in a photo (headcount.py), and of the faces it sees."""
    con.execute("""INSERT OR REPLACE INTO headcount(image_id, model, version, counted_at, people, people_sure, faces,
                                                    faces_sure) VALUES (?, 'm', 1, '2026-11-11T10:00:00', ?, ?, ?, ?)""",
                (image_id, people, sure, faces, faces_sure))


def library(con):
    ids = {"beach": photo(con, 1, "2019-07-04T12:00:00"), "crowd": photo(con, 2, "2020-07-04T12:00:00"),
           "named": photo(con, 3, "2021-07-04T12:00:00"), "empty": photo(con, 4, "2018-07-04T12:00:00"),
           "waiting": photo(con, 5, "2022-07-04T12:00:00"), "again": photo(con, 6, "2017-07-04T12:00:00"),
           "toy": photo(con, 7, "2016-07-04T12:00:00")}
    seen(con, ids["beach"], 2)
    seen(con, ids["crowd"], 9, "A crowd watching fireworks from behind")
    seen(con, ids["named"], 5)
    face(con, ids["named"], like(A), people.person_id(con, "Owen Miller"), "user")  # some found: Faces missed
    seen(con, ids["empty"], 0, "A sunset over the lake")  # nobody seen
    seen(con, ids["waiting"], 1, looked=False)  # faces not looked for yet
    seen(con, ids["again"], 1)
    con.execute("UPDATE image SET faces_again = '2026-11-07T10:00:00' WHERE id = ?", (ids["again"],))
    seen(con, ids["toy"], 1)
    con.execute("UPDATE face SET assigned_by = 'not_a_face' WHERE id = ?",
                (face(con, ids["toy"], like(A)),))  # a doll's face, set aside: none found
    con.commit()
    return ids


def test_which_photos_are_listed(con):
    ids = library(con)
    rows, total = faces.no_faces(con)
    assert total == 3 and [r["id"] for r in rows] == [ids["crowd"], ids["beach"], ids["toy"]]  # the newest first
    assert rows[0]["people"] == 9 and rows[0]["caption"] == "A crowd watching fireworks from behind"
    assert faces.no_faces(con, 1, 1)[0][0]["id"] == ids["beach"]
    # a group photo with some faces found is under Faces missed, one with none only here
    assert [r["id"] for r in faces.missed(con)[0]] == [ids["named"]]
    # set aside, or waiting to be looked at again: not listed
    settings.put(con, faces.NONE_OK_KEY, [ids["crowd"]])
    settings.put(con, faces.LOOK_AGAIN_KEY, [ids["beach"]])
    assert [r["id"] for r in faces.no_faces(con)[0]] == [ids["toy"]]


def test_the_page_and_its_buttons(con, cfg):
    ids = library(con)
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get("/cleanup/no-faces").text
    assert 'value="2"' in page and 'value="1"' in page and 'value="3"' not in page
    assert "9 people, 90% sure" in page and 'title="A crowd watching fireworks from behind"' in page
    assert 'data-api="/api/images/look-again"' in page and 'data-api="/api/images/no-faces-ok"' in page
    card = client.get("/cleanup/counts/nofaces").text
    assert "No faces found" in card and '<span class="num">3</span>' in card
    assert 'href="/cleanup/no-faces"' in client.get("/cleanup/faces?show=missed").text

    # no faces to find in the crowd: off the list, and Undo lists it again
    assert client.post("/api/images/no-faces-ok", json={"image_ids": [ids["crowd"]]}).status_code == 403
    r = client.post("/api/images/no-faces-ok", json={"image_ids": [ids["crowd"], 999]}, headers=APP)
    assert r.status_code == 200 and r.json()["undo"]
    assert settings.get(con, faces.NONE_OK_KEY) == [ids["crowd"]]
    assert 'value="2"' not in client.get("/cleanup/no-faces").text
    assert client.post(f"/api/changes/{r.json()['undo']}/undo-own", headers=APP).status_code == 200
    assert not settings.get(con, faces.NONE_OK_KEY, [])
    assert client.post("/api/images/no-faces-ok", json={"image_ids": [999]}, headers=APP).status_code == 404

    # looked at again by the worker: waiting, off the list, and a pass asked for
    r = client.post("/api/images/look-again", json={"image_ids": [ids["beach"], ids["beach"], ids["toy"]]},
                    headers=APP)
    assert r.status_code == 200 and "2 photos will be looked at again" in r.json()["message"]
    assert settings.get(con, faces.LOOK_AGAIN_KEY) == [ids["beach"], ids["toy"]]
    assert settings.get(con, "run_requested")
    page = client.get("/cleanup/no-faces").text
    assert "2 photos are waiting to be looked at again." in page and 'value="1"' not in page
    assert "2 are waiting to be looked at again." in client.get("/cleanup/counts/nofaces").text
    # past the end: its last page
    assert 'value="2"' in client.get("/cleanup/no-faces", params={"page": 9}).text


def test_the_worker_looks_again(con, cfg, monkeypatch):
    ids = library(con)
    settings.put(con, faces.LOOK_AGAIN_KEY, [ids["beach"], 999, ids["toy"]])
    looked = []

    def look(c, cfg_, image_id, app=None):
        assert app == "models"  # loaded once for them all
        if image_id == 999:
            raise FileNotFoundError("gone")
        looked.append(image_id)
        c.execute("UPDATE image SET faces_again = '2026-11-08T10:00:00' WHERE id = ?", (image_id,))
        c.commit()
        return ([face(c, image_id, like(A))] if image_id == ids["beach"] else []), []

    monkeypatch.setattr(faces, "ensure_models", lambda *a: None)
    monkeypatch.setattr(faces, "closer_look_model", lambda cfg_: "models")
    monkeypatch.setattr(faces, "look_again", look)
    stops = iter([False, False, True])
    done, found = faces.look_again_waiting(con, cfg, stop=lambda: next(stops))
    assert done == 2 and looked == [ids["beach"]] and len(found) == 1
    assert settings.get(con, faces.LOOK_AGAIN_KEY) == [ids["toy"]]  # stopped: the rest wait for the next pass
    assert faces.look_again_waiting(con, cfg) == (1, []) and settings.get(con, faces.LOOK_AGAIN_KEY) == []
    assert faces.look_again_waiting(con, cfg) == (0, [])
    assert faces.no_faces(con)[1] == 1  # the crowd is left; the two looked at again leave the list
