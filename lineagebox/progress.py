"""What the Status page shows about work in progress: the worker's current stage, how far the descriptions are, what
is waiting for each stage, downloads, problems with Ollama or Whisper, and the worker's recent activity."""
import sqlite3
from datetime import datetime, timedelta

from lineagebox import chapters, copies, downloads, retry, settings, speech
from lineagebox import describe, embeddings, headcount
from lineagebox.catalog import ON_SHARE, VISIBLE
from lineagebox.config import Config
from lineagebox.describe import count_pending
from lineagebox.faces import STAGE_FACES, count_due as faces_due
from lineagebox.video import LONG_VIDEO

STAGES = {"scan": "Looking for new and changed files", "back up the catalog": "Backing up the catalog",
          "ingest": "Reading new photos",
          "dedup": "Grouping similar shots", "folders": "Reading folder names (AI)",
          "folder dates": "Dating photos from folder names", "faces": "Finding faces",
          "recognise": "Recognising named people", "group faces": "Grouping faces",
          "sharpness": "Measuring sharpness", "places": "Finding places on the map", "search index": "Updating the search",
          "transcribe": "Transcribing videos (Whisper)", "describe": "Describing photos and videos (AI)",
          "slideshow videos": "Saving tours and albums as videos"}
STAGES.update({"transcribe": "Transcribing videos, recordings and stories (Whisper)",
               "chapters": "Finding chapters in long videos", "tidy stories": "Tidying recorded stories"})
STAGES.update({"describe again": "Describing photos again, as asked (AI)",
               "meaning": "Reading descriptions for search by meaning (AI)"})
STAGES.update({"look again": "Looking for faces again, as asked",
               "look closely at every photo": "Looking closely at every photo for faces",
               "count people": "Counting the people in photos whose faces don't add up (AI)",
               "how sure": "Working out how sure each recognised face is"})
SILENT_AFTER = timedelta(minutes=3)  # an idle worker checks in every 15 s


def counts(con: sqlite3.Connection, cfg: Config) -> dict:
    """What is left for each stage: on a big library these take a second or more, so the Status page, which asks
    every 10 seconds, keeps them a while (snapshot takes them). Each row of "waiting": key, label, left, and total,
    what left is out of, for a progress bar (None for a queue of what was asked for, which has no whole)."""
    one = lambda sql, *a: con.execute(sql, a).fetchone()[0]
    left = count_pending(con, cfg.ollama_model)
    visible = one(f"SELECT count(*) FROM image i WHERE {VISIBLE}")
    photos = one(f"SELECT count(*) FROM image i WHERE i.media = 'photo' AND {VISIBLE}")
    total = one(f"SELECT count(*) FROM image i WHERE (i.dup_group_id IS NULL OR i.dup_group_id = i.id) AND {VISIBLE}")
    files = one("""SELECT count(*) FROM file f JOIN folder d ON d.id = f.folder_id WHERE NOT f.missing
                   AND NOT d.ignored""")
    embedded = one("SELECT count(DISTINCT image_id) FROM embedding WHERE model = ?", cfg.ollama_embed_model)
    meaning = embeddings.count_pending(con, cfg.ollama_embed_model)
    sound = one(f"""SELECT count(*) FROM image i WHERE i.media IN ('video', 'audio')
                    AND i.codecs NOT LIKE '%/no sound' AND {VISIBLE}""")
    checked = """SELECT count(*) FROM file f JOIN image i ON i.id = f.image_id
                 WHERE NOT f.missing AND i.error IS NULL"""  # as check.check reads them
    row = lambda key, label, n, whole=None: {"key": key, "label": label, "left": n, "total": whole}
    return {
        "total": total,
        "left": left,
        "waiting": [
            row("files", "New or changed files to read", one("""SELECT count(*) FROM file f
                JOIN folder d ON d.id = f.folder_id WHERE f.image_id IS NULL AND NOT f.missing AND NOT d.ignored"""),
                files),
            row("folders", "Folder names to read (AI)", one("""SELECT count(*) FROM folder WHERE kind IS NULL
                AND source = 'auto' AND NOT ignored AND path != ''"""),
                one("SELECT count(*) FROM folder WHERE source = 'auto' AND NOT ignored AND path != ''")),
            row("faces", "Photos and videos to check for faces",
                one(f"SELECT count(*) FROM image i WHERE NOT (i.stage & {STAGE_FACES}) AND {VISIBLE}"), visible),
            row("closely", "Photos to look at closely for faces (Settings → Faces)", faces_due(con),
                one(f"""SELECT count(*) FROM image i WHERE i.media = 'photo' AND (i.stage & {STAGE_FACES})
                        AND {VISIBLE}""")),
            row("sharpness", "Photos to measure for sharpness",
                one(f"SELECT count(*) FROM image i WHERE i.sharpness IS NULL AND i.media = 'photo' AND {VISIBLE}"),
                photos),
            row("check", "Files to read whole for damage (Cleanup → Damaged files)",
                one(f"{checked} AND f.checked_at IS NULL"), one(checked)),
            row("search", "Photos to add to the search", one(f"""SELECT count(*) FROM image i WHERE {VISIBLE}
                AND i.id NOT IN (SELECT rowid FROM search_fts)"""), visible),
            row("describe", "Photos and videos to describe (AI)", left, total),
            row("describe again", "Photos asked to be described again (AI)", describe.count_requests(con)),
            row("headcount", "Photos whose faces don't add up, to count the people in (AI)",
                len(headcount.waiting(con, cfg.ollama_model))),
            row("meaning", "Descriptions to read for search by meaning (AI)", meaning,
                embedded + meaning if cfg.ollama_embed_model else None),
            row("transcribe", "Videos, recordings and stories to transcribe (Whisper)", speech.count_pending(con),
                sound + one("SELECT count(*) FROM story WHERE removed_at IS NULL")),
            row("chapters", "Long videos to find chapters in", chapters.count_pending(con),
                one(f"""SELECT count(*) FROM image i WHERE i.media = 'video' AND i.duration >= {LONG_VIDEO}
                        AND {VISIBLE}""")),
            row("copies", "Videos to make a 720p copy of", copies.count_pending(con),
                one(f"SELECT count(*) FROM image i WHERE i.playable IN ('copy', 'needed') AND {ON_SHARE}")),
        ],
    }


# How long what is waiting takes, at the pace it has been going. The worker notes what is left of each (record) as
# each pass starts and ends; the pace is how much less is left now than at the earliest note of the last PACE_OVER,
# over the time between, so it counts the hours between passes too: what "some each pass" comes to by the clock.
HISTORY_KEY = "progress_history"  # settings: [{"at": when, "left": {key: what was left}}], oldest first
PACE_OVER = timedelta(hours=24)
PACE_FROM = timedelta(minutes=20)  # notes closer together than this say nothing yet


def record(con: sqlite3.Connection, cfg: Config, now: datetime | None = None) -> None:
    """Note what is left of each kind of work, for its pace (pace()); notes older than twice PACE_OVER go."""
    now = now or datetime.now()
    waiting = counts(con, cfg)["waiting"]
    notes = [n for n in settings.get(con, HISTORY_KEY, []) if (t := _time(n.get("at"))) and now - t <= 2 * PACE_OVER]
    notes.append({"at": now.isoformat(timespec="seconds"), "left": {w["key"]: w["left"] for w in waiting}})
    settings.put(con, HISTORY_KEY, notes[-200:])


def pace(notes: list[dict], key: str, left: int, now: datetime) -> tuple[str, float | None]:
    """("going", how many an hour), ("still", None) when no less has been left for PACE_FROM or more, or ("early",
    None) while the notes span less than that."""
    points = sorted((t, n["left"][key]) for n in notes if key in n.get("left", {})
                    and (t := _time(n.get("at"))) and timedelta(0) <= now - t <= PACE_OVER)
    if not points or now - points[0][0] < PACE_FROM:
        return "early", None
    when, then = points[0]
    if then <= left:
        return "still", None
    return "going", (then - left) / ((now - when).total_seconds() / 3600)


# Kinds of work the AI (Ollama) does, and Whisper's, both only in the describing hours (settings.may_describe): their
# rows say what holds them up (held()) rather than "not moving lately". Those that look at pictures need a model that
# can. The Status page had a panel of its own for descriptions until 2026-11-12, when these rows took its place.
AI_WORK = {"folders", "describe", "describe again", "meaning", "headcount"}
SEES = {"describe", "describe again", "headcount"}
SPEECH_WORK = {"transcribe"}


def held(key: str, cfg: Config, now: datetime, state: dict, whisper: bool | None = None) -> dict | None:
    """What keeps a kind of work (a counts() row's key) from going on just now: {"why": "off" (in Settings), "paused",
    "hours" (with "at": when the describing hours start next, if they do), "ollama" (not answering), "model" (it can't
    look at pictures) or "whisper" (not answering)}. None when nothing does, or it isn't the AI's or Whisper's work.
    state: the worker's (its "ollama" and "waiting_for"); whisper: whether Whisper answers (None: not asked)."""
    if key not in AI_WORK | SPEECH_WORK:
        return None
    if not (cfg.describe_enabled if key in AI_WORK else cfg.transcribe_enabled and cfg.whisper_url):
        return {"why": "off"}
    if cfg.describe_schedule == "paused":
        return {"why": "paused"}
    if not settings.may_describe(cfg, now):
        return {"why": "hours", "at": settings.next_describe_start(cfg, now)}
    if key in AI_WORK and state.get("ollama") is False:
        return {"why": "ollama"}
    if key in SEES and state.get("waiting_for") == "model":
        return {"why": "model"}
    if key in SPEECH_WORK and whisper is False:
        return {"why": "whisper"}
    return None


def snapshot(con: sqlite3.Connection, cfg: Config, kept: dict | None = None, whisper: bool | None = None) -> dict:
    """What the worker is doing, with counts() (or the ones given, kept from a moment ago), each kind of work with what
    holds it up (held(); whisper: whether Whisper answers, None if not asked)."""
    now = datetime.now()
    one = lambda sql, *a: con.execute(sql, a).fetchone()[0]
    state = settings.get(con, "worker", {})
    heartbeat = _time(state.get("heartbeat"))

    numbers = kept or counts(con, cfg)
    total, left = numbers["total"], numbers["left"]
    last = one("SELECT max(created_at) FROM analysis")
    waiting = _with_pace(con, numbers["waiting"], now)
    for w in waiting:
        w["held"] = held(w["key"], cfg, now, state, whisper) if w["left"] else None

    return {
        "now": now,
        "stage": STAGES.get(state.get("stage"), state.get("stage")),
        "stage_since": _time(state.get("stage_since")),
        "silent": heartbeat is None or (now - heartbeat > SILENT_AFTER and not state.get("stage")),
        "heartbeat": heartbeat,
        "pass_finished": _time(state.get("pass_finished")),
        "next_pass": _time(state.get("next_pass")),
        "waiting_for": state.get("waiting_for"),
        "ollama": state.get("ollama"),
        "ollama_checked": _time(state.get("ollama_checked")),
        "run_requested": (settings.get(con, "run_requested") or "") > (state.get("pass_started") or ""),
        "describe": {
            "total": total, "done": total - left, "left": left, "last": _time(last),
            "schedule": settings.schedule_text(cfg),
        },
        "waiting": waiting,
        "downloads": downloads.current(con, now),
        "problems": {settings.PROBLEM_SERVICES[s]: p | {"at": _time(p["at"])} for s, p in settings.problems(con).items()},
        "retrying": retry.summary(con),
        "activity": con.execute("SELECT at, message FROM activity ORDER BY id DESC LIMIT 30").fetchall(),
    }


def _with_pace(con: sqlite3.Connection, waiting: list[dict], now: datetime) -> list[dict]:
    """The waiting rows, each with done (of its total), pace (pace()'s word), per_hour and hours_left."""
    notes = settings.get(con, HISTORY_KEY, [])
    out = []
    for w in waiting:
        how, per_hour = pace(notes, w["key"], w["left"], now) if w["left"] else ("done", None)
        out.append({**w, "done": (w["total"] - w["left"]) if w["total"] else None, "pace": how,
                    "per_hour": per_hour, "hours_left": w["left"] / per_hour if per_hour else None})
    return out


def _time(iso: str | None) -> datetime | None:
    return datetime.fromisoformat(iso) if iso else None
