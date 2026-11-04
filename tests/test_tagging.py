"""Tags and people added or taken off by hand, on a photo's page and for ticked photos; the Status page's lists of
photos; and what LineageBox made from a photo deleted on the share."""
import pytest
from fastapi.testclient import TestClient

from lineagebox import catalog, cleanup, events, search, tagging
from lineagebox.describe import _record
from lineagebox.folders import FolderIndex
from lineagebox.ingest import STAGE_INGEST, ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests.conftest import LAN, rel

APP = {"X-Requested-With": "lineagebox"}
BLUE = rel("2016", "IMG_20160811_182239.jpg")
ZOO = rel("2012", "Zoo", "DSC_0001.jpg")
ANSWER = {"caption": "A tall animal behind a fence.", "tags": ["Giraffe", "fence", "close-up"], "animals": []}


@pytest.fixture
def lib(con, cfg, library):
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    search.rebuild(con)
    return con


def image_of(con, path):
    return con.execute("SELECT image_id FROM file WHERE path = ?", (path,)).fetchone()[0]


def found(con, words):
    return [r["id"] for r in search.query(con, words)]


def test_tags_given_and_taken_off_by_hand_last(lib):
    con = lib
    blue = image_of(con, BLUE)
    _record(con, blue, "fake", ANSWER, FolderIndex(con))
    assert [t["name"] for t in tagging.for_image(con, blue)] == ["giraffe", "fence", "close up"]

    tagging.add(con, [blue], "Owen's  Birthday")
    tagging.remove(con, [blue], "Giraffe")
    assert tagging.for_image(con, blue) == [{"name": "fence", "mine": False}, {"name": "close up", "mine": False},
                                            {"name": "owen's birthday", "mine": True}]
    assert [r["id"] for r in catalog.tag_images(con, "giraffe")] == []
    assert [r["id"] for r in catalog.tag_images(con, "owen's birthday")] == [blue]
    assert "giraffe" not in [t["name"] for t in catalog.tags(con)[0]]
    assert found(con, "giraffe") == [] and found(con, "birthday") == [blue]

    _record(con, blue, "fake", ANSWER, FolderIndex(con))  # described again, as after a new prompt
    assert [t["name"] for t in tagging.for_image(con, blue)] == ["fence", "close up", "owen's birthday"]
    assert found(con, "giraffe") == [] and found(con, "birthday") == [blue]

    tagging.add(con, [blue], "giraffe")  # given back by hand
    assert {"name": "giraffe", "mine": True} in tagging.for_image(con, blue) and found(con, "giraffe") == [blue]
    with pytest.raises(tagging.Problem):
        tagging.add(con, [blue], "  ")


def test_tags_and_people_on_a_photo_page(lib, cfg):
    con = lib
    blue = image_of(con, BLUE)
    _record(con, blue, "fake", ANSWER, FolderIndex(con))
    con.commit()
    client = TestClient(webapp.create_app(cfg), client=LAN)
    html = client.get(f"/image/{blue}").text
    assert f'data-api="/api/images/{blue}/tags/remove"' in html and 'placeholder="+ tag"' in html
    assert client.post(f"/api/images/{blue}/tags", json={"tag": "Zoo trip"}, headers=APP).status_code == 200
    assert client.post(f"/api/images/{blue}/tags/remove", json={"tag": "fence"}, headers=APP).status_code == 200
    assert client.post(f"/api/images/{blue}/tags", json={"tag": " "}, headers=APP).status_code == 400
    assert [t["name"] for t in tagging.for_image(con, blue)] == ["giraffe", "close up", "zoo trip"]
    assert "zoo trip" in client.get("/api/tags/names").json()

    r = client.post(f"/api/images/{blue}/people", json={"name": "Lucas Miller"}, headers=APP)
    pid = r.json()["person_id"]
    html = client.get(f"/image/{blue}").text
    assert f'href="/person/{pid}">Lucas Miller</a>' in html and "not here" in html
    assert [r["id"] for r in catalog.person_images(con, pid)] == [blue] and found(con, "lucas") == [blue]
    assert [(p["name"], p["photos"]) for p in catalog.people(con)] == [("Lucas Miller", 1)]
    client.post(f"/api/images/{blue}/people/{pid}/remove", headers=APP)
    assert catalog.person_images(con, pid) == [] and found(con, "lucas") == []


def test_ticked_photos_tagged_untagged_and_given_someone(lib, cfg):
    con = lib
    blue, zoo = image_of(con, BLUE), image_of(con, rel("2012", "Zoo", "DSC_0001.jpg"))
    for i in (blue, zoo):
        _record(con, i, "fake", ANSWER, FolderIndex(con))
    con.commit()
    client = TestClient(webapp.create_app(cfg), client=LAN)
    html = client.get("/tag/giraffe?select=1").text
    assert 'data-api="/api/selection/untag?tag=giraffe"' in html and 'data-api="/api/selection/person"' in html
    assert "Select photos" in client.get("/tag/giraffe").text

    r = client.post("/api/selection/tag", json={"image_ids": [blue, zoo], "add_tag": "Safari"}, headers=APP)
    assert r.json()["message"] == "Tagged 2 photos “safari”."
    assert {r["id"] for r in catalog.tag_images(con, "safari")} == {blue, zoo}
    assert client.post("/api/selection/tag", json={"image_ids": [blue], "add_tag": ""}, headers=APP).status_code == 400

    client.post("/api/selection/untag?tag=giraffe", json={"image_ids": [zoo]}, headers=APP)
    assert [r["id"] for r in catalog.tag_images(con, "giraffe")] == [blue]

    r = client.post("/api/selection/person", json={"image_ids": [blue, zoo], "person": "Owen"}, headers=APP)
    assert r.json()["message"] == "Added Owen to 2 photos."
    r = client.post("/api/selection/person", json={"image_ids": [blue], "person": "owen"}, headers=APP)
    assert r.json()["message"] == "Added Owen to 0 photos (1 photo already had them)."
    pid = con.execute("SELECT id FROM person WHERE name = 'Owen'").fetchone()[0]
    assert {r["id"] for r in catalog.person_images(con, pid)} == {blue, zoo}

    # merged into the right spelling, the photos go along
    from lineagebox import people
    other = people.person_id(con, "Owen Miller")
    people.merge(con, pid, other)
    assert {r["id"] for r in catalog.person_images(con, other)} == {blue, zoo}


def test_status_lines_link_to_their_photos(lib, cfg, library):
    con = lib
    client = TestClient(webapp.create_app(cfg), client=LAN)
    s = catalog.stats(con, cfg.folders)
    html = client.get("/status").text
    assert 'href="/photos/format/jpeg"' in html and 'href="/photos/undecodable"' in html
    assert 'title="Files no longer where the scan last found them.' in html
    jpegs = client.get("/photos/format/jpeg").text
    assert "JPEG files" in jpegs and jpegs.count('class="tile"') == s["formats"]["jpeg"]
    assert client.get("/photos/dates/exif").text.count('class="tile"') == s["date_sources"]["exif"] == 1
    assert "broken.jpg" in client.get("/photos/undecodable").text
    assert client.get("/photos/nonsense").status_code == 404 and client.get("/photos/format").status_code == 404
    assert 'data-api="/api/selection/tag"' in client.get("/photos/format/jpeg?select=1").text
    assert s["images"] == sum(s["formats"].values()) == con.execute(
        f"SELECT count(*) FROM image i WHERE {catalog.VISIBLE}").fetchone()[0]

    # missing files, by why
    cleanup.remove_images(con, cfg, [image_of(con, BLUE)], "test")
    (library / "transparent.png").unlink()
    scan(con, cfg)
    s = catalog.stats(con, cfg.folders)
    assert (s["files_missing"], s["files_removed"], s["files_gone"], s["files_unwatched"]) == (2, 1, 1, 0)


def test_what_was_made_from_a_deleted_photo_goes_and_comes_back(lib, cfg, library):
    con = lib
    blue = image_of(con, BLUE)
    sha = catalog.image(con, blue)["sha256"]
    pid = con.execute("INSERT INTO person(name) VALUES ('Owen')").lastrowid
    face = con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, embedding, person_id, assigned_by, quality)
                          VALUES (?, 0.1, 0.1, 0.5, 0.5, x'00', ?, 'auto', 20)""", (blue, pid)).lastrowid
    crop = cfg.face_crop_path(face, 0.1, 0.1)  # where /face/{id} makes it
    crop.parent.mkdir(parents=True)
    crop.write_bytes(b"crop")
    con.commit()
    assert [p["photos"] for p in catalog.people(con)] == [1]

    # marked missing while the file is still there (a folder no longer watched): nothing is deleted
    con.execute("UPDATE file SET missing = 1 WHERE path = ?", (BLUE,))
    assert cleanup.drop_derivatives(con, cfg) == 0 and cfg.preview_path(sha).is_file()
    assert catalog.people(con) == [] and catalog.person_faces(con, pid, "auto") == []  # but its faces don't show

    (library / "2016" / "IMG_20160811_182239.jpg").rename(library / "kept.jpg")
    assert cleanup.drop_derivatives(con, cfg) == 1
    assert not cfg.preview_path(sha).exists() and not cfg.thumb_path(sha).exists() and not crop.exists()
    assert not catalog.image(con, blue)["stage"] & STAGE_INGEST
    assert cleanup.drop_derivatives(con, cfg) == 0  # once

    (library / "kept.jpg").rename(library / "2016" / "IMG_20160811_182239.jpg")
    scan(con, cfg)
    assert ingest(con, cfg, show_progress=False).rebuilt == 1
    assert cfg.preview_path(sha).is_file() and cfg.thumb_path(sha).is_file()
    assert catalog.image(con, blue)["stage"] & STAGE_INGEST


def test_a_removed_photo_keeps_its_preview_until_emptied(lib, cfg):
    con = lib
    blue = image_of(con, BLUE)
    sha = catalog.image(con, blue)["sha256"]
    cleanup.remove_images(con, cfg, [blue], "test")
    assert cleanup.drop_derivatives(con, cfg) == 0 and cfg.preview_path(sha).is_file()  # it can be put back
    cleanup.empty_removed(con, cfg)
    assert not cfg.preview_path(sha).exists()


def test_nothing_is_deleted_while_a_watched_folder_is_offline(lib, cfg, library):
    con = lib
    blue = image_of(con, BLUE)
    sha = catalog.image(con, blue)["sha256"]
    con.execute("UPDATE file SET missing = 1")
    library.rename(library.parent / "elsewhere")
    assert cleanup.drop_derivatives(con, cfg) == 0 and cfg.preview_path(sha).is_file()


def test_dates_set_by_hand(lib, cfg):
    """A photo's wrong date put right, as exactly as it is known; the automatic one stays, to go back to."""
    con = lib
    blue, zoo = image_of(con, BLUE), image_of(con, ZOO)
    when = lambda i: tuple(con.execute("""SELECT taken_at, taken_at_source, date_text, date_from, date_to, auto_taken_at,
                                                 auto_taken_at_source FROM image WHERE id = ?""", (i,)).fetchone())
    client = TestClient(webapp.create_app(cfg), client=LAN)
    url = f"/api/images/{blue}/date"
    assert client.post(url, json={"date": "summer 1987"}).status_code == 403
    bad = client.post(url, json={"date": "summery 1987"}, headers=APP)
    assert bad.status_code == 400 and "Can't read the date" in bad.json()["detail"]
    assert client.post("/api/images/9999/date", json={"date": "1987"}, headers=APP).status_code == 404

    assert client.post(url, json={"date": "summer of 1987"}, headers=APP).status_code == 200
    assert when(blue) == ("1987-07-16T00:00:00", "user", "summer 1987", "1987-06-01", "1987-08-31",
                          "2016-08-11T18:22:39", "filename")
    page = client.get(f"/image/{blue}").text
    assert "<title>summer 1987" in page and "set by you · automatic date Aug 11, 2016, date from file name" in page
    assert 'data-key="w"' in page and "use the automatic date" in page
    assert found(con, "1987 summer") == [blue] and found(con, "1988") == []
    # changed again: the automatic date is still the file name's; ages span what the date allows
    tagging.add_person(con, [blue], "Katherine")
    katherine = con.execute("SELECT id FROM person WHERE name = 'Katherine'").fetchone()[0]
    events.save(con, None, tag="BIRT", person_id=katherine, date_text="March 1987")
    con.commit()
    assert client.post(url, json={"date": "summer 1987 or summer 1988"}, headers=APP).status_code == 200
    assert when(blue)[2:] == ("between summer 1987 and summer 1988", "1987-06-01", "1988-08-31",
                              "2016-08-11T18:22:39", "filename")
    assert found(con, "1988") == [blue] and '<span class="age">0–1</span>' in client.get(f"/image/{blue}").text
    from lineagebox.people import _before_birth  # recognition: someone born during it can be in it
    face = {"taken_at": when(blue)[0], "taken_at_source": "user", "date_to": "1988-08-31"}
    assert not _before_birth(face, "1988-03") and _before_birth(face, "1989")

    # back to the automatic date
    assert client.post(url, json={"date": ""}, headers=APP).status_code == 200
    assert when(blue) == ("2016-08-11T18:22:39", "filename", None, None, None, None, None)
    # a day for a photo with a camera time keeps the time: its clock had the wrong date
    tagging.set_date(con, [zoo], "June 5, 2012")
    con.commit()
    assert when(zoo)[:3] == ("2012-06-05T10:00:00", "user", "Jun 5, 2012")

    # many at once, from a folder's photos
    folder = con.execute("SELECT folder_id FROM file WHERE path = ?", (BLUE,)).fetchone()[0]
    picking = client.get(f"/folder/{folder}?select=1").text
    assert 'class="select-form"' in picking and 'data-api="/api/selection/date"' in picking
    assert 'href="/folder/{}?select=1"'.format(folder) in client.get(f"/folder/{folder}").text
    r = client.post("/api/selection/date", json={"image_ids": [blue, zoo], "date": "the 1980s"}, headers=APP)
    assert r.json() == {"message": "Dated 2 photos 1980s."} and when(blue)[2] == when(zoo)[2] == "1980s"
    assert when(zoo)[5:] == ("2012-06-03T10:00:00", "exif")
    assert client.post("/api/selection/date", json={"image_ids": [blue], "date": " "}, headers=APP).status_code == 400
    assert "Set by you" in client.get("/status").text and client.get("/photos/dates/user").status_code == 200
