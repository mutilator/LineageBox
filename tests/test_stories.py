"""Recorded stories: kept from the browser's recording or a phone's file, played, written down by Whisper, found by
search, corrected and taken away by whoever recorded them (or an admin), undone, tidied away, backed up and exported;
and who may record, hear and change them when the family has accounts."""
import io
import json
import wave
import zipfile
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from lineagebox import accounts, backup, export, people, search, settings, speech, stories
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests import family_fixture as fx
from tests.conftest import LAN

APP = {"X-Requested-With": "lineagebox"}


def wav(seconds: float = 1.0) -> bytes:
    """A short WAV file, as a phone's recorder might save one (no ffmpeg needed to make it)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x01" * int(16000 * seconds))
    return buf.getvalue()


def heard(text: str):
    """A Whisper that hears this, and sound taken out of any file without ffmpeg."""
    def fake(self, data):
        return {"model": "fake-whisper", "language": "en", "duration": 1.0, "segments": [
            {"start": 0.0, "end": 1.0, "text": f" {text}", "avg_logprob": -0.2, "no_speech_prob": 0.01}]}
    return fake


def record(client, url, data=None, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return client.post(f"{url}{'&' if query else ''}{query}", content=wav() if data is None else data,
                       headers={**APP, "Content-Type": "audio/wav"})


@pytest.fixture
def photo(con, cfg, library):
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    return con.execute("SELECT id FROM image WHERE error IS NULL ORDER BY id LIMIT 1").fetchone()[0]


def test_a_photos_story_is_kept_played_heard_found_and_taken_away(cfg, con, photo, monkeypatch):
    home = TestClient(webapp.create_app(cfg), client=LAN)
    r = record(home, f"/api/stories?image={photo}", title="The%20zoo%20trip", seconds="1.0")
    assert r.status_code == 200, r.text
    story, undo = r.json()["id"], r.json()["undo"]
    row = con.execute("SELECT * FROM story WHERE id = ?", (story,)).fetchone()
    assert row["image_id"] == photo and row["mime"] == "audio/wav" and row["title"] == "The zoo trip"
    assert (cfg.data_dir / "stories" / row["file"]).is_file() and undo
    page = home.get(f"/image/{photo}").text
    assert "The zoo trip" in page and f'src="/story/{story}"' in page and "Not written down yet" in page
    assert 'data-story-form="/api/stories?image=' in page and 'accept="audio/*" capture' in page
    sound = home.get(f"/story/{story}")
    assert sound.status_code == 200 and sound.headers["content-type"] == "audio/wav" and sound.content == wav()
    assert home.get(f"/story/{story}", headers={"Range": "bytes=0-9"}).status_code == 206

    # what isn't a recording, or is nothing at all, is refused
    assert record(home, f"/api/stories?image={photo}", data=b"<html>not sound</html>").status_code == 400
    assert record(home, f"/api/stories?image={photo}", data=b"").status_code == 400
    assert record(home, "/api/stories?image=99999").status_code == 404
    assert record(home, f"/api/stories?image={photo}&person=1").status_code == 400

    # Whisper writes it down, and search finds the photo by it
    monkeypatch.setattr(speech.video, "audio_wav", lambda path: wav())
    monkeypatch.setattr(speech.Whisper, "transcribe", heard("We fed the elephants peanuts"))
    assert speech.count_pending(con) == 1
    s = speech.transcribe(con, cfg)
    assert (s.done, s.stories) == (1, 1) and speech.count_pending(con) == 0
    search.index_new(con)
    assert photo in [r["id"] for r in search.query(con, "elephants")]
    assert "We fed the elephants peanuts" in home.get(f"/image/{photo}").text

    # corrected by hand: kept as theirs, whatever Whisper hears later
    assert home.post(f"/api/stories/{story}/words", json={"title": "The zoo", "words": "We fed the giraffes"},
                     headers=APP).status_code == 200
    speech._record_story(con, story, "fake", heard("something else")(None, b""))
    assert stories.for_image(con, photo)[0]["words"] == "We fed the giraffes"
    assert photo in [r["id"] for r in search.query(con, "giraffes")]

    # taken away: hidden at once, back with Undo, deleted for good a month later
    r = home.post(f"/api/stories/{story}/remove", headers=APP)
    assert r.status_code == 200 and home.get(f"/story/{story}").status_code == 404
    assert f'src="/story/{story}"' not in home.get(f"/image/{photo}").text
    assert photo not in [r["id"] for r in search.query(con, "giraffes")]
    assert home.post(f"/api/changes/{r.json()['undo']}/undo-own", headers=APP).status_code == 200
    assert home.get(f"/story/{story}").status_code == 200
    home.post(f"/api/stories/{story}/remove", headers=APP)
    path = cfg.data_dir / "stories" / row["file"]
    assert stories.purge(con, cfg) == 0 and path.is_file()  # within the month: Undo still works
    assert stories.purge(con, cfg, now=datetime.now() + timedelta(days=stories.KEEP_REMOVED_DAYS + 2)) == 1
    assert not path.exists() and con.execute("SELECT count(*) FROM story").fetchone()[0] == 0


def test_a_recording_undone_leaves_no_file_behind(cfg, con, photo):
    home = TestClient(webapp.create_app(cfg), client=LAN)
    r = record(home, f"/api/stories?image={photo}")
    file = con.execute("SELECT file FROM story WHERE id = ?", (r.json()["id"],)).fetchone()[0]
    assert home.post(f"/api/changes/{r.json()['undo']}/undo-own", headers=APP).status_code == 200
    assert con.execute("SELECT count(*) FROM story").fetchone()[0] == 0
    assert stories.purge(con, cfg, now=datetime.now() + timedelta(days=2)) == 1
    assert not (cfg.data_dir / "stories" / file).exists()


def test_too_long_or_too_big_is_refused(cfg, con, photo, monkeypatch):
    home = TestClient(webapp.create_app(cfg), client=LAN)
    monkeypatch.setattr(stories, "MOST_BYTES", 1000)
    r = record(home, f"/api/stories?image={photo}")
    assert r.status_code == 413 and "MB" in r.json()["detail"]
    monkeypatch.setattr(stories, "MOST_BYTES", 60 * 2**20)
    monkeypatch.setattr(stories, "_length", lambda path: None)  # ffprobe can't tell: the browser's word is taken
    r = record(home, f"/api/stories?image={photo}", seconds=str(stories.MOST_SECONDS + 600))
    assert r.status_code == 413 and "minutes" in r.json()["detail"]
    monkeypatch.setattr(stories, "_length", lambda path: stories.MOST_SECONDS + 600.0)
    assert record(home, f"/api/stories?image={photo}", seconds="10").status_code == 413
    assert not list((cfg.data_dir / "stories").glob("*/*"))


@pytest.fixture
def fam(con, cfg, library):
    f = fx.build(con, cfg, library)
    p = f["people"]
    f["who"] = {"Sam": fx.account(con, p["Sam"], "sam@example.com", "admin"),
                "Cora": fx.account(con, p["Cora"], "cora@example.com"),
                "Tina": fx.account(con, p["Tina"], "tina@example.com")}
    settings.save(con, cfg, {"accounts_enabled": True})
    return f


def client(cfg, con, account):
    c = TestClient(webapp.create_app(cfg), client=LAN, follow_redirects=False)
    c.cookies.set(accounts.COOKIE, accounts.new_session(con, account["id"], 30))
    return c


def test_stories_about_people_and_their_life_events(fam, cfg, con):
    p = fam["people"]
    cora, tina, admin = (client(cfg, con, fam["who"][n]) for n in ("Cora", "Tina", "Sam"))
    wedding = con.execute("""INSERT INTO event(couple_a, couple_b, tag, date, sort_date) VALUES (?, ?, 'MARR',
                             '12 JUN 1962', '1962-06-12')""", tuple(sorted((p["Rex"], p["Nell"])))).lastrowid
    con.commit()
    r = record(cora, f"/api/stories?person={p['Rex']}", event=wedding, title="The%20wedding")
    assert r.status_code == 200, r.text
    about_rex = record(cora, f"/api/stories?person={p['Rex']}").json()["id"]
    block = cora.get(f"/person/{p['Rex']}/stories").text
    assert "The wedding" in block and "about Married" in block and "1962" in block
    assert f'src="/story/{about_rex}"' in block and 'data-story-form="/api/stories?person=' in block
    assert "The wedding" in cora.get(f"/person/{p['Nell']}/stories").text  # a couple's event: on both their pages
    assert f'data-later="/person/{p["Rex"]}/stories"' in cora.get(f"/person/{p['Rex']}").text
    # who recorded it: Cora, by name, to those who see her
    assert "Cora" in tina.get(f"/person/{p['Rex']}/stories").text

    # Cora can't record about, or hear, what she doesn't see
    zed, zed_photo = p["Zed"], fam["photos"]["zed.jpg"]
    assert record(cora, f"/api/stories?person={zed}").status_code == 404
    assert record(cora, f"/api/stories?image={zed_photo}").status_code == 404
    assert record(cora, f"/api/stories?person={p['Rex']}&event=99999").status_code == 404
    secret = record(admin, f"/api/stories?person={zed}", title=fx.SECRET).json()["id"]
    secret_photo = record(admin, f"/api/stories?image={zed_photo}", title=fx.SECRET).json()["id"]
    for story in (secret, secret_photo):
        assert cora.get(f"/story/{story}").status_code == 404
        assert cora.post(f"/api/stories/{story}/remove", headers=APP).status_code == 404
    assert cora.get(f"/person/{zed}/stories").status_code == 404

    # only whoever recorded a story, or an admin, changes it or takes it away
    assert tina.post(f"/api/stories/{about_rex}/remove", headers=APP).status_code == 403
    assert tina.post(f"/api/stories/{about_rex}/words", json={"words": "no"}, headers=APP).status_code == 403
    assert cora.post(f"/api/stories/{about_rex}/words", json={"title": "Dad", "words": "He sang"},
                     headers=APP).status_code == 200
    assert admin.post(f"/api/stories/{about_rex}/remove", headers=APP).status_code == 200

    # someone with stories isn't removed by mistake; merged, their stories go with them
    assert "recorded stor" in people.why_kept(con, p["Rex"])
    people.merge(con, p["Zed"], p["Zoe"])
    con.commit()
    assert con.execute("SELECT person_id FROM story WHERE id = ?", (secret,)).fetchone()[0] == p["Zoe"]


def test_corrected_transcript_lines_stay_and_are_searched(cfg, con, photo, monkeypatch):
    raw = {"model": "fake", "segments": [
        {"start": 1.0, "end": 2.0, "text": "Happy birthday, Owen", "avg_logprob": -0.2},
        {"start": 3.5, "end": 5.0, "text": "blow out the candels", "avg_logprob": -0.2},
        {"start": 6.0, "end": 7.0, "text": "a cow mooing", "avg_logprob": -0.2}]}
    con.execute("UPDATE image SET media = 'video', codecs = 'h264/aac' WHERE id = ?", (photo,))
    speech._record(con, search.FolderIndex(con), photo, "fake", raw)
    home = TestClient(webapp.create_app(cfg), client=LAN)
    page = home.get(f"/image/{photo}").text
    assert "Said in the video" in page and 'data-seek="3.5"' in page and f"/api/images/{photo}/transcript" in page
    r = home.post(f"/api/images/{photo}/transcript", json={"start": 3.5, "text": "blow out the candles"}, headers=APP)
    assert r.status_code == 200, r.text
    home.post(f"/api/images/{photo}/transcript", json={"start": 6.0, "text": ""}, headers=APP)  # nobody said that
    t = speech.for_page(con, photo)
    assert t["text"] == "Happy birthday, Owen blow out the candles" and t["edited"]
    assert [ln["removed"] for ln in t["lines"]] == [False, False, True]
    assert photo in [r["id"] for r in search.query(con, "candles")]
    assert photo not in [r["id"] for r in search.query(con, "mooing")]
    assert "corrected" in home.get(f"/image/{photo}").text and "taken out" in home.get(f"/image/{photo}").text
    # heard again, or filtered by better rules, the corrections stay
    speech._record(con, search.FolderIndex(con), photo, "fake", raw)
    monkeypatch.setattr(speech, "VERSION", speech.VERSION + 1)
    assert speech.refilter(con) == 1
    assert speech.for_page(con, photo)["text"] == "Happy birthday, Owen blow out the candles"
    # Undo, and "as Whisper heard it"
    assert home.post(f"/api/changes/{r.json()['undo']}/undo-own", headers=APP).status_code == 409  # changed since
    assert home.post(f"/api/images/{photo}/transcript", json={"start": 6.0, "heard": True}, headers=APP).status_code \
        == 200
    assert speech.for_page(con, photo)["text"].endswith("a cow mooing")
    assert home.post(f"/api/images/{photo}/transcript", json={"start": 9.9, "text": "x"},
                     headers=APP).status_code == 400  # no such line


def test_whisper_hears_the_language_set(cfg, con):
    assert speech.Whisper(cfg).language == "auto"  # Whisper tells from the speech itself
    assert settings.current(con, cfg).whisper_language == "auto"
    settings.save(con, cfg, {"whisper_language": "DE"})
    assert settings.current(con, cfg).whisper_language == "de"
    with pytest.raises(settings.Invalid):
        settings.save(con, cfg, {"whisper_language": "german"})


def test_backups_and_exports_carry_the_stories(cfg, con, photo, tmp_path):
    home = TestClient(webapp.create_app(cfg), client=LAN)
    story = record(home, f"/api/stories?image={photo}", title="Grandad").json()["id"]
    file = con.execute("SELECT file FROM story WHERE id = ?", (story,)).fetchone()[0]
    backup.make(con, cfg)
    copied = backup.folder(cfg) / "stories" / file
    assert copied.read_bytes() == wav()
    out = tmp_path / "export.zip"
    counts = export.write(con, cfg, out)
    assert counts["stories"] == 1
    with zipfile.ZipFile(out) as z:
        assert z.read(f"stories/{file}") == wav()
        photos = json.loads(z.read("photos.json"))
    told = next(p for p in photos["photos"] if p.get("stories"))["stories"][0]
    assert told["title"] == "Grandad" and told["file"] == file
    # gone for good from the catalog: gone from the backup folder at the next copy
    con.execute("DELETE FROM story")
    con.commit()
    backup.make(con, cfg)
    assert not copied.exists()


def test_a_sound_file_no_story_refers_to_is_set_aside_never_deleted(cfg, con, library):
    """After a catalog is put back from an older backup, recordings made since have no row: the daily tidy moves such
    files out of the way, but never deletes them. A story taken away a month ago does go, with its file."""
    import os
    from datetime import datetime, timedelta
    from lineagebox import stories
    root = stories.folder(cfg)
    (root / "ab").mkdir(parents=True, exist_ok=True)
    orphan = root / "ab" / "newer-than-the-backup.webm"
    orphan.write_bytes(b"\x1aE\xdf\xa3 not really sound")
    old = (datetime.now() - timedelta(days=2)).timestamp()
    os.utime(orphan, (old, old))
    pid = con.execute("INSERT INTO person(name) VALUES ('Nana Miller')").lastrowid
    gone = root / "ab" / "taken-away.webm"
    gone.write_bytes(b"\x1aE\xdf\xa3 also not sound")
    con.execute("""INSERT INTO story(person_id, file, mime, bytes, at, removed_at) VALUES (?, 'ab/taken-away.webm',
                   'audio/webm', 20, '2026-01-01T00:00:00', '2026-01-02T00:00:00')""", (pid,))
    con.commit()
    stories.purge(con, cfg)
    assert not orphan.exists() and (root / stories.SET_ASIDE / orphan.name).is_file()  # kept, out of the way
    assert not gone.exists()
    stories.purge(con, cfg)  # what was set aside stays where it is
    assert (root / stories.SET_ASIDE / orphan.name).is_file()
