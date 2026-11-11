"""Passkeys (passkeys.py): signing in with one, making the first with an admin's link, adding more on the Passkeys
page, and the admins' links.

Each use of a passkey is two requests: a start, whose answer the browser hands to the phone or computer
(navigator.credentials), and a finish with what it signed. What a start keeps until then (the challenge, and whose
passkey it is for) is in a cookie of that browser's, signed (Site.seal), so nothing is kept here for starts that
never finish. A challenge signs in once (Site.passkey_used); a link makes one passkey (passkeys.use_invite), and a
passkey is kept once.
"""
import base64
import hashlib
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from lineagebox import access, accounts, activity, passkeys
from lineagebox.web.common import (ADMIN, MEMBER, PASSKEY_STARTED, PUBLIC, InviteBody, PasskeyBody, PasskeyName,
                                   PasskeyStart, _from_app, _local_path, _recently_signed_in, tries_from)
from lineagebox.web.deps import page, site, viewer
from lineagebox.web.routes.signin import start_session
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/auth/passkey/start": PUBLIC,
    "/auth/passkey/finish": PUBLIC,
    "/auth/invite": PUBLIC,
    "/auth/invite/check": PUBLIC,
    "/auth/invite/start": PUBLIC,
    "/auth/invite/finish": PUBLIC,
    "/passkeys": MEMBER,
    "/api/passkeys/start": MEMBER,
    "/api/passkeys/finish": MEMBER,
    "/api/passkeys/{passkey_id}/rename": MEMBER,
    "/api/passkeys/{passkey_id}/remove": MEMBER,
    "/api/accounts/{account_id}/invite": ADMIN,
    "/api/accounts/{account_id}/invite/cancel": ADMIN,
}
KEPT = passkeys.TIMEOUT + 60  # seconds a start is kept: the browser's own time, and a moment to send the answer
TOO_MANY = "Too many tries from here just now. Wait a few minutes, then try again."
GONE = ("This link doesn't work any more: it was used, it ran out, or a newer one was made. Ask whoever sent it for "
        "a new one.")


def _keep(s: Site, request: Request, keep: dict, answer: dict) -> JSONResponse:
    keep = {**keep, "challenge": base64.urlsafe_b64encode(keep["challenge"]).decode()}
    response = JSONResponse(answer)
    response.set_cookie(PASSKEY_STARTED, s.seal(keep), max_age=KEPT, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https")
    return response


def _kept(s: Site, request: Request, kind: str) -> dict:
    """What the start kept, from this browser's cookie; the cookie goes with the answer (_used)."""
    keep = s.unseal(request.cookies.get(PASSKEY_STARTED), KEPT)
    if keep is None or keep.get("kind") != kind:
        raise HTTPException(400, "That took too long, or was started on another page. Try again.")
    return {**keep, "challenge": base64.urlsafe_b64decode(keep["challenge"])}


def _used(s: Site, challenge: bytes) -> bool:
    """Whether a passkey signed in with this challenge already; noted now if not. Only answers a passkey signed are
    noted, so nobody fills the list without one."""
    now = time.monotonic()
    for old in [k for k, at in s.passkey_used.items() if now - at > KEPT]:
        s.passkey_used.pop(old, None)
    key = hashlib.sha256(challenge).hexdigest()
    if key in s.passkey_used:
        return True
    s.passkey_used[key] = now
    return False


def _here(s: Site, request: Request) -> None:
    now = s.shared_cfg()
    if not passkeys.here(now, request.url.scheme, request.headers.get("host", "")):
        where = passkeys.origin(now)
        raise HTTPException(400, f"Passkeys work only at {where}: open the photos there." if where else
                                 "Passkeys aren't set up on this site.")


def _signed_in(request: Request) -> dict:
    account = getattr(request.state, "account", None)
    if account is None:
        raise HTTPException(400, "Family accounts are off, so nobody signs in: there is no passkey to add. An admin "
                                 "makes a link for one under Settings → Family accounts.")
    return account


# ---- signing in ----------------------------------------------------------------------------------------------------

@router.post("/auth/passkey/start", dependencies=[Depends(_from_app)])
def sign_in_start(request: Request, body: PasskeyStart, s: Site = Depends(site)):
    now = s.shared_cfg()
    if not now.accounts_enabled:
        raise HTTPException(404)
    _here(s, request)
    if not s.passkey_tries.allow(tries_from(request)):
        raise HTTPException(429, TOO_MANY)
    options, challenge = passkeys.start_signing_in(now)
    return _keep(s, request, {"kind": "sign-in", "challenge": challenge, "next": _local_path(body.next)},
                 {"options": options})


@router.post("/auth/passkey/finish", dependencies=[Depends(_from_app)])
def sign_in_finish(request: Request, body: PasskeyBody, s: Site = Depends(site)):
    now = s.shared_cfg()
    if not now.accounts_enabled:
        raise HTTPException(404)
    keep = _kept(s, request, "sign-in")
    with s.own_con() as c:
        try:
            account_id, passkey_id = passkeys.sign_in(c, body.credential, keep["challenge"], now)
        except passkeys.Problem as e:
            activity.log(c, f"A sign-in with a passkey was turned away: {e}"[:200])
            raise
        if _used(s, keep["challenge"]):  # the same answer sent again
            raise HTTPException(400, "That took too long, or was started on another page. Try again.")
        activity.log(c, f"{accounts.get(c, account_id)['email']} signed in with a passkey")
        response = start_session(s, request, c, account_id, JSONResponse({"go": keep["next"]}), passkey_id)
        response.delete_cookie(PASSKEY_STARTED)
        return response


# ---- an admin's link -----------------------------------------------------------------------------------------------

@router.get("/auth/invite", response_class=HTMLResponse)
def invite_page(request: Request, s: Site = Depends(site)):
    """Where an admin's link leads. Its token is after the #, which never reaches here: the page's script asks whom
    the link is for, then makes the passkey."""
    with s.own_con() as c:
        now = s.live(c)
    return page(request, "passkey_invite.html", problem=passkeys.why_not(now), origin=passkeys.origin(now),
                here=passkeys.here(now, request.url.scheme, request.headers.get("host", "")))


@router.post("/auth/invite/check", dependencies=[Depends(_from_app)])
def invite_check(request: Request, body: InviteBody, s: Site = Depends(site)):
    """Whom a link is for, to say so before the passkey is made: it is made for that account, whoever holds the
    phone."""
    if not s.passkey_tries.allow(tries_from(request)):
        raise HTTPException(429, TOO_MANY)
    with s.own_con() as c:
        row = passkeys.invited(c, body.token)
    if row is None:
        raise HTTPException(404, GONE)
    return {"who": row["person_name"] or row["email"], "email": row["email"]}


@router.post("/auth/invite/start", dependencies=[Depends(_from_app)])
def invite_start(request: Request, body: InviteBody, s: Site = Depends(site)):
    _here(s, request)
    if not s.passkey_tries.allow(tries_from(request)):
        raise HTTPException(429, TOO_MANY)
    with s.own_con() as c:
        row = passkeys.invited(c, body.token)
        if row is None:
            raise HTTPException(404, GONE)
        options, challenge = passkeys.start_adding(c, row["id"], s.live(c))
    return _keep(s, request, {"kind": "invite", "challenge": challenge, "account_id": row["id"], "token": body.token},
                 {"options": options})


@router.post("/auth/invite/finish", dependencies=[Depends(_from_app)])
def invite_finish(request: Request, body: PasskeyBody, s: Site = Depends(site)):
    """The passkey made, the link used up (in one transaction: two pages with one link can't both make one), and its
    owner signed in."""
    keep = _kept(s, request, "invite")
    with s.own_con() as c:
        try:
            if not passkeys.use_invite(c, keep["token"]):
                raise HTTPException(404, GONE)
            made = passkeys.add(c, keep["account_id"], body.credential, keep["challenge"], s.shared_cfg(),
                                passkeys.name_from(request.headers.get("user-agent", "")))
            c.commit()
        except BaseException:
            c.rollback()  # the link works again: nothing was made with it
            raise
        activity.log(c, f"{accounts.get(c, keep['account_id'])['email']} made a passkey with a link")
        return start_session(s, request, c, keep["account_id"], JSONResponse({"go": "/passkeys?made=1"}), made)


# ---- someone's own -------------------------------------------------------------------------------------------------

@router.get("/passkeys", response_class=HTMLResponse)
def passkeys_page(request: Request, made: bool = False, s: Site = Depends(site)):
    """The passkeys of whoever is signed in: adding one on this phone or computer, naming them, taking one away."""
    you = getattr(request.state, "account", None)
    with s.own_con() as c:
        now = s.live(c)
        rows = passkeys.of_account(c, you["id"]) if you else []
    return page(request, "passkeys.html", rows=rows, you=you, made=made, problem=passkeys.why_not(now),
                here=passkeys.here(now, request.url.scheme, request.headers.get("host", "")),
                origin=passkeys.origin(now), domain=passkeys.domain(now),
                wait=passkeys.may_add(you) if you else None, until=passkeys.add_until(you) if you else None,
                minutes=passkeys.RECENT_MINUTES, hours=passkeys.FIRST_HOURS)


@router.post("/api/passkeys/start", dependencies=[Depends(_from_app)])
def add_start(request: Request, s: Site = Depends(site)):
    you = _signed_in(request)
    with s.own_con() as c:
        now = s.live(c)
        if problem := passkeys.why_not(now):
            raise passkeys.Problem(problem)
        _here(s, request)
        if wait := passkeys.may_add(you):
            raise passkeys.Problem(wait)
        options, challenge = passkeys.start_adding(c, you["id"], now)
    return _keep(s, request, {"kind": "add", "challenge": challenge, "account_id": you["id"]}, {"options": options})


@router.post("/api/passkeys/finish", dependencies=[Depends(_from_app)])
def add_finish(request: Request, body: PasskeyBody, s: Site = Depends(site)):
    you = _signed_in(request)
    keep = _kept(s, request, "add")
    if keep["account_id"] != you["id"]:  # started by someone else signed in on this browser since
        raise HTTPException(400, "That was started for someone else. Try again.")
    with s.own_con() as c:
        try:
            passkeys.add(c, you["id"], body.credential, keep["challenge"], s.shared_cfg(),
                         passkeys.name_from(request.headers.get("user-agent", "")))
            c.commit()
        except BaseException:
            c.rollback()
            raise
        activity.log(c, f"{you['email']} added a passkey")
    return {"reload": True}


def _passkey(c, request: Request, v: access.Viewer, passkey_id: int):
    """A passkey of the account signed in; an admin may change anyone's. Others' are not found."""
    row = passkeys.get(c, passkey_id)
    you = getattr(request.state, "account", None)
    if row is None or not (v.admin or (you is not None and row["account_id"] == you["id"])):
        raise HTTPException(404)
    return row


@router.post("/api/passkeys/{passkey_id}/rename", dependencies=[Depends(_from_app)])
def rename(passkey_id: int, body: PasskeyName, request: Request, v: access.Viewer = Depends(viewer),
           s: Site = Depends(site)):
    with s.own_con() as c:
        _passkey(c, request, v, passkey_id)
        passkeys.rename(c, passkey_id, body.name)
    return {"reload": True}


@router.post("/api/passkeys/{passkey_id}/remove", dependencies=[Depends(_from_app)])
def remove(passkey_id: int, request: Request, v: access.Viewer = Depends(viewer), s: Site = Depends(site)):
    """It no longer signs in, and the sessions started with it end: the phone it is on is signed out."""
    with s.own_con() as c:
        row = _passkey(c, request, v, passkey_id)
        passkeys.remove(c, passkey_id)
        activity.log(c, f"A passkey of {accounts.get(c, row['account_id'])['email']} was taken away ({row['name']})")
    return {"reload": True}


# ---- admins' links -------------------------------------------------------------------------------------------------

@router.post("/api/accounts/{account_id}/invite", dependencies=[Depends(_from_app)])
def make_invite(account_id: int, request: Request, s: Site = Depends(site)):
    """A link for making this account's passkey (the one before it stops working), shown this once: to send, or
    as a QR code for their phone's camera."""
    you = getattr(request.state, "account", None)
    with s.own_con() as c:
        now = s.live(c)  # as the Settings pages show it, not as kept a moment
        target = accounts.get(c, account_id)
        if target is not None and (target["role"] == "admin" or (you and you["id"] == account_id)):
            _recently_signed_in(request, "make a passkey link for yourself or an admin")
        token, until = passkeys.make_invite(c, account_id, you["id"] if you else None, now)
        row = accounts.get(c, account_id)
        activity.log(c, f"A link for making a passkey was made for {row['email']}")
    link = passkeys.invite_link(now, token)
    return {"html": s.templates.get_template("_passkey_link.html").render(
        account_id=account_id, email=row["email"], link=link, qr=accounts.setup_qr(link), until=until,
        admin=row["role"] == "admin")}


@router.post("/api/accounts/{account_id}/invite/cancel", dependencies=[Depends(_from_app)])
def cancel_invite(account_id: int, s: Site = Depends(site)):
    with s.own_con() as c:
        passkeys.cancel_invite(c, account_id)
    return {"reload": True}
