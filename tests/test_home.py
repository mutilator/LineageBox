"""Home: its memories, the newest photos by the day they were taken, the storage meter; the Status page's storage;
and what pointing at a face shows (the whole photo, with the face's place on it)."""
import shutil
from collections import namedtuple
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from lineagebox import catalog, memories, people, storage, tours
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from lineagebox.web.routes.home import by_day
from tests.conftest import LAN, rel, write_jpeg

APP = {"X-Requested-With": "lineagebox"}
TODAY = date.today()
FIVE_AGO = memories._same_day(TODAY, TODAY.year - 5)


@pytest.fixture
def dated(con, cfg, library):
    """Five photos from this week five years ago (over two days), two from three years ago (too few for a memory),
    one from yesterday and one from 40 days ago. Returns file name -> image id."""
    shots = {f"ago5_{i}.jpg": FIVE_AGO - timedelta(days=i % 2) for i in range(5)}
    shots |= {f"ago3_{i}.jpg": memories._same_day(TODAY, TODAY.year - 3) for i in range(2)}
    shots |= {"yesterday.jpg": TODAY - timedelta(days=1), "earlier.jpg": TODAY - timedelta(days=40)}
    for i, (name, day) in enumerate(shots.items()):
        write_jpeg(library / "Dated" / name, size=(150, 100), color=(25 * i, 90, 140),
                   taken=f"{day:%Y:%m:%d} 10:00:00")
    scan(con, cfg)
    ingest(con, cfg)
    con.execute("UPDATE image SET sharpness = 100")  # flat test colours look blurry
    con.commit()
    return {name: con.execute("SELECT image_id FROM file WHERE path = ?", (rel("Dated", name),)).fetchone()[0]
            for name in shots}


def sha(con, image_id):
    return catalog.image(con, image_id)["sha256"]


def test_photos_by_day():
    today = date(2026, 11, 5)  # a Thursday
    row = lambda day, w=150, h=100: {"taken_at": f"{day}T10:00:00", "width": w, "height": h, "id": 0}
    rows = [row("2026-11-05"), row("2026-11-04"), row("2026-10-31"), row("2026-10-28"), row("2026-10-27"),
            *[row("2026-10-12", 300, 200)] * 4, row("2025-11-05", 10000, 100), row("2025-11-05", 100, 0)]
    days = by_day(rows, today)
    assert [(d["label"], d["month"]) for d in days] == [
        ("Today", None), ("Yesterday", None), ("Saturday", None), ("Wed, Oct 28", "October"), ("Tue, Oct 27", None),
        ("Mon, Oct 12", None), ("Wed, Nov 5, 2025", "November 2025")]
    assert [p["ratio"] for p in days[-1]["photos"]] == [2.5, 1.0]  # a panorama in bounds; a size unknown: square


def test_home_shows_the_newest_photos_by_day(dated, cfg, con):
    page = TestClient(webapp.create_app(cfg), client=LAN).get("/home").text
    latest = page.split('id="latest"')[1]
    assert "<h3>Yesterday</h3>" in latest
    assert latest.index(sha(con, dated["yesterday.jpg"])) < latest.index(sha(con, dated["earlier.jpg"]))
    assert f'<h2 class="month">{TODAY - timedelta(days=40):%B}' in latest
    assert "--r: 1.5" in latest  # each photo as wide as its shape
    # photos with no day known (only their file's time) aren't in the days: they are under Recently added
    undated = con.execute("SELECT image_id FROM file WHERE path = ?", (rel("transparent.png"),))
    small = sha(con, undated.fetchone()[0])
    assert small in latest.split("Recently added")[1] and small not in latest.split("Recently added")[0]
    assert 'data-later="/home/memories"' in page


def test_memories_from_this_week_in_earlier_years(dated, cfg, con):
    found = memories.years_ago(con, TODAY)
    assert [(m["title"], m["count"]) for m in found] == [("5 years ago", 5)]  # three years ago had only two
    assert found[0]["sub"].endswith(str(FIVE_AGO.year)) and found[0]["cover"] in {
        sha(con, dated[f"ago5_{i}.jpg"]) for i in range(5)}
    client = TestClient(webapp.create_app(cfg), client=LAN)
    row = client.get("/home/memories").text
    assert "<b>5 years ago</b>" in row and found[0]["href"].replace("&", "&amp;") in row
    page = client.get(found[0]["href"]).text  # it plays like a tour, and isn't one yet
    assert all(sha(con, dated[f"ago5_{i}.jpg"]) in page for i in range(5))
    assert sha(con, dated["yesterday.jpg"]) not in page
    assert "Keep as a tour" in page and "Download all" not in page and found[0]["sub"] in page
    body = parse_qs(urlsplit(found[0]["href"]).query)["body"][0]
    r = client.post("/api/tours", content=body, headers={**APP, "Content-Type": "application/json"})
    saved = tours.tour(con, int(r.json()["go"].rsplit("/", 1)[1]))
    assert saved["name"] == found[0]["sub"] and len(tours.photos(con, saved["spec"])) == 5


def test_a_tour_not_saved_yet_must_make_sense(dated, cfg, con):
    client = TestClient(webapp.create_app(cfg), client=LAN)
    for body in ("", "not json", "[1]", '{"type": 99999}', '{"spec": {"who": "x"}}',
                 '{"spec": {"who": {"mode": "person", "person_ids": [99999]}}}'):
        assert client.get("/tours/try", params={"body": body}).status_code == 404, body


def test_own_tours_and_suggestions_join_the_memories(dated, cfg, con):
    tid = tours.save_tour(con, None, None, tours.clean({"title": "All of them"}), "All of them")
    own = memories.own_tours(con, tours.tours(con), TODAY)
    assert [(m["title"], m["href"]) for m in own] == [("All of them", f"/tour/{tid}")]
    suggested = memories.suggested([{"name": "Owen through the years", "why": "the person in the most photos",
                                     "count": 20, "covers": ["a", "b"], "body": {"type": 3, "person": 1}}], TODAY)
    assert suggested[0]["href"] == memories.try_href({"type": 3, "person": 1}) and suggested[0]["cover"] in "ab"
    years = memories.years_ago(con, TODAY)
    assert memories.row(years, suggested * 3, own) == years + suggested * 2 + own + suggested
    assert f'href="/tour/{tid}"' in TestClient(webapp.create_app(cfg), client=LAN).get("/home/memories").text


Usage = namedtuple("Usage", "total used free")


def test_storage(dated, cfg, con, monkeypatch):
    disks = storage.disks(con, cfg)
    assert len(disks) == 1 and disks[0]["folders"] == ["lib"] and disks[0]["data"]  # one disk holds both
    on_disk = sum(p.stat().st_size for p in cfg.share_root.rglob("*") if p.is_file() and p.suffix != ".txt")
    files = con.execute("SELECT SUM(size) FROM file WHERE NOT missing").fetchone()[0]
    assert disks[0]["photos"] == files and 0 < files <= on_disk
    assert disks[0]["ours"] is None  # the worker hasn't measured the data folder yet
    measured = storage.measure_data(con, cfg)
    assert 0 < measured <= sum(p.stat().st_size for p in cfg.data_dir.rglob("*") if p.is_file())  # and the log grew
    assert storage.disks(con, cfg)[0]["ours"] == measured
    assert [storage.fmt_size(n) for n in (2048, 812 * 2**20, int(74.8 * 2**30), int(3.6 * 2**40), 250 * 2**30)] == [
        "2 KB", "812 MB", "74.8 GB", "3.6 TB", "250 GB"]
    client = TestClient(webapp.create_app(cfg), client=LAN)
    assert 'href="/status#storage"' in client.get("/home").text
    status = client.get("/status").text
    assert 'id="storage"' in status and "Photos and videos" in status and storage.fmt_size(measured) in status
    assert "running low" not in status
    tb = 2**40
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(tb, tb - 5 * 2**30, 5 * 2**30))
    low = storage.disks(con, cfg)[0]
    assert low["low"] and low["other"] == tb - 5 * 2**30 - files - measured
    client = TestClient(webapp.create_app(cfg), client=LAN)  # a new app: the storage is kept a minute
    assert "running low" in client.get("/status").text and 'class="storage-meter low"' in client.get("/home").text


def test_pointing_at_a_face_shows_its_whole_photo(dated, cfg, con):
    image_id = dated["yesterday.jpg"]
    face = con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding)
                          VALUES (?, 0.25, 0.1, 0.5, 0.6, 0.9, 25, x'00')""", (image_id,)).lastrowid
    frame = con.execute("""INSERT INTO face(image_id, frame, x1, y1, x2, y2, det_score, quality, embedding)
                           VALUES (?, 3, 0.1, 0.1, 0.2, 0.2, 0.9, 25, x'00')""", (image_id,)).lastrowid
    people.name_faces(con, [face], "Ann Miller")
    con.commit()
    client = TestClient(webapp.create_app(cfg), client=LAN)
    s = sha(con, image_id)
    assert client.get(f"/api/faces/{face}/photo").json() == {
        "src": f"/p/{s}", "thumb": f"/t/{s}", "box": [0.25, 0.1, 0.5, 0.6], "width": 150, "height": 100}
    on_video = client.get(f"/api/faces/{frame}/photo").json()  # found on a video's frame: that frame
    assert on_video["src"] == f"/frame/{s}/3" and on_video["thumb"] is None
    assert client.get("/api/faces/99999/photo").status_code == 404
