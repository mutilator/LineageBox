"""XMP sidecars (N4): what the family gave a photo, beside it for other programs, kept up to date by the worker, moved
with the photo by cleanup, carried by downloads and the export, and written into the file when an admin asks."""
import dataclasses
import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from lineagebox import cleanup, comments, export, places, sidecars, tools, writeback, xmp
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests.conftest import LAN, rel, write_jpeg

APP = {"X-Requested-With": "lineagebox"}


@pytest.fixture
def named(con, cfg, library):
    """A photo with a face named by hand, one recognised, a caption, a tag by hand and one by the AI."""
    path = write_jpeg(library / "Lake" / "IMG_0001.jpg", size=(400, 300), color=(30, 90, 160),
                      taken="2004:07:15 13:20:00")
    scan(con, cfg)
    ingest(con, cfg)
    image = con.execute("SELECT image_id FROM file WHERE path = ?", (rel("Lake", "IMG_0001.jpg"),)).fetchone()[0]
    ann = con.execute("INSERT INTO person(name) VALUES ('Ann Miller')").lastrowid
    bob = con.execute("INSERT INTO person(name) VALUES ('Bob Baker')").lastrowid
    for (x1, x2), who, how in (((0.1, 0.3), ann, "user"), ((0.6, 0.8), bob, "auto")):
        con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_by)
                       VALUES (?, ?, 0.2, ?, 0.5, 0.9, 25, ?, ?, ?)""",
                    (image, x1, x2, np.zeros(512, np.float32).tobytes(), who, how))
    comments.set_caption(con, image, "Grandma at the lake", None)
    for name, source in (("picnic", "user"), ("water", "ai")):
        tag = con.execute("INSERT INTO tag(name) VALUES (?)", (name,)).lastrowid
        con.execute("INSERT INTO image_tag(image_id, tag_id, source) VALUES (?, ?, ?)", (image, tag, source))
    con.commit()
    return {"image": image, "path": path, "ann": ann, "bob": bob}


def on(cfg):
    return dataclasses.replace(cfg, sidecars_enabled=True)


def test_what_people_made_goes_beside_the_photo(con, cfg, named):
    assert not sidecars.path_for(named["path"]).exists()
    stats = sidecars.run(con, on(cfg))
    side = sidecars.path_for(named["path"])
    assert side.name == "IMG_0001.jpg.xmp" and stats.written == 1
    found = xmp.parse(side.read_bytes())
    assert found.ours  # never taken back in as another app's
    assert [r[0] for r in found.regions] == ["Ann Miller"]  # the name given by hand, not recognition's guess
    assert found.regions[0][1:] == pytest.approx((0.1, 0.2, 0.3, 0.5), abs=1e-5)
    assert found.people == ["Ann Miller"] and found.caption == "Grandma at the lake"
    assert found.keywords == ["picnic"]  # the tag given by hand; the AI's stays in the catalog
    assert found.taken_at is None and found.gps is None  # the camera's date and no place: nothing to say

    # written again only when it says something new
    assert sidecars.run(con, on(cfg)).written == 0
    comments.set_caption(con, named["image"], "Grandma and Ann at the lake", None)
    places.place_photo(con, named["image"], 45.5, -122.6)
    con.execute("UPDATE image SET taken_at = '1987-07-16T00:00:00', taken_at_source = 'user', date_text = 'July 1987', "
                "date_from = '1987-07-01', date_to = '1987-07-31' WHERE id = ?", (named["image"],))
    con.commit()
    assert sidecars.run(con, on(cfg)).written == 1
    found = xmp.parse(side.read_bytes())
    assert found.caption == "Grandma and Ann at the lake" and found.gps == pytest.approx((45.5, -122.6))
    assert found.taken_at == "1987-07-16T00:00:00"
    assert b'photoshop:DateCreated="1987-07"' in side.read_bytes()  # as exactly as it is known

    # once it has nothing to say, LineageBox's sidecar goes
    con.execute("DELETE FROM comment WHERE image_id = ?", (named["image"],))
    con.execute("UPDATE face SET person_id = NULL, assigned_by = NULL WHERE image_id = ?", (named["image"],))
    con.execute("DELETE FROM image_tag WHERE image_id = ?", (named["image"],))
    con.execute("DELETE FROM image_place WHERE image_id = ?", (named["image"],))
    con.execute("UPDATE image SET taken_at_source = 'exif' WHERE id = ?", (named["image"],))
    con.commit()
    assert sidecars.run(con, on(cfg)).removed == 1 and not side.exists()
    assert con.execute("SELECT count(*) FROM sidecar").fetchone()[0] == 0


def test_off_writes_nothing_and_on_goes_through_every_photo(con, cfg, named):
    assert con.execute("SELECT count(*) FROM sidecar_stale").fetchone()[0] > 0  # the triggers noted the changes
    sidecars.run(con, cfg)
    assert not sidecars.path_for(named["path"]).exists()
    assert con.execute("SELECT count(*) FROM sidecar_stale").fetchone()[0] == 0  # forgotten while off...
    assert sidecars.run(con, on(cfg)).written == 1  # ...and every photo looked at once it is on


def test_another_programs_sidecar_is_left_as_it_is(con, cfg, named):
    side = sidecars.path_for(named["path"])
    side.write_text("<x:xmpmeta xmlns:x='adobe:ns:meta/'><!-- darktable's edits --></x:xmpmeta>")
    stats = sidecars.run(con, on(cfg))
    assert stats.others == 1 and stats.written == 0 and "darktable" in side.read_text()
    assert sidecars.summary(con) == {"written": 0, "others": 1, "waiting": 0}


def test_a_sidecar_written_as_root_is_the_photos_owners(con, cfg, named, monkeypatch):
    given = []
    monkeypatch.setattr(sidecars.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(sidecars.os, "chown", lambda p, uid, gid: given.append(Path(p).name), raising=False)
    real_fsync = sidecars.os.fsync
    monkeypatch.setattr(sidecars.os, "fsync", lambda fd: (given.append("on the disk"), real_fsync(fd))[1])
    sidecars.run(con, on(cfg))
    # on the disk before it takes the sidecar's name, so a power cut never leaves an empty one; then the owner's
    assert given == ["on the disk", ".lineagebox-new-IMG_0001.jpg.xmp.part"]


def test_a_sidecar_that_cant_be_written_is_tried_again_later(con, cfg, named, monkeypatch):
    def refused(*a, **k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(sidecars, "_write", refused)
    stats = sidecars.run(con, on(cfg))
    assert stats.failed == 1
    assert con.execute("SELECT stage FROM retry WHERE item_id = ?", (named["image"],)).fetchone()[0] == "sidecars"
    assert con.execute("SELECT 1 FROM sidecar_stale WHERE image_id = ?", (named["image"],)).fetchone()  # still to do
    assert sidecars.run(con, on(cfg)).failed == 0  # waiting: left out until its time comes


def test_cleanup_moves_the_sidecar_with_its_photo(con, cfg, library, named):
    sidecars.run(con, on(cfg))
    write_jpeg(library / "Copies" / "IMG_0001.jpg", size=(400, 300), color=(30, 90, 160), taken="2004:07:15 13:20:00")
    scan(con, cfg)
    ingest(con, cfg)
    file_id = con.execute("SELECT id FROM file WHERE path = ?", (rel("Lake", "IMG_0001.jpg"),)).fetchone()[0]
    moved = cleanup.remove_files(con, cfg, [file_id], "test", keep_last_copy=False)
    assert moved.moved == 1 and not moved.errors
    removed = cfg.share_root / con.execute("SELECT removed_path FROM file WHERE id = ?", (file_id,)).fetchone()[0]
    assert sidecars.path_for(removed).is_file() and not sidecars.path_for(named["path"]).exists()
    sidecars.run(con, on(cfg))  # a removed file's sidecar is left with it
    assert sidecars.path_for(removed).is_file()
    cleanup.restore(con, cfg, [file_id])
    assert sidecars.path_for(named["path"]).is_file() and not sidecars.path_for(removed).exists()
    cleanup.remove_files(con, cfg, [file_id], "test", keep_last_copy=False)
    cleanup.empty_removed(con, cfg)
    assert not sidecars.path_for(removed).exists()


def test_a_photo_deleted_on_the_share_takes_its_sidecar(con, cfg, named):
    sidecars.run(con, on(cfg))
    named["path"].unlink()
    scan(con, cfg)
    sidecars.run(con, on(cfg))
    assert not sidecars.path_for(named["path"]).exists()


def test_downloads_carry_the_sidecar_on_or_off(con, cfg, named):
    home = TestClient(webapp.create_app(cfg), client=LAN)
    r = home.post("/download", content=f"image={named['image']}",
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert sorted(z.namelist()) == ["IMG_0001.jpg", "IMG_0001.jpg.xmp"]
        assert xmp.parse(z.read("IMG_0001.jpg.xmp")).caption == "Grandma at the lake"
    file_id = con.execute("SELECT id FROM file WHERE image_id = ?", (named["image"],)).fetchone()[0]
    r = home.get(f"/original/{file_id}/xmp")
    assert r.status_code == 200 and 'filename="IMG_0001.jpg.xmp"' in r.headers["content-disposition"]
    assert xmp.parse(r.content).people == ["Ann Miller"]
    page = home.get(f"/image/{named['image']}").text
    assert f"/original/{file_id}/xmp" in page
    con.execute("DELETE FROM comment")
    con.execute("DELETE FROM image_tag")
    con.execute("UPDATE face SET assigned_by = 'auto'")
    con.commit()
    assert home.get(f"/original/{file_id}/xmp").status_code == 404  # nothing people made to go with it


def test_the_export_carries_every_sidecar(con, cfg, named, tmp_path):
    out = tmp_path / "export.zip"
    counts = export.write(con, cfg, out)
    with zipfile.ZipFile(out) as z:
        name = f"xmp/{rel('Lake', 'IMG_0001.jpg')}.xmp"
        assert name in z.namelist() and counts["sidecars"] == 1
        assert xmp.parse(z.read(name)).caption == "Grandma at the lake"


def test_the_sidecar_reads_in_exiftool(con, cfg, named):
    if writeback.exiftool() is None:
        pytest.skip("needs ExifTool")
    places.place_photo(con, named["image"], -33.86, 151.2)
    con.commit()
    sidecars.run(con, on(cfg))
    out = json.loads(tools.run([writeback.exiftool(), "-j", "-struct", "-n", "-charset", "utf8",
                                str(sidecars.path_for(named["path"]))]))[0]
    region = out["RegionInfo"]["RegionList"][0]
    assert region["Name"] == "Ann Miller" and region["Type"] == "Face"
    assert region["Area"]["X"] == pytest.approx(0.2) and out["RegionInfo"]["AppliedToDimensions"]["W"] == 400
    assert out["PersonInImage"] == ["Ann Miller"] and out["Description"] == "Grandma at the lake"
    assert out["GPSLatitude"] == pytest.approx(-33.86, abs=1e-5) and out["GPSLongitude"] == pytest.approx(151.2)


def test_an_admin_has_them_written_into_the_file(con, cfg, named):
    if writeback.exiftool() is None:
        pytest.skip("needs ExifTool")
    places.place_photo(con, named["image"], 45.5, -122.6)
    con.commit()
    old = con.execute("SELECT sha256 FROM image WHERE id = ?", (named["image"],)).fetchone()[0]
    home = TestClient(webapp.create_app(cfg), client=LAN)
    r = home.post(f"/api/images/{named['image']}/write-file", headers=APP)
    assert r.status_code == 200, r.text
    out = json.loads(tools.run([writeback.exiftool(), "-j", "-struct", "-n", "-G1", str(named["path"])]))[0]
    assert out["XMP-dc:Subject"] == ["picnic", "Ann Miller"] and out["XMP-dc:Description"] == "Grandma at the lake"
    assert out["XMP-mwg-rs:RegionInfo"]["RegionList"][0]["Name"] == "Ann Miller"
    assert out["GPS:GPSLatitude"] == pytest.approx(45.5) and out["GPS:GPSLongitude"] == pytest.approx(122.6)
    assert out["GPS:GPSLongitudeRef"] == "W"
    with Image.open(named["path"]) as im:
        assert im.size == (400, 300)  # the picture as it was
    assert con.execute("SELECT sha256 FROM image WHERE id = ?", (named["image"],)).fetchone()[0] != old
    # again: the keywords are not doubled
    assert home.post(f"/api/images/{named['image']}/write-file", headers=APP).status_code == 200
    out = json.loads(tools.run([writeback.exiftool(), "-j", "-G1", str(named["path"])]))[0]
    assert out["XMP-dc:Subject"] == ["picnic", "Ann Miller"]


def test_nothing_to_write_into_a_file_says_so(con, cfg, library):
    image = con.execute("SELECT image_id FROM file WHERE path = ?", (rel("2016", "IMG_20160811_182239.jpg"),)).fetchone()
    if image is None:
        scan(con, cfg)
        ingest(con, cfg)
        image = con.execute("SELECT image_id FROM file WHERE path = ?",
                            (rel("2016", "IMG_20160811_182239.jpg"),)).fetchone()
    with pytest.raises(writeback.Problem, match="nothing to write"):
        writeback.write_family(con, cfg, image[0])


def test_read_only_photo_folders_leave_them_to_the_copies_service(con, cfg, named, monkeypatch):
    """In Docker the worker only reads the photos: it leaves the sidecars queued, without a retry for each photo."""
    import errno

    def read_only(*a, **k):
        raise OSError(errno.EROFS, "Read-only file system")
    monkeypatch.setattr(sidecars, "_write", read_only)
    stats = sidecars.run(con, on(cfg))
    assert stats.read_only and stats.failed == 0
    assert not con.execute("SELECT 1 FROM retry").fetchone()
    assert con.execute("SELECT 1 FROM sidecar_stale WHERE image_id = ?", (named["image"],)).fetchone()
    monkeypatch.undo()
    assert sidecars.run(con, on(cfg)).written == 1  # where it can write (the copies service), they are written


SPRINGFIELD = {1: "N", 2: (39.0, 48.0, 7.0), 3: "W", 4: (89.0, 38.0, 38.0)}  # the camera's GPS: 39.80, -89.64


def test_a_folders_place_never_replaces_a_photos_own_gps(con, cfg, library):
    """A photo's GPS wins over its folder's place by hand (places.py): neither its sidecar nor its file, written by an
    admin, may say it was taken at the folder's place, the camera's position lost from the file."""
    gps = write_jpeg(library / "Trip" / "gps.jpg", size=(80, 60), gps=SPRINGFIELD, taken="2015:06:01 10:00:00")
    write_jpeg(library / "Trip" / "none.jpg", size=(80, 60), color="blue", taken="2015:06:02 10:00:00")
    scan(con, cfg)
    ingest(con, cfg)
    ids = {n: con.execute("SELECT image_id FROM file WHERE path = ?", (rel("Trip", n),)).fetchone()[0]
           for n in ("gps.jpg", "none.jpg")}
    trip = con.execute("SELECT id FROM folder WHERE path = ?", (rel("Trip"),)).fetchone()[0]
    places.place_folder(con, trip, 41.88, -87.63)
    comments.set_caption(con, ids["gps.jpg"], "At the fair", None)
    con.commit()
    assert sidecars.metadata(con, ids["none.jpg"]).gps == (41.88, -87.63)  # no GPS of its own: the folder's
    assert sidecars.metadata(con, ids["gps.jpg"]).gps is None  # its own GPS is in its file already
    if writeback.exiftool() is None:
        pytest.skip("needs ExifTool")
    writeback.write_family(con, cfg, ids["gps.jpg"])
    out = json.loads(tools.run([writeback.exiftool(), "-j", "-n", "-G1", str(gps)]))[0]
    assert out["GPS:GPSLatitude"] == pytest.approx(39.8019, abs=1e-3)  # still the camera's
    assert out["GPS:GPSLongitude"] == pytest.approx(89.6439, abs=1e-3) and out["XMP-dc:Description"] == "At the fair"


def test_writing_only_tags_keeps_the_other_keywords_in_the_file(con, cfg, library):
    """With nothing but tags and people to write, ExifTool must not be left to copy the whole sidecar over the file's
    XMP: the keywords and people another program put in the file stay, as with a caption."""
    if writeback.exiftool() is None:
        pytest.skip("needs ExifTool")
    path = write_jpeg(library / "Tagged" / "a.jpg", size=(80, 60), taken="2004:07:15 13:20:00")
    tools.run([writeback.exiftool(), "-q", "-overwrite_original", "-XMP-dc:Subject=Theirs",
               "-XMP-iptcExt:PersonInImage=Zoe Baker", "-XMP-dc:Description=Their caption", str(path)])
    scan(con, cfg)
    ingest(con, cfg)
    image = con.execute("SELECT image_id FROM file WHERE path = ?", (rel("Tagged", "a.jpg"),)).fetchone()[0]
    ann = con.execute("INSERT INTO person(name) VALUES ('Ann Miller')").lastrowid
    con.execute("INSERT INTO image_person(image_id, person_id, added_at) VALUES (?, ?, '2026-01-01')", (image, ann))
    tag = con.execute("INSERT INTO tag(name) VALUES ('picnic')").lastrowid
    con.execute("INSERT INTO image_tag(image_id, tag_id, source) VALUES (?, ?, 'user')", (image, tag))
    con.commit()
    writeback.write_family(con, cfg, image)
    out = json.loads(tools.run([writeback.exiftool(), "-j", "-G1", "-XMP:all", str(path)]))[0]
    assert out["XMP-dc:Subject"] == ["Theirs", "picnic", "Ann Miller"]
    assert out["XMP-iptcExt:PersonInImage"] == ["Zoe Baker", "Ann Miller"]
    assert out["XMP-dc:Description"] == "Their caption"  # no caption here: the file's stays


def test_a_changed_file_is_on_the_disk_before_it_takes_the_originals_place(con, cfg, named, monkeypatch):
    """The original is deleted once the catalog has the change: the changed file must be on the disk by then, or a
    power cut could leave neither (a renamed file's bytes may still be waiting in memory)."""
    if writeback.exiftool() is None:
        pytest.skip("needs ExifTool")
    events = []
    real_fsync, real_replace = writeback.os.fsync, writeback.os.replace
    monkeypatch.setattr(writeback.os, "fsync", lambda fd: (events.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(writeback.os, "replace", lambda a, b: (events.append(Path(a).name), real_replace(a, b))[1])
    writeback.write_family(con, cfg, named["image"])
    assert "fsync" in events and events.index("fsync") < events.index("IMG_0001.jpg")  # before the original moves


def test_xml_that_strict_readers_take(con, cfg):
    """Characters XML can't hold (a control character pasted with a name or caption) are left out of the sidecar, so
    programs that refuse a broken XMP file still read the rest."""
    data = xmp.packet(xmp.Metadata(width=4, height=4, people=["Ann\x01 Miller"], caption="The bell\x07 rang",
                                   faces=[("Ann\x01 Miller", 0.1, 0.1, 0.5, 0.5)], tags=["bell\x1b"]))
    found = xmp.parse(data)
    assert found.ours and found.people == ["Ann Miller"] and found.caption == "The bell rang"
    assert found.regions[0][0] == "Ann Miller"


class _Stop(BaseException):
    """Ends the copies service's endless loop in a test."""


def test_the_copies_service_writes_them_without_ffmpeg_and_between_copies(con, cfg, named, monkeypatch):
    """In Docker only the copies service may write beside the photos: its sidecars wait for no ffmpeg, nor for the
    last of a long run of 720p copies."""
    from lineagebox import copies, settings
    settings.save(con, cfg, {"sidecars_enabled": True})
    calls = []
    real_run = sidecars.run

    def run(c, f, minutes):
        calls.append("sidecars")
        return real_run(c, f, minutes=minutes)
    monkeypatch.setattr(sidecars, "run", run)
    monkeypatch.setattr(copies.video, "available", lambda: False)
    monkeypatch.setattr(copies.time, "sleep", lambda s: (_ for _ in ()).throw(_Stop()))
    with pytest.raises(_Stop):
        copies.run_forever(con, cfg)
    assert calls == ["sidecars"] and sidecars.path_for(named["path"]).is_file()
    rounds = iter([2, 1, 0])
    monkeypatch.setattr(copies.video, "available", lambda: True)
    monkeypatch.setattr(copies, "check", lambda c, f: 0)
    monkeypatch.setattr(copies, "make", lambda c, f, limit, failed: (calls.append("copies"), next(rounds))[1])
    calls.clear()
    with pytest.raises(_Stop):
        copies.run_forever(con, cfg)
    assert calls == ["copies", "sidecars", "copies", "sidecars", "copies", "sidecars"]


def test_a_file_is_never_half_written_when_exiftool_is_missing_or_fails(con, cfg, named, monkeypatch):
    """Without ExifTool, or when it can't be run, fails, hangs or writes nothing, the admin is told why and the photo,
    its copies and the catalog are exactly as they were, with nothing left beside the photo or in the data folder."""
    home = TestClient(webapp.create_app(cfg), client=LAN)
    before = named["path"].read_bytes()
    sha = con.execute("SELECT sha256 FROM image WHERE id = ?", (named["image"],)).fetchone()[0]

    def unchanged():
        assert named["path"].read_bytes() == before
        assert con.execute("SELECT sha256 FROM image WHERE id = ?", (named["image"],)).fetchone()[0] == sha
        assert not list(named["path"].parent.glob(".lineagebox-*"))
        assert not list((cfg.data_dir / "tmp").glob("*")) if (cfg.data_dir / "tmp").exists() else True

    monkeypatch.setattr(writeback, "exiftool", lambda: None)
    r = home.post(f"/api/images/{named['image']}/write-file", headers=APP)
    assert r.status_code == 409 and "needs ExifTool" in r.json()["detail"]
    unchanged()
    monkeypatch.setattr(writeback, "exiftool", lambda: str(cfg.data_dir / "no-such-exiftool"))
    r = home.post(f"/api/images/{named['image']}/write-file", headers=APP)
    assert r.status_code == 409 and "could not be run" in r.json()["detail"]
    unchanged()

    def fails(error):
        def run(*a, **k):
            if isinstance(error, Exception):
                raise error
            return error
        return run
    for error, words in ((tools.TimedOut("exiftool took over 300 s"), "more than 5 minutes"),
                         (tools.Error("Error: Not a valid JPG"), "Not a valid JPG"), (b"", "wrote nothing")):
        monkeypatch.setattr(writeback.tools, "run", fails(error))
        r = home.post(f"/api/images/{named['image']}/write-file", headers=APP)
        assert r.status_code == 409 and words in r.json()["detail"], r.text
        unchanged()


def test_a_sidecar_another_program_took_over_is_theirs(con, cfg, named):
    """darktable and digiKam read the sidecar of the same name and write their own work into it (darktable keeps a raw
    file's edits there), keeping what was in it, LineageBox's mark too. Once it isn't what LineageBox wrote, it is
    theirs: never written over, nor taken away when the photo has nothing to say or is deleted."""
    sidecars.run(con, on(cfg))
    side = sidecars.path_for(named["path"])
    # as darktable writes it back through Exiv2: its own work added, and the packet's maker its library's name
    theirs = side.read_bytes().replace(b'x:xmptk="LineageBox"', b'x:xmptk="XMP Core 4.4.0-Exiv2"').replace(
        b"  </rdf:Description>", b'   <darktable:history xmlns:darktable="http://darktable.sf.net/">crop, exposure'
                                 b" +0.7</darktable:history>\n  </rdf:Description>")
    side.write_bytes(theirs)
    comments.set_caption(con, named["image"], "Grandma at the lake, 1987", None)
    con.commit()
    stats = sidecars.run(con, on(cfg))
    assert (stats.written, stats.others) == (0, 1) and side.read_bytes() == theirs
    con.execute("DELETE FROM comment")
    con.execute("DELETE FROM image_tag")
    con.execute("UPDATE face SET assigned_by = 'auto'")
    con.commit()
    sidecars.run(con, on(cfg))
    assert side.read_bytes() == theirs  # nothing to say here now: still theirs
    named["path"].unlink()
    scan(con, cfg)
    sidecars.run(con, on(cfg))
    assert side.read_bytes() == theirs  # the photo deleted: their file is theirs to delete


def test_a_sidecar_exiftool_wrote_into_is_left_alone(con, cfg, named):
    """ExifTool keeps LineageBox's mark when it adds to a sidecar (a rating, here), and puts its own name on it."""
    if writeback.exiftool() is None:
        pytest.skip("needs ExifTool")
    sidecars.run(con, on(cfg))
    side = sidecars.path_for(named["path"])
    tools.run([writeback.exiftool(), "-q", "-overwrite_original", "-XMP-xmp:Rating=5", str(side)])
    theirs = side.read_bytes()
    assert xmp.OURS.encode() in theirs and b"Rating" in theirs
    comments.set_caption(con, named["image"], "Grandma at the lake, 1987", None)
    con.commit()
    assert sidecars.run(con, on(cfg)).others == 1 and side.read_bytes() == theirs


def test_a_sidecar_is_written_again_only_when_it_says_something_new(con, cfg, named):
    """A photo's dates worked out again (the folder stage) puts it on the list without changing what its sidecar says:
    the file is left as it is."""
    sidecars.run(con, on(cfg))
    side = sidecars.path_for(named["path"])
    before = side.read_bytes()
    con.execute("UPDATE image SET taken_at = taken_at WHERE id = ?", (named["image"],))
    con.commit()
    assert con.execute("SELECT 1 FROM sidecar_stale WHERE image_id = ?", (named["image"],)).fetchone()
    assert sidecars.run(con, on(cfg)).written == 0 and side.read_bytes() == before


def test_a_sidecar_that_fails_half_way_leaves_nothing_beside_the_photo(con, cfg, named, monkeypatch):
    """A sidecar is written under a dot-name and renamed into place: when the rename fails (a share gone for a moment),
    the half-made file is cleared away and the photo waits for the next pass."""
    def refused(src, dst):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(sidecars.os, "replace", refused)
    stats = sidecars.run(con, on(cfg))
    assert stats.failed == 1 and not list(named["path"].parent.glob(".lineagebox-*"))
    assert not sidecars.path_for(named["path"]).exists()


def test_a_photo_edited_on_the_share_takes_its_new_sidecar(con, cfg, named):
    """A file changed elsewhere is read again as another photo: the old photo's names stop being written beside it,
    and LineageBox's sidecar goes when the new photo has nothing to say."""
    sidecars.run(con, on(cfg))
    write_jpeg(named["path"], size=(400, 300), color=(200, 10, 10), taken="2004:07:15 13:20:00")  # edited elsewhere
    scan(con, cfg)
    ingest(con, cfg)
    new = con.execute("SELECT image_id FROM file WHERE path = ?", (rel("Lake", "IMG_0001.jpg"),)).fetchone()[0]
    assert new != named["image"]
    sidecars.run(con, on(cfg))
    assert not sidecars.path_for(named["path"]).exists()
    assert not con.execute("SELECT 1 FROM sidecar").fetchone()


def test_a_sidecar_that_cant_be_moved_with_its_photo_is_said(con, cfg, named, monkeypatch):
    """Cleanup moves the photo even when its sidecar can't go with it (the photo is what was asked for), and says so."""
    sidecars.run(con, on(cfg))
    real = cleanup._move

    def no_sidecars(src, dst, like):
        if src.name.endswith(".xmp"):
            raise PermissionError(13, "Permission denied")
        return real(src, dst, like)
    monkeypatch.setattr(cleanup, "_move", no_sidecars)
    file_id = con.execute("SELECT id FROM file WHERE image_id = ?", (named["image"],)).fetchone()[0]
    moved = cleanup.remove_files(con, cfg, [file_id], "test", keep_last_copy=False)
    assert moved.moved == 1 and moved.errors == ["IMG_0001.jpg.xmp: Permission denied"]
    assert not named["path"].exists() and sidecars.path_for(named["path"]).is_file()


def test_a_date_set_by_hand_goes_into_the_files_exif(con, cfg, named):
    if writeback.exiftool() is None:
        pytest.skip("needs ExifTool")
    con.execute("""UPDATE image SET auto_taken_at = taken_at, taken_at = '1987-07-16T00:00:00',
                   taken_at_source = 'user', date_text = 'July 1987', date_from = '1987-07-01', date_to = '1987-07-31'
                   WHERE id = ?""",
                (named["image"],))
    con.commit()
    writeback.write_family(con, cfg, named["image"])
    out = json.loads(tools.run([writeback.exiftool(), "-j", "-G1", str(named["path"])]))[0]
    assert out["ExifIFD:DateTimeOriginal"] == "1987:07:16 00:00:00"
    assert out["XMP-photoshop:DateCreated"] == "1987:07" and out["XMP-exif:DateTimeOriginal"].startswith("1987:07:16")


def test_a_changed_file_that_cant_take_the_originals_place_puts_the_original_back(con, cfg, named, monkeypatch):
    """The original has made way, and the changed file then can't be renamed into its place: the original goes back,
    and nothing is left beside it."""
    if writeback.exiftool() is None:
        pytest.skip("needs ExifTool")
    before = named["path"].read_bytes()
    real = writeback.os.replace

    def refused(src, dst):
        if Path(src).name.startswith(".lineagebox-new-") and Path(dst) == named["path"]:
            raise PermissionError(13, "Permission denied")
        return real(src, dst)
    monkeypatch.setattr(writeback.os, "replace", refused)
    with pytest.raises(writeback.Problem, match="Permission denied"):
        writeback.write_family(con, cfg, named["image"])
    assert named["path"].read_bytes() == before and not list(named["path"].parent.glob(".lineagebox-*"))


def test_folders_lineagebox_may_not_write_stop_the_stage_quietly(monkeypatch, cfg, con, library):
    """Permission refused on photo after photo, with nothing written: like a read-only mount, the stage stops with one
    problem instead of a retry line for every photo."""
    import dataclasses
    import errno as errno_mod
    from lineagebox import settings as settings_mod
    from lineagebox import sidecars as sc
    ids = list(range(1, sc.REFUSALS + 6))
    settings_mod.put(con, sc.SWEPT, "2026-11-07T00:00:00")  # already queued: only these photos wait
    con.executemany("INSERT OR IGNORE INTO sidecar_stale VALUES (?)", [(i,) for i in ids])
    con.commit()

    def refuse(*_a, **_k):
        raise PermissionError(errno_mod.EACCES, "Permission denied")
    monkeypatch.setattr(sc, "write_image", refuse)
    stats = sc.run(con, dataclasses.replace(cfg, sidecars_enabled=True))
    assert stats.read_only and stats.failed == sc.REFUSALS - 1 and stats.left == len(ids) - sc.REFUSALS + 1
