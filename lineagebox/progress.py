"""What the Status page shows about work in progress: the worker's current stage, how far the descriptions are, what
is waiting for each stage, downloads, problems with Ollama or Whisper, and the worker's recent activity."""
import sqlite3
from datetime import datetime, timedelta

from lineagebox import copies, downloads, retry, settings, speech
from lineagebox.catalog import VISIBLE
from lineagebox.config import Config
from lineagebox.describe import count_pending
from lineagebox.faces import STAGE_FACES

STAGES = {"scan": "Looking for new and changed files", "ingest": "Reading new photos",
          "dedup": "Grouping similar shots", "folders": "Reading folder names (AI)",
          "folder dates": "Dating photos from folder names", "faces": "Finding faces",
          "recognise": "Recognising named people", "group faces": "Grouping faces",
          "sharpness": "Measuring sharpness", "places": "Finding places on the map", "search index": "Updating the search",
          "transcribe": "Transcribing videos (Whisper)", "describe": "Describing photos and videos (AI)"}
SILENT_AFTER = timedelta(minutes=3)  # an idle worker checks in every 15 s


def counts(con: sqlite3.Connection, cfg: Config) -> dict:
    """What is left for each stage: on a big library these take a second or more, so the Status page, which asks
    every 10 seconds, keeps them a while (snapshot takes them)."""
    one = lambda sql, *a: con.execute(sql, a).fetchone()[0]
    left = count_pending(con, cfg.ollama_model)
    return {
        "total": one(f"""SELECT count(*) FROM image i
                         WHERE (i.dup_group_id IS NULL OR i.dup_group_id = i.id) AND {VISIBLE}"""),
        "left": left,
        "waiting": [
            ("New or changed files to read", one("""SELECT count(*) FROM file f JOIN folder d ON d.id = f.folder_id
                                                   WHERE f.image_id IS NULL AND NOT f.missing AND NOT d.ignored""")),
            ("Folder names to read (AI)", one("""SELECT count(*) FROM folder WHERE kind IS NULL AND source = 'auto'
                                                AND NOT ignored AND path != ''""")),
            ("Photos and videos to check for faces",
             one(f"SELECT count(*) FROM image i WHERE NOT (i.stage & {STAGE_FACES}) AND {VISIBLE}")),
            ("Photos to measure for sharpness",
             one(f"SELECT count(*) FROM image i WHERE i.sharpness IS NULL AND i.media = 'photo' AND {VISIBLE}")),
            ("Photos to add to the search", one(f"""SELECT count(*) FROM image i WHERE {VISIBLE}
                                                   AND i.id NOT IN (SELECT rowid FROM search_fts)""")),
            ("Photos and videos to describe (AI)", left),
            ("Videos to transcribe (Whisper)", speech.count_pending(con)),
            ("Videos to make a 720p copy of", copies.count_pending(con)),
        ],
    }


def snapshot(con: sqlite3.Connection, cfg: Config, kept: dict | None = None) -> dict:
    """What the worker is doing, with counts() (or the ones given, kept from a moment ago)."""
    now = datetime.now()
    one = lambda sql, *a: con.execute(sql, a).fetchone()[0]
    state = settings.get(con, "worker", {})
    heartbeat = _time(state.get("heartbeat"))

    numbers = kept or counts(con, cfg)
    total, left = numbers["total"], numbers["left"]
    recent = con.execute("SELECT count(*), min(created_at), max(created_at) FROM analysis WHERE created_at >= ?",
                         ((now - timedelta(minutes=30)).isoformat(timespec="seconds"),)).fetchone()
    per_hour = None
    if recent[0] >= 5 and (span := (_time(recent[2]) - _time(recent[1])).total_seconds()) > 0:
        per_hour = (recent[0] - 1) / span * 3600
    last = one("SELECT max(created_at) FROM analysis")

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
            "total": total, "done": total - left, "left": left, "per_hour": per_hour,
            "hours_left": left / per_hour if per_hour else None, "last": _time(last),
            "may_now": settings.may_describe(cfg, now), "schedule": settings.schedule_text(cfg),
            "next_start": settings.next_describe_start(cfg, now),
        },
        "waiting": numbers["waiting"],
        "downloads": downloads.current(con, now),
        "problems": {settings.PROBLEM_SERVICES[s]: p | {"at": _time(p["at"])} for s, p in settings.problems(con).items()},
        "retrying": retry.summary(con),
        "activity": con.execute("SELECT at, message FROM activity ORDER BY id DESC LIMIT 30").fetchall(),
    }


def _time(iso: str | None) -> datetime | None:
    return datetime.fromisoformat(iso) if iso else None
