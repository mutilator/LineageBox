"""Signing in and out: Google, the code for signing in at home, a demo's one button (demo.py), sessions. Passkeys:
routes/passkeys.py."""
import hmac
import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from lineagebox import accounts, activity, address, demo, passkeys
from lineagebox.web.common import (MEMBER, PUBLIC, SEE_AS, SIGNING_IN, DemoBody, LocalBody, RelayBody, client_ip,
                                   cookie_name, delete_cookie, from_app, local_path, redirect_uri, set_cookie,
                                   tries_from)
from lineagebox.web.deps import page, site
from lineagebox.web.site import CODE_MISSES, Site

GOOGLE_SECONDS = 600  # to sign in at Google and come back
NEXT_MOST = 1000  # the longest page to go back to after signing in, kept in a cookie meanwhile (browsers allow 4 KB)
router = APIRouter()
ACCESS = {
    "/signin": PUBLIC,
    "/auth/google": PUBLIC,
    "/auth/google/callback": PUBLIC,
    "/auth/google/relay": PUBLIC,
    "/auth/local": PUBLIC,
    "/auth/demo": PUBLIC,
    "/demo-preview.jpg": PUBLIC,
    "/auth/signout": MEMBER,
    "/auth/signout-everywhere": MEMBER,
}


def start_session(s: Site, request: Request, c, account_id: int, response: Response,
                  passkey_id: int | None = None) -> Response:
    """Sign in: the session's cookie on the response. passkey_id: the passkey signed in with (routes/passkeys.py)."""
    now = s.shared_cfg()
    token = accounts.new_session(c, account_id, now.session_days, client_ip(request),
                                 request.headers.get("user-agent", ""), passkey_id)
    set_cookie(request, response, accounts.COOKIE, token, max_age=now.session_days * 86400, samesite="lax")
    if request.url.scheme == "https":  # one 1.11 set without __Host- would otherwise outlive this one
        response.delete_cookie(accounts.COOKIE, secure=True, httponly=True)
    delete_cookie(request, response, SIGNING_IN)
    return response


@router.get("/signin", response_class=HTMLResponse)
def signin_page(request: Request, next: str = "/", s: Site = Depends(site)):
    return show_signin(s, request, next)  # no message from the address: a link could put any words on this page


def show_signin(s: Site, request: Request, next: str = "/", error: str = ""):
    now = s.shared_cfg()
    if not now.accounts_enabled or getattr(request.state, "account", None):
        return RedirectResponse(local_path(next), 303)
    if now.demo_enabled:  # one button, and nothing else
        return page(request, "signin.html", next=local_path(next), error=error, demo=True, help_page="signin_demo")
    with s.own_con() as c:
        offer = passkeys.any_for(c, now)  # once anyone has one: before that it would only puzzle Google's users
    return page(request, "signin.html", next=local_path(next), error=error,
                google=google_way(s, now) is not None, at_home=request.state.at_home,
                passkeys=offer, passkey_here=passkeys.here(now, request.url.scheme, request.headers.get("host", "")),
                passkey_origin=passkeys.origin(now))


def google_way(s: Site, now) -> dict | None:
    """How Sign in with Google goes here: {"own": True} with this server's own Google client; the free address (with
    the client it pinned, google_client) through ourplace.here (Family accounts, names/google.py); or None, not set
    up."""
    if not now.accounts_enabled:
        return None
    if now.google_relay:
        with s.own_con() as c:
            free = address.state(c)
        return free if free and free.get("google_client") and free.get("cert") else None
    return {"own": True} if now.google_client_id and now.google_client_secret else None


@router.get("/auth/google")
def google_sign_in(request: Request, next: str = "/", s: Site = Depends(site)):
    """Off to Google, which sends the browser back to /auth/google/callback (or, through ourplace.here, to
    /auth/google/relay)."""
    now = s.shared_cfg()
    way = google_way(s, now)
    if way is None:
        raise HTTPException(404)
    next = local_path(next) if len(next) <= NEXT_MOST else "/"
    here = request.headers.get("host", "").split(":")[0].lower()
    if not way.get("own") and (here != way["host"] or request.url.scheme != "https"):
        # through ourplace.here a sign-in comes back to the free address: so it starts there, where its cookie is
        return RedirectResponse(f"{address.url(way)}/auth/google?{urlencode({'next': next})}", 303)
    if not s.sign_ins.allow(tries_from(request)):
        return show_signin(s, request, next, "Too many sign-ins from here just now. Wait a few minutes, then try "
                                          "again.")
    if way.get("own"):
        url, keep = accounts.google_start(now.google_client_id, redirect_uri(request, now))
        keep.update(kind="google", next=next)
    else:
        keep = {"kind": "relay", "r": secrets.token_urlsafe(32), "state": secrets.token_urlsafe(24), "next": next}
        url = f"{way['service']}/v1/google/start?" + urlencode({"name": way["name"], "r": keep["r"],
                                                               "state": keep["state"], "port": way.get("port", 443)})
    response = RedirectResponse(url, 303)
    # what Google's answer is checked against, in this browser's own cookie (Site.seal): only the browser that
    # started a sign-in can finish it, and nothing is kept here until then
    set_cookie(request, response, SIGNING_IN, s.seal(keep), max_age=GOOGLE_SECONDS, samesite="lax")
    return response


@router.get("/auth/google/callback")
def google_back(request: Request, code: str = "", state: str = "", error: str = "", s: Site = Depends(site)):
    now = s.shared_cfg()
    keep = s.unseal(request.cookies.get(cookie_name(request, SIGNING_IN)), GOOGLE_SECONDS)
    if (error or not code or not state or keep is None or keep.get("kind") != "google"
            or not hmac.compare_digest(keep["state"].encode(), state.encode())):
        return show_signin(s, request, "/", "That sign-in didn't finish. Try again.")
    with s.own_con() as c:
        try:
            email = accounts.google_finish(code, keep, now.google_client_id, now.google_client_secret,
                                           redirect_uri(request, now), s.google_http)
        except accounts.Problem as e:
            if isinstance(e, accounts.Refused):  # on the Status page, for an admin setting it up
                activity.log(c, f"Google refused a sign-in: {e.why or 'no reason given'}")
            return show_signin(s, request, keep["next"], str(e))
        row = accounts.by_email(c, email)
        if row is None or row["disabled_at"]:
            activity.log(c, f"Sign-in turned away: {email} has no account")
            return show_signin(s, request, keep["next"], f"{email} hasn't been invited to these photos. Ask whoever "
                                                      "looks after them to add it.")
        activity.log(c, f"{row['email']} signed in")
        return start_session(s, request, c, row["id"], RedirectResponse(keep["next"], 303))


@router.get("/auth/google/relay", response_class=HTMLResponse)
def google_relayed_page(request: Request):
    """Where ourplace.here sends the browser back, with Google's token after a # (which no server sees): the page's
    script (app.js, data-google-relay) posts it to the route below."""
    return page(request, "google_relay.html")


GOOGLE_SAID = {"access_denied": "The sign-in was stopped at Google.",
               "google_refused": "Google didn't accept the sign-in. Try again from the start."}


@router.post("/auth/google/relay", dependencies=[Depends(from_app)])
def google_relayed(request: Request, body: RelayBody, s: Site = Depends(site)):
    """Who Google says signed in, through ourplace.here: Google's signature, the client, the nonce and this browser's
    own sign-in all checked (accounts.google_token), then signed in as /auth/google/callback does."""
    way = google_way(s, s.shared_cfg())
    keep = s.unseal(request.cookies.get(cookie_name(request, SIGNING_IN)), GOOGLE_SECONDS)
    if (way is None or way.get("own") or keep is None or keep.get("kind") != "relay"
            or not hmac.compare_digest(keep["state"].encode(), body.state.encode())):
        raise HTTPException(400, "That sign-in didn't finish. Try again.")
    if body.error or not body.id_token:
        raise HTTPException(400, GOOGLE_SAID.get(body.error, "That sign-in didn't finish. Try again."))
    with s.own_con() as c:
        try:
            email = accounts.google_token(body.id_token, way["google_client"],
                                          accounts.relay_nonce(way["host"], keep["r"]), s.google_http)
        except accounts.Problem as e:
            activity.log(c, f"A sign-in with Google through ourplace.here was turned away: {e}"[:200])
            raise HTTPException(400, str(e)) from None
        row = accounts.by_email(c, email)
        if row is None or row["disabled_at"]:
            activity.log(c, f"Sign-in turned away: {email} has no account")
            raise HTTPException(403, f"{email} hasn't been invited to these photos. Ask whoever looks after them to "
                                     "add it.")
        activity.log(c, f"{row['email']} signed in")
        return start_session(s, request, c, row["id"], JSONResponse({"go": keep["next"]}))


@router.post("/auth/local", dependencies=[Depends(from_app)])
def local_sign_in(request: Request, body: LocalBody, s: Site = Depends(site)):
    """Admins at home when Google can't be reached: their email and a code from their authenticator app."""
    if not s.shared_cfg().accounts_enabled:
        raise HTTPException(404)
    if not request.state.at_home:
        raise HTTPException(403, "Signing in with a code only works at home, on the home network.")
    email, here = body.email.strip().lower(), tries_from(request)
    if not (s.code_tries_here.allow(here) and s.code_tries.allow(f"{here} {email}")):
        raise HTTPException(429, "Too many tries. Wait 15 minutes, then try again.")
    with s.own_con() as c:
        if not s.code_tries_each.allow(email):
            activity.log(c, f"Sign-in codes for {email[:80]} were tried too often: stopped for 15 minutes")
            raise HTTPException(429, "Too many tries for this email. Wait 15 minutes, then try again.")
        if not s.code_misses.allow(email, count=False):  # a slow guesser, a few tries every 15 minutes
            raise HTTPException(429, "Codes for this email were wrong too often today. Sign in another way, or try "
                                     "again tomorrow.")
        row = accounts.by_email(c, body.email)
        if (row is None or row["disabled_at"] or row["role"] != "admin" or not row["local_secret"]
                or not accounts.check_code(c, row["id"], row["local_secret"], body.code)):
            activity.log(c, f"A sign-in with a code was turned away ({body.email.strip()[:80]})")
            s.code_misses.allow(email)
            if not s.code_misses.allow(email, count=False):  # that was the last: said once, on the Status page
                activity.log(c, f"Sign-in codes for {email[:80]} were wrong {CODE_MISSES} times today: they are "
                                "stopped until tomorrow")
            raise HTTPException(400, "That email and code don't go together. The code changes every 30 seconds.")
        activity.log(c, f"{row['email']} signed in with a code, at home")
        return start_session(s, request, c, row["id"], JSONResponse({"go": local_path(body.next)}))


@router.post("/auth/demo", dependencies=[Depends(from_app)])
def demo_sign_in(request: Request, body: DemoBody, s: Site = Depends(site)):
    """Sign in to a demo: an account of the visitor's own, made as they click (demo.visitor), a member's or (Sign in
    as an admin) a locked-down admin's."""
    now = s.shared_cfg()
    if not now.demo_enabled:
        raise HTTPException(404)
    if not s.sign_ins.allow(tries_from(request)):
        raise HTTPException(429, "Too many sign-ins from here just now. Wait a few minutes, then try again.")
    with s.own_con() as c:
        account_id = demo.visitor(c, now, admin=body.admin)
        return start_session(s, request, c, account_id, JSONResponse({"go": local_path(body.next)}))


@router.get("/demo-preview.jpg")
def demo_preview(s: Site = Depends(site)):
    """The photo chat apps and social sites show when the demo's address is shared ([demo] preview)."""
    with s.own_con() as c:
        path = demo.preview_image(c, s.shared_cfg())
    if path is None:
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


def signed_out(request: Request, response: Response) -> Response:
    """The browser forgets the session, and what it kept of the pages and photos seen with it: on a computer the
    family shares, Back then shows nobody's photos to whoever comes next."""
    delete_cookie(request, response, accounts.COOKIE)
    delete_cookie(request, response, SEE_AS)
    response.headers["Clear-Site-Data"] = '"cache"'
    return response


@router.post("/auth/signout", dependencies=[Depends(from_app)])
def sign_out(request: Request, s: Site = Depends(site)):
    with s.own_con() as c:
        accounts.end_session(c, request.cookies.get(cookie_name(request, accounts.COOKIE))
                             or request.cookies.get(accounts.COOKIE))
    return signed_out(request, JSONResponse({"go": "/signin"}))


@router.post("/auth/signout-everywhere", dependencies=[Depends(from_app)])
def sign_out_everywhere(request: Request, s: Site = Depends(site)):
    account = getattr(request.state, "account", None)
    if account is None:
        raise HTTPException(400, "Nobody is signed in.")
    with s.own_con() as c:
        accounts.end_sessions(c, account["id"])
    return signed_out(request, JSONResponse({"go": "/signin"}))
