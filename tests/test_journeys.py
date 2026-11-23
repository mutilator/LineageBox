"""A journey (journeys.py): an album read as a trip, its stops in the album's own order, the line between them on the
map, and putting an album in the order its photos were taken."""
import json

import pytest
from fastapi.testclient import TestClient

from lineagebox import albums, journeys, places
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests.conftest import LAN, write_jpeg

APP = {"X-Requested-With": "lineagebox"}

# a trip there and back: Michigan, Colorado, Yellowstone, Mount Rushmore, the Mall of America, Michigan again
TRIP = [("home.jpg", 42.963, -85.668, "2012:07:01 09:00:00"),
        ("denver.jpg", 39.739, -104.990, "2012:07:03 12:00:00"),
        ("yellowstone.jpg", 44.662, -111.104, "2012:07:06 10:00:00"),
        ("rushmore.jpg", 43.879, -103.459, "2012:07:09 15:00:00"),
        ("mall.jpg", 44.854, -93.242, "2012:07:11 13:00:00"),
        ("back.jpg", 42.963, -85.668, "2012:07:12 18:00:00")]


@pytest.fixture
def trip(con, cfg, library):
    """The photos of the trip, in a folder with no place of its own, each put on the map by hand, and an album of
    them in the order they were traveled."""
    for n, (name, _lat, _lon, taken) in enumerate(TRIP):
        write_jpeg(library / "Vacation" / name, color=("red", "blue", "green")[n % 3], taken=taken)
    scan(con, cfg)
    ingest(con, cfg)
    ids = {}
    for name, lat, lon, _taken in TRIP:
        image_id = con.execute("SELECT image_id FROM file WHERE path LIKE ?", (f"%/{name}",)).fetchone()[0]
        places.place_photo(con, image_id, lat, lon)
        ids[name] = image_id
    album_id = albums.create(con, "Vacation 2012", None)
    albums.add(con, album_id, [ids[name] for name, *_ in TRIP])
    con.commit()
    return {"album": album_id, "ids": ids}


def test_the_album_order_is_the_journey(con, trip):
    found = journeys.of_album(con, trip["album"])
    stops = found["stops"]
    assert [s["n"] for s in stops] == [1, 2, 3, 4, 5, 6]
    assert stops[0]["name"].startswith("Grand Rapids") and stops[1]["name"].startswith("Denver")
    assert stops[3]["name"].endswith("United States") and "Rapid City" in stops[3]["name"]
    # coming home is a stop of its own, although it is the place it set out from
    assert stops[5]["name"] == stops[0]["name"] and stops[5]["km"] > 600
    assert found["unplaced"] == 0 and found["placed"] == len(TRIP)
    assert found["km"] > 4000 and found["days"] == 12  # July 1st to the 12th, both counted
    assert stops[0]["from"] == "2012-07-01" and stops[0]["to"] == "2012-07-01"


def test_photos_taken_near_one_another_are_one_stop(con, cfg, library, trip):
    """A second photo 12 km from the first is the same stop at 25 km, and a stop of its own at 5 km."""
    write_jpeg(library / "Vacation" / "nearby.jpg", color="pink", taken="2012:07:03 16:00:00")
    scan(con, cfg)
    ingest(con, cfg)
    near = con.execute("SELECT image_id FROM file WHERE path LIKE '%/nearby.jpg'").fetchone()[0]
    places.place_photo(con, near, 39.845, -104.990)  # about 12 km north of the first Denver photo
    albums.add(con, trip["album"], [near])
    albums.reorder(con, trip["album"], [trip["ids"]["home.jpg"], trip["ids"]["denver.jpg"], near,
                                        *(trip["ids"][name] for name, *_ in TRIP[2:])])
    con.commit()
    one = journeys.of_album(con, trip["album"], 25.0)
    assert len(one["stops"]) == 6 and one["stops"][1]["count"] == 2
    apart = journeys.of_album(con, trip["album"], 5.0)
    assert len(apart["stops"]) == 7 and apart["stops"][1]["count"] == 1
    # the pages only offer the distances they know; anything else is the usual one
    assert journeys.nearness(5) == 5.0 and journeys.nearness(7) == journeys.NEAR_KM
    assert journeys.nearness(None) == journeys.NEAR_KM and journeys.nearness("100") == 100.0


def test_photos_with_no_place_are_counted_not_dropped(con, cfg, library, trip):
    write_jpeg(library / "Vacation" / "somewhere.jpg", color="black", taken="2012:07:05 10:00:00")
    scan(con, cfg)
    ingest(con, cfg)
    lost = con.execute("SELECT image_id FROM file WHERE path LIKE '%/somewhere.jpg'").fetchone()[0]
    albums.add(con, trip["album"], [lost])
    con.commit()
    found = journeys.of_album(con, trip["album"])
    assert found["unplaced"] == 1 and len(found["stops"]) == 6
    assert sum(s["count"] for s in found["stops"]) == len(TRIP)


def test_an_album_in_one_place_draws_no_journey(con, trip):
    only_home = albums.create(con, "At home", None)
    albums.add(con, only_home, [trip["ids"]["home.jpg"], trip["ids"]["back.jpg"]])
    con.commit()
    assert len(journeys.of_album(con, only_home)["stops"]) == 1
    assert journeys.how_many(con, [only_home, trip["album"]]) == {only_home: 1, trip["album"]: 6}


def test_the_journey_page_draws_it(cfg, trip):
    home = TestClient(webapp.create_app(cfg), client=LAN)
    page = home.get(f"/album/{trip['album']}/journey")
    assert page.status_code == 200
    assert "Grand Rapids" in page.text and "Follow the journey" in page.text
    drawn = json.loads(page.text.split('id="journey">')[1].split("</script>")[0])
    assert [s["n"] for s in drawn["stops"]] == [1, 2, 3, 4, 5, 6]
    assert all(s["photos"] and s["photos"][0]["sha"] for s in drawn["stops"])
    # the distance the page offers changes what counts as one stop, and anything else is the usual one
    assert "100 km" in page.text
    assert len(json.loads(home.get(f"/album/{trip['album']}/journey?near=5").text
                          .split('id="journey">')[1].split("</script>")[0])["stops"]) == 6
    assert "Grand Rapids" in home.get(f"/album/{trip['album']}/journey?near=zz").text  # junk: the usual distance
    # the album and the list of albums say it is there
    assert "See the journey" in home.get(f"/album/{trip['album']}").text
    assert "a journey of 6 stops" in home.get("/albums").text


def test_an_album_with_no_places_says_what_it_takes(con, cfg, library):
    write_jpeg(library / "Day out" / "one.jpg", taken="2012:07:01 09:00:00")
    write_jpeg(library / "Day out" / "two.jpg", color="blue", taken="2012:07:01 10:00:00")
    scan(con, cfg)
    ingest(con, cfg)
    album_id = albums.create(con, "A day out", None)
    albums.add(con, album_id, [r[0] for r in con.execute("SELECT id FROM image")])
    con.commit()
    home = TestClient(webapp.create_app(cfg), client=LAN)
    page = home.get(f"/album/{album_id}/journey")
    assert "doesn't draw a journey yet" in page.text and "Put it on the map" in page.text
    album = home.get(f"/album/{album_id}").text  # the button comes only once it draws one
    assert f'<a class="button" href="/album/{album_id}/journey">' not in album
    assert "draw <b>its journey</b>" in album


def test_put_in_date_order_and_undo_it(con, cfg, trip):
    """An album ticked together comes in any order; this puts it in the order the trip was traveled."""
    backwards = [trip["ids"][name] for name, *_ in reversed(TRIP)]
    albums.reorder(con, trip["album"], backwards)
    con.commit()
    home = TestClient(webapp.create_app(cfg), client=LAN)
    assert len(journeys.of_album(con, trip["album"])["stops"]) == 6  # backwards is a journey too, the other way
    r = home.post(f"/api/albums/{trip['album']}/by-date", headers=APP)
    assert r.status_code == 200, r.text
    assert albums.photo_ids(con, trip["album"]) == [trip["ids"][name] for name, *_ in TRIP]
    assert home.post(f"/api/changes/{r.json()['undo']}/undo-own", headers=APP).status_code == 200
    assert albums.photo_ids(con, trip["album"]) == backwards


def test_the_map_draws_the_journeys_too(con, cfg, trip):
    """The Map page has a Journeys tick of its own: /api/map carries them, and a year leaves out the others."""
    home = TestClient(webapp.create_app(cfg), client=LAN)
    every = home.get("/api/map").json()
    assert len(every["journeys"]) == 1
    drawn = every["journeys"][0]
    assert drawn["album_id"] == trip["album"] and drawn["name"] == "Vacation 2012"
    assert [s["n"] for s in drawn["stops"]] == [1, 2, 3, 4, 5, 6] and drawn["years"] == [2012]
    assert drawn["km"] > 4000 and drawn["days"] == 12
    assert all(s["name"] and s["count"] for s in drawn["stops"])
    assert home.get("/api/map?year=2012").json()["journeys"] == [drawn]
    assert home.get("/api/map?year=2013").json()["journeys"] == []
    # an album whose photos are all in one place draws no journey, so the map has nothing for it
    one = albums.create(con, "At home", None)
    albums.add(con, one, [trip["ids"]["home.jpg"]])
    con.commit()
    assert len(journeys.on_map(con)) == 1
