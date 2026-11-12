"""Watched folders, the Settings page, the describing schedule and the worker's passes."""
import dataclasses
import re
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from lineagebox import catalog, describe, progress, settings, worker
from lineagebox.faces import FaceStats
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests.conftest import LAN, rel, write_jpeg
from tests.test_recognition import FakeOllama
from tests.test_worker import OnlineOllama

APP = {"X-Requested-With": "lineagebox"}


def visible_files(con):
    return {r[0] for r in con.execute("SELECT path FROM file WHERE NOT missing")}


def test_watch_another_folder_and_stop(con, cfg):
    write_jpeg(cfg.share_root / "Scans" / "1998" / "scan1.jpg", color="purple")
    scan(con, cfg)
    assert "Scans/1998/scan1.jpg" not in visible_files(con)

    now = settings.watch(con, cfg, str(cfg.share_root / "Scans"))  # a pasted full path works too
    assert now.folders == ("lib", "Scans") and settings.current(con, cfg).folders == ("lib", "Scans")
    scan(con, now)
    ingest(con, now, show_progress=False)
    assert {"Scans/1998/scan1.jpg", rel("2012", "Zoo", "DSC_0001.jpg")} <= visible_files(con)
    tree = catalog.folder_tree(con)
    root = tree[catalog.root_folder_id(con)]
    assert sorted(f.name for f in root.children) == ["Scans", "lib"]

    now = settings.unwatch(con, cfg, "Scans")
    assert scan(con, now).missing == 1  # its photos leave the catalog's pages...
    assert "Scans/1998/scan1.jpg" not in visible_files(con)
    scan(con, settings.watch(con, cfg, "Scans"))
    assert "Scans/1998/scan1.jpg" in visible_files(con)  # ...and come back, already read
    assert con.execute("SELECT image_id FROM file WHERE path = 'Scans/1998/scan1.jpg'").fetchone()[0] is not None


def test_watching_needs_a_real_folder_outside_the_watched_ones(con, cfg):
    (cfg.share_root / "lib-Removed").mkdir()
    for bad, says in [("Nowhere", "no folder"), ("lib/2012", "already watched"), ("lib-Removed", "-Removed"),
                      ("", "not all of them"), ("../etc", ". or ..")]:
        with pytest.raises(settings.Invalid, match=says):
            settings.watch(con, cfg, bad)
    with pytest.raises(settings.Invalid, match="At least one"):
        settings.unwatch(con, cfg, "lib")


def test_watching_a_parent_folder_replaces_the_folders_in_it(con, cfg):
    write_jpeg(cfg.share_root / "Family" / "Pics" / "a.jpg")
    settings.watch(con, cfg, "Family/Pics")
    assert settings.watch(con, cfg, "Family").folders == ("lib", "Family")


def test_settings_are_checked_and_stored(con, cfg):
    for changes, says in [({"worker_interval_minutes": 2}, "from 5 to 1440"), ({"describe_from": "25:00"}, "22:00"),
                          ({"describe_schedule": "hours", "describe_days": []}, "at least one day"),
                          ({"ollama_url": "192.168.7.149"}, "looks like"), ({"llm_workers": "x"}, "whole number"),
                          ({"share_root": "/"}, "cannot be changed"),
                          ({"allowed_hosts": "*.com"}, "top-level domain"),  # every .com site could rebind to it
                          ({"trusted_proxies": "0.0.0.0/0"}, "far wider"), ({"trusted_proxies": "::/0"}, "far wider"),
                          ({"folders": ["C:/Windows"]}, "not where LineageBox can reach")]:
        with pytest.raises(settings.Invalid, match=says):
            settings.save(con, cfg, changes)
    # a proxy's own address with its network's prefix is stored as the network: uvicorn ignores the other form
    settings.save(con, cfg, {"trusted_proxies": "172.18.0.3/16\n10.0.0.7"})
    assert settings.current(con, cfg).trusted_proxies == ("172.18.0.0/16", "10.0.0.7")
    settings.save(con, cfg, {"describe_schedule": "hours", "describe_from": "22:00:00", "describe_until": "7:00",
                             "describe_days": ["0", "4"], "ignore_dirs": "Thumbs\n Previews \n\n", "llm_workers": "2"})
    now = settings.current(con, cfg)
    assert (now.describe_schedule, now.describe_from, now.describe_until, now.describe_days) == \
        ("hours", "22:00", "07:00", (0, 4))
    assert now.ignore_dirs == ("Thumbs", "Previews") and now.llm_workers == 2
    assert settings.schedule_text(now) == "22:00–07:00 on Mon, Fri"


def test_how_long_pointing_at_a_photo_takes(con, cfg):
    """Settings → Browsing: how long a photo is pointed at before it shows larger (app.js reads it from <body>)."""
    client = TestClient(webapp.create_app(cfg), client=LAN)
    assert settings.current(con, cfg).hover_preview_ms == 500
    assert 'data-hover-preview="500"' in client.get("/home").text and 'id="browsing"' in client.get("/settings").text
    for value, says in ((-1, "from 0 to 5000"), (6000, "from 0 to 5000"), ("soon", "whole number")):
        r = client.post("/api/settings", json={"hover_preview_ms": value}, headers=APP)
        assert r.status_code == 400 and says in r.json()["detail"], value
    assert client.post("/api/settings", json={"hover_preview_ms": "0"}, headers=APP).status_code == 200  # off
    assert settings.current(con, cfg).hover_preview_ms == 0
    client = TestClient(webapp.create_app(cfg), client=LAN)  # a new app: settings are looked up every 2 s
    assert 'data-hover-preview="0"' in client.get("/people").text


def test_describing_hours(cfg):
    monday = datetime(2026, 10, 26)  # a Monday
    night = dataclasses.replace(cfg, describe_schedule="hours", describe_from="22:00", describe_until="07:00",
                                describe_days=(0,))
    at = lambda days, h, m=0: monday + timedelta(days=days, hours=h, minutes=m)
    assert settings.may_describe(night, at(0, 23))
    assert settings.may_describe(night, at(1, 3))       # Monday's window runs into Tuesday morning
    assert not settings.may_describe(night, at(1, 7))
    assert not settings.may_describe(night, at(1, 23))  # no window starts on Tuesday
    assert not settings.may_describe(night, at(0, 12))
    assert settings.next_describe_start(night, at(1, 8)) == at(7, 22)
    day = dataclasses.replace(night, describe_from="09:00", describe_until="17:00", describe_days=tuple(range(7)))
    assert not settings.may_describe(day, at(2, 8, 59)) and settings.may_describe(day, at(2, 9))
    assert not settings.may_describe(day, at(2, 17))
    assert not settings.may_describe(dataclasses.replace(cfg, describe_schedule="paused"), at(0, 23))
    assert settings.may_describe(cfg, at(0, 12))


def test_new_photos_go_through_every_stage_on_the_next_pass(con, cfg, library, monkeypatch):
    monkeypatch.setattr(worker, "Ollama", OnlineOllama)
    monkeypatch.setattr(describe, "Ollama", OnlineOllama)
    faced = []
    monkeypatch.setattr(worker, "detect", lambda con, cfg, show_progress: faced.append(1) or FaceStats())
    worker.run_once(con, cfg)

    new = write_jpeg(library / "2012" / "Zoo" / "DSC_0099.jpg", color="olive", taken="2019:01:01 15:00:00")
    worker.run_once(con, cfg)
    image_id = con.execute("SELECT image_id FROM file WHERE path = ?", (rel("2012", "Zoo", new.name),)).fetchone()[0]
    assert image_id is not None
    assert con.execute("SELECT sharpness FROM image WHERE id = ?", (image_id,)).fetchone()[0] is not None
    assert con.execute("SELECT count(*) FROM analysis WHERE image_id = ?", (image_id,)).fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM search_fts WHERE rowid = ?", (image_id,)).fetchone()[0] == 1
    assert len(faced) == 2
    lines = [r[0] for r in con.execute("SELECT message FROM activity ORDER BY id")]
    assert any(l.startswith("scan: 1 new") for l in lines) and any(l.startswith("describe: 1 photos") for l in lines)
    assert settings.get(con, "worker")["stage"] is None


def test_descriptions_wait_for_their_hours(con, cfg, library, monkeypatch):
    monkeypatch.setattr(worker, "Ollama", OnlineOllama)
    monkeypatch.setattr(worker, "detect", lambda con, cfg, show_progress: FaceStats())
    settings.save(con, cfg, {"describe_schedule": "paused"})
    result = worker.run_once(con, settings.current(con, cfg))
    assert result.waiting_for == "schedule"
    assert con.execute("SELECT count(*) FROM image").fetchone()[0] > 0  # everything else ran
    assert con.execute("SELECT count(*) FROM analysis").fetchone()[0] == 0


def test_describing_stops_when_asked_and_newest_go_first(con, cfg, library, monkeypatch):
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    monkeypatch.setattr(describe, "Ollama", FakeOllama)
    asked = []
    stop = lambda: asked.append(1) or len(asked) > 1  # one photo, then stop
    s = describe.describe(con, dataclasses.replace(cfg, llm_workers=1), show_progress=False, stop=stop)
    assert s.stopped and s.done == 1 and s.pending > 1
    newest = con.execute("SELECT max(id) FROM image WHERE error IS NULL").fetchone()[0]
    assert con.execute("SELECT image_id FROM analysis").fetchone()[0] == newest


def test_stopper_notices_check_now(con, cfg):
    stop = worker.Stopper(con, cfg, budget_seconds=3600)
    assert not stop()
    stop.looked = 0  # as if TICK seconds had passed
    settings.put(con, "run_requested", (datetime.now() + timedelta(seconds=5)).isoformat(timespec="seconds"))
    assert stop() and stop.reason == "Check now was pressed"


def test_status_and_settings_pages(con, cfg, library):
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    write_jpeg(cfg.share_root / "Scans" / "x.jpg")
    client = TestClient(webapp.create_app(cfg), client=LAN)
    status = client.get("/status").text
    assert "<h2>Work in progress</h2>" in status and "Not responding" in status  # no worker in tests
    snap = progress.snapshot(con, cfg)
    assert snap["describe"]["left"] == snap["describe"]["total"] > 0
    assert "Photos and videos to describe (AI)" in client.get("/status/live").text

    page = client.get("/settings").text
    assert "Watched folders" in page and ">Scans</a>" in page
    assert client.post("/api/settings", json={"llm_workers": 99}, headers=APP).json()["detail"].startswith("Parallel")
    assert client.post("/api/settings", json={"llm_workers": 99}).status_code == 403  # not from the app's page
    assert client.post("/api/settings", json={"llm_workers": 2}, headers=APP).status_code == 200
    assert client.post("/api/folders/watch", json={"path": "Scans"}, headers=APP).status_code == 200
    assert settings.current(con, cfg).folders == ("lib", "Scans")
    assert client.post("/api/folders/watch", json={"path": "Nope"}, headers=APP).status_code == 400
    assert client.post("/api/worker/run", headers=APP).status_code == 200
    assert settings.get(con, "run_requested")
    assert client.get("/settings?browse=..").status_code == 200  # ignored, not followed


def test_the_settings_menu(con, cfg):
    """Settings, Family accounts and Codes share the menu at the side. Each of its Settings links names the first panel
    of one part of the page (app.js shows one part at a time), and every panel or heading other pages link to is still
    on the page, inside a part, so that the part holding it can be shown."""
    client = TestClient(webapp.create_app(cfg), client=LAN)
    pages = {path: client.get(path).text for path in ("/settings", "/accounts", "/settings/codes")}
    for path, html in pages.items():
        menu = html.split('<nav class="settings-nav"', 1)[1].split("</nav>", 1)[0]
        assert 'href="/settings#ollama"' in menu and 'href="/accounts"' in menu and 'href="/settings/codes"' in menu
        marked = re.findall(r'href="([^"]+)" aria-current="page"', menu)
        assert marked == ([] if path == "/settings" else [path]), path  # on Settings, app.js marks the part shown
    page = pages["/settings"].split('<dialog id="help"', 1)[0]
    parts = page.split(" data-part>")[1:]
    assert len(parts) > 5 and '<p class="meta"><a href="/accounts">' not in page  # the old links at the top
    firsts = [re.search(r'<section class="panel" id="([a-z]+)"', part)[1] for part in parts]
    menu = page.split('<nav class="settings-nav"', 1)[1].split("</nav>", 1)[0]
    assert re.findall(r'href="/settings#([a-z]+)"', menu) == firsts

    code = "".join(f.read_text(encoding="utf-8") for f in webapp.HERE.rglob("*.*") if f.suffix in (".html", ".py"))
    linked = set(re.findall(r"/settings#([a-z]+)", code))
    for service in settings.PROBLEM_SERVICES:  # the Status page links each service's problem to its panel
        settings.set_problem(con, service, "Something is wrong.")
    status = client.get("/status/live").text
    linked |= set(re.findall(r'href="/settings#([a-z]+)"', status)) - {"videos"}  # ffmpeg: installed, not set here
    assert {"add", "https", "schedule", "ollama", "whisper", "faces", "map", "tours", "cleanup", "updates"} <= linked
    in_parts = {i for part in parts for i in re.findall(r' id="([a-z]+)"', part)}
    assert linked <= in_parts, linked - in_parts


def test_reading_threads_change_mid_pass(con, cfg, library, monkeypatch):
    with pytest.raises(settings.Invalid, match="from 1 to 16"):
        settings.save(con, cfg, {"ingest_threads": 0})
    monkeypatch.setattr(worker, "Ollama", OnlineOllama)
    monkeypatch.setattr(describe, "Ollama", OnlineOllama)  # or describing would ask the real Ollama on this PC
    monkeypatch.setattr(worker, "detect", lambda con, cfg, show_progress: FaceStats())
    stop = worker.Stopper(con, cfg, budget_seconds=3600)
    settings.save(con, cfg, {"ingest_threads": "2"})  # saved while the pass is reading photos
    settings.put(con, "saved_at", (datetime.now() + timedelta(seconds=5)).isoformat(timespec="seconds"))
    result = worker.run_once(con, settings.current(con, cfg), stop)
    assert result.run_again and stop.reason == "the settings changed"
    assert con.execute("SELECT count(*) FROM analysis").fetchone()[0] == 0  # the rest waits for the next pass
    assert settings.current(con, cfg).ingest_threads == 2
    assert "2 at a time" in con.execute("SELECT message FROM activity ORDER BY id DESC LIMIT 1").fetchone()[0]
    worker.run_once(con, settings.current(con, cfg))  # the next pass reads the rest
    assert con.execute("""SELECT count(*) FROM file f JOIN folder d ON d.id = f.folder_id
                          WHERE f.image_id IS NULL AND NOT f.missing AND NOT d.ignored""").fetchone()[0] == 0


def test_the_catalogs_own_folder_is_never_watched_or_scanned(con, cfg, library):
    """With the catalog inside the photo folders (from source, say), its previews would become photos."""
    import dataclasses
    from lineagebox.scan import scan
    inside = dataclasses.replace(cfg, data_dir=library / "Catalog")
    (library / "Catalog" / "previews").mkdir(parents=True)
    photo = (library / "2016" / "IMG_20160811_182239.jpg").read_bytes()
    (library / "Catalog" / "previews" / "ab.jpg").write_bytes(photo)
    with pytest.raises(settings.Invalid, match="own catalog"):
        settings.watch(con, inside, f"{library.name}/Catalog")
    scan(con, inside)
    assert not con.execute("SELECT 1 FROM file WHERE path LIKE '%Catalog%'").fetchone()


def test_the_catalogs_folder_under_another_path_is_not_scanned_either(con, cfg, library, monkeypatch):
    """In Docker the catalog is /data, and with PHOTOS set to a folder that holds it, the same folder is in the photo
    folders too, under a path that says nothing: it is known by what it is (here the path check is taken away)."""
    from lineagebox import scan as scan_module
    inside = dataclasses.replace(cfg, data_dir=library / "docker" / "lineagebox" / "data")
    (inside.data_dir / "previews").mkdir(parents=True)
    photo = (library / "2016" / "IMG_20160811_182239.jpg").read_bytes()
    (inside.data_dir / "previews" / "ab.jpg").write_bytes(photo)
    (library / "docker" / "lineagebox" / "beside.jpg").write_bytes(photo)  # next to the catalog: a photo as any other
    monkeypatch.setattr(scan_module, "_own_folders", lambda cfg: set())
    scan(con, inside)
    assert not con.execute("SELECT 1 FROM file WHERE path LIKE '%/data/%'").fetchone()
    assert con.execute("SELECT 1 FROM file WHERE path LIKE '%beside.jpg'").fetchone()
