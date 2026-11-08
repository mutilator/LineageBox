"""Stage: what is said in each video and sound recording, and in the stories the family records (stories.py), from
the Whisper service (whisper_service/), for the search and their pages.

The worker takes the sound out of the file (16 kHz mono WAV) and sends it; the service answers with timed segments.
Whisper writes words over sounds that are not speech ("you", "Thank you.", "Thanks for watching!"), and family
videos are mostly wind, music and children shouting, so segments are filtered here. The service's whole answer is
kept, so better rules (a higher VERSION) re-filter every transcript without asking Whisper again.

A transcript's lines can be corrected on its page (correct()): each correction is kept by the second its line starts
at, so filtering again, or hearing the video again, keeps it. The language is the setting's (whisper_language):
"auto" by default, for Whisper to tell from the speech itself.
"""
import json
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import httpx

from lineagebox import errors, retry, search, video
from lineagebox.catalog import VISIBLE
from lineagebox.config import Config
from lineagebox.folders import FolderIndex

VERSION = 1
LINE_MOST = 500  # characters in one corrected line of a transcript
GIVE_UP_AFTER = 3  # failures in a row: the service is gone (its machine went to sleep)
# Measured on the service (whisper.cpp, large-v3-turbo, Silero VAD first): real speech has avg_logprob -0.20 at the
# median and -0.52 at the 10th percentile; below -0.6 are interjections and garbled phrases; the one hallucination in
# 45 minutes ("Thank you.") was at -0.92. whisper.cpp counts timestamp tokens in the average, so its values sit closer
# to 0 than OpenAI Whisper's. turbo always reports no_speech_prob 0.0; the rule below is for other models.
LOW_CONFIDENCE = -0.8   # avg_logprob below this: Whisper was guessing
NO_SPEECH = 0.6         # no_speech_prob above this, with a weak avg_logprob: probably not speech at all
NO_SPEECH_LOGPROB = -0.5
REPEATS = 2             # the same words more often than this in a row: Whisper stuck in a loop
# whole segments that are Whisper's habits on music, wind and silence rather than anything said
HALLUCINATIONS = {
    "you", "thank you", "thank you very much", "thanks", "thanks for watching", "thank you for watching",
    "thanks for watching and see you next time", "please subscribe", "subscribe", "like and subscribe", "bye",
    "bye bye", "okay", "oh", "uh", "um", "hmm", "music", "applause", "laughter", "laughs", "silence", "blank audio",
    "inaudible", "no audio", "subtitles by the amara org community", "transcription by castingwords",
}
_WORDS = re.compile(r"[^\w']+")
_ANNOTATION = re.compile(r"^\s*([\[(*♪].*[\])*♪]|♪+)\s*$")  # [Music], (laughs), *sigh*, ♪ ... ♪


@dataclass
class SpeechStats:
    pending: int = 0
    done: int = 0
    failed: int = 0
    gave_up: bool = False
    stopped: bool = False
    stories: int = 0  # of done: recorded stories
    seconds: float = 0.0
    audio_seconds: float = 0.0
    last_error: str | None = None


class Whisper:
    """Client for whisper_service: GET /health, POST /transcribe with a WAV body."""

    def __init__(self, cfg: Config, timeout: float = 3600):
        self.url, self.language = cfg.whisper_url, cfg.whisper_language
        self.http = httpx.Client(base_url=cfg.whisper_url or "http://whisper.invalid",
                                 timeout=httpx.Timeout(timeout, connect=10))

    def reachable(self) -> bool:
        if not self.url:
            return False
        try:
            return self.http.get("/health", timeout=10).is_success
        except httpx.HTTPError:
            return False

    def health(self, timeout: float = 2) -> dict | None:
        """What the service says about itself (its model, and what it is downloading), or None if it doesn't answer."""
        if not self.url:
            return None
        try:
            return self.http.get("/health", timeout=timeout).json()
        except (httpx.HTTPError, ValueError):
            return None

    def check(self) -> str:
        """A sentence for the Settings page."""
        if not self.url:
            return "No speech-to-text service is set, so videos, recordings and stories get no transcripts."
        try:
            h = self.http.get("/health", timeout=10).json()
        except (httpx.HTTPError, ValueError) as e:
            return f"Not answering ({type(e).__name__}). Is the machine on and the Whisper service running?"
        if d := h.get("downloading"):
            return f"Whisper answers, and is downloading {d.get('file', 'its model')} first (see the Status page)."
        if h.get("error"):
            return f"Whisper answers, but: {h['error']}"
        return f"Whisper answers: {h.get('model', '?')} on the {h.get('device', '?').upper()} ({h.get('engine', '?')})."

    def transcribe(self, wav: bytes) -> dict:
        r = self.http.post("/transcribe", params={"language": self.language}, content=wav,
                           headers={"Content-Type": "audio/wav"})
        r.raise_for_status()
        return r.json()


def keep(segments: list[dict]) -> list[tuple[float, float, str]]:
    """The segments that are probably real speech, as (start, end, text)."""
    out, last, repeats = [], None, 0
    for s in segments:
        text = " ".join(str(s.get("text") or "").split())
        words = _WORDS.sub(" ", text.lower()).strip()
        logprob, no_speech = s.get("avg_logprob"), s.get("no_speech_prob")
        if not words or words in HALLUCINATIONS or _ANNOTATION.match(text):
            continue
        if logprob is not None and logprob < LOW_CONFIDENCE:
            continue
        if no_speech is not None and no_speech > NO_SPEECH and (logprob is None or logprob < NO_SPEECH_LOGPROB):
            continue
        repeats = repeats + 1 if words == last else 0
        last = words
        if repeats >= REPEATS:
            continue
        out.append((round(float(s.get("start") or 0), 2), round(float(s.get("end") or 0), 2), text))
    return out


_PENDING = f"""FROM image i WHERE i.media IN ('video', 'audio') AND i.codecs NOT LIKE '%/no sound' AND {VISIBLE}
               AND NOT EXISTS (SELECT 1 FROM transcript t WHERE t.image_id = i.id) AND {retry.waiting("transcribe")}"""
_STORIES = f"""FROM story s WHERE s.heard IS NULL AND s.removed_at IS NULL
               AND {retry.waiting("stories", "s.id")}"""


def count_pending(con: sqlite3.Connection) -> int:
    """Videos and recordings with sound and no transcript, and recorded stories not heard yet."""
    return (con.execute(f"SELECT count(*) {_PENDING}").fetchone()[0]
            + con.execute(f"SELECT count(*) {_STORIES}").fetchone()[0])


def transcribe(con: sqlite3.Connection, cfg: Config, limit: int | None = None,
               stop: Callable[[], bool] | None = None) -> SpeechStats:
    """Transcribe the recorded stories not heard yet (short, and the family's own words: first), then the videos and
    recordings that have sound and no transcript, newest first. While Whisper works on one, the next one's sound is
    taken out of its file. stop() is asked between them."""
    refilter(con)
    stories = con.execute(f"SELECT s.id, s.file {_STORIES} ORDER BY s.id").fetchall()
    rows = con.execute(f"""SELECT i.id, (SELECT f.path FROM file f WHERE f.image_id = i.id AND NOT f.missing
                                         ORDER BY length(f.path), f.path LIMIT 1) AS path
                           {_PENDING} ORDER BY i.id DESC""").fetchall()
    todo = [("story", r["id"], cfg.data_dir / "stories" / r["file"]) for r in stories]
    todo += [("image", r["id"], cfg.source_path(r["path"])) for r in rows]
    todo = todo[:limit]
    stats = SpeechStats(pending=len(todo))
    if not todo:
        return stats
    client, folders, t0 = Whisper(cfg), FolderIndex(con), time.monotonic()
    failed_in_a_row = 0

    def sound(item):
        try:
            return item, video.audio_wav(item[2]), None
        except (video.Error, OSError) as e:
            return item, None, e

    with ThreadPoolExecutor(1) as pool:
        items = iter(todo)
        ahead = pool.submit(sound, next(items))
        while ahead is not None:
            (kind, item_id, _), wav, error = ahead.result()
            stage = "stories" if kind == "story" else "transcribe"
            ahead = None
            if not (stop is not None and stop()):
                ahead = pool.submit(sound, nxt) if (nxt := next(items, None)) is not None else None
            else:
                stats.stopped = True
            if wav is None:
                stats.failed += 1
                if isinstance(error, (OSError, video.Unavailable, video.TimedOut)):  # not the file: try again later
                    retry.later(con, stage, item_id, str(error))
                else:  # no usable sound track: an empty transcript, so it is not tried again
                    _save(con, folders, kind, item_id, "", {"error": str(error), "segments": []})
                continue
            try:
                answer = client.transcribe(wav)
            except httpx.HTTPStatusError as e:
                if e.response.status_code < 500:  # it could not use this file: not sent again, or it would block
                    _save(con, folders, kind, item_id, "", {"error": f"HTTP {e.response.status_code}", "segments": []})
                    stats.failed += 1
                    stats.last_error = f"Whisper answered {e.response.status_code}: {e.response.text[:200]}"
                    continue
                answer, error = None, e
            except (httpx.HTTPError, ValueError) as e:
                answer, error = None, e
            if answer is None:
                stats.failed += 1
                failed_in_a_row += 1
                stats.last_error = (f"Whisper answered {error.response.status_code}: {error.response.text[:200]}"
                                    if isinstance(error, httpx.HTTPStatusError) else f"{type(error).__name__}: {error}")
                print(f"{kind} {item_id}: {type(error).__name__}: {error}", flush=True)
                if isinstance(error, httpx.HTTPStatusError):  # it answered, but not for this one: later, not first
                    retry.later(con, stage, item_id, stats.last_error)
                if failed_in_a_row >= GIVE_UP_AFTER:
                    stats.gave_up = True
                    if ahead is not None:
                        ahead.cancel()
                    break
                continue
            failed_in_a_row = 0
            _save(con, folders, kind, item_id, answer.get("model") or "?", answer)
            stats.done += 1
            stats.stories += kind == "story"
            stats.audio_seconds += float(answer.get("duration") or 0)
    stats.seconds = time.monotonic() - t0
    return stats


def _save(con: sqlite3.Connection, folders: FolderIndex, kind: str, item_id: int, model: str, answer: dict) -> None:
    if kind == "story":
        _record_story(con, item_id, model, answer)
    else:
        _record(con, folders, item_id, model, answer)


# ---- corrections ---------------------------------------------------------------------------------------------------

def key(start: float) -> str:
    """A line's key in transcript.edits: the second it starts at, as the kept segments have it (to 2 places)."""
    return f"{float(start):.2f}"


def lines(segments: list, edits: dict | None) -> list[dict]:
    """The transcript's lines: Whisper's kept segments, each as someone corrected it, if anyone did. A line corrected
    to nothing (nobody said that) has text "" and removed set: its page shows it faded, to be put back."""
    edits = edits or {}
    out = []
    for start, end, text in segments:
        mine = edits.get(key(start))
        out.append({"start": start, "end": end, "text": text if mine is None else mine, "heard": text,
                    "edited": mine is not None, "removed": mine is not None and not mine.strip()})
    return out


def text_of(segments: list, edits: dict | None) -> str:
    """The transcript's text, for search and the AI: its lines as corrected, without those taken out."""
    return " ".join(ln["text"] for ln in lines(segments, edits) if not ln["removed"])


def for_page(con: sqlite3.Connection, image_id: int) -> dict | None:
    """A video's or recording's transcript for its page: the row, its lines with corrections (lines()), and why it
    couldn't be made, if it couldn't. None before it is transcribed."""
    row = con.execute("SELECT * FROM transcript WHERE image_id = ?", (image_id,)).fetchone()
    if row is None:
        return None
    segments, edits = json.loads(row["segments"]), json.loads(row["edits"] or "{}")
    return {**dict(row), "segments": segments, "lines": lines(segments, edits), "edited": bool(edits),
            "error": json.loads(row["raw"]).get("error")}


class Problem(errors.Problem):
    """A correction that can't be saved; the message says why, for whoever made it."""


def correct(con: sqlite3.Connection, image_id: int, start: float, text: str | None) -> None:
    """Put one line of a transcript right, as a person's edit that hearing or filtering it again never overwrites.
    text: the line as it should be ("" when nothing was said there); None puts back what Whisper heard. The search
    index is the caller's to update. Doesn't commit."""
    row = con.execute("SELECT segments, edits FROM main.transcript WHERE image_id = ?", (image_id,)).fetchone()
    if row is None:
        raise Problem("It hasn't been transcribed yet.")
    segments, edits = json.loads(row["segments"]), json.loads(row["edits"] or "{}")
    k = key(start)
    heard = next((t for s, _, t in segments if key(s) == k), None)
    if heard is None:
        raise Problem("That line isn't in the transcript any more: reload the page.")
    text = None if text is None else " ".join(text.split())[:LINE_MOST]
    if text is None or text == heard:
        edits.pop(k, None)
    else:
        edits[k] = text
    con.execute("UPDATE main.transcript SET edits = ?, text = ?, edited_at = ? WHERE image_id = ?",
                (json.dumps(edits) if edits else None, text_of(segments, edits),
                 datetime.now().isoformat(timespec="seconds") if edits else None, image_id))


# ---- keeping what was heard ----------------------------------------------------------------------------------------

def _record(con: sqlite3.Connection, folders: FolderIndex, image_id: int, model: str, answer: dict) -> None:
    kept = keep(answer.get("segments") or [])
    old = con.execute("SELECT edits FROM transcript WHERE image_id = ?", (image_id,)).fetchone()
    edits = json.loads(old[0] or "{}") if old else {}  # people's corrections stay, whatever Whisper hears this time
    con.execute("""INSERT INTO transcript(image_id, model, version, created_at, text, segments, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(image_id) DO UPDATE SET model = excluded.model, version = excluded.version,
                       created_at = excluded.created_at, text = excluded.text, segments = excluded.segments,
                       raw = excluded.raw""",
                (image_id, model, VERSION, datetime.now().isoformat(timespec="seconds"), text_of(kept, edits),
                 json.dumps(kept), json.dumps(answer)))
    retry.done(con, "transcribe", [image_id])
    search.index_image(con, image_id, folders)
    con.commit()  # one video at a time: each takes long enough that this is a short write between long waits


def _record_story(con: sqlite3.Connection, story_id: int, model: str, answer: dict) -> None:
    """What is said in a recorded story, as Whisper heard it (story.words, what someone wrote, is never touched)."""
    kept = keep(answer.get("segments") or [])
    con.execute("UPDATE story SET heard = ?, segments = ?, raw = ?, model = ? WHERE id = ?",
                (" ".join(t for _, _, t in kept), json.dumps(kept), json.dumps(answer), model, story_id))
    retry.done(con, "stories", [story_id])
    con.commit()  # the search index takes it in on the worker's next look at search_stale (migration 027)


def refilter(con: sqlite3.Connection) -> int:
    """Apply this version's rules to transcripts kept under older ones; people's corrections stay."""
    rows = con.execute("SELECT image_id, raw, edits FROM transcript WHERE version < ?", (VERSION,)).fetchall()
    if not rows:
        return 0
    folders = FolderIndex(con)
    for r in rows:
        kept = keep(json.loads(r["raw"]).get("segments") or [])
        con.execute("UPDATE transcript SET version = ?, text = ?, segments = ? WHERE image_id = ?",
                    (VERSION, text_of(kept, json.loads(r["edits"] or "{}")), json.dumps(kept), r["image_id"]))
        search.index_image(con, r["image_id"], folders)
    con.commit()
    return len(rows)
