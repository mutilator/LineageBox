import dataclasses
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from lineagebox import catalog, dedup, describe, search
from lineagebox.folders import FolderIndex, regex_day
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests.conftest import LAN, rel

ZOO = rel("2012", "Zoo")


@pytest.mark.parametrize("name, expected", [
    ("2007-03-08", (2007, 3, 8)),
    ("20160427", (2016, 4, 27)),
    ("Img_20160811_182239", (2016, 8, 11)),
    ("Amy & Tom Wedding 9.21.13", (2013, 9, 21)),
    ("Graduation 5.6.98", (1998, 5, 6)),
    ("Easter13", None),
    ("100NIKON", None),
    ("2012", None),
])
def test_regex_day(name, expected):
    assert regex_day(name) == expected


def _set_folder(con, path, **cols):
    sets = ", ".join(f"{k} = ?" for k in cols)
    con.execute(f"UPDATE folder SET {sets} WHERE path = ?", (*cols.values(), path))


def test_context_merges_every_folder_an_image_is_in(con, cfg):
    scan(con, cfg)
    ingest(con, cfg)
    _set_folder(con, rel("2012"), kind="date", year=2012)
    _set_folder(con, ZOO, kind="place", place="Bob's Petting Zoo", place_type="zoo", people_hint="[]", month=6)
    _set_folder(con, rel("Owen"), kind="person", people_hint='["Owen"]')
    _set_folder(con, rel("Owen", "Zoo"), kind="place", place="Bob's Petting Zoo", people_hint="[]")
    image_id = con.execute("SELECT image_id FROM file WHERE path = ?", (rel(ZOO, "DSC_0001.jpg"),)).fetchone()[0]
    ctx = FolderIndex(con).context(image_id)
    assert sorted(ctx.folders) == sorted([ZOO, rel("Owen", "Zoo")])
    assert ctx.places == ["Bob's Petting Zoo (zoo)"]  # same place from both folders, listed once
    assert ctx.people == ["Owen"]
    assert (ctx.year, ctx.month) == (2012, 6)  # month from the album folder, year from its parent


def test_file_time_dates_yield_to_folder_dates(con, cfg, library):
    from PIL import Image
    (library / "Phone Dump June09").mkdir()
    Image.new("RGB", (30, 30), "orange").save(library / "Phone Dump June09" / "x.png")  # no EXIF: dated by file time
    scan(con, cfg)
    ingest(con, cfg)
    png = con.execute("""SELECT i.id FROM image i JOIN file f ON f.image_id = i.id
                         WHERE f.path = ?""", (rel("Phone Dump June09", "x.png"),)).fetchone()
    _set_folder(con, rel("Phone Dump June09"), kind="date", year=2009, month=6)
    zoo = con.execute("SELECT image_id FROM file WHERE path = ?", (rel(ZOO, "DSC_0001.jpg"),)).fetchone()[0]
    _set_folder(con, rel("2012"), kind="date", year=2005)  # camera (EXIF) dates are never overridden
    from lineagebox.folders import date_from_folders
    assert date_from_folders(con) == 1
    assert tuple(con.execute("SELECT taken_at, taken_at_source FROM image WHERE id = ?", (png["id"],)).fetchone()) \
        == ("2009-06-01T00:00:00", "folder")
    assert con.execute("SELECT taken_at_source FROM image WHERE id = ?", (zoo,)).fetchone()[0] == "exif"


def _image(con, id_, phash, day="2012-06-03", source="exif", pixels=100, removed=False):
    con.execute("""INSERT INTO image(id, sha256, format, width, height, phash, taken_at, taken_at_source)
                   VALUES (?, ?, 'jpeg', ?, 1, ?, ?, ?)""", (id_, f"{id_:064x}", pixels, phash, f"{day}T10:00:00", source))
    con.execute("INSERT OR IGNORE INTO folder(id, path, name) VALUES (1, '', '')")
    con.execute("""INSERT INTO file(path, folder_id, image_id, size, mtime, last_seen, missing)
                   VALUES (?, 1, ?, 1, 1, '', ?)""", (f"{id_}.jpg", id_, int(removed)))


def test_dedup_groups_near_copies_around_the_largest(con):
    base = 0x0F0F0F0F0F0F0F0F
    _image(con, 1, base, pixels=50)
    _image(con, 2, base ^ 0b11, pixels=200)          # distance 2: edited copy, larger -> representative
    _image(con, 3, base ^ 0xFF00, pixels=10)          # distance 8 from 1, but 10 from 2: only within reach of 1
    _image(con, 4, base ^ 0xFFFFFFFF, pixels=10)      # distance 32: unrelated
    _image(con, 5, base ^ 0b1, day="2015-01-01")      # near in hash but shot on another day
    _image(con, 6, base ^ 0b1, day="2019-05-05", source="mtime")  # file-time date: cannot rule it out
    _image(con, 7, base, pixels=500, removed=True)    # bigger, but removed in cleanup: not a member
    groups, members = dedup.group(con)
    rows = dict(con.execute("SELECT id, dup_group_id FROM image").fetchall())
    assert rows[1] == rows[2] == rows[6] == 2
    assert rows[3] is None    # within 8 of image 1 but not of the representative: no chaining
    assert rows[4] is None and rows[5] is None and rows[7] is None
    assert (groups, members) == (1, 3)


class FakeOllama:
    model = "fake-vision"

    def __init__(self, cfg):
        self.calls = 0

    def model_problem(self):
        return None  # it has the model, and the model can see

    def chat_json(self, system, user, schema, images=(), temperature=0.1):
        assert len(images) == 1 and "Catalog this photo." in user
        assert "Owen" not in system
        answer = {"caption": "A young boy feeds a goat through a fence.", "setting": "petting zoo",
                  "indoor_outdoor": "outdoor", "event": None, "activities": ["feeding animals"], "people_count": 1,
                  "age_groups": ["child"], "animals": ["goat"], "objects": ["fence"], "text_visible": None,
                  "photo_type": "candid", "time_of_day": "day", "season": "summer", "quality_issues": [],
                  "tags": ["Petting Zoo", "goat", "goat", "t-shirt"]}
        return answer, None


def test_describe_then_search(con, cfg, library, monkeypatch):
    monkeypatch.setattr(describe, "Ollama", FakeOllama)
    scan(con, cfg)
    ingest(con, cfg)
    search.rebuild(con)
    s = describe.describe(con, cfg, folder=rel("2012"), show_progress=False)
    assert (s.pending, s.done, s.failed) == (1, 1, 0)  # only the photo under 2012\
    image_id = con.execute("SELECT image_id FROM file WHERE path = ?", (rel(ZOO, "DSC_0001.jpg"),)).fetchone()[0]
    a = catalog.analysis_for(con, image_id)
    assert a["caption"].startswith("A young boy") and a["model"] == "fake-vision"
    tags = [r[0] for r in con.execute("""SELECT t.name FROM tag t JOIN image_tag it ON it.tag_id = t.id
                                         WHERE it.image_id = ? ORDER BY t.name""", (image_id,))]
    assert tags == ["goat", "petting zoo", "t shirt"]  # lowercased, de-duplicated, hyphen split
    assert [r["id"] for r in search.query(con, "goats")] == [image_id]          # stemmed
    assert [r["id"] for r in search.query(con, "zoo 2012 june")] == [image_id]  # folder name + date words
    assert search.query(con, 'goat "t-shirt') != []                              # punctuation is not syntax
    assert search.query(con, "elephant") == []
    assert describe.describe(con, cfg, folder=rel("2012"), show_progress=False).pending == 0

    page = TestClient(webapp.create_app(cfg), client=LAN).get(f"/image/{image_id}").text
    assert "A young boy feeds a goat" in page and 'href="/tag/goat"' in page
    results = TestClient(webapp.create_app(cfg), client=LAN).get("/search?q=goat").text
    assert results.count('class="tile"') == 1


def test_describing_never_blocks_the_web_app(con, cfg, library, monkeypatch):
    """While the model works on a photo, another connection (the web app naming someone) can write straight away."""
    cfg = dataclasses.replace(cfg, llm_workers=1)  # one photo at a time: the second waits on the first being saved
    writes = []

    class WebAppMeanwhile(FakeOllama):
        def chat_json(self, *args, **kwargs):
            web = sqlite3.connect(cfg.db_path, timeout=0)
            try:
                web.execute("INSERT INTO person(name) VALUES (?)", (f"P{len(writes)}",))
                web.commit()
                writes.append("ok")
            finally:
                web.close()
            return super().chat_json(*args, **kwargs)

    monkeypatch.setattr(describe, "Ollama", WebAppMeanwhile)
    scan(con, cfg)
    ingest(con, cfg)
    s = describe.describe(con, cfg, show_progress=False)
    assert s.done >= 2 and writes == ["ok"] * s.done


def test_an_answer_that_runs_on_is_asked_again(con, cfg, library, monkeypatch):
    temperatures = []

    class RunsOnOnce(FakeOllama):
        def chat_json(self, system, user, schema, images=(), temperature=0.1):
            temperatures.append(temperature)
            if len(temperatures) == 1:
                raise json.JSONDecodeError("Unterminated string starting at", '{"caption": "a goat a goat a goat', 12)
            return super().chat_json(system, user, schema, images, temperature)

    monkeypatch.setattr(describe, "Ollama", RunsOnOnce)
    scan(con, cfg)
    ingest(con, cfg)
    s = describe.describe(con, dataclasses.replace(cfg, llm_workers=1), limit=1, show_progress=False)
    assert (s.done, s.failed) == (1, 0) and temperatures[0] < temperatures[1]


def test_near_duplicates_share_a_description(con, cfg, library, monkeypatch):
    monkeypatch.setattr(describe, "Ollama", FakeOllama)
    scan(con, cfg)
    ingest(con, cfg)
    ids = [r[0] for r in con.execute("SELECT id FROM image WHERE error IS NULL ORDER BY id LIMIT 2")]
    con.execute("UPDATE image SET dup_group_id = ? WHERE id IN (?, ?)", (ids[0], *ids))
    describe.describe(con, cfg, show_progress=False)
    assert con.execute("SELECT count(*) FROM analysis WHERE image_id = ?", (ids[1],)).fetchone()[0] == 0
    assert catalog.analysis_for(con, ids[1])["image_id"] == ids[0]
    assert [r["id"] for r in search.query(con, "goat")].count(ids[1]) == 0  # one result per group
