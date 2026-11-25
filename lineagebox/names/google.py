"""Sign in with Google for the free addresses' servers, through one Google client that the service holds, so that no
family has to set one up (Family accounts → Sign in with Google → Through ourplace.here).

1. A family's server (maple-river.ourplace.here) sends the browser to /v1/google/start with its name, a random value
   r it keeps in the browser's own sign-in cookie, and its state.
2. The service sends the browser on to Google, with a nonce that is the SHA-256 of the name and r together
   (google_nonce): Google signs that nonce into the ID token it makes.
3. Google sends the browser back to /v1/google/callback here; the code is swapped for the ID token (with the client's
   secret, which only the service has, and PKCE), and the browser goes on to that same name only, with the token after
   a # (never sent to any server, nor logged): https://maple-river.ourplace.here/auth/google/relay#id_token=...
4. The family's server checks Google's signature on the token, that it is for this client, and that its nonce is the
   one for its own name and the r in this browser's cookie (accounts.google_token).

So the service can't make up a sign-in (it can't sign as Google), and a token made for one family's server is refused by
every other, including one whose holder starts a sign-in and passes its link to someone else (the nonce is the name's).
Nothing about who signed in is kept here: the token passes through.
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
KEPT = 600                                  # seconds to sign in at Google and come back
RANDOM = re.compile(r"[A-Za-z0-9_-]{43}")   # r: 32 random bytes, base64url
STATE = re.compile(r"[A-Za-z0-9_-]{16,64}")
NAME = re.compile(r"[a-z]+-[a-z]+(?:-\d{2})?")  # maple-river; maple-river-42 as given until 2026-11-24
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def google_nonce(host: str, r: str) -> str:
    """The nonce Google signs into the ID token: one name's, for one sign-in (accounts.py computes it the same way)."""
    return b64(hashlib.sha256(f"{host}\n{r}".encode()).digest())


def add(app, store, domain: str, client_id: str, secret: str, ports, http: httpx.Client | None = None) -> None:
    """The two routes, on the service's app. Without a client (client_id ""), nothing is added and /v1/ says none."""
    if not (client_id and secret):
        return
    http = http or httpx.Client(timeout=20)
    callback = f"https://{domain}/v1/google/callback"
    with store.lock:
        row = store.con.execute("SELECT value FROM meta WHERE key = 'google_key'").fetchone()
        if row is None:
            store.con.execute("INSERT INTO meta VALUES ('google_key', ?)", (secrets.token_hex(32),))
            row = store.con.execute("SELECT value FROM meta WHERE key = 'google_key'").fetchone()
    key = row["value"].encode()

    def seal(data: dict) -> str:
        body = b64(json.dumps({**data, "t": time.time()}).encode())
        return f"{body}.{b64(hmac.new(key, body.encode(), hashlib.sha256).digest())}"

    def unseal(value: str) -> dict | None:
        body, _, mac = (value or "").partition(".")
        if not body or not hmac.compare_digest(mac, b64(hmac.new(key, body.encode(), hashlib.sha256).digest())):
            return None
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return data if time.time() - data.get("t", 0) < KEPT else None

    def active(name: str):
        rows = store.run("SELECT * FROM name WHERE name = ?", (name,)) if NAME.fullmatch(name or "") else []
        return rows[0] if rows and not rows[0]["released_at"] and not rows[0]["disabled"] else None

    def plain(message: str, status: int = 400) -> HTMLResponse:
        page = (f"<!doctype html><meta charset=utf-8><title>Sign in</title><link rel=stylesheet href=/site.css>"
                f"<main><p class=brand>ourplace.here</p><h1>That sign-in didn't work</h1><p>{message}</p></main>")
        return HTMLResponse(page, status, headers=NO_STORE)

    @app.get("/v1/google/start")
    def start(name: str = "", r: str = "", state: str = "", port: int = 443):
        if active(name) is None:
            raise HTTPException(404, "There is no such address.")
        if not (RANDOM.fullmatch(r) and STATE.fullmatch(state)) or port not in ports:
            raise HTTPException(400, "That isn't a sign-in a LineageBox started.")
        verifier = secrets.token_urlsafe(48)
        query = {"client_id": client_id, "redirect_uri": callback, "response_type": "code",
                 "scope": "openid email profile", "state": seal({"n": name, "s": state, "v": verifier, "p": port}),
                 "nonce": google_nonce(f"{name}.{domain}", r),
                 "code_challenge": b64(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256",
                 "prompt": "select_account"}  # which account, asked every time: never a sign-in nobody chose
        return RedirectResponse(f"{AUTH}?{urlencode(query)}", 303, headers=NO_STORE)

    @app.get("/v1/google/callback")
    def back(code: str = "", state: str = "", error: str = ""):
        kept = unseal(state)
        if kept is None or active(kept["n"]) is None:
            return plain("It took too long, or wasn't started from a family's LineageBox. Go back to its sign-in "
                         "page and try again.")
        home = f"https://{kept['n']}.{domain}" + ("" if kept["p"] == 443 else f":{kept['p']}")

        def to_server(**answer):  # after the #: the browser keeps it, no server sees it
            return RedirectResponse(f"{home}/auth/google/relay#{urlencode({**answer, 'state': kept['s']})}", 303,
                                    headers=NO_STORE)
        if error or not code:
            return to_server(error=error or "no_code")
        try:
            r = http.post(TOKEN, data={"code": code, "client_id": client_id, "client_secret": secret,
                                       "redirect_uri": callback, "grant_type": "authorization_code",
                                       "code_verifier": kept["v"]})
            token = r.json().get("id_token") if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            token = None
        return to_server(id_token=token) if token else to_server(error="google_refused")
