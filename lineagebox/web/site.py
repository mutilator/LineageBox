"""What one web app keeps while it runs: create_app (app.py) makes one Site and puts it on app.state, and a route that
needs it takes `s: Site = Depends(site)` (deps.py). Its settings as last looked up, who sees what, the limits on
signing in and downloading, the pages' templates, and a few answers kept a while (a newer release, Whisper's health,
the tours suggested to each viewer, the counts that take seconds on a big library, the pages that were slow)."""
import collections
import contextlib
import hashlib
import ipaddress
import threading
import time
from datetime import datetime

import httpx
from fastapi import Request
from fastapi.templating import Jinja2Templates

from lineagebox import __version__, access, accounts, cleanup, db, events, settings, speech, storage, updates
from lineagebox.config import Config
from lineagebox.web import help as page_help
from lineagebox.web.common import (DATE_SOURCE_LABEL, HERE, PAGE_SIZES, fmt_date, fmt_duration, locate, media_path,
                                   on_network)


SIGNED_IN_FOR = 10  # seconds a session's account is kept: a page asks for dozens of thumbnails, each signed in
SLOW_PAGE = 1.0  # seconds: a request that took longer goes on the Status page (Site.slow)
SLOW_KEPT = 40  # slow requests listed there, the newest
# what takes as long as it is big, so isn't timed: pictures, videos, originals, downloads, uploads
UNTIMED = ("/t/", "/p/", "/face/", "/frame/", "/play/", "/original/", "/download", "/api/upload", "/static/")


class Kept:
    """Answers that take a second or more on a big library (tag counts, faces to confirm, the Status page's numbers),
    kept a while under a key that names the viewer (Site.keep). Most are worked out again after any change made
    through the web app (changed()); those kept `through_changes` are not, for a page that reloads after each click
    and takes what changed out of the kept answer itself (Cleanup → Faces). Each is worked out once at a time: what
    asks for it meanwhile waits for that answer rather than working it out too (several name boxes on a page)."""
    MOST = 500  # answers kept at once; beyond that the oldest fifth goes

    def __init__(self):
        self.kept: dict = {}
        self.generation = 0
        self.lock = threading.Lock()
        self.working: dict = {}  # key -> a lock held while its answer is worked out

    def changed(self) -> None:
        with self.lock:
            self.generation += 1

    def get(self, key, seconds: float, work, through_changes: bool = False):
        found, _ = self.peek(key, seconds, through_changes)
        if found is not None:
            return found
        with self.lock:
            working = self.working.setdefault(key, threading.RLock())
        with working:
            found, generation = self.peek(key, seconds, through_changes)  # worked out while this one waited
            if found is not None:
                return found
            value = work()
            self.put(key, value, generation)
        return value

    def peek(self, key, seconds: float, through_changes: bool = False) -> tuple:
        """(the answer kept, or None; the generation to put a new one under). The generation is taken before the
        answer is worked out, so one worked out while a change was made is never kept as if it came after it."""
        with self.lock:
            found = self.kept.get(key)
            if found and time.monotonic() - found[1] < seconds and (through_changes or found[2] == self.generation):
                return found[0], self.generation
            return None, self.generation

    def put(self, key, value, generation: int) -> None:
        with self.lock:
            if len(self.kept) >= self.MOST:
                for old in sorted(self.kept, key=lambda k: self.kept[k][1])[:self.MOST // 5]:
                    del self.kept[old]
                    self.working.pop(old, None)
            self.kept[key] = (value, time.monotonic(), generation)


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
        # passkeys: sign-ins started, and admins' links tried, per address in 15 minutes; what each start keeps until
        # the browser answers (routes/passkeys.py)
        self.passkey_tries = accounts.Limiter(30, 900)
        self.passkey_started: dict[str, tuple[dict, float]] = {}
        self.suggested: dict = {}  # viewer -> {"at", "list"}: tours worked out from what that viewer sees
        self.kept = Kept()
        self.slow = collections.deque(maxlen=SLOW_KEPT)  # {"at", "method", "path", "seconds", "status", "who"}
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
        """The account whose session the request's cookie is, if any. Looked up again after SIGNED_IN_FOR seconds,
        and straight away after any change made through the web app (signing out, an account switched off): a page's
        thumbnails don't each open the catalog to ask."""
        token = request.cookies.get(accounts.COOKIE)
        if not token or len(token) > 100:
            return None
        key = ("signed in", hashlib.sha256(token.encode()).hexdigest(), now.session_days)
        found, generation = self.kept.peek(key, SIGNED_IN_FOR)
        if found is not None:
            return dict(found)
        with self.own_con() as c:
            row = accounts.session_account(c, token, now.session_days)
        if row is None:  # not kept: made-up cookies would only fill the memory
            return None
        self.kept.put(key, dict(row), generation)
        return dict(row)

    def keep(self, v: access.Viewer, name: tuple, seconds: float, work, through_changes: bool = False):
        """kept.get, under a key of the viewer's: an admin's answers are everyone's, a member's their own."""
        return self.kept.get(("all" if v.admin else ("member", v.account_id), *name), seconds, work, through_changes)

    def note_if_slow(self, request: Request, seconds: float, status: int) -> None:
        """A request that took over SLOW_PAGE seconds, for the Status page and the container's log. Pictures, videos,
        downloads and uploads aren't: they take as long as they are big."""
        if seconds < SLOW_PAGE or request.url.path.startswith(UNTIMED):
            return
        account = getattr(request.state, "account", None)
        who = (account.get("person_name") or account.get("email") or account["role"]) if account else ""
        path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        at = datetime.now()
        self.slow.appendleft({"at": at.isoformat(timespec="seconds"), "method": request.method, "path": path[:300],
                              "seconds": seconds, "status": status, "who": who})
        print(f"{at:%Y-%m-%d %H:%M:%S}  slow: {request.method} {path[:300]} took {seconds:.1f} s", flush=True)

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
        templates.env.filters["size"] = storage.fmt_size
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
        g["member_pages"] = ("/folders",)  # nor this in admins': their Folders is the library's, at /
        g["page_sizes"] = PAGE_SIZES
        g["lineagebox_version"] = __version__
        # the help's link to the source code: the repository this copy's releases come from, so a fork's points to it
        g["source_url"] = f"https://github.com/{self.cfg.update_repository}" if self.cfg.update_repository else ""
        g["month_names"] = [""] + [datetime(2000, m, 1).strftime("%B") for m in range(1, 13)]
        # in the stylesheet's and script's URLs, so browsers fetch them again after an update instead of a cached copy
        g["static_version"] = hashlib.sha256(
            b"".join(p.read_bytes() for p in sorted((HERE / "static").iterdir()) if p.is_file())).hexdigest()[:10]
        return templates
