"""Web UI: browse by folder, date, person and search; photo and video detail; originals; playing videos.

Bound to this machine (127.0.0.1) or, in Docker, to the container's network. Without family accounts there is no
login: only clients on the networks in `allowed_networks` (private ranges by default) are answered, and only when they
ask for it by address, by a name only a home network has (nas, photos.lan) or by one of `allowed_hosts`. That last check
stops DNS rebinding, where a public site points its own name at this server so that its page can read this one from a
browser at home.

Behind a reverse proxy (for HTTPS) every request comes from the proxy, so the client's address and the scheme are
taken from X-Forwarded-For and X-Forwarded-Proto, but only from the proxies in `trusted_proxies`. Host names and
proxies can be changed on the Settings page, as can HTTPS without a proxy (https.py; `serve` follows it, serving.py).

With family accounts on (Settings → Family accounts), the home network no longer decides: everything but signing in
needs a signed-in account (accounts.py), from anywhere. Admins see and do everything; a member sees their own part of
the family (access.py). Every route is in ROUTES as public, member or admin, and a member's database connection holds
only what they may see; their changes are checked against it and written on a connection of its own (deps.writer).

This module puts the app together: the Site (site.py), the checks every request goes through, the error pages, and
the routes, one module per part of the site in routes/.
"""
import sqlite3
import time
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from lineagebox import access, db, demo, errors, i18n
from lineagebox.config import Config
from lineagebox.web import frames, routes
from lineagebox.web.common import (ADMIN, HERE, PUBLIC, LiveProxyHeaders, _allowed, _content_policy, _host_allowed,
                                   _is_page, _plain_error, _public_path, _small_body)
from lineagebox.web.common import SEE_AS, _local_path, locate, network_path, on_network  # as they were: tests use them
from lineagebox.web.deps import answer, viewer
from lineagebox.web.site import Site

# Who may use each route once family accounts are on (docs/access-control.md, "Route classes"): anyone (to sign in), any
# signed-in account (a member sees only their part of the family: their connection holds nothing else, and every change
# is checked against it) or admins only (others are told there is no such page). Each routes module says so for its own
# routes, in ACCESS. tests/test_access_web.py fails when a route has no rule, and the app answers 500 for it rather than
# letting anyone in.
ROUTES = {path: rule for module in routes.MODULES for path, rule in module.ACCESS.items()}


def all_routes(app: FastAPI) -> list:
    """Every route the app answers, in the order they are tried. Newer FastAPI keeps each included router as one entry
    of app.routes; older copies its routes in."""
    out = []
    for r in app.routes:
        inner = getattr(r, "original_router", None)
        out += inner.routes if inner is not None else [r]
    return out


def route_gate(request: Request, v: access.Viewer = Depends(viewer)) -> None:
    """Each route's rule from ROUTES: anyone, any signed-in account, or admins only (others are told it isn't there,
    as for something that doesn't exist). Seeing the site as a member changes nothing."""
    route = request.scope.get("route")
    kind = ROUTES.get(getattr(route, "path", None))
    if kind is None:
        raise HTTPException(500, "This page has no access rule yet (ACCESS in its module in web/routes/).")
    if v.device is not None and route.path not in frames.ROUTES:
        raise HTTPException(404)  # a photo frame's link opens its frame and the pictures on it, nothing else
    if route.path in demo.CLOSED and request.app.state.site.cfg.demo_enabled:
        raise HTTPException(403, demo.OFF)  # other ways in, sharing, recordings (demo.py)
    if kind != PUBLIC and v is access.NOBODY:
        raise HTTPException(401, "Sign in first.")
    if kind == ADMIN and not v.admin:
        raise HTTPException(404)
    if v.seeing_as and request.method != "GET" and route.path != "/api/see-as/stop":
        raise HTTPException(403, "You are seeing the site as someone else: changes are off until you stop.")


def create_app(cfg: Config, google_http: httpx.Client | None = None) -> FastAPI:
    """The web app. google_http: how Google is reached for signing in (the tests answer for Google)."""
    db.connect(cfg).close()  # apply migrations once at startup
    s = Site(cfg, google_http)
    app = FastAPI(title="LineageBox", docs_url=None, redoc_url=None, openapi_url=None,
                  dependencies=[Depends(route_gate)])
    app.state.site = s

    @app.middleware("http")
    async def gate(request: Request, call_next):
        """Without accounts, the home network only, as before. With them, a signed-in account for everything but
        signing in, from anywhere. Host names are checked either way: that stops DNS rebinding."""
        started = time.perf_counter()
        now = s.shared_cfg()
        request.state.at_home = _allowed(request.client.host if request.client else "", s.networks)
        request.state.account = None
        request.state.device = None
        token = request.scope.get(frames.TOKEN)  # a photo frame's link: frames.DevicePath took it off the address
        if not now.accounts_enabled and not request.state.at_home:
            return PlainTextResponse("This photo catalog is only available on the home network.", status_code=403)
        if not _host_allowed(request.headers.get("host", ""), now.allowed_hosts):
            return PlainTextResponse("This photo catalog is not set up to be reached by this name. Open it by its "
                                     "address and add the name on its Settings page.", status_code=400)
        if token is not None:  # no account, whatever cookies come with it: only the frame and its pictures
            link = await run_in_threadpool(frames.link_for, s, token, request.client.host if request.client else "")
            if link is None:
                return PlainTextResponse(frames.OFF, status_code=404)
            if link["home_only"] and not request.state.at_home:
                return PlainTextResponse(frames.AWAY, status_code=404)
            request.state.device = link
        elif now.accounts_enabled:
            request.state.account = await run_in_threadpool(s.signed_in, request, now)
            if request.state.account is None and not _public_path(request.url.path):
                if request.method == "GET" and _is_page(request.url.path):
                    where = request.url.path + (f"?{request.url.query}" if request.url.query else "")
                    return RedirectResponse("/signin?" + urlencode({"next": where}), 303)
                return JSONResponse({"detail": "Sign in first."}, status_code=401)
            if request.state.account is None and request.method not in ("GET", "HEAD") and not _small_body(request):
                # Signing in needs a few hundred bytes. FastAPI reads a whole body before any check runs, so anyone on
                # the internet could otherwise fill the memory with one large request.
                return JSONResponse({"detail": "That request is too large."}, status_code=413)
        # every page and answer in the visitor's language (i18n.py): with English only, always English
        request.state.language = i18n.choose(request.headers.get("accept-language", ""),
                                             request.cookies.get(i18n.COOKIE), now.language)
        with i18n.speaking(request.state.language):
            response = await call_next(request)
        took = time.perf_counter() - started
        s.note_if_slow(request, took, response.status_code)
        response.headers.setdefault("Server-Timing", f"app;dur={took * 1000:.0f}")  # in the browser's developer tools
        if request.method == "POST" and response.status_code < 400:
            s.viewers.changed()  # who sees what may follow from it: worked out again on the next page
            s.kept.changed()  # and the counts kept a while, and who is signed in (signing out, an account turned off)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        framed = request.state.device is not None  # a photo frame may be shown in a dashboard's frame: it changes nothing
        if not framed:
            response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Content-Security-Policy", _content_policy(now.map_tiles, framed))
        if now.accounts_enabled and request.url.scheme == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    app.add_middleware(frames.DevicePath)  # before the gate: /d/<token>/... is the address without it, to the gate
    # added last, so it runs first: the checks above see the client behind the proxy
    app.add_middleware(LiveProxyHeaders, proxies=lambda: s.shared_cfg().trusted_proxies)

    @app.exception_handler(errors.Problem)
    async def problem(request: Request, exc: errors.Problem):
        """What a module said can't be done (errors.py), in its own words."""
        return answer(request, exc.status, str(exc))

    @app.exception_handler(StarletteHTTPException)  # FastAPI's are these too, and so is a page that isn't there
    async def refused(request: Request, exc: StarletteHTTPException):
        return answer(request, exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def does_not_fit(request: Request, exc: RequestValidationError):
        """Something sent that doesn't fit (too long, not a number, missing): one sentence the page can show, not
        FastAPI's list of what was wrong."""
        return answer(request, 400, _plain_error(exc.errors()))

    @app.exception_handler(sqlite3.OperationalError)
    async def busy(request: Request, exc: sqlite3.OperationalError):
        """The database stayed locked past the busy timeout, the disk did not answer, or the catalog belongs to another
        user than the one LineageBox runs as: say so, instead of a bare "Internal Server Error". Anything else is a
        real error."""
        if "locked" in str(exc):
            message = "The catalog is busy saving other changes. Try again in a moment."
        elif "disk I/O" in str(exc):
            message = "The photo disk did not answer (it may be busy with a big copy). Try again in a moment."
        elif "readonly" in str(exc):
            message = ("The catalog can't be saved: its folder belongs to another user than the one LineageBox runs "
                       "as. See Users and permissions in docs/install.md.")
        else:
            raise exc
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": message}, status_code=503)
        return PlainTextResponse(message, status_code=503, headers={"Retry-After": "10"})

    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    for module in routes.MODULES:  # in this order: where two paths can match one address, the first one listed wins
        app.include_router(module.router)
    return app
