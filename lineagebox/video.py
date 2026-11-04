"""Videos, through ffmpeg: what is in a file, when it was recorded, frames to look at, the sound for transcripts, and
720p copies that browsers can play.

Measured on the library LineageBox was developed on (over a thousand videos): 95% are H.264, but browsers cannot play a
quarter of them as they are (AVCHD .MTS, MOV with PCM sound, 3GP/3G2, AVI, WMV, HEVC). The camcorder's .MTS files
are interlaced and have no date in the container; every one has the recording date in the H.264 stream (see
mdpm_date). Phones write creation_time in UTC. On a small ARM board a frame takes 0.3-0.7 s (1.4 s at 4K) and a 720p
copy of 1080i takes about as long as the video.
"""
import io
import json
import math
import os
import re
import shutil
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

from lineagebox import dates, tools

FRAME_EVERY = 10  # seconds of video per sampled frame, between MIN_FRAMES and MAX_FRAMES frames
MIN_FRAMES, MAX_FRAMES = 3, 20
HEAD_BYTES = 4 << 20  # the start of a file, read while hashing it: enough to find an .MTS file's date
VIDEO_MIN_YEAR = 1990  # a container's date before this is a clock never set: no camera wrote one
SOUND_SECONDS = 9000  # the most sound sent for a transcript: what the Whisper service takes (300 MB of 16 kHz WAV)
COPY_HOURS = 12  # the longest a 720p copy may take, whatever the video's length
MISSING = ("ffmpeg wasn't found, so videos are not read, copied or transcribed until it is installed "
           "(docs/install.md).")
COPY_SUFFIX = ".720p.mp4"  # 00005.MTS -> 00005.MTS.720p.mp4, next to it
COPY_COMMENT = "LineageBox 720p copy of {name}"
# what plays in every current browser without a copy: these containers, with these codecs
BROWSER_EXTENSIONS = {".mp4", ".m4v", ".mov"}
BROWSER_VIDEO = {"h264"}
BROWSER_AUDIO = {None, "aac", "mp3"}
_ISO6709 = re.compile(r"^([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)")
_MDPM = bytes.fromhex("17ee8c60f84d11d98cd60800200c9a66") + b"MDPM"


# What can go wrong with ffmpeg (tools.py): the video is damaged or not one ffmpeg knows (Error), ffmpeg or ffprobe
# isn't there (Unavailable: nothing is wrong with the video), or it took too long (TimedOut: tried again later)
Error, Unavailable, TimedOut = tools.Error, tools.Unavailable, tools.TimedOut


@dataclass
class Probe:
    duration: float | None    # seconds
    width: int | None         # as shown: after rotation, in square pixels
    height: int | None
    video_codec: str | None
    audio_codec: str | None   # None: no sound
    interlaced: bool
    created: datetime | None  # the container's creation_time, in UTC
    gps: tuple[float, float] | None
    camera: str | None
    # The video stream these describe, which frames and copies use. Google's motion photos hold two: the clip, and a
    # larger single-frame still that ffmpeg would pick by itself (and then find nothing to seek to).
    stream: int = 0

    @property
    def codecs(self) -> str:
        return f"{self.video_codec or '?'}/{self.audio_codec or 'no sound'}"


def tool(name: str) -> str:
    """ffmpeg or ffprobe: from the folder in LINEAGEBOX_FFMPEG, else from PATH."""
    if folder := os.environ.get("LINEAGEBOX_FFMPEG"):
        return str(Path(folder) / name)
    return shutil.which(name) or name


def available() -> bool:
    return all(shutil.which(tool(n)) for n in ("ffmpeg", "ffprobe"))


def probe(path: Path) -> Probe:
    out = json.loads(tools.run([tool("ffprobe"), "-v", "error", "-print_format", "json", "-show_format",
                                "-show_streams", str(path)], timeout=120))
    streams = out.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video" and not _is_cover(s)), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is None:
        raise Error("no video stream")
    fmt = out.get("format", {})
    tags = {k.lower(): str(val) for k, val in {**fmt.get("tags", {}), **v.get("tags", {})}.items()}
    duration = _number(fmt.get("duration")) or _number(v.get("duration"))
    width, height = v.get("width"), v.get("height")
    sar = v.get("sample_aspect_ratio", "1:1")
    if width and re.match(r"^\d+:\d+$", sar) and sar not in ("1:1", "0:1"):
        n, d = map(int, sar.split(":"))
        width = round(width * n / d)  # anamorphic: stored narrower than shown
    rotation = next((sd.get("rotation") for sd in v.get("side_data_list", []) if "rotation" in sd), None)
    if rotation is None and tags.get("rotate"):
        rotation = _number(tags["rotate"])
    if rotation and round(abs(rotation)) % 180 == 90:
        width, height = height, width
    return Probe(duration=duration, width=width, height=height, video_codec=v.get("codec_name"),
                 audio_codec=a.get("codec_name") if a else None,
                 interlaced=v.get("field_order") not in (None, "progressive", "unknown"),
                 created=_created(tags.get("creation_time")), gps=_gps(tags), camera=_camera(tags),
                 stream=int(v.get("index", 0)))


def _is_cover(stream: dict) -> bool:
    return bool(stream.get("disposition", {}).get("attached_pic"))


def _number(value) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) and n > 0 else None


def _created(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    return dt if dt.year >= VIDEO_MIN_YEAR else None  # 1904 and 1970: the camera wrote zero


def _gps(tags: dict) -> tuple[float, float] | None:
    m = _ISO6709.match(tags.get("location") or tags.get("com.apple.quicktime.location.iso6709") or "")
    if not m:
        return None
    lat, lon = float(m[1]), float(m[2])
    if (lat == 0 and lon == 0) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return round(lat, 6), round(lon, 6)


def _camera(tags: dict) -> str | None:
    make = tags.get("com.android.manufacturer") or tags.get("com.apple.quicktime.make") or tags.get("make") or ""
    model = tags.get("com.android.model") or tags.get("com.apple.quicktime.model") or tags.get("model") or ""
    if make and model.lower().startswith(make.split()[0].lower()):
        return model
    return " ".join(p for p in (make, model) if p) or None


def mdpm_date(head: bytes) -> str | None:
    """The recording date AVCHD camcorders put in the H.264 stream (an MDPM block of SEI user data), as local time.

    Tags 0x18 and 0x19 hold the date and time in BCD. Some camcorders write only maker tags in the first
    block and the date in a later one, so every block in the head is looked at. The stream escapes 00 00 03, which
    would shift the fixed-size tags."""
    bcd = lambda b: (b >> 4) * 10 + (b & 15)
    i = head.find(_MDPM)
    while i >= 0:
        block = head[i + len(_MDPM): i + len(_MDPM) + 300].replace(b"\x00\x00\x03", b"\x00\x00")
        tags = {block[1 + 5 * k]: block[2 + 5 * k: 6 + 5 * k] for k in range(block[0]) if 6 + 5 * k <= len(block)}
        if 0x18 in tags and 0x19 in tags:
            a, b = tags[0x18], tags[0x19]
            if iso := dates.from_parts(bcd(a[1]) * 100 + bcd(a[2]), bcd(a[3]), bcd(b[0]), bcd(b[1]), bcd(b[2]),
                                       bcd(b[3])):
                return iso
        i = head.find(_MDPM, i + 1)
    return None


def taken_at(p: Probe, head: bytes, name: str) -> tuple[str | None, str | None]:
    """(local ISO time, source): the camcorder's own date, then the container's, then the file name. None, None
    when the file says nothing (the caller falls back to the file time)."""
    if iso := mdpm_date(head):
        return iso, "camera"
    named = dates.from_filename(name)
    if p.created:
        local = p.created.astimezone().replace(tzinfo=None)  # the system's zone (TZ in Docker)
        as_written = p.created.replace(tzinfo=None)
        # Most cameras do not know their time zone and write their clock as if it were UTC: when the file name's
        # time (local on most cameras and phones, except Pixel's PXL_ names, which are UTC) matches the time as
        # written, it was local time all along.
        if named and not name.upper().startswith("PXL_") and abs(datetime.fromisoformat(named) - as_written) <= \
                timedelta(minutes=2) and abs(datetime.fromisoformat(named) - local) > timedelta(minutes=2):
            local = as_written
        if local <= datetime.now() + dates.FUTURE_SLACK:
            return local.isoformat(timespec="seconds"), "camera"
    if named:
        return named, "filename"
    return None, None


def sample_times(duration: float | None) -> list[float]:
    """Where to take frames: one per FRAME_EVERY seconds, spread evenly, avoiding the very start and end (fades,
    the camera being raised and lowered)."""
    if not duration or duration < 1:
        return [0.0]
    n = max(MIN_FRAMES, min(MAX_FRAMES, math.ceil(duration / FRAME_EVERY)))
    return [round(duration * (k + 0.5) / n, 3) for k in range(n)]


def _fit(max_side: int) -> str:
    return f"scale=w='min({max_side},iw)':h='min({max_side},ih)':force_original_aspect_ratio=decrease"


def frame(path: Path, at: float, max_side: int, p: Probe) -> Image.Image:
    """One upright frame at `at` seconds, at most max_side pixels on its long side. ffmpeg turns phone videos upright
    itself; interlaced camcorder frames are deinterlaced, or every movement shows comb lines."""
    vf = ",".join([*(["yadif"] if p.interlaced else []), "scale='trunc(iw*sar/2)*2':ih", "setsar=1", _fit(max_side)])
    grab = ["-map", f"0:{p.stream}", "-frames:v", "1", "-vf", vf, "-f", "image2pipe", "-c:v", "bmp", "-"]
    out = tools.run([tool("ffmpeg"), "-v", "error", "-nostdin", "-ss", f"{at:.3f}", "-i", str(path), *grab],
                    timeout=120)
    if len(out) < 54:
        # MPEG-TS has no index: the quick seek can land past the keyframe it needed (camcorders put one every half
        # second, other recorders far fewer). Decoding from the start up to that moment always finds the frame.
        out = tools.run([tool("ffmpeg"), "-v", "error", "-nostdin", "-i", str(path), "-ss", f"{at:.3f}", *grab],
                        timeout=900)
    if len(out) < 54:
        raise Error(f"no frame at {at:.1f} s")
    im = Image.open(io.BytesIO(out))
    im.load()
    return im.convert("RGB")


def audio_wav(path: Path) -> bytes:
    """The sound as 16 kHz mono 16-bit WAV, what Whisper reads: its first SOUND_SECONDS (a digitised tape can be far
    longer, and the whole of it would be held in memory)."""
    wav = bytearray(tools.run([tool("ffmpeg"), "-v", "error", "-nostdin", "-i", str(path), "-vn",
                               "-t", str(SOUND_SECONDS), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav",
                               "-"], timeout=1800, nice=True))
    # Written to a pipe, ffmpeg cannot go back to fill in the sizes; some readers stop at the bogus ones.
    data = wav.find(b"data", 12)
    if wav[:4] != b"RIFF" or data < 0:
        raise Error("ffmpeg did not write a WAV file")
    struct.pack_into("<I", wav, 4, len(wav) - 8)
    struct.pack_into("<I", wav, data + 4, len(wav) - data - 8)
    return bytes(wav)


def plays_in_browsers(ext: str, p: Probe) -> bool:
    return ext.lower() in BROWSER_EXTENSIONS and p.video_codec in BROWSER_VIDEO and p.audio_codec in BROWSER_AUDIO


def is_copy(name: str) -> bool:
    """A 720p copy LineageBox made (00005.MTS.720p.mp4): never catalogued as a video of its own. A file of the user's
    named holiday.720p.mp4 is not one: before the suffix there must be a video's own name."""
    lower = name.lower()
    return lower.endswith(COPY_SUFFIX) and os.path.splitext(lower[:-len(COPY_SUFFIX)])[1] != ""


def copy_path(original: Path) -> Path:
    return original.with_name(original.name + COPY_SUFFIX)


def make_copy(original: Path, taken: str | None, threads: int = 4, target: Path | None = None) -> Path:
    """Write a 720p H.264/AAC MP4 next to the original, or at target (written to a hidden .part file first, so a
    half-written copy is never mistaken for a finished one). It has the original's dates, owner and permissions, and
    says in its comment what it is a copy of."""
    target = target or copy_path(original)
    st = original.stat()  # first: no folder is made on a share whose video isn't there
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(f".{target.name}.part")
    meta = ["-map_metadata", "0", "-metadata", "comment=" + COPY_COMMENT.format(name=original.name)]
    if taken:  # .MTS files have no creation_time of their own; players and photo apps sort by it
        utc = datetime.fromisoformat(taken).astimezone(timezone.utc)
        meta += ["-metadata", f"creation_time={utc:%Y-%m-%dT%H:%M:%S}.000000Z"]
    # the short side becomes 720 (or stays, if smaller); quoted, as the commas would otherwise separate filters
    p = probe(original)
    if not p.duration or p.duration > COPY_HOURS * 3600:
        raise Error("its length can't be read, or is over 12 hours: no copy is made")
    vf = ",".join([*(["yadif"] if p.interlaced else []), "scale='trunc(iw*sar/2)*2':ih", "setsar=1",
                   "scale='if(gte(iw,ih),-2,trunc(min(720,iw)/2)*2)':'if(gte(iw,ih),trunc(min(720,ih)/2)*2,-2)'"])
    try:
        # as long as the video says it is, frames as they come (an MP4 made at a fixed rate would fill gaps of hours
        # with copies of the last frame), and given at most 4 times its length (a small board takes about 1x)
        tools.run([tool("ffmpeg"), "-v", "error", "-nostdin", "-y", "-i", str(original), "-t", f"{p.duration:.3f}",
                   "-map", f"0:{p.stream}", "-map", "0:a:0?", "-vf", vf, "-fps_mode", "passthrough", "-c:v", "libx264",
                   "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                   "-ac", "2", *meta, "-movflags", "+faststart", "-threads", str(threads), "-f", "mp4", str(part)],
                  timeout=min(COPY_HOURS * 3600, max(1800, 4 * p.duration)), nice=True)
        os.utime(part, (st.st_atime, st.st_mtime))
        if hasattr(os, "chown") and hasattr(os, "geteuid") and os.geteuid() == 0:
            os.chown(part, st.st_uid, st.st_gid)  # the share's own user can still manage it
        part.chmod(st.st_mode & 0o666)
        os.replace(part, target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return target
