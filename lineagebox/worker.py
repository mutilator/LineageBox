"""Keeps the catalog up to date on its own (the Docker `worker` service).

A pass runs every stage, and each stage only does what is new, so an idle pass takes seconds. A pass starts:
- every worker_interval_minutes,
- right away when someone presses "Check now" on the Status page or saves a setting (a newly watched folder is
  scanned at once),
- when the describing schedule's window opens, and when Ollama answers again after being unreachable (the machine
  that runs it was asleep or off).

Transcribing videos (the Whisper service) keeps to the describing hours too, as it usually runs on the same machine as
Ollama; it stops after one interval like describing. The 720p copies of videos are made by a service of their own
(copies.py).

Describing a backlog takes many hours, so it stops after one interval (and when its window closes, a setting changes
or "Check now" is pressed). The next pass then starts at once: it picks up new photos first and carries on describing.

What the worker is doing goes into the settings table ("worker") and the activity table, for the Status page.
"""
import sqlite3
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta

from lineagebox import (accounts, check, cleanup, dedup, faces, orientation, people, places, quality, search,
                        settings, speech, updates, video)
from lineagebox.activity import log, note_problem
from lineagebox.config import Config
from lineagebox.describe import describe
from lineagebox.faces import detect
from lineagebox.folders import date_from_folders, parse_folders
from lineagebox.ingest import ingest
from lineagebox.llm import Ollama
from lineagebox.scan import scan

TICK = 15            # seconds between looks at the clock, "Check now" and the settings
OLLAMA_RETRY = 300   # seconds between checks whether an unreachable Ollama is back


@dataclass
class PassResult:
    run_again: bool = False         # a stage was stopped with work left: start the next pass at once
    waiting_for: str | None = None  # why photos are left undescribed: "schedule", "ollama" (not answering) or "model"
                                    # (its model is missing or can't see); "folders": none are watched yet


def set_state(con: sqlite3.Connection, **changes) -> None:
    state = settings.get(con, "worker", {})
    state.update(changes, heartbeat=_now())
    settings.put(con, "worker", state)


class Stopper:
    """Asked by the long stages, as they go, whether to stop; looks at the database at most every TICK seconds.

    Reading new photos stops when Check now is pressed or a setting is saved (the next pass starts at once, with the
    new settings). Describing also stops when its hours are over, and after one interval.
    """

    def __init__(self, con: sqlite3.Connection, base: Config, budget_seconds: float):
        self.con, self.base = con, base
        self.started, self.started_at = time.monotonic(), _now()
        self.budget = budget_seconds
        self.looked = 0.0
        self.reason: str | None = None

    def __call__(self, describing: bool = True) -> bool:
        now = time.monotonic()
        if self.reason or now - self.looked < TICK:
            return self.reason is not None
        self.looked = now
        try:
            set_state(self.con)  # heartbeat
            if (settings.get(self.con, "run_requested") or "") > self.started_at:
                self.reason = "Check now was pressed"
            elif (settings.get(self.con, "saved_at") or "") > self.started_at:
                self.reason = "the settings changed"
            elif describing and not settings.may_describe(settings.current(self.con, self.base), datetime.now()):
                self.reason = "the describing hours are over"
            elif describing and now - self.started >= self.budget:
                self.reason = "time to look for new photos"
        except sqlite3.OperationalError:  # the web app held the database a long time: look again next time
            pass
        return self.reason is not None

    def reading(self) -> bool:
        return self(describing=False)


CHECK_MINUTES = 20  # files checked for damage per pass, at most: the first passes work through the whole library


def _for_minutes(minutes: float, stop: Stopper | None):
    """A stop() for a stage that gets this long, or less when the pass is told to stop."""
    until = time.monotonic() + minutes * 60
    return lambda: time.monotonic() > until or (stop is not None and stop.reading())


def run_once(con: sqlite3.Connection, cfg: Config, stop: Stopper | None = None) -> PassResult:
    if not cfg.folders:  # a new catalog: nothing to do until a folder is picked on the Settings page (which starts a pass)
        set_state(con, stage=None, stage_since=None)
        return PassResult(waiting_for="folders")
    result = PassResult()

    def stage(name, fn):
        set_state(con, stage=name, stage_since=_now())
        try:
            return fn()
        except Exception:  # one broken stage must not stop the others, or the next pass
            con.rollback()
            print(traceback.format_exc(), flush=True)
            log(con, f"{name} failed: {traceback.format_exc().strip().splitlines()[-1]}")
            return None

    if s := stage("scan", lambda: scan(con, cfg)):
        if s.new or s.changed or s.missing or s.errors:
            log(con, f"scan: {s.new} new, {s.changed} changed, {s.missing} newly missing, {len(s.errors)} errors"
                + (f" ({'; '.join(s.errors[:3])})" if s.errors else ""))
        if s.missing:  # deleted, or in a folder no longer watched: out of the similar-shot and face groups
            stage("dedup", lambda: dedup.group(con))
            stage("group faces", lambda: people.cluster(con))
        # previews and the like of photos deleted on the share; only after a scan that could list every folder
        if not s.errors and (n := stage("tidy previews", lambda: cleanup.drop_derivatives(con, cfg))):
            log(con, f"previews: deleted those of {n} photos whose files are gone")
    # once a day: previews, thumbnails and video frames that are gone are made again by the ingest stage below
    if settings.get(con, "previews_checked") != (today := datetime.now().date().isoformat()):
        if n := stage("check previews", lambda: check.rebuild_derivatives(con, cfg)):
            log(con, f"previews: {n} photos and videos had theirs gone; making them again from the originals")
        settings.put(con, "previews_checked", today)
    if cfg.update_check:  # GitHub's newest release, once a day, for the Status page (updates.py)
        stage("check for updates", lambda: updates.check(con, cfg))
    # Before reading new files, which can take hours (a folder of videos): when the index was made again (a schema
    # change), searching would otherwise find nothing all that time. Nothing to do on most passes.
    if n := stage("search index", lambda: search.index_new(con)):
        log(con, f"search: indexed {n} photos")
    i = stage("ingest", lambda: ingest(con, cfg, show_progress=False, stop=stop.reading if stop else None))
    if i and i.rebuilt:
        log(con, f"ingest: previews made again for {i.rebuilt} photos back in the photo folders")
    if i is not None and (i.no_ffmpeg or video.available()):  # the copies service may say so too
        note_problem(con, "ffmpeg", video.MISSING if i.no_ffmpeg else None)
    if i and i.pending:
        log(con, f"ingest: {i.new_images} new images, {i.duplicates} duplicates, {i.decode_errors} undecodable, "
                 f"{cfg.ingest_threads} at a time"
            + (f"; {i.pending - i.processed} left (stopped: {stop.reason})" if i.stopped and stop else ""))
        if i.new_images:
            stage("dedup", lambda: dedup.group(con))
            stage("uploads", lambda: accounts.claim_uploads(con))  # members' photos stay theirs if moved
        if i.stopped:  # start again straight away, with the new settings
            set_state(con, stage=None, stage_since=None)
            return PassResult(run_again=True)

    # Ollama: descriptions and folder names, if they are on, Ollama answers and its model can do them
    online, problem, ai_now = None, None, False
    if cfg.describe_enabled:
        ollama = Ollama(cfg)
        online = ollama.reachable()
        was_online = settings.get(con, "worker", {}).get("ollama")
        set_state(con, ollama=online, ollama_checked=_now())
        if not online and was_online is not False:
            log(con, f"Ollama at {cfg.ollama_url} is not answering; descriptions wait for a later pass")
        if online:
            problem = ollama.model_problem()
            note_problem(con, "ollama", problem.message if problem else None)
        ai_now = online and settings.may_describe(cfg, datetime.now())
        if ai_now and (problem is None or problem.text_ok):
            if (parsed := stage("folders", lambda: parse_folders(con, ollama, about=cfg.library_about))) and parsed[0]:
                log(con, f"folders: read {parsed[0]} new folder names")
            stage("folder dates", lambda: date_from_folders(con))

    if cfg.faces_enabled:
        def find_faces():
            try:
                found = detect(con, cfg, show_progress=False)
            except faces.Unavailable as e:  # shown on the Status page, rather than failing on every pass
                note_problem(con, "faces", str(e))
                return None
            note_problem(con, "faces", None)
            return found

        f = stage("faces", find_faces)
        if f and f.pending:
            log(con, f"faces: {f.faces} faces in {f.images} photos ({f.seconds / max(f.images, 1):.1f} s/photo)"
                + (f"; {f.unreadable} previews couldn't be opened and are made again" if f.unreadable else ""))
            if f.faces:
                recognised = stage("recognise", lambda: people.identify(con))
                groups = stage("group faces", lambda: people.cluster(con))
                log(con, f"people: recognised {recognised} faces, {groups[0] if groups else '?'} unnamed groups")
        # a video's faces that plainly are someone named on its other frames (people.IN_VIDEO_SIMILARITY)
        if n := stage("faces in videos", lambda: people.name_in_videos(con)):
            log(con, f"people: named {n} faces in videos after the same person on other frames")
    if cfg.places_lookup and (n := stage("places", lambda: places.find_new(con, cfg,
                                                                           stop=stop.reading if stop else None))):
        log(con, f"places: found {n} more place names on the map")
    if n := stage("sharpness", lambda: quality.measure(con, cfg, show_progress=False)):
        log(con, f"sharpness: measured {n} photos")
    # every file read whole once, for damage a disk did (Cleanup → Damaged files): CHECK_MINUTES a pass at most
    if (c := stage("check files", lambda: check.check(con, cfg, stop=_for_minutes(CHECK_MINUTES, stop),
                                                     threads=cfg.ingest_threads))) and c[0]:
        log(con, f"check files: read {c[0]} files whole" + (f"; {c[1]} damaged (Cleanup → Damaged files)" if c[1]
                                                            else ", none damaged"))
    if n := stage("search index", lambda: search.index_new(con)):
        log(con, f"search: indexed {n} new photos")

    if (cfg.transcribe_enabled and settings.may_describe(cfg, datetime.now()) and video.available()
            and speech.Whisper(cfg).reachable() and speech.count_pending(con)):
        if (t := stage("transcribe", lambda: speech.transcribe(con, cfg, stop=stop))) and t.pending:
            log(con, f"transcribe: {t.done} videos ({t.audio_seconds / 60:.0f} min of sound) in "
                     f"{t.seconds / 60:.0f} min, {t.failed} failed, {t.pending - t.done - t.failed} left"
                + (" (Whisper stopped answering)" if t.gave_up else "")
                + (f" (stopped: {stop.reason})" if t.stopped and stop and stop.reason else ""))
            result.run_again = t.stopped and t.done + t.failed < t.pending
            if t.done or t.last_error:  # it works again, or every video failed
                note_problem(con, "whisper", None if t.done else f"Transcribing failed: {t.last_error}")
        if stop is not None and stop.reason:  # its time is used up: describing waits for the next pass
            set_state(con, stage=None, stage_since=None)
            return result

    # the AI's second opinion on the photos their faces say are turned: a few dozen, each asked about once
    if ai_now and problem is None and cfg.faces_enabled and cfg.turn_suggest != "off":
        if n := stage("turned photos", lambda: orientation.check_with_ai(con, cfg, ollama, cfg.turn_suggest, stop)):
            log(con, f"turned photos: the AI looked at {n} that their faces say are turned")

    if not cfg.describe_enabled:
        pass
    elif not online:
        result.waiting_for = "ollama"
    elif problem is not None:
        result.waiting_for = "model"
    elif not ai_now:
        result.waiting_for = "schedule"
    elif d := stage("describe", lambda: describe(con, cfg, show_progress=False, stop=stop)):
        if d.pending:
            minutes = d.seconds / 60
            log(con, f"describe: {d.done} photos in {minutes:.0f} min"
                + (f" ({d.seconds / d.done:.1f} s each)" if d.done else "") + f", {d.failed} failed, "
                + f"{d.pending - d.done - d.failed} left"
                + (" (Ollama stopped answering)" if d.gave_up else "")
                + (f" (stopped: {stop.reason})" if d.stopped and stop and stop.reason else ""))
        result.run_again = result.run_again or d.stopped and d.done + d.failed < d.pending
        if d.done or d.last_error:  # it works, or every photo failed: an error Ollama answers each one with
            note_problem(con, "ollama", None if d.done else f"Describing failed: {d.last_error}")
        if d.gave_up and not ollama.reachable():
            result.waiting_for = "ollama"
    set_state(con, stage=None, stage_since=None)
    return result


def run_forever(con: sqlite3.Connection, base: Config) -> None:
    cfg = settings.current(con, base)
    log(con, f"worker started: watching {', '.join(cfg.folders) or 'no folders yet (pick one on the Settings page)'}; "
             f"Ollama {cfg.ollama_url}; "
             f"looking for new photos every {cfg.worker_interval_minutes} min")
    next_pass, last_start, waiting_for, ollama_tried = 0.0, "", None, 0.0
    while True:
        try:
            cfg = settings.current(con, base)
            reason = None
            if time.monotonic() >= next_pass:
                reason = "scheduled"
            elif (settings.get(con, "run_requested") or "") > last_start:
                reason = "Check now"
            elif (settings.get(con, "saved_at") or "") > last_start:
                reason = "settings changed"
            elif waiting_for == "schedule" and settings.may_describe(cfg, datetime.now()):
                reason = "describing hours started"
            elif waiting_for == "ollama" and time.monotonic() - ollama_tried >= OLLAMA_RETRY:
                ollama_tried = time.monotonic()
                online = Ollama(cfg).reachable()
                set_state(con, ollama=online, ollama_checked=_now())
                if online:
                    reason = "Ollama is answering again"
            elif waiting_for == "model" and time.monotonic() - ollama_tried >= OLLAMA_RETRY:
                ollama_tried = time.monotonic()  # the model may have been downloaded, outside LineageBox too
                if Ollama(cfg).reachable() and Ollama(cfg).model_problem() is None:
                    reason = "Ollama's model is ready"
            if reason:
                last_start = _now()
                set_state(con, pass_started=last_start, pass_reason=reason, next_pass=None)
                if reason != "scheduled":
                    log(con, f"pass started: {reason}")
                budget = cfg.worker_interval_minutes * 60
                started = time.monotonic()
                result = run_once(con, cfg, Stopper(con, base, budget))
                next_pass = time.monotonic() if result.run_again else started + budget
                waiting_for, ollama_tried = result.waiting_for, time.monotonic()
                wait = max(0.0, next_pass - time.monotonic())
                set_state(con, pass_finished=_now(), waiting_for=waiting_for,
                          next_pass=(datetime.now() + timedelta(seconds=wait)).isoformat(timespec="seconds"))
                continue
            try:
                set_state(con)  # heartbeat, so the Status page can tell the worker is alive
            except sqlite3.OperationalError:
                pass
            time.sleep(TICK)
        except Exception:  # a catalog busy for long or full, a service's odd answer: carry on
            con.rollback()
            print(traceback.format_exc(), flush=True)
            log(con, f"worker: {traceback.format_exc().strip().splitlines()[-1]}; trying again in 5 min")
            next_pass = max(next_pass, time.monotonic() + 300)
            time.sleep(TICK)

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
