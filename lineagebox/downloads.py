"""Downloads that work waits on, for the Status page: the face models (the worker, the first time faces are looked
for) and a model Ollama pulls when asked to from the Settings page (the web app). The speech-to-text service reports
its own downloads (see speech.Whisper.health).

Each download is a row of the settings table, download:<name>, written at most every few seconds, so the web and
worker containers both see it and neither overwrites the other's.
"""
import json
import sqlite3
import time
from datetime import datetime, timedelta

from lineagebox import settings

PREFIX = "download:"
EVERY = 2.0                              # seconds between progress writes
STALLED = timedelta(minutes=5)           # no progress for this long: the download was cut off (a restart)
SHOW_FINISHED = timedelta(hours=24)


class Progress:
    def __init__(self, con: sqlite3.Connection, name: str, label: str):
        self.con, self.key = con, PREFIX + name
        self.state = {"label": label, "state": "downloading", "done": 0, "total": None, "started": _now(),
                      "updated": None, "error": None}
        self.written = 0.0
        self._write()

    def update(self, done: int, total: int | None = None) -> None:
        self.state["done"], self.state["total"] = done, total or self.state["total"]
        if time.monotonic() - self.written >= EVERY:
            self._write()

    def finish(self) -> None:
        self.state["state"] = "done"
        self._write()

    def fail(self, error: str) -> None:
        self.state["state"], self.state["error"] = "failed", error[:300]
        self._write()

    def _write(self) -> None:
        self.state["updated"] = _now()
        try:
            settings.put(self.con, self.key, self.state)
            self.written = time.monotonic()
        except sqlite3.OperationalError:  # the catalog was busy: written with the next update
            self.con.rollback()


def failed_lately(con: sqlite3.Connection, name: str, error: str, within: timedelta = SHOW_FINISHED) -> bool:
    """Whether the last download of this name failed with this error in the last day: not worth trying again yet."""
    d = settings.get(con, PREFIX + name)
    return bool(d and d.get("state") == "failed" and d.get("error") == error[:300]
                and datetime.now() - datetime.fromisoformat(d.get("updated") or d["started"]) < within)


def current(con: sqlite3.Connection, now: datetime | None = None) -> list[dict]:
    """Downloads running, cut off, or finished or failed in the last day, newest first."""
    now = now or datetime.now()
    out = []
    for (value,) in con.execute(f"SELECT value FROM settings WHERE key LIKE '{PREFIX}%'"):
        d = json.loads(value)
        updated = datetime.fromisoformat(d.get("updated") or d["started"])
        if d["state"] == "downloading" and now - updated > STALLED:
            d["state"] = "stalled"
        if d["state"] != "downloading" and now - updated > SHOW_FINISHED:
            continue
        d["percent"] = 100 * d["done"] / d["total"] if d.get("total") else None
        d["updated"], d["started_at"] = updated, datetime.fromisoformat(d["started"])
        out.append(d)
    return sorted(out, key=lambda d: d["started"], reverse=True)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
