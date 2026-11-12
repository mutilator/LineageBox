"""Home: its memories, the newest photos by the day they were taken, the storage meter; the Status page's storage;
and what pointing at a face shows (the whole photo, with the face's place on it)."""
import re
import shutil
from collections import namedtuple
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from lineagebox import catalog, cleanup, events, family, memories, people, storage, tours
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from lineagebox.web.routes import home
from lineagebox.web.routes.home import by_day
from tests.conftest import LAN, rel, write_jpeg
from tests.test_access_web import signed_in, site  # noqa: F401 - site is a fixture

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
    # a face low in yesterday's photo: its square, as Photo tiles makes them unless Whole photos is chosen, aims at it
    con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding)
                   VALUES (?, 0.4, 0.8, 0.5, 0.95, 0.9, 20, zeroblob(2048))""", (dated["yesterday.jpg"],))
    con.commit()
    page = TestClient(webapp.create_app(cfg), client=LAN).get("/home").text
    latest = page.split('id="latest"')[1]
    assert "<h3>Yesterday</h3>" in latest
    assert latest.index(sha(con, dated["yesterday.jpg"])) < latest.index(sha(con, dated["earlier.jpg"]))
    assert f'<h2 class="month">{TODAY - timedelta(days=40):%B}' in latest
    assert "--r: 1.5" in latest  # each photo as wide as its shape, for Whole photos
    tile = latest.split(sha(con, dated["yesterday.jpg"]))[1].split(">")[0]
    assert 'style="object-position:' in tile
    # photos with no day known (only their file's time) aren't in the days: they are under Recently added
    undated = con.execute("SELECT image_id FROM file WHERE path = ?", (rel("transparent.png"),))
    small = sha(con, undated.fetchone()[0])
    assert small in latest.split("Recently added")[1] and small not in latest.split("Recently added")[0]
    assert 'data-later="/home/memories"' in page


def test_a_members_home_shows_her_own_family_first(site, cfg, con):
    """Cora's parents, partner, brother and sister first; then the rest she sees, without those again. The catalog is
    mostly the admin's family, whose photos would fill her Home otherwise."""
    ph, p = site["photos"], site["people"]
    con.execute("INSERT INTO image_person(image_id, person_id, added_at) VALUES (?, ?, '2026-01-01')",
                (ph["scenery.jpg"], p["Bella"]))  # her niece: family she sees, but not her own
    con.commit()
    page = signed_in(cfg, con, site["who"]["Cora"]).get("/home").text
    ours, rest = page.split('id="family-latest"')[1].split('id="latest"')
    assert "Recent photos of your family" in ours and "<h2>Most recent photos</h2>" in rest
    assert "your closest family: Nell, Rex, Milo, Bart, Sam." in ours  # parents, partner, then brother and sister
    for name in ("cora_bart.jpg", "rex.jpg", "hank_sam.jpg"):  # her, her father (recognised), her brother
        assert sha(con, ph[name]) in ours and sha(con, ph[name]) not in rest, name
    assert sha(con, ph["scenery.jpg"]) not in ours and sha(con, ph["scenery.jpg"]) in rest
    assert sha(con, ph["hidden.jpg"]) not in page
    # an admin's Home is as before, and so is a member's who isn't anyone in the tree
    assert 'id="family-latest"' not in signed_in(cfg, con, site["who"]["Sam"]).get("/home").text
    con.execute("UPDATE account SET person_id = NULL WHERE id = ?", (site["who"]["Tina"]["id"],))
    con.commit()
    assert 'id="family-latest"' not in signed_in(cfg, con, site["who"]["Tina"]).get("/home").text
    assert [p[n] for n in ("Cora", "Nell", "Rex", "Milo", "Bart", "Sam")] == family.immediate(family.load(con),
                                                                                              p["Cora"])


def test_a_button_for_each_of_her_family_opens_their_own_home(site, cfg, con):
    ph, p = site["photos"], site["people"]
    cora = signed_in(cfg, con, site["who"]["Cora"])
    page = cora.get("/home").text
    buttons = page.split('class="kin-buttons"')[1].split("</nav>")[0]
    assert page.index('class="kin-buttons"') < page.index('id="family-latest"')
    assert re.findall(r'href="/home/person/(\d+)"', buttons) == [str(p[n]) for n in ("Cora", "Nell", "Rex", "Milo",
                                                                                    "Bart", "Sam")]
    assert "<b>Cora</b><small>you</small>" in buttons  # her own first: only photos of her
    assert "<b>Bart</b><small>your sibling</small>" in buttons  # no sex entered: sibling, not brother
    assert f'<img src="/face/{site["faces"]["cora_bart.jpg"][1]}"' in buttons  # his face; none for those with none
    bart = cora.get(f"/home/person/{p['Bart']}").text
    assert "<h1>Bart</h1>" in bart and "your sibling" in bart and "Recent photos of Bart" in bart
    assert sha(con, ph["cora_bart.jpg"]) in bart and sha(con, ph["rex.jpg"]) not in bart
    assert f'data-later="/home/person/{p["Bart"]}/memories"' in bart and f'href="/person/{p["Bart"]}"' in bart
    assert cora.get(f"/home/person/{p['Bart']}/memories").status_code == 200
    part = cora.get(f"/home/family?before=2011-01-01&person={p['Bart']}").text
    assert "That's all of them." in part
    for hidden in (p["Zed"], p["Hank"]):  # not hers to see
        assert cora.get(f"/home/person/{hidden}").status_code == 404
        assert cora.get(f"/home/family?before=2030-01-01&person={hidden}").status_code == 404
    assert "Recent photos of you" in cora.get(f"/home/person/{p['Cora']}").text


def test_everyone_in_the_tree_has_a_button_for_their_own_photos(site, cfg, con):
    """An admin's Home doesn't put their family's photos first, but has the buttons: their own, then their immediate
    family's. An account that isn't anyone has none (the "me" setting is whoever keeps the catalog), and with
    accounts off the person marked with This is me has them."""
    p = site["people"]
    con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('me', ?)", (str(p["Rex"]),))
    con.commit()
    buttons = lambda page: re.findall(r'class="kin-button" href="/home/person/(\d+)"', page)
    closest = lambda who: [str(x) for x in family.immediate(family.load(con), p[who])]
    sam = signed_in(cfg, con, site["who"]["Sam"])
    page = sam.get("/home").text
    assert buttons(page) == closest("Sam") and closest("Sam")[0] == str(p["Sam"]) and len(closest("Sam")) > 3
    assert "<b>Sam</b><small>you</small>" in page and "<b>Cora</b><small>your sibling</small>" in page
    assert 'id="family-latest"' not in page
    assert "Recent photos of you" in sam.get(f"/home/person/{p['Sam']}").text
    assert "Recent photos of Rex" in sam.get(f"/home/person/{p['Rex']}").text  # the "me" setting isn't Sam
    con.execute("UPDATE account SET person_id = NULL WHERE id = ?", (site["who"]["Sam"]["id"],))
    con.commit()
    assert buttons(signed_in(cfg, con, site["who"]["Sam"]).get("/home").text) == []
    con.execute("DELETE FROM settings WHERE key = 'accounts_enabled'")
    con.commit()
    home_alone = TestClient(webapp.create_app(cfg), client=LAN)
    page = home_alone.get("/home").text
    assert buttons(page) == closest("Rex") and "<b>Rex</b><small>you</small>" in page
    assert "Recent photos of you" in home_alone.get(f"/home/person/{p['Rex']}").text


def test_older_photos_of_her_family_however_long_ago(site, cfg, con, monkeypatch):
    """Show older photos of your family goes back a few days at a time, to the first."""
    monkeypatch.setattr(home, "FAMILY", 1)
    monkeypatch.setattr(home, "FAMILY_MOST", 1)
    monkeypatch.setattr(home, "FAMILY_MORE", 1)
    ph = site["photos"]
    cora = signed_in(cfg, con, site["who"]["Cora"])
    ours = cora.get("/home").text.split('id="family-latest"')[1].split('id="latest"')[0]
    assert sha(con, ph["hank_sam.jpg"]) in ours and sha(con, ph["rex.jpg"]) not in ours
    url = re.search(r'data-append="([^"]+)"', ours).group(1).replace("&amp;", "&")
    assert url == "/home/family?before=2013-05-04&month=2013-05"
    part = cora.get(url).text
    assert sha(con, ph["rex.jpg"]) in part and "<html" not in part and '<h3 class="month">May 2011</h3>' in part
    part = cora.get(re.search(r'data-append="([^"]+)"', part).group(1).replace("&amp;", "&")).text
    assert sha(con, ph["cora_bart.jpg"]) in part and "That's all of them." in part
    # the photos above were from another May: its name isn't said twice
    assert "May 2010" in cora.get("/home/family?before=2011-05-02&month=2011-06").text
    assert "May 2010" not in cora.get("/home/family?before=2011-05-02&month=2010-05").text
    assert cora.get("/home/memories").status_code == 200


def test_a_members_memories_are_her_familys_days(con, cfg, library):
    """Runs of days with photos of her family, however long ago, named by a life event on those days, the tags on
    them, or else their days and who is in them; the newest first."""
    days = {"wed": date(2012, 6, 16), "xmas": date(2018, 12, 24), "married": date(2020, 7, 4),
            "plain": TODAY - timedelta(days=400)}
    for n, (key, day) in enumerate(days.items()):
        for i in range(5):
            write_jpeg(library / "Family" / f"{key}{i}.jpg", size=(150, 100), color=(40 * n, 30 * i, 120),
                       taken=f"{day:%Y:%m:%d} 1{i}:00:00")
    scan(con, cfg)
    ingest(con, cfg)
    con.execute("UPDATE image SET sharpness = 100")
    ana, tom, ruth, grace = (people.person_id(con, n) for n in ("Ana Miller", "Tom Miller", "Ruth Miller",
                                                                 "Grace Baker"))
    family.link(con, ana, "husband", tom)
    family.link(con, ana, "daughter", ruth)
    family.link(con, ana, "mother", grace)
    events.save(con, None, tag="MARR", couple=(ana, tom), date_text="2020-07-04")
    plain = days["plain"]
    events.save(con, None, tag="DEAT", person_id=grace, date_text=plain.isoformat())  # her photos that day: a beach
    events.save(con, None, tag="GRAD", person_id=tom, date_text="2018-12-24")  # he isn't in them: Christmas still
    photo = lambda key, i: con.execute("SELECT image_id FROM file WHERE path = ?",  # noqa: E731
                                       (rel("Family", f"{key}{i}.jpg"),)).fetchone()[0]
    for key in days:
        for i in range(5):
            con.execute("INSERT INTO image_person(image_id, person_id, added_at) VALUES (?, ?, '2026-01-01')",
                        (photo(key, i), ruth if key == "wed" else ana))
    for name, key, n in (("wedding", "wed", 3), ("Christmas", "xmas", 2), ("beach", "plain", 5)):
        tag = con.execute("INSERT INTO tag(name) VALUES (?)", (name,)).lastrowid
        for i in range(n):
            con.execute("INSERT INTO image_tag(image_id, tag_id, source) VALUES (?, ?, 'user')", (photo(key, i), tag))
    con.commit()
    kin = family.immediate(family.load(con), ana)
    assert kin == [ana, grace, tom, ruth]
    found = memories.family_occasions(con, kin, TODAY)
    assert [(m["title"], m["sub"]) for m in found] == [
        (f"{plain:%B} {plain.day}, {plain.year}", "With Ana · 5 photos"),
        ("Ana Miller and Tom Miller's wedding", "July 4, 2020 · 5 photos"),
        ("Christmas 2018", "December 24, 2018 · 5 photos"),
        ("Ruth Miller's wedding", "June 16, 2012 · 5 photos")]
    assert all(m["href"].startswith("/tours/try?") and m["count"] == 5 for m in found)
    assert [m["why"] for m in found] == [
        "", "the family tree has Ana Miller and Tom Miller's wedding on these days, and Ana Miller is in 5 of the 5 "
            "photos", "2 of the 5 photos are tagged christmas",
        "3 of the 5 photos are tagged wedding, and Ruth Miller is in 5 of them"]
    # a death names a day only when its photos are of the funeral
    funeral = con.execute("INSERT INTO tag(name) VALUES ('funeral')").lastrowid
    for i in range(2):
        con.execute("INSERT INTO image_tag(image_id, tag_id, source) VALUES (?, ?, 'user')",
                    (photo("plain", i), funeral))
    con.commit()
    assert memories.family_occasions(con, kin, TODAY)[0]["title"] == "Remembering Grace Baker"
    assert memories.years_ago(con, TODAY, people=kin) == []  # none of them this week in other years
    assert memories.family_occasions(con, [grace], TODAY) == []  # nothing of her mother alone


def test_in_a_year_of_everyday_photos_only_the_busiest_days_are_events():
    start = date(2025, 1, 1)
    days = {start + timedelta(days=2 * k): [k] * 3 for k in range(40)}  # three photos every other day
    days |= {date(2025, 6, 14): [1] * 8, date(2025, 6, 15): [2] * 7,  # a weekend: one event
             date(2025, 7, 4): [3] * 5,  # a little busier than usual: not one
             date(2025, 12, 25): [4] * 9}
    assert [(r["first"], r["last"], len(r["ids"])) for r in memories.grouped(days)] == [
        (date(2025, 6, 14), date(2025, 6, 15), 15), (date(2025, 12, 25), date(2025, 12, 25), 9)]
    # a few days with photos: any of them, a day without allowed
    few = {date(2012, 6, 16): [1] * 3, date(2012, 6, 18): [2] * 2, date(2012, 9, 1): [3] * 2}
    assert [(r["first"], r["last"]) for r in memories.grouped(few)] == [(date(2012, 6, 16), date(2012, 6, 18))]


def test_each_year_on_the_timeline_has_its_events(dated, cfg, con):
    """The two latest years show their events at once; the others load theirs when pointed at. An event is a run of
    days with photos, named by the tags on them, or by its days."""
    tag = con.execute("INSERT INTO tag(name) VALUES ('wedding')").lastrowid
    for i in range(3):
        con.execute("INSERT INTO image_tag(image_id, tag_id, source) VALUES (?, ?, 'user')",
                    (dated[f"ago5_{i}.jpg"], tag))
    con.commit()
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get("/timeline").text
    years = re.findall(r'<details class="year-glance" data-glance( open)?>\s*'
                       r'<summary><a class="year" href="/timeline/(\d+)"', page)
    assert [y for _, y in years] == sorted((y for _, y in years), reverse=True) and len(years) > 3
    assert [bool(o) for o, _ in years] == [True, True] + [False] * (len(years) - 2)
    first, other = years[0][1], years[3][1]
    assert f'data-later="/timeline/{first}/events"' in page and f'data-on-open="/timeline/{other}/events"' in page
    events = client.get(f"/timeline/{FIVE_AGO.year}/events").text
    assert "<html" not in events and f"<b>A wedding, {FIVE_AGO.year}</b>" in events and "5 photos" in events
    href = re.search(r'class="event-card" href="([^"]+)"', events).group(1).replace("&amp;", "&")
    assert client.get(href).status_code == 200 and sha(con, dated["ago5_0.jpg"]) in client.get(href).text
    three_ago = memories._same_day(TODAY, TODAY.year - 3).year
    assert "No events in" in client.get(f"/timeline/{three_ago}/events").text  # two photos: not an event
    assert 'data-later="/timeline/' in client.get(f"/timeline/{FIVE_AGO.year}").text  # the year's own page too


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
    # a photo moved to Removed is counted as removed until the Removed folder is emptied
    gone = con.execute("SELECT id, size FROM file WHERE path = ?", (rel("Dated", "earlier.jpg"),)).fetchone()
    assert cleanup.remove_files(con, cfg, [gone["id"]], "a test", keep_last_copy=False).moved == 1
    disk = storage.disks(con, cfg)[0]
    assert (disk["photos"], disk["removed"]) == (files - gone["size"], gone["size"])
    status = TestClient(webapp.create_app(cfg), client=LAN).get("/status").text
    assert "Removed files" in status and f'<a href="/cleanup/removed"' in status
    assert storage.fmt_size(gone["size"]) in client.get("/cleanup/removed").text
    cleanup.empty_removed(con, cfg)
    assert storage.disks(con, cfg)[0]["removed"] == 0
    files -= gone["size"]
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
