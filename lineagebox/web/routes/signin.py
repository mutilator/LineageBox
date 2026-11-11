"""Signing in and out: Google, the code for signing in at home, a demo's one button (demo.py), sessions. Passkeys:
routes/passkeys.py."""
import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from lineagebox import accounts, activity, demo, passkeys
from lineagebox.web.common import (MEMBER, PUBLIC, SEE_AS, SIGNING_IN, DemoBody, LocalBody, _client_ip, _from_app,
                                   _local_path, _redirect_uri, tries_from)
from lineagebox.web.deps import page, site
from lineagebox.web.site import CODE_MISSES, Site

GOOGLE_SECONDS = 600  # to sign in at Google and come back
NEXT_MOST = 1000  # the longest page to go back to after signing in, kept in a cookie meanwhile (browsers allow 4 KB)
router = APIRouter()
ACCESS = {
    "/signin": PUBLIC,
    "/auth/google": PUBLIC,
    "/auth/google/callback": PUBLIC,
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
    token = accounts.new_session(c, account_id, now.session_days, _client_ip(request),
                                 request.headers.get("user-agent", ""), passkey_id)
    response.set_cookie(accounts.COOKIE, token, max_age=now.session_days * 86400, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https")
    response.delete_cookie(SIGNING_IN)
    return response


@router.get("/signin", response_class=HTMLResponse)
def signin_page(request: Request, next: str = "/", s: Site = Depends(site)):
    return show_signin(s, request, next)  # no message from the address: a link could put any words on this page


def show_signin(s: Site, request: Request, next: str = "/", error: str = ""):
    now = s.shared_cfg()
    if not now.accounts_enabled or getattr(request.state, "account", None):
        return RedirectResponse(_local_path(next), 303)
    if now.demo_enabled:  # one button, and nothing else
        return page(request, "signin.html", next=_local_path(next), error=error, demo=True, help_page="signin_demo")
    with s.own_con() as c:
        offer = passkeys.any_for(c, now)  # once anyone has one: before that it would only puzzle Google's users
    return page(request, "signin.html", next=_local_path(next), error=error,
                google=bool(now.google_client_id and now.google_client_secret), at_home=request.state.at_home,
                passkeys=offer, passkey_here=passkeys.here(now, request.url.scheme, request.headers.get("host", "")),
                passkey_origin=passkeys.origin(now))


@router.get("/auth/google")
def google_sign_in(request: Request, next: str = "/", s: Site = Depends(site)):
    """Off to Google, which sends the browser back to /auth/google/callback."""
    now = s.shared_cfg()
    if not (now.accounts_enabled and now.google_client_id and now.google_client_secret):
        raise HTTPException(404)
    if not s.sign_ins.allow(tries_from(request)):
        return show_signin(s, request, next, "Too many sign-ins from here just now. Wait a few minutes, then try "
                                          "again.")
    url, keep = accounts.google_start(now.google_client_id, _redirect_uri(request, now))
    keep.update(kind="google", next=_local_path(next) if len(next) <= NEXT_MOST else "/")
    response = RedirectResponse(url, 303)
    # what Google's answer is checked against, in this browser's own cookie (Site.seal): only the browser that
    # started a sign-in can finish it, and nothing is kept here until then
    response.set_cookie(SIGNING_IN, s.seal(keep), max_age=GOOGLE_SECONDS, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https")
    return response


@router.get("/auth/google/callback")
def google_back(request: Request, code: str = "", state: str = "", error: str = "", s: Site = Depends(site)):
    now = s.shared_cfg()
    keep = s.unseal(request.cookies.get(SIGNING_IN), GOOGLE_SECONDS)
    if (error or not code or not state or keep is None or keep.get("kind") != "google"
            or not hmac.compare_digest(keep["state"].encode(), state.encode())):
        return show_signin(s, request, "/", "That sign-in didn't finish. Try again.")
    with s.own_con() as c:
        try:
            email = accounts.google_finish(code, keep, now.google_client_id, now.google_client_secret,
                                           _redirect_uri(request, now), s.google_http)
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


@router.post("/auth/local", dependencies=[Depends(_from_app)])
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
        return start_session(s, request, c, row["id"], JSONResponse({"go": _local_path(body.next)}))


@router.post("/auth/demo", dependencies=[Depends(_from_app)])
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
        return start_session(s, request, c, account_id, JSONResponse({"go": _local_path(body.next)}))


@router.get("/demo-preview.jpg")
def demo_preview(s: Site = Depends(site)):
    """The photo chat apps and social sites show when the demo's address is shared ([demo] preview)."""
    with s.own_con() as c:
        path = demo.preview_image(c, s.shared_cfg())
    if path is None:
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


def signed_out(response: Response) -> Response:
    """The browser forgets the session, and what it kept of the pages and photos seen with it: on a computer the
    family shares, Back then shows nobody's photos to whoever comes next."""
    response.delete_cookie(accounts.COOKIE)
    response.delete_cookie(SEE_AS)
    response.headers["Clear-Site-Data"] = '"cache"'
    return response


@router.post("/auth/signout", dependencies=[Depends(_from_app)])
def sign_out(request: Request, s: Site = Depends(site)):
    with s.own_con() as c:
        accounts.end_session(c, request.cookies.get(accounts.COOKIE))
    return signed_out(JSONResponse({"go": "/signin"}))


@router.post("/auth/signout-everywhere", dependencies=[Depends(_from_app)])
def sign_out_everywhere(request: Request, s: Site = Depends(site)):
    account = getattr(request.state, "account", None)
    if account is None:
        raise HTTPException(400, "Nobody is signed in.")
    with s.own_con() as c:
        accounts.end_sessions(c, account["id"])
    return signed_out(JSONResponse({"go": "/signin"}))
