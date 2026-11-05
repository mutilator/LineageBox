"""Members' changes are kept and can be undone (Cleanup → Recent changes); a family change that widens someone's view is
made at once and marked; people added twice are found (Cleanup → Same person?)."""
import pytest
from fastapi.testclient import TestClient

from lineagebox import access, accounts, catalog, settings
from lineagebox.web import app as webapp
from tests import family_fixture as fx
from tests.conftest import LAN

APP = {"X-Requested-With": "lineagebox"}


@pytest.fixture
def site(con, cfg, library):
    fam = fx.build(con, cfg, library)
    p = fam["people"]
    fam["who"] = {"Sam": fx.account(con, p["Sam"], "sam@example.com", "admin"),
                  "Cora": fx.account(con, p["Cora"], "cora@example.com"),
                  "Tina": fx.account(con, p["Tina"], "tina@example.com")}
    fam["open_face"] = con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding)
                                      VALUES (?, 0.7, 0.1, 0.9, 0.5, 0.9, 25, ?)""",
                                   (fam["photos"]["cora_bart.jpg"], fx.unit(901).tobytes())).lastrowid
    con.commit()
    settings.save(con, cfg, {"accounts_enabled": True})
    return fam


def client(cfg, con, account):
    c = TestClient(webapp.create_app(cfg), client=LAN, follow_redirects=False)
    c.cookies.set(accounts.COOKIE, accounts.new_session(con, account["id"], 30))
    return c


def last_change(con):
    return con.execute("SELECT * FROM change_log ORDER BY id DESC LIMIT 1").fetchone()


def test_naming_a_face_and_undoing_it(site, cfg, con):
    cora, admin = client(cfg, con, site["who"]["Cora"]), client(cfg, con, site["who"]["Sam"])
    face = site["open_face"]
    new = cora.post(f"/api/faces/{face}/name", json={"name": "Cousin Vinny"}, headers=APP).json()["person_id"]
    change = last_change(con)
    assert change["account_id"] == site["who"]["Cora"]["id"] and "Cousin Vinny" in change["summary"]
    assert "Cousin Vinny" in admin.get("/cleanup/changes").text
    r = admin.post(f"/api/changes/{change['id']}/undo", headers=APP)
    assert r.status_code == 200
    assert con.execute("SELECT person_id, named_by FROM face WHERE id = ?", (face,)).fetchone()[:] == (None, None)
    assert catalog.person(con, new) is None  # the person she added by naming the face goes too
    assert admin.post(f"/api/changes/{change['id']}/undo", headers=APP).status_code == 409  # once


def test_undoing_a_name_given_in_a_video(site, cfg, con):
    """Naming one face of a video names that person's other faces in it too: undoing it takes back all of them."""
    cora, admin = client(cfg, con, site["who"]["Cora"]), client(cfg, con, site["who"]["Sam"])
    video, face = site["photos"]["cora_bart.jpg"], site["open_face"]
    con.execute("UPDATE image SET media = 'video' WHERE id = ?", (video,))
    con.execute("UPDATE face SET frame = 0 WHERE image_id = ?", (video,))
    other = con.execute("""INSERT INTO face(image_id, frame, x1, y1, x2, y2, det_score, quality, embedding)
                           VALUES (?, 1, 0.7, 0.1, 0.9, 0.5, 0.9, 25, ?)""", (video, fx.unit(901).tobytes())).lastrowid
    con.execute("""INSERT INTO face(image_id, frame, x1, y1, x2, y2, det_score, quality, embedding)
                   VALUES (?, 1, 0.1, 0.1, 0.3, 0.5, 0.9, 25, ?)""", (video, fx.unit(950).tobytes()))  # a stranger
    con.commit()
    new = cora.post(f"/api/faces/{face}/name", json={"name": "Newbie Q"}, headers=APP).json()["person_id"]
    assert con.execute("SELECT person_id FROM face WHERE id = ?", (other,)).fetchone()[0] == new  # named with it
    con.execute("UPDATE face SET cluster_id = 5 WHERE image_id = ? AND person_id IS NULL", (video,))  # regrouped
    con.commit()
    r = admin.post(f"/api/changes/{last_change(con)['id']}/undo", headers=APP)
    assert r.status_code == 200, r.text
    assert {con.execute("SELECT person_id FROM face WHERE id = ?", (f,)).fetchone()[0] for f in (face, other)} == {None}
    assert catalog.person(con, new) is None


def test_taking_off_a_citation_and_confirming_names_can_be_undone(site, cfg, con):
    cora, admin = client(cfg, con, site["who"]["Cora"]), client(cfg, con, site["who"]["Sam"])
    photo, bart = site["photos"]["cora_bart.jpg"], site["people"]["Bart"]
    admin.post(f"/api/images/{photo}/document", json={"kind": "Letter"}, headers=APP)
    admin.post(f"/api/images/{photo}/citations", json={"target": f"p:{bart}"}, headers=APP)
    cited = con.execute("SELECT id FROM citation WHERE image_id = ?", (photo,)).fetchone()[0]
    assert cora.post(f"/api/citations/{cited}/delete", headers=APP).status_code == 200
    assert "Took off what a document" in last_change(con)["summary"]
    assert admin.post(f"/api/changes/{last_change(con)['id']}/undo", headers=APP).status_code == 200
    assert con.execute("SELECT person_id FROM citation WHERE image_id = ?", (photo,)).fetchone()[0] == bart
    con.execute("UPDATE person SET names_checked = 0 WHERE id = ?", (bart,))
    con.commit()
    assert cora.post("/api/family/names/confirm", headers=APP).json()["confirmed"] >= 1
    assert admin.post(f"/api/changes/{last_change(con)['id']}/undo", headers=APP).status_code == 200
    assert not catalog.person(con, bart)["names_checked"]


def test_tags_dates_and_a_change_made_over(site, cfg, con):
    cora, admin = client(cfg, con, site["who"]["Cora"]), client(cfg, con, site["who"]["Sam"])
    photo = site["photos"]["cora_bart.jpg"]
    before = catalog.image(con, photo)["taken_at"]
    cora.post(f"/api/images/{photo}/date", json={"date": "summer 1987"}, headers=APP)
    dated = last_change(con)["id"]
    cora.post(f"/api/images/{photo}/tags", json={"tag": "picnic"}, headers=APP)
    tagged = last_change(con)["id"]
    admin.post(f"/api/images/{photo}/tags/remove", json={"tag": "picnic"}, headers=APP)  # changed again since
    r = admin.post(f"/api/changes/{tagged}/undo", headers=APP)
    assert r.status_code == 409 and "changed again since" in r.json()["detail"]
    assert admin.post(f"/api/changes/{dated}/undo", headers=APP).status_code == 200
    assert catalog.image(con, photo)["taken_at"] == before
    assert con.execute("SELECT count(*) FROM change_log WHERE account_id = ?",
                       (site["who"]["Sam"]["id"],)).fetchone()[0] == 0  # admins' changes are not kept


def test_a_link_that_widens_access_is_made_and_marked(site, cfg, con):
    cora, admin = client(cfg, con, site["who"]["Cora"]), client(cfg, con, site["who"]["Sam"])
    p = site["people"]
    # Cora says Tina's son Jake is her great-grandfather's father: Tina, her parents and her former husband would all
    # become Cora's ancestors
    r = cora.post("/api/family/link", json={"a": p["Old Rex"], "b": p["Jake"], "what": "father"}, headers=APP)
    assert r.status_code == 200  # made at once, as chosen
    change = last_change(con)
    assert change["widened"]
    seen = access.compute(con, site["who"]["Cora"]).people
    assert p["Hank"] in seen  # it did widen her view
    page = admin.get("/cleanup/changes").text
    assert "also sees" in page and "Hank" in page
    assert "let someone see more" in admin.get("/cleanup/counts/changes").text
    admin.post(f"/api/changes/{change['id']}/undo", headers=APP)
    assert not con.execute("SELECT 1 FROM parent WHERE child_id = ? AND parent_id = ?", (p["Old Rex"], p["Jake"])).fetchone()
    assert p["Hank"] not in access.compute(con, site["who"]["Cora"]).people


def test_undoing_a_link_to_someone_already_there_keeps_them(site, cfg, con):
    cora, admin = client(cfg, con, site["who"]["Cora"]), client(cfg, con, site["who"]["Sam"])
    p = site["people"]
    r = cora.post(f"/api/people/{p['Cora']}/link", json={"word": "friend", "name": "Mia", "name_id": p["Mia"]},
                  headers=APP)
    assert r.status_code == 200
    assert "Undone" in admin.post(f"/api/changes/{last_change(con)['id']}/undo", headers=APP).json()["message"]
    assert catalog.person(con, p["Mia"]) is not None  # only the link is undone, not the person
    assert con.execute("SELECT count(*) FROM parent WHERE child_id = ?", (p["Mia"],)).fetchone()[0] == 2
    assert not con.execute("SELECT 1 FROM relation WHERE ? IN (person_id, of_id) AND ? IN (person_id, of_id)",
                           (p["Mia"], p["Cora"])).fetchone()


def test_a_merge_keeps_what_refers_to_either_person(site, cfg, con):
    admin = client(cfg, con, site["who"]["Sam"])
    p, cora = site["people"], site["who"]["Cora"]
    twin = con.execute("INSERT INTO person(name, added_by) VALUES ('Cora Again', ?)", (cora["id"],)).lastrowid
    photo = site["photos"]["cora_bart.jpg"]
    con.execute("INSERT INTO document(image_id, kind, title, created_at) VALUES (?, 'Letter', 'x', '2020')", (photo,))
    con.execute("INSERT INTO citation(image_id, person_id) VALUES (?, ?)", (photo, twin))
    con.execute("INSERT INTO external_id(uid, person_id) VALUES ('I7', ?)", (twin,))
    con.execute("INSERT INTO access_rule(account_id, person_id, show, branch) VALUES (?, ?, 1, 0)",
                (site["who"]["Tina"]["id"], twin))
    con.execute("UPDATE account SET person_id = ? WHERE id = ?", (twin, cora["id"]))
    con.execute("INSERT INTO person_group(person_id, name, added_by) VALUES (?, 'Friends', ?)", (twin, cora["id"]))
    con.commit()
    r = admin.post("/api/same/merge", json={"added": twin, "other": p["Cora"], "keep_seeing": False}, headers=APP)
    assert r.status_code == 200 and catalog.person(con, twin) is None
    assert con.execute("SELECT count(*) FROM citation WHERE person_id = ?", (p["Cora"],)).fetchone()[0] == 1
    assert con.execute("SELECT person_id FROM external_id WHERE uid = 'I7'").fetchone()[0] == p["Cora"]
    assert con.execute("SELECT 1 FROM access_rule WHERE person_id = ?", (p["Cora"],)).fetchone()
    assert accounts.get(con, cora["id"])["person_id"] == p["Cora"]  # her account still knows who she is
    # the group she put the duplicate in is no longer a reason she sees anyone: keep_seeing alone decides that
    assert con.execute("SELECT added_by FROM person_group WHERE person_id = ?", (p["Cora"],)).fetchone()[0] is None


def test_undoing_everything_since(site, cfg, con):
    cora, admin = client(cfg, con, site["who"]["Cora"]), client(cfg, con, site["who"]["Sam"])
    photo = site["photos"]["cora_bart.jpg"]
    for tag in ("one", "two", "three"):
        cora.post(f"/api/images/{photo}/tags", json={"tag": tag}, headers=APP)
    first = con.execute("SELECT min(id) FROM change_log").fetchone()[0]
    r = admin.post("/api/changes/undo-since", json={"account_id": site["who"]["Cora"]["id"], "since": first},
                   headers=APP)
    assert "Undid 3 changes" in r.json()["message"]
    assert not {"one", "two", "three"} & {t["name"] for t in __import__("lineagebox").tagging.for_image(con, photo)}


def test_same_person(site, cfg, con):
    cora, admin = client(cfg, con, site["who"]["Cora"]), client(cfg, con, site["who"]["Sam"])
    p = site["people"]
    # Cora names a face Zed: she can't see the Zed already here, so he is someone new
    new = cora.post(f"/api/faces/{site['open_face']}/name", json={"name": "zed"}, headers=APP).json()["person_id"]
    assert new != p["Zed"]
    page = admin.get("/cleanup/same").text
    assert f"/person/{new}" in page and f"/person/{p['Zed']}" in page
    assert "Same person?" in admin.get("/cleanup").text
    # the same person, and she goes on seeing him (a friend from another family)
    r = admin.post("/api/same/merge", json={"added": new, "other": p["Zed"], "keep_seeing": True}, headers=APP)
    assert r.status_code == 200 and catalog.person(con, new) is None
    assert con.execute("SELECT person_id FROM face WHERE id = ?", (site["open_face"],)).fetchone()[0] == p["Zed"]
    assert p["Zed"] in access.compute(con, site["who"]["Cora"]).people
    assert cora.get(f"/person/{p['Zed']}").status_code == 200
    # different people are not listed again
    other = cora.post(f"/api/images/{site['photos']['cora_bart.jpg']}/people", json={"name": "Zoe"},
                      headers=APP).json()["person_id"]
    assert f"/person/{other}" in admin.get("/cleanup/same").text
    admin.post("/api/same/different", json={"added": other, "other": p["Zoe"]}, headers=APP)
    assert f"/person/{other}" not in admin.get("/cleanup/same").text
    assert cora.get("/cleanup/same").status_code == 404  # an admin's page
    assert cora.get("/cleanup/counts/same").status_code == 404  # nor its number on the Cleanup page
    assert "Loading" in cora.get("/cleanup").text and cora.get("/cleanup/counts/faces").status_code == 200
