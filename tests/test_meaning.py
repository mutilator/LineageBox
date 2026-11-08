"""Search by meaning: descriptions and captions read by an embedding model (faked here: words of one idea point the
same way), searches blended with the word match, falling back to words alone when Ollama is away, members kept to
their own photos, and More like this."""
import dataclasses
from datetime import datetime

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from lineagebox import embeddings, search, settings, tagging, worker
from lineagebox.describe import _record
from lineagebox.folders import FolderIndex
from lineagebox.ingest import ingest
from lineagebox.llm import OllamaError
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests import family_fixture as fx
from tests.conftest import LAN, rel
from tests.test_uploads import client

APP = {"X-Requested-With": "lineagebox"}
MODEL = "fake-embed"
# words of one idea: a text points the way of the ideas it has, so "kids sledding" is close to "children on a snowy
# hill" and far from "a dog on the beach", as a real model has them
IDEAS = [("kid", "kids", "children", "child", "boy", "girl"), ("snow", "snowy", "sled", "sledding", "winter"),
         ("hill", "slope"), ("dog", "puppy", "pup"), ("beach", "shore", "sea", "seaside"),
         ("cake", "birthday", "candles")]


class FakeEmbed:
    """Ollama with an embedding model, counting what it is asked."""
    asked: list = []
    away = False

    def __init__(self, cfg, timeout=600):
        self.embed_model = cfg.ollama_embed_model

    def embed(self, texts, timeout=None):
        if FakeEmbed.away:
            raise httpx.ConnectError("the PC is asleep")
        FakeEmbed.asked.append(list(texts))
        out = []
        for text in texts:
            words = text.lower().replace(".", " ").replace(",", " ").split()
            v = np.full(len(IDEAS) + 1, 0.05)
            for n, idea in enumerate(IDEAS):
                v[n] += sum(w in idea for w in words)
            out.append(list(v))
        return out


@pytest.fixture
def meaning(monkeypatch, cfg):
    embeddings.forget()
    FakeEmbed.asked, FakeEmbed.away = [], False
    monkeypatch.setattr(embeddings, "Ollama", FakeEmbed)
    yield dataclasses.replace(cfg, ollama_embed_model=MODEL)
    embeddings.forget()


@pytest.fixture
def lib(con, cfg, library):
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    search.rebuild(con)
    return con


def image_of(con, path):
    return con.execute("SELECT image_id FROM file WHERE path = ?", (path,)).fetchone()[0]


def describe_as(con, image_id, caption, tags=()):
    _record(con, image_id, "fake-vision", {"caption": caption, "tags": list(tags)}, FolderIndex(con))
    con.commit()


def ids(rows):
    return [r["id"] for r in rows]


def test_a_search_finds_what_its_words_mean(lib, meaning):
    con = lib
    snow, dog = image_of(con, rel("2016", "IMG_20160811_182239.jpg")), image_of(con, rel("2012", "Zoo", "DSC_0001.jpg"))
    describe_as(con, snow, "Two children slide down a snowy hill.")
    describe_as(con, dog, "A dog runs along the beach.")
    assert embeddings.count_pending(con, MODEL) == 2
    stats = embeddings.embed_new(con, meaning)
    assert (stats.pending, stats.done) == (2, 2) and embeddings.count_pending(con, MODEL) == 0
    assert FakeEmbed.asked[0] == [embeddings.text_of(con, i) for i in sorted((snow, dog), reverse=True)]  # newest first

    ask = embeddings.asker(meaning)
    assert ids(search.query(con, "kids sledding")) == []  # the words alone find nothing
    assert ids(search.query(con, "kids sledding", meaning=ask)) == [snow]
    assert ids(search.query(con, "a pup by the seaside", meaning=ask)) == [dog]
    assert ids(search.query(con, "beach", meaning=ask))[0] == dog  # a word match, found both ways, comes first
    assert ids(search.query(con, "kids sledding", filters=search.Filters(media="videos"), meaning=ask)) == []
    assert search.context_ids(con, "search:kids sledding", ask) == [snow]  # ←/→ on the photo page, the same

    asked = len(FakeEmbed.asked)
    search.query(con, "kids sledding", meaning=ask)
    assert len(FakeEmbed.asked) == asked  # a search's meaning is kept: paging and ←/→ don't ask again

    # nothing changed: embedding again asks nothing; a caption the family writes is read with the description
    assert embeddings.embed_new(con, meaning).pending == 0
    con.execute("INSERT INTO comment(image_id, kind, text, at) VALUES (?, 'caption', 'Our puppy, first winter', ?)",
                (dog, datetime.now().isoformat(timespec="seconds")))
    con.commit()
    assert embeddings.pending(con, MODEL) == [dog]
    assert embeddings.embed_new(con, meaning).done == 1
    assert FakeEmbed.asked[-1] == [embeddings.text_of(con, dog)]
    assert embeddings.text_of(con, dog).startswith("Our puppy, first winter A dog runs")


def test_ollama_away_finds_words_only_and_stops_asking_a_while(lib, meaning):
    con = lib
    snow = image_of(con, rel("2016", "IMG_20160811_182239.jpg"))
    describe_as(con, snow, "Two children slide down a snowy hill.")
    embeddings.embed_new(con, meaning)
    FakeEmbed.away = True
    ask = embeddings.asker(meaning)
    assert ids(search.query(con, "kids sledding", meaning=ask)) == []
    assert ids(search.query(con, "snowy", meaning=ask)) == [snow]  # words still find what they say
    FakeEmbed.away = False
    assert ids(search.query(con, "children playing in snow", meaning=ask)) == []  # not asked again for a while
    assert embeddings.asker(dataclasses.replace(meaning, ollama_embed_model="")) is None  # switched off
    assert embeddings.asker(dataclasses.replace(meaning, describe_enabled=False)) is None  # no AI at all

    FakeEmbed.away = True  # the worker's stage: Ollama away ends it, the rest waits
    describe_as(con, snow, "Children slide down a snowy slope on a sled.")
    stats = embeddings.embed_new(con, meaning)
    assert stats.gave_up and stats.done == 0 and embeddings.count_pending(con, MODEL) == 1


def test_names_and_years_must_still_match(lib, meaning):
    """'emma sledding': Emma must be in the photo; only what the photo shows is read for its meaning."""
    con = lib
    snow, other = image_of(con, rel("2016", "IMG_20160811_182239.jpg")), image_of(con, rel("transparent.png"))
    describe_as(con, snow, "Two children slide down a snowy hill.")
    describe_as(con, other, "Children play on a snowy slope.")
    tagging.add_person(con, [snow], "Emma Miller")
    con.commit()
    embeddings.embed_new(con, meaning)
    ask = embeddings.asker(meaning)
    assert set(ids(search.query(con, "kids sledding", meaning=ask))) == {snow, other}
    assert ids(search.query(con, "emma sledding", meaning=ask)) == [snow]
    assert FakeEmbed.asked[-1] == ["sledding"]  # the name isn't read for its meaning
    dog = image_of(con, rel("2012", "Zoo", "DSC_0001.jpg"))
    noah, _ = tagging.add_person(con, [dog], "Noah Baker")
    con.commit()
    assert ids(search.query(con, "kids sledding", filters=search.Filters(people=(noah,)), meaning=ask)) == []


def test_the_worker_reads_new_descriptions_and_says_when_the_model_is_missing(lib, meaning, monkeypatch):
    con = lib
    snow = image_of(con, rel("2016", "IMG_20160811_182239.jpg"))
    describe_as(con, snow, "Two children slide down a snowy hill.")
    stages = []

    def stage(name, fn):
        stages.append(name)
        return fn()

    worker._asked_and_meaning(con, meaning, stage, None)
    assert stages == ["meaning"] and embeddings.count_pending(con, MODEL) == 0
    assert "search by meaning: read 1 descriptions" in con.execute("SELECT message FROM activity").fetchall()[-1][0]

    class Missing(FakeEmbed):
        def embed(self, texts, timeout=None):
            raise OllamaError('Ollama answered 404: model "fake-embed" not found, try pulling it first')

    monkeypatch.setattr(embeddings, "Ollama", Missing)
    describe_as(con, snow, "Children slide down a snowy slope.")
    worker._asked_and_meaning(con, meaning, stage, None)
    assert "no model named fake-embed" in settings.problems(con)["meaning"]["message"]
    monkeypatch.setattr(embeddings, "Ollama", FakeEmbed)
    worker._asked_and_meaning(con, meaning, stage, None)
    assert "meaning" not in settings.problems(con)  # it works again


def test_members_find_by_meaning_only_their_own_photos(con, cfg, library, meaning, monkeypatch):
    site = fx.build(con, cfg, library)
    p, photos = site["people"], site["photos"]
    describe_as(con, photos["cora_bart.jpg"], "Two children slide down a snowy hill.")
    describe_as(con, photos["zed.jpg"], "Kids on a snowy slope with a sled.")
    describe_as(con, photos["scenery.jpg"], "A dog on the beach.")
    fx.account(con, p["Sam"], "sam@example.com", "admin")
    cora = fx.account(con, p["Cora"], "cora@example.com")
    settings.save(con, cfg, {"accounts_enabled": True, "ollama_embed_model": MODEL})
    embeddings.embed_new(con, meaning)
    app = webapp.create_app(cfg)
    as_cora = client(cfg, con, cora, app)
    page = as_cora.get("/search", params={"q": "kids sledding"}).text
    assert f'href="/image/{photos["cora_bart.jpg"]}' in page and f'/image/{photos["zed.jpg"]}' not in page
    nobody = TestClient(app, client=LAN)  # accounts on: from the home network without signing in, nothing
    assert f'/image/{photos["cora_bart.jpg"]}' not in nobody.get("/search", params={"q": "kids sledding"}).text

    # More like this: the photos closest by description, of those she sees; a photo she doesn't see is not there
    like = as_cora.get(f"/image/{photos['cora_bart.jpg']}/like").text
    assert f'/image/{photos["zed.jpg"]}"' not in like
    assert as_cora.get(f"/image/{photos['zed.jpg']}/like").status_code == 404


def test_more_like_this(lib, cfg, meaning):
    con = lib
    snow, other = image_of(con, rel("2016", "IMG_20160811_182239.jpg")), image_of(con, rel("transparent.png"))
    dog = image_of(con, rel("2012", "Zoo", "DSC_0001.jpg"))
    describe_as(con, snow, "Two children slide down a snowy hill.")
    describe_as(con, other, "Children play on a snowy slope.")
    describe_as(con, dog, "A dog runs along the beach.")
    embeddings.embed_new(con, meaning)
    assert ids(embeddings.like_this(con, meaning, snow)) == [other]
    assert embeddings.like_this(con, cfg, snow) == []  # search by meaning off: nothing
    settings.save(con, cfg, {"ollama_embed_model": MODEL})
    web = TestClient(webapp.create_app(cfg), client=LAN)
    html = web.get(f"/image/{snow}/like").text
    assert "More like this" in html and f'href="/image/{other}"' in html and f'href="/image/{dog}"' not in html
    assert 'data-later="/image/%d/like"' % snow in web.get(f"/image/{snow}").text
    assert web.get(f"/image/{dog}/like").text.strip() == ""  # nothing close enough: no heading either
    assert web.get("/image/99999/like").status_code == 404


def test_the_model_is_chosen_and_downloaded_on_the_settings_page(con, cfg, monkeypatch):
    from lineagebox import llm

    settings.save(con, cfg, {"ollama_embed_model": "nomic-embed-text"})
    assert settings.current(con, cfg).ollama_embed_model == "nomic-embed-text"
    settings.save(con, cfg, {"ollama_embed_model": ""})  # off: words only
    assert settings.current(con, cfg).ollama_embed_model == ""
    with pytest.raises(settings.Invalid):
        settings.save(con, cfg, {"ollama_embed_model": "two words"})
    settings.save(con, cfg, {"ollama_embed_model": "nomic-embed-text"})
    pulled = []
    monkeypatch.setattr(llm, "pull", lambda now, c, model=None: (pulled.append(model), c.close()))
    web = TestClient(webapp.create_app(cfg), client=LAN)
    assert "Download nomic-embed-text to Ollama" in web.get("/settings").text
    r = web.post("/api/ollama/pull", params={"model": "meaning"}, headers=APP)
    assert "nomic-embed-text" in r.json()["message"]
    for _ in range(100):
        if pulled:
            break
        import time
        time.sleep(0.05)
    assert pulled == ["nomic-embed-text"]
