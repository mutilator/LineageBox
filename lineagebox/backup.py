"""A copy of the catalog every night, checked, and kept for weeks: what the family would otherwise lose with a disk.

The catalog (lineagebox.db in the data folder) holds years of work that no other file has: who is on each face, the
family tree, dates and tags given by hand, captions and comments. The rest of the data folder (previews, thumbnails,
video frames) can be made again from the photos. Before this, the catalog was only copied before an upgrade changed
it (db.py), onto the same disk.

Once a day, on its first pass of the day, the worker copies the catalog with SQLite's backup API, which reads it as it
is at one moment, whatever the web app and the worker are doing. A program that copies the data folder's files itself
(the backup application of a NAS) would get lineagebox.db in the middle of changes, with the latest of them still in
lineagebox.db-wal beside it: a copy that may not open. The copies made here are whole files of their own, ready to be
picked up that way, in the data folder's backups/nightly unless Settings → Backup names another folder (a second disk,
a NAS share, a folder Dropbox or Google Drive syncs).

Each copy is written under a temporary name, opened again and read whole (PRAGMA integrity_check), and only then given
its name, lineagebox-YYYYMMDD-HHMMSS.db. Older copies are deleted only after a new one has passed that check, so a copy
that failed never costs the last good one. Kept: the newest copy of each of the last keep_daily days, and of each of
keep_weekly weeks before those.

Copies aren't compressed. Each is ready to put back as it is, and a backup program that deduplicates (or Dropbox)
sends only the parts that changed since the night before, where a compressed copy would differ all through every
night. Compressing would save less than half anyway: a third of a catalog is face embeddings, which don't compress
(measured on a catalog of 40,000 photos and 58,000 faces: 350 MB, 149 MB gzipped, about 8 KB a photo).
"""
import os
import re
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from lineagebox import db, errors, settings
from lineagebox.activity import log, note_problem
from lineagebox.config import Config
from lineagebox.storage import fmt_size

KEY = "backup"                     # settings: the last good copy, and the last try if it failed
DEFAULT_FOLDER = ("backups", "nightly")  # in the data folder; db.py's copies before upgrades are in backups/
NAME = re.compile(r"lineagebox-(\d{8}-\d{6})\.db")
STEP = 1024                        # pages copied at a time: 4 MB at SQLite's usual page size
RESTARTS = 3                       # a copy started again this often (the web app writing all along) is made in one go
RESERVE = 2**30                    # room left on the disk after a copy: the catalog itself has to keep growing
RETRY_AFTER = timedelta(hours=3)   # after a try that failed
OVERDUE = timedelta(days=2)        # without a good copy for this long: a problem on the Status page
LEFT_OVER = timedelta(hours=12)    # a temporary file this old was left by a copy cut off (a restart, a power cut)
WAIT = 20.0                        # seconds Back up now waits for its copy before answering that it carries on


class Failed(errors.Problem):
    """A copy that couldn't be made or didn't check out; the message says why, for the person who asked."""
    status = 409


@dataclass(frozen=True)
class Copy:
    path: Path
    at: datetime
    size: int


def folder(cfg: Config) -> Path:
    """Where the copies go: Settings → Backup's folder (one a config file gives may be relative to the data folder),
    else the data folder's backups/nightly."""
    if not cfg.backup_folder:
        return cfg.data_dir.joinpath(*DEFAULT_FOLDER)
    return Path(cfg.data_dir, cfg.backup_folder)  # an absolute path stays as it is


def copies(where: Path) -> list[Copy]:
    """The copies in a folder, newest first. Only files named as this module names them: whatever else the folder
    holds is never counted, and never deleted."""
    out = []
    try:
        with os.scandir(where) as listing:
            entries = list(listing)
    except OSError:
        return []
    for e in entries:
        if (m := NAME.fullmatch(e.name)) is None:
            continue
        try:
            out.append(Copy(Path(e.path), datetime.strptime(m[1], "%Y%m%d-%H%M%S"), e.stat().st_size))
        except (OSError, ValueError):  # deleted meanwhile, or a name that only looks like a date
            continue
    return sorted(out, key=lambda c: c.at, reverse=True)


def make(con: sqlite3.Connection, cfg: Config, why: str = "nightly") -> dict:
    """Copy the catalog into the backup folder, check the copy, then delete the copies no longer kept. Returns what is
    kept in settings (KEY). Raises Failed, after noting it for the Status page, when no good copy was made; the copies
    already there are then left as they are."""
    where = folder(cfg)
    now = datetime.now()
    target = where / f"lineagebox-{now:%Y%m%d-%H%M%S}.db"
    part = where / f".{target.stem}-{os.getpid()}-{threading.get_ident()}.part"  # each process and thread its own
    started = time.monotonic()
    try:
        try:
            where.mkdir(parents=True, exist_ok=True)
            need = _size(con)
            free = shutil.disk_usage(where).free
            if free < need + RESERVE:
                raise Failed(f"There isn't room in {where} for a copy of the catalog: it needs about "
                             f"{_mb(need)} and would leave less than {_mb(RESERVE)} free. Make room there, or pick "
                             f"another folder under Settings → Backup.")
            _copy(con, part)
            found = check(part)
            os.replace(part, target)
        except (OSError, sqlite3.Error) as e:
            raise Failed(_why(e, where)) from None
        finally:
            _remove_part(part)
    except Failed as e:
        _record(con, cfg, failed={"at": _iso(now), "message": str(e), "folder": str(where)})  # logged as a problem
        raise
    removed = prune(where, cfg.backup_keep_daily, cfg.backup_keep_weekly, keep=target)
    _with_settings_file(cfg, where)
    _with_stories(con, cfg, where)
    kept = copies(where)
    last = {"at": _iso(now), "file": target.name, "folder": str(where), "bytes": target.stat().st_size,
            "version": found["version"], "seconds": round(time.monotonic() - started, 1), "why": why}
    state = _record(con, cfg, last=last, failed=None, kept=len(kept), kept_bytes=sum(c.size for c in kept))
    log(con, f"backup: {target.name} ({_mb(last['bytes'])}) made and checked in {last['seconds']:.0f} s; "
             f"{len(kept)} kept in {where}" + (f", {len(removed)} older deleted" if removed else ""))
    return state


def _size(con: sqlite3.Connection) -> int:
    """How big a copy will be: the catalog's pages, its -wal's latest changes counted in."""
    return con.execute("PRAGMA page_count").fetchone()[0] * con.execute("PRAGMA page_size").fetchone()[0]


def _copy(con: sqlite3.Connection, part: Path) -> None:
    """The catalog as it is at one moment, into part, a few megabytes at a time.

    In WAL mode (db.connect) reading the catalog never holds up the web app's or the worker's writes. A change made by
    another connection while the copy is going makes SQLite start it again from the first page, so that it stays one
    moment's catalog; if that keeps happening, the copy is made in one step, which reads one moment by itself.
    """
    con.commit()  # the backup reads what is committed: nothing of the caller's is left half-done in it
    dest = sqlite3.connect(part)
    try:
        dest.execute("PRAGMA locking_mode = EXCLUSIVE")  # nobody else opens it: no -shm file, which shares can't hold
        seen = {"left": None, "restarts": 0}

        def progress(status, remaining, total):
            # started again: no nearer the end than the step before (a write after every step starts it again at
            # the same place each time, so that "further from the end" alone would never be seen)
            if seen["left"] is not None and remaining >= seen["left"]:
                seen["restarts"] += 1
                if seen["restarts"] > RESTARTS:
                    raise _StartedAgain
            seen["left"] = remaining

        try:
            con.backup(dest, pages=STEP, progress=progress)
        except _StartedAgain:
            con.backup(dest)
        # the copy says WAL, as the catalog does: as an ordinary database it opens without a -wal file beside it, in
        # any SQLite program, from a folder it can't write to. Put back as the catalog, db.connect makes it WAL again.
        dest.execute("PRAGMA journal_mode = DELETE")
    finally:
        dest.close()


class _StartedAgain(Exception):
    pass


def newest_version() -> int:
    """The newest catalog version this LineageBox knows: its newest migration (db.migrate)."""
    found = [*db.MIGRATIONS_DIR.glob("*.sql"), *db.MIGRATIONS_DIR.glob("*.py")]
    return max(int(p.name.split("_", 1)[0]) for p in found)


def check(path: Path) -> dict:
    """Read a copy whole and say what it holds: {"version", "photos", "people", "named_faces", "bytes"}. Raises Failed
    when it isn't a whole LineageBox catalog. Opened read-only, as it is (nothing beside it is read or written)."""
    path = Path(path)
    if not path.is_file():
        raise Failed(f"There is no file {path}.")
    try:
        con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
        try:
            found = [r[0] for r in con.execute("PRAGMA integrity_check")]
            if found != ["ok"]:
                raise Failed(f"{path.name} is damaged: SQLite's check says {'; '.join(map(str, found[:3]))}.")
            version = con.execute("PRAGMA user_version").fetchone()[0]
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if version == 0 or not {"image", "face", "person"} <= tables:
                raise Failed(f"{path.name} is a database, but not a LineageBox catalog.")
            one = lambda sql: con.execute(sql).fetchone()[0]
            return {"version": version, "photos": one("SELECT count(*) FROM image"),
                    "people": one("SELECT count(*) FROM person"),
                    "named_faces": one("SELECT count(*) FROM face WHERE person_id IS NOT NULL "
                                       "AND assigned_by IN ('user', 'auto')"),
                    "bytes": path.stat().st_size}
        finally:
            con.close()
    except sqlite3.DatabaseError as e:
        raise Failed(f"{path.name} can't be read as a catalog ({e}).") from None


def prune(where: Path, daily: int, weekly: int, keep: Path | None = None) -> list[Path]:
    """Delete the copies no longer kept: kept are the newest copy of each of the last `daily` days that have one, and
    the newest of each of `weekly` weeks before those (Monday to Sunday). The newest copy of all, and `keep`, always
    stay. Returns what was deleted. Temporary files left by copies that were cut off go too."""
    found = copies(where)
    by_day: dict = {}
    for c in found:  # newest first: the first of each day is its newest
        by_day.setdefault(c.at.date(), c)
    days = list(by_day)
    kept = {by_day[d].path for d in days[:max(daily, 1)]}
    by_week: dict = {}
    for d in days[max(daily, 1):]:
        by_week.setdefault(d.isocalendar()[:2], by_day[d])
    kept |= {c.path for c in list(by_week.values())[:max(weekly, 0)]}
    kept |= {found[0].path} if found else set()
    if keep is not None:
        kept.add(keep)
    removed = []
    for c in found:
        if c.path not in kept:
            try:
                c.path.unlink()
                removed.append(c.path)
            except OSError:  # deleted by hand meanwhile, or read-only: tried again next time
                continue
    _left_over_parts(where)
    return removed


def _left_over_parts(where: Path) -> None:
    for part in where.glob(".lineagebox-*.part"):
        try:
            if datetime.now() - datetime.fromtimestamp(part.stat().st_mtime) > LEFT_OVER:
                _remove_part(part)
        except OSError:
            continue


def _remove_part(part: Path) -> None:
    for leftover in (part, part.with_name(part.name + "-journal"), part.with_name(part.name + "-wal")):
        try:
            leftover.unlink(missing_ok=True)
        except OSError:
            pass


def _with_stories(con: sqlite3.Connection, cfg: Config, where: Path) -> None:
    """The recorded stories' sound files (stories.py), which are in the data folder and not in the catalog, copied
    into stories/ beside the copies: each once (they never change), and deleted there only once no story in the
    catalog refers to it any more. A catalog put back from a copy finds its stories there (docs/install.md)."""
    source, target = cfg.data_dir / "stories", where / "stories"
    try:
        if not source.is_dir() or source.resolve() == target.resolve():
            return
        kept = {r[0] for r in con.execute("SELECT file FROM story")}
        for path in source.glob("*/*"):
            rel = path.relative_to(source).as_posix()
            if rel not in kept or not path.is_file():
                continue
            dest = target / rel
            if not dest.is_file() or dest.stat().st_size != path.stat().st_size:
                dest.parent.mkdir(parents=True, exist_ok=True)
                part = dest.with_name(f".{dest.name}.part")
                shutil.copyfile(path, part)
                os.replace(part, dest)
        for path in target.glob("*/*") if target.is_dir() else ():
            if path.relative_to(target).as_posix() not in kept and not path.name.startswith("."):
                path.unlink(missing_ok=True)
    except (OSError, sqlite3.Error) as e:  # the catalog's copy is good: say so, rather than fail it
        log(con, f"backup: the recorded stories could not all be copied beside it ({e})")


def _with_settings_file(cfg: Config, where: Path) -> None:
    """The data folder's own config.toml, when it has one, beside the copies: it holds settings the catalog doesn't
    (an install on a NAS set up that way), and a backup folder on another disk outlives the data folder."""
    own = cfg.data_dir / "config.toml"
    other = where / "config.toml"
    try:
        if own.is_file() and own.resolve() != other.resolve() and (
                not other.is_file() or other.read_bytes() != own.read_bytes()):
            shutil.copyfile(own, other)
    except OSError:  # not worth failing a good copy over
        pass


def _why(e: Exception, where: Path) -> str:
    """What went wrong, in words someone can act on."""
    if isinstance(e, PermissionError):
        return (f"LineageBox can't write in {where}: give the user it runs as (PUID) that folder, or pick another one "
                "under Settings → Backup.")
    if isinstance(e, OSError) and e.errno == 28:  # ENOSPC
        return f"The disk of {where} is full: make room there, or pick another folder under Settings → Backup."
    if isinstance(e, OSError):
        return f"The copy couldn't be written in {where}: {e.strerror or e}."
    return f"The copy couldn't be made: {e}."


def _record(con: sqlite3.Connection, cfg: Config, **changes) -> dict:
    """Keep how the last try went in settings (KEY), and show or clear its problem on the Status page."""
    state = settings.get(con, KEY) or {}
    state.update(changes)
    settings.put(con, KEY, state)
    note_problem(con, "backup", problem(con, cfg))
    return state


# ---- once a day, and for the pages -----------------------------------------------------------------------------------

def due(con: sqlite3.Connection, cfg: Config, now: datetime | None = None) -> bool:
    """Whether the worker makes a copy now: none made today yet (or none in the folder picked last), and no try failed
    in the last few hours."""
    if not cfg.backup_enabled:
        return False
    now = now or datetime.now()
    state = settings.get(con, KEY) or {}
    last, failed = state.get("last") or {}, state.get("failed")
    if failed and failed.get("folder") == str(folder(cfg)) and now - datetime.fromisoformat(failed["at"]) < RETRY_AFTER:
        return False  # a folder just picked instead is tried at once
    return not last or last["at"][:10] < now.date().isoformat() or last.get("folder") != str(folder(cfg))


def nightly(con: sqlite3.Connection, cfg: Config) -> dict | None:
    """The worker's copy of the day; a failure is already noted (and on the Status page) by make()."""
    try:
        return make(con, cfg)
    except Failed:
        return None


def problem(con: sqlite3.Connection, cfg: Config, now: datetime | None = None) -> str | None:
    """What the Status page says is wrong with the copies, if anything: the last try failed, or there has been no
    good copy for OVERDUE (the worker stopped, or busy for days). Worded the same until it changes, so that
    note_problem logs it once."""
    if not cfg.backup_enabled:
        return None
    now = now or datetime.now()
    state = settings.get(con, KEY) or {}
    last, failed = state.get("last"), state.get("failed")
    if failed:  # a good copy since would have taken it off
        return f"The copy of the catalog made at {failed['at'][:16].replace('T', ' ')} failed: {failed['message']}"
    if last and now - datetime.fromisoformat(last["at"]) > OVERDUE:
        return (f"No copy of the catalog has been made since {last['at'][:16].replace('T', ' ')}. The worker makes "
                "one a day: is it running?")
    return None


def summary(con: sqlite3.Connection, cfg: Config) -> dict:
    """For the Status page and Settings → Backup: the last good copy, the copies kept and their size, the last failed
    try and the problem. The folder is listed when this process can see it, else what the worker found last."""
    state = settings.get(con, KEY) or {}
    where = folder(cfg)
    kept = copies(where)
    seen = bool(kept) or where.is_dir()
    return {"enabled": cfg.backup_enabled, "folder": str(where), "default": not cfg.backup_folder,
            "last": state.get("last"), "failed": state.get("failed"),
            "kept": len(kept) if seen else state.get("kept", 0),
            "kept_bytes": sum(c.size for c in kept) if seen else state.get("kept_bytes", 0),
            "problem": problem(con, cfg),
            # what the Status page's list of problems shows already (a failed try, noted by make)
            "noted": (settings.problems(con).get("backup") or {}).get("message")}


# ---- Back up now, beside the web app ---------------------------------------------------------------------------------

_job: dict = {}
_job_lock = threading.Lock()


def soon(cfg: Config, wait: float = WAIT) -> dict | None:
    """A copy made now, in a thread of its own with its own connection, so that a big catalog doesn't keep the page
    waiting: what make() returned (or {"problem": why it failed}) if it is done within `wait` seconds, else None,
    and the copy carries on. Pressed again meanwhile, it waits for the same copy instead of making another."""
    with _job_lock:
        thread = _job.get("thread")
        if thread is None or not thread.is_alive():
            thread = threading.Thread(target=_make_beside, args=(cfg,), daemon=True, name="backup")
            _job.update(thread=thread, result=None)
            thread.start()
    thread.join(wait)
    return None if thread.is_alive() else _job.get("result")


def _make_beside(cfg: Config) -> None:
    try:
        con = db.connect(cfg, migrate_schema=False)
        try:
            _job["result"] = make(con, cfg, why="asked")
        finally:
            con.close()
    except Failed as e:
        _job["result"] = {"problem": str(e)}
    except (OSError, sqlite3.Error) as e:  # the catalog itself couldn't be opened
        _job["result"] = {"problem": f"The catalog couldn't be opened to copy it: {e}."}


def _iso(at: datetime) -> str:
    return at.isoformat(timespec="seconds")


def _mb(n: int) -> str:
    return fmt_size(n)
