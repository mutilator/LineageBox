"""What one web app keeps while it runs: create_app (app.py) makes one Site and puts it on app.state, and a route that
needs it takes `s: Site = Depends(site)` (deps.py). Its settings as last looked up, who sees what, the limits on
signing in and downloading, the pages' templates, and a few answers kept a while (a newer release, Whisper's health,
the tours suggested to each viewer)."""
import contextlib
import hashlib
import ipaddress
import threading
import time
from datetime import datetime

import httpx
from fastapi import Request
from fastapi.templating import Jinja2Templates

from lineagebox import __version__, access, accounts, cleanup, db, events, settings, speech, updates
from lineagebox.config import Config
from lineagebox.web import help as page_help
from lineagebox.web.common import (DATE_SOURCE_LABEL, HERE, PAGE_SIZES, fmt_date, fmt_duration, locate, media_path,
                                   on_network)


class Site:
    def __init__(self, cfg: Config, google_http: httpx.Client | None = None):
        """google_http: how Google is reached for signing in (the tests answer for Google)."""
        self.cfg, self.google_http = cfg, google_http
        self.networks = [ipaddress.ip_network(n) for n in cfg.allowed_networks]
        self.shared = {"at": -1e9, "cfg": cfg}
        self.viewers = access.Viewers()
        self.sign_ins = accounts.Limiter(20, 900)  # Google sign-ins started per address in 15 minutes
        self.code_tries = accounts.Limiter(5, 900)  # local sign-in codes tried per address and email in 15 minutes
        # ... and per email from every address together: many addresses on the home network can't share out the guessing
        self.code_tries_each = accounts.Limiter(10, 900)
        self.zip_tries = accounts.Limiter(30, 3600)  # zip downloads per member in an hour: each can be up to ZIP_BYTES
        self.upload_tries = accounts.Limiter(3000, 3600)  # files uploaded per account in an hour
        self.look_tries = accounts.Limiter(40, 3600)  # "Look for faces again" per member in an hour: 10-15 s CPU each
        self.google_started: dict[str, tuple[dict, float]] = {}  # state -> what a Google sign-in keeps until it's back
        self.suggested: dict = {}  # viewer -> {"at", "list"}: tours worked out from what that viewer sees
        self.release = {"at": -1e9, "found": None}
        self.whisper_seen = {"at": -1e9, "url": None, "health": None}
        self.asked_worker = {"at": -1e9}
        self.pulling = threading.Lock()  # one Ollama model download at a time
        self.templates = self._templates()

    @contextlib.contextmanager
    def own_con(self):
        """A catalog connection of the caller's own, closed when done: for what can't use con() (a dependency of its
        own, the work a route hands to a thread)."""
        c = db.connect(self.cfg, migrate_schema=False, check_same_thread=False)
        try:
            yield c
        finally:
            c.close()

    def shared_cfg(self) -> Config:
        """The settings with the changes made on the Settings page (host names, proxies, network paths): looked up at
        most every 2 seconds (each photo page asks for several), and straight away after a save."""
        if time.monotonic() - self.shared["at"] > 2:
            with self.own_con() as c:
                self.shared["cfg"] = settings.current(c, self.cfg)
            self.shared["at"] = time.monotonic()
        return self.shared["cfg"]

    def live(self, c) -> Config:
        """The settings as changed on the Settings page."""
        return settings.current(c, self.cfg)

    def signed_in(self, request: Request, now: Config) -> dict | None:
        """The account whose session the request's cookie is, if any."""
        with self.own_con() as c:
            row = accounts.session_account(c, request.cookies.get(accounts.COOKIE), now.session_days)
            return dict(row) if row else None

    def update_ready(self) -> dict | None:
        """The newer release admins see a link to on every page; looked up again every few minutes."""
        if time.monotonic() - self.release["at"] > 300:
            with self.own_con() as c:
                self.release.update(at=time.monotonic(),
                                    found=updates.available(c) if self.shared_cfg().update_check else None)
        return self.release["found"]

    def whisper_health(self, now: Config) -> dict | None:
        """What the speech-to-text service says about itself (what it downloads), asked at most every 20 seconds: the
        Status page reloads every 10, and a sleeping PC takes the whole timeout to not answer."""
        if not (now.transcribe_enabled and now.whisper_url):
            return None
        if time.monotonic() - self.whisper_seen["at"] > 20 or self.whisper_seen["url"] != now.whisper_url:
            self.whisper_seen.update(at=time.monotonic(), url=now.whisper_url, health=speech.Whisper(now).health())
        return self.whisper_seen["health"]

    def ask_worker(self) -> None:
        """New uploads: the worker starts a pass within 15 seconds (asked at most once a minute)."""
        if time.monotonic() - self.asked_worker["at"] > 60:
            self.asked_worker["at"] = time.monotonic()
            with self.own_con() as c:
                settings.put(c, "run_requested", datetime.now().isoformat(timespec="seconds"))

    def _templates(self) -> Jinja2Templates:
        templates = Jinja2Templates(directory=HERE / "templates")
        templates.env.filters["date"] = fmt_date
        templates.env.filters["duration"] = fmt_duration
        templates.env.filters["gdate"] = events.show_date
        g = templates.env.globals
        g["date_source_label"] = DATE_SOURCE_LABEL
        g["network_path"] = lambda rel: locate(self.shared_cfg(), rel)
        g["media_path"] = lambda rel="": media_path(self.cfg, rel)
        g["on_network"] = lambda rel: on_network(self.shared_cfg(), rel)
        g["removed_folder"] = lambda watched: cleanup.removed_folder(self.cfg, watched)
        g.update(help_for=page_help.for_page, help_everywhere=page_help.EVERYWHERE, help_go=page_help.GO,
                 help_menu=page_help.MENU)
        g["day_names"] = settings.DAY_NAMES
        g["admin_pages"] = ("/", "/status", "/settings")  # not in members' menus
        g["page_sizes"] = PAGE_SIZES
        g["lineagebox_version"] = __version__
        # the help's link to the source code: the repository this copy's releases come from, so a fork's points to it
        g["source_url"] = f"https://github.com/{self.cfg.update_repository}" if self.cfg.update_repository else ""
        g["month_names"] = [""] + [datetime(2000, m, 1).strftime("%B") for m in range(1, 13)]
        # in the stylesheet's and script's URLs, so browsers fetch them again after an update instead of a cached copy
        g["static_version"] = hashlib.sha256(
            b"".join(p.read_bytes() for p in sorted((HERE / "static").iterdir()) if p.is_file())).hexdigest()[:10]
        return templates
