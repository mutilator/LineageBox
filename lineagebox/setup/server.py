"""The setup wizard's web server (lineagebox setup; docs/install.md#the-setup-wizard): its page (static/), and the
API the page asks for the compose file and, on the machine itself, about the machine.

Two ways it runs:
- On the machine LineageBox will run on: as LineageBox's image starts before it is set up (the repository's
  compose.yaml; setup.first_start), or as `lineagebox setup` from a shell there. Its root is read-only at /host
  (host.py) and the folder it was started from is at /install, the only place it writes (Save). Looking at the
  machine and saving need the key it printed with its address, so that no one else on the network can look at the
  machine's folders while it runs; without the key, the page makes files as the hosted one does. It stops by itself
  once it hasn't been used for a while.
- Hosted, for anyone on the internet (--hosted; the public demo runs one): no key, nothing of any machine, no Save.
  It only makes files from the answers it is sent, and keeps nothing. Passwords never come to it (compose.py).
"""
import hmac
import os
import posixpath
import time
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from lineagebox import __version__
from lineagebox.setup import compose, host as hostlib, profiles

STATIC = Path(__file__).with_name("static")
MAX_BODY = 64 * 1024
HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                               "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def create_app(hosted=False, host=None, key=None, first=False):
    """The wizard's app. hosted: the public kind, without key or machine; else host is a host.Host (None: no machine
    to look at, as when run from source) and key the one that looking at it and saving need. first: started by
    LineageBox's image in place of LineageBox, from the compose.yaml in /install that the new one replaces."""
    app = FastAPI(title="LineageBox setup", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.last = time.monotonic()
    machine = None if hosted or host is None or not host.available else host
    saving = host if not hosted and host is not None and host.can_save else None
    locks = machine is not None or saving is not None   # with neither, a key would keep nothing from anyone

    @app.middleware("http")
    async def guard(request: Request, call_next):
        app.state.last = time.monotonic()
        try:
            size = int(request.headers.get("content-length") or 0)
        except ValueError:
            return JSONResponse({"detail": "That wasn't what the page sends."}, status_code=400)
        if size > MAX_BODY:
            return JSONResponse({"detail": "That is more than the setup page ever sends."}, status_code=413)
        response = await call_next(request)
        for k, v in HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def unlocked(request: Request):
        given = request.headers.get("x-setup-key", "")
        return not locks or bool(key) and hmac.compare_digest(given.encode(), key.encode())

    def keyed(request: Request):
        if not unlocked(request):
            raise HTTPException(401, "Open the setup with the address it printed when it started: that has its key.")

    def in_place(request):
        """How the new file takes the place of the compose.yaml this runs from: Save puts it there, or people do."""
        if not first:
            return ""
        if saving is not None and unlocked(request):
            return "save"
        return "replace" if host is not None and host.install.is_dir() else ""   # none, as from a bare docker run

    def needs_machine():
        if machine is None:
            raise HTTPException(404, "This setup can't see the machine's folders.")
        return machine

    @app.get("/")
    def page():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/api/info")
    def info(request: Request):
        systems = profiles.load()
        mine = unlocked(request)
        out = {"mode": "hosted" if hosted else "local", "version": __version__, "image": compose.image(),
               "repository": compose.repository(), "systems": list(systems.values()), "whisper_models": compose.WHISPER_MODELS,
               "smb_versions": compose.SMB_VERSIONS, "mb_per_photo": compose.CATALOG_MB_PER_PHOTO,
               "can_browse": mine and machine is not None, "can_save": mine and saving is not None, "detected": None,
               # without its key, the page makes files as the hosted one does, and says how to let it look
               "locked": not mine, "wrong_key": not mine and bool(request.headers.get("x-setup-key")),
               "first": first, "by_hand": first and host is not None and host.set_up_by_hand()}
        if not mine:
            return out
        if machine is not None:
            out["detected"] = machine.detect(systems)
        elif saving is not None:
            out["detected"] = {"install_dir": None, "can_save": True}
        return out

    @app.post("/api/compose")
    async def make(request: Request):
        answers = await _json(request)
        plan = compose.read(answers.get("answers"), profiles.load())
        plan.in_place = in_place(request)
        if machine is not None and unlocked(request):
            plan.problems += look_at_folders(machine, plan)
        return result(plan)

    @app.get("/api/browse", dependencies=[Depends(keyed)])
    def browse(path: str = "/", uid: int | None = None, gid: int | None = None, m=Depends(needs_machine)):
        return m.browse(path, uid, gid)

    @app.post("/api/check", dependencies=[Depends(keyed)])
    async def check(request: Request, m=Depends(needs_machine)):
        body = await _json(request)
        # it waits up to seconds on the network: in a thread, so the page's other requests don't wait with it
        return await run_in_threadpool(check_service, str(body.get("kind")), str(body.get("url") or ""))

    @app.post("/api/save", dependencies=[Depends(keyed)])
    async def save(request: Request):
        if saving is None:
            raise HTTPException(404, "This setup wasn't started with a folder to save in.")
        body = await _json(request)
        plan = compose.read(body.get("answers"), profiles.load())
        plan.in_place = in_place(request)
        if machine is not None:
            plan.problems += look_at_folders(machine, plan)
        here = saving.install_dir() if machine is not None else None
        if here and plan.install_dir and posixpath.normpath(plan.install_dir) != here:
            plan.problems.append(compose.Problem("error", "install_dir", f"This setup was started in {here}, so "
                                                 "that is where compose.yaml is saved: give that folder, or download "
                                                 "the file instead."))
        if plan.errors:
            return JSONResponse({**result(plan), "saved": None}, status_code=400)
        secrets = body.get("secrets") if isinstance(body.get("secrets"), dict) else {}
        try:
            done = write(saving, plan, here or plan.install_dir, secrets, bool(body.get("replace")))
        except FileExistsError:
            return JSONResponse({"exists": True, "detail": "There is a compose.yaml in that folder already."},
                                status_code=409)
        return done

    return app


async def _json(request):
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "That wasn't what the page sends.")
    if not isinstance(body, dict):
        raise HTTPException(400, "That wasn't what the page sends.")
    return body


def result(plan):
    """What the page shows: the file (passwords as stand-ins), what is wrong, the steps, and what was worked out."""
    return {"compose": compose.render(plan), "problems": [vars(p) for p in plan.problems],
            "steps": compose.steps(plan), "network_paths": plan.network_paths,
            "catalog_gb": compose.catalog_gb(plan.photos_count), "folders": compose.folders_to_make(plan)}


def look_at_folders(machine, plan):
    """What the machine itself says of the folders: missing ones, ones the user can't write, a catalog on a share."""
    out = []
    Problem = compose.Problem
    user = f"{plan.puid}:{plan.pgid}"
    checks = [("data", plan.data, None)] + [(f"sources.{i}.path", s.path, s)
                                           for i, s in enumerate(plan.sources) if s.kind == "folder" and s.path]
    for where, path, source in checks:
        may_be_missing = source is None or source.new
        full = plan.full(path)
        if not full.startswith("/"):
            continue
        look = machine.look(full, plan.puid, plan.pgid)
        if where == "data" and machine.is_network(full):
            out.append(Problem("error", where, f"{full} is on a network share ({machine.fs_type(full)}). The "
                                               "catalog's database gets damaged on shares: put it on this machine's "
                                               "own disk."))
        if not look.get("exists"):
            if not may_be_missing:
                out.append(Problem("warning", where, f"There is no folder {full} on this machine (yet)."))
            continue
        if look.get("writable") is False and not plan.root:
            whose = look.get("user") or f"user {look.get('uid')}"
            text = (f"{full} belongs to {whose} (group {look.get('group') or look.get('gid')}), and its permissions "
                    f"don't let {user} write it.")
            if where == "data":
                out.append(Problem("warning", where, text + " LineageBox won't start until it can: change the user "
                                                            "above, or give the folder to them."))
            elif source.writable:
                out.append(Problem("warning", where, text + " Unless ACLs let it, Cleanup, uploads and 720p copies "
                                                            "won't work there: change the user, or make the source "
                                                            "read-only."))
    return out


def write(target, plan, install_dir, secrets, replace):
    """Save on the machine: compose.yaml in /install (one there already kept beside it, with replace), and the
    folders it needs that are inside it, given to LineageBox's user. Folders elsewhere can't be made from here: the
    page lists them."""
    folder = target.install
    file = folder / "compose.yaml"
    kept = None
    if file.exists():
        if not replace:
            raise FileExistsError(file)
        stem, n = f"compose.yaml.before-{datetime.now():%Y%m%d-%H%M%S}", 1
        kept = file.with_name(stem)
        while kept.exists():   # never over an earlier one
            n += 1
            kept = file.with_name(f"{stem}-{n}")
        file.rename(kept)
    text = compose.render(plan, {int(k): str(v) for k, v in secrets.items() if str(k).isdigit()})
    has_password = any(s.has_password for s in plan.smb_sources())
    fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if has_password else 0o644)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    not_given = []   # what couldn't be given to LineageBox's user
    _give(file, plan, not_given)
    made, todo = [], []
    for path in compose.folders_to_make(plan):
        full = posixpath.normpath(posixpath.join(install_dir, path)) if compose.is_relative(path) else path
        if install_dir and compose.inside(full, install_dir) and full != install_dir:
            here = folder / posixpath.relpath(full, install_dir)
            if not here.resolve().is_relative_to(folder.resolve()):   # never outside /install, whatever the path
                todo.append(full)
                continue
            missing = []
            p = here
            while not p.exists() and p != folder:
                missing.append(p)
                p = p.parent
            for p in reversed(missing):
                p.mkdir(mode=0o755)
                _give(p, plan, not_given, full if p == here else None)
            made.append({"path": full, "new": bool(missing)})
        else:
            todo.append(full)
    return {"saved": posixpath.join(install_dir, "compose.yaml") if install_dir else "compose.yaml",
            "kept": kept.name if kept else None, "made": made, "todo": todo, "not_given": not_given}


def _give(path, plan, failed, name=None):
    """Give what Save made to LineageBox's user, when this runs as root (in its container, it does). A disk that
    won't (Docker Desktop's shared folders, whose files have no owner of their own) leaves it as it is, and the page
    says so of a folder (name)."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        try:
            os.chown(path, plan.puid, plan.pgid)
        except OSError:
            if name:
                failed.append(name)


def check_service(kind, url):
    """Whether Ollama or Whisper answers at url, asked from this container: on Docker's bridge network like
    LineageBox's will be, so what answers here answers there."""
    if kind not in ("ollama", "whisper"):
        return {"ok": False, "text": "Nothing to check."}
    url = compose.service(url, 11434 if kind == "ollama" else 9300)
    if not compose.URL.fullmatch(url):
        return {"ok": False, "text": "That isn't an address."}
    asked = url
    if "//host.docker.internal" in url and (gw := hostlib.gateway()):
        asked = url.replace("//host.docker.internal", f"//{gw}")
    name = "Ollama" if kind == "ollama" else "Whisper"
    try:
        r = httpx.get(asked + ("/api/version" if kind == "ollama" else "/health"), timeout=4)
    except httpx.HTTPError:
        hint = (" Ollama answers only its own machine unless OLLAMA_HOST is 0.0.0.0 (docs/ollama.md)."
                if kind == "ollama" else "")
        return {"ok": False, "text": f"Nothing answered at {url}. Is {name} running, and is the address right?{hint}"}
    if r.status_code != 200:
        return {"ok": False, "text": f"{url} answered, but not as {name} does (HTTP {r.status_code})."}
    version = ""
    if kind == "ollama":
        try:
            version = f" {r.json().get('version', '')}".rstrip()
        except ValueError:
            return {"ok": False, "text": f"{url} answered, but not as Ollama does."}
    return {"ok": True, "text": f"{name}{version} answers at {url}."}


def serve(hosted=False, bind="0.0.0.0", port=8080, root="/host", install="/install", idle_minutes=60, first=False):
    """`lineagebox setup`: the wizard until it is stopped (Ctrl+C), or, on a machine, until it has gone unused for
    idle_minutes. first: LineageBox's image started it in place of LineageBox (cli.py, setup.first_start)."""
    import secrets
    import signal
    import socket
    import threading

    import uvicorn

    host = None if hosted else hostlib.Host(root, install)
    key = None if hosted else secrets.token_urlsafe(18)
    app = create_app(hosted, host, key, first)
    if hosted:
        print(f"LineageBox setup (hosted) on port {port}", flush=True)
    else:
        name = ""
        if host.available:
            name = (host.read("/etc/hostname").strip().splitlines() or [""])[0].strip()
        name = name or ("<this machine>" if Path("/.dockerenv").exists() else socket.gethostname())
        lines = [""]
        if first:
            lines += ["LineageBox's image starts this, its setup wizard, until its compose file says",
                      "LINEAGEBOX_SETUP: done, as the one the wizard writes does."]
        lines += ["LineageBox's setup is running. Open this in a browser on your network:", "",
                  f"    http://{name}:{port}/" + (f"?key={key}" if host.available or host.can_save else ""), "",
                  f"(or put this machine's address in place of {name}, and its port on this machine if it isn't "
                  f"{port})."]
        if first:
            lines.append("It looks at this machine's folders, read-only." if host.available else
                         "It can't see this machine's folders: you type the paths.")
            lines.append("It saves the new compose.yaml here, in place of this one. Start that the same way, and "
                         "LineageBox takes the wizard's place." if host.can_save else
                         "Download the file it makes, and start LineageBox with it: in place of the compose.yaml that "
                         "started this wizard, if one did.")
            if host.set_up_by_hand():
                lines += ["", "This folder has a .env file: LineageBox may have been set up here by hand. That way's",
                          "compose file is now compose.manual.yaml: put COMPOSE_FILE=compose.manual.yaml in .env",
                          "(first, where it lists other files), then run docker compose up -d again."]
        else:
            lines.append("It can see this machine's folders, read-only." if host.available else
                         "It can't see this machine's folders: start it with -v /:/host:ro to have it look for you.")
            lines.append("It saves the compose file in the folder it was started from." if host.can_save else
                         "It can't save on this machine (start it with -v \"$PWD\":/install for that): download the "
                         "file instead.")
        if idle_minutes:
            lines.append(("" if first else "Press Ctrl+C to stop it. ")
                         + f"It stops by itself after {idle_minutes} minutes unused"
                         + (": start it again the same way." if first else "."))
        print("\n".join(lines) + "\n", flush=True)
        if idle_minutes:
            def watch():
                while time.monotonic() - app.state.last < idle_minutes * 60:
                    time.sleep(30)
                print(f"The setup went unused for {idle_minutes} minutes, so it has stopped.", flush=True)
                os.kill(os.getpid(), signal.SIGINT)
            threading.Thread(target=watch, daemon=True).start()
    uvicorn.run(app, host=bind, port=port, log_level="warning", access_log=False, proxy_headers=False)
