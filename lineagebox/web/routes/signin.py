"""Signing in and out: Google, the code for signing in at home, sessions. Passkeys: routes/passkeys.py."""
import hmac
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from lineagebox import accounts, activity, passkeys
from lineagebox.web.common import (MEMBER, PUBLIC, SEE_AS, SIGNING_IN, LocalBody, _client_ip, _from_app, _local_path,
                                   _redirect_uri)
from lineagebox.web.deps import page, site
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/signin": PUBLIC,
    "/auth/google": PUBLIC,
    "/auth/google/callback": PUBLIC,
    "/auth/local": PUBLIC,
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
    if not s.sign_ins.allow(_client_ip(request)):
        return show_signin(s, request, next, "Too many sign-ins from here just now. Wait a few minutes, then try "
                                          "again.")
    url, keep = accounts.google_start(now.google_client_id, _redirect_uri(request, now))
    keep["next"] = _local_path(next)
    for state in [s for s, (_, at) in s.google_started.items() if time.monotonic() - at > 600]:
        s.google_started.pop(state, None)
    if len(s.google_started) > 2000:  # a family signs in a few at a time; more is someone filling the memory
        return show_signin(s, request, next, "Too many sign-ins at once just now. Wait a few minutes, then try "
                                          "again.")
    s.google_started[keep["state"]] = (keep, time.monotonic())
    response = RedirectResponse(url, 303)
    response.set_cookie(SIGNING_IN, keep["state"], max_age=600, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https")  # this browser started it (no signing in others)
    return response


@router.get("/auth/google/callback")
def google_back(request: Request, code: str = "", state: str = "", error: str = "", s: Site = Depends(site)):
    now = s.shared_cfg()
    kept = s.google_started.pop(state, None) if state else None
    if (error or not code or kept is None or time.monotonic() - kept[1] > 600
            or not hmac.compare_digest(request.cookies.get(SIGNING_IN, ""), state)):
        return show_signin(s, request, "/", "That sign-in didn't finish. Try again.")
    keep = kept[0]
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
    email = body.email.strip().lower()
    if not s.code_tries.allow(f"{_client_ip(request)} {email}"):
        raise HTTPException(429, "Too many tries. Wait 15 minutes, then try again.")
    with s.own_con() as c:
        if not s.code_tries_each.allow(email):
            activity.log(c, f"Sign-in codes for {email[:80]} were tried too often: stopped for 15 minutes")
            raise HTTPException(429, "Too many tries for this email. Wait 15 minutes, then try again.")
        row = accounts.by_email(c, body.email)
        if (row is None or row["disabled_at"] or row["role"] != "admin" or not row["local_secret"]
                or not accounts.check_code(c, row["id"], row["local_secret"], body.code)):
            activity.log(c, f"A sign-in with a code was turned away ({body.email.strip()[:80]})")
            raise HTTPException(400, "That email and code don't go together. The code changes every 30 seconds.")
        activity.log(c, f"{row['email']} signed in with a code, at home")
        return start_session(s, request, c, row["id"], JSONResponse({"go": _local_path(body.next)}))


def signed_out(response: Response) -> Response:
    response.delete_cookie(accounts.COOKIE)
    response.delete_cookie(SEE_AS)
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
