"""Reorganizing the photo folders into year and month folders (reorganize.py): where each file goes, what goes with it,
what the catalog keeps (nothing read again, the old folders' meaning), putting it back, and the scan meanwhile."""
import os
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from lineagebox import access, catalog, cleanup, folders, reorganize, search
from lineagebox.ingest import STAGE_INGEST, ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests.conftest import LAN, rel, write_jpeg

BLUE = rel("2016", "IMG_20160811_182239.jpg")    # dated by its name: 2016-08
GREEN = rel("2012", "Zoo", "DSC_0001.jpg")        # by its camera: 2012-06, and a copy in Owen/Zoo


@pytest.fixture
def lib(con, cfg, library):
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    return con


def image_of(con, path):
    return con.execute("SELECT image_id FROM file WHERE path = ?", (path,)).fetchone()[0]


def paths(con, image_id):
    return sorted(r[0] for r in con.execute("SELECT path FROM file WHERE image_id = ? AND NOT missing", (image_id,)))


def files_on_disk(library):
    return sorted(p.relative_to(library.parent).as_posix() for p in library.rglob("*") if p.is_file())


def test_everything_goes_into_its_year_and_month(lib, cfg, library):
    """Each photo into <year>/<year>-<month> by its date, its name kept (a second of that name numbered), what can't be
    read and what isn't a photo to Other files at its old path; ignored folders stay, emptied ones go. The catalog
    follows: the same photos and files, nothing to read again, previews kept, nothing new for the next scan."""
    con = lib
    blue, green = image_of(con, BLUE), image_of(con, GREEN)
    png = image_of(con, rel("transparent.png"))
    png_month = catalog.image(con, png)["taken_at"][:7]
    files_before = con.execute("SELECT count(*) FROM file").fetchone()[0]
    p = reorganize.plan(con, cfg, by_type=False)
    assert p.media == 4 and p.in_place == 0 and p.others == 2 and p.renamed == 1 and not p.waiting
    assert sorted(p.tops) == sorted({"2012", "2016", png_month[:4]})
    reorganize.start(con, cfg, by_type=False, account_id=None)
    run = reorganize.latest(con)
    assert run["state"] == "done" and run["moved"] == 6 and run["planned"] == 6 and run["problems"] is None
    assert paths(con, blue) == [rel("2016", "2016-08", "IMG_20160811_182239.jpg")]
    assert paths(con, green) == [rel("2012", "2012-06", "DSC_0001 (2).jpg"), rel("2012", "2012-06", "DSC_0001.jpg")]
    assert paths(con, png) == [rel(png_month[:4], png_month, "transparent.png")]
    on_disk = files_on_disk(library)
    assert rel("Other files", "broken.jpg") in on_disk and rel("Other files", "notes.txt") in on_disk
    assert rel("Portraits", "Thumbs", "small.jpg") in on_disk  # an ignored folder stays as it is
    assert not (library / "Owen").exists() and not (library / "2012" / "Zoo").exists()
    assert con.execute("SELECT count(*) FROM file").fetchone()[0] == files_before
    assert con.execute("""SELECT f.path FROM file f JOIN image i ON i.id = f.image_id
                          WHERE i.error IS NOT NULL""").fetchone()[0] == rel("Other files", "broken.jpg")
    for image_id in (blue, green, png):
        img = catalog.image(con, image_id)
        assert img["stage"] & STAGE_INGEST and cfg.preview_path(img["sha256"]).is_file()
    # the next scan finds every file where the catalog has it
    s = scan(con, cfg)
    assert (s.new, s.changed, s.missing, s.errors) == (0, 0, 0, [])
    # the new folders are in the catalog's tree, and say nothing of their own (never asked about)
    month = con.execute("SELECT * FROM folder WHERE path = ?", (rel("2016", "2016-08"),)).fetchone()
    assert month["kind"] == "date" and month["year"] is None
    # once more: everything is where it belongs
    again = reorganize.plan(con, cfg, by_type=False)
    assert again.moves == [] and again.in_place == 4


def test_folders_by_kind(lib, cfg, library):
    """With by_type, Photos, Scans, Videos and Recordings at the top, each with its years and months. A scan is one from
    a scanner or named so; a Live Photo's clip goes with its still, not to Videos."""
    con = lib
    write_jpeg(library / "ScannedPhotos" / "img001.jpg", color="white", taken="1975:06:01 00:00:00")
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    blue, png = image_of(con, BLUE), image_of(con, rel("transparent.png"))
    scanned = image_of(con, rel("ScannedPhotos", "img001.jpg"))
    con.execute("UPDATE image SET media = 'video' WHERE id = ?", (png,))
    con.commit()
    reorganize.start(con, cfg, by_type=True, account_id=None)
    assert paths(con, blue) == [rel("Photos", "2016", "2016-08", "IMG_20160811_182239.jpg")]
    assert paths(con, scanned) == [rel("Scans", "1975", "1975-06", "img001.jpg")]
    assert paths(con, png)[0].startswith(rel("Videos") + "/")
    assert con.execute("SELECT kind FROM folder WHERE path = ?", (rel("Photos"),)).fetchone()[0] == "media_type"
    # turned off again: back out of the folders by kind
    reorganize.start(con, cfg, by_type=False, account_id=None)
    assert paths(con, blue) == [rel("2016", "2016-08", "IMG_20160811_182239.jpg")]
    assert not (library / "Photos").exists()


def test_what_belongs_to_a_photo_goes_with_it(lib, cfg, library):
    """Sidecars, Google Takeout's files (also cut short and numbered), a RAW's sidecar by the name without its
    extension: with their photo, and numbered with it when its name is taken in the new folder."""
    con = lib
    a, b = library / "A", library / "B"
    write_jpeg(a / "IMG_1.jpg", color="purple", taken="2019:05:04 10:00:00")
    write_jpeg(b / "IMG_1.jpg", color="orange", taken="2019:05:05 10:00:00")
    long_name = "PXL_20190506_101010123.PORTRAIT.ORIGINAL.jpg"  # 45 characters: Takeout cuts its sidecar's name
    write_jpeg(a / long_name, color="cyan", taken="2019:05:06 10:10:10")
    write_jpeg(a / "IMG_2(1).jpg", color="navy", taken="2019:05:07 10:00:00")
    for name in ("IMG_1.jpg.xmp", "IMG_1.xmp", "IMG_1.jpg.json", "IMG_1.AAE", "IMG_2.jpg(1).json",
                 (long_name + ".supplemental-metadata")[:46] + ".json", "Thumbs.db"):
        (a / name).write_text("x")
    (b / "IMG_1.jpg.xmp").write_text("b's")
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    reorganize.start(con, cfg, by_type=False, account_id=None)
    month = library / "2019" / "2019-05"
    names = set(os.listdir(month))
    assert {"IMG_1.jpg", "IMG_1 (2).jpg", "IMG_1.jpg.xmp", "IMG_1.xmp", "IMG_1.jpg.json", "IMG_1.AAE",
            "IMG_1 (2).jpg.xmp", "IMG_2(1).jpg", "IMG_2.jpg(1).json", long_name,
            (long_name + ".supplemental-metadata")[:46] + ".json"} <= names
    # each sidecar is the one of the photo it is beside now: B's went with B's photo, numbered with it
    b_photo = paths(con, image_of(con, rel("2019", "2019-05", "IMG_1 (2).jpg")))[0]
    b_side = (library.parent / (b_photo + ".xmp")).read_text()
    a_side = (month / "IMG_1.jpg.xmp").read_text()
    assert {a_side, b_side} == {"x", "b's"}
    assert (library / "Other files" / "A" / "Thumbs.db").is_file()
    assert not a.exists() and not b.exists()


def test_what_the_old_folders_said_stays(lib, cfg, library):
    """A folder's meaning (its date, an event, a place) still goes for the photos moved out of it: their dates, search
    by its words, and a photo dated by a folder that names only the year goes in the year's folder."""
    con = lib
    write_jpeg(library / "Summer at the lake" / "lake.jpg", color="teal")  # no date of its own: its file's time
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    lake = image_of(con, rel("Summer at the lake", "lake.jpg"))
    folder = con.execute("SELECT id FROM folder WHERE path = ?", (rel("Summer at the lake"),)).fetchone()[0]
    folders.set_meaning(con, folder, place="Crater Lake", event="Lake trip", people="", when="1998")
    folders.reflow(con, folder)
    zoo = con.execute("SELECT id FROM folder WHERE path = ?", (rel("2012", "Zoo"),)).fetchone()[0]
    folders.set_meaning(con, zoo, place="", event="Zoo day", people="", when="")
    folders.reflow(con, zoo)
    assert catalog.image(con, lake)["taken_at"].startswith("1998") and \
        catalog.image(con, lake)["taken_at_source"] == "folder"
    reorganize.start(con, cfg, by_type=False, account_id=None)
    assert paths(con, lake) == [rel("1998", "lake.jpg")]  # only the year is known
    ctx = folders.FolderIndex(con).context(lake)
    assert ctx.year == 1998 and "Lake trip" in ctx.events and any("Crater Lake" in p for p in ctx.places)
    assert ctx.folders == [rel("Summer at the lake")]
    folders.date_from_folders(con)  # dated by the folder it came from, never by the one named from its own date
    assert catalog.image(con, lake)["taken_at"].startswith("1998-01-01")
    search.index_new(con)
    con.execute("DELETE FROM search_fts")
    con.commit()
    search.index_new(con)
    assert image_of(con, rel("2012", "2012-06", "DSC_0001.jpg")) in {r["id"] for r in search.query(con, "Zoo day", 50)}
    assert lake in {r["id"] for r in search.query(con, "lake", 50)}


def test_hidden_and_shared_folders_still_hold_their_photos(lib, cfg, library):
    con = lib
    green = image_of(con, GREEN)
    con.execute("UPDATE folder SET hidden = 1 WHERE path = ?", (rel("Owen"),))
    con.commit()

    def hidden():
        sql, args = access.hidden(con)
        return {r[0] for r in con.execute(sql, args)}

    assert green in hidden()
    reorganize.start(con, cfg, by_type=False, account_id=None)
    assert all("/Owen/" not in p for p in paths(con, green)) and green in hidden()
    assert access.folders_for(con, {green}, None) >= {
        con.execute("SELECT id FROM folder WHERE path = ?", (rel("Owen", "Zoo"),)).fetchone()[0]}


def test_a_folder_with_new_photos_waits(lib, cfg, library):
    """A photo the scan hasn't seen yet, or the worker hasn't read: its folder stays as it is, all of it, for now."""
    con = lib
    green = image_of(con, GREEN)
    write_jpeg(library / "2016" / "new.jpg", color="pink")
    p = reorganize.plan(con, cfg, by_type=False)
    assert p.waiting == [rel("2016")]
    reorganize.start(con, cfg, by_type=False, account_id=None)
    assert (library / "2016" / "IMG_20160811_182239.jpg").is_file() and (library / "2016" / "new.jpg").is_file()
    assert paths(con, green)[0].startswith(rel("2012", "2012-06"))


def test_putting_it_back(lib, cfg, library):
    """Undo moves every file back where it was, with the catalog; the folders it made go; the scan finds no change."""
    con = lib
    before_disk = files_on_disk(library)
    before = sorted(tuple(r) for r in con.execute("SELECT id, path, folder_id, from_folder_id FROM file"))
    reorganize.start(con, cfg, by_type=True, account_id=None)
    assert files_on_disk(library) != before_disk
    reorganize.undo(con, cfg)
    assert reorganize.latest(con)["state"] == "undone"
    assert files_on_disk(library) == before_disk
    assert sorted(tuple(r) for r in con.execute("SELECT id, path, folder_id, from_folder_id FROM file")) == before
    assert not (library / "Photos").exists() and not (library / "Other files").exists()
    s = scan(con, cfg)
    assert (s.new, s.changed, s.missing) == (0, 0, 0)
    with pytest.raises(reorganize.Problem):
        reorganize.undo(con, cfg)  # nothing more to put back


def test_a_file_moved_since_stays_where_it_is(lib, cfg, library):
    con = lib
    blue = image_of(con, BLUE)
    reorganize.start(con, cfg, by_type=False, account_id=None)
    now = paths(con, blue)[0]
    (library.parent / now).rename(library / "elsewhere.jpg")
    con.execute("UPDATE file SET path = ? WHERE path = ?", (rel("elsewhere.jpg"), now))
    con.commit()
    reorganize.undo(con, cfg)
    assert paths(con, blue) == [rel("elsewhere.jpg")] and (library / "elsewhere.jpg").is_file()
    assert (library / "2012" / "Zoo" / "DSC_0001.jpg").is_file()  # the rest went back
    assert "moved or removed since" in reorganize.latest(con)["problems"]


def test_putting_back_that_stopped_at_once_can_be_tried_again(lib, cfg, library, monkeypatch):
    """Putting back that stopped before a single file went back (the disk away just then) is still to be done: the
    same reorganization stays the one to put back, with the files it moved."""
    con = lib
    before_disk = files_on_disk(library)
    reorganize.start(con, cfg, by_type=False, account_id=None)
    run = reorganize.latest(con)

    def away(src, dst, like):
        raise RuntimeError("the disk went away")

    monkeypatch.setattr(cleanup, "move_file", away)
    with pytest.raises(RuntimeError):
        reorganize.undo(con, cfg)
    monkeypatch.undo()
    assert reorganize.latest(con)["state"] == "stopped"
    undoable = reorganize.undoable(con)
    assert undoable["id"] == run["id"] and undoable["moves"] == run["moved"]
    reorganize.undo(con, cfg)
    assert reorganize.latest(con)["state"] == "undone" and files_on_disk(library) == before_disk


def test_files_moved_but_not_written_down_go_back(lib, cfg, library):
    """A run cut short between moving files and writing that down (the web app stopped): each move is recorded
    before it is made, so putting it back finds those files on the disk too, though the catalog has them where they
    were."""
    con = lib
    blue = image_of(con, BLUE)
    before_disk = files_on_disk(library)
    reorganize.start(con, cfg, by_type=False, account_id=None)
    con.execute("UPDATE file SET path = ? WHERE image_id = ?", (BLUE, blue))  # as if never written down
    con.execute("UPDATE reorganize_run SET state = 'stopped'")
    con.commit()
    assert not (library / "2016" / "IMG_20160811_182239.jpg").exists()
    reorganize.undo(con, cfg)
    assert files_on_disk(library) == before_disk and paths(con, blue) == [BLUE]
    assert reorganize.latest(con)["problems"] is None


def test_a_long_look_through_the_folders_gives_signs_of_life(lib, cfg, library, monkeypatch):
    """Looking through a big share before moving anything takes minutes: the run says it is alive meanwhile, so the
    scan doesn't take it for one that stopped."""
    con = lib
    looked = []
    reorganize.plan(con, cfg, by_type=False, beat=lambda: looked.append(1))
    assert len(looked) >= 4  # each folder looked through
    monkeypatch.setattr(reorganize, "BEAT", 0)
    beats = []
    real = reorganize._beater

    def counting(c, run_id):
        beat = real(c, run_id)
        return lambda: (beats.append(c.execute("SELECT beat_at FROM reorganize_run WHERE id = ?",
                                               (run_id,)).fetchone()[0]), beat())
    monkeypatch.setattr(reorganize, "_beater", counting)
    reorganize.start(con, cfg, by_type=False, account_id=None, remove_empty=True)
    assert beats and all(beats) and reorganize.latest(con)["state"] == "done"


def test_the_scan_waits_while_files_move(lib, cfg, library, monkeypatch):
    """While a reorganization moves files the scan leaves the folders alone; one that began before throws away what it
    found, so nothing is marked missing or found twice."""
    con = lib
    now = datetime.now().isoformat(timespec="seconds")
    con.execute("INSERT INTO reorganize_run(started_at, by_type, state, beat_at) VALUES (?, 0, 'moving', ?)",
                (now, now))
    con.commit()
    write_jpeg(library / "late.jpg", color="gray")
    s = scan(con, cfg)
    assert s.errors == [reorganize.BUSY] and s.new == 0
    assert con.execute("SELECT 1 FROM file WHERE path = ?", (rel("late.jpg"),)).fetchone() is None
    # a run whose web app went away (no sign of life for long) holds nothing up
    con.execute("UPDATE reorganize_run SET beat_at = '2000-01-01T00:00:00'")
    con.commit()
    assert not reorganize.busy(con)
    # one starting while the scan looked through the folders: what it found is thrown away
    marks = iter([(1, "stopped"), (2, "moving")])
    monkeypatch.setattr(reorganize, "mark", lambda c: next(marks))
    s = scan(con, cfg)
    assert s.errors == [reorganize.MOVED]
    assert con.execute("SELECT 1 FROM file WHERE path = ?", (rel("late.jpg"),)).fetchone() is None


def test_the_page(lib, cfg, library):
    """Settings → Reorganize folders: the preview moves nothing; Reorganize now moves the files beside the request;
    the page follows it, then offers to put them back."""
    con = lib
    client = TestClient(webapp.create_app(cfg), client=LAN)
    assert "Reorganize folders" in client.get("/settings").text
    html = client.get("/settings/reorganize").text
    assert "See where everything goes" in html and "/api/reorganize/start" not in html
    before = files_on_disk(library)
    html = client.get("/settings/reorganize?preview=1&by_type=1").text
    assert "/api/reorganize/start" in html and "Photos/2016/2016-08/IMG_20160811_182239.jpg" in html
    assert files_on_disk(library) == before
    headers = {"X-Requested-With": "lineagebox"}
    r = client.post("/api/reorganize/start", json={"by_type": True}, headers=headers)
    assert r.status_code == 200 and r.json()["go"] == "/settings/reorganize"
    for _ in range(100):
        if reorganize.latest(con)["state"] != "moving":
            break
        time.sleep(0.05)
    assert reorganize.latest(con)["state"] == "done"
    assert (library / "Photos" / "2016" / "2016-08" / "IMG_20160811_182239.jpg").is_file()
    assert client.get("/settings/reorganize/progress").text == "<span data-reload-page></span>"
    assert "Put back the" in client.get("/settings/reorganize").text
    r = client.post("/api/reorganize/undo", headers=headers)
    assert r.status_code == 200
    for _ in range(100):
        if reorganize.latest(con)["state"] != "undoing":
            break
        time.sleep(0.05)
    assert files_on_disk(library) == before
    r = client.post("/api/reorganize/undo", headers=headers)
    assert r.status_code == 409 and "no reorganization to undo" in r.json()["detail"]


def test_three_of_one_name_in_one_month(lib, cfg, library):
    """Each photo of a name taken in its new folder gets the next number, and so does the Takeout sidecar named
    otherwise (IMG_2.jpg(1).json for IMG_2(1).jpg); a file half-written long ago no longer holds its folder back."""
    con = lib
    for n, folder in enumerate(("C", "D", "E")):
        write_jpeg(library / folder / "IMG_2(1).jpg", color=("red", "lime", "gold")[n],
                   taken=f"2020:01:0{n + 1} 10:00:00")
        (library / folder / "IMG_2.jpg(1).json").write_text(folder)
    left = library / "E" / ".clip.mp4.part"
    left.write_text("left behind")
    os.utime(left, (time.time() - 3 * 86400,) * 2)
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    reorganize.start(con, cfg, by_type=False, account_id=None)
    month = library / "2020" / "2020-01"
    assert {"IMG_2(1).jpg", "IMG_2(1) (2).jpg", "IMG_2(1) (3).jpg", "IMG_2.jpg(1).json", "IMG_2.jpg(1) (2).json",
            "IMG_2.jpg(1) (3).json"} <= set(os.listdir(month))
    assert [(month / f"IMG_2.jpg(1){s}.json").read_text() for s in ("", " (2)", " (3)")] == ["C", "D", "E"]
    assert (library / "Other files" / "E" / ".clip.mp4.part").is_file()
    assert con.execute("SELECT kind FROM folder WHERE path = ?", (rel("Other files", "E"),)).fetchone()[0] == "misc"


def test_empty_folders_and_junk(lib, cfg, library):
    """Asked to, empty folders go (also those empty before), and junk is deleted rather than kept in Other files: in
    folders it walks, not in ignored ones. Tidying again with nothing to move leaves the reorganization that moved
    files the one to put back."""
    con = lib
    blue = image_of(con, BLUE)
    (library / "Empty" / "Deeper").mkdir(parents=True)
    for name in ("Thumbs.db", ".DS_Store", "._IMG_20160811_182239.jpg"):
        (library / "2016" / name).write_text("x")
    (library / "pics" / ".thumbnails").mkdir(parents=True)
    (library / "pics" / ".thumbnails" / "1321024508887.jpg").write_bytes(b"x")
    (library / "Portraits" / "Thumbs" / "Thumbs.db").write_text("x")  # in an ignored folder: left as it is
    scan(con, cfg)  # notes the cache's file too (an ignored folder's), and ._IMG...jpg as a photo to read
    without = reorganize.plan(con, cfg, by_type=False)
    assert without.junk_count == 0 and rel("2016") in without.waiting  # ._IMG...jpg looks like a new photo
    p = reorganize.plan(con, cfg, by_type=False, remove_junk=True)
    assert p.junk_count == 4 and p.junk_names[".thumbnails/"] == 1 and p.junk_names["Thumbs.db"] == 1
    assert p.others == 2 and not p.waiting  # notes.txt and broken.jpg still go to Other files
    reorganize.start(con, cfg, by_type=False, account_id=None, remove_empty=True, remove_junk=True)
    run = reorganize.latest(con)
    assert run["junk"] == 4 and run["emptied"] >= 4  # Empty/Deeper, Empty, pics, 2012/Zoo, Owen/Zoo, Owen
    for gone in ("Empty", "pics", "Owen", "2016/Thumbs.db", "2016/.DS_Store"):
        assert not (library / gone).exists(), gone
    assert (library / "Portraits" / "Thumbs" / "Thumbs.db").is_file()
    assert sorted(os.listdir(library / "Other files")) == ["broken.jpg", "notes.txt"]
    # tidying again: nothing moves, the empty folder goes, and Put back still means the reorganization before
    (library / "Again").mkdir()
    reorganize.start(con, cfg, by_type=False, account_id=None, remove_empty=True)
    again = reorganize.latest(con)
    assert again["moved"] == 0 and again["emptied"] == 1 and not (library / "Again").exists()
    assert reorganize.undoable(con)["id"] == run["id"]
    reorganize.undo(con, cfg)
    assert paths(con, blue) == [BLUE] and reorganize.undoable(con) is None


def test_junk_an_earlier_run_kept_in_other_files(lib, cfg, library):
    """A reorganization without Delete junk kept Thumbs.db and the like in Other files; one with it deletes them
    there too, and the folders of Other files left empty go."""
    con = lib
    (library / "2016" / "Thumbs.db").write_text("x")
    (library / "Owen" / "Zoo" / "desktop.ini").write_text("x")
    reorganize.start(con, cfg, by_type=False, account_id=None)
    assert (library / "Other files" / "2016" / "Thumbs.db").is_file()
    assert (library / "Other files" / "Owen" / "Zoo" / "desktop.ini").is_file()
    p = reorganize.plan(con, cfg, by_type=False, remove_junk=True)
    assert p.moves == [] and p.junk_count == 2
    reorganize.start(con, cfg, by_type=False, account_id=None, remove_empty=True, remove_junk=True)
    assert sorted(os.listdir(library / "Other files")) == ["broken.jpg", "notes.txt"]
    assert reorganize.latest(con)["junk"] == 2 and reorganize.undoable(con)["moves"] > 0
