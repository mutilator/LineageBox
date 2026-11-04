"""Stage: what is said in each video, from the Whisper service (whisper_service/), for the search and the
video's page.

The worker takes the sound out of the video (16 kHz mono WAV) and sends it; the service answers with timed segments.
Whisper writes words over sounds that are not speech ("you", "Thank you.", "Thanks for watching!"), and family
videos are mostly wind, music and children shouting, so segments are filtered here. The service's whole answer is
kept, so better rules (a higher VERSION) re-filter every transcript without asking Whisper again.
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

from lineagebox import retry, search, video
from lineagebox.catalog import VISIBLE
from lineagebox.config import Config
from lineagebox.folders import FolderIndex

VERSION = 1
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
            return "No speech-to-text service is set, so videos get no transcripts."
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


_PENDING = f"""FROM image i WHERE i.media = 'video' AND i.codecs NOT LIKE '%/no sound' AND {VISIBLE}
               AND NOT EXISTS (SELECT 1 FROM transcript t WHERE t.image_id = i.id) AND {retry.waiting("transcribe")}"""


def count_pending(con: sqlite3.Connection) -> int:
    return con.execute(f"SELECT count(*) {_PENDING}").fetchone()[0]


def transcribe(con: sqlite3.Connection, cfg: Config, limit: int | None = None,
               stop: Callable[[], bool] | None = None) -> SpeechStats:
    """Transcribe videos that have sound and no transcript, newest first. While Whisper works on one video, the next
    one's sound is taken out of its file. stop() is asked between videos."""
    refilter(con)
    rows = con.execute(f"""SELECT i.id, (SELECT f.path FROM file f WHERE f.image_id = i.id AND NOT f.missing
                                         ORDER BY length(f.path), f.path LIMIT 1) AS path
                           {_PENDING} ORDER BY i.id DESC""").fetchall()[:limit]
    stats = SpeechStats(pending=len(rows))
    if not rows:
        return stats
    client, folders, t0 = Whisper(cfg), FolderIndex(con), time.monotonic()
    failed_in_a_row = 0

    def sound(row):
        try:
            return row, video.audio_wav(cfg.source_path(row["path"])), None
        except (video.Error, OSError) as e:
            return row, None, e

    with ThreadPoolExecutor(1) as pool:
        todo = iter(rows)
        ahead = pool.submit(sound, next(todo))
        while ahead is not None:
            row, wav, error = ahead.result()
            ahead = None
            if not (stop is not None and stop()):
                ahead = pool.submit(sound, nxt) if (nxt := next(todo, None)) is not None else None
            else:
                stats.stopped = True
            if wav is None:
                stats.failed += 1
                if isinstance(error, (OSError, video.Unavailable, video.TimedOut)):  # not the video: try again later
                    retry.later(con, "transcribe", row["id"], str(error))
                else:  # no usable sound track: an empty transcript, so it is not tried again
                    _record(con, folders, row["id"], "", {"error": str(error), "segments": []})
                continue
            try:
                answer = client.transcribe(wav)
            except httpx.HTTPStatusError as e:
                if e.response.status_code < 500:  # it could not use this file: not sent again, or it would block
                    _record(con, folders, row["id"], "", {"error": f"HTTP {e.response.status_code}", "segments": []})
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
                print(f"video {row['id']}: {type(error).__name__}: {error}", flush=True)
                if isinstance(error, httpx.HTTPStatusError):  # it answered, but not for this video: later, not first
                    retry.later(con, "transcribe", row["id"], stats.last_error)
                if failed_in_a_row >= GIVE_UP_AFTER:
                    stats.gave_up = True
                    if ahead is not None:
                        ahead.cancel()
                    break
                continue
            failed_in_a_row = 0
            _record(con, folders, row["id"], answer.get("model") or "?", answer)
            stats.done += 1
            stats.audio_seconds += float(answer.get("duration") or 0)
    stats.seconds = time.monotonic() - t0
    return stats


def _record(con: sqlite3.Connection, folders: FolderIndex, image_id: int, model: str, answer: dict) -> None:
    kept = keep(answer.get("segments") or [])
    con.execute("""INSERT OR REPLACE INTO transcript(image_id, model, version, created_at, text, segments, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (image_id, model, VERSION, datetime.now().isoformat(timespec="seconds"),
                 " ".join(t for _, _, t in kept), json.dumps(kept), json.dumps(answer)))
    retry.done(con, "transcribe", [image_id])
    search.index_image(con, image_id, folders)
    con.commit()  # one video at a time: each takes long enough that this is a short write between long waits


def refilter(con: sqlite3.Connection) -> int:
    """Apply this version's rules to transcripts kept under older ones."""
    rows = con.execute("SELECT image_id, raw FROM transcript WHERE version < ?", (VERSION,)).fetchall()
    if not rows:
        return 0
    folders = FolderIndex(con)
    for r in rows:
        kept = keep(json.loads(r["raw"]).get("segments") or [])
        con.execute("UPDATE transcript SET version = ?, text = ?, segments = ? WHERE image_id = ?",
                    (VERSION, " ".join(t for _, _, t in kept), json.dumps(kept), r["image_id"]))
        search.index_image(con, r["image_id"], folders)
    con.commit()
    return len(rows)
