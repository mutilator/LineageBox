"""What a stage can't do for a reason that may pass waits to be tried again (retry.py), and nothing is recorded as
broken because a tool, a service or a share was away: ffmpeg missing, Whisper answering 500, OpenStreetMap refusing,
an AI answer that isn't JSON."""
import struct
import zlib

import httpx
import pytest

from lineagebox import check, copies, faces, folders, ingest, media, places, retry, settings, speech, video, worker
from lineagebox.scan import scan


def a_video(con, cfg) -> int:
    """One of the sample photos, made out to be a video that has sound and needs a 720p copy."""
    scan(con, cfg)
    ingest.ingest(con, cfg, show_progress=False)
    image_id = con.execute("SELECT id FROM image ORDER BY id LIMIT 1").fetchone()[0]
    con.execute("UPDATE image SET media = 'video', codecs = 'h264/aac', playable = 'needed' WHERE id = ?", (image_id,))
    con.commit()
    return image_id


def test_videos_wait_while_ffmpeg_is_missing(con, cfg, library, monkeypatch):
    (library / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42 not really a video")
    monkeypatch.setattr(video, "available", lambda: False)
    scan(con, cfg)
    s = ingest.ingest(con, cfg, show_progress=False)
    assert s.no_ffmpeg == 1
    assert not con.execute("SELECT 1 FROM file WHERE path LIKE '%clip.mp4' AND image_id IS NOT NULL").fetchone()
    assert not con.execute("SELECT 1 FROM image WHERE media = 'video'").fetchone()  # not recorded as a bad video
    settings.save(con, cfg, {"describe_enabled": False, "transcribe_enabled": False, "faces_enabled": False})
    worker.run_once(con, settings.current(con, cfg))
    assert settings.problems(con)["ffmpeg"]["message"] == video.MISSING
    monkeypatch.setattr(video, "available", lambda: True)  # installed: the problem goes once a pass finds it
    monkeypatch.setattr(video, "probe", lambda path: (_ for _ in ()).throw(video.Error("not a video")))
    worker.run_once(con, settings.current(con, cfg))
    assert "ffmpeg" not in settings.problems(con)


def test_a_copy_that_fails_for_a_passing_reason_is_made_later(con, cfg, library, monkeypatch):
    clip = a_video(con, cfg)
    monkeypatch.setattr(copies.video, "make_copy", lambda *a: (_ for _ in ()).throw(OSError(112, "Host is down")))
    assert copies.make(con, cfg, failed=lambda m: None) == 0
    assert con.execute("SELECT playable FROM image WHERE id = ?", (clip,)).fetchone()[0] == "needed"  # not failed
    assert copies.count_pending(con) == 0 and retry.summary(con)[0]["stage"] == "copies"
    con.execute("UPDATE retry SET next_at = 0")
    con.commit()
    monkeypatch.setattr(copies.video, "make_copy", lambda *a: (_ for _ in ()).throw(video.Error("Invalid data")))
    copies.make(con, cfg, failed=lambda m: None)  # ffmpeg can't read it: that one is for good
    assert con.execute("SELECT playable FROM image WHERE id = ?", (clip,)).fetchone()[0] == "failed: Invalid data"


def test_a_video_whisper_fails_on_waits(con, cfg, library, monkeypatch):
    clip = a_video(con, cfg)
    monkeypatch.setattr(speech.video, "audio_wav", lambda path: b"RIFF" + b"\x00" * 40)

    def fails(self, wav):
        request = httpx.Request("POST", "http://whisper:9300/transcribe")
        raise httpx.HTTPStatusError("500", request=request, response=httpx.Response(500, text="boom", request=request))

    monkeypatch.setattr(speech.Whisper, "transcribe", fails)
    s = speech.transcribe(con, cfg)
    assert s.failed == 1 and "500" in s.last_error
    assert not con.execute("SELECT 1 FROM transcript WHERE image_id = ?", (clip,)).fetchone()  # nothing for good
    assert speech.count_pending(con) == 0  # not first on the next pass
    con.execute("UPDATE retry SET next_at = 0")
    con.commit()
    monkeypatch.setattr(speech.Whisper, "transcribe", lambda self, wav: {"model": "m", "segments": [], "duration": 1})
    assert speech.transcribe(con, cfg).done == 1 and not con.execute("SELECT 1 FROM retry").fetchone()


def test_folder_names_the_ai_cant_answer_wait(con, cfg, library):
    scan(con, cfg)

    class NotJson:
        asked = 0

        def chat_json(self, *args, **kwargs):
            NotJson.asked += 1
            raise ValueError("Expecting value: line 1 column 1")

    parsed, left = folders.parse_folders(con, NotJson())
    assert parsed == 0 and left > 0 and NotJson.asked == 2  # once more, less cautious, then later
    assert folders.parse_folders(con, NotJson()) == (0, 0) and NotJson.asked == 2


def test_openstreetmap_refusing_is_shown_and_waited_out(con, cfg, monkeypatch):
    con.execute("INSERT INTO person(name) VALUES ('Ann')")
    con.execute("INSERT INTO event(person_id, tag, place) VALUES (last_insert_rowid(), 'BIRT', 'Salem, Oregon')")
    con.commit()
    answers, asked = [httpx.Response(429, headers={"Retry-After": "120"})], []
    real = httpx.Client
    monkeypatch.setattr(places.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(
        lambda r: asked.append(r) or answers[0])))
    monkeypatch.setattr(places.time, "sleep", lambda s: None)
    assert places.find_new(con, cfg) == 0
    assert "refused the lookups (429)" in settings.problems(con)["places"]["message"]
    assert places.find_new(con, cfg) == 0 and len(asked) == 1  # not asked again while it said to wait
    settings.put(con, places.WAIT_KEY, 0)
    answers[0] = httpx.Response(200, json=[{"lat": "44.9", "lon": "-123.0", "display_name": "Salem"}])
    assert places.find_new(con, cfg) == 1 and "places" not in settings.problems(con)


def test_a_small_png_that_says_it_is_huge_is_not_decoded():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 10_000, 10_000, 8, 0, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b""))
    with pytest.raises(ValueError, match="too large a PNG"):
        media.decode(png, ".png", 48)


def test_a_file_that_cant_be_read_is_damaged_only_if_it_fails_again(con, cfg, library, monkeypatch):
    scan(con, cfg)
    ingest.ingest(con, cfg, show_progress=False)
    monkeypatch.setattr(check, "problem", lambda *a: (_ for _ in ()).throw(OSError(5, "Input/output error")))
    check.check(con, cfg)
    assert not con.execute("SELECT 1 FROM file WHERE damage IS NOT NULL").fetchone()  # the share may have dropped
    con.execute("UPDATE retry SET next_at = 0")
    con.commit()
    check.check(con, cfg)
    assert con.execute("SELECT damage FROM file WHERE damage IS NOT NULL").fetchone()[0] == \
        "can't be read (Input/output error)"


def test_without_the_face_libraries_nothing_is_downloaded(con, cfg, library, monkeypatch):
    monkeypatch.setattr(faces.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(faces.httpx, "stream", lambda *a, **k: pytest.fail("downloaded the models"))
    scan(con, cfg)
    ingest.ingest(con, cfg, show_progress=False)
    settings.save(con, cfg, {"describe_enabled": False, "transcribe_enabled": False})
    worker.run_once(con, settings.current(con, cfg))
    assert settings.problems(con)["faces"]["message"] == faces.MISSING  # said once, on the Status page
    assert not con.execute("SELECT 1 FROM activity WHERE message LIKE 'faces failed%'").fetchone()
