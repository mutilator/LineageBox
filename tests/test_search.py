"""Search: partial words, filters with or without words, sorting, what was read off photos and written about them,
suggestions as a search is typed (a member's only of what they see), what to try when nothing is found, and the
photo page's ←/→ walking the same results in the same order."""
import re
import shutil
from datetime import date
from urllib.parse import quote

import numpy as np
import pytest
from fastapi.testclient import TestClient

from lineagebox import describe, family, memories, people, search, settings
from lineagebox.describe import _record
from lineagebox.folders import FolderIndex
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests import family_fixture as fx
from tests.conftest import LAN, rel, write_jpeg
from tests.test_access_web import signed_in, site  # noqa: F401 - site is a fixture

# file -> (folder, EXIF date or None, the AI's answer)
SHOTS = {
    "cake.jpg": ("Party", "2018:03:01 15:00:00", {"caption": "A birthday cake with candles on a table.",
                                                  "tags": ["birthday", "cake"], "event": "birthday party"}),
    "walk.jpg": ("Party", "2018:03:02 15:00:00", {"caption": "Children do a cakewalk in a hall.",
                                                  "tags": ["game"]}),
    "sign.jpg": ("Trips/Lake", "2015:07:04 10:00:00", {"caption": "A wooden sign by a road.", "tags": ["sign"],
                                                       "text_visible": "Welcome to Pine Hollow"}),
    "zoo.jpg": ("Trips/Zoo", "2014:06:03 10:00:00", {"caption": "A giraffe eats leaves.", "tags": ["giraffe"]}),
    "beach.jpg": ("Trips/Beach", "2014:08:10 10:00:00", {"caption": "Two children on a beach.", "tags": ["beach"]}),
    "old.jpg": ("Scans", None, {"caption": "An old photo of a family on a porch.", "tags": ["porch"]}),
}


def image_of(con, name):
    folder = SHOTS[name][0]
    return con.execute("SELECT image_id FROM file WHERE path = ?", (rel(*folder.split("/"), name),)).fetchone()[0]


def face(con, image_id, person_id, x=0.1, how="user"):
    return con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding, person_id,
                                           assigned_by) VALUES (?, ?, 0.1, ?, 0.6, 0.9, 25, ?, ?, ?)""",
                       (image_id, x, x + 0.4, np.zeros(512, np.float32).tobytes(), person_id, how)).lastrowid


@pytest.fixture
def lib(con, cfg, library):
    """The photos of SHOTS, described; Emma in the cake, the walk and the sign, Jack in the walk and the sign; a
    face nobody named on the beach; the zoo's folder named for a place. Returns file name -> image id."""
    for old in library.iterdir():  # conftest's photos: only these here
        shutil.rmtree(old) if old.is_dir() else old.unlink()
    for n, (name, (folder, taken, _)) in enumerate(SHOTS.items()):
        write_jpeg(library / folder / name, size=(200, 160), color=(30 * n, 200 - 20 * n, 90), taken=taken)
    scan(con, cfg)
    ingest(con, cfg)
    ids = {name: image_of(con, name) for name in SHOTS}
    folders = FolderIndex(con)
    for name, (_, _, answer) in SHOTS.items():
        _record(con, ids[name], "fake", answer, folders)
    emma, jack = people.person_id(con, "Emma Miller"), people.person_id(con, "Jack Miller")
    for name in ("cake.jpg", "walk.jpg", "sign.jpg"):
        face(con, ids[name], emma)
    for name in ("walk.jpg", "sign.jpg"):
        face(con, ids[name], jack, x=0.5)
    face(con, ids["beach.jpg"], None, how=None)
    con.execute("UPDATE folder SET place = 'Riverside Zoo', place_type = 'zoo', kind = 'place' WHERE name = 'Zoo'")
    con.commit()
    search.rebuild(con)
    return {**ids, "emma": emma, "jack": jack}


def found(con, text, **filters):
    return [r["id"] for r in search.query(con, text, filters=search.Filters(**filters))]


def test_partial_words_and_what_was_read_off_photos(con, lib):
    assert found(con, "birthd") == [lib["cake.jpg"]]  # the last word typed partly
    assert found(con, "cake")[0] == lib["cake.jpg"] and lib["walk.jpg"] in found(con, "cake")  # whole word first
    assert found(con, "cak table") == []  # only the last word may be partly typed
    assert found(con, "pine hollow") == [lib["sign.jpg"]]  # words the AI read off the photo
    assert found(con, "elephant") == []


def test_filters_with_and_without_words(con, lib):
    emma, jack = lib["emma"], lib["jack"]
    assert found(con, "", people=(emma, jack)) == [lib["sign.jpg"], lib["walk.jpg"]]  # together; oldest first
    assert found(con, "", people=(emma, jack), sort="newest") == [lib["walk.jpg"], lib["sign.jpg"]]
    assert found(con, "", people=(emma,), year_from=2018, year_to=2018) == [lib["cake.jpg"], lib["walk.jpg"]]
    assert found(con, "", year_from=2014, year_to=2015) == [lib["zoo.jpg"], lib["beach.jpg"], lib["sign.jpg"]]
    assert found(con, "", unnamed=True) == [lib["beach.jpg"]]
    assert found(con, "", undated=True) == [lib["old.jpg"]]
    assert found(con, "", place="Riverside Zoo") == [lib["zoo.jpg"]]
    assert found(con, "giraffe", place="riverside zoo") == [lib["zoo.jpg"]]
    assert found(con, "beach", place="Riverside Zoo") == []
    con.execute("UPDATE image SET media = 'video' WHERE id = ?", (lib["beach.jpg"],))
    assert found(con, "", media="videos") == [lib["beach.jpg"]]
    assert lib["beach.jpg"] not in found(con, "children", media="photos")
    # a date set by hand as a span is in each of its years
    con.execute("""UPDATE image SET taken_at = '1950-01-01T00:00:00', taken_at_source = 'user', date_text = '1950s',
                   date_from = '1950-01-01', date_to = '1959-12-31' WHERE id = ?""", (lib["old.jpg"],))
    assert found(con, "", year_from=1955, year_to=1955) == [lib["old.jpg"]] and found(con, "", undated=True) == []
    assert found(con, "") == [] and found(con, "", sort="newest") == []  # nothing asked, nothing found


def test_the_address_holds_the_filters():
    f = search.Filters.from_params({"people": "4,x,2,4", "from": "2016", "to": "2012", "media": "videos",
                                    "unnamed": "1", "sort": "newest", "place": "  Pine   Hollow "})
    assert f == search.Filters(people=(4, 2), year_from=2012, year_to=2016, media="videos", unnamed=True,
                               sort="newest", place="Pine Hollow")
    assert search.Filters.from_params({"media": "pdfs", "sort": "random", "from": "99"}) == search.Filters()
    ctx = search.context("zoo", f)
    assert ctx.startswith("search:?") and search.from_context(ctx[7:]) == ("zoo", f)
    assert search.context("zoo") == "search:zoo" and search.from_context("zoo") == ("zoo", search.Filters())
    assert search.from_context(search.context("?zoo")[7:]) == ("?zoo", search.Filters())


def test_what_the_family_writes_and_documents_are_searched(con, lib):
    """Captions and comments of any copy in a group of near-duplicates find the one search shows, and so does a
    document's text read into the tree. The catalog marks the photos to index again (search_stale), however the rows
    were written; the worker's search stage does it, and saving through the site does it at once."""
    rep, copy = lib["zoo.jpg"], lib["beach.jpg"]
    con.execute("UPDATE image SET dup_group_id = ? WHERE id IN (?, ?)", (rep, rep, copy))
    con.execute("""INSERT INTO comment(image_id, kind, text, at) VALUES (?, 'caption', 'Grandpa Joe at the gate',
                   '2026-01-01T10:00:00')""", (copy,))
    con.execute("""INSERT INTO comment(image_id, kind, text, at) VALUES (?, 'comment', 'I remember the ice cream!',
                   '2026-01-02T10:00:00')""", (rep,))
    con.execute("""INSERT INTO family_reading(created_at, title, image_id, text, state)
                   VALUES ('2026-01-01', 'Obituary of Ruth', ?, 'Ruth was born in Millbrook', 'ready')""",
                (lib["old.jpg"],))
    con.commit()
    assert found(con, "grandpa") == []  # not yet indexed again
    assert {r[0] for r in con.execute("SELECT image_id FROM search_stale")} == {rep, copy, lib["old.jpg"]}
    assert search.index_new(con) >= 3
    assert con.execute("SELECT count(*) FROM search_stale").fetchone()[0] == 0
    assert found(con, "grandpa joe") == [rep] and found(con, "ice cream") == [rep]  # one of the group
    assert found(con, "millbrook") == [lib["old.jpg"]] and found(con, "obituary ruth") == [lib["old.jpg"]]
    # the family's words count above the AI's caption
    con.execute("""INSERT INTO comment(image_id, kind, text, at) VALUES (?, 'caption', 'Autumn leaves everywhere',
                   '2026-01-03T10:00:00')""", (lib["sign.jpg"],))
    search.index_group(con, lib["sign.jpg"], FolderIndex(con))  # as the site does once a caption is saved
    assert con.execute("SELECT count(*) FROM search_stale").fetchone()[0] == 0
    assert found(con, "leaves") == [lib["sign.jpg"], lib["zoo.jpg"]]  # the zoo's: in the AI's caption
    con.execute("DELETE FROM comment WHERE kind = 'caption' AND image_id = ?", (copy,))
    search.index_new(con)
    assert found(con, "grandpa") == []


def test_more_than_a_thousand_results_keep_their_arrows(con, cfg):
    """The photo page's ←/→ re-run the search with as many results as the search page shows, in its order."""
    root = con.execute("INSERT INTO folder(path, parent_id, name) VALUES ('', NULL, '')").lastrowid
    folder = con.execute("INSERT INTO folder(path, parent_id, name) VALUES ('lib', ?, 'lib')", (root,)).lastrowid
    for n in range(1200):
        i = con.execute("""INSERT INTO image(sha256, format, width, height, taken_at, taken_at_source)
                           VALUES (?, 'jpeg', 100, 100, ?, 'exif')""",
                        (f"{n:064x}", f"{2000 + n % 20}-01-01T{n % 24:02d}:00:00")).lastrowid
        con.execute("""INSERT INTO file(path, folder_id, image_id, size, mtime, last_seen)
                       VALUES (?, ?, ?, 1, 0, '2026-01-01')""", (f"lib/{n}.jpg", folder, i))
        con.execute("INSERT INTO search_fts(rowid, image_id, caption) VALUES (?, ?, 'a lighthouse by the sea')", (i, i))
    con.commit()
    f = search.Filters(year_from=2005, sort="newest")
    shown = [r["id"] for r in search.query(con, "lighthouse", filters=f)]
    assert len(shown) == 900 and search.context_ids(con, search.context("lighthouse", f)) == shown
    assert len(search.context_ids(con, "search:lighthouse")) == 1200  # past the 1,000th


def test_the_search_page(con, cfg, lib):
    client = TestClient(webapp.create_app(cfg), client=LAN)
    emma, jack = lib["emma"], lib["jack"]
    page = client.get(f"/search?people={emma},{jack}&sort=newest").text
    assert "<h1>Emma Miller, Jack Miller</h1>" in page
    tiles = re.findall(r'class="tile" href="/image/(\d+)\?ctx=([^"]+)"', page)
    assert [int(i) for i, _ in tiles] == [lib["walk.jpg"], lib["sign.jpg"]]
    ctx = tiles[0][1]
    assert ctx == quote(search.context("", search.Filters(people=(emma, jack), sort="newest")), safe="")
    # each chip takes its filter off; the others are links that add one
    assert f'href="/search?people={jack}&amp;sort=newest" title="Take this filter off">Emma Miller' in page
    assert f'href="/search?people={emma}%2C{jack}&amp;media=videos"' not in page  # the order stays
    assert f'href="/search?people={emma}%2C{jack}&amp;media=videos&amp;sort=newest"' in page
    assert 'aria-current="true">Newest first' in page and "Best match" not in page  # no words: no best match
    # the photo page walks the same results, in the same order, and goes back to the same search
    photo = client.get(f"/image/{lib['walk.jpg']}?ctx={ctx}").text
    assert f'/image/{lib["sign.jpg"]}?ctx=' in photo and "1 of 2" in photo.replace("&nbsp;", " ")
    back = re.search(r'href="(/search\?[^"]+)" data-back', photo).group(1).replace("&amp;", "&")
    assert back == f"/search?people={emma}%2C{jack}&sort=newest"  # straight back to the same search
    r = client.get(f"/search?q={quote(back[7:])}", follow_redirects=False)  # an old link of that kind still works
    assert r.status_code == 303 and r.headers["location"] == back
    # words and filters together, and pages of them keep both
    page = client.get(f"/search?q=cake&people={emma}").text
    assert "<h1>“cake”</h1>" in page and "Best match" in page and "Emma Miller" in page
    assert f'href="/search?q=cake&amp;people={emma}&amp;select=1"' in page
    # a search with no words and no filters is the page that says how
    assert "Type words into the box above" in client.get("/search").text
    assert 'data-search-suggest="/api/search/suggest"' in page and "data-search-keep" in page


def test_nothing_found_says_what_to_try(con, cfg, lib):
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get("/search?q=brithday").text
    assert "Nothing found" in page and "“birthday” instead of “brithday”" in page
    assert 'href="/search?q=birthday"' in page
    page = client.get("/search?q=giraffe+zebra").text
    assert "Without “zebra”" in page and 'href="/search?q=giraffe"' in page
    page = client.get("/search?q=giraffe&media=videos").text
    assert "Without “Videos only”" in page and 'href="/search?q=giraffe"' in page


def test_suggestions_as_a_search_is_typed(con, cfg, lib):
    ann, grace = people.person_id(con, "Ann Miller"), people.person_id(con, "Grace Baker")
    family.link(con, lib["emma"], "mother", ann)
    family.link(con, ann, "mother", grace)
    settings.put(con, "me", lib["emma"])
    con.commit()
    client = TestClient(webapp.create_app(cfg), client=LAN)
    ask = lambda q, **more: client.get("/api/search/suggest", params={"q": q, **more}).json()["items"]  # noqa: E731
    emma = ask("emm")
    assert [(i["kind"], i["label"], i["add"], i["keep"]) for i in emma] == [
        ("person", "Emma Miller", {"people": str(lib["emma"])}, "")]
    assert "3 photos" in emma[0]["about"]
    assert [i["keep"] for i in ask("beach emma mil")] == ["beach"]  # "emma mil" is her
    assert [i["label"] for i in ask("mill")] == ["Emma Miller", "Jack Miller", "Ann Miller"]  # most photos first
    assert ask("mill", people=str(lib["emma"]))[0]["label"] == "Jack Miller"  # asked for already
    grandma = [i for i in ask("grandm") if i["kind"] == "family"]
    assert [(i["label"], i["words"]) for i in grandma] == [("grandmother", "grandmother")]
    assert [i["label"] for i in ask("grandma") if i["kind"] == "family"] == ["grandmother"]
    assert [(i["label"], i["add"]) for i in ask("rivers") if i["kind"] == "place"] == [
        ("Riverside Zoo", {"place": "Riverside Zoo"})]
    assert [(i["label"], i["words"], i["keep"]) for i in ask("cake birt") if i["kind"] == "tag"] == [
        ("birthday", "birthday", "cake")]
    assert [(i["kind"], i["label"]) for i in ask("201")] == [("year", "2018"), ("year", "2015"), ("year", "2014")]
    assert ask("2014")[0]["add"] == {"from": "2014", "to": "2014"} and ask("1999") == []
    assert ask("") == [] and ask("e") == []


def test_a_members_search_never_names_who_she_cant_see(site, cfg, con):  # noqa: F811
    p = site["people"]
    con.execute("UPDATE folder SET place = ? WHERE path LIKE '%ZedSecret'", (f"{fx.SECRET} lake",))
    con.execute("UPDATE folder SET place = 'Picnic Park' WHERE path LIKE '%Picnic'")
    con.commit()
    c = signed_in(cfg, con, site["who"]["Cora"])
    hidden = [n for n in fx.PEOPLE if n not in fx.SEES["Cora"]]
    for typed in [*hidden, *(n[:2] for n in hidden), fx.SECRET[:4], "zeb", "lake"]:
        text = c.get("/api/search/suggest", params={"q": typed}).text
        assert fx.SECRET not in text, typed
        for name in hidden:
            assert not re.search(rf"\b{name}\b", text), (typed, name)
    assert [i["label"] for i in c.get("/api/search/suggest?q=cor").json()["items"]] == ["Cora"]
    assert any(i["label"] == "Picnic Park" for i in c.get("/api/search/suggest?q=picn").json()["items"])
    # someone she can't see, asked for by their id, is as if not asked for
    for who in ("Hank", "Zed"):
        page = c.get(f"/search?people={p[who]}").text
        assert who not in page and "Type words into the box above" in page
        both = c.get(f"/search?people={p['Cora']},{p[who]}").text
        assert who not in both and f"/image/{site['photos']['cora_bart.jpg']}" in both
    ctx = quote(search.context("", search.Filters(people=(p["Hank"],))), safe="")
    photo = c.get(f"/image/{site['photos']['hank_sam.jpg']}?ctx={ctx}")
    assert photo.status_code == 200 and " of 1" not in photo.text


def test_a_video_is_described_with_what_is_said_in_it(con, cfg, lib, monkeypatch):
    clip = lib["beach.jpg"]
    con.execute("UPDATE image SET media = 'video' WHERE id = ?", (clip,))
    con.execute("DELETE FROM analysis WHERE image_id = ?", (clip,))
    said = "Happy birthday dear Owen! " + "la " * 3000
    con.execute("""INSERT INTO transcript(image_id, model, version, created_at, text, segments, raw)
                   VALUES (?, 'fake', 1, '2026-01-01', ?, '[]', '{}')""", (clip, said))
    con.commit()
    assert describe.said_in(con, clip).startswith("Happy birthday dear Owen!")
    assert len(describe.said_in(con, clip)) <= describe.SAID_CHARS + 2 and describe.said_in(con, lib["cake.jpg"]) == ""
    asked = []

    class Fake:
        model = "fake-vision"

        def __init__(self, cfg):
            pass

        def chat_json(self, system, user, schema, images=(), temperature=0.1):
            asked.append((system, user))
            return {"caption": "Children sing to a boy at a party.", "tags": ["party"], "event": "birthday party"}, None

    monkeypatch.setattr(describe, "Ollama", Fake)
    s = describe.describe(con, cfg, show_progress=False, media="video")
    assert (s.done, s.failed) == (1, 0)
    system, user = asked[0]
    assert system == describe.SYSTEM_VIDEO and "Said in the video" in user and "Happy birthday dear Owen!" in user
    assert found(con, "sing") == [clip]


def test_the_ais_occasions_name_days_of_photos(con, cfg, library):
    """What the AI saw the photos were of names their day, when enough of them say it: as a tag of its words
    ("birthday party": a birthday), or in its own words on half of them, with the year."""
    days = {"party": date(2019, 5, 4), "game": date(2019, 9, 14), "plain": date(2019, 11, 2)}
    events = {"party": ["birthday party", "birthday party", None, "none", "party"],
              "game": ["soccer game", "Soccer game.", "soccer game", None, "picnic"],
              "plain": ["soccer game", None, None, None, None]}
    for n, (key, day) in enumerate(days.items()):
        for i in range(5):
            write_jpeg(library / "Year" / f"{key}{i}.jpg", size=(150, 100), color=(60 * n, 40 * i, 120),
                       taken=f"{day:%Y:%m:%d} 1{i}:00:00")
    scan(con, cfg)
    ingest(con, cfg)
    con.execute("UPDATE image SET sharpness = 100")
    folders = FolderIndex(con)
    for key, said in events.items():
        for i, event in enumerate(said):
            image_id = con.execute("SELECT image_id FROM file WHERE path = ?",
                                   (rel("Year", f"{key}{i}.jpg"),)).fetchone()[0]
            _record(con, image_id, "fake", {"caption": "Some people.", "tags": ["people"], "event": event}, folders)
    con.commit()
    found = memories.year_events(con, 2019, date(2026, 1, 1))
    assert [(m["title"], m["why"]) for m in found] == [
        ("A birthday, 2019", "2 of the 5 photos are described by the AI as birthday"),
        ("Soccer game, 2019", "the AI described 3 of the 5 photos as soccer game"),
        ("November 2, 2019", "")]
