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
                       (site["who"]["Sam"]["id"],)).fetchone()[0] == 1  # admins' changes are kept too


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


def test_an_admins_merge_is_kept_and_undone(site, cfg, con):
    """An admin's changes are kept too (Recent changes), and a merge, which deletes someone, can be put back whole."""
    admin = client(cfg, con, site["who"]["Sam"])
    p = site["people"]
    twin = con.execute("INSERT INTO person(name) VALUES ('Cora Again')").lastrowid
    face = site["open_face"]
    con.execute("UPDATE face SET person_id = ?, assigned_by = 'user' WHERE id = ?", (twin, face))
    con.execute("INSERT INTO parent(child_id, parent_id) VALUES (?, ?)", (twin, p["Zed"]))
    con.execute("INSERT INTO person_name(person_id, name, type) VALUES (?, 'Corrie', 'nickname')", (twin,))
    con.commit()
    had_zed = bool(con.execute("SELECT 1 FROM parent WHERE child_id = ? AND parent_id = ?",
                               (p["Cora"], p["Zed"])).fetchone())
    r = admin.post(f"/api/people/{twin}/merge", json={"into": p["Cora"]}, headers=APP)
    assert r.status_code == 200 and catalog.person(con, twin) is None
    change = last_change(con)
    assert change["account_id"] == site["who"]["Sam"]["id"] and "Merged Cora Again into Cora" in change["summary"]
    assert "merged Cora Again into Cora" in admin.get("/cleanup/changes").text  # "Sam (admin) merged…"
    assert "merged Cora Again" not in admin.get("/cleanup/changes?members=1").text  # family members' only
    con.execute("UPDATE face SET cluster_id = 3 WHERE person_id IS NULL")  # the worker regroups in the meantime
    con.commit()
    assert admin.post(f"/api/changes/{change['id']}/undo", headers=APP).status_code == 200
    assert catalog.person(con, twin)["name"] == "Cora Again"
    assert con.execute("SELECT person_id FROM face WHERE id = ?", (face,)).fetchone()[0] == twin
    assert con.execute("SELECT 1 FROM parent WHERE child_id = ? AND parent_id = ?", (twin, p["Zed"])).fetchone()
    assert bool(con.execute("SELECT 1 FROM parent WHERE child_id = ? AND parent_id = ?",
                            (p["Cora"], p["Zed"])).fetchone()) == had_zed  # the link the merge moved goes back
    assert con.execute("SELECT person_id FROM person_name WHERE name = 'Corrie'").fetchone()[0] == twin


def test_a_removal_is_undone_after_the_worker_regroups(cfg, con, library):
    """Without family accounts (everyone at home is an admin) a removal is kept too: undoing it brings the person
    back with their names on their faces, even after the worker has grouped and recognised those faces since."""
    fam = fx.build(con, cfg, library)
    p = fam["people"]
    named = con.execute("SELECT id FROM face WHERE person_id = ?", (p["Bart"],)).fetchall()
    assert named
    home = TestClient(webapp.create_app(cfg), client=LAN)
    r = home.post(f"/api/people/{p['Bart']}/remove", headers=APP)
    assert r.status_code == 200, r.text
    undo = r.json()["undo"]
    assert catalog.person(con, p["Bart"]) is None
    con.execute("UPDATE face SET cluster_id = 9, assigned_by = 'auto', person_id = ? WHERE id = ?",
                (p["Zed"], named[0][0]))  # recognised as someone else by the worker
    con.commit()
    assert home.post(f"/api/changes/{undo}/undo-own", headers=APP).status_code == 200
    assert catalog.person(con, p["Bart"])["name"] == "Bart"
    assert {r[0] for r in con.execute("SELECT person_id FROM face WHERE id IN (SELECT value FROM json_each(?))",
                                      (str([f[0] for f in named]),))} == {p["Bart"]}


def test_a_rename_that_would_merge_asks_first(site, cfg, con):
    admin = client(cfg, con, site["who"]["Sam"])
    p = site["people"]
    twin = con.execute("INSERT INTO person(name) VALUES ('Zedd')").lastrowid
    con.commit()
    r = admin.post(f"/api/people/{twin}/rename", json={"name": "Zed"}, headers=APP).json()
    assert "confirm" in r and catalog.person(con, twin) is not None  # nothing done yet
    r = admin.post(f"/api/people/{twin}/rename", json={"name": "Zed", "merge": True}, headers=APP).json()
    assert r["person_id"] == p["Zed"] and catalog.person(con, twin) is None
    assert "Merged Zedd into Zed" in last_change(con)["summary"]


def test_a_member_undoes_only_their_own_change_and_soon(site, cfg, con):
    cora, tina = client(cfg, con, site["who"]["Cora"]), client(cfg, con, site["who"]["Tina"])
    face = site["open_face"]
    cora.post(f"/api/faces/{face}/name", json={"name": "Cousin Vinny"}, headers=APP)
    undo = cora.post(f"/api/faces/{face}/not-a-face", headers=APP).json()["undo"]
    assert tina.post(f"/api/changes/{undo}/undo-own", headers=APP).status_code in (404, 409)  # not hers
    con.execute("UPDATE change_log SET at = '2020-01-01T00:00:00' WHERE id = ?", (undo,))
    con.commit()
    assert cora.post(f"/api/changes/{undo}/undo-own", headers=APP).status_code == 409  # too late: an admin's now
    con.execute("UPDATE change_log SET at = ? WHERE id = ?", (__import__("datetime").datetime.now().isoformat(), undo))
    con.commit()
    assert cora.post(f"/api/changes/{undo}/undo-own", headers=APP).status_code == 200
    assert con.execute("SELECT assigned_by FROM face WHERE id = ?", (face,)).fetchone()[0] == "user"


def _as_kept_before_upgrade(con, change_id, table, column):
    """The change as an older LineageBox kept it: its rows of `table` without `column`, which a later migration added
    (image.camera_taken_at in 026, person.biography in 025, share.album_id in 028)."""
    import json
    row = con.execute("SELECT undo FROM change_log WHERE id = ?", (change_id,)).fetchone()
    saved = json.loads(row[0])
    for side in ("before", "after"):
        for values in saved[side].get(table, {}).values():
            assert column in values
            del values[column]
    con.execute("UPDATE change_log SET undo = ? WHERE id = ?", (json.dumps(saved), change_id))
    con.commit()


def test_changes_kept_before_an_upgrade_can_still_be_undone(site, cfg, con):
    """An upgrade that adds a column to photos, people or shares doesn't make the changes kept before it look
    "changed again since": they are compared, and put back, in the columns they kept."""
    cora, admin = client(cfg, con, site["who"]["Cora"]), client(cfg, con, site["who"]["Sam"])
    photo, bart = site["photos"]["cora_bart.jpg"], site["people"]["Bart"]
    before = catalog.image(con, photo)["taken_at"]
    cora.post(f"/api/images/{photo}/date", json={"date": "summer 1987"}, headers=APP)
    dated = last_change(con)["id"]
    _as_kept_before_upgrade(con, dated, "image", "camera_taken_at")
    assert admin.post(f"/api/people/{bart}/rename", json={"name": "Bartholomew"}, headers=APP).status_code == 200
    renamed = last_change(con)["id"]
    _as_kept_before_upgrade(con, renamed, "person", "biography")
    assert admin.post("/api/shares", json={"image_id": photo, "to": "everyone"}, headers=APP).status_code == 200
    shared = last_change(con)["id"]
    _as_kept_before_upgrade(con, shared, "share", "album_id")
    for change in (dated, renamed, shared):
        r = admin.post(f"/api/changes/{change}/undo", headers=APP)
        assert r.status_code == 200, r.text
    assert catalog.image(con, photo)["taken_at"] == before
    assert catalog.person(con, bart)["name"] == "Bart"
    assert not con.execute("SELECT 1 FROM share WHERE image_id = ?", (photo,)).fetchone()


def test_undoing_words_on_a_copy_finds_the_photo_search_shows_by_them_no_more(site, cfg, con):
    """Search shows one photo of each group of near-duplicates, found by the words written on any copy: undoing a
    caption written on a copy takes it out of the search too, not only out of the copy's own index row."""
    from lineagebox import search
    admin = client(cfg, con, site["who"]["Sam"])
    shown, copy = site["photos"]["cora_bart.jpg"], site["photos"]["rex.jpg"]
    con.execute("UPDATE image SET dup_group_id = ? WHERE id IN (?, ?)", (shown, shown, copy))
    con.commit()
    r = admin.post(f"/api/images/{copy}/caption", json={"text": "Grandpa's zanzibar trip"}, headers=APP)
    assert r.status_code == 200
    assert [x["id"] for x in search.query(con, "zanzibar")] == [shown]
    assert admin.post(f"/api/changes/{r.json()['undo']}/undo", headers=APP).status_code == 200
    assert not con.execute("SELECT 1 FROM comment WHERE image_id = ?", (copy,)).fetchone()
    assert search.query(con, "zanzibar") == []


def test_a_source_others_cite_too_comes_back_with_an_undo(site, cfg, con):
    """Taking someone's citation off a source others cite too, or removing them, leaves the source where it is: the
    undo puts their citation back, rather than finding the source "changed again since"."""
    admin = client(cfg, con, site["who"]["Sam"])
    p = site["people"]
    r = admin.post(f"/api/people/{p['Bart']}/sources", json={"title": "Parish register", "target": f"p:{p['Bart']}",
                                                             "page": "12"}, headers=APP)
    assert r.status_code == 200, r.text
    source = con.execute("SELECT id FROM source WHERE title = 'Parish register'").fetchone()[0]
    assert admin.post(f"/api/people/{p['Cora']}/sources", json={"source_id": source, "target": f"p:{p['Cora']}"},
                      headers=APP).status_code == 200
    cited = con.execute("SELECT id FROM source_citation WHERE person_id = ?", (p["Bart"],)).fetchone()[0]
    r = admin.post(f"/api/source-citations/{cited}/delete", headers=APP)
    assert r.status_code == 200
    r = admin.post(f"/api/changes/{r.json()['undo']}/undo", headers=APP)
    assert r.status_code == 200, r.text
    assert con.execute("SELECT source_id, page FROM source_citation WHERE person_id = ?",
                       (p["Bart"],)).fetchone()[:] == (source, "12")
    r = admin.post(f"/api/people/{p['Bart']}/remove", headers=APP)
    assert r.status_code == 200, r.text
    assert admin.post(f"/api/changes/{r.json()['undo']}/undo", headers=APP).status_code == 200
    assert catalog.person(con, p["Bart"])["name"] == "Bart"
    cited = con.execute("SELECT source_id FROM source_citation WHERE person_id = ?", (p["Bart"],)).fetchone()
    assert cited[0] == source


def test_undoing_a_rename_finds_the_photos_they_were_added_to_by_the_old_name(site, cfg, con):
    """Someone added to a photo by hand, with no face to name, is found there by their name: after a rename is
    undone, by the name they had again."""
    from lineagebox import search
    admin = client(cfg, con, site["who"]["Sam"])
    scenery, bart = site["photos"]["scenery.jpg"], site["people"]["Bart"]
    r = admin.post(f"/api/images/{scenery}/people", json={"name": "Bart", "name_id": bart}, headers=APP)
    assert r.status_code == 200, r.text
    assert scenery in {x["id"] for x in search.query(con, "Bart")}
    assert admin.post(f"/api/people/{bart}/rename", json={"name": "Bartholomew"}, headers=APP).status_code == 200
    search.index_image(con, scenery, search.FolderIndex(con))  # (people.rename re-indexes only their faces' photos)
    con.commit()
    assert scenery in {x["id"] for x in search.query(con, "Bartholomew")}
    assert admin.post(f"/api/changes/{last_change(con)['id']}/undo", headers=APP).status_code == 200
    assert scenery in {x["id"] for x in search.query(con, "Bart")}
    assert search.query(con, "Bartholomew") == []


def test_someone_a_change_added_and_named_since_stays(site, cfg, con):
    """Undoing the naming of a face takes away the person it added, unless someone named another face as them since:
    the undo then leaves everything as it is, and says so (Undo everything since, too)."""
    cora, admin = client(cfg, con, site["who"]["Cora"]), client(cfg, con, site["who"]["Sam"])
    face = site["open_face"]
    other = con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding)
                           VALUES (?, 0.1, 0.1, 0.3, 0.5, 0.9, 25, ?)""",
                        (site["photos"]["cora_bart.jpg"], fx.unit(950).tobytes())).lastrowid
    con.commit()
    new = cora.post(f"/api/faces/{face}/name", json={"name": "Cousin Vinny"}, headers=APP).json()["person_id"]
    named = last_change(con)["id"]
    r = admin.post(f"/api/faces/{other}/name", json={"name": "Cousin Vinny", "name_id": new}, headers=APP)
    assert r.json()["person_id"] == new
    r = admin.post(f"/api/changes/{named}/undo", headers=APP)
    assert r.status_code == 409 and "used elsewhere" in r.json()["detail"]
    assert con.execute("SELECT person_id FROM face WHERE id = ?", (face,)).fetchone()[0] == new  # left as it was
    assert catalog.person(con, new) is not None and last_change(con)["undone_at"] is None
    r = admin.post("/api/changes/undo-since", json={"account_id": site["who"]["Cora"]["id"], "since": named},
                   headers=APP)
    assert "Undid 0 changes" in r.json()["message"] and "left as they are" in r.json()["message"]


def test_a_merge_of_someone_with_a_life_story_sources_and_stories_is_undone_whole(site, cfg, con):
    """Merging someone moves their life story (where the other has none), their written sources, files another tree
    linked and their recorded stories; undoing it gives each back to whom it was, and the other's own back as it was."""
    admin = client(cfg, con, site["who"]["Sam"])
    p = site["people"]
    twin = con.execute("INSERT INTO person(name, biography) VALUES ('Cora Again', 'Grew up by the sea.')").lastrowid
    source = con.execute("INSERT INTO source(title, created_at) VALUES ('Parish register', 'x')").lastrowid
    con.execute("INSERT INTO source_citation(source_id, person_id, page) VALUES (?, ?, '7')", (source, twin))
    con.execute("INSERT INTO tree_media(person_id, file, title) VALUES (?, 'scans/cora.jpg', 'Cora at ten')", (twin,))
    told = con.execute("""INSERT INTO story(person_id, file, mime, bytes, at)
                          VALUES (?, 'ab/ab12.webm', 'audio/webm', 10, '2026-11-01')""", (twin,)).lastrowid
    con.commit()
    assert catalog.person(con, p["Cora"])["biography"] is None
    r = admin.post(f"/api/people/{twin}/merge", json={"into": p["Cora"]}, headers=APP)
    assert r.status_code == 200 and catalog.person(con, twin) is None
    assert catalog.person(con, p["Cora"])["biography"] == "Grew up by the sea."
    assert con.execute("SELECT person_id FROM story WHERE id = ?", (told,)).fetchone()[0] == p["Cora"]
    assert admin.post(f"/api/changes/{last_change(con)['id']}/undo", headers=APP).status_code == 200
    assert catalog.person(con, twin)["biography"] == "Grew up by the sea."
    assert catalog.person(con, p["Cora"])["biography"] is None
    assert con.execute("SELECT person_id FROM story WHERE id = ?", (told,)).fetchone()[0] == twin
    assert con.execute("SELECT person_id, page FROM source_citation WHERE source_id = ?",
                       (source,)).fetchone()[:] == (twin, "7")
    assert con.execute("SELECT person_id FROM tree_media WHERE file = 'scans/cora.jpg'").fetchone()[0] == twin
