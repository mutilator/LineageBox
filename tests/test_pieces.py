"""Big pages a piece at a time with a scrubber, sharp tiles, ticking photos on every list, and what tiles and the
keyboard offer someone who can't see the screen or use a mouse (pieces.py, tiles.py, _pieces.html)."""
import json
import os
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from lineagebox import catalog, people, tours
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from lineagebox.web import pieces, tiles
from tests.conftest import LAN, rel
from tests.test_access_web import signed_in, site  # noqa: F401 - site is a fixture

BIG = 450  # photos in the big folder, one every two to three days from 2001 into 2004
STATIC = Path(webapp.__file__).parent / "static"
TEMPLATES = Path(webapp.__file__).parent / "templates"


@pytest.fixture
def big(con, cfg, library):
    """The sample library, and a folder of BIG photos as the catalog has them (their pictures aren't needed for the
    pages). The first has Emma and Jack's faces, left to right, and Lily added by hand; the second is a video."""
    scan(con, cfg)
    ingest(con, cfg)
    lib = con.execute("SELECT id FROM folder WHERE path = ?", (rel(),)).fetchone()[0]
    folder = con.execute("INSERT INTO folder(path, name, parent_id) VALUES (?, 'Big', ?)",
                         (rel("Big"), lib)).lastrowid
    ids = []
    for i in range(BIG):
        taken = (date(2001, 1, 1) + timedelta(days=i * 1100 // BIG)).isoformat() + "T12:00:00"
        image = con.execute("""INSERT INTO image(sha256, format, stage, media, taken_at, taken_at_source)
                               VALUES (?, 'jpeg', 7, ?, ?, 'exif')""",
                            (f"{i + 1:064x}", "video" if i == 1 else "photo", taken)).lastrowid
        con.execute("""INSERT INTO file(path, folder_id, size, mtime, last_seen, image_id)
                       VALUES (?, ?, 1, 0, '2026-01-01', ?)""", (rel("Big", f"IMG_{i:04d}.jpg"), folder, image))
        ids.append(image)
    emma, jack, lily = (people.person_id(con, n) for n in ("Emma Miller", "Jack Baker", "Lily Miller"))
    for person, x in ((jack, 0.6), (emma, 0.1)):
        con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, embedding, person_id, assigned_by)
                       VALUES (?, ?, 0.1, ?, 0.4, x'00', ?, 'user')""", (ids[0], x, x + 0.2, person))
    con.execute("INSERT INTO image_person(image_id, person_id, added_at) VALUES (?, ?, '2026-01-01')", (ids[0], lily))
    con.commit()
    return {"folder": folder, "ids": ids, "emma": emma}


@pytest.fixture
def client(cfg):
    return TestClient(webapp.create_app(cfg), client=LAN)


def tile_ids(html: str) -> list[int]:
    return [int(i) for i in re.findall(r'class="tile[^"]*" href="/image/(\d+)\?', html)
            + re.findall(r'class="tile pick"[^>]*>\s*<input type="checkbox" name="image" value="(\d+)"', html)]


def test_a_big_folder_comes_a_piece_at_a_time(big, client):
    f, ids = big["folder"], big["ids"]
    first = client.get(f"/folder/{f}").text
    assert first.count('class="tile"') == pieces.PIECE
    assert f'data-pieces data-start="0" data-end="{pieces.PIECE}" data-total="{BIG}"' in first
    assert 'href="?skip=200" data-more>Show more <span class="hint inline">(250 more)' in first  # a plain link too
    assert "data-earlier" not in first
    # the next piece, as app.js asks for it: only the tiles, and the links on
    piece = client.get(f"/folder/{f}?skip=200&piece=1").text
    assert "<html" not in piece and piece.count('class="tile"') == 200
    assert 'data-piece data-start="200" data-end="400"' in piece
    assert 'data-earlier="?skip=0&amp;upto=200"' in piece and 'href="?skip=400" data-more' in piece
    last = client.get(f"/folder/{f}?skip=400").text  # a page opened further down: its own, with the way back up
    assert last.count('class="tile"') == 50 and 'href="?skip=200" data-earlier' in last and "data-more" not in last
    assert tile_ids(first) + tile_ids(piece) + tile_ids(last) == ids
    # loaded again with all it had loaded (app.js keeps ?upto= in the address): the same tiles, at most MOST
    assert tile_ids(client.get(f"/folder/{f}?upto={BIG}").text) == ids
    assert client.get(f"/folder/{f}?skip=99999").status_code == 200


def test_at_most_so_many_at_once(big, client, monkeypatch):
    monkeypatch.setattr(pieces, "MOST", 100)
    assert client.get(f"/folder/{big['folder']}?upto={BIG}").text.count('class="tile"') == 100


def test_a_photo_still_steps_through_the_whole_list(big, client):
    """←/→ on a photo go through the whole folder whatever part was loaded, and Back opens the piece it is in."""
    f, ids = big["folder"], big["ids"]
    photo = client.get(f"/image/{ids[300]}?ctx=folder:{f}").text
    assert "301 of 450" in photo
    assert f'href="/image/{ids[299]}?ctx=folder%3A{f}"' in photo
    assert f'href="/image/{ids[301]}?ctx=folder%3A{f}"' in photo
    assert f'href="/folder/{f}?skip=200#t{ids[300]}" data-back' in photo
    assert f'href="/folder/{f}" data-back' in client.get(f"/image/{ids[3]}?ctx=folder:{f}").text
    assert f'id="t{ids[300]}"' in client.get(f"/folder/{f}?skip=200").text  # where Back scrolls to


def test_the_scrubber(big, client, con):
    f, ids = big["folder"], big["ids"]
    page = client.get(f"/folder/{f}").text
    nav = page[page.index("data-scrubber"):page.index("</nav>", page.index("data-scrubber"))]
    rows = catalog.folder_images(con, f)
    for year in ("2001", "2002", "2003", "2004"):
        at = next(i for i, r in enumerate(rows) if r["taken_at"].startswith(year))
        assert f'href="?skip={at}#t{ids[at]}" data-at="{at}" data-ym="{year}"' in nav, year
    assert re.search(r'data-ym="2001" title="2001: [\d,]+ photos" aria-current="true"', nav)
    assert 'class="scrub-year on"' in nav and nav.count('class="scrub-months"') == 3  # 2004 has only January
    assert 'data-ym="2001-02"' in nav and "Feb</a>" in nav
    # opened further down, the year it opens on is the one marked
    later = client.get(f"/folder/{f}?skip=300").text
    assert re.search(r'data-ym="2003" title="2003: [\d,]+ photos" aria-current="true"', later)


def test_marks():
    rows = [{"id": i, "taken_at": t} for i, t in enumerate(
        [None] * 3 + ["1999-12-31T10:00:00"] * 50 + ["2000-01-05T10:00:00"] * 60)]
    marks = pieces.marks(rows)
    assert [(m["label"], m["at"], m["count"]) for m in marks] == [("No date", 0, 3), ("1999", 3, 50), ("2000", 53, 60)]
    assert [(m["label"], m["name"], m["ym"]) for m in marks[2]["months"]] == [("Jan", "January 2000", "2000-01")]
    assert pieces.marks(rows[:60]) == []  # a short list needs none
    assert pieces.marks(rows[3:53]) == []  # nor one month's photos
    months = pieces.marks(rows[3:], months_only=True)  # a year's page: its months, however few its photos
    assert [m["ym"] for m in months] == ["1999-12", "2000-01"] and pieces.marks(rows[40:60], months_only=True)
    assert pieces.current(marks, 60) == ("2000", "2000-01") and pieces.current(marks, 0) == ("", "")


def test_a_year_a_piece_at_a_time(big, client, monkeypatch):
    """A month cut between two pieces keeps one heading: the second piece's photos of it go into its grid."""
    monkeypatch.setattr(pieces, "PIECE", 50)
    with client.app.state.site.own_con() as c:
        rows = catalog.year_images(c, 2002)
    page = client.get("/timeline/2002").text
    assert page.count('class="tile"') == 50 and 'data-scrubber' in page and 'aria-label="Jump to a month"' in page
    months = sorted({int(r["taken_at"][5:7]) for r in rows[:50]})
    assert all(f'<h2 id="m{m}" data-grid-head="m{m}">' in page for m in months)
    piece = client.get("/timeline/2002?skip=50&piece=1").text
    going_on = int(rows[50]["taken_at"][5:7])
    assert going_on == int(rows[49]["taken_at"][5:7])  # the data cuts a month here
    assert f'data-grid-head="m{going_on}"' not in piece and f'data-grid-part="m{going_on}"' in piece
    assert f'<h2 id="m{going_on + 1}" data-grid-head="m{going_on + 1}">' in piece
    # opened in the middle of that month (the scrubber, or Back from a photo): its heading is at the top
    assert f'<h2 id="m{going_on}"' in client.get("/timeline/2002?skip=50").text
    # the months jump to the page from there, to their heading
    first_of_next = next(i for i, r in enumerate(rows) if int(r["taken_at"][5:7]) == going_on + 1)
    assert f'href="?skip={first_of_next}#m{going_on + 1}"' in page


def test_ticking_on_every_list(big, client, con):
    """Folder, year, tag, tour and person pages tick their photos; Select all ticks the whole list, loaded or not."""
    f, ids = big["folder"], big["ids"]
    page = client.get(f"/folder/{f}?select=1").text
    assert 'class="select-form"' in page and page.count('class="tile pick"') == pieces.PIECE
    everything = json.loads(re.search(r'<script type="application/json" data-everything>(.*?)</script>', page)[1])
    assert everything == ids and "Select all 450</button>" in page and ">Clear</button>" in page
    assert client.get(f"/folder/{f}?select=1&skip=200&piece=1").text.count('class="tile pick"') == 200
    year = client.get("/timeline/2002?select=1").text
    assert 'class="select-form"' in year and 'data-select-api="/api/images/remove"' in year
    assert "data-everything" in year
    assert 'href="/timeline/2002?select=1" data-key="s"' in client.get("/timeline/2002").text
    con.execute("INSERT INTO tag(name) VALUES ('kite')")
    con.executemany("INSERT INTO image_tag(image_id, tag_id, source) VALUES (?, (SELECT id FROM tag), 'user')",
                    [(i,) for i in ids[:250]])
    tour = tours.save_tour(con, None, None, tours.clean({"title": "Everything"}), "Everything")
    con.commit()
    assert client.get("/tag/kite").text.count('class="tile"') == pieces.PIECE
    assert client.get("/tag/kite?select=1").text.count('class="tile pick"') == pieces.PIECE
    assert 'href="/tour/%d?select=1"' % tour in client.get(f"/tour/{tour}").text
    picking = client.get(f"/tour/{tour}?select=1").text
    assert 'class="select-form"' in picking and 'class="tile pick"' in picking and 'id="slides"' in picking
    assert 'class="tile pick"' in client.get(f"/tour/{tour}?select=1&skip=0&piece=1").text
    emma = big["emma"]
    person = client.get(f"/person/{emma}?select=1").text
    assert 'class="select-form"' in person and f'/person/{emma}/life/' in person and "?select=1\"" in person
    key = re.search(rf'/person/{emma}/life/([^"?]+)\?select=1', person).group(1)
    assert 'class="tile pick"' in client.get(f"/person/{emma}/life/{key}?select=1").text
    assert 'class="tile pick"' not in client.get(f"/person/{emma}/life/{key}").text


def test_a_download_of_more_than_one_zip_holds_is_refused(big, client, monkeypatch):
    """Select all can tick more than one zip holds (common.ZIP_MOST): the bar says so, and so does the download,
    rather than a zip with fewer photos than were ticked."""
    from lineagebox.web.routes import uploads as upload_routes
    page = client.get(f"/folder/{big['folder']}?select=1").text
    assert re.search(r'data-download data-most="2000"', page)
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "ids.length > most" in script
    monkeypatch.setattr(upload_routes, "ZIP_MOST", 10)
    r = client.post("/download", data={"image": [str(i) for i in big["ids"][:11]]})
    assert r.status_code == 413 and "at most 10" in r.json()["detail"]


def test_tiles_say_who_is_in_them(big, client):
    page = client.get(f"/folder/{big['folder']}").text
    assert 'alt="Photo of Emma Miller, Jack Baker and Lily Miller, Jan 1, 2001"' in page  # faces left to right
    assert 'alt="Video from Jan 3, 2001"' in page
    assert tiles.said(["A", "B", "C", "D", "E", "F"]) == "A, B, C, D and 2 others"
    assert tiles.said(["A", "B"]) == "A and B" and tiles.said(["A"]) == "A"
    assert tiles.alt({"id": 1, "media": "audio", "taken_at": None}, []) == "Sound recording, date not known"
    assert tiles.alt({"id": 1}, ["A"]) == "Photo of A"  # a list that has fewer fields


def test_sharp_tiles(con, cfg, library, client):
    scan(con, cfg)
    ingest(con, cfg)
    sha = con.execute("SELECT sha256 FROM image WHERE error IS NULL ORDER BY id LIMIT 1").fetchone()[0]
    r = client.get(f"/t2/{sha}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert "immutable" in r.headers["cache-control"]
    made = cfg.sharp_thumb_path(sha)
    with Image.open(made) as im, Image.open(cfg.preview_path(sha)) as preview:
        assert max(im.size) == min(cfg.thumb_size * 2, max(preview.size))  # twice the thumbnail, from the preview
    kept = made.stat().st_mtime_ns
    assert client.get(f"/t2/{sha}").content == r.content and made.stat().st_mtime_ns == kept  # kept, not made again
    past = made.stat().st_mtime - 100
    os.utime(made, (past, past))  # a preview newer than the tile (the photo turned, made again): made again
    client.get(f"/t2/{sha}")
    assert made.stat().st_mtime > past + 50
    cfg.preview_path(sha).unlink()  # no preview to make it from: the thumbnail, never an empty tile
    made.unlink()
    stand_in = client.get(f"/t2/{sha}")
    assert stand_in.content == client.get(f"/t/{sha}").content
    assert "immutable" not in stand_in.headers["cache-control"]  # asked for again once there is a preview
    assert client.get(f"/t2/{'0' * 64}").status_code == 404 and client.get("/t2/nope").status_code == 404


def test_a_sharp_tile_waits_its_turn_only_so_long(con, cfg, library, client, monkeypatch):
    """Two are made at once; a request that would wait longer than WAIT gets the thumbnail for now, so a phone asking
    for a screenful doesn't keep the web app's threads from everyone else."""
    scan(con, cfg)
    ingest(con, cfg)
    sha = con.execute("SELECT sha256 FROM image WHERE error IS NULL ORDER BY id LIMIT 1").fetchone()[0]
    monkeypatch.setattr(tiles, "WAIT", 0.05)
    for _ in range(2):  # both turns taken
        tiles.MAKING.acquire()
    try:
        r = client.get(f"/t2/{sha}")
    finally:
        tiles.MAKING.release()
        tiles.MAKING.release()
    assert r.status_code == 200 and r.content == client.get(f"/t/{sha}").content
    assert "no-cache" in r.headers["cache-control"] and not cfg.sharp_thumb_path(sha).exists()
    monkeypatch.setattr(tiles, "_room", lambda cfg: 0)  # the disk nearly full: the thumbnail too, and nothing made
    assert client.get(f"/t2/{sha}").headers["cache-control"] == "private, no-cache"
    monkeypatch.undo()
    assert "immutable" in client.get(f"/t2/{sha}").headers["cache-control"] and cfg.sharp_thumb_path(sha).exists()
    cfg.sharp_thumb_path(sha).unlink()
    cfg.preview_path(sha).write_bytes(b"not a picture")  # a preview that can't be read: the thumbnail, nothing made
    assert client.get(f"/t2/{sha}").headers["cache-control"] == "private, no-cache"
    assert not cfg.sharp_thumb_path(sha).exists()
    assert tiles.alt({"id": 1, "taken_at": "sometime"}, []) == "Photo, date not known"  # not a date as kept


def test_grids_offer_the_sharp_tile(big, client):
    sha = f"{3:064x}"
    page = client.get(f"/folder/{big['folder']}").text
    assert f'src="/t/{sha}" srcset="/t/{sha} 300w, /t2/{sha} 600w" sizes=' in page


def test_keys_of_one_letter_can_be_turned_off(cfg, client):
    """WCAG's character key shortcuts: a switch in the help, which every handler of such keys asks."""
    assert "data-letter-keys" in client.get("/people").text
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "if (e.key.length === 1 && lettersOff()) return;" in script  # ?, /, g, and every data-key
    assert script.count("if (!lettersOff()) {") == 2  # their tooltips, on pages and on the naming queue's cards


def test_the_slideshow_is_a_dialog_and_map_circles_take_the_focus():
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    play = script[script.index("function playTour"):script.index("// Tags page")]
    assert 'createElement("dialog")' in play and "box.showModal()" in play and "opener.focus()" in play
    assert 'aria-label="${attrText(t("Close the slideshow"))}"' in play  # through t(), for other languages
    marks = (STATIC / "map.js").read_text(encoding="utf-8")
    assert marks.count("reachable(L.circleMarker(") == 2 and 'setAttribute("tabindex", "0")' in marks


def test_upload_can_be_reached_by_keyboard():
    page = (TEMPLATES / "upload.html").read_text(encoding="utf-8")
    pickers = re.findall(r'<input type="file"[^>]*>', page)
    assert len(pickers) == 2 and not any(" hidden" in p for p in pickers)
    assert 'data-drop tabindex' not in page  # nothing to do there by keyboard: the pickers are



def test_a_members_tiles_name_only_her_people(site, cfg, con):
    """Cora sees the photo of Sam with Tina's father, but not him: the tile names Sam alone."""
    cora = signed_in(cfg, con, site["who"]["Cora"])
    page = cora.get("/search?q=Sam").text
    assert re.search(r'alt="Photo of Sam, [A-Z][a-z]{2} \d+, \d{4}"', page)
    assert "Hank" not in page
    admin = signed_in(cfg, con, site["who"]["Sam"])
    assert re.search(r'alt="Photo of (Hank and Sam|Sam and Hank),', admin.get("/search?q=Sam").text)
