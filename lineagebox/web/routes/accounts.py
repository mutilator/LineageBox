"""Settings → Family accounts and Codes (admins): accounts, their rules, sign-in settings, seeing the site as a
member. The links for making passkeys are in routes/passkeys.py."""
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from lineagebox import accounts, passkeys, settings
from lineagebox.web.common import (ADMIN, MEMBER, SEE_AS, AccountBody, AccountChange, CodeBody, MeBody, RuleBody,
                                   SeeAsBody, _from_app, _person_named, _recently_signed_in, _redirect_uri,
                                   _uploads_root)
from lineagebox.web.deps import con, page, site
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/accounts": ADMIN,
    "/api/accounts": ADMIN,
    "/api/accounts/{account_id}": ADMIN,
    "/api/accounts/{account_id}/delete": ADMIN,
    "/api/accounts/{account_id}/signout": ADMIN,
    "/settings/codes": ADMIN,
    "/api/accounts/{account_id}/local-code": ADMIN,
    "/api/accounts/{account_id}/local-code/check": ADMIN,
    "/api/accounts/{account_id}/local-code/remove": ADMIN,
    "/api/accounts/{account_id}/rules": ADMIN,
    "/api/accounts/{account_id}/rules/remove": ADMIN,
    "/api/settings/accounts": ADMIN,
    "/api/see-as": ADMIN,
    "/api/see-as/stop": MEMBER,
}


@router.get("/accounts", response_class=HTMLResponse)
def accounts_page(request: Request, c=Depends(con), s: Site = Depends(site)):
    now = s.live(c)
    rules = defaultdict(list)
    for r in c.execute("""SELECT r.*, p.name FROM access_rule r JOIN person p ON p.id = r.person_id
                          ORDER BY p.name COLLATE NOCASE"""):
        rules[r["account_id"]].append(r)
    rows = accounts.everyone(c)
    return page(request, "accounts.html", nav="settings", rows=rows, rules=rules, cfg=now, roles=accounts.ROLES,
                redirect_uri=_redirect_uri(request, now), you=getattr(request.state, "account", None),
                uploads_root=_uploads_root(now), keys=passkeys.by_account(c), links=passkeys.invites(c),
                passkey_problem=passkeys.why_not(now), passkey_domain=passkeys.domain(now),
                invite_days=passkeys.INVITE_DAYS, admin_hours=passkeys.ADMIN_INVITE_HOURS)


@router.post("/api/accounts", dependencies=[Depends(_from_app)])
def add_account(request: Request, body: AccountBody, c=Depends(con)):
    if body.role == "admin":
        _recently_signed_in(request, "add an admin")
    pid = _person_named(c, body.name, body.name_id) if body.name.strip() else None
    accounts.add(c, body.email, pid, body.role)
    c.commit()
    return {"reload": True}


@router.post("/api/accounts/{account_id}", dependencies=[Depends(_from_app)])
def change_account(account_id: int, request: Request, body: AccountChange, c=Depends(con)):
    if body.role == "admin" and (row := accounts.get(c, account_id)) is not None and row["role"] != "admin":
        _recently_signed_in(request, "make someone an admin")
    pid = _person_named(c, body.name, body.name_id) if body.name.strip() else None
    accounts.update(c, account_id, role=body.role or None, person_id=pid,
                    disabled=body.disabled)
    c.commit()
    return {"reload": True}


@router.post("/api/accounts/{account_id}/delete", dependencies=[Depends(_from_app)])
def delete_account(account_id: int, c=Depends(con)):
    accounts.remove(c, account_id)
    c.commit()
    return {"reload": True}


@router.post("/api/accounts/{account_id}/signout", dependencies=[Depends(_from_app)])
def sign_account_out(account_id: int, c=Depends(con)):
    n = accounts.end_sessions(c, account_id)
    return {"message": f"Signed out of {n} browser{'' if n == 1 else 's'}."}


@router.get("/settings/codes", response_class=HTMLResponse)
def codes_page(request: Request, c=Depends(con), s: Site = Depends(site)):
    """Admins' authenticator apps, for signing in at home when Google can't be reached. Whoever is looking first."""
    you = getattr(request.state, "account", None)
    admins = sorted((a for a in accounts.everyone(c) if a["role"] == "admin"),
                    key=lambda a: you is None or a["id"] != you["id"])
    return page(request, "codes.html", nav="settings", admins=admins, you=you, cfg=s.live(c))


@router.post("/api/accounts/{account_id}/local-code", dependencies=[Depends(_from_app)])
def local_code(account_id: int, request: Request, c=Depends(con), s: Site = Depends(site)):
    """A new secret for an admin's authenticator app (the old one stops working), shown this once: a QR code to
    scan, a link that opens the app on a phone, and the key to type."""
    _recently_signed_in(request, "make a code for signing in")
    secret = accounts.set_local_secret(c, account_id)
    email = accounts.get(c, account_id)["email"]
    uri = accounts.setup_uri(secret, email, "LineageBox")
    return {"html": s.templates.get_template("_code.html").render(
        account_id=account_id, email=email, uri=uri, qr=accounts.setup_qr(uri),
        key=" ".join(secret[i:i + 4] for i in range(0, len(secret), 4)))}


@router.post("/api/accounts/{account_id}/local-code/check", dependencies=[Depends(_from_app)])
def check_local_code(account_id: int, body: CodeBody, c=Depends(con)):
    """Whether the app makes this account's codes, without using one up for signing in."""
    row = accounts.get(c, account_id)
    if row is None or not row["local_secret"]:
        raise HTTPException(404)
    if accounts.code_step(row["local_secret"], body.code) is None:
        return {"message": "That isn't the code for now. Type the one the app shows, before it changes."}
    return {"message": "That's the one: the app is set up. Keep the key somewhere safe too, in case the phone "
                       "is lost."}


@router.post("/api/accounts/{account_id}/local-code/remove", dependencies=[Depends(_from_app)])
def remove_local_code(account_id: int, c=Depends(con)):
    """No more signing in with this account's code (a phone lost), until a new one is made."""
    if accounts.get(c, account_id) is None:
        raise HTTPException(404)
    accounts.clear_local_secret(c, account_id)
    return {"reload": True}


@router.post("/api/accounts/{account_id}/rules", dependencies=[Depends(_from_app)])
def add_rule(account_id: int, body: RuleBody, c=Depends(con)):
    if accounts.get(c, account_id) is None:
        raise HTTPException(404, "No such account")
    pid = _person_named(c, body.name, body.name_id)
    c.execute("INSERT OR REPLACE INTO access_rule(account_id, person_id, show, branch) VALUES (?, ?, ?, ?)",
              (account_id, pid, int(body.show), int(body.branch)))
    c.commit()
    return {"reload": True}


@router.post("/api/accounts/{account_id}/rules/remove", dependencies=[Depends(_from_app)])
def remove_rule(account_id: int, body: MeBody, c=Depends(con)):
    c.execute("DELETE FROM access_rule WHERE account_id = ? AND person_id = ?", (account_id, body.person_id))
    c.commit()
    return {"reload": True}


@router.post("/api/settings/accounts", dependencies=[Depends(_from_app)])
def save_account_settings(body: dict, c=Depends(con), s: Site = Depends(site)):
    """Sign-in and uploads. The client secret is kept when the box is left empty (it is never shown). A new address
    from outside is a new name for passkeys: those made for the old one are said to stop working."""
    keys = ("accounts_enabled", "google_client_id", "google_client_secret", "public_url", "session_days",
            "uploads_folder", "uploads_into", "upload_max_gb")
    changes = {k: body[k] for k in keys if k in body}
    if not str(changes.get("google_client_secret", "")).strip():
        changes.pop("google_client_secret", None)

    def save():
        now = s.live(c)
        turning_on = settings._switch(changes.get("accounts_enabled", now.accounts_enabled), now)
        google = (changes.get("google_client_id", now.google_client_id)
                  and changes.get("google_client_secret", now.google_client_secret))
        if turning_on and not now.accounts_enabled and not google and not passkeys.admin_has_one(c) and not c.execute(
                "SELECT 1 FROM account WHERE role = 'admin' AND local_secret IS NOT NULL").fetchone():
            raise settings.Invalid("Set up Sign in with Google, an admin's passkey, or an admin's code for signing in "
                                   "at home, before turning sign-in on: otherwise nobody could get in.")
        settings.save(c, s.cfg, changes)
        return passkeys.domain(now), passkeys.domain(s.live(c))
    was, now_named = save()
    s.shared["at"] = -1e9
    left = c.execute("SELECT count(*) FROM passkey WHERE rp_id = ?", (was,)).fetchone()[0] if was != now_named else 0
    if left:
        return {"message": f"Saved. The {left} passkey{'s' if left > 1 else ''} made for {was} won't work on "
                           f"{now_named or 'another address'}: each person makes a new one there."}  # not reloaded
    return {"message": "Saved.", "reload": True}


@router.post("/api/see-as", dependencies=[Depends(_from_app)])
def see_as(request: Request, body: SeeAsBody, c=Depends(con)):
    """See the site as a member sees it, until "Stop"; nothing can be changed meanwhile."""
    other = accounts.get(c, body.account_id)
    if other is None or other["role"] != "member":
        raise HTTPException(404, "No such member")
    response = JSONResponse({"go": "/"})
    response.set_cookie(SEE_AS, str(body.account_id), httponly=True, samesite="lax",
                        secure=request.url.scheme == "https")
    return response


@router.post("/api/see-as/stop", dependencies=[Depends(_from_app)])
def stop_seeing_as(request: Request):
    response = JSONResponse({"go": "/accounts"})
    response.delete_cookie(SEE_AS)
    return response
