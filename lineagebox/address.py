"""A free address for this LineageBox, such as maple-river.ourplace.here, with a certificate of its own.

Passkeys, Sign in with Google and the phone app all need HTTPS on a real name, which a home server at
http://192.168.1.20:8000 doesn't have. One click on Settings → Address and HTTPS gets both:

1. This server makes a Let's Encrypt account (its key stays here, acme.py).
2. The free-address service (config address_service, names/server.py) gives it a random name, pointed at the
   house's public address (for outside) or at this machine's home-network address (home network only), whose CAA
   record lets only that Let's Encrypt account get it a certificate.
3. Let's Encrypt checks a TXT record the service publishes for it, and signs a certificate for the name. Its key is
   made here and never leaves the data folder (certs/<name>.crt and .key).
4. The name is added to the host names; it becomes the address from outside if there is none yet; and, unless a
   reverse proxy does HTTPS, LineageBox serves HTTPS with it.

Then serve's thread (start) tells the service when the house's address changes (it asks every EVERY seconds what
address it calls from), checks in at least twice a day, and renews the certificate two-thirds of the way through its
life. Outside the house only with family accounts on: without them LineageBox lets in whoever looks local, and a
tunnel or proxy can make the whole internet look local.

What is kept, in the settings table (so the catalog's backups have it): the name, its token, the Let's Encrypt
account's key and address. Whoever has them can move the name: keep backups as private as the photos.
"""
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from lineagebox import __version__, acme, clock, db, errors, https, settings
from lineagebox.config import Config

KEY = "address"          # in the settings table
EVERY = 300              # seconds between looks at the house's address
KEEP_ALIVE = timedelta(hours=12)
RENEW_AT = 2 / 3         # of a certificate's life, as Let's Encrypt advises
RETRY_RENEWAL = timedelta(hours=6)
FIRST_RETRY = timedelta(minutes=9)   # a name with no certificate yet: tried again at the next look after this
UA = f"LineageBox/{__version__} (free address)"
_busy = threading.Lock()  # one change at a time: the Settings page's and the thread's


class Problem(errors.Problem):
    """The free-address service or Let's Encrypt refused, or couldn't be reached; the message says why."""


def state(con) -> dict | None:
    """This server's free address: name, host, mode, token, address, port, certificate files and dates."""
    s = settings.get(con, KEY)
    return s if s and not s.get("released_at") else None


def url(s: dict) -> str:
    """The address from outside: https://<host>, and the port when it isn't 443."""
    port = int(s.get("port") or 443)
    return f"https://{s['host']}" + ("" if port == 443 else f":{port}")


def _http(base: str) -> httpx.Client:
    """The service, over IPv4: the house's public address is the router's, which IPv6 would bypass."""
    return httpx.Client(base_url=base, timeout=30, headers={"User-Agent": UA},
                        transport=httpx.HTTPTransport(local_address="0.0.0.0", retries=1))


def _api(base: str, method: str, path: str, token: str = "", body: dict | None = None) -> dict:
    try:
        with _http(base) as http:
            r = http.request(method, path, json=body,
                             headers={"Authorization": f"Bearer {token}"} if token else {})
    except httpx.HTTPError as e:
        raise Problem(f"The free-address service ({base}) could not be reached ({type(e).__name__}). Try again in a "
                      "few minutes.") from None
    try:
        answer = r.json()
    except ValueError:
        answer = {}
    if r.status_code >= 400:
        raise Problem(str(answer.get("detail") or f"The free-address service answered {r.status_code}."))
    return answer


def _acme(directory: str, key_pem: str, kid: str = "") -> acme.Client:
    return acme.Client(directory, key_pem, kid)


def _since(stamp: str | None) -> timedelta:
    if not stamp:
        return timedelta.max
    return datetime.now() - datetime.fromisoformat(stamp)


def _iso(seconds: float) -> str:
    """A moment as clock.now() writes them: this machine's time, to the second."""
    return datetime.fromtimestamp(seconds).isoformat(timespec="seconds")


# ---- getting one ---------------------------------------------------------------------------------------------------

def suggest(cfg: Config, count: int = 5) -> list[str]:
    """A few names nobody has, from the service, for the admin to choose from (Get a free address)."""
    if not cfg.address_service:
        raise Problem("No free-address service is set (address_service).")
    return [str(n) for n in _api(cfg.address_service.rstrip("/"), "GET", f"/v1/names/suggest?count={count}")
            .get("names", [])]


def get(con, cfg: Config, mode: str, home: str = "", port: int = 443, name: str = "") -> dict:
    """Ask for a free address (name: one the admin chose from suggest(), or "" for any) and its certificate, and use
    them (see the module's steps). Returns the state, with "serving": whether LineageBox now serves HTTPS with it,
    and "pending": why there is no certificate yet, when the name was given but Let's Encrypt couldn't sign one just
    now (busy, say). The name is kept then: asking again, Get the certificate, or keep_up (within FIRST_RETRY)
    finishes it. Raises Problem when no name was given."""
    if not cfg.address_service:
        raise Problem("No free-address service is set (address_service).")
    if mode not in ("outside", "home"):
        raise Problem("Choose: from outside the house, or the home network only.")
    if mode == "outside" and not cfg.accounts_enabled:
        raise Problem("Turn on family accounts first (Settings → Family accounts). Without them LineageBox lets in "
                      "anyone who looks like the home network, and from outside the house that can be anyone.")
    if not 1 <= int(port) <= 65535:
        raise Problem("A port is a number from 1 to 65535.")
    if not _busy.acquire(timeout=5):
        raise Problem("LineageBox is busy with its free address already. Wait a minute and look again.")
    try:
        s, client = state(con), None
        if s and s.get("cert"):
            raise Problem("This server has a free address already.")
        if s is None:  # (one given before, still without its certificate, is finished instead)
            key = acme.new_key_pem()
            client = _acme(cfg.acme_directory, key)
            kid = client.account()
            base = cfg.address_service.rstrip("/")
            got = _api(base, "POST", "/v1/names", body={"mode": mode, "account": kid, "address": home, "name": name})
            s = {"service": base, "name": got["name"], "host": got["host"], "token": got["token"], "mode": mode,
                 "address": got["address"], "port": int(port), "acme_key": key, "acme_account": kid,
                 "acme_directory": cfg.acme_directory, "made_at": clock.now(), "seen_at": clock.now()}
            settings.put(con, KEY, s)
        return _finish(con, cfg, s, client)
    finally:
        _busy.release()


def _finish(con, cfg: Config, s: dict, client: acme.Client | None = None) -> dict:
    """The first certificate for a name already given, and the name put to use (_use). Let's Encrypt not managing
    it just now isn't the name's end: s["pending"] says why, the address's problem says what happens next."""
    try:
        _certificate(con, cfg, s, client)
    except (acme.Problem, Problem, https.Problem) as e:
        settings.set_problem(con, "address", f"{s['host']} is yours, but it has no certificate yet: {e} LineageBox "
                                             "tries again by itself within ten minutes, or press Get the certificate.")
        return {**s, "serving": False, "pending": str(e)}
    serving = _use(con, cfg, s)
    settings.set_problem(con, "address", None)
    settings.put(con, WAY, "free")  # Address and HTTPS shows the free address from now on
    return {**s, "serving": serving}


def change(con, cfg: Config, name: str = "") -> dict:
    """A different free name for this server (one the admin chose from suggest(), or any): the new name and its
    certificate first, then the new one put in the old one's place everywhere (host names, the address from outside,
    LineageBox's HTTPS, Google sign-in through it), and the old one given up. Nothing changes if the new one can't be
    had. Returns the new state, with "serving" (LineageBox's HTTPS switched to it) and "was" (the old host)."""
    old = state(con)
    if old is None or not old.get("cert"):
        raise Problem("This server has no free address with a certificate to change from.")
    if not _busy.acquire(timeout=5):
        raise Problem("LineageBox is busy with its free address already. Wait a minute and look again.")
    try:
        base = old["service"]
        got = _api(base, "POST", "/v1/names", body={
            "mode": old["mode"], "account": old["acme_account"], "name": name,
            "address": old["address"] if old["mode"] == "home" else ""})
        new = {**{k: old[k] for k in ("service", "mode", "port", "acme_key", "acme_account", "acme_directory")},
               "name": got["name"], "host": got["host"], "token": got["token"], "address": got["address"],
               "made_at": clock.now(), "seen_at": clock.now()}
        if old.get("google_client"):
            new["google_client"] = old["google_client"]
        try:
            _certificate(con, cfg, new, save=False)
        except (acme.Problem, Problem, https.Problem):
            try:  # the new name goes again: the old one stays as it was
                _api(base, "DELETE", f"/v1/names/{new['name']}", new["token"])
            except Problem:
                pass
            raise
        now = settings.current(con, cfg)
        changes = {"allowed_hosts": [h for h in now.allowed_hosts if h != old["host"]] + [new["host"]]}
        if now.public_url == url(old):
            changes["public_url"] = url(new)
        switch = now.tls_cert == old["cert"]
        if switch:
            changes.update(tls_cert=new["cert"], tls_key=new["key"])
        settings.save(con, cfg, changes)
        settings.put(con, KEY, new)
        try:
            _api(base, "DELETE", f"/v1/names/{old['name']}", old["token"])
        except Problem:
            pass  # unheard of for QUIET_DAYS, it stops resolving by itself
        for f in (old["cert"], old.get("key")):
            if f:
                Path(f).unlink(missing_ok=True)
        settings.set_problem(con, "address", None)
        return {**new, "serving": switch, "was": old["host"]}
    finally:
        _busy.release()


WAY = "address_way"  # in the settings table: "free" or "own", the way Address and HTTPS shows


def way(con, cfg: Config, free: dict) -> str:
    """Which way to a name Address and HTTPS shows: the admin's choice; else the free address, if this server has
    one or nothing of its own is set up; else a name of one's own."""
    if not (free.get("offered") or free.get("url")):
        return "own"
    chosen = settings.get(con, WAY)
    if chosen in ("free", "own"):
        return chosen
    if free.get("url"):
        return "free"
    return "own" if (cfg.tls_cert or cfg.trusted_proxies or cfg.allowed_hosts) else "free"


def choose(con, cfg: Config, chosen: str) -> bool:
    """Show the free address or a name of one's own on Address and HTTPS. Choosing the free one, with a certificate
    and no reverse proxy, also has LineageBox serve that certificate: True then (the web server starts again)."""
    if chosen not in ("free", "own"):
        raise Problem("Choose a free address or a name of your own.")
    settings.put(con, WAY, chosen)
    s = state(con)
    if chosen == "free" and s and s.get("cert") and not cfg.trusted_proxies and cfg.tls_cert != s["cert"]:
        settings.save(con, cfg, {"tls_cert": s["cert"], "tls_key": s["key"]})
        return True
    return False


def _certificate(con, cfg: Config, s: dict, client: acme.Client | None = None, save: bool = True) -> None:
    """A new certificate for the name, into data/certs/<host>.crt and .key (replacing the last one only once the new
    one is known to load). save: keep the state as it goes (not for a name the server doesn't use yet: change())."""
    client = client or _acme(s["acme_directory"], s["acme_key"], s["acme_account"])
    path = f"/v1/names/{s['name']}/challenge"

    def unpublish():
        try:
            _api(s["service"], "DELETE", path, s["token"])
        except Problem:
            pass  # it goes with the next one, or with the name

    s["renew_tried_at"] = clock.now()
    if save:
        settings.put(con, KEY, s)
    chain, key = client.certificate(s["host"], lambda value: _api(s["service"], "PUT", path, s["token"],
                                                                  {"value": value}), unpublish)
    folder = cfg.data_dir / https.CERTS
    folder.mkdir(parents=True, exist_ok=True)
    cert_file, key_file = folder / f"{s['host']}.crt", folder / f"{s['host']}.key"
    new_cert, new_key = folder / f".{s['host']}.crt.new", folder / f".{s['host']}.key.new"
    try:
        new_cert.write_text(chain, encoding="ascii")
        fd = os.open(new_key, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # for this user only
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(key)
        https.check(new_cert, new_key)
        os.replace(new_key, key_file)
        os.replace(new_cert, cert_file)
    finally:
        new_cert.unlink(missing_ok=True)
        new_key.unlink(missing_ok=True)
    start, end = acme.lifetime(chain)
    s.update(cert=str(cert_file), key=str(key_file), cert_from=_iso(start), cert_until=_iso(end), renew_tried_at=None)
    if save:
        settings.put(con, KEY, s)


def _use(con, cfg: Config, s: dict) -> bool:
    """The name in the host names, as the address from outside if there is none, and LineageBox's HTTPS if nothing
    else does it (no certificate set, no reverse proxy). True if it serves HTTPS with it now."""
    now = settings.current(con, cfg)
    changes = {}
    if s["host"] not in now.allowed_hosts:
        changes["allowed_hosts"] = [*now.allowed_hosts, s["host"]]
    if not now.public_url and s["mode"] == "outside":
        changes["public_url"] = url(s)
    serve = not now.trusted_proxies and not now.tls_cert
    if serve:
        changes.update(tls_cert=s["cert"], tls_key=s["key"])
    if changes:
        settings.save(con, cfg, changes)
    return serve


def make_main(con, cfg: Config) -> bool:
    """Make the free address the address from outside, and (with no reverse proxy) the certificate LineageBox serves.
    True if HTTPS changed (the web server starts again)."""
    s = state(con)
    if s is None:
        raise Problem("This server has no free address.")
    now = settings.current(con, cfg)
    changes = {"public_url": url(s)}
    if s["host"] not in now.allowed_hosts:
        changes["allowed_hosts"] = [*now.allowed_hosts, s["host"]]
    switch = not now.trusted_proxies and now.tls_cert != s.get("cert")
    if switch:
        changes.update(tls_cert=s["cert"], tls_key=s["key"])
    settings.save(con, cfg, changes)
    return switch


def renew(con, cfg: Config) -> dict:
    """A new certificate now. The first one, if getting the address stopped before it (Let's Encrypt away), is then
    put to use as get() would have; s["serving"] says whether LineageBox serves HTTPS with it."""
    s = state(con)
    if s is None:
        raise Problem("This server has no free address.")
    with _busy:
        if not s.get("cert"):
            got = _finish(con, cfg, s)
            if got.get("pending"):
                raise Problem(f"Still no certificate: {got['pending']}")
            return got
        _certificate(con, cfg, s)
    return {**s, "serving": False}


def check(con, cfg: Config) -> dict:
    """Whether the internet reaches this server by its free address, as the service sees it."""
    s = state(con)
    if s is None:
        raise Problem("This server has no free address.")
    return _api(s["service"], "POST", f"/v1/names/{s['name']}/check", s["token"], {"port": int(s.get("port") or 443)})


def google_relay(con, cfg: Config, on: bool) -> str:
    """Sign in with Google through the free-address service's client (names/google.py), or not. Turning it on asks the
    service for its client ID, and keeps it: a token is believed only if it was made for that client. Returns what to
    tell the admin."""
    if not on:
        settings.save(con, cfg, {"google_relay": False})
        return "Sign in with Google through the free address is off."
    s = state(con)
    if s is None or not s.get("cert"):
        raise Problem("Get a free address first, with its certificate (Settings → Address and HTTPS): signing in "
                      "through it goes by that address.")
    if not cfg.accounts_enabled:
        raise Problem("Turn on family accounts first.")
    client = str(_api(s["service"], "GET", "/v1/").get("google_client_id") or "")
    if not client.endswith(".apps.googleusercontent.com"):
        raise Problem(f"{s['service'].split('://')[-1]} doesn't offer Sign in with Google yet.")
    s["google_client"] = client
    settings.put(con, KEY, s)
    settings.save(con, cfg, {"google_relay": True})
    return f"Sign in with Google is on, through {s['host']}: there is nothing to set up at Google."


def release(con, cfg: Config) -> dict:
    """Give the name up for good: it stops resolving and is never given to anyone else. Its certificate files go;
    the host names lose it. Returns what was given up."""
    s = state(con)
    if s is None:
        raise Problem("This server has no free address.")
    with _busy:
        try:
            _api(s["service"], "DELETE", f"/v1/names/{s['name']}", s["token"])
        except Problem as e:
            if "given up" not in str(e):
                raise
        now = settings.current(con, cfg)
        if s["host"] in now.allowed_hosts:
            settings.save(con, cfg, {"allowed_hosts": [h for h in now.allowed_hosts if h != s["host"]]})
        for f in (s.get("cert"), s.get("key")):
            if f:
                Path(f).unlink(missing_ok=True)
        settings.put(con, KEY, {"name": s["name"], "host": s["host"], "released_at": clock.now()})
        settings.set_problem(con, "address", None)
    return s


# ---- keeping it up ---------------------------------------------------------------------------------------------------

def keep_up(con, cfg: Config) -> None:
    """Tell the service a changed address (and check in twice a day), and renew the certificate when it is due. What
    goes wrong is kept as the address's problem, for the Settings and Status pages."""
    s = state(con)
    if s is None or not _busy.acquire(blocking=False):
        return
    try:
        if s["mode"] == "outside":
            seen = _api(s["service"], "GET", "/v1/address").get("address")
            due = seen != s.get("address") or _since(s.get("seen_at")) > KEEP_ALIVE
        else:
            due = _since(s.get("seen_at")) > KEEP_ALIVE
        if due:
            got = _api(s["service"], "PUT", f"/v1/names/{s['name']}/address", s["token"],
                       {"address": s["address"] if s["mode"] == "home" else ""})
            s.update(address=got["address"], seen_at=clock.now(), notice=got.get("notice") or "")
            settings.put(con, KEY, s)
        if not s.get("cert"):  # the first one didn't come when the name did: tried again, sooner than a renewal
            if _since(s.get("renew_tried_at")) > FIRST_RETRY:
                _finish(con, cfg, s)  # which says how it went
            return
        if renewal_due(s) and _since(s.get("renew_tried_at")) > RETRY_RENEWAL:
            _certificate(con, cfg, s)
        settings.set_problem(con, "address", None)
    except (Problem, acme.Problem, https.Problem) as e:
        settings.set_problem(con, "address", f"The free address {s['host']}: {e}")
    finally:
        _busy.release()


def renewal_due(s: dict) -> bool:
    if not s.get("cert") or not Path(s["cert"]).is_file() or not s.get("cert_until"):
        return True
    start, end = datetime.fromisoformat(s["cert_from"]), datetime.fromisoformat(s["cert_until"])
    return datetime.now() > start + (end - start) * RENEW_AT


def start(cfg: Config) -> None:
    """serve's thread: keep_up every EVERY seconds, on a connection of its own."""
    def run():
        while True:
            try:
                con = db.connect(cfg, migrate_schema=False)
                try:
                    keep_up(con, settings.current(con, cfg))
                finally:
                    con.close()
            except Exception as e:  # a busy catalog: next time
                print(f"free address: {type(e).__name__}: {e}", flush=True)
            time.sleep(EVERY)

    threading.Thread(target=run, daemon=True, name="free-address").start()


def view(con, cfg: Config) -> dict:
    """What the Settings page shows: the address (without its secrets), or what was given up, and whether it is
    offered at all."""
    s = settings.get(con, KEY) or {}
    shown = {k: s.get(k) for k in ("name", "host", "mode", "address", "port", "cert", "key", "cert_until",
                                    "seen_at", "notice", "released_at", "acme_directory")}
    shown["url"] = url(s) if s.get("host") and not s.get("released_at") else ""
    shown["staging"] = s.get("acme_directory") == acme.STAGING
    shown["offered"] = bool(cfg.address_service) and not cfg.demo_enabled
    shown["service"] = cfg.address_service.split("://")[-1].rstrip("/")
    return shown
