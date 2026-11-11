"""Edge cases of the media and AI pipeline as it runs in the worker on a small board: files tidied away that an undo
could still bring back, slideshow videos that fail or are cut off, recordings among the photos, stages that fail
without stopping the pass, and Ollama or Whisper away."""
import dataclasses
import io
import sqlite3
import tempfile
import time
import wave
from datetime import datetime, timedelta

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from lineagebox import (chapters, check, describe, embeddings, llm, media, outbox, retry, search, settings, slideshow,
                        speech, stories, tagging, tours, video, worker)
from lineagebox import ingest as ingest_module
from lineagebox.catalog import VISIBLE
from lineagebox.describe import _record
from lineagebox.faces import FaceStats
from lineagebox.folders import FolderIndex
from lineagebox.ingest import NOTHING_TO_SEE, STAGE_INGEST as INGESTED, ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests.conftest import LAN, rel
from tests.test_meaning import lib, meaning  # noqa: F401 (fixtures)
from tests.test_video import make_video

APP = {"X-Requested-With": "lineagebox"}


def wav(seconds: float = 1.0) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x01" * int(16000 * seconds))
    return buf.getvalue()


@pytest.fixture
def photo(con, cfg, library):
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    return con.execute("SELECT id FROM image WHERE error IS NULL ORDER BY id LIMIT 1").fetchone()[0]


# ---- recorded stories: the tidy step never deletes a file an undo can bring back ------------------------------------

def test_a_story_an_undo_can_bring_back_keeps_its_file(cfg, con, photo):
    """A story taken away, then its person removed (the hidden story goes with them): undoing the removal brings the
    story's row back, so its sound must still be there however long after."""
    home = TestClient(webapp.create_app(cfg), client=LAN)
    pid = con.execute("INSERT INTO person(name) VALUES ('Ada Miller')").lastrowid
    con.commit()
    r = home.post(f"/api/stories?person={pid}", content=wav(), headers={**APP, "Content-Type": "audio/wav"})
    assert r.status_code == 200, r.text
    story = r.json()["id"]
    file = stories.folder(cfg) / con.execute("SELECT file FROM story WHERE id = ?", (story,)).fetchone()[0]
    taken = home.post(f"/api/stories/{story}/remove", headers=APP).json()["undo"]
    gone = home.post(f"/api/people/{pid}/remove", headers=APP)
    assert gone.status_code == 200, gone.text
    assert con.execute("SELECT count(*) FROM story").fetchone()[0] == 0  # went with them (ON DELETE CASCADE)

    later = datetime.now() + timedelta(days=3)
    assert stories.purge(con, cfg, now=later) == 0 and file.is_file()  # the removal can still be undone
    assert home.post(f"/api/changes/{gone.json()['undo']}/undo-own", headers=APP).status_code == 200
    assert home.post(f"/api/changes/{taken}/undo-own", headers=APP).status_code == 200
    assert home.get(f"/story/{story}").content == wav()

    # once nothing can bring it back (taken away again and its month over), it goes for good
    home.post(f"/api/stories/{story}/remove", headers=APP)
    assert stories.purge(con, cfg, now=datetime.now() + timedelta(days=stories.KEEP_REMOVED_DAYS + 2)) == 1
    assert not file.exists()


# ---- stories through Whisper: the PC away, a file unreadable just now, an answer refused -----------------------------

def test_stories_wait_while_whisper_is_away_and_only_a_refusal_is_kept(cfg, con, photo, monkeypatch):
    ids = [stories.save(con, cfg, wav(), None, image_id=photo, title=f"Story {n}") for n in range(4)]
    con.commit()
    monkeypatch.setattr(speech.video, "audio_wav", lambda path: wav())
    sent = []

    def away(self, data):
        sent.append(self.language)
        raise httpx.ConnectError("the PC is asleep")

    monkeypatch.setattr(speech.Whisper, "transcribe", away)
    s = speech.transcribe(con, cfg)
    assert s.gave_up and (s.done, s.failed) == (0, speech.GIVE_UP_AFTER) and set(sent) == {"auto"}
    assert con.execute("SELECT count(*) FROM story WHERE heard IS NULL").fetchone()[0] == 4  # all wait for it
    assert not con.execute("SELECT 1 FROM retry").fetchone()  # not held against the stories

    def refuses(self, data):  # the service answers, but won't take this one (too long, say)
        request = httpx.Request("POST", "http://whisper/transcribe")
        raise httpx.HTTPStatusError("413", request=request, response=httpx.Response(413, request=request,
                                                                                      text="too large"))

    read = []

    def second_unreadable(path):
        read.append(path)
        if len(read) == 2:
            raise OSError("the data folder isn't there just now")
        return wav()

    monkeypatch.setattr(speech.Whisper, "transcribe", refuses)
    monkeypatch.setattr(speech.video, "audio_wav", second_unreadable)
    stopped = iter([False, False, True])  # asked after each: stopped after the third
    s = speech.transcribe(con, cfg, stop=lambda: next(stopped, True))
    assert s.stopped and s.failed == 3 and "413" in s.last_error
    heard = dict(con.execute("SELECT id, heard FROM story"))
    assert heard[ids[0]] == "" and heard[ids[1]] is None and heard[ids[2]] == "" and heard[ids[3]] is None
    assert con.execute("SELECT stage FROM retry WHERE item_id = ?", (ids[1],)).fetchone()[0] == "stories"


# ---- slideshow videos: clips with their sound, ffmpeg failing midway, the worker cut off ----------------------------

def _tour(con) -> int:
    return tours.save_tour(con, None, None, tours.clean({"title": "All", "seconds": 1}), "All")


def _slides(con, with_clip: int | None = None) -> list[dict]:
    photos = con.execute(f"""SELECT i.id, i.sha256 FROM image i WHERE {VISIBLE} AND i.media = 'photo'
                             ORDER BY i.id LIMIT 2""").fetchall()
    out = [{"title": "The Millers", "span": "2012 to 2016", "about": "Zoë and Łukasz", "count": "3 photos"}]
    out += [{"id": r["id"], "sha": r["sha256"], "video": False, "caption": "At the zoo — Müller’s day",
             "when": "June 3, 2012", "people": "Ada Miller (6)"} for r in photos]
    if with_clip is not None:
        sha, duration = con.execute("SELECT sha256, duration FROM image WHERE id = ?", (with_clip,)).fetchone()
        out.append({"id": with_clip, "sha": sha, "video": True, "duration": duration, "caption": "Singing"})
    return out


@pytest.fixture
def clip(con, cfg, library):
    if not video.available():
        pytest.skip("needs ffmpeg and ffprobe")
    make_video(library / "2016" / "song.mp4", seconds=3)
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    return con.execute("SELECT image_id FROM file WHERE path = ?", (rel("2016", "song.mp4"),)).fetchone()[0]


def test_a_slideshow_with_a_clip_keeps_its_sound_and_leaves_nothing_behind(con, cfg, clip, tmp_path, monkeypatch):
    spare = tmp_path / "spare"  # where the clips' captions are drawn for ffmpeg: emptied again
    spare.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(spare))
    asked = slideshow.ask(con, None, "tour", _tour(con), "With a clip", _slides(con, clip), 1)
    assert slideshow.make_waiting(con, cfg) == 1
    made = con.execute("SELECT * FROM slideshow_video WHERE id = ?", (asked,)).fetchone()
    assert made["state"] == "done", made["message"]
    p = video.probe(slideshow.folder(cfg) / made["file"])
    assert p.audio_codec == "aac" and (p.width, p.height) == (slideshow.WIDTH, slideshow.HEIGHT)
    # the title card, two photos of a second, the 3 s clip, and three crossfades
    assert abs(p.duration - (slideshow.TITLE_SECONDS + 2 + 3 + 3 * slideshow.FADE)) < 0.6
    assert [f.name for f in slideshow.folder(cfg).iterdir()] == [made["file"]] and not list(spare.iterdir())


def test_the_encoder_stopping_before_a_clip_stops_the_clips_ffmpeg_too(con, cfg, clip, tmp_path, monkeypatch):
    spare = tmp_path / "spare"
    spare.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(spare))
    asked = slideshow.ask(con, None, "tour", _tour(con), "Cut off", _slides(con, clip), 1)
    real, clips = slideshow._slide, []

    def dies_at_the_clip(con_, cfg_, slide, seconds, last, encoder, track, background):
        if slide.get("video"):
            encoder.p.kill()
            encoder.p.wait()
            real_clip = slideshow._Clip

            class Watched(real_clip):
                def first(self):
                    clips.append(self)
                    return super().first()

            monkeypatch.setattr(slideshow, "_Clip", Watched)
        return real(con_, cfg_, slide, seconds, last, encoder, track, background)

    monkeypatch.setattr(slideshow, "_slide", dies_at_the_clip)
    assert slideshow.make_waiting(con, cfg) == 0
    assert con.execute("SELECT state FROM slideshow_video WHERE id = ?", (asked,)).fetchone()[0] == "failed"
    assert clips and clips[0].pipe is None and not list(spare.iterdir())  # its ffmpeg ended, its caption gone
    assert not list(slideshow.folder(cfg).iterdir())


def test_ffmpeg_failing_midway_fails_that_video_only(con, cfg, photo, monkeypatch):
    if not video.available():
        pytest.skip("needs ffmpeg and ffprobe")
    first = slideshow.ask(con, None, "tour", _tour(con), "Cut off", _slides(con), 1)
    second = slideshow.ask(con, None, "tour", _tour(con), "Fine", _slides(con), 1)
    real, calls = slideshow._slide, []

    def dies(con_, cfg_, slide, seconds, last, encoder, track, background):
        calls.append(1)
        if len(calls) == 2:  # the encoder goes (out of memory, a full disk) while the video is being made
            encoder.p.kill()
            encoder.p.wait()
        return real(con_, cfg_, slide, seconds, last, encoder, track, background)

    monkeypatch.setattr(slideshow, "_slide", dies)
    assert slideshow.make_waiting(con, cfg) == 1
    states = dict(con.execute("SELECT id, state FROM slideshow_video"))
    assert states == {first: "failed", second: "done"}
    assert con.execute("SELECT message FROM slideshow_video WHERE id = ?", (first,)).fetchone()[0]
    assert [f.name for f in slideshow.folder(cfg).iterdir()] == [f"{second}.mp4"]  # no half-made one left


def test_a_video_that_cant_be_made_never_holds_up_the_others(con, cfg, photo, monkeypatch):
    """Whatever goes wrong drawing one (here a picture that can't be drawn), it is failed, and the next is made."""
    first = slideshow.ask(con, None, "tour", _tour(con), "Odd", _slides(con), 1)
    second = slideshow.ask(con, None, "tour", _tour(con), "Fine", _slides(con), 1)
    monkeypatch.setattr(slideshow.video, "available", lambda: True)
    made = []

    def make(con_, cfg_, row, threads):
        if row["id"] == first:
            raise ValueError("a picture that can't be drawn")
        made.append(row["id"])
        return "x.mp4", 1

    monkeypatch.setattr(slideshow, "_make", make)
    assert slideshow.make_waiting(con, cfg) == 1 and made == [second]
    row = con.execute("SELECT state, message FROM slideshow_video WHERE id = ?", (first,)).fetchone()
    assert row["state"] == "failed" and row["message"]


def _recording(con) -> int:
    """A photo of the sample library made a sound recording (as ingest records one: nothing to see in it)."""
    rec = con.execute("SELECT id FROM image WHERE error IS NULL ORDER BY id DESC LIMIT 1").fetchone()[0]
    con.execute("UPDATE image SET media = 'audio', codecs = 'mp3', duration = 60, stage = ? WHERE id = ?",
                (INGESTED | NOTHING_TO_SEE, rec))
    con.commit()
    return rec


def test_a_recording_is_never_described_again_from_its_sound_wave(con, cfg, photo, monkeypatch):
    """Ticked with photos, a recording isn't asked of the AI (it would describe the picture of its sound)."""
    rec = _recording(con)
    assert describe.ask_again(con, [rec]) == 0 and describe.count_requests(con) == 0
    assert describe.ask_again(con, [rec, photo]) == 1 and [r["id"] for r in describe.requests(con)] == [photo]
    # one asked for before this was so isn't described either
    con.execute("INSERT INTO describe_request(image_id, model, asked_at) VALUES (?, NULL, '2026-01-01')", (rec,))
    con.commit()
    assert [r["id"] for r in describe.requests(con)] == [photo]


def test_asks_of_another_model_wait_while_ollama_cant_say_what_it_is(con, cfg, photo, monkeypatch):
    """Only a model Ollama hasn't got, or one that can't see, has its asks dropped; an error Ollama answers about it
    (busy, restarting) may pass."""
    class Hiccup:
        def __init__(self, cfg):
            self.model = cfg.ollama_model

        def model_problem(self):
            if self.model == "gemma3:12b":
                return llm.ModelProblem("Ollama could not say what gemma3:12b is: server busy")
            return None

    monkeypatch.setattr(describe, "Ollama", Hiccup)
    describe.ask_again(con, [photo], "gemma3:12b")
    stats = describe.describe_requests(con, cfg)
    assert (stats.dropped, stats.done) == (0, 0) and describe.count_requests(con) == 1
    real = llm.Ollama(dataclasses.replace(cfg, ollama_model="gemma3:12b"))
    real.http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404, json={})),
                             base_url="http://ollama")
    assert real.model_problem().missing  # a model Ollama hasn't got: those asks are dropped


def test_asks_stopped_midway_wait_for_the_next_pass(con, cfg, photo, monkeypatch):
    from tests.test_ai_on_request import Seeing
    Seeing.problems, Seeing.answers = {}, {}
    monkeypatch.setattr(describe, "Ollama", Seeing)
    others = [r[0] for r in con.execute("SELECT id FROM image WHERE error IS NULL AND id != ? AND (dup_group_id IS NULL "
                                        "OR dup_group_id = id) ORDER BY id", (photo,))]
    describe.ask_again(con, [photo], "gemma3:12b")
    describe.ask_again(con, others, "llava:13b")
    asked = iter([False, True])  # stopped after the first: the describing hours are over
    s = describe.describe_requests(con, dataclasses.replace(cfg, llm_workers=1), stop=lambda: next(asked, True))
    assert s.stopped and s.done == 1 and describe.count_requests(con) == len(others)
    assert {r["model"] for r in describe.requests(con)} == {"llava:13b"}


def test_a_tag_taken_off_by_hand_stays_off_when_merged_into_one_the_ai_gave(con, cfg, photo):
    """Merging keeps the strongest say each photo had: taken off by hand, then given by hand, then the AI's."""
    _record(con, photo, "fake", {"caption": "Dogs.", "tags": ["puppy", "dog"]}, FolderIndex(con))
    tagging.remove(con, [photo], "puppy")  # "that is no puppy": taken off by hand, the AI's dog stays
    con.commit()
    tagging.rename(con, "puppy", "dog", merge=True)
    con.commit()
    assert con.execute("""SELECT it.source FROM image_tag it JOIN tag t ON t.id = it.tag_id
                          WHERE it.image_id = ? AND t.name = 'dog'""", (photo,)).fetchone()[0] == "not"
    assert [t["name"] for t in tagging.for_image(con, photo)] == []


def test_a_recordings_picture_waits_when_its_sound_cant_be_read_just_now(con, cfg, library, monkeypatch):
    """A slow share (ffmpeg timing out) or the file gone for a moment: the recording is read again later, rather than
    kept with a blank picture for good."""
    if not video.available():
        pytest.skip("needs ffmpeg and ffprobe")
    from tests.test_tapes import make_sound
    path = make_sound(library / "Tapes" / "side A.mp3")
    scan(con, cfg)
    with monkeypatch.context() as m:
        m.setattr(video, "waveform", lambda path: (_ for _ in ()).throw(video.TimedOut("ffmpeg took over 1800 s")))
        ingest(con, cfg, show_progress=False)
    f = con.execute("SELECT image_id, error FROM file WHERE path = ?", (rel("Tapes", "side A.mp3"),)).fetchone()
    assert f["image_id"] is None and "1800 s" in f["error"]  # read again later (retry.py)
    con.execute("UPDATE retry SET next_at = 0")  # the wait is over
    ingest(con, cfg, show_progress=False)
    rec = con.execute("SELECT i.* FROM image i JOIN file f ON f.image_id = i.id WHERE f.path = ?",
                      (rel("Tapes", "side A.mp3"),)).fetchone()
    assert rec["media"] == "audio" and cfg.preview_path(rec["sha256"]).is_file()
    wave_picture = cfg.preview_path(rec["sha256"]).read_bytes()

    cfg.preview_path(rec["sha256"]).unlink()  # its picture gone (check.rebuild_derivatives), and its file too, for now
    con.execute(f"UPDATE image SET stage = stage & ~{INGESTED} WHERE id = ?", (rec["id"],))
    con.commit()
    path.rename(path.with_suffix(".away"))
    assert ingest(con, cfg, show_progress=False).rebuilt == 0
    assert not con.execute("SELECT stage FROM image WHERE id = ?", (rec["id"],)).fetchone()[0] & INGESTED
    path.with_suffix(".away").rename(path)
    assert ingest(con, cfg, show_progress=False).rebuilt == 1
    assert cfg.preview_path(rec["sha256"]).read_bytes() == wave_picture  # its sound wave, not a blank


# ---- long tapes: frames that take too long --------------------------------------------------------------------------

def test_a_frame_that_takes_too_long_ends_the_frames_of_that_video(con, cfg, library, monkeypatch):
    """On a tape with no index, a frame is found by decoding from the start: one that took too long means every later
    one would take longer still (120 of them on a two-hour tape), so the video keeps the frames it has. One whose
    very first frame takes too long is read again later, not recorded as damaged."""
    if not video.available():
        pytest.skip("needs ffmpeg and ffprobe")
    make_video(library / "Tapes" / "tape.mp4", seconds=40)
    make_video(library / "Tapes" / "slow.mp4", seconds=40, size="160x120")
    scan(con, cfg)
    real, asked = video.frame, []

    def slow_after_two(path, at, max_side, p):
        asked.append((path.name, at))
        if path.name == "slow.mp4" or len([a for a in asked if a[0] == "tape.mp4"]) > 2:
            raise video.TimedOut("ffmpeg took over 900 s")
        return real(path, at, max_side, p)

    monkeypatch.setattr(video, "frame", slow_after_two)
    ingest(con, cfg, show_progress=False)
    tape = con.execute("SELECT i.id FROM image i JOIN file f ON f.image_id = i.id WHERE f.path = ?",
                       (rel("Tapes", "tape.mp4"),)).fetchone()
    assert tape is not None and len([a for a in asked if a[0] == "tape.mp4"]) == 3  # none asked after it
    assert 1 <= con.execute("SELECT count(*) FROM video_frame WHERE image_id = ?", (tape[0],)).fetchone()[0] <= 2
    slow = con.execute("SELECT id, image_id, error FROM file WHERE path = ?", (rel("Tapes", "slow.mp4"),)).fetchone()
    assert slow["image_id"] is None and "900 s" in slow["error"]  # read again later
    # ... after a while (retry.py), not on every pass: each try takes ffmpeg's whole time limit
    assert con.execute("SELECT attempts FROM retry WHERE stage = 'ingest' AND item_id = ?", (slow["id"],)).fetchone()
    assert ingest(con, cfg, show_progress=False).pending == 0
    assert [s["stage"] for s in retry.summary(con)] == ["ingest"]
    con.execute("UPDATE retry SET next_at = 0")  # the wait is over, and ffmpeg is quick this time
    monkeypatch.setattr(video, "frame", real)
    assert ingest(con, cfg, show_progress=False).new_images == 1
    assert not con.execute("SELECT 1 FROM retry").fetchone()


def test_a_video_this_code_cant_make_sense_of_never_stops_a_pass(con, cfg, library, monkeypatch):
    """Whatever goes wrong in reading one video is recorded on it; the files after it are read as usual."""
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)  # the sample library's photos
    (library / "Camcorder").mkdir()
    for name in ("00001.MTS", "00002.MTS"):
        (library / "Camcorder" / name).write_bytes(name.encode() * 100)
    scan(con, cfg)
    monkeypatch.setattr(video, "available", lambda: True)
    probe = video.Probe(duration=10, width=640, height=480, video_codec="h264", audio_codec=None, interlaced=False,
                        created=None, gps=None, camera=None)
    monkeypatch.setattr(video, "probe", lambda path: probe)
    monkeypatch.setattr(ingest_module, "_video_frames", lambda *a, **k: [])

    def taken_at(p, head, name):  # an odd camcorder block the date reader trips on
        if name == "00001.MTS":
            raise IndexError("index out of range")
        return "2010-09-29T18:39:45", "camera"

    monkeypatch.setattr(video, "taken_at", taken_at)
    s = ingest(con, cfg, show_progress=False)
    assert (s.new_images, s.decode_errors) == (2, 1)
    errors = dict(con.execute("SELECT f.path, i.error FROM file f JOIN image i ON i.id = f.image_id"))
    assert "IndexError" in errors[rel("Camcorder", "00001.MTS")] and errors[rel("Camcorder", "00002.MTS")] is None
    # the date reader itself: a block cut off by the end of what was read says nothing
    assert video.mdpm_date(b"\x00" * 10 + bytes.fromhex("17ee8c60f84d11d98cd60800200c9a66") + b"MDPM") is None


def test_chapters_stop_taking_frames_after_one_takes_too_long(con, cfg, photo, monkeypatch):
    con.execute("UPDATE image SET media = 'video', duration = 1800, codecs = 'h264/aac' WHERE id = ?", (photo,))
    con.commit()
    probe = video.Probe(duration=1800, width=640, height=480, video_codec="h264", audio_codec="aac",
                        interlaced=False, created=None, gps=None, camera=None)
    monkeypatch.setattr(video, "probe", lambda path: probe)
    monkeypatch.setattr(video, "keyframes", lambda path, p: [(600.0, 0.9, False), (1200.0, 0.9, False)])
    asked = []

    def slow(path, at, max_side, p):
        asked.append(at)
        raise video.TimedOut("ffmpeg took over 900 s")

    monkeypatch.setattr(video, "frame", slow)
    s = chapters.find(con, cfg)
    assert (s.done, s.failed) == (1, 0) and len(asked) == 1
    assert [c["at"] for c in chapters.for_page(con, photo)] == [0.0, 600.0, 1200.0]  # their times all the same


def test_chapters_wait_for_a_file_that_cant_be_read_now_and_note_one_ffmpeg_cant(con, cfg, photo, monkeypatch):
    con.execute("UPDATE image SET media = 'video', duration = 1800, codecs = 'h264/aac' WHERE id = ?", (photo,))
    con.commit()
    monkeypatch.setattr(video, "probe", lambda path: (_ for _ in ()).throw(video.TimedOut("ffprobe took over 120 s")))
    s = chapters.find(con, cfg)
    assert s.failed == 1 and chapters.count_pending(con) == 0  # waiting (retry.py), then tried again
    assert con.execute("SELECT stage FROM retry WHERE item_id = ?", (photo,)).fetchone()[0] == "chapters"
    con.execute("DELETE FROM retry")
    monkeypatch.setattr(video, "probe", lambda path: (_ for _ in ()).throw(video.Error("Invalid data found")))
    assert chapters.find(con, cfg).failed == 1 and chapters.count_pending(con) == 0  # not tried again
    assert con.execute("SELECT error FROM video_chapters WHERE image_id = ?", (photo,)).fetchone()[0]
    assert chapters.for_page(con, photo) == []


# ---- odd raw files -------------------------------------------------------------------------------------------------

def test_a_raw_file_whose_preview_is_damaged_is_read_from_its_raw_data(monkeypatch):
    from tests.test_media import CAMERA, FakeRaw, tiff
    monkeypatch.setattr(media.rawpy, "imread", lambda f: FakeRaw(b"\xff\xd8 not really a JPEG", flip=6))
    d = media.decode(tiff(CAMERA) + bytes(100), ".nef", max_side=100)
    assert (d.format, d.width, d.height) == ("nef", 4000, 6000) and d.image.size == (20, 30)


# ---- search by meaning: the web app's vectors in memory, a model changed --------------------------------------------

def _vectors_db(n: int, dims: int, model: str = "m") -> sqlite3.Connection:
    """A catalog's embedding table alone, with n made-up vectors."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE embedding (image_id INTEGER PRIMARY KEY, model TEXT, text_sha TEXT, vector BLOB, "
               "made_at TEXT)")
    rng = np.random.default_rng(1)
    db.executemany("INSERT INTO embedding VALUES (?, ?, '', ?, '2026-11-07T10:00:00')",
                   ((i, model, rng.random(dims, dtype=np.float32).tobytes()) for i in range(1, n + 1)))
    return db


def test_the_vectors_kept_in_memory_take_little_more_room_than_they_need():
    """40,000 photos' vectors at 768 numbers are 123 MB: read at once into a matrix of their own size, and grown by
    a quarter at a time as the worker adds more, not doubled (which would be 250 MB on a small board)."""
    db = _vectors_db(3000, 8)
    ix = embeddings._Index()
    ix.refresh(db, "m")
    assert ix.n == 3000 and len(ix.ids) == 3000
    db.execute("INSERT INTO embedding VALUES (3001, 'm', '', ?, '2026-11-07T10:00:01')",
               (np.ones(8, dtype=np.float32).tobytes(),))
    ix.refresh(db, "m")
    assert ix.n == 3001 and len(ix.ids) <= 3001 * 1.3 and ix.pos[3001] == 3000


def test_a_new_model_lets_go_of_the_last_ones_vectors(tmp_path, monkeypatch):
    embeddings.forget()
    db = _vectors_db(10, 4, "old-model")
    db.executemany("INSERT INTO embedding VALUES (?, 'new-model', '', ?, '2026-11-07T11:00:00')",
                   ((i, np.ones(6, dtype=np.float32).tobytes()) for i in range(11, 15)))
    embeddings._index(db, "old-model")
    embeddings._index(db, "new-model")
    assert [model for _, model in embeddings._INDEXES] == ["new-model"]
    embeddings.forget()


def test_vectors_of_another_length_under_the_same_name_are_made_again(con, cfg, photo, monkeypatch):
    """The model under that name was replaced by one that gives vectors of another length (pulled again, a custom
    one): the old vectors can't be compared with the new, so every photo is read again."""
    from tests.test_meaning import MODEL, FakeEmbed
    embeddings.forget()
    FakeEmbed.asked, FakeEmbed.away = [], False
    monkeypatch.setattr(embeddings, "Ollama", FakeEmbed)
    meant = dataclasses.replace(cfg, ollama_embed_model=MODEL)
    other = con.execute("SELECT id FROM image WHERE error IS NULL AND id != ? AND (dup_group_id IS NULL OR "
                        "dup_group_id = id) ORDER BY id LIMIT 1", (photo,)).fetchone()[0]
    _record(con, photo, "fake", {"caption": "Kids in the snow."}, FolderIndex(con))
    con.commit()
    old = np.ones(3, dtype=np.float32)  # as the model of that name gave them before
    con.execute("INSERT INTO embedding(image_id, model, text_sha, vector, made_at) VALUES (?, ?, ?, ?, ?)",
                (photo, MODEL, embeddings._sha(embeddings.text_of(con, photo)), old.tobytes(), "2026-11-01T10:00:00"))
    con.execute("DELETE FROM embed_stale")
    _record(con, other, "fake", {"caption": "Children sledding."}, FolderIndex(con))
    con.commit()
    embeddings.embed_new(con, meant)
    embeddings.embed_new(con, meant)  # the next pass reads the rest again
    lengths = {r[0] for r in con.execute("SELECT length(vector) FROM embedding WHERE model = ?", (MODEL,))}
    assert lengths == {4 * (len(FakeEmbed(meant).embed(["x"])[0]))}
    q = embeddings.query_vector(meant, "kids")
    assert {photo, other} <= {i for i, _ in embeddings.nearest(con, q)}
    embeddings.forget()


# ---- the worker's pass: a stage that fails, and the time each gets ---------------------------------------------------

@pytest.fixture
def online(monkeypatch, cfg):
    """Ollama answering with a model that sees, an embedding model set, and no face models (tests have none)."""
    from tests.test_worker import OnlineOllama
    monkeypatch.setattr(worker, "Ollama", OnlineOllama)
    monkeypatch.setattr(describe, "Ollama", OnlineOllama)
    monkeypatch.setattr(worker, "detect", lambda con, cfg, show_progress: FaceStats())
    return dataclasses.replace(cfg, ollama_embed_model="fake-embed")


def test_a_new_stage_that_fails_never_stops_the_pass(con, cfg, library, online, monkeypatch, capsys):
    def broken(*args, **kwargs):
        raise RuntimeError("something odd")

    for module, name in ((slideshow, "make_waiting"), (stories, "purge"), (chapters, "find"),
                         (describe, "describe_requests"), (embeddings, "embed_new"), (outbox, "after_pass")):
        monkeypatch.setattr(module, name, broken)
    monkeypatch.setattr(chapters, "count_pending", lambda con: 1)
    monkeypatch.setattr(describe, "count_requests", lambda con: 1)
    monkeypatch.setattr(video, "available", lambda: True)
    worker.run_once(con, online)
    out = capsys.readouterr().out
    for stage in ("slideshow videos", "tidy stories", "chapters", "describe again", "meaning", "news"):
        assert f"{stage} failed: RuntimeError: something odd" in out
    assert con.execute("SELECT count(*) FROM analysis").fetchone()[0] > 0  # describing, at the end, still ran
    assert con.execute("SELECT count(*) FROM search_fts").fetchone()[0] > 0


def test_asks_to_describe_again_leave_the_later_stages_their_time(con, cfg, library, online, monkeypatch):
    """A thousand photos asked to be described again take hours: they get their share of each pass, and the stages
    after them (damage checks, other apps' names, transcripts) still get theirs."""
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(describe, "count_requests", lambda con: 1000)

    def long_asks(con_, cfg_, stop=None, show_progress=False):
        n = 0
        while not stop():  # a minute each
            clock[0] += 60
            n += 1
        return describe.DescribeStats(pending=1000, done=n, stopped=True)

    seen = {}

    def checking(con_, cfg_, stop=None, threads=1):
        seen["check"] = stop()
        return 0, 0

    monkeypatch.setattr(describe, "describe_requests", long_asks)
    monkeypatch.setattr(embeddings, "embed_new", lambda con_, cfg_, stop=None: embeddings.EmbedStats())
    monkeypatch.setattr(check, "check", checking)
    result = worker.run_once(con, online, worker.Stopper(con, online, budget_seconds=3600))
    assert seen["check"] is False  # the pass's time wasn't all used up by them
    assert result.run_again  # and the rest of them are done on the next pass, which starts at once


def test_found_by_meaning_newest_or_oldest_first_as_asked(lib, meaning):
    from tests.test_meaning import describe_as, image_of
    con = lib
    snow, dog = image_of(con, rel("2016", "IMG_20160811_182239.jpg")), image_of(con, rel("2012", "Zoo", "DSC_0001.jpg"))
    describe_as(con, snow, "Two children slide down a snowy hill with a dog.")
    describe_as(con, dog, "A puppy and children on the beach.")
    embeddings.embed_new(con, meaning)
    ask = embeddings.asker(meaning)
    found = [r["id"] for r in search.query(con, "kids", search.RESULTS, search.Filters(sort="newest"), meaning=ask)]
    assert found == [snow, dog]  # 2016 before 2012, the order the page asked for, not the closest first
    found = [r["id"] for r in search.query(con, "kids", search.RESULTS, search.Filters(sort="oldest"), meaning=ask)]
    assert found == [dog, snow]


class _Done(BaseException):
    """Ends run_forever in a test (it carries on after any Exception)."""


def test_asking_for_a_slideshow_video_starts_a_pass_and_stops_a_long_one(con, cfg, monkeypatch):
    stopper = worker.Stopper(con, cfg, budget_seconds=3600)
    assert not stopper.reading()
    settings.put(con, slideshow.WAKE, "9999-01-01T00:00:00")  # asked for after this pass started
    stopper.looked = -1e9
    assert stopper.reading() and stopper.reason == "someone asked for a slideshow video"

    con.execute("DELETE FROM settings WHERE key = ?", (slideshow.WAKE,))
    con.commit()
    reasons = []

    def a_pass(con_, cfg_, stop):
        reasons.append(settings.get(con_, "worker")["pass_reason"])
        if len(reasons) == 1:
            settings.put(con_, slideshow.WAKE, "9999-01-01T00:00:00")
        if len(reasons) == 2:
            raise _Done
        return worker.PassResult()

    monkeypatch.setattr(worker, "run_once", a_pass)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)
    with pytest.raises(_Done):
        worker.run_forever(con, cfg)
    assert reasons == ["scheduled", "a slideshow video was asked for"]


def test_a_pass_transcribes_stories_and_says_so(con, cfg, photo, monkeypatch, capsys):
    stories.save(con, cfg, wav(), None, image_id=photo)
    con.commit()
    monkeypatch.setattr(worker, "detect", lambda con, cfg, show_progress: FaceStats())
    monkeypatch.setattr(speech.Whisper, "reachable", lambda self: True)
    monkeypatch.setattr(video, "available", lambda: True)  # the sound is taken out by a fake: no ffmpeg needed
    monkeypatch.setattr(speech.video, "audio_wav", lambda path: wav())
    monkeypatch.setattr(speech.Whisper, "transcribe", lambda self, data: {
        "model": "fake", "duration": 1.0, "segments": [{"start": 0, "end": 1, "text": "We met at the dance hall"}]})
    worker.run_once(con, dataclasses.replace(cfg, describe_enabled=False, whisper_url="http://whisper.invalid"))
    out = capsys.readouterr().out
    assert "transcribe: 0 videos and recordings, 1 stories" in out
    assert con.execute("SELECT heard FROM story").fetchone()[0] == "We met at the dance hall"


def test_a_video_cut_off_by_a_restart_is_made_again_and_its_leftovers_go(con, cfg, photo):
    if not video.available():
        pytest.skip("needs ffmpeg and ffprobe")
    asked = slideshow.ask(con, None, "tour", _tour(con), "Again", _slides(con), 1)
    con.execute("UPDATE slideshow_video SET state = 'making', done = 2 WHERE id = ?", (asked,))
    con.commit()
    left = slideshow.folder(cfg) / ".making-abc123"  # what the worker was writing when it was stopped
    left.mkdir(parents=True)
    (left / "picture.mp4").write_bytes(b"\x00" * 1000)
    (left / "sound.wav").write_bytes(b"\x00" * 1000)
    assert slideshow.make_waiting(con, cfg) == 1
    assert con.execute("SELECT state FROM slideshow_video WHERE id = ?", (asked,)).fetchone()[0] == "done"
    assert [f.name for f in slideshow.folder(cfg).iterdir()] == [f"{asked}.mp4"]
