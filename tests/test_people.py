import html
import re

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from lineagebox import events, family, people, search, settings, tours
from lineagebox.folders import FolderIndex
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests.conftest import LAN, rel, write_jpeg

RNG = np.random.default_rng(7)
DIM = 512


def unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


A, B = unit(RNG.standard_normal(DIM)), unit(RNG.standard_normal(DIM))  # two people, unrelated directions


def like(v, noise=0.3):
    """Another photo of the same person: cosine ~0.96 to v, ~0.92 to other photos of them."""
    return unit(v + noise * unit(RNG.standard_normal(DIM)))


@pytest.fixture
def lib(con, cfg, library):
    """12 photos big enough for usable faces: Owen\\o0-5 and Lucas\\l0-5."""
    for i in range(6):
        write_jpeg(library / "Owen" / f"o{i}.jpg", size=(200, 200), color=(40 * i, 10, 10))
        write_jpeg(library / "Lucas" / f"l{i}.jpg", size=(200, 200), color=(10, 40 * i, 10))
    scan(con, cfg)
    ingest(con, cfg)
    con.execute("""UPDATE folder SET kind = 'person', people_hint = '["Owen"]' WHERE path = 'lib/Owen'""")
    con.commit()
    search.rebuild(con)
    return con


def image(con, path):
    return con.execute("SELECT image_id FROM file WHERE path = ?", (path,)).fetchone()[0]


def add_face(con, image_id, emb, quality=22.0, x=0.0):
    return con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding)
                          VALUES (?, ?, 0, ?, 1, 0.9, ?, ?)""", (image_id, x, x + 0.5, quality, emb.tobytes())).lastrowid


def test_cluster_name_identify_and_search(lib, cfg):
    con = lib
    owen = [image(con, rel("Owen", f"o{i}.jpg")) for i in range(6)]
    lucas = [image(con, rel("Lucas", f"l{i}.jpg")) for i in range(6)]
    o_faces = [add_face(con, i, like(A)) for i in owen[:5]]
    for i in lucas[:4]:
        add_face(con, i, like(B))
    blurry = add_face(con, owen[5], like(A), quality=9.0)  # below the quality bar: never clustered or recognised
    con.commit()

    clusters, faces = people.cluster(con)
    assert (clusters, faces) == (2, 9)
    o_cluster = con.execute("SELECT cluster_id FROM face WHERE id = ?", (o_faces[0],)).fetchone()[0]
    folders = FolderIndex(con)
    images = [r[0] for r in con.execute("SELECT image_id FROM face WHERE cluster_id = ?", (o_cluster,))]
    assert people.suggest_name(folders, images, taken=set()) == "Owen"
    assert people.suggest_name(folders, images, taken={"owen"}) is None

    people.name_faces(con, people.cluster_faces(con, o_cluster), "Owen")
    assert {r["id"] for r in search.query(con, "owen")} >= set(owen[:5])

    # new faces: one clear Owen, one halfway between the brothers once Lucas is named, two Owens in one photo
    l_cluster = con.execute("SELECT DISTINCT cluster_id FROM face WHERE image_id = ?", (lucas[0],)).fetchone()[0]
    people.name_faces(con, people.cluster_faces(con, l_cluster), "Lucas")
    clear = add_face(con, lucas[5], like(A))
    ambiguous = add_face(con, lucas[4], unit(A + B))
    twin1 = add_face(con, owen[0], like(A), x=0.5)  # owen[0] already has Owen, named by hand
    con.commit()
    assert people.identify(con) == 1
    row = lambda f: con.execute("SELECT person_id, assigned_by FROM face WHERE id = ?", (f,)).fetchone()
    assert row(clear)["assigned_by"] == "auto"
    assert row(ambiguous)["person_id"] is None
    assert row(twin1)["person_id"] is None  # Owen cannot be in the same photo twice
    assert row(blurry)["person_id"] is None

    # a wrong match set aside stays out
    people.set_aside(con, [clear])
    assert people.identify(con) == 0 and row(clear)["assigned_by"] == "rejected"

    # renaming to an existing name merges
    oid = con.execute("SELECT id FROM person WHERE name = 'Owen'").fetchone()[0]
    lid = con.execute("SELECT id FROM person WHERE name = 'Lucas'").fetchone()[0]
    assert people.rename(con, lid, "owen") == oid
    assert con.execute("SELECT count(*) FROM person").fetchone()[0] == 1


def test_seen_together(lib, cfg):
    """An unnamed face next to someone already named is suggested as the closest person who isn't in that photo."""
    con = lib
    o = [image(con, rel("Owen", f"o{i}.jpg")) for i in range(6)]
    lu = [image(con, rel("Lucas", f"l{i}.jpg")) for i in range(6)]
    owen = people.name_faces(con, [add_face(con, i, like(A)) for i in o[:3]], "Owen")
    lucas = people.name_faces(con, [add_face(con, i, like(B)) for i in lu[:3]], "Lucas")
    both = unit(A + 1.1 * B)  # a brother: a little more like Lucas, too close to both for recognition
    brother = add_face(con, lu[0], both, x=0.5)
    twin = add_face(con, lu[0], like(both, 0.5), x=0.8)  # less like Owen, in the same photo: only one can be him
    other = add_face(con, lu[1], both, x=0.5)
    alone = add_face(con, o[3], both)  # nobody named in this photo: left to recognition
    stranger = add_face(con, lu[2], unit(RNG.standard_normal(DIM)), x=0.5)  # like nobody
    con.commit()
    assert people.identify(con) == 0
    assert people.beside_named(con) == 4
    found = people.together(con)
    assert [(s["person_id"], [f["id"] for f in s["faces"]], s["seen_with"]) for s in found] == [
        (owen, [brother, other], [(lucas, 2)])]

    client = TestClient(webapp.create_app(cfg), client=LAN)
    assert 'href="/cleanup/faces"' in client.get("/people").text  # unnamed faces are confirmed under Cleanup
    assert client.get("/people/together?page=1", follow_redirects=False).headers["location"] ==         "/cleanup/faces?page=1&show=together"
    page = client.get("/cleanup/faces").text  # Seen together comes first while it has faces
    assert 'aria-current="page">Seen together' in page
    assert f'data-api="/api/people/{owen}/faces"' in page and "in photos with Lucas (2)" in page
    assert f'value="{brother}"' in page and f'value="{twin}"' not in page and f'value="{alone}"' not in page
    url, app_header = f"/api/people/{owen}/faces", {"X-Requested-With": "lineagebox"}
    assert client.post(url, json={"yes": [brother]}).status_code == 403
    assert client.post("/api/people/9999/faces", json={"yes": [brother]}, headers=app_header).status_code == 404
    # ticked: named; unticked: not him, so neither suggested nor recognised as him again
    assert client.post(url, json={"yes": [brother], "no": [other]}, headers=app_header).json() == {"named": 1}
    row = lambda f: tuple(con.execute("SELECT person_id, assigned_by FROM face WHERE id = ?", (f,)).fetchone())
    assert row(brother) == (owen, "user") and row(other) == (None, None)
    assert people.together(con) == []
    # the page keeps who the faces may be through the reload after a confirm, without what the confirm changed
    page = client.get("/cleanup/faces?show=together").text
    assert f'value="{brother}"' not in page and f'value="{other}"' not in page
    assert f'data-api="/api/people/{owen}/faces"' not in page
    clear = add_face(con, lu[3], like(A), x=0.5)  # plainly Owen, but said not to be
    people.confirm(con, owen, [], [clear])
    assert people.identify(con) == 0 and row(clear) == (None, None)
    assert "No suggestions" in client.get("/cleanup/faces?show=together").text

    # merging carries over who a face is not
    typo = people.person_id(con, "Owenn")
    people.confirm(con, typo, [], [stranger])
    people.merge(con, typo, owen)
    assert {r[0] for r in con.execute("SELECT person_id FROM face_not_person WHERE face_id = ?", (stranger,))} == {owen}


def test_merging_one_person_named_twice(lib, cfg):
    con = lib
    lu = [image(con, rel("Lucas", f"l{i}.jpg")) for i in range(4)]
    lucas = people.name_faces(con, [add_face(con, i, like(B)) for i in lu[:2]], "Lucas")
    typo = people.name_faces(con, [add_face(con, i, like(B)) for i in lu[2:]], "lukas")
    owen = people.person_id(con, "Owen")
    con.execute("UPDATE person SET sex = 'male' WHERE id = ?", (lucas,))
    con.execute("UPDATE person SET sex = 'female' WHERE id = ?", (typo,))
    events.save(con, None, tag="BIRT", person_id=typo, date_text="2014-08-21")
    family.link(con, typo, "brother", owen)
    settings.put(con, "me", typo)
    tour = tours.save_tour(con, None, None, tours.clean({"who": {"mode": "people", "person_ids": [owen, typo]}}),
                           "The boys")
    client = TestClient(webapp.create_app(cfg), client=LAN)
    app_header = {"X-Requested-With": "lineagebox"}

    page = client.get(f"/person/{typo}").text
    assert f'data-api="/api/people/{typo}/merge"' in page
    assert f'<option value="{lucas}">Lucas</option>' in page and f'<option value="{typo}">' not in page
    url = f"/api/people/{typo}/merge"
    assert client.post(url, json={"into": lucas}).status_code == 403
    assert client.post(url, json={"into": typo}, headers=app_header).status_code == 400
    assert client.post(url, json={"into": 9999}, headers=app_header).status_code == 404
    # the page's form sends the picked id as text
    assert client.post(url, json={"into": str(lucas)}, headers=app_header).json() == {"person_id": lucas}

    assert con.execute("SELECT count(*) FROM person WHERE id = ?", (typo,)).fetchone()[0] == 0
    assert {r[0] for r in con.execute("SELECT image_id FROM face WHERE person_id = ?", (lucas,))} == set(lu)
    p = con.execute("SELECT birth_date, sex FROM person WHERE id = ?", (lucas,)).fetchone()
    assert (p["birth_date"], p["sex"]) == ("2014-08-21", "male")  # blanks filled in; what Lucas had stays
    assert [s["id"] for s in family.links(family.load(con), lucas)["siblings"]] == [owen]
    assert family.me(con) == lucas
    assert tours.tour(con, tour)["spec"]["who"]["person_ids"] == [owen, lucas]
    assert {r["id"] for r in search.query(con, "lucas")} >= set(lu)

    # renaming to a new name, or only changing case, keeps the same person
    assert people.rename(con, lucas, "lucas  james") == lucas
    assert people.rename(con, lucas, "Lucas James") == lucas
    assert con.execute("SELECT name FROM person WHERE id = ?", (lucas,)).fetchone()[0] == "Lucas James"
    assert {r["id"] for r in search.query(con, "james")} >= set(lu)


def test_people_pages_and_api(lib, cfg):
    con = lib
    for i in range(5):
        # o4's face is the clearest, so the page shows it first in Owen's group
        add_face(con, image(con, rel("Owen", f"o{i}.jpg")), like(A), quality=30.0 if i == 4 else 22.0)
        add_face(con, image(con, rel("Lucas", f"l{i}.jpg")), like(B))
    con.commit()
    people.cluster(con)  # HDBSCAN never reports a single all-in-one cluster, so the test needs two people
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get("/cleanup/faces?show=groups").text
    assert 'value="Owen"' in page and "suggested from folder names" in page
    o0 = image(con, rel("Owen", "o0.jpg"))
    face, cid = con.execute("SELECT id, cluster_id FROM face WHERE image_id = ?", (o0,)).fetchone()
    other = con.execute("SELECT id FROM face WHERE cluster_id != ?", (cid,)).fetchone()[0]
    app_header = {"X-Requested-With": "lineagebox"}

    assert client.post(f"/api/clusters/{cid}/name?face={face}", json={"name": "Owen"}).status_code == 403
    # a page from before a regroup: the group number now belongs to other faces
    stale = client.post(f"/api/clusters/{cid}/name?face={other}", json={"name": "Owen"}, headers=app_header)
    assert stale.status_code == 409 and "Reload" in stale.json()["detail"]
    assert client.post(f"/api/clusters/{cid}/ignore?face={other}", headers=app_header).status_code == 409
    assert con.execute("SELECT count(*) FROM person").fetchone()[0] == 0

    # a stranger taken out of the group before naming it, even the first face, which the page sends to recognise
    # the group by
    name_url = html.unescape(re.search(rf'data-api="(/api/clusters/{cid}/name\?[^"]+)"', page).group(1))
    stranger = int(re.search(r"face=(\d+)", name_url).group(1))
    o4 = image(con, rel("Owen", "o4.jpg"))
    assert stranger == con.execute("SELECT id FROM face WHERE image_id = ?", (o4,)).fetchone()[0]
    assert f'data-api="/api/faces/{stranger}/reject"' in page
    assert client.post(f"/api/faces/{stranger}/reject", headers=app_header).status_code == 200
    r = client.post(name_url, json={"name": "Owen"}, headers=app_header)
    assert r.status_code == 200
    pid = r.json()["person_id"]
    assert con.execute("SELECT count(*) FROM face WHERE person_id = ?", (pid,)).fetchone()[0] == 4
    assert con.execute("SELECT assigned_by FROM face WHERE id = ?", (stranger,)).fetchone()[0] == "rejected"
    person = client.get(f"/person/{pid}").text  # its photos load by age group, all at once for a few
    groups = re.findall(r'data-later="(/person/\d+/life/[^"]+)"', person)
    assert sum(client.get(g).text.count('class="tile"') for g in groups) == 4

    # the name boxes suggest everyone, with what tells two people of the same name apart, relatives with no photos too
    assert "data-names" in client.get("/cleanup/faces?show=groups").text and "data-names" in person
    grandpa = people.person_id(con, "Owen Senior")
    events.save(con, None, tag="BIRT", person_id=grandpa, date_text="1931")
    events.save(con, None, tag="DEAT", person_id=grandpa, date_text="12 June 2001")
    events.add_name(con, grandpa, "Gus", "nickname")
    con.commit()
    choices = {p["name"]: p for p in client.get("/api/people/names").json()}
    assert choices["Owen"]["photos"] == 4 and choices["Owen"]["face"]
    assert choices["Owen Senior"] == {"id": grandpa, "name": "Owen Senior", "face": None, "photos": 0,
                                      "about": "1931–2001", "also": "Gus"}

    # a stranger named along with a group comes first among the faces named by hand
    wrong = add_face(con, image(con, rel("Lucas", "l5.jpg")), unit(RNG.standard_normal(DIM)))
    people.name_faces(con, [wrong], "Owen")
    assert people.least_typical_first(con, pid)[0]["id"] == wrong
    person = client.get(f"/person/{pid}").text
    assert f'href="/person/{pid}/faces"' in person and "Named by you" not in person  # checking faces is its own page
    faces = client.get(f"/person/{pid}/faces").text
    named = faces[faces.index("Named by you"):]
    assert re.findall(r'class="face" id="f(\d+)"', named)[0] == str(wrong)
    fid = con.execute("SELECT id FROM face LIMIT 1").fetchone()[0]
    crop = client.get(f"/face/{fid}")
    assert crop.status_code == 200 and crop.headers["content-type"] == "image/jpeg"
    photo = client.get(f"/image/{image(con, rel('Owen', 'o0.jpg'))}").text
    assert f'href="/person/{pid}">Owen</a>' in photo


def test_not_them_leaves_the_face_to_name(lib, cfg):
    """'not Owen' on a photo takes the wrong name off and keeps the face there to be named as whoever it is."""
    con = lib
    o0 = image(con, rel("Owen", "o0.jpg"))
    face = add_face(con, o0, like(A))
    owen = people.name_faces(con, [face], "Owen")
    stranger = add_face(con, o0, unit(RNG.standard_normal(DIM)), x=0.5)
    people.set_aside(con, [stranger])  # set aside before this change: shown again, to be named
    client = TestClient(webapp.create_app(cfg), client=LAN)
    app_header = {"X-Requested-With": "lineagebox"}
    page = client.get(f"/image/{o0}").text
    assert f'data-api="/api/faces/{face}/not"' in page and f'data-api="/api/faces/{stranger}/name"' in page
    assert client.post(f"/api/faces/{face}/not").status_code == 403
    assert client.post("/api/faces/99999/not", headers=app_header).status_code == 404
    assert client.post(f"/api/faces/{face}/not", headers=app_header).status_code == 200
    assert tuple(con.execute("SELECT person_id, assigned_by FROM face WHERE id = ?", (face,)).fetchone()) == (None, None)
    assert {r[0] for r in con.execute("SELECT person_id FROM face_not_person WHERE face_id = ?", (face,))} == {owen}
    assert f'data-api="/api/faces/{face}/name"' in client.get(f"/image/{o0}").text
    lucas = client.post(f"/api/faces/{face}/name", json={"name": "Lucas"}, headers=app_header).json()["person_id"]
    assert lucas != owen
    assert f'data-api="/api/faces/{face}/not"' in client.get(f"/person/{lucas}/faces").text  # × there does the same


def test_fix_names_a_face_as_someone_else(lib, cfg):
    """'Fix' on a named face opens a box for who it really is: the new name goes on, and the face is never suggested or
    recognised as the wrong person again. Fixed back, it is them after all."""
    con = lib
    o0 = image(con, rel("Owen", "o0.jpg"))
    face = add_face(con, o0, like(A))
    owen = people.name_faces(con, [face], "Owen")
    client = TestClient(webapp.create_app(cfg), client=LAN)
    app_header = {"X-Requested-With": "lineagebox"}
    page = client.get(f"/image/{o0}").text
    assert f'data-toggle="#fix{face}"' in page and ">Fix</button>" in page and "not Owen</button>" not in page
    fix = page[page.index(f'id="fix{face}"'):]
    fix = fix[:fix.index("</span>")]  # a box for who it is, and don't know who (take the name off)
    assert f'data-api="/api/faces/{face}/name"' in fix and f'data-api="/api/faces/{face}/not"' in fix
    lucas = client.post(f"/api/faces/{face}/name", json={"name": "Lucas"}, headers=app_header).json()["person_id"]
    named = con.execute("SELECT person_id, assigned_by FROM face WHERE id = ?", (face,)).fetchone()
    assert lucas != owen and tuple(named) == (lucas, "user")
    assert [r[0] for r in con.execute("SELECT person_id FROM face_not_person WHERE face_id = ?", (face,))] == [owen]
    client.post(f"/api/faces/{face}/name", json={"name": "Owen", "name_id": owen}, headers=app_header)
    assert [r[0] for r in con.execute("SELECT person_id FROM face_not_person WHERE face_id = ?", (face,))] == [lucas]


def test_looking_for_faces_again(lib, cfg, monkeypatch):
    """A photo looked at again more closely: faces missed the first time are added and recognised, one marked not a
    face comes back, and unnamed faces show who they most look like among those not in the photo yet."""
    from lineagebox import faces
    con = lib
    o = [image(con, rel("Owen", f"o{i}.jpg")) for i in range(6)]
    lu = [image(con, rel("Lucas", f"l{i}.jpg")) for i in range(3)]
    owen = people.name_faces(con, [add_face(con, i, like(A)) for i in o[2:5]], "Owen")
    lucas = people.name_faces(con, [add_face(con, i, like(B)) for i in lu], "Lucas")
    known = add_face(con, o[1], like(B))  # left half, found the first time
    hidden = add_face(con, o[1], unit(RNG.standard_normal(DIM)), x=0.5)  # right half
    people.set_aside(con, [hidden], "not_a_face")
    con.commit()
    size = Image.open(cfg.preview_path(con.execute("SELECT sha256 FROM image WHERE id = ?", (o[1],)).fetchone()[0])).width

    # the same two again, and a new one: Owen (boxes on a 48-pixel photo)
    seen = [([0, 0, 24, 48], like(B)), ([24, 0, 48, 48], unit(RNG.standard_normal(DIM))), ([6, 30, 30, 48], like(A))]

    class Model:
        class det_model:
            @staticmethod
            def detect(bgr, max_num, metric):
                return np.array([[*(np.array(box) * size / 48), 0.9] for box, _ in seen]), np.zeros((len(seen), 5, 2))

        class Reader:  # the recognition model: an embedding of norm 22
            @staticmethod
            def get(bgr, f):
                f.embedding = 22.0 * next(emb for box, emb in seen if np.allclose(np.array(box) * size / 48, f.bbox))

        models = {"detection": None, "recognition": Reader}

    monkeypatch.setattr(faces, "ensure_models", lambda con, models_dir: None)
    monkeypatch.setattr(faces, "load_model", lambda models_dir, **how: Model())
    client = TestClient(webapp.create_app(cfg), client=LAN)
    app_header = {"X-Requested-With": "lineagebox"}
    url = f"/api/images/{o[1]}/faces"
    page = client.get(f"/image/{o[1]}").text
    assert f'data-api="{url}"' in page and f'id="f{hidden}"' not in page
    assert client.post(url).status_code == 403
    assert client.post(url, headers=app_header).json() == {
        "message": "Found 1 more face; brought back 1; recognised 1.", "reload": True}
    new = con.execute("SELECT id, person_id, assigned_by FROM face WHERE image_id = ? ORDER BY id", (o[1],)).fetchall()
    assert [tuple(r)[1:] for r in new] == [(None, None), (None, None), (owen, "auto")]
    assert client.post(url, headers=app_header).json() == {"message": "No other faces found."}

    # the unnamed face that looks like Lucas, who isn't in this photo: one click names it
    page = client.get(f"/image/{o[1]}").text
    assert f'data-api="/api/people/{lucas}/faces" data-body=\'{{"yes": [{known}]}}\'' in page and ">Lucas?</button>" in page
    assert f'data-api="/api/faces/{hidden}/not-a-face"' in page
    assert client.post(f"/api/faces/{hidden}/not-a-face", headers=app_header).status_code == 200
    assert f'id="f{hidden}"' not in client.get(f"/image/{o[1]}").text


def test_people_page_shows_30_at_a_time(con, cfg):
    scan(con, cfg)
    ingest(con, cfg)
    image_id = con.execute("SELECT id FROM image WHERE error IS NULL ORDER BY id LIMIT 1").fetchone()[0]
    for i in range(35):
        con.execute("INSERT INTO image_person(image_id, person_id, added_at) VALUES (?, ?, '2026-11-02')",
                    (image_id, people.person_id(con, f"Person {i:02d}")))
    con.commit()
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get("/people").text
    assert page.count('class="person-card"') == 35 and page.count('" hidden data-text=') == 5
    assert 'data-more="#people-filter"' in page and "5 more" in page and 'data-limit="30"' in page
    photo = client.get(f"/image/{image_id}").text  # its details, files, albums and tools folded away
    assert photo.count('<details class="info-panel"') == 4 and "<h2>Tools</h2>" in photo


def test_faces_of_a_video_and_photos_to_finish(lib, cfg):
    con = lib
    C = unit(RNG.standard_normal(DIM))  # a third person
    video, video2, photo = (image(con, rel("Lucas", f"l{i}.jpg")) for i in (5, 4, 0))
    con.executemany("UPDATE image SET media = 'video' WHERE id = ?", [(video,), (video2,)])  # their faces get frames

    def face(image_id, emb, frame=None, x=0.0):
        return con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding, frame)
                              VALUES (?, ?, 0, ?, 1, 0.9, 22, ?, ?)""", (image_id, x, x + 0.5, emb.tobytes(),
                                                                          frame)).lastrowid

    owen_named, turned_away = face(video, like(A), 0), face(video, like(A), 1)  # the same boy, kept as two faces
    beside_himself = face(video, like(A), 2, x=0.5)  # on a frame where he is named already: someone else
    face_on_2, stranger = face(video, like(A), 2), face(video, unit(RNG.standard_normal(DIM)), 3)
    lucas_named = face(video, like(B), 4)
    con.commit()
    owen, lucas = people.name_faces(con, [owen_named, face_on_2], "Owen"), people.name_faces(con, [lucas_named], "Lucas")
    row = lambda f: tuple(con.execute("SELECT person_id, assigned_by FROM face WHERE id = ?", (f,)).fetchone())
    assert row(turned_away) == (owen, "auto")  # named with him, by naming him in the video
    assert row(beside_himself) == (None, None) and row(stranger) == (None, None)

    first, second = face(video2, like(C), 0), face(video2, like(C), 1)
    con.commit()
    cousin = people.name_faces(con, [first], "Cousin")
    assert row(second) == (cousin, "auto")
    people.not_them(con, [second])  # "not Cousin": taken off, and not named as her again
    assert people.name_in_videos(con) == 0 and row(second) == (None, None)

    # a photo with two people named and a third face: the likeliest person not in it is suggested
    face(photo, like(A)), face(photo, like(B), x=0.5)
    third = face(photo, like(C), x=0.25)
    con.commit()
    people.name_faces(con, [r[0] for r in con.execute("SELECT id FROM face WHERE image_id = ? AND id != ? ORDER BY id",
                                                      (photo, third))][:1], "Owen")
    people.name_faces(con, [r[0] for r in con.execute("SELECT id FROM face WHERE image_id = ? AND id != ? ORDER BY id",
                                                      (photo, third))][1:], "Lucas")
    found, total = people.to_finish(con)
    by_id = {p["id"]: p for p in found}
    assert set(by_id) == {photo, video} and total == 2
    assert [f["suggest"] for f in by_id[photo]["faces"]] == [(cousin, "Cousin")]
    assert by_id[photo]["named"] == [(lucas, "Lucas"), (owen, "Owen")]
    assert {f["id"] for f in by_id[video]["faces"]} == {beside_himself, stranger}

    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get("/cleanup/faces?show=finish").text
    assert f'id="ff{third}"' in page and ">Cousin?</button>" in page and "data-reload" in page  # a video reloads
    assert 'aria-current="page">Photos to finish' in page


def test_a_rename_or_merge_reindexes_photos_they_were_added_to(cfg, con, library):
    """Someone added to a photo by hand (no face found) is found by search under their new name after a rename, and
    under the kept name after a merge."""
    from lineagebox import scan as scan_mod, ingest as ingest_mod, search as search_mod
    scan_mod.scan(con, cfg)
    ingest_mod.ingest(con, cfg)
    image_id = con.execute("SELECT id FROM image WHERE error IS NULL ORDER BY id LIMIT 1").fetchone()[0]
    pid = people.person_id(con, "Wilbur Miller")
    con.execute("INSERT INTO image_person(image_id, person_id, added_at) VALUES (?, ?, '2026-11-07')", (image_id, pid))
    con.commit()
    search_mod.index_new(con)
    people.rename(con, pid, "Wilbert Miller", merge_same=False)
    assert image_id in [r["id"] for r in search_mod.query(con, "wilbert")]
    other = people.person_id(con, "Grandpa Will")
    people.merge(con, pid, other)
    assert image_id in [r["id"] for r in search_mod.query(con, "grandpa will")]


def test_a_face_crop_without_its_preview_is_not_found(cfg, con, library):
    from fastapi.testclient import TestClient
    from lineagebox import scan as scan_mod, ingest as ingest_mod
    from lineagebox.web import app as webapp
    from tests.conftest import LAN
    scan_mod.scan(con, cfg)
    ingest_mod.ingest(con, cfg)
    image = con.execute("SELECT id, sha256 FROM image WHERE error IS NULL ORDER BY id LIMIT 1").fetchone()
    face = con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding)
                          VALUES (?, 0.1, 0.1, 0.5, 0.5, 0.9, 25, zeroblob(2048))""", (image["id"],)).lastrowid
    con.commit()
    cfg.picture_path(image["sha256"], None).unlink()
    assert TestClient(webapp.create_app(cfg), client=LAN).get(f"/face/{face}").status_code == 404
