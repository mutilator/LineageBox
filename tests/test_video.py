import json
import subprocess
import sys
from datetime import datetime, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from lineagebox import cleanup, copies, search, speech, tools, video
from lineagebox.describe import _record
from lineagebox.faces import one_per_person
from lineagebox.folders import FolderIndex
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests.conftest import LAN, rel

APP = {"X-Requested-With": "lineagebox"}
needs_ffmpeg = pytest.mark.skipif(not video.available(), reason="needs ffmpeg and ffprobe")


def make_video(path, seconds=12, size="320x240", source="life", audio=True, fmt=None, extra=()):
    """A short test video made by ffmpeg, with a tone: by default Conway's life, so frames seconds apart differ (a
    still test pattern would give one frame, the rest being near-identical)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pattern = f"{source}:size={size}:rate=10" if "=" in source else f"{source}=size={size}:rate=10"
    args = [video.tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", pattern]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440"]
    args += ["-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p",
             *(["-c:a", "aac" if fmt != "mpegts" else "ac3"] if audio else []),
             *extra, *(["-f", fmt] if fmt else []), str(path)]
    subprocess.run(args, check=True)
    return path


# ---- without ffmpeg ------------------------------------------------------------------------------------------------

def mdpm_block(tags: dict[int, bytes]) -> bytes:
    body = bytes([len(tags)]) + b"".join(bytes([t]) + v for t, v in tags.items())
    return bytes.fromhex("17ee8c60f84d11d98cd60800200c9a66") + b"MDPM" + body


def test_camcorder_date_from_the_stream():
    # BCD: 2010-09-29 18:39:45. The first block has only maker tags, as some camcorders write.
    head = b"\x00" * 100 + mdpm_block({0xe0: b"\x01\x02\x03\x04"}) + b"\x47" * 50 + \
        mdpm_block({0x18: b"\x2a\x20\x10\x09", 0x19: b"\x29\x18\x39\x45"})
    assert video.mdpm_date(head) == "2010-09-29T18:39:45"
    assert video.mdpm_date(b"no date here") is None
    # the stream escapes 00 00 01 as 00 00 03 01, which would shift the tags after it: 2010-09-15 00:00:01
    raw = mdpm_block({0x18: b"\x2a\x20\x10\x09", 0x19: b"\x15\x00\x00\x01"})
    escaped = raw.replace(b"\x00\x00\x01", b"\x00\x00\x03\x01")
    assert escaped != raw and video.mdpm_date(escaped) == "2010-09-15T00:00:01"


def probe(created=None):
    return video.Probe(duration=10, width=1920, height=1080, video_codec="h264", audio_codec="aac", interlaced=False,
                       created=created, gps=None, camera=None)


def test_video_dates_prefer_the_camera_then_the_container_then_the_name():
    utc = datetime(2018, 11, 4, 19, 40, 55, tzinfo=timezone.utc)
    local = utc.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
    # a phone: creation_time is real UTC, and the name has the local time
    named_local = utc.astimezone().strftime("VID_%Y%m%d_%H%M%S.mp4")
    assert video.taken_at(probe(utc), b"", named_local) == (local, "camera")
    # a camera that writes its clock as if it were UTC: the name matches the time as written
    if utc.astimezone().utcoffset():
        assert video.taken_at(probe(utc), b"", "VID_20181104_194055.mp4") == ("2018-11-04T19:40:55", "camera")
        # but Pixel names are UTC themselves, so a match there says nothing
        assert video.taken_at(probe(utc), b"", "PXL_20181104_194055123.mp4") == (local, "camera")
    assert video.taken_at(probe(), b"", "VID_20160811_182239.mp4") == ("2016-08-11T18:22:39", "filename")
    assert video.taken_at(probe(), b"", "00005.MTS") == (None, None)
    assert video._created("1904-01-01T00:00:00.000000Z") is None


def test_copy_names():
    assert video.is_copy("00005.MTS.720p.mp4") and video.is_copy("clip.MOV.720P.MP4")
    assert not video.is_copy("holiday.720p.mp4") and not video.is_copy("00005.MTS")
    assert video.sample_times(None) == [0.0]
    assert len(video.sample_times(5)) == video.MIN_FRAMES and len(video.sample_times(36000)) == video.MAX_FRAMES
    # one every 10 s for the first 20 (a phone's clip), then one a minute: a 44-minute video 61, a 2-hour tape 120
    assert [video.frame_count(s) for s in (12, 200, 2627, 5000, 7200)] == [3, 20, 61, 100, 120]
    assert video._gps({"location": "+45.5152-122.6784/"}) == (45.5152, -122.6784)
    assert video._gps({"location": "+00.0000+000.0000/"}) is None


def test_a_program_that_fails_hangs_or_is_missing_is_said_in_a_sentence(tmp_path):
    py = [sys.executable, "-c"]
    assert tools.run([*py, "import sys; sys.stdout.write('out')"]) == b"out"
    with pytest.raises(tools.Error, match="^Invalid data found$") as e:  # the program's last line about it
        tools.run([*py, "import sys; print('reading', file=sys.stderr); sys.exit('Invalid data found')"])
    assert type(e.value) is tools.Error
    with pytest.raises(tools.Error, match="exit code 3"):
        tools.run([*py, "import sys; sys.exit(3)"])
    with pytest.raises(tools.TimedOut, match="took over 1 s"):
        tools.run([*py, "import time; time.sleep(10)"], timeout=1)
    with pytest.raises(tools.Unavailable, match="no-such-tool could not be run"):
        tools.run([str(tmp_path / "no-such-tool"), "-v"])
    assert video.Error is tools.Error  # what callers of video catch


def test_one_face_per_person_per_video():
    rng = np.random.default_rng(0)
    a, b = rng.normal(size=512), rng.normal(size=512)
    unit = lambda v: (v / np.linalg.norm(v)).astype(np.float32)

    def face(e, frame, quality):
        return (1, 0, 0, .1, .1, b"", .9, quality, 5, "M", unit(e).tobytes(), frame)

    faces = [face(a, 0, 20), face(a + rng.normal(scale=.3, size=512), 1, 25), face(b, 1, 22), face(b, 2, 18)]
    kept = one_per_person(faces)
    assert sorted(f[7] for f in kept) == [22, 25]  # the clearest face of each person
    # two faces on one frame are two people, however alike
    assert len(one_per_person([face(a, 0, 20), face(a, 0, 21)])) == 2


def test_transcript_filtering():
    seg = lambda text, lp=-0.3, ns=0.1: {"start": 0, "end": 1, "text": text, "avg_logprob": lp, "no_speech_prob": ns}
    kept = speech.keep([seg(" Happy birthday, Owen!"), seg(" Thank you."), seg(" you"), seg("[Music]"), seg("♪♪"),
                        seg(" mumble", lp=-0.9), seg(" maybe words", lp=-0.7, ns=0.9), seg(" blow out the candles"),
                        seg(" go go go"), seg(" go go go"), seg(" go go go")])
    assert [t for _, _, t in kept] == ["Happy birthday, Owen!", "blow out the candles", "go go go", "go go go"]


# ---- with ffmpeg ---------------------------------------------------------------------------------------------------

@pytest.fixture
def videos(library):
    make_video(library / "2016" / "clip.mp4", extra=["-metadata", "creation_time=2016-08-11T22:22:39Z"])
    make_video(library / "2016" / "Camcorder" / "00001.MTS", fmt="mpegts")
    make_video(library / "2016" / "dark.mp4", seconds=4, source="color=c=black", audio=False)
    (library / "Owen" / "2016").mkdir(parents=True)
    (library / "Owen" / "2016" / "00001.MTS").write_bytes((library / "2016" / "Camcorder" / "00001.MTS").read_bytes())
    return library


def video_row(con, path):
    return con.execute("SELECT i.* FROM image i JOIN file f ON f.image_id = i.id WHERE f.path = ?", (path,)).fetchone()


@needs_ffmpeg
def test_videos_are_catalogued_with_frames(con, cfg, videos):
    s = scan(con, cfg)
    assert s.files == 10  # 6 photo files + 4 video files
    i = ingest(con, cfg, show_progress=False)
    errors = [tuple(r) for r in con.execute("SELECT format, error FROM image WHERE error IS NOT NULL")]
    assert i.decode_errors == 1, errors  # broken.jpg
    assert i.duplicates == 2  # the copied photo and the copied MTS
    clip = video_row(con, rel("2016", "clip.mp4"))
    assert (clip["media"], clip["format"], clip["codecs"], clip["playable"]) == ("video", "mp4", "h264/aac", "original")
    assert (clip["width"], clip["height"]) == (320, 240) and 11.5 < clip["duration"] < 12.5
    expected = datetime(2016, 8, 11, 22, 22, 39, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    assert (clip["taken_at"], clip["taken_at_source"]) == (expected.isoformat(timespec="seconds"), "camera")
    assert clip["phash"] is None  # videos stay out of the similar-shot groups
    frames = con.execute("SELECT idx, at FROM video_frame WHERE image_id = ? ORDER BY idx", (clip["id"],)).fetchall()
    assert len(frames) == 3 and all(cfg.frame_path(clip["sha256"], f["idx"]).is_file() for f in frames)
    assert cfg.preview_path(clip["sha256"]).is_file() and cfg.thumb_path(clip["sha256"]).is_file()

    mts = video_row(con, rel("2016", "Camcorder", "00001.MTS"))
    assert (mts["format"], mts["codecs"], mts["playable"]) == ("mts", "h264/ac3", "needed")
    dark = video_row(con, rel("2016", "dark.mp4"))
    assert dark["codecs"] == "h264/no sound"
    assert con.execute("SELECT count(*) FROM video_frame WHERE image_id = ?", (dark["id"],)).fetchone()[0] == 1
    assert ingest(con, cfg).pending == 0


@needs_ffmpeg
def test_motion_photo_frames_come_from_the_clip(tmp_path):
    # Google's motion photos: a short clip and a larger single-frame still in one MP4. ffmpeg picks the still by
    # itself, which has nothing at 0.5 s.
    path = tmp_path / "MVIMG_20200524_135649.MP4"
    subprocess.run([video.tool("ffmpeg"), "-v", "error", "-y", "-t", "2", "-f", "lavfi", "-i", "life=size=160x120:rate=25",
                    "-f", "lavfi", "-i", "testsrc=size=640x480:rate=25:duration=0.04", "-map", "0:v", "-map", "1:v",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    p = video.probe(path)
    assert (p.width, p.height, p.stream) == (160, 120, 0)
    assert [video.frame(path, at, 2048, p).size for at in video.sample_times(p.duration)] == [(160, 120)] * 3


@needs_ffmpeg
def test_stored_files_are_read_only_as_videos_and_recordings(tmp_path):
    # ffmpeg is told which kinds of file it may read them as (video.READS): the usual ones still open...
    picture = ["-f", "lavfi", "-i", "testsrc=size=160x120:rate=10", "-t", "1"]
    tone = ["-f", "lavfi", "-i", "sine=frequency=440", "-t", "1"]
    kinds = {"clip.mov": [*picture, "-c:v", "libx264", "-pix_fmt", "yuv420p"],
             "clip.mkv": [*picture, "-c:v", "libx264", "-pix_fmt", "yuv420p"],
             "clip.avi": [*picture, "-c:v", "mpeg4"], "clip.mpg": [*picture, "-c:v", "mpeg2video", "-f", "mpeg"],
             "clip.wmv": [*picture, "-c:v", "wmv2"], "tape.wav": tone, "tape.flac": tone, "tape.aiff": tone,
             "tape.ogg": [*tone, "-c:a", "flac"], "memo.m4a": [*tone, "-c:a", "aac"]}
    for name, args in kinds.items():
        subprocess.run([video.tool("ffmpeg"), "-v", "error", "-y", *args, str(tmp_path / name)], check=True)
        p = video.probe(tmp_path / name)
        assert p.sound_only == name.startswith(("tape", "memo")), name
    assert video.audio_wav(tmp_path / "tape.flac")[:4] == b"RIFF"
    # ...and a file that is something else is refused, though ffmpeg could read it: a picture named as a video
    subprocess.run([video.tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120",
                    "-frames:v", "1", "-f", "image2", "-c:v", "png", str(tmp_path / "still.png")], check=True)
    (tmp_path / "still.mp4").write_bytes((tmp_path / "still.png").read_bytes())
    with pytest.raises(video.Error):
        video.probe(tmp_path / "still.mp4")
    with pytest.raises(video.Error):
        video.frame(tmp_path / "still.mp4", 0, 100, probe())


@needs_ffmpeg
def test_720p_copies_and_cleanup(con, cfg, videos):
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    mts = video_row(con, rel("2016", "Camcorder", "00001.MTS"))
    assert copies.count_pending(con) == 1
    assert copies.make(con, cfg) == 1
    first = cfg.share_root / copies.present_files(con, mts["id"])[0]
    made = video.copy_path(first)
    assert made.name == first.name + ".720p.mp4" and made.is_file()
    assert json.loads(subprocess.run([video.tool("ffprobe"), "-v", "error", "-print_format", "json", "-show_format",
                                      str(made)], capture_output=True, check=True).stdout)["format"]["tags"][
        "comment"] == f"LineageBox 720p copy of {first.name}"
    p = video.probe(made)
    assert (p.video_codec, p.audio_codec) == ("h264", "aac")
    assert con.execute("SELECT playable FROM image WHERE id = ?", (mts["id"],)).fetchone()[0] == "copy"
    # the copy is no video of its own: not catalogued, so in no count, duplicate list or folder
    files = con.execute("SELECT count(*) FROM file").fetchone()[0]
    scan(con, cfg)
    assert con.execute("SELECT count(*) FROM file").fetchone()[0] == files
    assert ".720p" not in json.dumps([dict(r) for r in con.execute("SELECT path FROM file")])

    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get(f"/image/{mts['id']}").text
    assert f'src="/play/{mts["id"]}"' in page and "720p copy" in page
    part = client.get(f"/play/{mts['id']}", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100

    # removing the copy of the video that has the 720p copy next to it: it moves next to the copy that stays
    f = con.execute("SELECT id FROM file WHERE path = ?", (first.relative_to(cfg.share_root).as_posix(),)).fetchone()
    cleanup.remove_files(con, cfg, [f["id"]], "test")
    other = cfg.share_root / copies.present_files(con, mts["id"])[0]
    assert not made.exists() and video.copy_path(other).is_file()
    assert client.get(f"/play/{mts['id']}").status_code == 200
    # removing the last copy takes the 720p copy along to the Removed folder, and restoring brings it back
    cleanup.remove_images(con, cfg, [mts["id"]], "test")
    assert not video.copy_path(other).exists()
    removed = con.execute("SELECT removed_path FROM file WHERE path = ?",
                          (other.relative_to(cfg.share_root).as_posix(),)).fetchone()[0]
    assert video.copy_path(cfg.share_root / removed).is_file()
    cleanup.restore(con, cfg, [r[0] for r in con.execute("SELECT id FROM file WHERE removed_at IS NOT NULL")])
    assert video.copy_path(other).is_file() or video.copy_path(first).is_file()
    # a copy deleted by hand is made again
    for path in (video.copy_path(other), video.copy_path(first)):
        path.unlink(missing_ok=True)
    assert copies.check(con, cfg) == 1 and copies.count_pending(con) == 1


@needs_ffmpeg
def test_transcripts_are_searchable(con, cfg, videos, monkeypatch):
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    clip = video_row(con, rel("2016", "clip.mp4"))
    sent = []

    def fake(self, wav):
        sent.append(wav)
        return {"model": "fake-whisper", "language": "en", "duration": 12.0, "seconds": 0.5, "segments": [
            {"start": 1.0, "end": 2.5, "text": " Happy birthday, Owen!", "avg_logprob": -0.2, "no_speech_prob": 0.01},
            {"start": 9.0, "end": 11.0, "text": " Thank you.", "avg_logprob": -0.9, "no_speech_prob": 0.7}]}

    monkeypatch.setattr(speech.Whisper, "transcribe", fake)
    assert speech.count_pending(con) == 2  # the MTS and the clip; the dark video has no sound
    s = speech.transcribe(con, cfg)
    assert (s.done, s.failed) == (2, 0)
    assert sent[0][:4] == b"RIFF" and int.from_bytes(sent[0][4:8], "little") == len(sent[0]) - 8
    t = con.execute("SELECT * FROM transcript WHERE image_id = ?", (clip["id"],)).fetchone()
    assert t["text"] == "Happy birthday, Owen!" and json.loads(t["segments"]) == [[1.0, 2.5, "Happy birthday, Owen!"]]
    photo = con.execute("SELECT id FROM image WHERE media = 'photo' AND error IS NULL").fetchone()[0]
    _record(con, photo, "fake", {"caption": "A boy plays a video game on a video call.",
                                 "tags": ["video game", "video call", "video"]}, FolderIndex(con))
    search.rebuild(con)
    assert clip["id"] in [r["id"] for r in search.query(con, "birthday")]
    found = [(r["id"], r["media"]) for r in search.query(con, "videos")]
    assert clip["id"] in dict(found) and found[-1] == (photo, "photo")  # the photo says 'video' more, but comes last
    page = TestClient(webapp.create_app(cfg), client=LAN).get(f"/image/{clip['id']}").text
    assert "Said in the video" in page and 'data-seek="1.0"' in page and "Happy birthday, Owen!" in page
