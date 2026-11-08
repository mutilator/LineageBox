"""Live Photos (live.py): an iPhone's still and its short clip, saved beside it with the same name, are one item."""
import pytest
from fastapi.testclient import TestClient

from lineagebox import access, catalog, cleanup, copies, db, describe, live, search, settings, speech
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests import family_fixture as fx
from tests.conftest import LAN, rel, write_jpeg
from tests.test_access_web import signed_in
from tests.test_media import heic
from tests.test_video import make_video, needs_ffmpeg

APP = {"X-Requested-With": "lineagebox"}


def image_of(con, path):
    return con.execute("SELECT i.* FROM image i JOIN file f ON f.image_id = i.id WHERE f.path = ?", (path,)).fetchone()


def by_hand(con, path, media, duration=None):
    """Catalogue a scanned file as if it had been read (no ffmpeg needed): its image row, linked to it."""
    image = con.execute("""INSERT INTO image(sha256, format, media, duration, codecs, playable, taken_at, stage)
                           VALUES (?, ?, ?, ?, ?, ?, '2021-07-04T10:11:12', 1)""",
                        (f"{abs(hash(path)):064x}"[-64:], path.rpartition(".")[2].lower(), media, duration,
                         "h264/aac" if media == "video" else None, "original" if media == "video" else None)).lastrowid
    con.execute("UPDATE file SET image_id = ? WHERE path = ?", (image, path))
    con.commit()
    return image


@pytest.fixture
def pairs(con, cfg, library):
    """A still and its clip, as an iPhone's files are copied off it, and videos that are not anyone's clip."""
    trip = library / "2021" / "Trip"
    trip.mkdir(parents=True)
    names = {"IMG_0001.HEIC": "photo", "IMG_0001.MOV": 2.9,     # a Live Photo
             "IMG_0002.JPG": "photo", "img_0002.mp4": 3.0,      # from an older phone, renamed by an export
             "IMG_0003.JPG": "photo", "IMG_0003.MOV": 25.0,     # too long to be a Live Photo's clip
             "IMG_0004.MOV": 2.5}                               # short, and no photo of that name beside it
    ids = {}
    for name in names:
        (trip / name).write_bytes(name.encode())
    scan(con, cfg)
    for name, what in names.items():
        ids[name] = by_hand(con, rel("2021", "Trip", name), "photo" if what == "photo" else "video",
                            None if what == "photo" else what)
    return ids


def test_a_clip_is_paired_with_the_still_beside_it(con, cfg, pairs):
    assert live.pair(con) == 2
    of = lambda name: catalog.image(con, pairs[name])["live_of"]
    assert of("IMG_0001.MOV") == pairs["IMG_0001.HEIC"] and of("img_0002.mp4") == pairs["IMG_0002.JPG"]
    assert of("IMG_0003.MOV") is None and of("IMG_0004.MOV") is None
    assert live.pair(con) == 0  # nothing new: nothing written

    # in no list or count: the folder, the year, the Status page, the search, the worker's stages
    folder = con.execute("SELECT id FROM folder WHERE path = ?", (rel("2021", "Trip"),)).fetchone()[0]
    shown = {r["id"] for r in catalog.folder_images(con, folder)}
    assert shown == {pairs[n] for n in ("IMG_0001.HEIC", "IMG_0002.JPG", "IMG_0003.JPG", "IMG_0003.MOV",
                                        "IMG_0004.MOV")}
    assert {r["id"] for r in catalog.year_images(con, 2021)} == shown
    assert catalog.folder_tree(con)[folder].own == 5 and catalog.stats(con)["videos"] == 2
    search.index_new(con)
    assert not {r[0] for r in con.execute("SELECT rowid FROM search_fts")} & {pairs["IMG_0001.MOV"],
                                                                              pairs["img_0002.mp4"]}
    assert speech.count_pending(con) == 2
    assert not {r["id"] for r in describe.pending(con, "test")} & {pairs["IMG_0001.MOV"], pairs["img_0002.mp4"]}

    # its still's page has it to play; a photo without one has none
    clip = catalog.live_clip(con, pairs["IMG_0001.HEIC"])
    assert (clip["id"], clip["url"]) == (pairs["IMG_0001.MOV"], f"/play/{pairs['IMG_0001.MOV']}")
    assert catalog.live_clip(con, pairs["IMG_0003.JPG"]) is None
    con.execute("UPDATE image SET playable = 'needed' WHERE id = ?", (pairs["IMG_0001.MOV"],))
    assert catalog.live_clip(con, pairs["IMG_0001.HEIC"])["url"] is None  # waits for its 720p copy
    assert copies.count_pending(con) == 1  # which is made like any video's


def test_a_clip_worked_on_by_hand_stays_a_video_of_its_own(con, cfg, pairs):
    person = con.execute("INSERT INTO person(name) VALUES ('Owen Miller')").lastrowid
    con.execute("INSERT INTO image_person(image_id, person_id, added_at) VALUES (?, ?, '2026-01-01')",
                (pairs["img_0002.mp4"], person))
    con.commit()
    assert live.pair(con) == 1
    assert catalog.image(con, pairs["img_0002.mp4"])["live_of"] is None
    assert catalog.image(con, pairs["IMG_0001.MOV"])["live_of"] == pairs["IMG_0001.HEIC"]


def test_a_clip_goes_where_its_still_goes(con, cfg, pairs):
    live.pair(con)
    still, clip = pairs["IMG_0001.HEIC"], pairs["IMG_0001.MOV"]
    clip_file = lambda: con.execute("SELECT * FROM file WHERE image_id = ?", (clip,)).fetchone()
    moved = cleanup.remove_images(con, cfg, [still], "test")
    assert (moved.moved, moved.errors) == (1, [])  # one photo, its clip with it
    gone = clip_file()
    assert gone["missing"] and gone["removed_path"].startswith("lib-Removed/")
    assert (cfg.share_root / gone["removed_path"]).is_file() and not (cfg.share_root / gone["path"]).exists()
    assert catalog.live_clip(con, still) is None

    # put back with its still, file by file or the whole lot
    still_file = con.execute("SELECT id FROM file WHERE image_id = ?", (still,)).fetchone()[0]
    assert cleanup.restore(con, cfg, [still_file]).moved == 1
    assert not clip_file()["missing"] and (cfg.share_root / clip_file()["path"]).is_file()
    assert catalog.live_clip(con, still)["id"] == clip
    cleanup.remove_files(con, cfg, [still_file], "test", keep_last_copy=False)
    batch = clip_file()["removed_batch"]
    assert batch is not None and cleanup.restore(con, cfg, batch=batch).moved == 1
    assert not clip_file()["missing"]

    # the last copy of a photo is kept, and so is its clip
    assert cleanup.remove_files(con, cfg, [still_file], "test").errors
    assert not clip_file()["missing"]

    # a still deleted on the share: its clip is a video of its own again
    (cfg.share_root / rel("2021", "Trip", "IMG_0001.HEIC")).unlink()
    scan(con, cfg)
    assert live.pair(con) == 1 and catalog.image(con, clip)["live_of"] is None
    folder = con.execute("SELECT id FROM folder WHERE path = ?", (rel("2021", "Trip"),)).fetchone()[0]
    assert clip in {r["id"] for r in catalog.folder_images(con, folder)}


@needs_ffmpeg
def test_live_photos_from_the_share_to_the_page(con, cfg, library):
    trip = library / "2021" / "Trip"
    trip.mkdir(parents=True)
    (trip / "IMG_0001.HEIC").write_bytes(heic(taken="2021:07:04 10:11:12", make="Apple", model="iPhone 12"))
    make_video(trip / "IMG_0001.MOV", seconds=3)
    write_jpeg(trip / "IMG_0002.JPG", taken="2021:07:04 10:20:00")
    make_video(trip / "IMG_0002.MOV", seconds=12)  # a video someone made, numbered after the photo
    scan(con, cfg)
    stats = ingest(con, cfg, show_progress=False)
    assert stats.live == 1 and stats.decode_errors == 1  # broken.jpg
    still = image_of(con, rel("2021", "Trip", "IMG_0001.HEIC"))
    clip = image_of(con, rel("2021", "Trip", "IMG_0001.MOV"))
    assert (still["format"], still["camera"], still["taken_at"]) == ("heic", "Apple iPhone 12", "2021-07-04T10:11:12")
    assert clip["live_of"] == still["id"] and clip["playable"] == "original"
    assert len(catalog.video_frames(con, clip["id"])) == 1  # one frame is enough: it is shown with its still
    assert image_of(con, rel("2021", "Trip", "IMG_0002.MOV"))["live_of"] is None
    assert len(catalog.video_frames(con, image_of(con, rel("2021", "Trip", "IMG_0002.MOV"))["id"])) == 3

    client = TestClient(webapp.create_app(cfg), client=LAN)
    folder = con.execute("SELECT id FROM folder WHERE path = ?", (rel("2021", "Trip"),)).fetchone()[0]
    page = client.get(f"/folder/{folder}").text
    assert f"/t/{still['sha256']}" in page and f"/t/{clip['sha256']}" not in page
    assert client.get(catalog.live_clip(con, still["id"])["url"], headers={"Range": "bytes=0-99"}).status_code == 206

    # removed on its page, the clip goes along; put back, it comes back
    assert client.post(f"/api/images/{still['id']}/remove", headers=APP).status_code == 200
    assert not (trip / "IMG_0001.MOV").exists() and client.get(f"/play/{clip['id']}").status_code == 404
    batch = con.execute("SELECT removed_batch FROM file WHERE image_id = ?", (clip["id"],)).fetchone()[0]
    assert client.post(f"/api/removed/restore?batch={batch}", headers=APP).status_code == 200
    assert (trip / "IMG_0001.MOV").is_file() and client.get(f"/play/{clip['id']}").status_code == 200


@needs_ffmpeg
def test_whoever_sees_the_still_may_play_its_clip(con, cfg, library):
    fam = fx.build(con, cfg, library)
    fx.account(con, fam["people"]["Sam"], "sam@example.com", "admin")
    make_video(library / "Family" / "Picnic" / "cora_bart.mov", seconds=3)  # Cora's
    make_video(library / "ZedSecret" / "zed.mov", seconds=3)  # not hers
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    hers, not_hers = (image_of(con, rel("Family", "Picnic", "cora_bart.mov")),
                      image_of(con, rel("ZedSecret", "zed.mov")))
    assert hers["live_of"] == fam["photos"]["cora_bart.jpg"] and not_hers["live_of"] == fam["photos"]["zed.jpg"]

    cora = fx.account(con, fam["people"]["Cora"], "cora@example.com")
    v = access.compute(con, cora)
    assert hers["id"] in v.images and hers["sha256"] in v.shas and not_hers["id"] not in v.images
    c = access.scope(db.connect(cfg, migrate_schema=False), v)
    try:
        assert catalog.live_clip(c, fam["photos"]["cora_bart.jpg"])["id"] == hers["id"]
        assert catalog.live_clip(c, fam["photos"]["zed.jpg"]) is None
    finally:
        c.close()
    settings.save(con, cfg, {"accounts_enabled": True})
    client = signed_in(cfg, con, cora)
    assert client.get(f"/play/{hers['id']}").status_code == 200
    assert client.get(f"/play/{not_hers['id']}").status_code == 404
    assert client.get(f"/t/{not_hers['sha256']}").status_code == 404


def test_a_clip_whose_still_stays_elsewhere_is_never_emptied_away(con, cfg, pairs, library):
    """Removing one copy of a still (Cleanup's copies in two folders) leaves the photo in the library, so its clip's
    only copy stays too: moved to Removed, emptying it would delete the moving part of a photo still kept."""
    live.pair(con)
    still, clip = pairs["IMG_0001.HEIC"], pairs["IMG_0001.MOV"]
    backup = library / "Backup"
    backup.mkdir()
    (backup / "IMG_0001.HEIC").write_bytes(b"IMG_0001.HEIC")  # the still alone, copied without its clip
    scan(con, cfg)
    con.execute("UPDATE file SET image_id = ? WHERE path = ?", (still, rel("Backup", "IMG_0001.HEIC")))
    con.commit()
    trip_still = con.execute("SELECT id FROM file WHERE path = ?",
                             (rel("2021", "Trip", "IMG_0001.HEIC"),)).fetchone()[0]
    moved = cleanup.remove_files(con, cfg, [trip_still], "copies in Trip (kept in Backup)")
    assert (moved.moved, moved.errors) == (1, [])
    assert not con.execute("SELECT missing FROM file WHERE image_id = ?", (clip,)).fetchone()[0]
    cleanup.empty_removed(con, cfg)
    assert (library / "2021" / "Trip" / "IMG_0001.MOV").is_file()
