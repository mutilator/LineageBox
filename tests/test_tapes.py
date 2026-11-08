"""Long tapes and sound: sound recordings (cassettes, voice memos) catalogued, played and transcribed, songs left
out; long videos' chapters and the frames that cover them."""
import subprocess

import pytest
from fastapi.testclient import TestClient

from lineagebox import catalog, chapters, copies, describe, search, speech, tours, video
from lineagebox.faces import STAGE_FACES
from lineagebox.ingest import STAGE_INGEST, ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests.conftest import LAN, rel

needs_ffmpeg = pytest.mark.skipif(not video.available(), reason="needs ffmpeg and ffprobe")


def make_sound(path, seconds=6, codec=None, tags=(), fmt=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    args = [video.tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}"]
    args += [*(["-c:a", codec] if codec else []), *[x for k, v in tags for x in ("-metadata", f"{k}={v}")],
             *(["-f", fmt] if fmt else []), str(path)]
    subprocess.run(args, check=True)
    return path


def row(con, path):
    return con.execute("SELECT i.* FROM image i JOIN file f ON f.image_id = i.id WHERE f.path = ?", (path,)).fetchone()


# ---- without ffmpeg ------------------------------------------------------------------------------------------------

def test_chapters_start_after_blanks_and_at_the_biggest_changes():
    keys = [(t, 0.05, False) for t in range(0, 1800, 2)]
    keys[100] = (200, 0.0, True)    # a blank between two recordings on the tape...
    keys[101] = (202, 0.0, True)
    keys[102] = (204, 0.1, False)   # ...and the first picture after it
    keys[300] = (600, 0.9, False)   # the picture changes completely
    keys[310] = (620, 0.95, False)  # again, too soon after: the stronger of the two is kept
    keys[500] = (1000, 0.3, False)  # a pan: under the cut
    keys[899] = (1798, 0.9, False)  # too close to the end
    assert video.chapters(keys, 1800) == [204, 620]
    assert video.chapters(keys, 500) == []  # a short video has none
    assert video.chapters([(5, 0.0, True), (7, 0.2, False)], 1800) == []  # a blank at the very start isn't one


# ---- with ffmpeg ---------------------------------------------------------------------------------------------------

@needs_ffmpeg
def test_sound_recordings_are_catalogued_played_and_transcribed(con, cfg, library, monkeypatch):
    make_sound(library / "Tapes" / "Grandma side A.m4a", tags=[("creation_time", "1994-05-01T10:00:00Z")])
    make_sound(library / "Tapes" / "memo.3gp", codec="aac", fmt="3gp")  # a "video" with no picture: a voice note
    make_sound(library / "Tapes" / "old memo.wma", codec="wmav2")       # browsers can't play: a copy is made
    make_sound(library / "Music" / "song.mp3", tags=[("artist", "A Band"), ("album", "Hits"), ("track", "3")])
    scan(con, cfg)
    i = ingest(con, cfg, show_progress=False)
    tape, memo, old = (row(con, rel("Tapes", n)) for n in ("Grandma side A.m4a", "memo.3gp", "old memo.wma"))
    assert (tape["media"], tape["codecs"], tape["playable"]) == ("audio", "aac", "original")
    assert 5.5 < tape["duration"] < 6.5 and tape["taken_at"].startswith("1994-05-01")
    assert (memo["media"], old["media"], old["playable"]) == ("audio", "audio", "needed")
    assert tape["stage"] & STAGE_INGEST and tape["stage"] & STAGE_FACES  # nothing for the faces stage to look at
    assert cfg.preview_path(tape["sha256"]).is_file() and cfg.thumb_path(tape["sha256"]).is_file()
    # a song from an album is music: left out, and not on the list of files that couldn't be read
    song = row(con, rel("Music", "song.mp3"))
    assert song["error"].startswith("song:") and i.decode_errors >= 1
    assert song["id"] not in [r["id"] for r in catalog.undecodable(con)]
    assert catalog.stats(con)["recordings"] == 3
    # never described (no picture), never in a tour's slides, but transcribed and found by search
    assert tape["id"] not in [r["id"] for r in describe.pending(con, cfg.ollama_model)]
    assert tape["id"] not in [p["id"] for p in tours.photos(con, tours.clean({"title": "All", "media": "both"}))]
    monkeypatch.setattr(speech.Whisper, "transcribe", lambda self, wav: {"model": "fake", "duration": 6.0, "segments": [
        {"start": 0.5, "end": 3.0, "text": " We moved to the farm in the spring", "avg_logprob": -0.2}]})
    assert speech.count_pending(con) == 3
    assert speech.transcribe(con, cfg).done == 3
    search.index_new(con)
    assert tape["id"] in [r["id"] for r in search.query(con, "farm")]
    assert tape["id"] in [r["id"] for r in search.query(con, "recording")]

    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get(f"/image/{tape['id']}").text
    assert f'<audio id="player" controls preload="metadata" src="/play/{tape["id"]}"' in page
    assert "Said in the recording" in page and "We moved to the farm" in page
    assert f'data-api="/api/images/{tape["id"]}/faces"' not in page and 'class="document-panel"' not in page
    played = client.get(f"/play/{tape['id']}")
    assert played.status_code == 200 and played.headers["content-type"] == "audio/mp4"
    folder_id = con.execute("SELECT folder_id FROM file WHERE image_id = ?", (tape["id"],)).fetchone()[0]
    folder = client.get(f"/folder/{folder_id}")
    assert "Sound recording" in folder.text and "🎙" in folder.text
    # the one browsers can't play gets a copy they can, next to it
    assert copies.make(con, cfg) == 1
    assert con.execute("SELECT playable FROM image WHERE id = ?", (old["id"],)).fetchone()[0] == "copy"
    assert video.probe(copies.find_copy(con, cfg, old["id"])).audio_codec == "aac"
    assert client.get(f"/play/{old['id']}").headers["content-type"] == "audio/mp4"
    # its picture gone, it is drawn again from the file
    cfg.preview_path(tape["sha256"]).unlink()
    con.execute(f"UPDATE image SET stage = stage & ~{STAGE_INGEST} WHERE id = ?", (tape["id"],))
    con.commit()
    assert ingest(con, cfg, show_progress=False).rebuilt == 1 and cfg.preview_path(tape["sha256"]).is_file()


@needs_ffmpeg
def test_long_videos_get_chapters_and_more_frames(con, cfg, library):
    # an 11-minute tape: a test card, a blank, colour bars, then a moving pattern
    path = library / "Tapes" / "tape1.mp4"
    path.parent.mkdir(parents=True)
    parts = [("rgbtestsrc", 200), ("color=c=black", 6), ("smptebars", 214), ("testsrc", 240)]
    args = [video.tool("ffmpeg"), "-v", "error", "-y"]
    for source, seconds in parts:
        args += ["-f", "lavfi", "-i", f"{source}:size=64x48:rate=2:duration={seconds}" if "=" in source
                 else f"{source}=size=64x48:rate=2:duration={seconds}"]
    chain = "".join(f"[{n}:v]" for n in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=0[v]"
    subprocess.run([*args, "-filter_complex", chain, "-map", "[v]", "-c:v", "libx264", "-g", "4", "-pix_fmt",
                    "yuv420p", str(path)], check=True)
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    tape = row(con, rel("Tapes", "tape1.mp4"))
    frames = con.execute("SELECT count(*) FROM video_frame WHERE image_id = ?", (tape["id"],)).fetchone()[0]
    assert 3 <= frames <= video.frame_count(660) == 28  # by length; near-identical ones are left out

    assert chapters.count_pending(con) == 1
    s = chapters.find(con, cfg)
    assert (s.done, s.failed) == (1, 0)
    found = chapters.for_page(con, tape["id"])
    starts = [round(c["at"]) for c in found]
    assert starts[0] == 0 and len(starts) == 3, starts
    assert 200 <= starts[1] <= 210 and 415 <= starts[2] <= 425  # after the blank, and where the bars end
    assert all(c["frame"] is not None for c in found)
    assert con.execute("SELECT count(*) FROM video_frame WHERE image_id = ?", (tape["id"],)).fetchone()[0] > frames
    assert chapters.count_pending(con) == 0
    page = TestClient(webapp.create_app(cfg), client=LAN).get(f"/image/{tape['id']}").text
    assert "3 chapters" in page and f'data-seek="{found[1]["at"]}"' in page


def test_a_home_burned_cd_is_a_recording_not_a_song():
    """A computer's player tags a CD burned at home as if it were an album: 'Track 1', a number or 'Unknown Album'
    for the album, a family member as the artist. Those are the family's recordings (old voicemails), not music."""
    assert video.is_song({"artist": "A Band", "album": "Hits", "track": "3", "title": "Our Song"})
    assert video.is_song({"artist": "A Band", "album": "Hits", "track": "3"})
    assert not video.is_song({"artist": "Ruth Miller", "album": "259195", "track": "1", "title": "Track 1"})
    assert not video.is_song({"artist": "Ruth Miller", "album": "Unknown Album (5/6/2019)", "track": "2"})
    assert not video.is_song({"artist": "Unknown Artist", "album": "Voicemails", "track": "2", "title": "Hi Mom"})
    assert not video.is_song({"album": "Hits", "track": "3"})  # no artist: a recording, as before


@needs_ffmpeg
def test_files_left_out_under_an_older_song_rule_are_read_again(cfg, con, library):
    make_sound(library / "Voicemails" / "01 Track 1.mp3",
               tags=[("artist", "Ruth Miller"), ("album", "259195"), ("track", "1"), ("title", "Track 1")])
    scan(con, cfg)
    ingest(con, cfg)
    path = rel("Voicemails", "01 Track 1.mp3")
    image_id = row(con, path)["id"]
    con.execute("UPDATE image SET error = 'song: an older wording of the rule' WHERE id = ?", (image_id,))
    con.commit()
    ingest(con, cfg)  # read again under today's rule: a recording
    again = row(con, path)
    assert again["error"] is None and again["media"] == "audio"
