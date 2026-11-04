"""The web server and HTTPS: which certificate it serves now, and when it should start again to serve another (HTTPS
switched on the Settings page, or a renewed certificate in place of the one it loaded). https.py knows certificates;
this follows the settings that name them.
"""
import os
import threading
import time

from lineagebox import db, https, settings
from lineagebox.config import Config

CHECK_EVERY = 30         # seconds between looks at the certificate files, for a renewed one
SETTLE = 1.0             # after a save: time for its answer to reach the browser before the server stops
saved = threading.Event()  # set when the Settings page changes HTTPS: the server starts again the new way now


def tls(cfg: Config) -> tuple[tuple[str, str] | None, str | None]:
    """(certificate, key) to serve now if HTTPS is on and they load, else None; and why they don't, if they don't,
    which is also kept for the Settings and Status pages."""
    con = db.connect(cfg, migrate_schema=False)
    try:
        now = settings.current(con, cfg)
        problem = None
        if now.tls_cert:
            try:
                https.check(now.tls_cert, now.tls_key)
            except https.Problem as e:
                problem = f"The site is on plain HTTP, because its certificate could not be used. {e}"
        settings.set_problem(con, "https", problem)
        return ((now.tls_cert, now.tls_key) if now.tls_cert and not problem else None), problem
    finally:
        con.close()


def watch(cfg: Config, server) -> threading.Event:
    """Stop a uvicorn server once what it should serve changes: HTTPS turned on or off or another certificate (straight
    away when the Settings page says so, through `saved`), or a renewed certificate in place of the one it loaded.
    The returned event tells the caller to start it again."""
    started = _state(cfg)
    changed = threading.Event()

    def look():
        while True:
            if saved.wait(CHECK_EVERY):
                saved.clear()
                time.sleep(SETTLE)
            try:
                now = _state(cfg)
            except Exception:  # a busy catalog: look again next time
                continue
            if now != started:
                changed.set()
                server.should_exit = True
                return

    threading.Thread(target=look, name="https-watch", daemon=True).start()
    return changed


def _state(cfg: Config) -> tuple:
    con = db.connect(cfg, migrate_schema=False)
    try:
        now = settings.current(con, cfg)
    finally:
        con.close()
    return now.tls_cert, now.tls_key, _stamp(now.tls_cert), _stamp(now.tls_key)


def _stamp(path: str):
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    return st.st_mtime_ns, st.st_size
