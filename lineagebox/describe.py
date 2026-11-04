"""Stage: describe each photo with the vision LLM (caption, setting, event, tags...), once per near-duplicate group.

A video is described from up to VIDEO_FRAMES of its frames, sent together in time order: measured on a phone clip,
ministral-3 takes four 1024 px frames in one request (3,200 prompt tokens, inside the server's 8k context; ~10 s),
and the four frames found what one frame missed (the bee the child was watching).
"""
import io
import json
import sqlite3
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Callable
from datetime import datetime

import httpx
from PIL import Image
from tqdm import tqdm

from lineagebox import retry, search
from lineagebox.catalog import tag_name, under
from lineagebox.config import Config
from lineagebox.folders import Context, FolderIndex
from lineagebox.llm import Ollama, OllamaError

PROMPT_VERSION = 1
STAGE_LLM = 4
COMMIT_EVERY = 20
VIDEO_FRAMES = 4
GIVE_UP_AFTER = 10  # failures in a row

AGE_GROUPS = ["baby", "toddler", "child", "teen", "adult", "senior"]
SCHEMA = {
    "type": "object",
    "required": ["caption", "setting", "indoor_outdoor", "event", "activities", "people_count", "age_groups",
                 "animals", "objects", "text_visible", "photo_type", "time_of_day", "season", "quality_issues", "tags"],
    "properties": {
        "caption": {"type": "string"},
        "setting": {"type": "string"},
        "indoor_outdoor": {"type": "string", "enum": ["indoor", "outdoor", "unknown"]},
        "event": {"type": ["string", "null"]},
        "activities": {"type": "array", "items": {"type": "string"}},
        "people_count": {"type": "integer"},
        "age_groups": {"type": "array", "items": {"type": "string", "enum": AGE_GROUPS}},
        "animals": {"type": "array", "items": {"type": "string"}},
        "objects": {"type": "array", "items": {"type": "string"}},
        "text_visible": {"type": ["string", "null"]},
        "photo_type": {"type": "string", "enum": ["candid", "posed group", "portrait", "selfie", "landscape",
                                                  "object/still life", "document/screenshot", "other"]},
        "time_of_day": {"type": "string", "enum": ["day", "night", "dusk/dawn", "unknown"]},
        "season": {"type": "string", "enum": ["spring", "summer", "fall", "winter", "unknown"]},
        "quality_issues": {"type": "array", "items": {"type": "string",
                           "enum": ["blurry", "too dark", "overexposed", "cropped subject"]}},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
}

SYSTEM = """You catalog a family photo library. Look at the photo and fill in the JSON fields.
- caption: 1-2 factual sentences describing what is happening.
- setting: short noun phrase for the place type (e.g. "petting zoo", "beach", "living room", "backyard").
- event: the occasion if evident (birthday party, Christmas, Halloween, wedding, ...), else null.
- age_groups: the age groups of the people visible.
- tags: 5-15 lowercase search keywords.
Only describe what is visible; if unsure what an animal or object is, use a general word ("animal", "toy").
Never use people's names: describe people ("a young boy", "an older woman"). Names come from elsewhere.
Library context may be given. Use it to name the place or event when it is consistent with the image, but never
invent details the image contradicts."""

SYSTEM_VIDEO = """You catalog a family video library. The images are frames from one video clip, in the order they
appear in it. Fill in the JSON fields for the clip as a whole.
- caption: 1-2 factual sentences describing what happens in the clip.
- setting: short noun phrase for the place type (e.g. "petting zoo", "beach", "living room", "backyard").
- event: the occasion if evident (birthday party, Christmas, Halloween, wedding, ...), else null.
- people_count: the most people visible at once.
- age_groups: the age groups of the people visible.
- photo_type: how the clip was filmed ("candid" for most home videos).
- tags: 5-15 lowercase search keywords.
Only describe what is visible; if unsure what an animal or object is, use a general word ("animal", "toy").
Never use people's names: describe people ("a young boy", "an older woman"). Names come from elsewhere.
Library context may be given. Use it to name the place or event when it is consistent with the frames, but never
invent details the frames contradict."""


def context_text(ctx: Context, taken_at: str | None, taken_at_source: str | None, date_text: str | None = None) -> str:
    lines = []
    if ctx.folders:
        lines.append("Folder: " + "; ".join(f.replace("/", " / ") for f in ctx.folders[:3]))
    if ctx.places:
        lines.append("Place (from folder names): " + "; ".join(ctx.places[:3]))
    if ctx.events:
        lines.append("Event (from folder names): " + "; ".join(ctx.events[:3]))
    if taken_at_source == "user" and date_text:  # set by hand, as exactly as it is known
        lines.append(f"Date taken: {date_text}")
    elif taken_at and taken_at_source in ("exif", "camera", "filename", "folder"):
        dt = datetime.fromisoformat(taken_at)
        lines.append(f"Date taken: {dt:%B} {dt.day}, {dt.year}" + (f" {dt:%H:%M}" if dt.hour or dt.minute else ""))
    return "\n".join(f"- {line}" for line in lines)


@dataclass
class DescribeStats:
    pending: int = 0
    done: int = 0
    failed: int = 0
    interrupted: bool = False
    gave_up: bool = False  # Ollama stopped answering (PC asleep or off); the rest waits for the next run
    stopped: bool = False  # stop() asked to (the schedule's window closed, ...); the rest waits for the next run
    seconds: float = 0.0
    last_error: str | None = None


_PENDING = """FROM image i
    WHERE i.error IS NULL AND (i.dup_group_id IS NULL OR i.dup_group_id = i.id)
      AND EXISTS (SELECT 1 FROM file f WHERE f.image_id = i.id AND NOT f.missing {folder})
      AND NOT EXISTS (SELECT 1 FROM analysis a WHERE a.image_id = i.id AND a.model = :model AND a.prompt_version = :v)
      AND {waiting}
      AND (i.dup_group_id IS NULL OR NOT EXISTS (  -- the group shares one description
           SELECT 1 FROM image m JOIN analysis a ON a.image_id = m.id
           WHERE m.dup_group_id = i.dup_group_id AND a.model = :model AND a.prompt_version = :v))"""


def pending(con: sqlite3.Connection, model: str, folder: str | None = None,
            media: str | None = None) -> list[sqlite3.Row]:
    """Group representatives (and ungrouped images) without a description from this model and prompt version,
    newest first: photos added to the library are described on the next pass, ahead of any backlog. Only those
    under folder, and only photos or only videos (media), if given."""
    params = {"model": model, "v": PROMPT_VERSION, "media": media}
    if folder:
        folder = folder.replace("\\", "/").strip("/")  # paths are stored with '/', but people type Windows paths
        params["folder"] = under(folder)
    sql = _PENDING.format(folder="AND f.path LIKE :folder ESCAPE '!'" if folder else "",
                          waiting=retry.waiting("describe"))
    return con.execute(f"""SELECT i.id, i.sha256, i.media, i.taken_at, i.taken_at_source, i.date_text {sql}
                           {"AND i.media = :media" if media else ""} ORDER BY i.id DESC""", params).fetchall()


def count_pending(con: sqlite3.Connection, model: str) -> int:
    return con.execute(f"SELECT count(*) {_PENDING.format(folder='', waiting=retry.waiting('describe'))}",
                       {"model": model, "v": PROMPT_VERSION}).fetchone()[0]


def describe(con: sqlite3.Connection, cfg: Config, folder: str | None = None, limit: int | None = None,
             show_progress: bool = True, stop: Callable[[], bool] | None = None,
             media: str | None = None) -> DescribeStats:
    """Describe what is pending. stop() is asked between photos; once it says so, the photos already sent to Ollama
    are finished and saved, and no more are started."""
    client = Ollama(cfg)
    rows = pending(con, client.model, folder, media)[:limit]
    stats = DescribeStats(pending=len(rows))
    if not rows:
        return stats
    folders = FolderIndex(con)
    todo = ((r["id"], _pictures(con, cfg, r),  # made as needed: a pass may stop long before the end
             context_text(folders.context(r["id"]), r["taken_at"], r["taken_at_source"], r["date_text"]),
             r["media"] == "video")
            for r in rows)
    t0 = time.monotonic()
    answers = []  # held until a batch is ready: the write lock is only taken for the moment it takes to save one
    failed_in_a_row = 0
    with ThreadPoolExecutor(cfg.llm_workers) as pool, \
            tqdm(total=len(rows), unit="img", disable=not show_progress) as bar:
        running = set()
        try:
            while True:
                if not stats.stopped and stop is not None and stop():
                    stats.stopped = True
                while not stats.stopped and len(running) < cfg.llm_workers and (job := next(todo, None)) is not None:
                    running.add(pool.submit(_describe_one, client, cfg.llm_image_size, *job))
                if not running:
                    break
                done, running = wait(running, return_when=FIRST_COMPLETED)
                for fut in done:
                    image_id, answer, error, ollama_failed = fut.result()
                    if error:
                        stats.failed += 1
                        bar.write(f"image {image_id}: {error}")
                        # Only Ollama failing stops the stage, and is shown as its problem. One photo that can't be
                        # done (its preview gone, two answers that weren't JSON) is tried again later (retry.py), and
                        # the photos after it go on.
                        if ollama_failed:
                            failed_in_a_row += 1
                            stats.last_error = error
                        else:
                            retry.later(con, "describe", image_id, error)
                    else:
                        answers.append((image_id, answer))
                        stats.done += 1
                        failed_in_a_row = 0
                    bar.update()
                if failed_in_a_row >= GIVE_UP_AFTER:
                    stats.gave_up = True
                    bar.write(f"stopping: {failed_in_a_row} photos in a row failed; is Ollama at {cfg.ollama_url} up?")
                    for fut in running:
                        fut.cancel()
                    break
                if len(answers) >= COMMIT_EVERY:
                    if _save(con, client.model, answers, folders):
                        answers = []
                    else:
                        bar.write(f"database busy; keeping {len(answers)} descriptions to save with the next batch")
        except KeyboardInterrupt:
            stats.interrupted = True
            for fut in running:
                fut.cancel()
        finally:
            if answers and not _save(con, client.model, answers, folders):
                stats.done -= len(answers)  # not saved: described again on the next run
    stats.seconds = time.monotonic() - t0
    return stats


def _pictures(con: sqlite3.Connection, cfg: Config, row) -> list:
    """What the model looks at: the preview, or up to VIDEO_FRAMES of a video's frames spread over its length."""
    if row["media"] != "video":
        return [cfg.preview_path(row["sha256"])]
    idxs = [r[0] for r in con.execute("SELECT idx FROM video_frame WHERE image_id = ? ORDER BY at", (row["id"],))]
    if len(idxs) > VIDEO_FRAMES:
        idxs = [idxs[round(k * (len(idxs) - 1) / (VIDEO_FRAMES - 1))] for k in range(VIDEO_FRAMES)]
    return [cfg.frame_path(row["sha256"], i) for i in idxs] or [cfg.preview_path(row["sha256"])]


def _describe_one(client: Ollama, size: int, image_id: int, pictures: list, context: str, is_video: bool):
    """Runs in a worker thread: no database access."""
    try:
        jpegs = []
        for path in pictures:
            im = Image.open(path)
            im.thumbnail((size, size), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "JPEG", quality=88)
            jpegs.append(buf.getvalue())
        user = ("Library context (may be imprecise):\n" + context + "\n\n" if context else "") + \
            ("Catalog this video clip." if is_video else "Catalog this photo.")
        system = SYSTEM_VIDEO if is_video else SYSTEM
        try:
            answer, _ = client.chat_json(system, user, SCHEMA, images=jpegs)
        except ValueError:  # it repeated a phrase until cut off; a less cautious answer usually breaks the loop
            answer, _ = client.chat_json(system, user, SCHEMA, images=jpegs, temperature=0.7)
        return image_id, answer, None, False
    except Exception as e:  # network hiccup, model unloaded, bad JSON: skipped now, retried on the next run
        return image_id, None, f"{type(e).__name__}: {e}", isinstance(e, (httpx.HTTPError, OllamaError))


def _clean_list(values, limit: int = 20) -> list[str]:
    out = []
    for v in values or []:
        v = tag_name(v)
        if v and v not in out:
            out.append(v)
    return out[:limit]


def _save(con: sqlite3.Connection, model: str, answers: list[tuple[int, dict]], folders: FolderIndex) -> bool:
    """Write a batch of answers in one short transaction. False, with nothing written, if the web app kept the
    database locked past the busy timeout: the caller keeps the batch rather than stopping an overnight run."""
    try:
        for image_id, answer in answers:
            _record(con, image_id, model, answer, folders)
        retry.done(con, "describe", [image_id for image_id, _ in answers])
        con.commit()
        return True
    except sqlite3.OperationalError as e:
        con.rollback()
        if "locked" not in str(e):
            raise
        return False


def _record(con: sqlite3.Connection, image_id: int, model: str, a: dict, folders: FolderIndex) -> None:
    con.execute("""INSERT OR REPLACE INTO analysis(image_id, model, prompt_version, created_at, caption, setting,
                       indoor_outdoor, event, photo_type, time_of_day, season, people_count, text_visible,
                       quality_issues, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (image_id, model, PROMPT_VERSION, datetime.now().isoformat(timespec="seconds"),
                 a.get("caption"), a.get("setting"), a.get("indoor_outdoor"), a.get("event"), a.get("photo_type"),
                 a.get("time_of_day"), a.get("season"), a.get("people_count"), a.get("text_visible"),
                 json.dumps(a.get("quality_issues") or []), json.dumps(a)))
    con.execute("DELETE FROM image_tag WHERE image_id = ? AND source = 'ai'", (image_id,))
    for name in _clean_list(list(a.get("tags") or []) + list(a.get("animals") or [])):
        con.execute("INSERT OR IGNORE INTO tag(name) VALUES (?)", (name,))
        con.execute("""INSERT OR IGNORE INTO image_tag(image_id, tag_id, source)
                       SELECT ?, id, 'ai' FROM tag WHERE name = ?""", (image_id, name))
    con.execute("UPDATE image SET stage = stage | ? WHERE id = ? OR dup_group_id = ?", (STAGE_LLM, image_id, image_id))
    search.index_group(con, image_id, folders)
