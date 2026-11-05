"""Settings changed on the web UI's Settings page, and the worker's state for the Status page.

Both live in the catalog's settings table, so the web and worker containers share them: the worker reads the settings
at the start of every pass (and while describing, to stop when the schedule says so). config.toml gives the values
until someone changes them here.
"""
import dataclasses
import ipaddress
import json
import os
import re
import sqlite3
from datetime import datetime, time, timedelta
from pathlib import Path

from lineagebox import accounts, config, errors, https, mounts, scan
from lineagebox.config import Config

EDITABLE = ("folders", "ignore_dirs", "ingest_threads", "worker_interval_minutes", "describe_schedule", "describe_from",
            "describe_until", "describe_days", "ollama_url", "ollama_model", "llm_workers", "blurry_below",
            "turn_suggest", "whisper_url", "library_about", "southern_hemisphere", "day_first", "describe_enabled",
            "transcribe_enabled", "faces_enabled",
            "places_lookup", "network_paths", "allowed_hosts", "trusted_proxies", "tls_cert", "tls_key",
            "accounts_enabled", "google_client_id", "google_client_secret", "public_url", "session_days",
            "uploads_folder", "uploads_into", "upload_max_gb", "update_check", "hover_preview_ms")
SCHEDULES = ("always", "hours", "paused")
TURN_LEVELS = ("sure", "more", "off")  # Cleanup → Turned photos, see orientation.py
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
SKIP_ON_SHARE = {"system volume information", "lost+found", "$recycle.bin", "@eadir", "#recycle"}
_CLOCK = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)(:00)?$")  # browsers may add seconds
_LABEL = r"[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?"
_HOST = re.compile(rf"^(\*\.)?({_LABEL}\.)*{_LABEL}$")


class Invalid(errors.Problem):
    """A setting that cannot be used; the message is shown to the person who typed it."""


def current(con: sqlite3.Connection, cfg: Config) -> Config:
    """config.toml's settings with every change made on the Settings page applied."""
    stored = {}
    for key, value in con.execute(f"SELECT key, value FROM settings WHERE key IN ({','.join('?' * len(EDITABLE))})",
                                  EDITABLE):
        value = json.loads(value)
        if key == "network_paths":
            value = [tuple(pair) for pair in value]
        stored[key] = tuple(value) if isinstance(value, list) else value
    return dataclasses.replace(cfg, **stored)


def save(con: sqlite3.Connection, cfg: Config, changes: dict) -> Config:
    """Check and store changed settings. Raises Invalid, storing nothing, if any of them cannot be used."""
    now = current(con, cfg)
    clean = {}
    for key, value in changes.items():
        if key not in EDITABLE:
            raise Invalid(f"{key} cannot be changed here")
        try:
            clean[key] = _CHECKS[key](value, now)
        except Invalid:
            raise
        except (TypeError, ValueError, AttributeError) as e:  # sent as the wrong kind of thing, not by these pages
            raise Invalid(f"{key} can't be {value!r} ({type(e).__name__}).") from None
    if (clean.get("describe_schedule", now.describe_schedule) == "hours"
            and not clean.get("describe_days", now.describe_days)):
        raise Invalid("Pick at least one day for describing.")
    if {"tls_cert", "tls_key"} & clean.keys():
        _tls(clean.get("tls_cert", now.tls_cert), clean.get("tls_key", now.tls_key))
    if {"folders", "ignore_dirs", "uploads_into", "uploads_folder"} & clean.keys():
        _uploads_read(dataclasses.replace(now, **clean), "uploads_into" in clean)
    if clean.get("accounts_enabled") and not now.accounts_enabled:
        if not accounts.admins_exist(con):
            raise Invalid("Add an admin account first (yours): with sign-in on and no admin, nobody could get in.")
    con.executemany("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
                    [(k, json.dumps(list(v) if isinstance(v, tuple) else v)) for k, v in clean.items()])
    if clean.get("southern_hemisphere", now.southern_hemisphere) != now.southern_hemisphere:
        con.execute("DELETE FROM search_fts")  # season words in it change: the worker makes it again on its pass
    con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('saved_at', ?)", (json.dumps(_now()),))
    con.commit()
    return dataclasses.replace(now, **clean)


def watch(con: sqlite3.Connection, cfg: Config, folder: str) -> Config:
    now = current(con, cfg)
    folder = folder_path(now, folder)
    for other in now.folders:
        if folder == other or folder.startswith(other + "/"):
            raise Invalid(f"{folder} is already watched, as part of {other}.")
    # a folder that holds watched folders replaces them
    kept = [f for f in now.folders if not f.startswith(folder + "/")]
    return save(con, cfg, {"folders": [*kept, folder]})


def unwatch(con: sqlite3.Connection, cfg: Config, folder: str) -> Config:
    now = current(con, cfg)
    if folder not in now.folders:
        raise Invalid(f"{folder} is not watched.")
    if len(now.folders) == 1:
        raise Invalid("At least one folder has to be watched.")
    return save(con, cfg, {"folders": [f for f in now.folders if f != folder]})


def folder_path(cfg: Config, text: str) -> str:
    """A watched folder as stored: relative to the share, '/' separated. People may paste where their PC finds it
    (\\\\nas\\photos\\Scans, see network_paths) or type the part after the share (Scans, or Family/Scans)."""
    text = text.strip().replace("\\", "/")
    prefixes = [*cfg.network_roots(), ("", str(cfg.share_root))]
    for folder, prefix in sorted(prefixes, key=lambda p: -len(p[1])):  # the longest that matches
        prefix = prefix.replace("\\", "/").rstrip("/")
        if prefix and (text.lower() == prefix.lower() or text.lower().startswith(prefix.lower() + "/")):
            text = f"{folder}/{text[len(prefix):]}"
            break
    parts = [p for p in text.split("/") if p]
    if not parts:
        raise Invalid("Pick a folder to watch, not all of them at once.")
    if ":" in parts[0]:  # a drive (C:) or an address the paths above didn't match: outside the share
        raise Invalid(f"{text} is not where LineageBox can reach. Pick one of the photo folders it shows.")
    if any(p in (".", "..") for p in parts):
        raise Invalid("Folder names cannot be . or ..")
    if any(p.lower().endswith("-removed") for p in parts):
        raise Invalid("Folders ending in -Removed hold what cleanup removed; they cannot be watched.")
    if not (cfg.share_root / Path(*parts)).is_dir():
        raise Invalid(f"There is no folder {'/'.join(parts)}.")
    candidate = (cfg.share_root / Path(*parts)).resolve()
    for own in (Path(cfg.data_dir).resolve(), Path(cfg.models_dir).resolve()):
        if candidate == own or own in candidate.parents or candidate in own.parents:
            raise Invalid(f"{'/'.join(parts)} holds LineageBox's own catalog (or is in it): its previews and video "
                          f"frames would be read as photos. Pick another folder.")
    return "/".join(parts)


def subfolders(cfg: Config, folder: str) -> list[str]:
    """Folders directly inside a folder of the share (share-relative), for picking one to watch."""
    base = cfg.share_root / folder if folder else cfg.share_root
    try:
        names = [e.name for e in base.iterdir() if e.is_dir() and not e.is_symlink()]
    except OSError:
        return []
    names = [n for n in names if not n.startswith(".") and n.lower() not in SKIP_ON_SHARE]
    return [f"{folder}/{n}" if folder else n for n in sorted(names, key=str.lower)]


def sources(cfg: Config) -> list[tuple[str, str]]:
    """The folders of the share that are mounts of their own (in Docker, each photo source: a folder of this machine,
    a NAS share), with what is wrong with one, if anything, for the Settings page."""
    try:
        names = sorted(os.listdir(cfg.share_root), key=str.lower)
    except OSError:
        return []
    out = []
    for name in names:
        path = cfg.share_root / name
        if not mounts.is_mount(path):
            continue
        try:
            with os.scandir(path) as entries:
                empty = next(entries, None) is None
        except OSError as e:
            out.append((name, f"not reachable ({e.strerror or e})"))
            continue
        if empty:
            out.append((name, "empty: is its disk or share connected?"))
        elif not os.access(path, os.W_OK):
            out.append((name, "read-only: cleanup and 720p copies cannot write here"))
        else:
            out.append((name, ""))
    return out


# ---- describing schedule -------------------------------------------------------------------------------------------

def may_describe(cfg: Config, at: datetime) -> bool:
    if cfg.describe_schedule == "paused":
        return False
    if cfg.describe_schedule != "hours":
        return True
    start, end = _clock(cfg.describe_from), _clock(cfg.describe_until)
    t = at.time()
    if start < end:
        return start <= t < end and at.weekday() in cfg.describe_days
    if start == end:  # all day, on the chosen days
        return at.weekday() in cfg.describe_days
    if t >= start:  # a window that runs past midnight belongs to the day it starts on
        return at.weekday() in cfg.describe_days
    return t < end and (at - timedelta(days=1)).weekday() in cfg.describe_days


def next_describe_start(cfg: Config, at: datetime) -> datetime | None:
    """When describing may start next, if it may not now."""
    if cfg.describe_schedule != "hours" or not cfg.describe_days:
        return None
    start = _clock(cfg.describe_from)
    for days in range(8):
        candidate = datetime.combine(at.date() + timedelta(days=days), start)
        if candidate > at and candidate.weekday() in cfg.describe_days:
            return candidate
    return None


def schedule_text(cfg: Config) -> str:
    if cfg.describe_schedule == "paused":
        return "paused"
    if cfg.describe_schedule != "hours":
        return "any time"
    days = sorted(cfg.describe_days)
    if len(days) == 7:
        on = "every day"
    elif days == [0, 1, 2, 3, 4]:
        on = "on weekdays"
    elif days == [5, 6]:
        on = "on weekends"
    else:
        on = "on " + ", ".join(DAY_NAMES[d] for d in days)
    return f"{cfg.describe_from}–{cfg.describe_until} {on}"


def _clock(text: str) -> time:
    h, m = text.split(":")
    return time(int(h), int(m))


# ---- worker state --------------------------------------------------------------------------------------------------

def get(con: sqlite3.Connection, key: str, default=None):
    row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def put(con: sqlite3.Connection, key: str, value) -> None:
    """Store one value and commit: only called between the worker's stages, never inside another write."""
    con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, json.dumps(value)))
    con.commit()


PROBLEM_SERVICES = {"ollama": "Ollama", "whisper": "Whisper", "https": "HTTPS", "ffmpeg": "Videos",
                    "places": "OpenStreetMap", "faces": "Faces"}


def set_problem(con: sqlite3.Connection, service: str, message: str | None) -> bool:
    """Remember what keeps a service from working for LineageBox (a model that can't see pictures, an error it answers
    every request with), or forget it (None), for the Status and Settings pages. True if that changed anything."""
    if (get(con, f"problem:{service}") or {}).get("message") == message:
        return False
    if message:
        put(con, f"problem:{service}", {"message": message, "at": _now()})
    else:
        con.execute("DELETE FROM settings WHERE key = ?", (f"problem:{service}",))
        con.commit()
    return True


def problems(con: sqlite3.Connection) -> dict[str, dict]:
    """service -> {"message", "at"} for each service with a problem."""
    return {s: p for s in PROBLEM_SERVICES if (p := get(con, f"problem:{s}"))}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---- checks, one per editable setting ------------------------------------------------------------------------------

def _folders(value, cfg: Config) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):  # a string would be read a letter at a time
        raise Invalid("The watched folders come as a list.")
    # only new ones are looked up: a watched folder that is gone from the share for now stays watched
    folders = [v if v in cfg.folders else folder_path(cfg, str(v)) for v in value]
    if not folders:
        raise Invalid("At least one folder has to be watched.")
    for a in folders:
        for b in folders:
            if a != b and b.startswith(a + "/"):
                raise Invalid(f"{b} is inside {a}, which is already watched.")
    return tuple(dict.fromkeys(folders))


def _names(value, cfg: Config) -> tuple[str, ...]:
    if isinstance(value, str):
        value = re.split(r"[,\n]", value)
    names = [str(v).strip() for v in value if str(v).strip()]
    if any("/" in n or "\\" in n for n in names):
        raise Invalid("Folder names to skip are names, not paths.")
    return tuple(dict.fromkeys(names))


def _whole(low: int, high: int, what: str):
    def check(value, cfg: Config) -> int:
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise Invalid(f"{what} must be a whole number.") from None
        if not low <= n <= high:
            raise Invalid(f"{what} must be from {low} to {high}.")
        return n
    return check


def _blurry(value, cfg: Config) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise Invalid("The blur cut-off must be a number.") from None
    if not 1 <= n <= 500:
        raise Invalid("The blur cut-off must be from 1 to 500.")
    return n


def _turn_level(value, cfg: Config) -> str:
    if value not in TURN_LEVELS:
        raise Invalid("Unknown choice for turned photos.")
    return value


def _schedule(value, cfg: Config) -> str:
    if value not in SCHEDULES:
        raise Invalid("Unknown schedule.")
    return value


def _clock_text(value, cfg: Config) -> str:
    m = _CLOCK.match(str(value).strip())
    if not m:
        raise Invalid("Times are written like 22:00.")
    return f"{int(m[1]):02d}:{m[2]}"


def _days(value, cfg: Config) -> tuple[int, ...]:
    try:
        days = sorted({int(d) for d in value})
    except (TypeError, ValueError):
        raise Invalid("Unknown day.") from None
    if any(not 0 <= d <= 6 for d in days):
        raise Invalid("Unknown day.")
    return tuple(days)


def _url(value, cfg: Config) -> str:
    value = str(value).strip().rstrip("/")
    if problem := config.url_problem(value):
        raise Invalid(f"That Ollama address {problem}. It looks like http://192.168.1.10:11434")
    return value


def _whisper_url(value, cfg: Config) -> str:
    value = str(value).strip().rstrip("/")
    if value and (problem := config.url_problem(value)):
        raise Invalid(f"That Whisper address {problem}. It looks like http://192.168.1.10:9300 (or leave it empty for "
                      "no transcripts)")
    return value


def _network_paths(value, cfg: Config) -> tuple[tuple[str, str], ...]:
    """Where people's PCs find folders of the share: {folder: path}, '' for the share itself. Empty ones are dropped."""
    if not isinstance(value, (dict, list, tuple)):
        raise Invalid("Network paths come as folder and path pairs.")
    pairs = value.items() if isinstance(value, dict) else value
    out = {}
    for folder, where in pairs:
        folder = str(folder).replace("\\", "/").strip().strip("/")
        where = str(where).strip()
        if not where:
            continue
        if len(where) > 300 or "\n" in where:
            raise Invalid("A network path is one line, like \\\\nas\\photos or smb://nas/photos.")
        if folder and not (cfg.share_root / folder).is_dir():
            raise Invalid(f"There is no folder {folder} in {cfg.share_root.as_posix()}.")
        out[folder] = where
    return tuple(sorted(out.items()))


def host_names(value, cfg: Config | None = None) -> tuple[str, ...]:
    """Names for the web UI to answer to, one per line or comma-separated. Left out: https://, ports and paths, and
    addresses (they always work)."""
    if isinstance(value, str):
        value = re.split(r"[\s,]+", value)
    elif not isinstance(value, (list, tuple)):
        raise Invalid("Host names come as text, one per line, or as a list.")
    out = []
    for name in value:
        name = re.sub(r"^[a-z]+://", "", str(name).strip().lower()).split("/")[0]
        if name.startswith("[") or name.count(":") > 1:
            continue  # an IPv6 address
        name = name.split(":")[0].rstrip(".")
        if not name:
            continue
        try:
            ipaddress.ip_address(name)
            continue
        except ValueError:
            pass
        if name == "*":
            raise Invalid("List the names themselves: * would answer to any name, and so let other websites read "
                          "your photos through your browser.")
        if not _HOST.match(name):
            raise Invalid(f"{name} is not a host name like photos.example.com, or *.example.com for every name "
                          "under it.")
        if name.startswith("*.") and name.count(".") < 2:
            raise Invalid(f"{name} is every name under a whole top-level domain, which would let other websites "
                          "read your photos through your browser. Give your own domain, like *.example.com.")
        out.append(name)
    return tuple(dict.fromkeys(out))


def _proxies(value, cfg: Config) -> tuple[str, ...]:
    if isinstance(value, str):
        value = re.split(r"[\s,]+", value)
    out = []
    for item in value:
        item = str(item).strip()
        if not item:
            continue
        try:
            net = ipaddress.ip_network(item, strict=False)
        except ValueError:
            raise Invalid(f"{item} is not an address or network, like 172.18.0.2 or 172.18.0.0/16.") from None
        if net.prefixlen < (8 if net.version == 4 else 16):
            raise Invalid(f"{item} is far wider than any proxy's network: anyone in it could claim to be at home. "
                          "Give the proxy's own address or its Docker network, like 172.18.0.0/16.")
        # as a network uvicorn can read: it takes "172.18.0.1/16" (host bits set) for a name and never matches it
        out.append(str(net.network_address) if net.num_addresses == 1 else str(net))
    return tuple(dict.fromkeys(out))


def _file(value, cfg: Config) -> str:
    value = str(value).strip()
    if len(value) > 500 or "\n" in value:
        raise Invalid("A file is one path, like /data/certs/fullchain.pem.")
    return value


def _tls(cert: str, key: str) -> None:
    """HTTPS is set with a certificate and its key that load together and are still good, or turned off."""
    if bool(cert) != bool(key):
        raise Invalid("Give both the certificate and its private key, or neither.")
    if not cert:
        return
    try:
        found = https.check(cert, key)
    except https.Problem as e:
        raise Invalid(str(e)) from None
    if found.expired:
        raise Invalid(f"This certificate expired on {found.expires:%Y-%m-%d}. Use a renewed one.")


def _switch(value, cfg: Config) -> bool:
    if isinstance(value, bool):
        return value
    if str(value).lower() in ("true", "on", "1"):
        return True
    if str(value).lower() in ("false", "off", "0", ""):
        return False
    raise Invalid("A switch is on or off.")


def _about(value, cfg: Config) -> str:
    value = " ".join(str(value).split())
    if len(value) > 200:
        raise Invalid("Keep it short, like: a family in Oregon, USA.")
    return value


def _client_id(value, cfg: Config) -> str:
    value = str(value).strip()
    if value and not re.fullmatch(r"[\w.-]{1,200}\.apps\.googleusercontent\.com", value):
        raise Invalid("Google's client ID ends in .apps.googleusercontent.com (Google Cloud → APIs & Services → "
                      "Credentials).")
    return value


def _secret(value, cfg: Config) -> str:
    value = str(value).strip()
    if len(value) > 200 or any(c.isspace() for c in value):
        raise Invalid("Paste the client secret as Google shows it.")
    return value


def _public_url(value, cfg: Config) -> str:
    value = str(value).strip().rstrip("/")
    if value and not re.match(r"^https://[a-z0-9.-]+(:\d+)?$", value.lower()):
        raise Invalid("The address people use from outside looks like https://photos.example.com (Google only "
                      "sends people back to https addresses).")
    return value


def _folder_name(value, cfg: Config) -> str:
    value = " ".join(str(value).split())
    if not value or len(value) > 60 or any(c in value for c in '/\\:*?"<>|') or value in (".", ".."):
        raise Invalid("The uploads folder is one folder's name, like Family uploads.")
    return value


def _uploads_into(value, cfg: Config) -> str:
    return str(value).strip().strip("/")  # that it is watched is checked with the folders (_uploads_read)


def _uploads_read(cfg: Config, moved: bool) -> None:
    """Members' uploads go where the scan reads them: into a watched folder, and not into a folder it skips. moved:
    the uploads folder itself is being changed (otherwise the watched folders or the folders to skip are)."""
    into = cfg.uploads_into
    if into and not any(into == f or into.startswith(f + "/") for f in cfg.folders):
        raise Invalid("Uploads go into one of the watched folders." if moved else
                      f"Members' uploads go into {into}: pick another folder for them under Family accounts → "
                      f"Uploads first.")
    name = cfg.uploads_folder
    if name and (name.lower().endswith("-removed") or scan.is_ignored(name, cfg.ignore_dirs)):
        raise Invalid(f"Folders called {name} are skipped when looking for photos, so uploads there would never be "
                      f"shown: pick another name for the uploads folder, or take it off the folders to skip.")


def _model(value, cfg: Config) -> str:
    value = str(value).strip()
    if not value or len(value) > 100 or any(c.isspace() for c in value):
        raise Invalid("Give the model's name as Ollama lists it, e.g. ministral-3.")
    return value


def _ranged(field: str, what: str):
    return _whole(*config.RANGES[field], what)


_CHECKS = {
    "folders": _folders, "ignore_dirs": _names, "ingest_threads": _ranged("ingest_threads", "Photos read at a time"),
    "worker_interval_minutes": _ranged("worker_interval_minutes", "The check interval"),
    "describe_schedule": _schedule, "describe_from": _clock_text, "describe_until": _clock_text, "describe_days": _days,
    "ollama_url": _url, "ollama_model": _model, "llm_workers": _ranged("llm_workers", "Parallel requests"),
    "blurry_below": _blurry, "turn_suggest": _turn_level, "whisper_url": _whisper_url, "library_about": _about,
    "southern_hemisphere": _switch, "day_first": _switch,
    "describe_enabled": _switch, "transcribe_enabled": _switch, "faces_enabled": _switch, "places_lookup": _switch,
    "update_check": _switch,
    "network_paths": _network_paths, "allowed_hosts": host_names, "trusted_proxies": _proxies,
    "tls_cert": _file, "tls_key": _file,
    "accounts_enabled": _switch, "google_client_id": _client_id, "google_client_secret": _secret,
    "public_url": _public_url, "session_days": _ranged("session_days", "Days signed in"),
    "uploads_folder": _folder_name, "uploads_into": _uploads_into,
    "upload_max_gb": _ranged("upload_max_gb", "The largest upload"),
    "hover_preview_ms": _ranged("hover_preview_ms", "The time to point at a photo"),
}
