"""Family members who sign in: their accounts, sessions, Sign in with Google, and admins' codes for signing in at home
when the internet is down.

An account is an email address tied to someone in the family tree, as an admin or a member. Admins see and do
everything; a member sees their own part of the family (access.py). Nobody signs themselves up: an admin adds the
email, and Google says who is signing in, or a passkey made with the admin's link does (passkeys.py).

Sign in with Google is OpenID Connect's code flow with PKCE. The code is swapped for the ID token directly with Google
over HTTPS, so the token can be trusted without checking its signature (OIDC Core 3.1.3.7, item 6): only its issuer,
audience, expiry, nonce and verified email are checked, and no JWT library is needed.

A session is a random token in a cookie; the catalog keeps only its SHA-256, so a stolen copy of the catalog can't
use anyone's session. It does hold the Google client secret and the admins' code secrets, though, so backups of the
catalog must be kept as private as the photos. The local sign-in code is TOTP (RFC 6238: 6 digits, 30 seconds, as Google Authenticator makes them), for admins
only and only from the home network.
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import struct
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

import httpx
import segno

from lineagebox import errors
from lineagebox.catalog import under

ROLES = ("admin", "member")
COOKIE = "lineagebox_session"
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
TOUCH_EVERY = 600  # seconds between writing a session's last use: each page asks for many thumbnails
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Problem(errors.Problem):
    """Something that keeps an account from being added or someone from signing in; the message is shown."""


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


# ---- accounts ------------------------------------------------------------------------------------------------------

def add(con: sqlite3.Connection, email: str, person_id: int | None, role: str = "member") -> int:
    email = email.strip()
    if not _EMAIL.match(email):
        raise Problem("Type the email address they sign in to Google with.")
    if role not in ROLES:
        raise Problem("An account is an admin's or a member's.")
    if person_id is None and role == "member":
        raise Problem("Pick who they are in the family tree: a member sees their own part of it.")
    if person_id is not None and not con.execute("SELECT 1 FROM person WHERE id = ?", (person_id,)).fetchone():
        raise Problem("No such person.")
    if con.execute("SELECT 1 FROM account WHERE email = ?", (email,)).fetchone():
        raise Problem(f"{email} already has an account.")
    return con.execute("INSERT INTO account(email, person_id, role, created_at) VALUES (?, ?, ?, ?)",
                       (email, person_id, role, _now().isoformat())).lastrowid


def update(con: sqlite3.Connection, account_id: int, *, role: str | None = None, person_id: int | None = None,
           disabled: bool | None = None) -> None:
    row = get(con, account_id)
    if row is None:
        raise Problem("No such account.")
    if role is not None:
        if role not in ROLES:
            raise Problem("An account is an admin's or a member's.")
        if row["role"] == "admin" and role != "admin":
            _not_the_last_admin(con, account_id)
        con.execute("UPDATE account SET role = ? WHERE id = ?", (role, account_id))
    if person_id is not None:
        if not con.execute("SELECT 1 FROM person WHERE id = ?", (person_id,)).fetchone():
            raise Problem("No such person.")
        con.execute("UPDATE account SET person_id = ? WHERE id = ?", (person_id, account_id))
    if disabled is not None:
        if disabled and row["role"] == "admin":
            _not_the_last_admin(con, account_id)
        con.execute("UPDATE account SET disabled_at = ? WHERE id = ?",
                    (_now().isoformat() if disabled else None, account_id))
        if disabled:
            end_sessions(con, account_id)


def remove(con: sqlite3.Connection, account_id: int) -> None:
    row = get(con, account_id)
    if row is not None and row["role"] == "admin":
        _not_the_last_admin(con, account_id)
    con.execute("DELETE FROM account WHERE id = ?", (account_id,))  # sessions, uploads' records and rules go with it


def _not_the_last_admin(con: sqlite3.Connection, account_id: int) -> None:
    """Whether sign-in is on now or later (a setting, config.toml or LINEAGEBOX_ACCOUNTS_ENABLED), there is always an
    admin to change the settings."""
    others = con.execute("""SELECT count(*) FROM account WHERE role = 'admin' AND disabled_at IS NULL AND id != ?""",
                         (account_id,)).fetchone()[0]
    if not others:
        raise Problem("This is the only admin: add another admin first, or nobody could change the settings.")


def get(con: sqlite3.Connection, account_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM account WHERE id = ?", (account_id,)).fetchone()


def by_email(con: sqlite3.Connection, email: str) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM account WHERE email = ?", (email.strip(),)).fetchone()


def everyone(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every account with the name of their person, admins first."""
    return con.execute("""SELECT a.*, p.name AS person_name,
                                 (SELECT count(*) FROM session s WHERE s.account_id = a.id
                                  AND s.expires_at > :now) AS sessions
                          FROM account a LEFT JOIN person p ON p.id = a.person_id
                          ORDER BY a.role = 'admin' DESC, p.name COLLATE NOCASE, a.email""",
                       {"now": _now().isoformat()}).fetchall()


def admins_exist(con: sqlite3.Connection) -> bool:
    return con.execute("SELECT 1 FROM account WHERE role = 'admin' AND disabled_at IS NULL").fetchone() is not None


# ---- sessions ------------------------------------------------------------------------------------------------------

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(con: sqlite3.Connection, account_id: int, days: int, ip: str = "", user_agent: str = "",
                passkey_id: int | None = None) -> str:
    """Sign someone in: returns the token for their cookie. passkey_id: the passkey they signed in with, whose
    removal ends this session. The account's first sign-in ever is marked (passkeys.may_add)."""
    token = secrets.token_urlsafe(32)
    now = _now()
    first = con.execute("SELECT last_seen_at IS NULL FROM account WHERE id = ?", (account_id,)).fetchone()
    con.execute("""INSERT INTO session(token_hash, account_id, created_at, last_seen_at, expires_at, ip, user_agent,
                                       passkey_id, first_sign_in)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (_hash(token), account_id, now.isoformat(), now.isoformat(),
                 (now + timedelta(days=days)).isoformat(), ip[:64], user_agent[:300], passkey_id,
                 int(bool(first and first[0]))))
    con.execute("UPDATE account SET last_seen_at = ? WHERE id = ?", (now.isoformat(), account_id))
    con.commit()
    return token


_touched: dict[str, float] = {}  # token hash -> when its last use was written (monotonic)


def session_account(con: sqlite3.Connection, token: str | None, days: int) -> sqlite3.Row | None:
    """The account a session cookie signs in, while the session lasts (days from its last use) and the account is not
    disabled; None otherwise."""
    if not token or len(token) > 100:
        return None
    h = _hash(token)
    row = con.execute("""SELECT a.*, s.last_seen_at AS session_seen, s.created_at AS session_made,
                                s.first_sign_in AS session_first, s.passkey_id AS session_passkey,
                                p.name AS person_name
                         FROM session s JOIN account a ON a.id = s.account_id LEFT JOIN person p ON p.id = a.person_id
                         WHERE s.token_hash = ? AND s.expires_at > ? AND a.disabled_at IS NULL""",
                      (h, _now().isoformat())).fetchone()
    if row is None:
        return None
    if time.monotonic() - _touched.get(h, -1e9) > TOUCH_EVERY:
        _touched[h] = time.monotonic()
        now = _now()
        try:
            con.execute("UPDATE session SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
                        (now.isoformat(), (now + timedelta(days=days)).isoformat(), h))
            con.execute("UPDATE account SET last_seen_at = ? WHERE id = ?", (now.isoformat(), row["id"]))
            con.commit()
        except sqlite3.OperationalError:  # busy: the next page writes it
            con.rollback()
            _touched.pop(h, None)
    return row


def end_session(con: sqlite3.Connection, token: str | None) -> None:
    if token:
        con.execute("DELETE FROM session WHERE token_hash = ?", (_hash(token),))
        con.commit()


def end_sessions(con: sqlite3.Connection, account_id: int) -> int:
    n = con.execute("DELETE FROM session WHERE account_id = ?", (account_id,)).rowcount
    con.commit()
    return n


# ---- Sign in with Google -------------------------------------------------------------------------------------------

def google_start(client_id: str, redirect_uri: str) -> tuple[str, dict]:
    """Where to send the browser to sign in, and what to keep until it comes back (state, nonce, PKCE verifier)."""
    keep = {"state": secrets.token_urlsafe(24), "nonce": secrets.token_urlsafe(24),
            "verifier": secrets.token_urlsafe(48)}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(keep["verifier"].encode()).digest()).rstrip(b"=").decode()
    query = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
             "scope": "openid email profile", "state": keep["state"], "nonce": keep["nonce"],
             "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account"}
    return f"{GOOGLE_AUTH}?{urlencode(query, quote_via=quote)}", keep


class Refused(Problem):
    """Google refused the code exchange. why: its error code (invalid_client...), which admins are shown."""

    def __init__(self, why: str, message: str):
        super().__init__(message)
        self.why = why


# What Google's token endpoint answers for a sign-in it refuses, and what to do about each
_GOOGLE_SAYS = {
    "invalid_client": "Google doesn't know the client ID and secret on Settings → Family accounts: an admin should "
                      "check them against Google Cloud.",
    "unauthorized_client": "Google doesn't know the client ID and secret on Settings → Family accounts: an admin "
                           "should check them against Google Cloud.",
    "redirect_uri_mismatch": "Google Cloud doesn't list this site's address for signing in: an admin should add "
                             "{redirect_uri} to the client's authorised redirect URIs.",
    "invalid_grant": "That sign-in took too long, or was finished already. Try again from the start.",
}


def google_finish(code: str, keep: dict, client_id: str, client_secret: str, redirect_uri: str,
                  http: httpx.Client | None = None) -> str:
    """Swap the code Google sent back for who signed in. Returns their email, as Google has verified it."""
    http = http or httpx.Client(timeout=20)
    try:
        r = http.post(GOOGLE_TOKEN, data={"code": code, "client_id": client_id, "client_secret": client_secret,
                                          "redirect_uri": redirect_uri, "grant_type": "authorization_code",
                                          "code_verifier": keep["verifier"]})
    except httpx.HTTPError as e:
        raise Problem(f"Google could not be reached ({e}). Try again in a moment.") from None
    if r.status_code != 200:
        try:
            refused = str(r.json().get("error") or "")
        except ValueError:
            refused = ""
        raise Refused(refused, _GOOGLE_SAYS.get(refused, "Google did not accept the sign-in. Try again from the "
                                                         "start.").format(redirect_uri=redirect_uri))
    try:
        claims = json.loads(_b64(r.json()["id_token"].split(".")[1]))
    except (KeyError, IndexError, ValueError):
        raise Problem("Google's answer could not be read. Try again from the start.") from None
    if claims.get("iss") not in GOOGLE_ISSUERS or claims.get("aud") != client_id:
        raise Problem("That sign-in was not meant for this site.")
    if float(claims.get("exp", 0)) < time.time():
        raise Problem("That sign-in took too long. Try again.")
    if not hmac.compare_digest(str(claims.get("nonce", "")), keep["nonce"]):
        raise Problem("That sign-in was not the one started here. Try again from the start.")
    if not claims.get("email") or claims.get("email_verified") not in (True, "true"):
        raise Problem("Google has not verified that email address.")
    return claims["email"]


def _b64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ---- local sign-in codes (TOTP) ------------------------------------------------------------------------------------

STEP = 30


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode()


def code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret.replace(" ", "").upper() + "=" * (-len(secret.replace(" ", "")) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    return f"{(struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def code_step(secret: str, code: str, at: float | None = None) -> int | None:
    """Which 30 seconds a code from the authenticator app is for: now, or a step early or late (clocks drift). None
    when it is no code of this secret's."""
    code = re.sub(r"\s", "", code)
    if not re.fullmatch(r"\d{6}", code or ""):
        return None
    now = int((time.time() if at is None else at) // STEP)
    for step in (now, now - 1, now + 1):
        if hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def check_code(con: sqlite3.Connection, account_id: int, secret: str, code: str, at: float | None = None) -> bool:
    """A code from the authenticator app for signing in: each works once, also after a restart. The step it was for
    is kept on the account, and taken in one statement, so two requests with one code can't both get in."""
    step = code_step(secret, code, at)
    if step is None:
        return False
    taken = con.execute("UPDATE account SET code_step = ? WHERE id = ? AND (code_step IS NULL OR code_step < ?)",
                        (step, account_id, step)).rowcount
    con.commit()
    return taken == 1


def setup_uri(secret: str, email: str, issuer: str = "LineageBox") -> str:
    """otpauth:// link for authenticator apps (also what their QR codes hold)."""
    return (f"otpauth://totp/{quote(issuer)}:{quote(email)}?"
            + urlencode({"secret": secret, "issuer": issuer, "digits": 6, "period": STEP}))


def setup_qr(uri: str) -> str:
    """The setup link as a QR code for the app to scan: an SVG to put in a page. Dark on white whatever the page's
    colours, as the apps' cameras expect."""
    return segno.make(uri, error="m").svg_inline(scale=5, border=4, dark="#000", light="#fff")


def set_local_secret(con: sqlite3.Connection, account_id: int) -> str:
    row = get(con, account_id)
    if row is None or row["role"] != "admin":
        raise Problem("Only admins sign in with a code.")
    secret = new_secret()
    con.execute("UPDATE account SET local_secret = ?, code_step = NULL WHERE id = ?", (secret, account_id))
    con.commit()
    return secret


def clear_local_secret(con: sqlite3.Connection, account_id: int) -> None:
    """No more signing in with a code (a phone lost): until a new one is made."""
    con.execute("UPDATE account SET local_secret = NULL, code_step = NULL WHERE id = ?", (account_id,))
    con.commit()


# ---- rate limits ---------------------------------------------------------------------------------------------------

class Limiter:
    """At most `most` tries per key in `seconds` (sign-ins per address, costly actions per account), in memory."""

    def __init__(self, most: int, seconds: float):
        self.most, self.seconds, self.tries, self.lock = most, seconds, defaultdict(deque), threading.Lock()
        self.swept = time.monotonic()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            if now - self.swept > self.seconds:  # forget keys with no recent tries: addresses come and go
                for old in [k for k, t in self.tries.items() if not t or now - t[-1] > self.seconds]:
                    del self.tries[old]
                self.swept = now
            tries = self.tries[key]
            while tries and now - tries[0] > self.seconds:
                tries.popleft()
            if len(tries) >= self.most:
                return False
            tries.append(now)
            return True


# ---- uploads -------------------------------------------------------------------------------------------------------

def upload_folder(con: sqlite3.Connection, account: sqlite3.Row, root: str, watched) -> str:
    """The account's uploads folder ('/' separated, from the share): kept on the account once given, so merging or
    renaming people never moves it, as long as it is inside one of the watched folders. A new one is
    <root>/<their person id>, or <root>/<person id>-<account id> when another account has that one already (a person
    can have two accounts, or an account be tied to someone else after it uploaded). One that is no longer watched
    goes under root with the same name: the photos uploaded before stay the account's (claim_uploads)."""
    folder = account["upload_folder"]
    if folder and any(folder == w or folder.startswith(w + "/") for w in watched):
        return folder
    if folder:
        name = folder.rpartition("/")[2]
    elif account["person_id"] is None:
        raise Problem("This account is not tied to anyone in the family tree yet.")
    else:
        name = str(account["person_id"])
    folder = f"{root}/{name}"
    if con.execute("SELECT 1 FROM account WHERE upload_folder = ? AND id != ?", (folder, account["id"])).fetchone():
        folder = f"{root}/{name}-{account['id']}"
    con.execute("UPDATE account SET upload_folder = ? WHERE id = ?", (folder, account["id"]))
    con.commit()
    return folder


def claim_uploads(con: sqlite3.Connection) -> int:
    """Record the photos read from each account's uploads folder as theirs, so they stay theirs if the files are moved.
    Returns how many were new."""
    n = 0
    for account_id, folder in con.execute("SELECT id, upload_folder FROM account WHERE upload_folder IS NOT NULL"):
        pattern = under(folder)
        n += con.execute("""INSERT OR IGNORE INTO upload(image_id, account_id, uploaded_at, original_name)
                            SELECT f.image_id, ?, ?, min(f.path) FROM file f JOIN image i ON i.id = f.image_id
                            WHERE f.path LIKE ? ESCAPE '!' AND NOT f.missing AND i.error IS NULL
                            GROUP BY f.image_id""",
                         (account_id, _now().isoformat(), pattern)).rowcount
    con.commit()
    return n
