"""Copies of the catalog (backup.py), putting one back, and the export that outlives LineageBox (export.py)."""
import dataclasses
import io
import json
import os
import shutil
import zipfile
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from lineagebox import accounts, backup, cli, db, documents, export, people, settings, tagging, worker
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests import family_fixture as fx
from tests.conftest import LAN, rel

APP = {"X-Requested-With": "lineagebox"}
ZOO, BLUE = rel("2012", "Zoo", "DSC_0001.jpg"), rel("2016", "IMG_20160811_182239.jpg")


def image_of(con, path):
    return con.execute("SELECT image_id FROM file WHERE path = ?", (path,)).fetchone()[0]


@pytest.fixture
def known(con, cfg):
    """The library read, with what only the family knows in it: a named face, a tag given and one taken off, a date
    set by hand, someone added to a photo, a document and what it records, a caption and a comment."""
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    zoo, blue = image_of(con, ZOO), image_of(con, BLUE)
    owen, ada = people.person_id(con, "Owen Miller"), people.person_id(con, "Ada Baker")
    con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_by)
                   VALUES (?, 0.1, 0.2, 0.3, 0.4, 0.9, 25, ?, ?, 'user')""", (zoo, fx.unit(1).tobytes(), owen))
    tagging.add(con, [zoo], "zoo trip")
    tagging.add(con, [blue], "beach")
    tagging.remove(con, [zoo], "beach")
    tagging.set_date(con, [blue], "summer 1987")
    tagging.add_person(con, [blue], "Ada Baker", ada)
    documents.save(con, blue, "certificate", "Ada's birth certificate")
    birth = con.execute("""INSERT INTO event(person_id, tag, date, sort_date, place)
                           VALUES (?, 'BIRT', '3 MAR 1950', '1950-03-03', 'Portland, Oregon')""", (ada,)).lastrowid
    documents.cite(con, blue, f"e:{birth}")
    documents.cite(con, blue, f"p:{ada}")
    con.execute("INSERT INTO comment(image_id, kind, text, at) VALUES (?, 'caption', 'Owen at the zoo', "
                "'2026-11-01T10:00:00')", (zoo,))
    con.execute("INSERT INTO comment(image_id, kind, text, at) VALUES (?, 'comment', 'I remember the giraffes', "
                "'2026-11-02T09:00:00')", (zoo,))
    con.commit()
    return {"zoo": zoo, "blue": blue, "owen": owen, "ada": ada}


def nightly(cfg):
    return cfg.data_dir / "backups" / "nightly"


def test_a_copy_is_whole_checked_and_needs_nothing_beside_it(known, con, cfg):
    state = backup.make(con, cfg)
    made = nightly(cfg) / state["last"]["file"]
    assert backup.NAME.fullmatch(made.name)
    assert [p.name for p in nightly(cfg).iterdir()] == [made.name]  # nothing temporary left beside it
    assert made.read_bytes()[18:20] == b"\x01\x01"  # an ordinary database, not WAL: it opens on its own, anywhere
    found = backup.check(made)
    assert found["version"] == backup.newest_version() == con.execute("PRAGMA user_version").fetchone()[0]
    assert found["named_faces"] == 1 and found["people"] == 2
    assert found["photos"] == con.execute("SELECT count(*) FROM image").fetchone()[0]
    assert state["kept"] == 1 and state["kept_bytes"] == made.stat().st_size == state["last"]["bytes"]
    assert "backup" not in settings.problems(con)
    line = con.execute("SELECT message FROM activity ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert line.startswith(f"backup: {made.name}") and "checked" in line


def test_a_copy_put_back_is_the_catalog(known, con, cfg, tmp_path):
    """The plan's gate: a copy restored onto a spare catalog, opened through the migrations, with everything in it."""
    made = nightly(cfg) / backup.make(con, cfg)["last"]["file"]
    spare = dataclasses.replace(cfg, data_dir=tmp_path / "spare")
    spare.data_dir.mkdir()
    shutil.copyfile(made, spare.db_path)  # stop, put the file in place, start
    restored = db.connect(spare)
    try:
        assert restored.execute("""SELECT p.name FROM face f JOIN person p ON p.id = f.person_id
                                   WHERE f.image_id = ? AND f.assigned_by = 'user'""",
                                (known["zoo"],)).fetchone()[0] == "Owen Miller"
        assert restored.execute("SELECT date_text, taken_at_source FROM image WHERE id = ?",
                                (known["blue"],)).fetchone()[:] == ("summer 1987", "user")
        assert [r[0] for r in restored.execute("SELECT text FROM comment ORDER BY at")] == [
            "Owen at the zoo", "I remember the giraffes"]
        assert restored.execute("SELECT count(*) FROM citation").fetchone()[0] == 2
        assert restored.execute("PRAGMA journal_mode").fetchone()[0] == "wal"  # the catalog's own way again
    finally:
        restored.close()


def test_a_copy_from_an_older_version_is_upgraded_when_put_back(cfg, tmp_path):
    old = dataclasses.replace(cfg, data_dir=tmp_path / "old")
    old.data_dir.mkdir()
    c = db.connect(old, migrate_schema=False)
    db.migrate(c, old, upto=21)  # a catalog of before captions and comments (022)
    c.execute("INSERT INTO person(name) VALUES ('Grace Miller')")
    c.commit()
    made = nightly(old) / backup.make(c, old)["last"]["file"]
    c.close()
    assert backup.check(made)["version"] == 21
    spare = dataclasses.replace(cfg, data_dir=tmp_path / "spare")
    spare.data_dir.mkdir()
    shutil.copyfile(made, spare.db_path)
    restored = db.connect(spare)
    try:
        assert restored.execute("PRAGMA user_version").fetchone()[0] == backup.newest_version()
        assert restored.execute("SELECT name FROM person").fetchone()[0] == "Grace Miller"
        assert restored.execute("SELECT count(*) FROM comment").fetchone()[0] == 0  # the newer tables are there
    finally:
        restored.close()
    assert list((spare.data_dir / "backups").glob("catalog-v21-*.db"))  # and kept as it was, before the upgrade


def test_a_copy_a_day_for_a_week_and_one_a_week_for_a_month(tmp_path):
    where = tmp_path / "copies"
    where.mkdir()
    newest = datetime(2026, 11, 7, 0, 30)  # a Saturday
    for days in range(60):
        (where / f"lineagebox-{newest - timedelta(days=days):%Y%m%d-%H%M%S}.db").write_bytes(b"x")
    (where / "lineagebox-20261107-120000.db").write_bytes(b"x")  # Back up now later that day: the day's newest
    (where / "notes.txt").write_text("the owner's own")
    (where / "lineagebox.db").write_bytes(b"not one of the copies")
    removed = backup.prune(where, daily=7, weekly=4)
    kept = [c.at for c in backup.copies(where)]
    assert kept == [datetime(2026, 11, 7, 12)] + [datetime(2026, 11, d, 0, 30) for d in (6, 5, 4, 3, 2, 1)] + [
        datetime(2026, 10, d, 0, 30) for d in (31, 25, 18, 11)]  # the newest of each week (Monday to Sunday) before
    assert len(removed) == 61 - len(kept)
    assert (where / "notes.txt").exists() and (where / "lineagebox.db").exists()  # only its own copies go
    backup.prune(where, daily=1, weekly=0, keep=where / "lineagebox-20261011-003000.db")
    assert [c.at for c in backup.copies(where)] == [datetime(2026, 11, 7, 12), datetime(2026, 10, 11, 0, 30)]


def test_a_copy_that_fails_costs_no_good_copy(known, con, cfg, monkeypatch):
    good = backup.make(con, cfg)["last"]["file"]

    def damaged(con, part):  # a disk that wrote it wrong
        part.write_bytes(b"SQLite format 3\x00" + os.urandom(8000))

    monkeypatch.setattr(backup, "_copy", damaged)
    with pytest.raises(backup.Failed, match="can't be read|damaged"):
        backup.make(con, cfg)
    assert [p.name for p in nightly(cfg).iterdir()] == [good]  # not named as a copy, and the good one kept
    state = settings.get(con, backup.KEY)
    assert state["last"]["file"] == good and state["failed"]["folder"] == str(nightly(cfg))
    message = settings.problems(con)["backup"]["message"]
    assert message.startswith("The copy of the catalog made at") and "failed" in message
    live = TestClient(webapp.create_app(cfg), client=LAN).get("/status/live").text
    assert "<b>Backup:</b> The copy of the catalog made at" in live and 'href="/settings#backup"' in live
    tomorrow = datetime.now() + timedelta(days=1)
    assert not backup.due(con, cfg)  # tried again after a few hours, not on every pass
    assert backup.due(con, cfg, tomorrow.replace(hour=12))

    monkeypatch.setattr(backup.shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(10**9, 10**9 - 5, 5))
    with pytest.raises(backup.Failed, match="isn't room"):
        backup.make(con, cfg)
    monkeypatch.undo()
    backup.make(con, cfg)
    assert "backup" not in settings.problems(con) and settings.get(con, backup.KEY)["failed"] is None


def test_the_worker_makes_one_a_day(known, con, cfg):
    quiet = dataclasses.replace(cfg, describe_enabled=False, faces_enabled=False, transcribe_enabled=False)
    worker.run_once(con, quiet)
    worker.run_once(con, quiet)
    lines = [r[0] for r in con.execute("SELECT message FROM activity WHERE message LIKE 'backup: %'")]
    assert len(lines) == 1 and len(backup.copies(nightly(cfg))) == 1
    state = settings.get(con, backup.KEY)
    state["last"]["at"] = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    settings.put(con, backup.KEY, state)
    worker.run_once(con, quiet)  # the first pass of the next day
    assert con.execute("SELECT count(*) FROM activity WHERE message LIKE 'backup: %'").fetchone()[0] == 2
    assert settings.get(con, backup.KEY)["last"]["at"][:10] == datetime.now().date().isoformat()
    assert not backup.due(con, dataclasses.replace(quiet, backup_enabled=False), datetime.now() + timedelta(days=1))


def test_status_says_when_there_has_been_no_copy_for_days(known, con, cfg):
    made = backup.make(con, cfg)["last"]["file"]
    client = TestClient(webapp.create_app(cfg), client=LAN)
    html = client.get("/status").text
    panel = html.split('<section class="panel" id="backup">', 1)[1].split("</section>", 1)[0]
    assert "Last good copy" in panel and made in panel and "1 copy" in panel
    assert "No copy of the catalog has been made" not in html
    state = settings.get(con, backup.KEY)
    state["last"]["at"] = (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")
    settings.put(con, backup.KEY, state)
    html = client.get("/status").text
    top = html.split('id="progress"', 1)[0]
    assert "<b>Backup:</b> No copy of the catalog has been made since" in top


def test_back_up_now_and_the_folder_it_goes_to(known, con, cfg, tmp_path):
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get("/settings").text
    assert '<section class="panel" id="backup">' in page and 'href="/download/export"' in page
    assert 'href="/settings#backup"' in page.split('<nav class="settings-nav"', 1)[1].split("</nav>", 1)[0]
    r = client.post("/api/backup", headers=APP)
    assert r.status_code == 200 and r.json()["reload"] and r.json()["message"].startswith("Backed up and checked")
    assert len(backup.copies(nightly(cfg))) == 1
    for bad, says in (("backups", "whole path"), (str(cfg.share_root / "lib" / "2012"), "outside the photo folders"),
                      (str(tmp_path / "nowhere"), "There is no folder")):
        r = client.post("/api/settings", json={"backup_folder": bad}, headers=APP)
        assert r.status_code == 400 and says in r.json()["detail"], bad
    assert client.post("/api/settings", json={"backup_keep_daily": 0}, headers=APP).status_code == 400
    other = tmp_path / "second disk"
    other.mkdir()
    r = client.post("/api/settings", json={"backup_folder": str(other), "backup_keep_daily": "3",
                                           "backup_keep_weekly": "2"}, headers=APP)
    assert r.status_code == 200
    now = settings.current(con, cfg)
    assert (now.backup_folder, now.backup_keep_daily, now.backup_keep_weekly) == (str(other.resolve()), 3, 2)
    assert backup.due(con, now)  # a folder just picked is tried at once, not tomorrow
    assert client.post("/api/backup", headers=APP).status_code == 200
    assert len(backup.copies(other)) == 1 and len(backup.copies(nightly(cfg))) == 1  # the old folder left as it was
    assert str(other.resolve()) in client.get("/settings").text


def test_the_export_holds_the_tree_and_what_the_family_said(known, con, cfg, tmp_path):
    con.execute("UPDATE folder SET place = 'San Diego Zoo, California' WHERE path = ?", (rel("2012", "Zoo"),))
    con.execute("""INSERT INTO place(name, lat, lon, found_as, source, looked_up, confirmed_at)
                   VALUES ('San Diego Zoo, California', 32.73, -117.15, 'San Diego Zoo', 'osm', '2026-11-01T10:00:00',
                           '2026-11-02T10:00:00')""")
    con.commit()
    path = tmp_path / "export.zip"
    counts = export.write(con, cfg, path)
    with zipfile.ZipFile(path) as z:
        assert sorted(n for n in z.namelist() if not n.startswith("xmp/")) == [
            "README.txt", "family-tree.ged", "photos.json"]  # xmp/: the photos' sidecars (tests/test_sidecars.py)
        tree = z.read("family-tree.ged").decode("utf-8-sig")
        data = json.loads(z.read("photos.json"))
        assert "docs/export.md" in z.read("README.txt").decode("utf-8")
    assert f"0 @I{known['owen']}@ INDI" in tree and "Owen" in tree
    assert (data["format"], data["format_version"], data["catalog_version"]) == (
        "lineagebox-export", 1, backup.newest_version())
    assert {p["id"]: (p["name"], p["gedcom"]) for p in data["people"]} == {
        known["owen"]: ("Owen Miller", f"@I{known['owen']}@"), known["ada"]: ("Ada Baker", f"@I{known['ada']}@")}
    assert [p["name"] for p in data["places"]] == ["San Diego Zoo, California"] and data["places"][0]["confirmed_at"]
    by_path = {f: p for p in data["photos"] for f in p["files"]}
    zoo, blue = by_path[ZOO], by_path[BLUE]
    assert zoo is by_path[rel("Owen", "Zoo", "DSC_0001.jpg")]  # one photo in two places: one record, both paths
    assert zoo["faces"] == [{"box": [0.1, 0.2, 0.3, 0.4], "person": known["owen"], "named_by": "hand"}]
    assert zoo["tags"] == ["zoo trip"] and zoo["tags_taken_off"] == ["beach"]
    assert zoo["caption"] == {"text": "Owen at the zoo", "at": "2026-11-01T10:00:00"}
    assert [c["text"] for c in zoo["comments"]] == ["I remember the giraffes"]
    assert zoo["date"] == {"at": "2012-06-03T10:00:00", "source": "exif"}
    assert zoo["place"] == "San Diego Zoo, California" and "place" not in blue
    assert blue["date"]["source"] == "user" and blue["date"]["as_said"] == "summer 1987"
    assert blue["date"]["from"].startswith("1987-06") and blue["date"]["to"].startswith("1987-08")
    assert blue["date"]["automatic"]["source"] == "filename"
    assert blue["people"] == [known["ada"]] and blue["tags"] == ["beach"]
    assert blue["document"] == {"kind": "certificate", "title": "Ada's birth certificate", "records": [
        {"event": {"tag": "BIRT", "date": "3 MAR 1950", "place": "Portland, Oregon", "person": known["ada"]}},
        {"person": known["ada"]}]}
    assert counts.pop("sidecars") == len([n for n in z.namelist() if n.startswith("xmp/")])
    assert counts.pop("stories") == 0  # recorded stories: tests/test_stories.py
    assert counts.pop("sources") == 0  # written sources: tests/test_life_story.py
    assert counts.pop("albums") == counts.pop("favourites") == 0  # albums and hearts: tests/test_albums.py
    assert counts == {"photos": len(data["photos"]), "people": 2, "named_faces": 1, "tags": 2, "captions": 1,
                      "comments": 1, "documents": 1}


def test_only_admins_download_the_export(known, con, cfg):
    client = TestClient(webapp.create_app(cfg), client=LAN)
    r = client.get("/download/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "lineagebox-export-" in r.headers["content-disposition"] and r.headers["cache-control"] == "private"
    assert "photos.json" in zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert not list(cfg.data_dir.glob(".export-*"))  # made beside the catalog, sent, then deleted

    fx.account(con, known["owen"], "owen@example.com", "admin")
    member = fx.account(con, known["ada"], "ada@example.com")
    settings.save(con, cfg, {"accounts_enabled": True})
    c = TestClient(webapp.create_app(cfg), client=LAN, follow_redirects=False)
    c.cookies.set(accounts.COOKIE, accounts.new_session(con, member["id"], 30))
    assert c.get("/download/export").status_code == 404
    before = len(backup.copies(nightly(cfg)))
    assert c.post("/api/backup", headers=APP).status_code in (403, 404)
    assert len(backup.copies(nightly(cfg))) == before


def test_the_command_line_backs_up_checks_and_exports(known, con, cfg, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli.config, "load", lambda path=None: cfg)
    cli.main(["backup"])
    assert "and checked it; 1 copies kept" in capsys.readouterr().out
    made = backup.copies(nightly(cfg))[0].path
    monkeypatch.setattr(cli.config, "load", None)  # checking a copy needs no config: the catalog may be what was lost
    cli.main(["backup", "--check", str(made)])
    out = capsys.readouterr().out
    assert "is whole" in out and "as this LineageBox" in out and "1 named faces" in out
    (tmp_path / "notes.db").write_bytes(b"not a catalog at all, but long enough to look at" * 20)
    with pytest.raises(SystemExit, match="can't be read as a catalog"):
        cli.main(["backup", "--check", str(tmp_path / "notes.db")])
    monkeypatch.setattr(cli.config, "load", lambda path=None: cfg)
    cli.main(["export", str(tmp_path)])
    assert (tmp_path / export.file_name()).is_file() and "1 named faces" in capsys.readouterr().out


class _Busy:
    """The catalog's connection as the copy uses it, with another connection writing after each step of the copy
    (the web app, while it is going): each write makes SQLite start the copy again from the first page."""

    def __init__(self, con, other, writes):
        self.con, self.other, self.writes, self.steps = con, other, writes, 0

    def commit(self):
        self.con.commit()

    def backup(self, dest, pages=-1, progress=None):
        def step(status, remaining, total):
            self.steps += 1
            if progress is not None:
                progress(status, remaining, total)
            if self.writes:
                self.writes -= 1
                self.other.execute("INSERT INTO person(name) VALUES ('Written meanwhile')")
                self.other.commit()
        return self.con.backup(dest, pages=pages, progress=step if progress else None)


def test_a_copy_started_again_on_every_step_is_made_in_one_go(known, con, cfg, monkeypatch, tmp_path):
    """A web app that writes after every step of the copy: rather than starting it again until the writes stop, the
    copy is made in one step after a few starts, and it is still one moment's catalog, whole."""
    monkeypatch.setattr(backup, "STEP", 1)  # a page at a time: many steps for a small catalog
    other = db.connect(cfg, migrate_schema=False)
    try:
        busy = _Busy(con, other, writes=200)
        part = tmp_path / "copy.part"
        backup._copy(busy, part)
        assert busy.steps <= backup.RESTARTS + 2  # the starts it waits for, then one step
        found = backup.check(part)
        assert found["people"] == con.execute("SELECT count(*) FROM person").fetchone()[0]
    finally:
        other.close()


def test_what_a_copy_that_is_no_catalog_says(tmp_path, monkeypatch, known, con, cfg):
    with pytest.raises(backup.Failed, match="There is no file"):
        backup.check(tmp_path / "gone.db")
    import sqlite3
    other = sqlite3.connect(tmp_path / "other.db")
    other.execute("CREATE TABLE notes(text)")
    other.execute("PRAGMA user_version = 3")
    other.commit()
    other.close()
    with pytest.raises(backup.Failed, match="not a LineageBox catalog"):
        backup.check(tmp_path / "other.db")

    def refused(con, part):  # a folder the user LineageBox runs as (PUID) can't write in
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(backup, "_copy", refused)
    r = TestClient(webapp.create_app(cfg), client=LAN).post("/api/backup", headers=APP)
    assert r.status_code == 409 and "PUID" in r.json()["detail"]
    assert backup.nightly(con, cfg) is None  # the worker's: noted for the Status page, and the pass goes on
    assert "PUID" in settings.problems(con)["backup"]["message"]


def test_the_export_writes_every_field_its_format_describes(known, con, cfg, tmp_path):
    """The fields of docs/export.md a photo or person has only now and then: files gone, a camera's clock put right,
    GPS and a place by hand, a video's face and the people a face isn't, a couple's event a document records, a
    folder on the map, files another tree linked, a transcript put right."""
    from lineagebox import places
    zoo, blue, owen, ada = known["zoo"], known["blue"], known["owen"], known["ada"]
    con.execute("UPDATE file SET missing = 1 WHERE path = ?", (rel("Owen", "Zoo", "DSC_0001.jpg"),))
    con.execute("UPDATE image SET camera_taken_at = '2012-06-03T09:00:00' WHERE id = ?", (zoo,))
    con.execute("UPDATE image SET gps_lat = 1.5, gps_lon = 2.5, media = 'video' WHERE id = ?", (blue,))
    places.place_photo(con, blue, 48.85, 2.35)
    places.place_folder(con, con.execute("SELECT id FROM folder WHERE path = ?", (rel("2012", "Zoo"),)).fetchone()[0],
                        32.7, -117.1)
    face = con.execute("SELECT id FROM face WHERE image_id = ?", (zoo,)).fetchone()[0]
    con.execute("INSERT INTO face_not_person(face_id, person_id) VALUES (?, ?)", (face, ada))
    con.execute("INSERT INTO video_frame(image_id, idx, at) VALUES (?, 2, 12.5)", (blue,))
    con.execute("""INSERT INTO face(image_id, frame, x1, y1, x2, y2, det_score, quality, embedding, person_id,
                                    assigned_by) VALUES (?, 2, 0.5, 0.5, 0.6, 0.6, 0.9, 25, ?, ?, 'user')""",
                (blue, fx.unit(2).tobytes(), ada))
    a, b = sorted((owen, ada))
    con.execute("INSERT INTO partner(a, b) VALUES (?, ?)", (a, b))
    wed = con.execute("INSERT INTO event(couple_a, couple_b, tag, date) VALUES (?, ?, 'MARR', '1975')",
                      (a, b)).lastrowid
    documents.cite(con, blue, f"e:{wed}")
    con.execute("INSERT INTO tree_media(person_id, file, title) VALUES (?, 'scans/owen.jpg', 'Owen at ten')", (owen,))
    con.execute("""INSERT INTO transcript(image_id, model, version, created_at, text, segments, raw, edits)
                   VALUES (?, 'small', 1, 'x', 'Happy birthday', '[]', '[]', '{"12.4": "Happy birthday, Owen!"}')""",
                (blue,))
    con.commit()
    export.write(con, cfg, tmp_path / "export.zip")
    with zipfile.ZipFile(tmp_path / "export.zip") as z:
        data = json.loads(z.read("photos.json"))
    by_path = {f: p for p in data["photos"] for f in p["files"]}
    z_rec, b_rec = by_path[ZOO], by_path[BLUE]
    assert z_rec["missing_files"] == [{"path": rel("Owen", "Zoo", "DSC_0001.jpg")}]
    assert rel("Owen", "Zoo", "DSC_0001.jpg") not in z_rec["files"]
    assert z_rec["date"]["camera_wrote"] == "2012-06-03T09:00:00"
    assert z_rec["faces"][0]["not_people"] == [ada]
    assert b_rec["gps"] == {"lat": 1.5, "lon": 2.5} and b_rec["placed"]["lat"] == 48.85
    assert {"box": [0.5, 0.5, 0.6, 0.6], "frame_seconds": 12.5, "person": ada, "named_by": "hand"} in b_rec["faces"]
    assert {"event": {"tag": "MARR", "date": "1975", "couple": [a, b]}} in b_rec["document"]["records"]
    assert b_rec["transcript_corrections"] == [{"at": 12.4, "text": "Happy birthday, Owen!"}]
    assert [(f["path"], f["lat"]) for f in data["placed_folders"]] == [(rel("2012", "Zoo"), 32.7)]
    owen_rec = next(p for p in data["people"] if p["id"] == owen)
    assert owen_rec["files_elsewhere"] == [{"file": "scans/owen.jpg", "title": "Owen at ten"}]
