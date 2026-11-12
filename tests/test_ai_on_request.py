"""AI on request: photos described again as someone asked (the worker does it; people's words and tags stay), and tags
renamed, merged or taken off every photo by an admin, each with an Undo."""
import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from lineagebox import catalog, changes, describe, llm, progress, search, settings, tagging
from lineagebox.access import EVERYONE
from lineagebox.describe import _record
from lineagebox.folders import FolderIndex
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests import family_fixture as fx
from tests.conftest import LAN, rel
from tests.test_uploads import client

APP = {"X-Requested-With": "lineagebox"}
BLUE = rel("2016", "IMG_20160811_182239.jpg")
ZOO = rel("2012", "Zoo", "DSC_0001.jpg")


@pytest.fixture
def lib(con, cfg, library):
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    search.rebuild(con)
    return con


def image_of(con, path):
    return con.execute("SELECT image_id FROM file WHERE path = ?", (path,)).fetchone()[0]


def found(con, words):
    return [r["id"] for r in search.query(con, words)]


class Seeing:
    """Ollama, with any model it is given, which can look at pictures and says what it was asked with."""
    problems: dict = {}
    answers: dict = {}

    def __init__(self, cfg):
        self.model = cfg.ollama_model

    def model_problem(self):
        return Seeing.problems.get(self.model)

    def chat_json(self, system, user, schema, images=(), temperature=0.1):
        return {"caption": f"Seen again by {self.model}.", "tags": ["giraffe", "fence"], **Seeing.answers}, None


@pytest.fixture
def seeing(monkeypatch):
    Seeing.problems, Seeing.answers = {}, {}
    monkeypatch.setattr(describe, "Ollama", Seeing)
    return Seeing


def test_described_again_as_asked_keeping_what_people_said(lib, cfg, seeing):
    con = lib
    blue = image_of(con, BLUE)
    _record(con, blue, cfg.ollama_model, {"caption": "A tall animal.", "tags": ["giraffe", "close-up"]},
            FolderIndex(con))
    tagging.add(con, [blue], "zoo trip")
    tagging.remove(con, [blue], "giraffe")
    con.execute("INSERT INTO comment(image_id, kind, text, at) VALUES (?, 'caption', 'Owen at the zoo', '2026-01-01')",
                (blue,))
    con.commit()
    web = TestClient(webapp.create_app(cfg), client=LAN)
    page = web.get(f"/image/{blue}").text
    assert f'data-api="/api/images/{blue}/describe-again"' in page and 'name="ai_model"' in page  # an admin picks

    r = web.post(f"/api/images/{blue}/describe-again", json={"ai_model": "gemma3:12b"}, headers=APP)
    assert r.status_code == 200 and "with gemma3:12b" in r.json()["message"], r.text
    assert settings.get(con, "run_requested")  # the worker comes at once
    assert [(q["id"], q["model"]) for q in describe.requests(con)] == [(blue, "gemma3:12b")]
    assert {"key": "describe again", "label": "Photos asked to be described again (AI)", "left": 1, "total": None}         in progress.counts(con, cfg)["waiting"]

    stats = describe.describe_requests(con, cfg)
    assert (stats.pending, stats.done) == (1, 1) and describe.count_requests(con) == 0
    a = catalog.analysis_for(con, blue)
    assert (a["caption"], a["model"], a["asked"]) == ("Seen again by gemma3:12b.", "gemma3:12b", 1)
    assert [t["name"] for t in tagging.for_image(con, blue)] == ["fence", "zoo trip"]  # giraffe stays off
    assert con.execute("SELECT text FROM comment WHERE image_id = ?", (blue,)).fetchone()[0] == "Owen at the zoo"
    # the Settings page's model changes: a photo described as asked keeps its description
    assert blue not in [r["id"] for r in describe.pending(con, "another-model")]

    # the same model as the Settings page's is no other model
    web.post(f"/api/images/{blue}/describe-again", json={"ai_model": cfg.ollama_model}, headers=APP)
    assert describe.requests(con)[0]["model"] is None


def test_ticked_photos_described_again_and_a_model_that_cant(lib, cfg, seeing):
    con = lib
    blue, zoo = image_of(con, BLUE), image_of(con, ZOO)
    web = TestClient(webapp.create_app(cfg), client=LAN)
    r = web.post("/api/selection/describe-again", json={"image_ids": [blue, zoo], "ai_model": "llama3",
                                                        "add_tag": "", "reason": "x"}, headers=APP)
    assert "2 photos will be described again with llama3" in r.json()["message"]
    seeing.problems["llama3"] = llm.ModelProblem("llama3 can't look at pictures.", text_ok=True)
    stats = describe.describe_requests(con, cfg)
    assert (stats.dropped, stats.done) == (2, 0) and describe.count_requests(con) == 0  # dropped, said in the log

    web.post("/api/selection/describe-again", json={"image_ids": [blue, zoo]}, headers=APP)
    seeing.problems[cfg.ollama_model] = llm.ModelProblem("no such model")
    assert describe.describe_requests(con, cfg).done == 0 and describe.count_requests(con) == 2  # waiting for it
    seeing.problems.clear()
    assert describe.describe_requests(con, cfg).done == 2

    off = settings.save(con, cfg, {"describe_enabled": False})
    assert off.describe_enabled is False
    r = web.post(f"/api/images/{blue}/describe-again", headers=APP)
    assert r.status_code == 409 and "switched off" in r.json()["detail"]


def test_members_ask_with_the_usual_model_for_their_own_photos(con, cfg, library, seeing):
    site = fx.build(con, cfg, library)
    p, photos = site["people"], site["photos"]
    fx.account(con, p["Sam"], "sam@example.com", "admin")
    cora = fx.account(con, p["Cora"], "cora@example.com")
    settings.save(con, cfg, {"accounts_enabled": True})
    for name in ("cora_bart.jpg", "zed.jpg"):
        _record(con, photos[name], cfg.ollama_model, {"caption": "A day out."}, FolderIndex(con))
    con.commit()
    as_cora = client(cfg, con, cora)
    page = as_cora.get(f"/image/{photos['cora_bart.jpg']}").text
    assert "describe-again" in page and 'name="ai_model"' not in page  # no model names on members' pages
    r = as_cora.post(f"/api/images/{photos['cora_bart.jpg']}/describe-again", json={"ai_model": "gemma3"}, headers=APP)
    assert r.status_code == 200 and "gemma3" not in r.json()["message"]
    assert [(q["id"], q["model"]) for q in describe.requests(con)] == [(photos["cora_bart.jpg"], None)]
    assert as_cora.post(f"/api/images/{photos['zed.jpg']}/describe-again", headers=APP).status_code == 404
    r = as_cora.post("/api/selection/describe-again", json={"image_ids": [photos["zed.jpg"]]}, headers=APP)
    assert r.status_code == 404 and len(describe.requests(con)) == 1
    assert as_cora.get("/api/ai/models").status_code in (403, 404)


def test_a_tag_renamed_across_the_library_and_back(lib, cfg):
    con = lib
    blue, zoo = image_of(con, BLUE), image_of(con, ZOO)
    _record(con, blue, "fake", {"caption": "An animal.", "tags": ["giraffe", "fence"]}, FolderIndex(con))
    tagging.add(con, [zoo], "giraffe")
    con.commit()
    web = TestClient(webapp.create_app(cfg), client=LAN)
    page = web.get("/tag/giraffe").text
    assert "Rename, merge or remove this tag" in page and 'data-api="/api/tags/remove-everywhere"' in page
    r = web.post("/api/tags/rename", json={"tag": "giraffe", "to": "Tall Animal"}, headers=APP)
    assert r.status_code == 200 and r.json()["go"] == "/tag/tall%20animal", r.text
    assert {x["id"] for x in catalog.tag_images(con, "tall animal")} == {blue, zoo}
    assert catalog.tag_images(con, "giraffe") == []
    assert set(found(con, "tall animal")) == {blue, zoo} and found(con, "giraffe") == []
    _record(con, blue, "fake", {"caption": "An animal.", "tags": ["giraffe"]}, FolderIndex(con))  # described again
    assert [t["name"] for t in tagging.for_image(con, blue)] == ["tall animal"]  # the AI's word is the tag now

    changes.undo(con, r.json()["undo"])  # the rename, put back
    assert {x["id"] for x in catalog.tag_images(con, "giraffe")} == {blue, zoo}
    assert set(found(con, "giraffe")) == {blue, zoo}
    assert tagging.aliases(con) == {}


def test_tags_merged_keep_the_strongest_say_of_each_photo(lib, cfg):
    con = lib
    blue, zoo = image_of(con, BLUE), image_of(con, ZOO)
    _record(con, blue, "fake", {"caption": "Dogs.", "tags": ["puppy", "dog"]}, FolderIndex(con))
    _record(con, zoo, "fake", {"caption": "A dog.", "tags": ["dog"]}, FolderIndex(con))
    tagging.add(con, [blue], "puppy")      # given by hand: stays given by hand
    tagging.remove(con, [zoo], "dog")      # taken off by hand: stays off
    tagging.add(con, [zoo], "puppy")
    con.commit()
    web = TestClient(webapp.create_app(cfg), client=LAN)
    asked = web.post("/api/tags/rename", json={"tag": "puppy", "to": "dog"}, headers=APP).json()
    assert asked["again"] == {"merge": True} and "is a tag already, on 1 photos" in asked["confirm"]
    assert catalog.tag_images(con, "puppy")  # nothing done before the yes
    r = web.post("/api/tags/rename", json={"tag": "puppy", "to": "dog", "merge": True}, headers=APP)
    assert r.status_code == 200, r.text
    rows = dict(con.execute("""SELECT it.image_id, it.source FROM image_tag it JOIN tag t ON t.id = it.tag_id
                               WHERE t.name = 'dog'"""))
    assert rows == {blue: "user", zoo: "not"}
    assert con.execute("SELECT count(*) FROM tag WHERE name = 'puppy'").fetchone()[0] == 0
    _record(con, zoo, "fake", {"caption": "A puppy.", "tags": ["puppy"]}, FolderIndex(con))
    assert [t["name"] for t in tagging.for_image(con, zoo)] == []  # puppy is dog now, and dog stays off it
    changes.undo(con, r.json()["undo"])
    assert {x["id"] for x in catalog.tag_images(con, "puppy")} == {blue, zoo}


def test_a_tag_taken_off_every_photo_stays_off(lib, cfg, library):
    con = lib
    blue, zoo = image_of(con, BLUE), image_of(con, ZOO)
    _record(con, blue, "fake", {"caption": "A photo.", "tags": ["close-up", "fence"]}, FolderIndex(con))
    tagging.add(con, [zoo], "close up")
    con.commit()
    web = TestClient(webapp.create_app(cfg), client=LAN)
    r = web.post("/api/tags/remove-everywhere", json={"tag": "close up"}, headers=APP)
    assert r.status_code == 200 and "off 2 photos" in r.json()["message"]
    assert catalog.tag_images(con, "close up") == [] and found(con, "close") == []
    _record(con, blue, "fake", {"caption": "A photo.", "tags": ["close-up"]}, FolderIndex(con))
    _record(con, zoo, "fake", {"caption": "A photo.", "tags": ["close up", "fence"]}, FolderIndex(con))
    assert catalog.tag_images(con, "close up") == []  # the AI doesn't give it again, to any photo
    tagging.add(con, [zoo], "close up")  # but someone may, by hand
    assert [x["id"] for x in catalog.tag_images(con, "close up")] == [zoo]
    assert web.post("/api/tags/remove-everywhere", json={"tag": "nothing"}, headers=APP).status_code == 400


def test_tags_across_the_library_are_for_admins(con, cfg, library):
    site = fx.build(con, cfg, library)
    p = site["people"]
    fx.account(con, p["Sam"], "sam@example.com", "admin")
    cora = fx.account(con, p["Cora"], "cora@example.com")
    settings.save(con, cfg, {"accounts_enabled": True})
    tagging.add(con, [site["photos"]["cora_bart.jpg"]], "picnic")
    con.commit()
    as_cora = client(cfg, con, cora)
    assert 'data-api="/api/tags/rename"' not in as_cora.get("/tag/picnic").text
    for path, body in (("/api/tags/rename", {"tag": "picnic", "to": "lunch"}),
                       ("/api/tags/remove-everywhere", {"tag": "picnic"})):
        assert as_cora.post(path, json=body, headers=APP).status_code in (403, 404)
    assert catalog.tag_images(con, "picnic")
