"""Places without going online (D8): GPS photos named after the nearest town from the list that comes with LineageBox,
photos and folders put on the map by hand (tracked, undoable, members for the photos they see), and which place wins."""
import pytest
from fastapi.testclient import TestClient

from lineagebox import accounts, catalog, places, settings, sidecars, towns
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests import family_fixture as fx
from tests.conftest import LAN, rel, write_jpeg

APP = {"X-Requested-With": "lineagebox"}
SPRINGFIELD = {1: "N", 2: (39.0, 48.0, 7.0), 3: "W", 4: (89.0, 38.0, 38.0)}  # Springfield, Illinois


def test_town_names_without_going_online():
    assert towns.label(39.80, -89.64) == "Springfield, Illinois, United States"
    assert towns.label(39.80, -89.40).startswith("near Springfield, Illinois")  # about 20 km away
    assert towns.label(0, 0) is None and towns.label(None, 5) is None  # no fix, or no position
    assert towns.label(-33.87, 151.21).endswith("New South Wales, Australia")  # across the date line's side too
    found = towns.search("springfield")
    assert found and all(t["name"].startswith("Springfield") for t in found)
    assert towns.search("") == []


@pytest.fixture
def placed(con, cfg, library):
    write_jpeg(library / "Trip" / "gps.jpg", gps=SPRINGFIELD, taken="2015:06:01 10:00:00")
    write_jpeg(library / "Trip" / "Day 2" / "none.jpg", color="blue", taken="2015:06:02 10:00:00")
    write_jpeg(library / "Trip" / "own.jpg", color="green", taken="2015:06:03 10:00:00")
    scan(con, cfg)
    ingest(con, cfg)
    ids = {n: con.execute("SELECT image_id FROM file WHERE path LIKE ?", (f"%/{n}",)).fetchone()[0]
           for n in ("gps.jpg", "none.jpg", "own.jpg")}
    ids["trip"] = con.execute("SELECT id FROM folder WHERE path = ?", (rel("Trip"),)).fetchone()[0]
    return ids


def test_a_photo_put_on_the_map_by_hand_and_undone(con, cfg, placed):
    home = TestClient(webapp.create_app(cfg), client=LAN)
    photo = placed["own.jpg"]
    assert "Put it on the map" in home.get(f"/image/{photo}").text
    r = home.post(f"/api/images/{photo}/place", json={"lat": 48.8566, "lon": 2.3522}, headers=APP)
    assert r.status_code == 200, r.text
    assert r.json()["label"].startswith("Paris") and r.json()["undo"]
    assert tuple(con.execute("SELECT lat, lon FROM image_place WHERE image_id = ?", (photo,)).fetchone()) == (
        48.8566, 2.3522)
    page = home.get(f"/image/{photo}").text
    assert "placed by hand" in page and "Move it on the map" in page
    shown = {p["kind"]: p for p in home.get("/api/map").json()["places"]}
    assert shown["placed"]["photos"][0]["id"] == photo and shown["placed"]["name"].startswith("Paris")
    assert home.post(f"/api/changes/{r.json()['undo']}/undo-own", headers=APP).status_code == 200
    assert con.execute("SELECT 1 FROM image_place WHERE image_id = ?", (photo,)).fetchone() is None
    assert home.post(f"/api/images/{photo}/place", json={"lat": 91, "lon": 0}, headers=APP).status_code == 400


def test_which_place_wins(con, cfg, placed):
    gps, none, own = (placed[n] for n in ("gps.jpg", "none.jpg", "own.jpg"))
    where = lambda i: places.photo_place(con, i, catalog.image(con, i))  # noqa: E731
    assert where(gps)["how"] == "gps" and where(gps)["label"] == "Springfield, Illinois, United States"
    assert where(none) is None
    places.place_folder(con, placed["trip"], 41.88, -87.63)  # Chicago: for its photos and its folders' photos
    places.place_photo(con, own, 40.71, -74.0)
    places.place_photo(con, gps, 34.05, -118.24)  # a phone's GPS can be wrong: a person's word wins
    con.commit()
    assert where(none)["how"] == "folder" and where(none)["label"].startswith("Chicago")
    assert where(own)["how"] == "photo" and where(gps)["how"] == "photo"
    assert sidecars.metadata(con, none).gps == (41.88, -87.63)  # confirmed GPS goes into the sidecar
    data = places.map_data(con)
    folder = next(p for p in data["places"] if p["kind"] == "placed" and p["folder"] == placed["trip"])
    assert [ph["id"] for ph in folder["photos"]] == [none]  # the others have places of their own


def test_an_admin_places_a_folder_from_the_map(con, cfg, placed):
    home = TestClient(webapp.create_app(cfg), client=LAN)
    assert "/map?place=folder:" in home.get(f"/folder/{placed['trip']}").text
    target = home.get("/api/places/target", params={"kind": "folder", "id": placed["trip"]}).json()
    assert target["by_hand"] is False and target["back"] == f"/folder/{placed['trip']}"
    r = home.post(f"/api/folders/{placed['trip']}/place", json={"lat": 41.88, "lon": -87.63}, headers=APP)
    assert r.status_code == 200 and r.json()["undo"]
    assert home.get("/api/places/target", params={"kind": "folder", "id": placed["trip"]}).json()["by_hand"]
    assert home.post(f"/api/folders/{placed['trip']}/place", json={"clear": True}, headers=APP).status_code == 200
    assert con.execute("SELECT 1 FROM folder_place").fetchone() is None
    assert home.get("/api/towns", params={"q": "chicago"}).json()["towns"][0]["name"].startswith("Chicago")


def test_members_place_the_photos_they_see_only(con, cfg, library):
    fam = fx.build(con, cfg, library)
    fx.account(con, fam["people"]["Sam"], "sam@example.com", "admin")
    cora = fx.account(con, fam["people"]["Cora"], "cora@example.com")
    settings.save(con, cfg, {"accounts_enabled": True})
    member = TestClient(webapp.create_app(cfg), client=LAN, follow_redirects=False)
    member.cookies.set(accounts.COOKIE, accounts.new_session(con, cora["id"], 30))
    mine, secret = fam["photos"]["cora_bart.jpg"], fam["photos"]["zed.jpg"]
    r = member.post(f"/api/images/{mine}/place", json={"lat": 45.5, "lon": -122.6}, headers=APP)
    assert r.status_code == 200, r.text
    change = con.execute("SELECT account_id, kind FROM change_log ORDER BY id DESC LIMIT 1").fetchone()
    assert tuple(change) == (cora["id"], "places")  # an admin can undo it on Recent changes
    assert member.post(f"/api/images/{secret}/place", json={"lat": 1, "lon": 1}, headers=APP).status_code == 404
    assert member.get("/api/places/target", params={"kind": "image", "id": secret}).status_code == 404
    folder = con.execute("SELECT id FROM folder WHERE path LIKE '%Picnic'").fetchone()[0]
    assert member.get("/api/places/target", params={"kind": "folder", "id": folder}).status_code == 404
    assert member.post(f"/api/folders/{folder}/place", json={"lat": 1, "lon": 1}, headers=APP).status_code in (
        403, 404)
    assert con.execute("SELECT count(*) FROM image_place").fetchone()[0] == 1


def test_a_nearer_folder_named_for_a_place_wins_over_one_placed_by_hand_above_it(con, cfg, library):
    """Trip is put on the map by hand, and Trip/Paris is a place its name gives: Paris's photos are in Paris on every
    page, and so their sidecars (and files, when written) say no place, as a place from a name stays in the catalog,
    rather than the Trip folder's spot far away."""
    write_jpeg(library / "Trip" / "Paris" / "tower.jpg", taken="2015:06:01 10:00:00")
    write_jpeg(library / "Trip" / "start.jpg", color="blue", taken="2015:06:01 08:00:00")
    scan(con, cfg)
    ingest(con, cfg)
    tower, start = (con.execute("SELECT image_id FROM file WHERE path LIKE ?", (f"%/{n}",)).fetchone()[0]
                    for n in ("tower.jpg", "start.jpg"))
    trip, paris = (con.execute("SELECT id FROM folder WHERE path = ?", (rel(*p),)).fetchone()[0]
                   for p in (("Trip",), ("Trip", "Paris")))
    con.execute("UPDATE folder SET place = 'Paris, France' WHERE id = ?", (paris,))
    con.execute("""INSERT INTO place(name, lat, lon, found_as, source, looked_up)
                   VALUES ('Paris, France', 48.8566, 2.3522, 'Paris', 'osm', '2026-01-01')""")
    places.place_folder(con, trip, 41.88, -87.63)
    con.commit()
    assert places.photo_place(con, tower, catalog.image(con, tower))["how"] == "name"
    assert sidecars.metadata(con, tower) is None  # nothing given by hand: no sidecar
    assert sidecars.metadata(con, start).gps == (41.88, -87.63)  # Trip's own photo: the folder's spot
    assert places.HandPlaces(con).of(tower) is None


def test_a_town_list_cut_short_names_no_places_and_breaks_no_page(tmp_path, monkeypatch):
    """A copy of LineageBox whose town list is missing or was cut short in copying: photos just aren't named after a
    town (the photo page and the map still show), and the list isn't read again for every photo."""
    cut = tmp_path / "towns.tsv.gz"
    cut.write_bytes(towns.DATA.read_bytes()[:5000])
    monkeypatch.setattr(towns, "DATA", cut)
    monkeypatch.setattr(towns, "_loaded", None)
    monkeypatch.setattr(towns, "_missing", False)
    assert towns.label(39.80, -89.64) is None and towns.search("springfield") == []
    cut.unlink()
    assert towns.label(39.80, -89.64) is None  # not read again


def test_a_photos_place_by_hand_taken_away_and_a_place_that_isnt_one(con, cfg, placed):
    home = TestClient(webapp.create_app(cfg), client=LAN)
    gps = placed["gps.jpg"]
    assert home.post(f"/api/images/{gps}/place", json={"lat": 34.05, "lon": -118.24}, headers=APP).status_code == 200
    r = home.post(f"/api/images/{gps}/place", json={"clear": True}, headers=APP)
    assert r.status_code == 200 and r.json()["label"] == "Springfield, Illinois, United States"  # its GPS again
    assert con.execute("SELECT 1 FROM image_place").fetchone() is None
    for body in ({}, {"lat": None, "lon": 3}, {"lat": "north", "lon": 3}):
        assert home.post(f"/api/images/{gps}/place", json=body, headers=APP).status_code in (400, 422)
    assert home.post("/api/images/999999/place", json={"lat": 1, "lon": 1}, headers=APP).status_code == 404
    assert home.post("/api/images/999999/write-file", headers=APP).status_code == 404
