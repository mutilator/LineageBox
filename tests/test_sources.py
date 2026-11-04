"""Photo sources that are mounts of their own (a NAS share or a folder of the host, in Docker), which can go offline;
a new catalog with no folder watched yet."""
import dataclasses
import os
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from lineagebox import cleanup, copies, mounts, settings, worker
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests.conftest import LAN, rel

ZOO_B = rel("Owen", "Zoo", "DSC_0001.jpg")  # the second copy of 2012/Zoo/DSC_0001.jpg


def file_row(con, path):
    return con.execute("SELECT * FROM file WHERE path = ?", (path,)).fetchone()


def test_a_watched_folder_that_lists_nothing_is_offline(con, cfg, library):
    """An unmounted drive or share looks like an empty folder: its photos must not all be marked missing."""
    scan(con, cfg)
    away = library.parent / "away"
    shutil.move(library, away)
    library.mkdir()  # the mount point, with nothing mounted on it
    s = scan(con, cfg)
    assert s.missing == 0 and "no photos or videos found" in s.errors[0]
    assert con.execute("SELECT count(*) FROM file WHERE missing").fetchone()[0] == 0
    library.rmdir()
    shutil.move(away, library)
    s = scan(con, cfg)
    assert (s.errors, s.missing, s.new) == ([], 0, 0)


def test_one_stray_file_on_an_unmounted_folder_does_not_make_the_rest_missing(con, cfg, library):
    """A file put on the empty mount point while the share was away (an upload, say) is not the share coming back."""
    for i in range(60):
        (library / "many" / f"{i}.jpg").parent.mkdir(exist_ok=True)
        shutil.copy(library / "2016" / "IMG_20160811_182239.jpg", library / "many" / f"{i}.jpg")
    scan(con, cfg)
    away = library.parent / "away"
    shutil.move(library, away)
    (library / "Family uploads").mkdir(parents=True)
    shutil.copy(away / "2016" / "IMG_20160811_182239.jpg", library / "Family uploads" / "new.jpg")
    s = scan(con, cfg)
    assert s.missing == 0 and "only 1 of the 66 photos" in s.errors[0]  # 60 here, 6 from the sample library
    assert con.execute("SELECT count(*) FROM file WHERE missing").fetchone()[0] == 0


def test_the_removed_folder_of_a_watched_mount_is_inside_it(con, cfg, library, monkeypatch):
    """A file can't be renamed onto another mount, so the Removed folder of a watched folder that is a mount of its
    own goes inside it; the scan never walks it."""
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    assert cleanup.removed_folder(cfg, "lib") == "lib-Removed"
    monkeypatch.setattr(mounts, "is_mount", lambda p: Path(p) == cfg.share_root / "lib")
    assert cleanup.removed_folder(cfg, "lib") == "lib/LineageBox-Removed"

    r = cleanup.remove_files(con, cfg, [file_row(con, ZOO_B)["id"]], "test")
    assert (r.moved, r.errors) == (1, [])
    assert file_row(con, ZOO_B)["removed_path"] == "lib/LineageBox-Removed/Owen/Zoo/DSC_0001.jpg"
    assert (library / "LineageBox-Removed" / "Owen" / "Zoo" / "DSC_0001.jpg").is_file()
    s = scan(con, cfg)
    assert s.new == 0 and file_row(con, ZOO_B)["missing"] == 1
    assert con.execute("SELECT count(*) FROM folder WHERE path LIKE '%Removed%'").fetchone()[0] == 0

    assert cleanup.restore(con, cfg, [file_row(con, ZOO_B)["id"]]).moved == 1
    assert (cfg.share_root / ZOO_B).is_file() and file_row(con, ZOO_B)["missing"] == 0
    cleanup.remove_files(con, cfg, [file_row(con, ZOO_B)["id"]], "test")
    assert cleanup.empty_removed(con, cfg).moved == 1
    assert list((library / "LineageBox-Removed").iterdir()) == []  # emptied, but the Removed folder itself stays


def test_copies_are_not_marked_gone_while_their_source_is_offline(con, cfg, library):
    folder = con.execute("INSERT INTO folder(path, name) VALUES ('lib/videos', 'videos')").lastrowid
    image = con.execute("""INSERT INTO image(sha256, format, media, playable)
                           VALUES ('ab', 'mts', 'video', 'copy')""").lastrowid
    con.execute("INSERT INTO file(path, folder_id, image_id, size, mtime, last_seen) VALUES (?, ?, ?, 1, 1, '')",
                (rel("videos", "a.mts"), folder, image))
    con.commit()
    assert copies.check(con, cfg) == 0  # neither the video nor its copy is there: the source is offline
    (library / "videos").mkdir()
    (library / "videos" / "a.mts").write_bytes(b"video")
    assert copies.check(con, cfg) == 1  # the video is back but its copy isn't: one is needed again
    assert con.execute("SELECT playable FROM image WHERE id = ?", (image,)).fetchone()[0] == "needed"


def test_sources_and_their_network_paths_on_the_settings_page(con, cfg, tmp_path, monkeypatch):
    root = tmp_path / "photos"
    for d in ("local/Pictures", "nas", "readonly/x", "plain"):
        (root / d).mkdir(parents=True)
    several = dataclasses.replace(cfg, share_root=root, share_path="/photos",
                                  network_paths=(("local", "\\\\mynas\\photo"), ("nas", "\\\\otherpc\\pics")))
    monkeypatch.setattr(mounts, "is_mount", lambda p: Path(p).name in ("local", "nas", "readonly"))
    access = os.access
    monkeypatch.setattr(settings.os, "access", lambda p, mode: Path(p).name != "readonly" and access(p, mode))
    assert settings.sources(several) == [("local", ""), ("nas", "empty: is its disk or share connected?"),
                                         ("readonly", "read-only: cleanup and 720p copies cannot write here")]
    # a folder pasted as its PC finds it
    assert settings.folder_path(several, "\\\\MyNAS\\photo\\Pictures") == "local/Pictures"
    assert settings.folder_path(several, "/photos/plain") == "plain"
    page = TestClient(webapp.create_app(several), client=LAN).get("/settings").text
    assert "\\\\otherpc\\pics" in page and "is its disk or share connected" in page


def test_whose_photos_goes_into_reading_folder_names(con, cfg):
    from lineagebox.folders import parse_folders

    class Folders:
        def chat_json(self, system, user, schema, temperature=0.1):
            self.system = system
            return {"folders": [{"id": 1, "kind": "place", "place": "Cedar Point"}]}, None

    con.execute("INSERT INTO folder(path, name) VALUES ('lib/CedarPoint', 'CedarPoint')")
    saved = settings.save(con, cfg, {"library_about": "  a family in Oregon,\n USA "})
    assert saved.library_about == "a family in Oregon, USA"
    ollama = Folders()
    assert parse_folders(con, ollama, about=saved.library_about) == (1, 0)
    assert "family photo library (a family in Oregon, USA)." in ollama.system
    con.execute("INSERT INTO folder(path, name) VALUES ('lib/Zoo', 'Zoo')")
    parse_folders(con, ollama)
    assert "family photo library." in ollama.system  # nothing said about whose


def test_a_new_catalog_waits_for_a_folder_to_watch(con, cfg):
    fresh = dataclasses.replace(cfg, folders=())
    assert worker.run_once(con, fresh).waiting_for == "folders"
    c = TestClient(webapp.create_app(fresh), client=LAN)
    assert "Pick a folder to watch" in c.get("/").text
    assert "None yet" in c.get("/settings").text and "No folders are watched yet" in c.get("/status/live").text
