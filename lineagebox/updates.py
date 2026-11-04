"""Telling admins a newer LineageBox is out, and asking the updater service (updater.py) to install it.

Once a day the worker asks GitHub for the newest release of LineageBox's repository (only its version number, page and
date come back; nothing is sent but the request itself). Settings → Updates can switch that off. A version is a release
when it is tagged vX.Y.Z on GitHub; the Docker images of a release are published as X.Y.Z, X.Y and latest at the same
time, so a container on "latest" gets exactly that version when it is updated.

The web app never touches Docker. It asks for an update by making an empty file, <data>/update/requested, which the
updater only checks is there, and it shows how the update went from the updater's <data>/update/status.json.
"""
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from lineagebox import __version__, settings
from lineagebox.config import Config

LATEST = "https://api.github.com/repos/{}/releases/latest"
KEY = "update_latest"          # settings: what the last check found
EVERY = timedelta(hours=20)    # once a day, give or take when the worker passes by
ALIVE = timedelta(minutes=3)   # the updater writes its status every minute while it runs
_VERSION = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?([-.+]?[0-9A-Za-z.-]*)$")
_REPOSITORY = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")


def version_key(version: str) -> tuple | None:
    """Versions in order: 1.2.0-rc.1 and 1.2.0.dev0 come before 1.2.0, which comes before 1.2.1. None: not a version."""
    m = _VERSION.match(version.strip())
    if not m:
        return None
    numbers = tuple(int(n or 0) for n in m.groups()[:3])
    return numbers + ((0, m[4]) if m[4] else (1, ""))


def newer(latest: str, running: str = __version__) -> bool:
    a, b = version_key(latest), version_key(running)
    return a is not None and b is not None and a > b


def check(con: sqlite3.Connection, cfg: Config, http: httpx.Client | None = None, force: bool = False) -> dict | None:
    """Ask GitHub for the newest release, at most once a day; keeps what it found (or why it couldn't) in settings."""
    if not (cfg.update_check and _REPOSITORY.fullmatch(cfg.update_repository or "")):
        return None
    last = settings.get(con, KEY) or {}
    if not force and last.get("checked_at") and \
            datetime.now() - datetime.fromisoformat(last["checked_at"]) < EVERY:
        return last
    found = {"checked_at": datetime.now().isoformat(timespec="seconds")}
    try:
        r = (http or httpx).get(LATEST.format(cfg.update_repository), timeout=15, follow_redirects=True,
                                headers={"Accept": "application/vnd.github+json",
                                         "User-Agent": f"LineageBox/{__version__} (update check)"})
        if r.status_code == 404:
            found["problem"] = f"{cfg.update_repository} has no releases on GitHub yet."
        elif r.status_code != 200:
            found["problem"] = f"GitHub answered {r.status_code}."
        else:
            data = r.json()
            tag = str(data.get("tag_name") or "")
            if version_key(tag) is None:
                found["problem"] = f"The newest release's name ({tag[:40]}) isn't a version number."
            else:
                url = str(data.get("html_url") or "")
                found.update(version=tag.lstrip("v"), url=url if url.startswith("https://github.com/") else "",
                             published=str(data.get("published_at") or "")[:10])
    except (httpx.HTTPError, ValueError) as e:
        found["problem"] = f"GitHub could not be reached ({type(e).__name__})."
    settings.put(con, KEY, found)
    return found


def available(con: sqlite3.Connection) -> dict | None:
    """The newer release, from the last check: {"version", "url", "published"}; None when this one is the newest."""
    last = settings.get(con, KEY) or {}
    return last if last.get("version") and newer(last["version"]) else None


# ---- the updater service's two files ---------------------------------------------------------------------------------

def folder(cfg: Config) -> Path:
    return cfg.data_dir / "update"


def updater_state(cfg: Config) -> dict | None:
    """The updater's last status, with alive: whether it is running now. None: there is no updater (from source, or
    turned off in compose.yaml)."""
    try:
        state = json.loads((folder(cfg) / "status.json").read_text(encoding="utf-8"))
        seen = datetime.fromisoformat(state["seen"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if seen.tzinfo is None:
        return None
    return {**state, "alive": datetime.now(timezone.utc) - seen < ALIVE,
            "requested": (folder(cfg) / "requested").exists()}


def request_update(cfg: Config) -> None:
    """Ask the updater for an update: an empty file it looks for every few seconds, and never reads."""
    folder(cfg).mkdir(parents=True, exist_ok=True)
    (folder(cfg) / "requested").touch()
