"""Passkeys: signing in without Google, with the lock of a phone or computer (its fingerprint, face or PIN).

A passkey is a key pair a phone or computer (or a password manager) makes for this site and keeps; only its public key
comes here. To sign in, it signs a challenge from here once the person has unlocked it, and the signature is checked
against the key kept. That is WebAuthn, through the webauthn package, which checks each answer from the browser: the
challenge, the site's address and name, that the person unlocked it, and the signature. The browser offers the passkeys
it has for this site, so nobody types an email ("discoverable" passkeys), and the passkey alone signs someone in, so
the person must always have unlocked it (user verification required).

Nobody makes a passkey for an account they aren't signed in to, except with an admin's link (make_invite): it works
once, for INVITE_DAYS (for ADMIN_INVITE_HOURS when the account is an admin's, which opens everything). Someone signed
in adds one within RECENT_MINUTES of signing in, or FIRST_HOURS of their first sign-in ever (may_add): someone who
finds a phone left signed in can't add a passkey of their own and so keep a way in.

A passkey belongs to one name, the host of the address from outside (public_url), and works only on pages served by
that name over HTTPS; browsers refuse it anywhere else. So passkeys are only set up while that name is set, HTTPS is
on and the name is listed, exactly, under Host names (why_not). Changing the address later leaves the passkeys made for
the old name unusable: everyone makes new ones.
"""
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from webauthn import (generate_authentication_options, generate_registration_options, verify_authentication_response,
                      verify_registration_response)
from webauthn.helpers import options_to_json_dict, parse_authentication_credential_json
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, AuthenticatorTransport,
                                      PublicKeyCredentialDescriptor, ResidentKeyRequirement,
                                      UserVerificationRequirement)

from lineagebox import errors, https
from lineagebox.config import Config

RP_NAME = "Family photos"  # what a phone may show beside the site's name when it offers the passkey
INVITE_DAYS = 7
ADMIN_INVITE_HOURS = 24
RECENT_MINUTES = 15
FIRST_HOURS = 3
TIMEOUT = 300  # seconds the browser gives the person to unlock the phone or computer; a start is kept as long
NOT_CHECKED = "That passkey couldn't be checked. Try again."


class Problem(errors.Problem):
    """Why a passkey can't be made or used; the message is shown."""


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


# ---- where passkeys work -------------------------------------------------------------------------------------------

def domain(cfg: Config) -> str:
    """The name passkeys are made for (WebAuthn's relying party ID): the host of the address from outside."""
    return (urlsplit(cfg.public_url).hostname or "").lower() if cfg.public_url else ""


def origin(cfg: Config) -> str:
    """The address the browser says it signed on: https://, the name, and the port unless it is 443."""
    parts = urlsplit(cfg.public_url)
    port = f":{parts.port}" if parts.port and parts.port != 443 else ""
    return f"https://{domain(cfg)}{port}" if domain(cfg) else ""


def _is_address(name: str) -> bool:
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def why_not(cfg: Config) -> str | None:
    """Why passkeys can't be set up (made) now, in words for the Settings pages; None when they can."""
    name = domain(cfg)
    if not name:
        return ("Passkeys need the address the family opens the photos by: set Address from outside under "
                "Settings → Family accounts → Sign-in, like https://photos.example.com.")
    if _is_address(name):
        return (f"Passkeys need a name, such as photos.example.com, not an address like {name}: browsers only make "
                "passkeys for names. Set one as the Address from outside.")
    if not (cfg.tls_cert or cfg.trusted_proxies):
        return (f"Passkeys need HTTPS, and LineageBox is served over plain HTTP. Turn HTTPS on under Settings → "
                f"Address and HTTPS: a certificate for {name}, or a reverse proxy that does HTTPS in front of "
                "LineageBox.")
    if cfg.tls_cert and not cfg.trusted_proxies:
        cert = https.about(cfg)
        if cert is None:
            return ("Passkeys need HTTPS, and the HTTPS certificate set under Settings → Address and HTTPS can't be "
                    "read.")
        if cert.expired:
            return "Passkeys need HTTPS that browsers trust, and the HTTPS certificate has expired."
        if not https.covers(cert.names, name):
            return (f"Passkeys need HTTPS that browsers trust for {name}, and the HTTPS certificate is not for it "
                    f"({', '.join(cert.names) or 'no names'}).")
    wild = [h for h in cfg.allowed_hosts if "*" in h]
    if wild:
        return (f"Host names lists {wild[0]}, a name with *. Passkeys belong to one exact name, so they can't be "
                f"set up while one is listed: list each name itself instead, such as {name}.")
    if name not in cfg.allowed_hosts:
        return (f"List {name} under Host names (Settings → Address and HTTPS): passkeys are made for that one name.")
    return None


def here(cfg: Config, scheme: str, host_header: str) -> bool:
    """Whether a page was opened where passkeys work: over HTTPS, by their name (the browser refuses them anywhere
    else)."""
    host = host_header.strip().lower().split(":")[0].rstrip(".")
    return scheme == "https" and bool(domain(cfg)) and host == domain(cfg)


def usable(cfg: Config) -> bool:
    """Whether passkeys made before can sign in: their name is still set. A name with * listed since stops new ones
    being made, not those."""
    return bool(domain(cfg)) and not _is_address(domain(cfg))


# ---- making one ----------------------------------------------------------------------------------------------------

def _user(con: sqlite3.Connection, account_id: int) -> bytes:
    """The random id the account's passkeys know it by, made the first time. Commits."""
    con.execute("UPDATE account SET passkey_user = ? WHERE id = ? AND passkey_user IS NULL",
                (secrets.token_bytes(32), account_id))
    con.commit()
    return bytes(con.execute("SELECT passkey_user FROM account WHERE id = ?", (account_id,)).fetchone()[0])


def _transports(text: str | None) -> list[AuthenticatorTransport]:
    out = []
    for t in json.loads(text or "[]"):
        try:
            out.append(AuthenticatorTransport(t))
        except ValueError:
            pass  # one a newer browser knows
    return out


def start_adding(con: sqlite3.Connection, account_id: int, cfg: Config) -> tuple[dict, bytes]:
    """What the browser needs to make a passkey for this account (navigator.credentials.create), and the challenge to
    keep until it answers. Each phone or computer makes one: those the account has already are left out."""
    if problem := why_not(cfg):
        raise Problem(problem)
    row = con.execute("""SELECT a.email, p.name FROM account a LEFT JOIN person p ON p.id = a.person_id
                         WHERE a.id = ? AND a.disabled_at IS NULL""", (account_id,)).fetchone()
    if row is None:
        raise Problem("No such account.")
    have = con.execute("SELECT credential_id, transports FROM passkey WHERE account_id = ? AND rp_id = ?",
                       (account_id, domain(cfg))).fetchall()
    options = generate_registration_options(
        rp_id=domain(cfg), rp_name=RP_NAME, user_id=_user(con, account_id), user_name=row["email"],
        user_display_name=row["name"] or row["email"], timeout=TIMEOUT * 1000,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=bytes(r["credential_id"]),
                                                           transports=_transports(r["transports"]) or None)
                             for r in have])
    return options_to_json_dict(options), options.challenge


def add(con: sqlite3.Connection, account_id: int, credential: dict, challenge: bytes, cfg: Config,
        name: str = "") -> int:
    """Check the browser's answer to start_adding and keep the passkey it made. Doesn't commit: the caller does, with
    the invite it used up."""
    try:
        made = verify_registration_response(credential=credential, expected_challenge=challenge,
                                            expected_rp_id=domain(cfg), expected_origin=origin(cfg),
                                            require_user_verification=True)
    except Exception:  # anything about what the browser sent: it comes from anyone, and is checked here
        raise Problem("That passkey couldn't be checked, so it wasn't kept. Try again.") from None
    response = credential.get("response") if isinstance(credential.get("response"), dict) else {}
    sent = response.get("transports")
    transports = [t for t in sent if isinstance(t, str) and len(t) < 20][:8] if isinstance(sent, list) else []
    try:
        return con.execute("""INSERT INTO passkey(account_id, credential_id, public_key, sign_count, rp_id, transports,
                                                  synced, name, created_at)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                           (account_id, made.credential_id, made.credential_public_key, made.sign_count, domain(cfg),
                            json.dumps(transports), int(made.credential_backed_up), _name(name) or "A passkey",
                            _now().isoformat())).lastrowid
    except sqlite3.IntegrityError:
        raise Problem("That passkey is kept here already.") from None


def may_add(account: dict, what: str | None = None) -> str | None:
    """Why someone signed in can't add a passkey just now, or None: within RECENT_MINUTES of signing in, or
    FIRST_HOURS when that was the account's first sign-in. what: something else that gives a lasting way in, held to
    the same rule (common._recently_signed_in)."""
    until = add_until(account)
    if until is not None and _now() <= until:
        return None
    if what:
        return (f"To {what}, sign out and in again first. It can be done within {RECENT_MINUTES} minutes of signing "
                "in, so that nobody who finds a phone or computer left signed in can make a way in of their own.")
    return (f"To add a passkey, sign out and in again first. It can be added within {RECENT_MINUTES} minutes of "
            "signing in, so that nobody who finds a phone or computer left signed in can add one of their own.")


def add_until(account: dict) -> datetime | None:
    """Until when the session someone is signed in with may add passkeys."""
    if not account.get("session_made"):
        return None
    allowed = timedelta(hours=FIRST_HOURS) if account.get("session_first") else timedelta(minutes=RECENT_MINUTES)
    return datetime.fromisoformat(account["session_made"]) + allowed


# ---- signing in ----------------------------------------------------------------------------------------------------

def start_signing_in(cfg: Config) -> tuple[dict, bytes]:
    """What the browser needs to sign in with a passkey (navigator.credentials.get): no list of passkeys, so that it
    offers whichever it has for this site, and nobody types an email."""
    if not usable(cfg):
        raise Problem("Signing in with a passkey isn't set up here.")
    options = generate_authentication_options(rp_id=domain(cfg), timeout=TIMEOUT * 1000,
                                              user_verification=UserVerificationRequirement.REQUIRED)
    return options_to_json_dict(options), options.challenge


def sign_in(con: sqlite3.Connection, credential: dict, challenge: bytes, cfg: Config) -> tuple[int, int]:
    """Check a passkey's answer to start_signing_in. Returns (account id, passkey id). Commits its use."""
    try:
        sent = parse_authentication_credential_json(credential)
    except Exception:  # what anyone can send
        raise Problem(NOT_CHECKED) from None
    row = con.execute("""SELECT p.*, a.passkey_user, a.disabled_at FROM passkey p JOIN account a ON a.id = p.account_id
                         WHERE p.credential_id = ? AND p.rp_id = ?""", (sent.raw_id, domain(cfg))).fetchone()
    if row is None:
        raise Problem("This passkey isn't one of this site's: it may have been taken away. Ask whoever looks after "
                      "the photos for a new link.")
    # the account the phone made it for: a discoverable passkey always says so
    if not sent.response.user_handle or not hmac.compare_digest(bytes(sent.response.user_handle),
                                                                bytes(row["passkey_user"] or b"")):
        raise Problem(NOT_CHECKED)
    try:
        used = verify_authentication_response(credential=sent, expected_challenge=challenge,
                                              expected_rp_id=domain(cfg), expected_origin=origin(cfg),
                                              credential_public_key=bytes(row["public_key"]),
                                              credential_current_sign_count=row["sign_count"],
                                              require_user_verification=True)
    except Exception:  # a wrong signature, address or count, or the person didn't unlock it
        raise Problem(NOT_CHECKED) from None
    if row["disabled_at"]:
        raise Problem("This account is switched off. Ask whoever looks after the photos.")
    con.execute("UPDATE passkey SET sign_count = ?, synced = ?, last_used_at = ? WHERE id = ?",
                (used.new_sign_count, int(used.credential_backed_up), _now().isoformat(), row["id"]))
    con.commit()
    return row["account_id"], row["id"]


# ---- an admin's links ----------------------------------------------------------------------------------------------

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def make_invite(con: sqlite3.Connection, account_id: int, made_by: int | None, cfg: Config) -> tuple[str, datetime]:
    """A link's token for making a passkey for this account (shown once), and when it runs out. It replaces the
    account's link before it. Commits."""
    if problem := why_not(cfg):
        raise Problem(problem)
    row = con.execute("SELECT role, disabled_at FROM account WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        raise Problem("No such account.")
    if row["disabled_at"]:
        raise Problem("This account is switched off: switch it back on first.")
    token, now = secrets.token_urlsafe(32), _now()
    until = now + (timedelta(hours=ADMIN_INVITE_HOURS) if row["role"] == "admin" else timedelta(days=INVITE_DAYS))
    con.execute("DELETE FROM passkey_invite WHERE account_id = ?", (account_id,))
    con.execute("INSERT INTO passkey_invite(token_hash, account_id, made_by, made_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (_hash(token), account_id, made_by, now.isoformat(), until.isoformat()))
    con.commit()
    return token, until


def invite_link(cfg: Config, token: str) -> str:
    """The link to send. The token is after the #, which browsers never send to a server: it stays out of the
    proxy's and the web server's logs, and the page's script hands it over."""
    return f"{origin(cfg)}/auth/invite#{token}"


def invited(con: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    """The account a link is for, while the link works and the account is on."""
    if not token or len(token) > 100:
        return None
    return con.execute("""SELECT a.*, p.name AS person_name, i.expires_at AS invite_until, i.token_hash
                          FROM passkey_invite i JOIN account a ON a.id = i.account_id
                          LEFT JOIN person p ON p.id = a.person_id
                          WHERE i.token_hash = ? AND i.expires_at > ? AND a.disabled_at IS NULL""",
                       (_hash(token), _now().isoformat())).fetchone()


def use_invite(con: sqlite3.Connection, token: str) -> bool:
    """Take a link out of use, in one statement: two pages with one link can't both make a passkey. Doesn't commit."""
    return con.execute("DELETE FROM passkey_invite WHERE token_hash = ? AND expires_at > ?",
                       (_hash(token), _now().isoformat())).rowcount == 1


def cancel_invite(con: sqlite3.Connection, account_id: int) -> None:
    con.execute("DELETE FROM passkey_invite WHERE account_id = ?", (account_id,))
    con.commit()


def invites(con: sqlite3.Connection) -> dict[int, str]:
    """Each account's link that still works: when it runs out."""
    return {r[0]: r[1] for r in con.execute("SELECT account_id, expires_at FROM passkey_invite WHERE expires_at > ?",
                                            (_now().isoformat(),))}


# ---- the passkeys kept ---------------------------------------------------------------------------------------------

def of_account(con: sqlite3.Connection, account_id: int) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM passkey WHERE account_id = ? ORDER BY created_at, id", (account_id,)).fetchall()


def by_account(con: sqlite3.Connection) -> dict[int, list[sqlite3.Row]]:
    out: dict[int, list] = {}
    for r in con.execute("SELECT * FROM passkey ORDER BY created_at, id"):
        out.setdefault(r["account_id"], []).append(r)
    return out


def get(con: sqlite3.Connection, passkey_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM passkey WHERE id = ?", (passkey_id,)).fetchone()


def rename(con: sqlite3.Connection, passkey_id: int, name: str) -> None:
    if not _name(name):
        raise Problem("Give it a name, like Ann's phone.")
    con.execute("UPDATE passkey SET name = ? WHERE id = ?", (_name(name), passkey_id))
    con.commit()


def remove(con: sqlite3.Connection, passkey_id: int) -> None:
    """Take a passkey away (a lost phone): it no longer signs in, and the sessions it started end with it."""
    con.execute("DELETE FROM passkey WHERE id = ?", (passkey_id,))
    con.commit()


def any_for(con: sqlite3.Connection, cfg: Config) -> bool:
    """Whether anyone has a passkey for this site's name: the sign-in page then offers them."""
    return usable(cfg) and con.execute("SELECT 1 FROM passkey WHERE rp_id = ? LIMIT 1",
                                       (domain(cfg),)).fetchone() is not None


def admin_has_one(con: sqlite3.Connection) -> bool:
    return con.execute("""SELECT 1 FROM passkey p JOIN account a ON a.id = p.account_id
                          WHERE a.role = 'admin' AND a.disabled_at IS NULL LIMIT 1""").fetchone() is not None


def _name(text: str) -> str:
    return " ".join(str(text or "").split())[:60]


def name_from(user_agent: str) -> str:
    """A first name for a passkey, from the browser that made it: Chrome on Windows, Safari on iPhone."""
    ua = user_agent or ""
    device = next((d for pattern, d in ((r"iPhone", "iPhone"), (r"iPad", "iPad"), (r"Android", "Android"),
                                        (r"CrOS", "Chromebook"), (r"Windows", "Windows"), (r"Macintosh", "Mac"),
                                        (r"Linux", "Linux")) if re.search(pattern, ua)), "")
    browser = next((b for pattern, b in ((r"Edg[A-Z]?/", "Edge"), (r"SamsungBrowser/", "Samsung Internet"),
                                         (r"Firefox/|FxiOS/", "Firefox"), (r"Chrome/|CriOS/", "Chrome"),
                                         (r"Safari/", "Safari")) if re.search(pattern, ua)), "")
    if browser and device:
        return f"{browser} on {device}"
    return device or browser or "A passkey"
