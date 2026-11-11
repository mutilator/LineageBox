"""A tour or an album saved as one video file, to send to someone (Save as a video, on its page).

Asking for one (ask) keeps the slides as the one who asked sees them: the title card, each photo with its caption, its
date and who is in it, and the videos in it. That is all the worker needs, so a member's video holds only what they
may see, and nothing is worked out again for them later. The worker makes it as one of its stages (make_waiting), never
while a page waits: the small board LineageBox may run on takes minutes over it. Asking wakes the worker, as Check now
does.

What it makes is kept modest for such a board: 1280 × 720, FPS frames a second, each photo held for the tour's seconds
with a crossfade of FADE into the next, its words drawn onto the picture by Pillow (not by ffmpeg, which would need
fonts set up for it: the first of FONTS there is, DejaVu Sans in the Docker image, else Pillow's own), and at most
CLIP_MOST seconds of each video, with its sound. Frames go to ffmpeg
through a pipe as they are drawn, so nothing but the finished file is kept on disk (and, with videos in it, their
sound, for a moment). The file is in the data folder's videos/, kept KEEP_DAYS days, or until the same person asks for
the same one again; it can be made again from its tour or album whenever wanted, so backups leave it out.
"""
import functools
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import traceback
import unicodedata
import wave
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from lineagebox import copies, errors, settings, tools, video
from lineagebox.config import Config

WIDTH, HEIGHT = 1280, 720
FPS = 15                 # frames a second: enough for photos and crossfades, light for a small board to encode
FADE = 1.0               # seconds each crossfade takes
TITLE_SECONDS = 4        # the title card's
CLIP_MOST = 60           # seconds of each video put in it
MOST_SLIDES = 300        # photos and videos in one
MOST_MINUTES = 40        # the longest one may be
RATE = 24000             # sound: 16-bit mono samples a second, 1,600 to a frame
KEEP_DAYS = 30
FOLDER = "videos"        # in the data folder
WAKE = "slideshow_asked"  # settings key: when one was last asked for (worker.py starts a pass for it)
STATES = {"waiting": "waiting for the worker", "making": "being made", "done": "ready", "failed": "failed"}
# fonts with accented letters (José, Müller, Łódź), the first found: Debian's DejaVu Sans (in the Docker image), other
# systems' own. Pillow's built-in one has plain letters only, so without any of these accents are left off.
FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/TTF/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
         "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf", "C:/Windows/Fonts/segoeui.ttf",
         "C:/Windows/Fonts/arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf")
PLAINER = {"—": "-", "–": "-", "ß": "ss", "Ł": "L", "ł": "l", "Ø": "O", "ø": "o", "Æ": "AE", "æ": "ae", "Œ": "OE",
           "œ": "oe", "Đ": "D", "đ": "d", "•": "·", "…": "...", "“": '"', "”": '"', "‘": "'", "’": "'"}


class Problem(errors.Problem):
    """A video that can't be asked for: too long, or nothing in it."""


def folder(cfg: Config) -> Path:
    return cfg.data_dir / FOLDER


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def seconds_long(slides: list[dict], seconds: int) -> float:
    """How long the video of these slides is: each photo for `seconds`, each video for up to CLIP_MOST, the title
    card, and the crossfades between them."""
    total = 0.0
    for s in slides:
        if s.get("title") is not None:
            total += TITLE_SECONDS
        elif s.get("video"):
            total += min(float(s.get("duration") or seconds), CLIP_MOST)
        else:
            total += seconds
    return total + FADE * max(len(slides) - 1, 0)


def ask(con: sqlite3.Connection, account_id: int | None, kind: str, item_id: int, name: str, slides: list[dict],
        seconds: int) -> int:
    """Ask the worker for a video of these slides (a tour's or an album's, kind "tour" or "album", as the one who
    asks sees them). One asked for before by the same person, of the same tour or album, goes with its file. Raises
    Problem when it would be too long."""
    assert kind in ("tour", "album")
    shown = [s for s in slides if s.get("title") is None]
    if not shown:
        raise Problem("There are no photos in it to make a video of.")
    if len(shown) > MOST_SLIDES:
        raise Problem(f"A video can hold up to {MOST_SLIDES} photos and videos; this has {len(shown):,}. Make it "
                      f"shorter first (fewer photos a year, or a set number).")
    minutes = seconds_long(slides, seconds) / 60
    if minutes > MOST_MINUTES:
        raise Problem(f"That would be a video of {minutes:.0f} minutes; one can be up to {MOST_MINUTES}. Make it "
                      f"shorter first: fewer photos, or fewer seconds for each.")
    for old in con.execute(f"""SELECT id, file FROM main.slideshow_video WHERE {kind}_id = ? AND account_id IS ?""",
                           (item_id, account_id)).fetchall():
        _drop(con, None, old)
    video_id = con.execute(f"""INSERT INTO main.slideshow_video(account_id, {kind}_id, name, slides, seconds, total,
                                                                asked_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                           (account_id, item_id, name[:120], json.dumps(slides), seconds, len(slides),
                            _now())).lastrowid
    con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (WAKE, json.dumps(_now())))
    con.commit()
    return video_id


def latest(con: sqlite3.Connection, account_id: int | None, kind: str, item_id: int) -> dict | None:
    """The video this person last asked for of a tour or an album, with how far it is."""
    assert kind in ("tour", "album")
    row = con.execute(f"""SELECT id, name, state, done, total, message, file, bytes, asked_at, finished_at
                          FROM main.slideshow_video WHERE {kind}_id = ? AND account_id IS ?
                          ORDER BY id DESC LIMIT 1""", (item_id, account_id)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["said"] = STATES[row["state"]]
    out["until"] = ((datetime.fromisoformat(row["finished_at"]) + timedelta(days=KEEP_DAYS)).date().isoformat()
                    if row["finished_at"] else None)
    return out


def path(cfg: Config, row) -> Path | None:
    """The finished file of a video, if it is there."""
    if row is None or row["state"] != "done" or not row["file"]:
        return None
    p = folder(cfg) / row["file"]
    return p if p.is_file() else None


def _drop(con: sqlite3.Connection, cfg: Config | None, row) -> None:
    """One video goes, with its file. cfg None: the file is left for tidy() (the web app has no need to know where
    the data folder's videos are kept, and the worker clears them)."""
    if cfg is not None and row["file"]:
        (folder(cfg) / row["file"]).unlink(missing_ok=True)
    con.execute("DELETE FROM main.slideshow_video WHERE id = ?", (row["id"],))


def tidy(con: sqlite3.Connection, cfg: Config) -> int:
    """Videos older than KEEP_DAYS go, and files no video of the catalog is (one asked for again, half-made ones),
    with the folders of those being made when the worker was stopped (only the worker makes them, and not while this
    runs). Returns how many files and folders went."""
    old = (datetime.now() - timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds")
    for row in con.execute("""SELECT id, file FROM slideshow_video WHERE state IN ('done', 'failed')
                              AND finished_at < ?""", (old,)).fetchall():
        _drop(con, cfg, row)
    con.commit()
    kept = {r[0] for r in con.execute("SELECT file FROM slideshow_video WHERE file IS NOT NULL")}
    gone = 0
    if folder(cfg).is_dir():
        for f in folder(cfg).iterdir():
            if f.is_dir() and f.name.startswith(".making-"):  # _make's, cut off: a restart, the box losing power
                shutil.rmtree(f, ignore_errors=True)
                gone += 1
            elif f.is_file() and f.name not in kept:
                f.unlink(missing_ok=True)
                gone += 1
    return gone


def make_waiting(con: sqlite3.Connection, cfg: Config, stop=None, threads: int = 2) -> int:
    """The worker's stage: make the videos asked for, the oldest first. Returns how many were made. Without ffmpeg
    they wait (the Status page says ffmpeg is missing). stop(): asked between videos, so that a pass isn't held up
    for long; one being made is finished first (made again from the start, it would take as long again). One cut off
    by the worker stopping is made again from the start on its next pass."""
    con.execute("UPDATE slideshow_video SET state = 'waiting', done = 0 WHERE state = 'making'")  # cut off: again
    con.commit()
    tidy(con, cfg)
    if not video.available():
        return 0
    made = 0
    while row := con.execute("""SELECT * FROM slideshow_video WHERE state = 'waiting'
                                ORDER BY asked_at, id LIMIT 1""").fetchone():
        if stop is not None and stop():
            break
        con.execute("UPDATE slideshow_video SET state = 'making', started_at = ?, done = 0 WHERE id = ?",
                    (_now(), row["id"]))
        con.commit()
        try:
            file, size = _make(con, cfg, row, threads)
        except Exception as e:  # ffmpeg failed, the disk is full, or this one can't be drawn: it alone fails, or it
            # would be made again on every pass, first in the queue, and none asked for after it would ever be made
            if not isinstance(e, (tools.Error, OSError)):
                traceback.print_exc()
            con.rollback()
            con.execute("""UPDATE slideshow_video SET state = 'failed', message = ?, finished_at = ?
                           WHERE id = ?""", ((str(e) or type(e).__name__)[:300], _now(), row["id"]))
            con.commit()
            continue
        con.execute("""UPDATE slideshow_video SET state = 'done', file = ?, bytes = ?, done = total, finished_at = ?
                       WHERE id = ?""", (file, size, _now(), row["id"]))
        con.commit()
        made += 1
    return made


# ---- making one ----------------------------------------------------------------------------------------------------

def _make(con: sqlite3.Connection, cfg: Config, row, threads: int) -> tuple[str, int]:
    """Draw every frame and have ffmpeg encode them as they come; then, with videos in it, add their sound. Returns
    the file's name in folder(cfg) and its size."""
    slides = json.loads(row["slides"])
    out = folder(cfg)
    out.mkdir(parents=True, exist_ok=True)
    name = f"{row['id']}.mp4"
    work = Path(tempfile.mkdtemp(prefix=".making-", dir=out))
    silent, sound = work / "picture.mp4", work / "sound.wav"
    has_clips = any(s.get("video") for s in slides)
    background = _background(cfg, next((s for s in slides if s.get("sha")), None))
    try:
        encoder = _Pipe([video.tool("ffmpeg"), "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                         "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset",
                         "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-threads", str(threads), "-f", "mp4",
                         str(silent)], work / "encoder.log")
        with encoder, (wave.open(str(sound), "wb") if has_clips else _NoSound()) as track:
            track.setnchannels(1)
            track.setsampwidth(2)
            track.setframerate(RATE)
            last = None
            for k, slide in enumerate(slides):
                last = _slide(con, cfg, slide, row["seconds"], last, encoder, track, background)
                if k % 5 == 4:
                    _progress(con, row["id"], k + 1)
        if has_clips:  # the sound beside the pictures, in one file
            final = work / "with-sound.mp4"
            tools.run([video.tool("ffmpeg"), "-v", "error", "-nostdin", "-y", "-i", str(silent), "-i", str(sound),
                       "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "96k", "-shortest",
                       "-movflags", "+faststart", "-f", "mp4", str(final)], timeout=1800, nice=True)
        else:
            final = work / "ready.mp4"
            tools.run([video.tool("ffmpeg"), "-v", "error", "-nostdin", "-y", "-i", str(silent), "-c", "copy",
                       "-movflags", "+faststart", "-f", "mp4", str(final)], timeout=1800, nice=True)
        os.replace(final, out / name)
        return name, (out / name).stat().st_size
    finally:
        for f in work.iterdir():
            f.unlink(missing_ok=True)
        work.rmdir()


def _progress(con: sqlite3.Connection, video_id: int, done: int) -> None:
    try:
        con.execute("UPDATE slideshow_video SET done = ? WHERE id = ?", (done, video_id))
        con.commit()
    except sqlite3.OperationalError:  # the web app is writing: said next time
        con.rollback()


def _slide(con, cfg, slide: dict, seconds: int, last: np.ndarray | None, encoder, track, background) -> np.ndarray:
    """One slide's frames, after the crossfade from the last frame of the one before. Returns its own last frame."""
    clip = _clip_path(con, cfg, slide) if slide.get("video") else None
    frames = _Clip(clip, slide) if clip else None
    first = frames.first() if frames else None
    if first is None:  # a photo, the title card, or a video ffmpeg couldn't read: shown as a still
        frames = None
        first = _still(cfg, slide, background)
        hold = round((TITLE_SECONDS if slide.get("title") is not None else seconds) * FPS)
    if last is not None:
        a, b = last.astype(np.float32), first.astype(np.float32)
        steps = round(FADE * FPS)
        try:
            for n in range(1, steps + 1):
                encoder.write((a + (b - a) * (n / (steps + 1))).astype(np.uint8))
            track.writeframes(bytes(2 * RATE * steps // FPS))
        except BaseException:  # the encoder stopped, or the disk is full: the clip's own ffmpeg stops too
            if frames is not None:
                frames.__exit__()
            raise
    if frames is None:
        data = first.tobytes()
        for _ in range(hold):
            encoder.write(data)
        track.writeframes(bytes(2 * RATE * hold // FPS))
        return first
    n, now = 0, first
    with frames:
        while now is not None:
            encoder.write(now)
            last_frame, n = now, n + 1
            now = frames.next()
    track.writeframes(frames.sound(n))
    return last_frame


def _clip_path(con, cfg, slide: dict) -> Path | None:
    """The file of a video to put in: its 720p copy, else its original (as the player plays it)."""
    if copy := copies.find_copy(con, cfg, slide["id"]):
        return copy
    for rel in copies.present_files(con, slide["id"]):
        if (p := cfg.source_path(rel)).is_file():
            return p
    return None


class _Clip:
    """A video's frames at the slideshow's size and rate, with its words drawn over them, read from ffmpeg as they are
    needed; then its sound, as long as those frames."""

    def __init__(self, path: Path, slide: dict):
        self.path, self.slide, self.pipe, self.band = path, slide, None, None

    def first(self) -> np.ndarray | None:
        try:
            p = video.probe(self.path)
        except tools.Error:
            return None
        self.band = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        self.band.close()
        _band(self.slide).save(self.band.name)
        fit = (f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
               f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={FPS}")
        graph = f"[0:{p.stream}]{'yadif,' if p.interlaced else ''}{fit},format=rgba[v];[v][1:v]overlay=0:0,format=rgb24"
        args = [video.tool("ffmpeg"), "-v", "error", "-nostdin", "-t", str(CLIP_MOST), *video.source(self.path),
                "-i", self.band.name, "-filter_complex", graph, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        if hasattr(os, "nice"):
            args = ["nice", "-n", "10", *args]
        try:
            self.pipe = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL)
        except OSError:
            self.__exit__()  # its caption's picture goes
            return None
        frame = self.next()
        if frame is None:
            self.__exit__()
        return frame

    def next(self) -> np.ndarray | None:
        size = WIDTH * HEIGHT * 3
        data = self.pipe.stdout.read(size) if self.pipe else b""
        if len(data) < size:
            return None
        return np.frombuffer(data, np.uint8).reshape(HEIGHT, WIDTH, 3)

    def sound(self, frames: int) -> bytes:
        """The video's sound for as many frames as were shown, silence where it has none."""
        want = 2 * RATE * frames // FPS
        try:
            got = tools.run([video.tool("ffmpeg"), "-v", "error", "-nostdin", "-t", str(CLIP_MOST),
                             *video.source(self.path), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le",
                             "-"], timeout=600, nice=True)
        except tools.Error:
            got = b""
        return (got + bytes(want))[:want]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self.pipe:
            self.pipe.stdout.close()
            self.pipe.kill()
            self.pipe.wait()
            self.pipe = None
        if self.band:
            Path(self.band.name).unlink(missing_ok=True)
            self.band = None


class _Pipe:
    """ffmpeg reading frames from a pipe; what it says goes to a file, so that a long run never fills a pipe nobody
    reads. Leaving it waits for it to finish; it raises tools.Error when it failed."""

    def __init__(self, args: list[str], log: Path):
        if hasattr(os, "nice"):
            args = ["nice", "-n", "10", *args]
        self.log = log
        self.said = open(log, "wb")
        try:
            self.p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self.said)
        except OSError as e:
            self.said.close()
            raise tools.Unavailable(f"ffmpeg could not be run: {e.strerror or e}") from None

    def write(self, frame: np.ndarray) -> None:
        try:
            self.p.stdin.write(frame.tobytes() if isinstance(frame, np.ndarray) else frame)
        except (BrokenPipeError, OSError):
            raise tools.Error(self._last_line() or "ffmpeg stopped while the video was being made") from None

    def _last_line(self) -> str:
        self.said.flush()
        lines = self.log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        return lines[-1] if lines else ""

    def __enter__(self):
        return self

    def __exit__(self, kind, *_):
        try:
            if kind is None:
                self.p.stdin.close()
                code = self.p.wait(timeout=3600)
            else:  # a frame couldn't be drawn or written: what was encoded so far is of no use
                self.p.kill()
                self.p.wait()
                code = 0
        except (BrokenPipeError, OSError):
            code = self.p.wait()
        except subprocess.TimeoutExpired:
            self.p.kill()
            raise tools.TimedOut("ffmpeg took over an hour to finish the video") from None
        finally:
            self.said.close()
        if code and kind is None:
            raise tools.Error(self._last_line() or f"ffmpeg stopped with exit code {code}")


class _NoSound:
    """A sound track for a video with no videos in it: none is written."""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def setnchannels(self, _): pass  # noqa: E704

    def setsampwidth(self, _): pass  # noqa: E704

    def setframerate(self, _): pass  # noqa: E704

    def writeframes(self, _): pass  # noqa: E704


# ---- drawing -------------------------------------------------------------------------------------------------------

@functools.cache
def _font(size: int):
    """The first of FONTS there is, else Pillow's own, as large as asked."""
    for name in FONTS:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


@functools.cache
def _lacks(font, ch: str) -> bool:
    """Whether the font has no letter for ch (it would draw an empty box)."""
    return not ch.isspace() and bytes(font.getmask(ch)) == bytes(font.getmask(""))


def _say(text: str, font) -> str:
    """The text with the letters the font lacks written plainer (é as e, ß as ss), else as ?."""
    out = []
    for ch in text:
        if not _lacks(font, ch):
            out.append(ch)
            continue
        plain = PLAINER.get(ch) or "".join(c for c in unicodedata.normalize("NFKD", ch)
                                          if not unicodedata.combining(c))
        out.append(plain if plain and not any(_lacks(font, c) for c in plain) else "?")
    return "".join(out)


def _picture(cfg: Config, sha: str | None) -> Image.Image | None:
    """A photo's 2048-pixel preview, upright, or None when it is gone (it is made again; the slide shows its words)."""
    if not sha:
        return None
    try:
        with Image.open(cfg.preview_path(sha)) as im:
            return im.convert("RGB")
    except (OSError, ValueError):
        return None


def _background(cfg: Config, slide: dict | None) -> Image.Image:
    """The title card's: the first photo, blurred and darkened, else plain dark."""
    picture = _picture(cfg, slide["sha"]) if slide else None
    if picture is None:
        return Image.new("RGB", (WIDTH, HEIGHT), (24, 24, 28))
    return Image.eval(_cover(picture).resize((WIDTH // 8, HEIGHT // 8)).filter(ImageFilter.GaussianBlur(3))
                      .resize((WIDTH, HEIGHT), Image.Resampling.BILINEAR), lambda v: v * 2 // 5)


def _cover(picture: Image.Image) -> Image.Image:
    """The picture filling the frame, cut to its shape."""
    scale = max(WIDTH / picture.width, HEIGHT / picture.height)
    big = picture.resize((max(WIDTH, round(picture.width * scale)), max(HEIGHT, round(picture.height * scale))),
                         Image.Resampling.BILINEAR)
    left, top = (big.width - WIDTH) // 2, (big.height - HEIGHT) // 2
    return big.crop((left, top, left + WIDTH, top + HEIGHT))


def _still(cfg: Config, slide: dict, background: Image.Image) -> np.ndarray:
    """One frame: the title card, or a photo (or a video's still) whole on a blurred copy of itself, with its words."""
    if slide.get("title") is not None:
        frame = background.copy()
        draw = ImageDraw.Draw(frame)
        y = HEIGHT // 2 - 90
        for line in _lines(slide["title"], _font(64), WIDTH - 160, 2):
            _centred(draw, line, _font(64), y, (255, 255, 255))
            y += 78
        for text, size in ((slide.get("span"), 34), (slide.get("about"), 28), (slide.get("count"), 26)):
            for line in _lines(text or "", _font(size), WIDTH - 200, 2):
                y += 8
                _centred(draw, line, _font(size), y, (225, 225, 225))
                y += size + 6
        return np.asarray(frame)
    picture = _picture(cfg, slide.get("sha"))
    frame = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    if picture is not None:
        frame = Image.eval(_cover(picture).resize((WIDTH // 8, HEIGHT // 8)).filter(ImageFilter.GaussianBlur(2))
                           .resize((WIDTH, HEIGHT), Image.Resampling.BILINEAR), lambda v: v // 3)
        fit = picture.copy()
        fit.thumbnail((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
        frame.paste(fit, ((WIDTH - fit.width) // 2, (HEIGHT - fit.height) // 2))
    frame = frame.convert("RGBA")
    frame.alpha_composite(_band(slide))
    return np.asarray(frame.convert("RGB"))


def _band(slide: dict) -> Image.Image:
    """A slide's words at the foot of the frame, on a dark band (transparent elsewhere): its caption, then when it
    was taken and who is in it."""
    out = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    caption = _lines(slide.get("caption") or "", _font(34), WIDTH - 96, 2)
    under = " · ".join(t for t in (slide.get("when"), slide.get("people")) if t)
    small = _lines(under, _font(26), WIDTH - 96, 2)
    if not caption and not small:
        return out
    height = 28 + 44 * len(caption) + 34 * len(small)
    draw = ImageDraw.Draw(out)
    draw.rectangle((0, HEIGHT - height, WIDTH, HEIGHT), fill=(0, 0, 0, 150))
    y = HEIGHT - height + 14
    for line in caption:
        draw.text((48, y), line, font=_font(34), fill=(255, 255, 255, 255))
        y += 44
    for line in small:
        draw.text((48, y), line, font=_font(26), fill=(220, 220, 220, 255))
        y += 34
    return out


def _centred(draw: ImageDraw.ImageDraw, text: str, font, y: int, fill) -> None:
    draw.text(((WIDTH - font.getlength(text)) / 2, y), text, font=font, fill=fill)


def _lines(text: str, font, width: int, most: int) -> list[str]:
    """The text broken into lines that fit, at most `most` of them (the last ends with … when it doesn't all fit)."""
    lines, line = [], ""
    for word in _say(text, font).split():
        trial = f"{line} {word}".strip()
        if font.getlength(trial) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    if len(lines) > most:
        lines = lines[:most]
        more = _say(" …", font)
        while lines[-1] and font.getlength(lines[-1] + more) > width:
            lines[-1] = lines[-1].rsplit(" ", 1)[0] if " " in lines[-1] else lines[-1][:-1]
        lines[-1] += more
    return lines
