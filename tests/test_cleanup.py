import re

import pytest
from fastapi.testclient import TestClient

from lineagebox import cleanup, quality, search
from lineagebox.describe import _record
from lineagebox.folders import FolderIndex
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from lineagebox.web.routes import cleanup as cleanup_routes
from tests.conftest import LAN, rel
from tests.test_recognition import FakeOllama

APP = {"X-Requested-With": "lineagebox"}
ZOO_A, ZOO_B = rel("2012", "Zoo", "DSC_0001.jpg"), rel("Owen", "Zoo", "DSC_0001.jpg")  # identical files


@pytest.fixture
def lib(con, cfg, library):
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    search.rebuild(con)
    return con


def removed(cfg, path):
    """Where cleanup puts a removed file: in the Removed folder next to its watched folder."""
    return cfg.share_root / path.replace("lib/", "lib-Removed/", 1)


def file_row(con, path):
    return con.execute("SELECT * FROM file WHERE path = ?", (path,)).fetchone()


def folder_id(con, path):
    return con.execute("SELECT id FROM folder WHERE path = ?", (path,)).fetchone()[0]


def test_duplicate_copies_move_out_and_back(lib, cfg, library):
    con = lib
    pairs, total = cleanup.copy_pairs(con)
    assert total == 1 and pairs[0]["shared"] == 1
    zoo_a, zoo_b = folder_id(con, rel("2012", "Zoo")), folder_id(con, rel("Owen", "Zoo"))

    r = cleanup.remove_copies(con, cfg, remove_from=zoo_b, keep_in=zoo_a)
    assert (r.moved, r.errors) == (1, [])
    assert not (cfg.share_root / ZOO_B).exists() and removed(cfg, ZOO_B).is_file()
    assert (cfg.share_root / ZOO_A).is_file()
    f = file_row(con, ZOO_B)
    assert f["missing"] == 1 and f["removed_reason"] == "copies in lib/Owen/Zoo (kept in lib/2012/Zoo)"
    assert cleanup.copy_pairs(con)[1] == 0
    assert [r["id"] for r in search.query(con, "zoo")] == [f["image_id"]]  # the photo itself is still there

    # the last copy is never removed as a "copy"
    r = cleanup.remove_files(con, cfg, [file_row(con, ZOO_A)["id"]], "test")
    assert r.moved == 0 and "only copy" in r.errors[0] and (cfg.share_root / ZOO_A).is_file()

    r = cleanup.restore(con, cfg, batch=f["removed_batch"])
    assert r.moved == 1 and (cfg.share_root / ZOO_B).is_file() and not removed(cfg, ZOO_B).exists()
    f = file_row(con, ZOO_B)
    assert f["missing"] == 0 and f["removed_at"] is None


def test_removed_photos_leave_every_list_until_restored(lib, cfg, library):
    con = lib
    image_id = file_row(con, ZOO_A)["image_id"]
    client = TestClient(webapp.create_app(cfg), client=LAN)
    r = client.post("/api/images/remove", json={"image_ids": [image_id], "reason": "test"})
    assert r.status_code == 403  # only from the app's own pages
    r = client.post("/api/images/remove", json={"image_ids": [image_id], "reason": "test"}, headers=APP)
    assert r.json() == {"moved": 2, "errors": [], "more_errors": 0}
    assert not (cfg.share_root / ZOO_A).exists() and not (cfg.share_root / ZOO_B).exists()
    assert search.query(con, "zoo") == []
    assert f'href="/image/{image_id}' not in client.get(f"/folder/{folder_id(con, rel('2012', 'Zoo'))}").text
    page = client.get(f"/image/{image_id}").text
    assert page.count("in the Removed folder (test)") == 2 and "Restore" in page

    removed = client.get("/cleanup/removed").text
    assert "2 files in" in removed and "test" in removed
    batch = file_row(con, ZOO_A)["removed_batch"]
    assert client.post(f"/api/removed/restore?batch={batch}", headers=APP).json()["moved"] == 2
    assert (cfg.share_root / ZOO_A).is_file() and (cfg.share_root / ZOO_B).is_file()
    assert len(search.query(con, "zoo")) == 1


def test_empty_deletes_for_good(lib, cfg, library):
    con = lib
    cleanup.remove_images(con, cfg, [file_row(con, ZOO_A)["image_id"]], "test")
    assert removed(cfg, ZOO_A).is_file()
    r = cleanup.empty_removed(con, cfg)
    assert (r.moved, r.errors) == (2, [])
    assert file_row(con, ZOO_A) is None and file_row(con, ZOO_B) is None
    assert list((cfg.share_root / "lib-Removed").iterdir()) == []  # no empty folders left behind
    assert scan(con, cfg).new == 0  # and the photos stay gone


def test_name_clash_in_removed_folder(lib, cfg, library):
    con = lib
    removed(cfg, ZOO_A).parent.mkdir(parents=True)
    removed(cfg, ZOO_A).write_bytes(b"removed earlier")
    cleanup.remove_files(con, cfg, [file_row(con, ZOO_A)["id"]], "test")
    assert file_row(con, ZOO_A)["removed_path"] == "lib-Removed/2012/Zoo/DSC_0001 (2).jpg"
    assert removed(cfg, ZOO_A).read_bytes() == b"removed earlier"


def test_put_back_by_hand_is_noticed(lib, cfg, library):
    con = lib
    cleanup.remove_files(con, cfg, [file_row(con, ZOO_B)["id"]], "test")
    removed(cfg, ZOO_B).rename(cfg.share_root / ZOO_B)
    scan(con, cfg)
    f = file_row(con, ZOO_B)
    assert f["missing"] == 0 and f["removed_at"] is None


def test_similar_shots_keep_the_group_searchable(lib, cfg):
    con = lib
    a, b = [r[0] for r in con.execute("SELECT id FROM image WHERE error IS NULL AND format = 'jpeg' ORDER BY id LIMIT 2")]
    con.execute("UPDATE image SET dup_group_id = ?, width = 100, height = 100, taken_at_source = 'exif', sharpness = 50 WHERE id = ?", (a, a))
    con.execute("UPDATE image SET dup_group_id = ?, width = 100, height = 100, taken_at_source = 'exif', sharpness = 80 WHERE id = ?", (a, b))
    _record(con, a, "fake", FakeOllama(cfg).chat_json("", "Catalog this photo.", {}, b"x")[0], FolderIndex(con))
    con.commit()
    groups, total = cleanup.similar_groups(con)
    assert total == 1 and [m["id"] for m in groups[0]["members"] if m["keep"]] == [b]  # same size: the sharper

    cleanup.remove_images(con, cfg, [a], "similar shots")  # the group's representative goes
    assert con.execute("SELECT dup_group_id FROM image WHERE id = ?", (b,)).fetchone()[0] == b
    assert [r["id"] for r in search.query(con, "goat")] == [b]  # still found, with the shared description
    assert cleanup.similar_groups(con)[1] == 0


def test_sharpness_and_lists(lib, cfg):
    con = lib
    assert quality.measure(con, cfg, show_progress=False) > 0
    assert con.execute("SELECT count(*) FROM image WHERE error IS NULL AND sharpness IS NULL").fetchone()[0] == 0
    flat, total = cleanup.blurry(con, below=1e9)
    assert total == len(flat) > 0 and [r["sharpness"] for r in flat] == sorted(r["sharpness"] for r in flat)

    image_id = file_row(con, ZOO_A)["image_id"]
    answer = FakeOllama(cfg).chat_json("", "Catalog this photo.", {}, b"x")[0]
    _record(con, image_id, "fake", {**answer, "caption": "Exposed wiring inside an electrical box.", "people_count": 0},
            FolderIndex(con))
    con.commit()
    found = cleanup.reference_photos(con)
    assert [p["id"] for p in found["repairs"]] == [image_id] and found["repairs"][0]["matched"] == "wiring"
    con.execute("UPDATE analysis SET people_count = 2 WHERE image_id = ?", (image_id,))
    assert cleanup.reference_photos(con)["repairs"] == []  # photos with people are never "reference photos"


def test_cleanup_pages(lib, cfg):
    client = TestClient(webapp.create_app(cfg), client=LAN)
    for url in ("/cleanup", "/cleanup/copies", "/cleanup/similar", "/cleanup/blurry", "/cleanup/reference/papers",
                "/cleanup/removed", "/search?q=zoo&select=1"):
        r = client.get(url)
        assert r.status_code == 200, url
    copies = client.get("/cleanup/copies").text
    assert "Remove the copies from this folder" in copies and "1 pair of folders holds" in copies
    # each folder's side has a class of its own: .side is the menu at the side, whose rules hid them on a phone
    assert copies.count('class="copy-side"') == 2 and copies.count('class="side"') == 1
    assert client.get("/cleanup/reference/nope").status_code == 404


def test_the_cleanup_page_opens_before_its_numbers_are_worked_out(lib, cfg):
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get("/cleanup").text
    cards = len(re.findall(r'class="card[ "]', page))  # Private papers' card has a class of its own too
    assert page.count('class="num loading"') == cards > 10  # every card waits for its number
    for part in cleanup_routes.COUNTS:
        assert f'data-later="/cleanup/counts/{part}"' in page, part
        r = client.get(f"/cleanup/counts/{part}")
        assert r.status_code == 200 and "loading" not in r.text, part
    copies = client.get("/cleanup/counts/copies").text  # ZOO_A and ZOO_B are the same photo
    assert 'href="/cleanup/copies"' in copies and '<span class="num">1</span>' in copies
    assert page.count('href="/cleanup/reference/') == len(cleanup.REFERENCE_LABELS)
    assert client.get("/cleanup/counts/nope").status_code == 404


def test_kept_photos_are_not_suggested_again(lib, cfg):
    con = lib
    quality.measure(con, cfg, show_progress=False)
    blurry_ids = lambda: [r["id"] for r in cleanup.blurry(con, below=1e9)[0]]
    image_id = file_row(con, ZOO_A)["image_id"]
    answer = FakeOllama(cfg).chat_json("", "Catalog this photo.", {}, b"x")[0]
    _record(con, image_id, "fake", {**answer, "caption": "A stack of receipts.", "people_count": 0}, FolderIndex(con))
    con.commit()
    assert image_id in blurry_ids() and [p["id"] for p in cleanup.reference_photos(con)["papers"]] == [image_id]

    assert cleanup.keep_images(con, [image_id]) == 1
    assert image_id not in blurry_ids() and cleanup.reference_photos(con)["papers"] == []
    assert cleanup.summary(con, 1e9)["kept"] == 1 and [r["id"] for r in cleanup.kept(con)[0]] == [image_id]
    # the Cleanup card counts the photos and the folder pairs kept, as its page lists both
    con.execute("INSERT INTO kept_copies(folder_a, folder_b, kept_at) VALUES (1, 2, '2026-11-08T10:00:00')")
    con.commit()
    card = TestClient(webapp.create_app(cfg), client=LAN).get("/cleanup/counts/kept").text
    assert '<span class="num">2</span>' in card and "1 photo,\n1 folder pair." in card
    con.execute("DELETE FROM kept_copies")
    cleanup.keep_images(con, [image_id], keep=False)
    assert image_id in blurry_ids()

    a, b = [r[0] for r in con.execute("SELECT id FROM image WHERE error IS NULL AND format = 'jpeg' ORDER BY id LIMIT 2")]
    con.execute("UPDATE image SET dup_group_id = ? WHERE id IN (?, ?)", (a, a, b))
    con.commit()
    assert cleanup.similar_groups(con)[1] == 1
    cleanup.keep_group(con, a)
    assert cleanup.similar_groups(con)[1] == 0

    pair = cleanup.copy_pairs(con)[0][0]
    cleanup.keep_copies(con, pair["b"], pair["a"])  # either order
    assert cleanup.copy_pairs(con)[1] == 0 and cleanup.kept(con)[1][0]["a"] == pair["a"]
    cleanup.keep_copies(con, pair["a"], pair["b"], keep=False)
    assert cleanup.copy_pairs(con)[1] == 1


def test_keep_through_the_pages(lib, cfg):
    con = lib
    client = TestClient(webapp.create_app(cfg), client=LAN)
    image_id = file_row(con, ZOO_A)["image_id"]
    assert client.post("/api/images/keep", json={"image_ids": [image_id]}).status_code == 403
    assert client.post("/api/images/keep", json={"image_ids": [image_id]}, headers=APP).json() == {"kept": 1}
    assert "Undo" in client.get(f"/image/{image_id}").text
    assert f'value="{image_id}"' in client.get("/cleanup/kept").text
    pair = cleanup.copy_pairs(con)[0][0]
    assert client.post(f"/api/cleanup/copies/keep?a={pair['a']}&b={pair['b']}", headers=APP).status_code == 200
    assert "Nothing" not in client.get("/cleanup/kept").text and "List again" in client.get("/cleanup/kept").text
    assert client.post("/api/images/unkeep", json={"image_ids": [image_id]}, headers=APP).json() == {"kept": 1}
    assert "Keep (don" in client.get(f"/image/{image_id}").text


def _photo(con, id_, group, w, h, fmt="jpeg", taken="2012-06-03T10:00:00", source="exif", phash=0, sharp=50.0):
    con.execute("""INSERT INTO image(id, sha256, format, width, height, phash, taken_at, taken_at_source, sharpness,
                                     dup_group_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (id_, f"{id_:064x}", fmt, w, h, phash, taken, source, sharp, group))
    folder = con.execute("SELECT id FROM folder WHERE path = 'lib'").fetchone()[0]
    con.execute("INSERT INTO file(path, folder_id, image_id, size, mtime, last_seen) VALUES (?, ?, ?, ?, 1, '')",
                (f"lib/x/{id_}.{fmt}", folder, id_, w * h))


def test_similar_shots_by_kind(lib, cfg):
    con = lib
    _photo(con, 101, 101, 4000, 3000)                                   # a photo...
    _photo(con, 102, 101, 1000, 750)                                    # ...a small copy of it...
    _photo(con, 103, 101, 4000, 3000, taken="2012-06-03T10:00:05", sharp=90)  # ...and the next shot of a burst
    _photo(con, 201, 201, 3008, 2000, fmt="nef")                        # RAW + JPEG of one shot
    _photo(con, 202, 201, 3008, 2000)
    _photo(con, 301, 301, 2000, 1500, taken="2010-01-01T09:00:00")      # one copy kept its camera date...
    _photo(con, 302, 301, 2000, 1500, taken="2010-01-01T00:00:00", source="folder", phash=1)  # ...one lost it
    con.commit()

    def ticked(kind, keep_raw=True):
        return sorted(cleanup.similar_ticked(con, kind, keep_raw))

    assert ticked("smaller") == [102]                  # not the burst shot in the same group
    assert ticked("raw") == [202] and ticked("raw", keep_raw=False) == [201]
    assert ticked("undated") == [302]
    assert ticked("same") == [202, 302]                # same picture, same size
    assert ticked("burst") == [101, 102]               # the sharpest shot is suggested
    assert ticked("all") == [101, 102, 202, 302]
    counts = cleanup.similar_counts(con)
    assert (counts["smaller"], counts["raw"], counts["undated"], counts["burst"], counts["big"]) == (1, 1, 1, 1, 0)

    cleanup.keep_images(con, [102])  # kept on purpose: never ticked again
    assert ticked("smaller") == [] and 102 not in ticked("all")

    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get("/cleanup/similar?kind=raw&keep=jpeg").text
    assert 'aria-current="true">RAW + JPEG' in page and "the RAW file</span>" in page
    assert 'value="201" checked' in page and 'value="202" checked' not in page
    assert "Move the ticked photos in all 1 groups" in page
    assert "Move the ticked photos in all" not in client.get("/cleanup/similar?kind=burst").text  # look first
    assert client.get("/cleanup/similar?kind=nope").status_code == 404
    assert client.post("/api/similar/remove-all?kind=burst", headers=APP).status_code == 400


def test_page_sizes(lib, cfg):
    con = lib
    for n in range(60):
        _photo(con, 500 + n, None, 100, 100, sharp=1.0 + n / 100)  # 60 blank photos, worst first
    con.commit()
    client = TestClient(webapp.create_app(cfg), client=LAN)
    tiles = lambda html: html.count('name="image"')

    page = client.get("/cleanup/blurry").text
    assert tiles(page) == 60 and "1–60 of 60" in page  # 100 to start with
    page = client.get("/cleanup/blurry?per=25&page=2").text
    assert tiles(page) == 25 and "26–50 of 60" in page and "page 2 of 3" in page
    assert 'href="?per=50&amp;page=1"' in page  # a bigger page that still starts with photo 26
    assert client.cookies.get("per_blurry") == "25"
    page = client.get("/cleanup/blurry").text  # remembered for this list...
    assert tiles(page) == 25
    assert tiles(client.get("/cleanup/blurry?per=all").text) == 60
    assert tiles(client.get("/cleanup/blurry?per=7").text) == 60  # ...and only the offered sizes are taken
    assert "per_similar" not in client.cookies  # ...but not for the others


def test_similar_shots_survive_odd_rows(lib, cfg):
    """A photo listed twice in its group (a damaged index did that) or without a known size never breaks the page."""
    from lineagebox.cleanup import _mark
    a = {"id": 1, "pixels": 0, "dated": True, "taken_at": "t", "phash": 0, "sharpness": 1.0, "bytes": 1,
         "kept_at": None, "format": "jpeg"}
    for kind in cleanup.SIMILAR_KINDS:
        _mark(kind, [a, dict(a)], True)  # the same photo twice, size unknown
        _mark(kind, [a, {**a, "id": 2}], True)  # two photos of unknown size: neither is a "smaller copy"
    assert not _mark("smaller", [a, {**a, "id": 2}], True)


def test_members_clean_up_their_own_uploads(con, cfg, library):
    """Blank and blurry, and Similar shots, for a member: of the photos they uploaded and nobody else has, moved to
    Removed as their uploads are; never another photo they see, nor one they don't."""
    from lineagebox import settings
    from tests import family_fixture as fx
    from tests.test_uploads import client, jpeg, upload

    site = fx.build(con, cfg, library)
    p, photos = site["people"], site["photos"]
    fx.account(con, p["Sam"], "sam@example.com", "admin")
    cora = fx.account(con, p["Cora"], "cora@example.com")
    settings.save(con, cfg, {"accounts_enabled": True})
    for name, color in (("a.jpg", "red"), ("b.jpg", "green"), ("c.jpg", "blue")):
        assert upload(client(cfg, con, cora), name, jpeg(color), folder="Trip").status_code == 200
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    up = {n: con.execute("SELECT image_id FROM file WHERE path LIKE ?", (f"%/Trip/{n}",)).fetchone()[0]
          for n in ("a.jpg", "b.jpg", "c.jpg")}
    hers_too = photos["cora_bart.jpg"]  # a photo she sees, not one she uploaded
    con.execute("UPDATE image SET sharpness = 80, dup_group_id = NULL")
    con.execute("UPDATE image SET dup_group_id = ? WHERE id IN (?, ?, ?)", (up["a.jpg"], up["a.jpg"], up["b.jpg"],
                                                                            hers_too))
    con.execute("UPDATE image SET sharpness = 1 WHERE id IN (?, ?, ?)", (up["c.jpg"], hers_too, photos["zed.jpg"]))
    con.commit()

    as_cora = client(cfg, con, cora)
    page = as_cora.get("/cleanup").text
    assert 'data-later="/cleanup/counts/blurry"' in page and 'data-later="/cleanup/counts/similar"' in page
    assert 'data-later="/cleanup/counts/copies"' not in page
    assert '<span class="num">1</span>' in as_cora.get("/cleanup/counts/blurry").text
    assert '<span class="num">1</span>' in as_cora.get("/cleanup/counts/similar").text
    blurry = as_cora.get("/cleanup/blurry").text
    assert f'value="{up["c.jpg"]}"' in blurry and f'value="{hers_too}"' not in blurry
    assert f'value="{photos["zed.jpg"]}"' not in blurry and "/api/images/keep" not in blurry
    similar = as_cora.get("/cleanup/similar").text
    assert f'value="{up["a.jpg"]}"' in similar and f'value="{up["b.jpg"]}"' in similar
    assert f'value="{hers_too}"' not in similar and "Keep all" not in similar and "an admin can put them back" in similar
    assert as_cora.post("/api/similar/remove-all?kind=same", headers=APP).status_code in (403, 404)

    r = as_cora.post("/api/images/remove", json={"image_ids": [up["c.jpg"]], "reason": "blank or blurry"}, headers=APP)
    assert r.status_code == 200 and r.json()["moved"] == 1, r.text
    assert as_cora.post("/api/images/remove", json={"image_ids": [hers_too]}, headers=APP).status_code == 404

    sam = client(cfg, con, fx.account(con, p["Bart"], "bart@example.com", "admin"))
    everyone = sam.get("/cleanup/blurry").text  # an admin's: the whole library's
    assert f'value="{hers_too}"' in everyone and f'value="{photos["zed.jpg"]}"' in everyone
