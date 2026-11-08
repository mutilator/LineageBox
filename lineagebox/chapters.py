"""Stage: chapters for long videos (a digitised tape, a long recording), and enough frames to cover them.

A video over video.LONG_VIDEO gets chapters where a recording on the tape starts or the picture changes completely
(video.keyframes, video.chapters), each with a frame taken just after it starts, for the chapter list on its page,
which jumps the player there. Videos read before frames were taken by length (video.frame_count) get the frames
they lack too. The new frames are video_frame rows like the others: faces are found on them for a video read now
(this stage comes before the faces stage), and "Look for faces again" finds them on one read before (the faces
stage doesn't look at a video twice).

Each video is looked at once per VERSION of these rules (video_chapters). The keyframes are decoded on the router
itself, a few minutes for a 2-hour tape, so the stage stops after its share of a pass (worker.CHAPTER_MINUTES).
"""
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from lineagebox import media, retry, video
from lineagebox.catalog import VISIBLE
from lineagebox.config import Config

VERSION = 1
NEAR = 0.4  # a frame wanted within this share of the spacing of one the video has is not taken again


@dataclass
class ChapterStats:
    pending: int = 0
    done: int = 0
    chapters: int = 0
    frames: int = 0
    failed: int = 0
    stopped: bool = False
    seconds: float = 0.0


_PENDING = f"""FROM image i WHERE i.media = 'video' AND i.duration >= {video.LONG_VIDEO} AND {VISIBLE}
               AND NOT EXISTS (SELECT 1 FROM video_chapters c WHERE c.image_id = i.id AND c.version >= {VERSION})
               AND {retry.waiting("chapters")}"""


def count_pending(con: sqlite3.Connection) -> int:
    return con.execute(f"SELECT count(*) {_PENDING}").fetchone()[0]


def find(con: sqlite3.Connection, cfg: Config, stop: Callable[[], bool] | None = None) -> ChapterStats:
    """Chapters and frames for the long videos that have none yet, newest first. stop() is asked between videos."""
    rows = con.execute(f"""SELECT i.id, i.sha256, i.duration, (SELECT f.path FROM file f WHERE f.image_id = i.id
                                  AND NOT f.missing ORDER BY length(f.path), f.path LIMIT 1) AS path
                           {_PENDING} ORDER BY i.id DESC""").fetchall()
    stats = ChapterStats(pending=len(rows))
    t0 = time.monotonic()
    for r in rows:
        if stop is not None and stop():
            stats.stopped = True
            break
        path = cfg.source_path(r["path"])
        try:
            p = video.probe(path)
            starts = video.chapters(video.keyframes(path, p), p.duration or r["duration"])
            have = {idx: at for idx, at in con.execute("SELECT idx, at FROM video_frame WHERE image_id = ?",
                                                        (r["id"],))}
            made, chapters = _frames(cfg, path, p, r["sha256"], have, starts)
        except (OSError, video.Unavailable, video.TimedOut) as e:  # not the video: tried again later
            retry.later(con, "chapters", r["id"], str(e))
            stats.failed += 1
            continue
        except video.Error as e:  # ffmpeg can't read it as it is: noted, and not tried again under these rules
            _record(con, r["id"], [], [], str(e))
            stats.failed += 1
            continue
        _record(con, r["id"], chapters, made, None)
        stats.done += 1
        stats.chapters += len(chapters)
        stats.frames += len(made)
    stats.seconds = time.monotonic() - t0
    return stats


def _frames(cfg: Config, path, p: video.Probe, sha: str, have: dict[int, float],
            starts: list[float]) -> tuple[list[tuple[int, float]], list[list[float]]]:
    """Take a frame at each chapter's start, and the frames a video of this length gets that it lacks. Returns the
    frames made, (idx, seconds), and the chapters, [seconds, frame idx]. A chapter whose frame can't be read has
    none: its time is still listed."""
    spacing = (p.duration or 0) / max(video.frame_count(p.duration), 1)
    wanted = [(at, None) for at in video.sample_times(p.duration)
              if all(abs(at - t) > spacing * NEAR for t in have.values())]
    wanted += [(min(at + video.CHAPTER_FRAME_AFTER, (p.duration or at) - 0.5), at) for at in starts]
    made: list[tuple[int, float]] = []
    chapters: list[list[float]] = []
    idx = max(have, default=-1) + 1
    slow = False
    for at, chapter in sorted(wanted):
        try:
            if slow:  # one took too long: a tape decoded from its start, where every later frame takes longer still
                raise video.TimedOut("an earlier frame took too long")
            im = video.frame(path, at, cfg.preview_size, p)
        except video.Error as e:  # past the real end, or a damaged stretch
            slow = slow or isinstance(e, video.TimedOut)
            if chapter is not None:
                chapters.append([chapter, None])
            continue
        media._atomic_save(im, cfg.frame_path(sha, idx), "JPEG", quality=85)
        made.append((idx, at))
        if chapter is not None:
            chapters.append([chapter, idx])
        idx += 1
    return made, sorted(chapters)


def _record(con: sqlite3.Connection, image_id: int, chapters: list, made: list, error: str | None) -> None:
    con.executemany("INSERT OR IGNORE INTO video_frame(image_id, idx, at) VALUES (?, ?, ?)",
                    ((image_id, idx, at) for idx, at in made))
    con.execute("""INSERT OR REPLACE INTO video_chapters(image_id, version, made_at, chapters, error)
                   VALUES (?, ?, ?, ?, ?)""",
                (image_id, VERSION, datetime.now().isoformat(timespec="seconds"), json.dumps(chapters), error))
    retry.done(con, "chapters", [image_id])
    con.commit()  # one video at a time: each takes minutes to look at, and this is a short write


def for_page(con: sqlite3.Connection, image_id: int, transcript: dict | None = None) -> list[dict]:
    """A video's chapters for its page: where each starts (the first at 0), its picture (a frame idx, or None), and
    the first words said in it, from the transcript (corrected lines included), if there is one. [] for a video with
    none."""
    row = con.execute("SELECT chapters FROM video_chapters WHERE image_id = ?", (image_id,)).fetchone()
    found = json.loads(row[0]) if row else []
    if not found:
        return []
    first = con.execute("SELECT idx FROM video_frame WHERE image_id = ? ORDER BY at LIMIT 1", (image_id,)).fetchone()
    starts = [[0.0, first[0] if first else None], *found]
    lines = (transcript or {}).get("lines") or []
    out = []
    for n, (at, frame) in enumerate(starts):
        until = starts[n + 1][0] if n + 1 < len(starts) else float("inf")
        said = next((ln["text"] for ln in lines if at <= ln["start"] < until and ln["text"]), "")
        out.append({"at": at, "frame": frame, "said": said})
    return out
