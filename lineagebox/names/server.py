"""The free-address service's web API, at https://<zone>/v1/ behind nginx (`lineagebox names serve`).

What it promises (docs/remote-access.md, and the terms at https://lineagebox.com/terms/):
- Keys stay at home. A LineageBox makes its own key and gets its own certificate from Let's Encrypt; the service only
  writes DNS records, and never sees a key.
- A name is never given to anyone else, even after it is given up: links, photo frames, passkeys and cookies made for
  it would otherwise reach a stranger.
- A name is for the home network (private addresses only) or for outside (the public address its LineageBox calls
  from), and stays so: a name that could flip from a public address to a private one is how a web page reaches into
  a home network (DNS rebinding).
- Each name's CAA record lets only its own Let's Encrypt account get it a certificate, and only by the DNS check: whoever
  gets a house's old address after it changes can't get one by answering on port 80, and a stolen token alone can't.
- Kept: the name, a hash of its token, its address, its Let's Encrypt account, when it was last heard from, and a hash
  of the address that asked for it (for the limits). No email, no account, nothing about the family.

Requests send the token as `Authorization: Bearer <token>`. Answers are JSON; a refusal is {"detail": "..."} in words a
person can be shown. IPv4 only for now: a house's IPv6 address is the computer's own, not the router's.
"""
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import socket
import sqlite3
import ssl
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import certifi
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from lineagebox import __version__
from lineagebox.names import google, words, zone as zones

TERMS = "https://lineagebox.com/terms/"
PRIVACY = "https://lineagebox.com/privacy/"
ABUSE = "abuse@lineagebox.com"
PER_DAY = 3            # new names a day from one address
PER_WEEK = 40          # new names a week in all: Let's Encrypt gives a domain 50 new certificates a week
QUIET_DAYS = 90        # a name not heard from this long stops resolving (it stays its holder's)
CHANGE_EVERY = 30      # seconds: a name's address changes at most this often
CHALLENGES_AN_HOUR = 20
CHECK_EVERY = 10       # seconds between reachability checks of one name
CHECK_PORTS = frozenset({443, 8443, *range(8000, 8100)})
SHARED = ipaddress.ip_network("100.64.0.0/10")  # carrier NAT, and VPNs such as Tailscale: a home-network address
ACCOUNT = re.compile(r"https://acme(-staging)?-v02\.api\.letsencrypt\.org/acme/acct/\d{1,20}")
NAME = re.compile(r"[a-z]+-[a-z]+(?:-\d{2})?")  # maple-river; maple-river-42 as given until 2026-11-24
CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")  # base64url of a SHA-256, as RFC 8555 makes it

SCHEMA = """
CREATE TABLE IF NOT EXISTS name (
    name        TEXT PRIMARY KEY,
    token       TEXT NOT NULL,                 -- SHA-256 of the token, hex
    mode        TEXT NOT NULL CHECK (mode IN ('outside', 'home')),
    address     TEXT,
    account     TEXT NOT NULL,                 -- the Let's Encrypt account its CAA record names
    asked_by    TEXT NOT NULL,                 -- keyed hash of the address that asked for it
    created_at  TEXT NOT NULL,
    seen_at     TEXT NOT NULL,
    changed_at  TEXT,
    dormant     INTEGER NOT NULL DEFAULT 0,    -- not heard from in QUIET_DAYS: no records until it is again
    released_at TEXT,                          -- given up by its holder: never given out again
    disabled    TEXT                           -- switched off by the service, and why (abuse)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ago(stamp: str) -> timedelta:
    return datetime.now(timezone.utc) - datetime.fromisoformat(stamp)


def ipv4(text: str) -> ipaddress.IPv4Address | None:
    try:
        a = ipaddress.ip_address(str(text).strip())
    except ValueError:
        return None
    if a.version == 6:
        a = a.ipv4_mapped
    return a


def home_address(a) -> bool:
    """An address only a home network (or a VPN like Tailscale) has: never the internet's, never this machine's."""
    return a is not None and (a.is_private or a in SHARED) and not (a.is_loopback or a.is_link_local
                                                                     or a.is_unspecified or a.is_reserved)


def outside_address(a) -> bool:
    return a is not None and a.is_global


class Register(BaseModel):
    mode: str = Field(pattern="^(outside|home)$")
    account: str = Field(max_length=120)
    address: str = Field("", max_length=45)
    name: str = Field("", max_length=40)  # one of /v1/names/suggest's, or "" for any


class Address(BaseModel):
    address: str = Field("", max_length=45)


class Challenge(BaseModel):
    value: str = Field(max_length=64)


class Check(BaseModel):
    port: int = 443


class Store:
    """The names, in SQLite: one connection, used under a lock (requests are few and short)."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.executescript(SCHEMA)
        self.lock = threading.Lock()
        with self.lock:
            row = self.con.execute("SELECT value FROM meta WHERE key = 'salt'").fetchone()
            if row is None:
                self.con.execute("INSERT INTO meta VALUES ('salt', ?)", (secrets.token_hex(32),))
                row = self.con.execute("SELECT value FROM meta WHERE key = 'salt'").fetchone()
        self.salt = row["value"]

    def run(self, sql: str, args=()):
        with self.lock:
            return self.con.execute(sql, args).fetchall()

    def asked_by(self, ip: str) -> str:
        return hmac.new(self.salt.encode(), ip.encode(), hashlib.sha256).hexdigest()


def create_app(zone, data: Path, trust_proxy: bool = True, google_client: tuple[str, str] = ("", ""),
               google_http=None) -> FastAPI:
    """The API for `zone` (zone.Cloudflare, zone.Memory), keeping its names in data/names.db. trust_proxy: take the
    caller's address from X-Real-IP when the connection comes from this machine or a private network (nginx in front;
    the container's port is bound to 127.0.0.1 only). google_client: (ID, secret) of the Google client that its
    names' servers sign in with (google.py); none, no such sign-in."""
    app = FastAPI(title="Free addresses for LineageBox", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url=None)
    store = Store(data / "names.db")
    domain = zone.domain
    app.state.store, app.state.zone = store, zone
    recent: dict[str, deque] = defaultdict(deque)  # name -> times of its challenges and checks, this last hour
    recent_lock = threading.Lock()

    def caller(request: Request) -> str:
        peer = request.client.host if request.client else ""
        if trust_proxy and (a := ipv4(peer)) is not None and (a.is_loopback or a.is_private):
            return request.headers.get("x-real-ip", peer).strip()
        return peer

    def held(request: Request, name: str) -> sqlite3.Row:
        """The name, if the request has its token; refused otherwise, the same way whether or not it exists."""
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        rows = store.run("SELECT * FROM name WHERE name = ?", (name,)) if NAME.fullmatch(name) else []
        if not rows or not token or not hmac.compare_digest(rows[0]["token"],
                                                            hashlib.sha256(token.encode()).hexdigest()):
            raise HTTPException(403, "That isn't this address's key.")
        row = rows[0]
        if row["released_at"]:
            raise HTTPException(410, "This address was given up. Ask for a new one.")
        if row["disabled"]:
            raise HTTPException(403, f"This address was switched off: {row['disabled']} Write to {ABUSE} if that "
                                     "is a mistake.")
        return row

    def often(key: str, most: int, seconds: float) -> None:
        with recent_lock:
            times = recent[key]
            while times and time.monotonic() - times[0] > seconds:
                times.popleft()
            if len(times) >= most:
                raise HTTPException(429, "Too many tries just now. Wait a little and try again.")
            times.append(time.monotonic())

    def records(row, address: str | None = None) -> None:
        """The name's address and CAA records, as they should be now."""
        host = f"{row['name']}.{domain}"
        try:  # the CAA first: until a name has its own, the zone's (any check, any account) would be its
            zone.put(host, "CAA", [(0, "issue", f"letsencrypt.org; accounturi={row['account']}; "
                                               "validationmethods=dns-01"), (0, "issuewild", ";")])
            zone.put(host, "A", [address or row["address"]])
        except zones.Problem as e:
            raise HTTPException(503, f"The address couldn't be saved just now ({e}). Try again in a few minutes.")

    def forget(name: str) -> None:
        host = f"{name}.{domain}"
        try:
            for kind in ("A", "AAAA", "CAA"):
                zone.put(host, kind, [])
            zone.put(f"_acme-challenge.{host}", "TXT", [])
        except zones.Problem as e:
            raise HTTPException(503, f"That couldn't be done just now ({e}). Try again in a few minutes.")

    def answer(row) -> dict:
        return {"name": row["name"], "host": f"{row['name']}.{domain}", "mode": row["mode"],
                "address": row["address"], "notice": "", "terms": TERMS}

    @app.get("/v1/")
    def about():
        return {"service": domain, "version": __version__, "terms": TERMS, "privacy": PRIVACY, "abuse": ABUSE,
                "google_client_id": google_client[0] if all(google_client) else ""}

    google.add(app, store, domain, *google_client, CHECK_PORTS, google_http)

    @app.get("/v1/names/suggest")
    def suggestions(count: int = 5):
        """A few names nobody has (nor had), to choose from: all random, so none says who asks."""
        out: list[str] = []
        for _ in range(60):
            name = words.new_name()
            if name not in out and not store.run("SELECT 1 FROM name WHERE name = ?", (name,)):
                out.append(name)
            if len(out) >= min(max(count, 1), 8):
                break
        return {"names": out}

    @app.get("/v1/address")
    def your_address(request: Request):
        return {"address": caller(request)}

    @app.post("/v1/names")
    def register(request: Request, body: Register):
        ip = caller(request)
        if not ACCOUNT.fullmatch(body.account):
            raise HTTPException(400, "Give the Let's Encrypt account this server made (its address).")
        if body.mode == "outside":
            if not outside_address(ipv4(ip)):
                raise HTTPException(400, f"This server reached the service from {ip}, which isn't a public IPv4 "
                                         "address, so there is nothing outside to point the address at.")
            address = str(ipv4(ip))
        else:
            if not home_address(ipv4(body.address)):
                raise HTTPException(400, "A home-network address is a private one, such as 192.168.1.20.")
            address = str(ipv4(body.address))
        who = store.asked_by(ip)
        day = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
        week = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
        if store.run("SELECT count(*) n FROM name WHERE asked_by = ? AND created_at > ?", (who, day))[0]["n"] >= PER_DAY:
            raise HTTPException(429, "This address has asked for enough names today. Try again tomorrow.")
        if store.run("SELECT count(*) n FROM name WHERE created_at > ?", (week,))[0]["n"] >= PER_WEEK:
            raise HTTPException(429, "Every free address for this week has been given out. Try again in a few days.")
        token = secrets.token_urlsafe(32)
        if body.name and not words.valid(body.name):
            raise HTTPException(400, "Choose one of the names offered.")
        for _ in range(1 if body.name else 50):
            name = body.name or words.new_name()
            try:
                store.run("INSERT INTO name (name, token, mode, address, account, asked_by, created_at, seen_at) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          (name, hashlib.sha256(token.encode()).hexdigest(), body.mode, address, body.account, who,
                           now(), now()))
                break
            except sqlite3.IntegrityError:
                continue
        else:
            if body.name:
                raise HTTPException(409, f"{body.name} was taken a moment ago: choose another.")
            raise HTTPException(503, "No free name was found. Try again.")
        row = store.run("SELECT * FROM name WHERE name = ?", (name,))[0]
        try:
            records(row)
        except HTTPException:
            try:
                forget(name)
            except HTTPException:
                pass
            store.run("DELETE FROM name WHERE name = ?", (name,))  # nothing was given out after all
            raise
        return {**answer(row), "token": token}

    @app.get("/v1/names/{name}")
    def show(request: Request, name: str):
        return answer(held(request, name))

    @app.put("/v1/names/{name}/address")
    def update(request: Request, name: str, body: Address):
        row = held(request, name)
        if row["mode"] == "outside":
            a = ipv4(caller(request))
            if not outside_address(a):
                raise HTTPException(400, f"This server reached the service from {caller(request)}, which isn't a "
                                         "public IPv4 address.")
        else:
            a = ipv4(body.address)
            if not home_address(a):
                raise HTTPException(400, "This address is for the home network: give a private address.")
        address, changed = str(a), str(a) != row["address"] or bool(row["dormant"])
        if changed:
            if row["changed_at"] and ago(row["changed_at"]) < timedelta(seconds=CHANGE_EVERY):
                raise HTTPException(429, "The address changed a moment ago. Wait a little and try again.")
            records(row, address)
            store.run("UPDATE name SET address = ?, changed_at = ?, dormant = 0 WHERE name = ?",
                      (address, now(), name))
        store.run("UPDATE name SET seen_at = ? WHERE name = ?", (now(), name))
        return {**answer(store.run("SELECT * FROM name WHERE name = ?", (name,))[0]), "changed": changed}

    @app.put("/v1/names/{name}/challenge")
    def challenge(request: Request, name: str, body: Challenge):
        """The TXT record Let's Encrypt looks for when this name's own server asks for its certificate."""
        held(request, name)
        if not CHALLENGE.fullmatch(body.value):
            raise HTTPException(400, "That isn't a Let's Encrypt DNS check value.")
        often(f"challenge {name}", CHALLENGES_AN_HOUR, 3600)
        try:
            zone.put(f"_acme-challenge.{name}.{domain}", "TXT", [body.value])
        except zones.Problem as e:
            raise HTTPException(503, f"The check couldn't be published just now ({e}). Try again in a few minutes.")
        store.run("UPDATE name SET seen_at = ? WHERE name = ?", (now(), name))
        return {}

    @app.delete("/v1/names/{name}/challenge")
    def challenge_done(request: Request, name: str):
        held(request, name)
        try:
            zone.put(f"_acme-challenge.{name}.{domain}", "TXT", [])
        except zones.Problem as e:
            raise HTTPException(503, f"The check couldn't be taken down just now ({e}).")
        return {}

    @app.delete("/v1/names/{name}")
    def release(request: Request, name: str):
        """Give the name up for good: it stops resolving, and is never given to anyone else."""
        held(request, name)
        forget(name)
        # only the name and its dates are kept, so that it is never given out again (https://lineagebox.com/privacy/)
        store.run("UPDATE name SET released_at = ?, token = '', address = NULL, account = '', asked_by = '' "
                  "WHERE name = ?", (now(), name))
        return {}

    @app.post("/v1/names/{name}/check")
    def check(request: Request, name: str, body: Check):
        """Whether the internet reaches this name's server: from here, to its own address and the given port only."""
        row = held(request, name)
        if row["mode"] != "outside":
            raise HTTPException(400, "This address is for the home network only: the internet isn't meant to reach it.")
        if body.port not in CHECK_PORTS:
            raise HTTPException(400, "Check port 443, 8443 or one from 8000 to 8099.")
        often(f"check {name}", 1, CHECK_EVERY)
        return reach(row["address"], f"{name}.{domain}", body.port)

    def sweep() -> int:
        """Names not heard from in QUIET_DAYS stop resolving (no record is left pointing at an old address)."""
        quiet = (datetime.now(timezone.utc) - timedelta(days=QUIET_DAYS)).isoformat(timespec="seconds")
        done = 0
        for row in store.run("SELECT name FROM name WHERE seen_at < ? AND dormant = 0 AND released_at IS NULL "
                             "AND disabled IS NULL", (quiet,)):
            try:
                forget(row["name"])
            except HTTPException:
                continue
            store.run("UPDATE name SET dormant = 1 WHERE name = ?", (row["name"],))
            done += 1
        return done

    app.state.sweep, app.state.forget = sweep, forget
    return app


def reach(address: str, host: str, port: int) -> dict:
    """Connect to address:port as a browser would to host: a TLS handshake with a certificate it trusts for host, then
    LineageBox's /healthz. Says what failed in words for the family's admin."""
    a = ipv4(address)
    if not outside_address(a):  # only ever the internet, never this machine's networks
        return {"reachable": False, "detail": "The address isn't a public one."}
    where = f"{address}:{port}"
    try:
        sock = socket.create_connection((str(a), port), timeout=8)
    except TimeoutError:
        return {"reachable": False, "detail": f"Nothing answered at {where}: the router's port forward isn't set, "
                                              "or something blocks it."}
    except OSError as e:
        return {"reachable": False, "detail": f"{where} refused the connection ({e.strerror or e}): the port forward "
                                              "may point at the wrong machine or port."}
    try:
        with ssl.create_default_context(cafile=certifi.where()).wrap_socket(sock, server_hostname=host) as tls:
            tls.settimeout(8)
            tls.sendall(f"GET /healthz HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\nUser-Agent: LineageBox "
                        f"address check\r\n\r\n".encode())
            first = tls.recv(200).split(b"\r\n", 1)[0].decode("latin-1")
    except ssl.SSLCertVerificationError as e:
        return {"reachable": False, "detail": f"{where} answered, but not with a certificate for {host} "
                                              f"({e.verify_message}). The router may forward to another machine."}
    except (ssl.SSLError, OSError) as e:
        return {"reachable": False, "detail": f"{where} answered, but not over HTTPS ({type(e).__name__})."}
    finally:
        sock.close()
    if " 200 " in f"{first} ":
        return {"reachable": True, "detail": f"The internet reaches this server at {host}."}
    return {"reachable": False, "detail": f"{where} answered with a valid certificate but said {first[:60]!r}: "
                                          "it may not be LineageBox."}


# ---- running it ---------------------------------------------------------------------------------------------------

def from_environment():
    """The zone named by NAMES_ZONE, on Cloudflare (CLOUDFLARE_ZONE_ID, CLOUDFLARE_API_TOKEN), or in memory when
    NAMES_ZONE_IN_MEMORY is set (trying it out: nothing resolves)."""
    domain = os.environ.get("NAMES_ZONE", "").strip().lower()
    if not domain:
        raise SystemExit("lineagebox names: set NAMES_ZONE (the domain the names go under)")
    if os.environ.get("NAMES_ZONE_IN_MEMORY"):
        return zones.Memory(domain)
    zone_id, token = os.environ.get("CLOUDFLARE_ZONE_ID", ""), os.environ.get("CLOUDFLARE_API_TOKEN", "")
    if not (zone_id and token):
        raise SystemExit("lineagebox names: set CLOUDFLARE_ZONE_ID and CLOUDFLARE_API_TOKEN")
    return zones.Cloudflare(domain, zone_id, token)


def main(action: str, host: str, port: int, data: str, name: str = "", why: str = "") -> None:
    zone = from_environment()
    app = create_app(zone, Path(data), google_client=(os.environ.get("GOOGLE_CLIENT_ID", "").strip(),
                                                      os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()))
    store = app.state.store
    if action == "serve":
        import uvicorn

        def every_day():
            while True:
                try:
                    if done := app.state.sweep():
                        print(f"{done} quiet name(s) stopped resolving", flush=True)
                except Exception as e:  # the DNS host away: next time
                    print(f"sweep failed: {e}", flush=True)
                time.sleep(86400)

        threading.Thread(target=every_day, daemon=True, name="sweep").start()
        print(f"names for {zone.domain} on http://{host}:{port}/v1/", flush=True)
        uvicorn.run(app, host=host, port=port, log_level="warning", proxy_headers=False)
    elif action == "list":
        for row in store.run("SELECT * FROM name ORDER BY created_at"):
            state = ("given up" if row["released_at"] else f"off: {row['disabled']}" if row["disabled"]
                     else "quiet" if row["dormant"] else "on")
            print(f"{row['name']}.{zone.domain:24} {row['mode']:8} {row['address'] or '':16} made {row['created_at'][:10]}"
                  f" seen {row['seen_at'][:10]}  {state}")
    elif action in ("disable", "enable"):
        rows = store.run("SELECT * FROM name WHERE name = ?", (name.split(".")[0],))
        if not rows:
            raise SystemExit(f"no name {name}")
        if action == "disable":
            app.state.forget(rows[0]["name"])
            store.run("UPDATE name SET disabled = ? WHERE name = ?", (why or "it broke the terms.", rows[0]["name"]))
            print(f"{name} is off and resolves no more")
        else:
            store.run("UPDATE name SET disabled = NULL, dormant = 1 WHERE name = ?", (rows[0]["name"],))
            print(f"{name} is on again: its records come back when its server next checks in")
