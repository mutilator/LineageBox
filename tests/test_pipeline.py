import os
from dataclasses import replace

from lineagebox.ingest import ingest
from lineagebox.scan import scan
from tests.conftest import rel, write_jpeg


def test_scan_records_folders_and_image_files(con, cfg):
    s = scan(con, cfg)
    assert not s.errors
    assert s.files == 6 and s.new == 6  # 4 jpg + png + broken.jpg; notes.txt is not an image
    folders = {r["path"]: r["ignored"] for r in con.execute("SELECT path, ignored FROM folder")}
    assert folders[rel("Portraits", "Thumbs")] == 1
    assert folders[rel("Portraits")] == 0
    assert folders["lib"] == 0
    assert folders[""] == 0


def test_rescan_is_a_no_op(con, cfg):
    scan(con, cfg)
    s = scan(con, cfg)
    assert (s.new, s.changed, s.missing) == (0, 0, 0)


def test_ingest_dedupes_and_extracts_metadata(con, cfg):
    scan(con, cfg)
    s = ingest(con, cfg)
    assert s.pending == 5  # the file under Thumbs is skipped
    assert (s.new_images, s.duplicates, s.decode_errors, s.read_errors) == (4, 1, 1, 0)

    zoo = con.execute("""SELECT i.* FROM image i JOIN file f ON f.image_id = i.id
                         WHERE f.path = ?""", (rel("2012", "Zoo", "DSC_0001.jpg"),)).fetchone()
    assert (zoo["width"], zoo["height"]) == (30, 60)
    assert (zoo["taken_at"], zoo["taken_at_source"], zoo["camera"]) == ("2012-06-03T10:00:00", "exif", "NIKON D7000")
    assert con.execute("SELECT count(DISTINCT image_id) FROM file WHERE path LIKE '%DSC_0001.jpg'").fetchone()[0] == 1

    img = con.execute("""SELECT i.taken_at, i.taken_at_source FROM image i JOIN file f ON f.image_id = i.id
                         WHERE f.path LIKE '%IMG_20160811_182239.jpg'""").fetchone()
    assert tuple(img) == ("2016-08-11T18:22:39", "filename")
    png = con.execute("SELECT * FROM image WHERE format = 'png'").fetchone()
    assert png["taken_at_source"] == "mtime"
    broken = con.execute("SELECT * FROM image WHERE error IS NOT NULL").fetchone()
    assert broken["format"] == "jpeg" and broken["width"] is None

    for r in con.execute("SELECT sha256 FROM image WHERE error IS NULL"):
        assert cfg.preview_path(r["sha256"]).is_file()
        assert cfg.thumb_path(r["sha256"]).is_file()
    assert ingest(con, cfg).pending == 0


def test_retry_errors_rereads_undecodable_files(con, cfg, library):
    scan(con, cfg)
    ingest(con, cfg)
    assert ingest(con, cfg).pending == 0
    write_jpeg(library / "broken.jpg", color="pink")  # no re-scan: --retry-errors alone makes ingest read it again
    s = ingest(con, cfg, retry_errors=True)
    assert (s.pending, s.new_images, s.decode_errors) == (1, 1, 0)
    assert con.execute("SELECT count(*) FROM image WHERE error IS NOT NULL").fetchone()[0] == 0


def test_many_copies_in_flight_decode_once(con, cfg, library):
    original = write_jpeg(library / "burst" / "copy00.jpg", color="orange")
    for i in range(1, 16):
        (library / "burst" / f"copy{i:02d}.jpg").write_bytes(original.read_bytes())
    scan(con, cfg)
    s = ingest(con, replace(cfg, ingest_threads=6))
    assert s.new_images == 5 and s.duplicates == 16  # 4 from the base library + 1 burst; 15 burst copies + 1
    linked = con.execute("""SELECT count(*), count(DISTINCT image_id) FROM file
                            WHERE path LIKE 'lib/burst%' AND image_id IS NOT NULL""").fetchone()
    assert tuple(linked) == (16, 1)


def test_changed_and_deleted_files(con, cfg, library):
    scan(con, cfg)
    ingest(con, cfg)
    changed = library / "2016" / "IMG_20160811_182239.jpg"
    write_jpeg(changed, size=(70, 30), color="purple")
    os.utime(changed, (1_600_000_000, 1_600_000_000))
    os.remove(library / "Owen" / "Zoo" / "DSC_0001.jpg")

    s = scan(con, cfg)
    assert (s.new, s.changed, s.missing) == (0, 1, 1)
    assert con.execute("SELECT image_id FROM file WHERE path LIKE '%IMG_2016%'").fetchone()[0] is None
    s = ingest(con, cfg)
    assert (s.pending, s.new_images) == (1, 1)


def test_ingest_can_keep_to_a_folder(con, cfg):
    scan(con, cfg)
    s = ingest(con, cfg, folder=rel("2016"))
    assert (s.pending, s.new_images, s.decode_errors) == (1, 1, 0)  # only that folder
    img = con.execute("SELECT i.* FROM image i JOIN file f ON f.image_id = i.id WHERE f.path LIKE '%IMG_2016%'").fetchone()
    assert img["error"] is None and img["taken_at_source"] == "filename"
    assert ingest(con, cfg).pending == 4  # the rest of the library is still to read
