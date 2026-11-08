"""A camera's clock put right from its own photos (camera_clock.py, routes/camera_clock.py): a stretch of its photos,
from the first it dated wrong to the last, moved as far as one of them whose real date was given; a second one dated
spreads a clock's drift over the photos between; kept for photos read later, changed at either end, and undone like
any change. The page shows its photos a month at a time, marks those with someone not born yet, and says what can be
done with the photo clicked."""
import json
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from lineagebox import camera_clock, db, people, tagging
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests.conftest import LAN, rel, write_jpeg
from tests.test_face_checks import A, born, face, like, photo

APP = {"X-Requested-With": "lineagebox"}


def taken(con, image_id):
    return con.execute("SELECT taken_at FROM image WHERE id = ?", (image_id,)).fetchone()[0]


def pins(*pairs):
    return [{"at": at, "real": real, "image": n} for n, (at, real) in enumerate(pairs, 1)]


def january(con, *days, camera="Cam"):
    """Photos the camera dated on those days of January 2008, at midnight: photo n on the nth day given."""
    for n, day in enumerate(days, 1):
        photo(con, n, f"2008-01-{day:02d}T00:00:00", camera=camera)
    con.commit()


def stretch_id(con):
    return con.execute("SELECT max(id) FROM camera_clock").fetchone()[0]


def test_how_far_a_photo_moves():
    one = pins(("2008-01-01T00:00:00", "2010-01-01T00:00:00"))
    assert camera_clock.offset(one, "2007-06-01T00:00:00") == camera_clock.offset(one, "2009-01-01T00:00:00")
    two = pins(("2008-01-01T00:00:00", "2010-01-01T00:00:00"), ("2008-01-21T00:00:00", "2010-01-21T02:00:00"))
    base = camera_clock.offset(two, "2008-01-01T00:00:00")
    assert camera_clock.offset(two, "2008-01-11T00:00:00") == base + 3600  # halfway: half of the two hours
    assert camera_clock.offset(two, "2007-12-01T00:00:00") == base  # before the first: as much as the first
    assert camera_clock.offset(two, "2008-03-01T00:00:00") == base + 7200  # after the last: as much as the last
    assert camera_clock.moved("9999-12-31T23:00:00", 7200) == "9999-12-31T23:00:00"  # out of the calendar: stays
    # said by the calendar from the camera's date: two years from a day in 2008 is two years, though 2008 is a leap year
    assert camera_clock.amount(731 * 86400, "2008-01-01T00:00:00") == [(2, "years")]
    assert camera_clock.amount(-730 * 86400, "2010-11-28T10:00:00") == [(2, "years")]
    assert camera_clock.amount(29 * 86400 + 3 * 3600, "2008-01-10T00:00:00") == [(29, "days"), (3, "hours")]
    assert camera_clock.amount(29 * 86400, "2008-01-31T00:00:00") == [(1, "months")]  # Feb 29: its last day
    assert camera_clock.amount(2 * 3600 + 5 * 60) == [(2, "hours"), (5, "minutes")]
    assert camera_clock.amount(30) == []


def test_a_cameras_clock_put_right_from_one_of_its_photos(con, cfg, library):
    """The photos from the first to the last picked move as far as the one dated; a date set by hand stays; a photo of
    the camera read later is moved too, and undoing puts them all back."""
    for name, at, color in (("a.jpg", "2008:11:28 10:00:00", "red"), ("b.jpg", "2009:02:01 09:30:00", "green"),
                            ("c.jpg", "2012:05:05 08:00:00", "blue")):
        write_jpeg(library / "Cam" / name, color=color, taken=at, make="Acme", model="Acme Snap 2")
    scan(con, cfg)
    ingest(con, cfg)
    ids = {p: con.execute("SELECT image_id FROM file WHERE path = ?", (rel("Cam", p),)).fetchone()[0]
           for p in ("a.jpg", "b.jpg", "c.jpg", )}
    camera = con.execute("SELECT camera FROM image WHERE id = ?", (ids["a.jpg"],)).fetchone()[0]
    tagging.set_date(con, [ids["b.jpg"]], "summer 1999")  # set by hand: stays
    con.commit()
    client = TestClient(webapp.create_app(cfg), client=LAN)
    body = {"camera": camera, "first_id": ids["a.jpg"], "last_id": ids["b.jpg"], "image_id": ids["a.jpg"],
            "day": "2010-11-28", "time": ""}
    assert client.post("/api/camera-clock", json=body).status_code == 403
    made = client.post("/api/camera-clock", json=body, headers=APP).json()
    assert made["message"] == "Moved the dates of 2 photos." and made["undo"]
    row = lambda i: tuple(con.execute("""SELECT taken_at, taken_at_source, auto_taken_at, camera_taken_at FROM image
                                         WHERE id = ?""", (i,)).fetchone())
    assert row(ids["a.jpg"]) == ("2010-11-28T10:00:00", "exif", None, "2008-11-28T10:00:00")  # no time: the camera's
    assert row(ids["b.jpg"])[1:] == ("user", "2011-02-01T09:30:00", "2009-02-01T09:30:00")
    assert row(ids["c.jpg"])[0] == "2012-05-05T08:00:00"  # after the last picked
    # a photo of that camera read later is moved too
    write_jpeg(library / "Cam" / "d.jpg", color="purple", taken="2009:01:15 12:00:00", make="Acme", model="Acme Snap 2")
    scan(con, cfg)
    ingest(con, cfg)
    later = con.execute("SELECT image_id FROM file WHERE path = ?", (rel("Cam", "d.jpg"),)).fetchone()[0]
    assert row(later)[0] == "2011-01-15T12:00:00"
    page = client.get("/cleanup/camera-clock", params={"camera": camera}).text
    assert "3 photos from Nov 28, 2008 10:00 to Feb 1, 2009 09:30" in page and "2 years later" in page
    # undone: the dates come back, the one read since too, and the stretch is forgotten
    assert client.post(f"/api/changes/{made['undo']}/undo", headers=APP).status_code == 200
    assert row(ids["a.jpg"]) == ("2008-11-28T10:00:00", "exif", None, None)
    assert row(later)[0] == "2009-01-15T12:00:00"
    assert con.execute("SELECT count(*) FROM camera_clock").fetchone()[0] == 0


def test_a_second_photo_dated_spreads_its_clocks_drift(con, cfg):
    january(con, 1, 11, 21)
    photo(con, 4, "2008-06-01T00:00:00", camera="Cam")  # after the stretch: stays
    con.commit()
    client = TestClient(webapp.create_app(cfg), client=LAN)
    assert client.post("/api/camera-clock", headers=APP, json={"camera": "Cam", "first_id": 1, "last_id": 3,
                       "image_id": 1, "day": "2010-01-01", "time": "00:00"}).status_code == 200
    one = stretch_id(con)
    assert [taken(con, i) for i in (1, 2, 3, 4)] == ["2010-01-01T00:00:00", "2010-01-11T00:00:00",
                                                     "2010-01-21T00:00:00", "2008-06-01T00:00:00"]
    pinned = client.post(f"/api/camera-clock/{one}/pin", headers=APP,
                         json={"image_id": 3, "day": "2010-01-21", "time": "02:00"})
    assert pinned.status_code == 200 and pinned.json()["undo"]
    assert taken(con, 2) == "2010-01-11T01:00:00"  # halfway: an hour of the two its clock lost
    assert taken(con, 3) == "2010-01-21T02:00:00"
    page = client.get("/cleanup/camera-clock", params={"camera": "Cam"}).text
    assert "2 years later" in page and "2 hours more by the last (its clock lost time)" in page
    # its date given again, then forgotten: the first one says how far they all move
    assert client.post(f"/api/camera-clock/{one}/pin", headers=APP,
                       json={"image_id": 3, "day": "2010-01-21", "time": "04:00"}).status_code == 200
    assert taken(con, 2) == "2010-01-11T02:00:00" and len(camera_clock.stretch(con, one)["pins"]) == 2
    assert client.post(f"/api/camera-clock/{one}/unpin", headers=APP, json={"image_id": 3}).status_code == 200
    assert taken(con, 2) == "2010-01-11T00:00:00" and taken(con, 3) == "2010-01-21T00:00:00"
    only = client.post(f"/api/camera-clock/{one}/unpin", headers=APP, json={"image_id": 1})
    assert only.status_code == 400 and "only photo" in only.json()["detail"]
    # the second date undone from Recent changes
    assert client.post(f"/api/camera-clock/{one}/pin", headers=APP,
                       json={"image_id": 3, "day": "2010-01-21", "time": "02:00"}).status_code == 200
    undo = con.execute("SELECT max(id) FROM change_log").fetchone()[0]
    assert client.post(f"/api/changes/{undo}/undo", headers=APP).status_code == 200
    assert taken(con, 2) == "2010-01-11T00:00:00"


def test_what_a_date_given_cant_be(con, cfg):
    january(con, 1, 11)
    photo(con, 3, "2015-06-01T00:00:00", camera="Cam")
    photo(con, 4, "2016-06-01T00:00:00", camera="Cam")
    photo(con, 5, "2016-06-01T00:00:00", camera="Other")
    con.commit()
    client = TestClient(webapp.create_app(cfg), client=LAN)
    new = lambda **b: client.post("/api/camera-clock", headers=APP, json={"camera": "Cam", "first_id": 1, **b})
    assert new(last_id=2, image_id=1, day="2010-01-01").status_code == 200
    one = stretch_id(con)
    pin = lambda **b: client.post(f"/api/camera-clock/{one}/pin", headers=APP, json={"image_id": 2, **b})
    said = lambda r: (r.status_code, r.json()["detail"])
    assert "a clock only runs forward" in said(pin(day="2009-12-01"))[1]  # before the photo it took first
    assert "Pick the day" in said(pin(day="soon"))[1]
    assert "time of day" in said(pin(day="2010-01-11", time="25:00"))[1]
    assert "before any photo" in said(pin(day="1800-01-01"))[1]
    assert "hasn't come yet" in said(pin(day=f"{date.today().year + 2}-01-01"))[1]
    assert "isn't one of these" in said(pin(image_id=3, day="2016-01-01"))[1]
    assert "isn't one this camera dated" in said(pin(image_id=5, day="2016-01-01"))[1]
    # a stretch over another; one that would put a later photo in the future
    assert "put right already" in said(new(first_id=2, last_id=3, image_id=3, day="2015-07-01"))[1]
    assert "in the future" in said(new(first_id=3, image_id=3, day=date.today().isoformat()))[1]
    assert client.post("/api/camera-clock", headers=APP, json={"camera": "Nobody's", "first_id": 1, "image_id": 1,
                                                               "day": "2010-01-01"}).status_code == 404
    assert client.post("/api/camera-clock/999/pin", headers=APP, json={"image_id": 1, "day": "2010-01-01"}).status_code == 404
    assert client.post("/api/camera-clock/999/remove", headers=APP).status_code == 404
    # nothing kept but the first, and nothing moved but its photos
    assert con.execute("SELECT count(*) FROM camera_clock").fetchone()[0] == 1
    assert [taken(con, i) for i in (2, 3, 4)] == ["2010-01-11T00:00:00", "2015-06-01T00:00:00", "2016-06-01T00:00:00"]


def test_where_the_wrong_dates_start_and_end(con, cfg):
    january(con, 1, 11, 21, 31)
    client = TestClient(webapp.create_app(cfg), client=LAN)
    assert client.post("/api/camera-clock", headers=APP, json={"camera": "Cam", "first_id": 1, "last_id": 2,
                       "image_id": 1, "day": "2010-01-01"}).status_code == 200
    one = stretch_id(con)
    bounds = lambda **b: client.post(f"/api/camera-clock/{one}/bounds", headers=APP, json=b)
    assert bounds(last_id=3).json()["message"] == "Now 3 photos."
    assert taken(con, 3) == "2010-01-21T00:00:00" and taken(con, 4) == "2008-01-31T00:00:00"
    assert bounds(to_end=True).status_code == 200  # on to every later photo, those read later too
    assert taken(con, 4) == "2010-01-31T00:00:00"
    photo(con, 5, "2009-05-01T00:00:00", camera="Cam")
    camera_clock.apply_new(con, 5, "Cam")
    con.commit()
    assert taken(con, 5) == "2011-05-02T00:00:00"  # 731 days, as photo 1 moved (2008 had a Feb 29)
    # starting later leaves out a photo dated: its date is forgotten
    assert client.post(f"/api/camera-clock/{one}/pin", headers=APP,
                       json={"image_id": 3, "day": "2010-01-21", "time": "02:00"}).status_code == 200
    started = bounds(first_id=2).json()
    assert "1 photo you dated is no longer in it" in started["message"]
    assert taken(con, 1) == "2008-01-01T00:00:00" and taken(con, 2) == "2010-01-11T02:00:00"
    kept = bounds(first_id=4)
    assert kept.status_code == 400 and "Keep at least one" in kept.json()["detail"]
    assert bounds(first_id=4, last_id=2).status_code == 400  # the last before the first
    # put back: the camera's dates again; undone, moved again
    removed = client.post(f"/api/camera-clock/{one}/remove", headers=APP).json()
    assert removed["message"] == "Put back: 4 photos have the camera's dates again."
    assert [taken(con, i) for i in (2, 3, 4, 5)] == ["2008-01-11T00:00:00", "2008-01-21T00:00:00",
                                                     "2008-01-31T00:00:00", "2009-05-01T00:00:00"]
    assert client.post(f"/api/changes/{removed['undo']}/undo", headers=APP).status_code == 200
    assert taken(con, 3) == "2010-01-21T02:00:00" and taken(con, 5) == "2011-05-02T02:00:00"


def test_the_camera_clock_page(con, cfg, monkeypatch):
    kid = people.person_id(con, "Owen Miller")
    born(con, kid, "2010-11-27")
    photo(con, 1, "2009-05-01T10:00:00", camera="Cam")
    face(con, 1, like(A), kid, "auto")  # dated before he was born: the clock, likely
    photo(con, 2, "2009-05-02T11:15:00", camera="Cam")
    photo(con, 3, "2012-07-01T09:00:00", camera="Cam")
    photo(con, 4, "2008-01-01T09:00:00", camera="Cam")
    con.commit()
    client = TestClient(webapp.create_app(cfg), client=LAN)
    get = lambda **q: client.get("/cleanup/camera-clock", params=q).text
    cameras = get()
    assert "/cleanup/camera-clock?camera=Cam" in cameras and "1 face" in cameras
    # it opens on the month of the first photo with someone not born yet, marked
    page = get(camera="Cam")
    assert 'id="p1"' in page and 'id="p2"' in page and 'id="p4"' not in page and "How to put it right" in page
    assert 'class="clock-mark warn"' in page and "not born yet: Owen Miller" in page
    assert 'id="p4"' in get(camera="Cam", year="2008") and 'id="p3"' in get(camera="Cam", year="2012", month="07")
    clicked = get(camera="Cam", photo=1)
    assert "Its dates are wrong from this photo" in clicked and "Owen Miller" in clicked and "not born yet" in clicked
    assert "Now click the last photo it dated wrong" in get(camera="Cam", **{"from": 1})
    picking = get(camera="Cam", photo=2, **{"from": 1, "to": 2})
    assert "Move the 2 photos" in picking and 'name="first_id" value="1"' in picking
    assert 'name="last_id" value="2"' in picking
    assert 'name="first_id" value="1"' in get(camera="Cam", photo=1, **{"from": 2, "to": 1})  # picked backwards
    open_end = get(camera="Cam", photo=3, year="2012", month="07", **{"from": 1, "to": "end"})
    assert "Move the 3 photos" in open_end and 'name="last_id"' not in open_end
    assert "Start from this photo instead" in get(camera="Cam", photo=4, year="2008", **{"from": 1, "to": 2})
    # once put right: the photo clicked says when it was, and where its stretch starts and ends
    assert client.post("/api/camera-clock", headers=APP, json={"camera": "Cam", "first_id": 1, "last_id": 2,
                       "image_id": 1, "day": "2011-05-01", "time": "10:00"}).status_code == 200
    fixed = get(camera="Cam", photo=2)
    assert "Date it" in fixed and "The wrong dates start here" in fixed and "Put all 2 back" in fixed
    assert "2 photos from May 1, 2009 10:00 to May 2, 2009 11:15" in fixed and "2 years later" in fixed
    assert "Change its date" in get(camera="Cam", photo=1)
    assert "Add it to the photos put right before it" in get(camera="Cam", photo=3)
    assert 'class="clock-mark warn"' not in get(camera="Cam", year="2009", month="05")  # after his birth now
    # a month bigger than a page: the page with the photo clicked
    monkeypatch.setattr(camera_clock, "PER_PAGE", 1)
    second = get(camera="Cam", year="2009", month="05", photo=2)
    assert 'id="p2"' in second and 'id="p1"' not in second and "2–2 of 2" in second
    assert 'id="p2"' in get(camera="Cam", year="2009", month="05", page=99)
    assert "Camera clocks" in get(camera="Nobody's") and "/cleanup/camera-clock?camera=Cam" in get(camera="Nobody's")


def test_moves_typed_in_before_become_stretches():
    """026's moves (dates between two days moved by months and seconds) are stretches with one photo dated."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE camera_clock (id INTEGER PRIMARY KEY, camera TEXT NOT NULL, first TEXT NOT NULL,
                   last TEXT NOT NULL, months INTEGER NOT NULL DEFAULT 0, seconds INTEGER NOT NULL DEFAULT 0,
                   made_at TEXT NOT NULL, account_id INTEGER)""")
    con.execute("""INSERT INTO camera_clock VALUES (1, 'Cam', '2008-01-01T00:00:00', '2008-12-31T23:59:59', 24, 3600,
                   '2026-11-08T00:00:00', NULL)""")
    db._module(db.MIGRATIONS_DIR / "032_camera_clock_pins.py").upgrade(con, None)
    kept = camera_clock.stretches(con, "Cam")
    assert [(s["id"], s["first"], s["last"]) for s in kept] == [(1, "2008-01-01T00:00:00", "2008-12-31T23:59:59")]
    assert json.loads(con.execute("SELECT pins FROM camera_clock").fetchone()[0]) == [
        {"at": "2008-01-01T00:00:00", "real": "2010-01-01T01:00:00", "image": None}]
    assert camera_clock.date_for(kept, "2008-06-01T00:00:00") == "2010-06-02T01:00:00"  # 731 days and an hour
    assert camera_clock.date_for(kept, "2009-06-01T00:00:00") == "2009-06-01T00:00:00"


@pytest.mark.parametrize("given", ["", "14:30", "14:30:15"])
def test_a_time_of_day_typed_or_not(given):
    real = camera_clock.real_date("2010-05-06", given, "2008-01-01T09:05:07")
    assert real == {"": "2010-05-06T09:05:07", "14:30": "2010-05-06T14:30:00", "14:30:15": "2010-05-06T14:30:15"}[given]
