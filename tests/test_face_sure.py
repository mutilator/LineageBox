"""How sure a recognised or suggested name is (people.how_sure): kept by recognition and worked out again when the faces
named by hand change (people.measure_recognised), shown on Name faces and Check faces, and yes to the surest faces of
a step at once, undone as one change."""
import re

from fastapi.testclient import TestClient

from lineagebox import people
from lineagebox.web import app as webapp
from tests.conftest import LAN
from tests.test_naming_queue import APP, card_face, face_row, queue  # noqa: F401 (queue is a fixture)
from tests.test_people import A, B, add_face, lib, like, unit  # noqa: F401 (lib is a fixture)


def bars(page):
    """Yes to the surest at once: {bar: faces at least that sure}."""
    return {int(b): int(n) for b, n in re.findall(r'sure=(\d+)"(?: aria-current="page")?>\1% sure <span>(\d+)</span>',
                                                  page)}


def test_how_sure_rises_with_the_score_and_the_lead_and_is_never_certain():
    assert people.how_sure(None, 0.2) is None and people.how_sure(0.6, None) is None
    low = people.how_sure(0.35, 0.05)
    assert 0.8 < low < people.how_sure(0.45, 0.05) < 0.98 and low < people.how_sure(0.35, 0.15)
    assert people.how_sure(0.9, 0.5) == people.SURE_MOST == 0.98
    assert people.how_sure(0.4, -0.2) < 0.2  # looks more like someone else


def test_recognition_keeps_the_lead_and_the_worker_works_it_out_again(queue):
    q, con = queue, queue["con"]
    score, lead = con.execute("SELECT assign_score, assign_lead FROM face WHERE id = ?", (q["recognised"],)).fetchone()
    assert score >= people.AUTO_SIMILARITY and lead >= people.AUTO_MARGIN
    assert people.how_sure(score, lead) == 0.98
    assert people.measure_recognised(con) == 1  # the first time
    assert people.measure_recognised(con) == 0  # nothing named by hand since
    # more of Lucas named by hand, and the recognised face looks more like him than like Owen (as faces named since can
    # show): it is far from sure, and asked about before a surer one
    con.execute("UPDATE face SET embedding = ? WHERE id = ?", (unit(A + 1.5 * B).tobytes(), q["recognised"]))
    people.name_faces(con, [add_face(con, q["lu"][1], like(B), x=0.7)], "Lucas")
    sure = add_face(con, q["o"][5], like(A), x=0.7)
    con.execute("UPDATE face SET person_id = ?, assigned_by = 'auto', assign_score = 0.9, assign_lead = 0.5 WHERE id = ?",
                (q["owen"], sure))
    con.commit()
    assert people.measure_recognised(con) == 2
    score, lead = con.execute("SELECT assign_score, assign_lead FROM face WHERE id = ?", (q["recognised"],)).fetchone()
    assert lead < 0 and people.how_sure(score, lead) < 0.5
    assert face_row(con, q["recognised"]) == (q["owen"], "auto")  # the name stays: only how sure it is changed
    assert [f for f, _ in people.recognised_to_check(con)] == [q["recognised"], sure]
    assert people.recognised_sure(con, 0.9) == {q["owen"]: [sure]}
    # a name said wrong takes the lead with it
    people.not_this_person(con, q["owen"], [q["recognised"]])
    assert tuple(con.execute("SELECT assign_score, assign_lead FROM face WHERE id = ?",
                             (q["recognised"],)).fetchone()) == (None, None)


def test_name_faces_says_how_sure_and_yes_to_the_surest_at_once(queue, cfg):
    q, con = queue, queue["con"]
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get("/people/name?only=recognised").text
    assert card_face(page) == ("face", q["recognised"]) and "About 98% sure." in page
    assert client.get(f"/api/faces/{q['recognised']}/who").json()["named"]["sure"] == 0.98
    assert "Yes to the surest at once" in page and bars(page) == {98: 1, 95: 1, 90: 1}
    assert "Yes to the 1 face at least 98% sure" in page and "about 2 in 100" in page
    assert "Yes to the 1 face at least 90% sure" in client.get("/people/name?only=recognised&sure=90").text
    assert "at least 97% sure" not in client.get("/people/name?only=recognised&sure=97").text  # only the bars offered
    for elsewhere in ("/people/name", "/people/name?only=groups", "/people/name?only=asked"):
        assert "Yes to the surest at once" not in client.get(elsewhere).text, elsewhere

    # the face beside Lucas that looks like Owen says how sure too
    page = client.get(f"/people/name?only=suggested").text
    assert card_face(page) == ("face", q["brother"]) and re.search(r"About \d+% sure\.", page)
    assert bars(page)[90] == 1

    url = "/api/faces/sure-yes"
    assert client.post(url, json={"step": "recognised", "at_least": 98}).status_code == 403  # the site's scripts only
    assert client.post(url, json={"step": "groups", "at_least": 98}, headers=APP).status_code == 400
    assert client.post(url, json={"step": "recognised", "at_least": 20}, headers=APP).status_code == 400
    said = client.post(url, json={"step": "recognised", "at_least": 98}, headers=APP).json()
    assert said["named"] == 1 and said["message"] == "Said yes to 1 face." and said["undo"]
    assert face_row(con, q["recognised"]) == (q["owen"], "user")
    said = client.post(url, json={"step": "suggested", "at_least": 90}, headers=APP).json()
    assert said["named"] == 1 and face_row(con, q["brother"]) == (q["owen"], "user")
    assert client.post(url, json={"step": "recognised", "at_least": 98}, headers=APP).json()["message"] == \
        "No faces are that sure just now."

    # undone as one change: the brother unnamed again
    assert client.post(f"/api/changes/{said['undo']}/undo-own", headers=APP).status_code == 200
    assert face_row(con, q["brother"]) == (None, None) and face_row(con, q["recognised"]) == (q["owen"], "user")


def test_check_faces_says_how_sure_each_is(queue, cfg):
    q = queue
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get(f"/person/{q['owen']}/faces").text
    assert "least sure first" in page and re.search(r'class="sure" title="Recognition is about 98% sure this is them">98%<',
                                                    page)
