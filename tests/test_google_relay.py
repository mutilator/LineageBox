"""Sign in with Google through ourplace.here (names/google.py; routes/signin.py, accounts.google_token): the service's
two routes, the token's checks on the family's server, and the switch and guided steps on Family accounts."""
import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

from lineagebox import accounts, address, settings
from lineagebox.names import google, server, zone as zones
from lineagebox.web import app as webapp
from tests.conftest import LAN

APP = {"X-Requested-With": "lineagebox"}
CLIENT = "123-abc.apps.googleusercontent.com"
DOMAIN = "ourplace.test"
HOST = f"maple-river-42.{DOMAIN}"
HOUSE = ("93.184.216.34", 40000)


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class Google:
    """Google's token endpoint and signing keys, as httpx answers."""
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.posted = []
        self.error = ""

    def jwks(self, key=None):
        n = (key or self.key).public_key().public_numbers()
        return {"keys": [{"kty": "RSA", "kid": "k1", "alg": "RS256", "e": b64(n.e.to_bytes(3, "big")),
                          "n": b64(n.n.to_bytes((n.n.bit_length() + 7) // 8, "big"))}]}

    def token(self, nonce, aud=CLIENT, email="pat.miller@example.com", exp=None, key=None, alg="RS256"):
        head = b64(json.dumps({"alg": alg, "kid": "k1"}).encode())
        body = b64(json.dumps({"iss": "https://accounts.google.com", "aud": aud, "exp": exp or time.time() + 300,
                               "nonce": nonce, "email": email, "email_verified": True}).encode())
        sig = (key or self.key).sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{head}.{body}.{b64(sig)}"

    def __call__(self, request):
        if request.url.path == "/oauth2/v3/certs":
            return httpx.Response(200, json=self.jwks())
        self.posted.append(dict(parse_qs(request.content.decode())))
        if self.error:
            return httpx.Response(400, json={"error": self.error})
        return httpx.Response(200, json={"id_token": "ID.TOKEN.HERE"})


@pytest.fixture
def g():
    accounts._keys.update(at=0.0, keys={})  # Google's keys kept between tests otherwise
    return Google()


# ---- the service ---------------------------------------------------------------------------------------------------

@pytest.fixture
def relay(tmp_path, g):
    zone = zones.Memory(DOMAIN)
    app = server.create_app(zone, tmp_path / "names", google_client=(CLIENT, "s3cret"),
                            google_http=httpx.Client(transport=httpx.MockTransport(g)))
    got = TestClient(app, client=HOUSE).post("/v1/names", json={
        "mode": "outside", "account": "https://acme-v02.api.letsencrypt.org/acme/acct/1"}).json()
    return app, got


def test_the_service_sends_people_to_google_with_the_names_nonce(relay):
    app, got = relay
    c = TestClient(app, client=HOUSE, follow_redirects=False)
    assert c.get("/v1/").json()["google_client_id"] == CLIENT
    r = "r" * 43
    answer = c.get("/v1/google/start", params={"name": got["name"], "r": r, "state": "s" * 20})
    assert answer.status_code == 303
    to = urlparse(answer.headers["location"])
    q = {k: v[0] for k, v in parse_qs(to.query).items()}
    assert to.netloc == "accounts.google.com" and q["client_id"] == CLIENT
    assert q["nonce"] == google.google_nonce(got["host"], r) == accounts.relay_nonce(got["host"], r)
    assert q["redirect_uri"] == f"https://{DOMAIN}/v1/google/callback" and q["prompt"] == "select_account"
    assert c.get("/v1/google/start", params={"name": "no-such-10", "r": r, "state": "s" * 20}).status_code == 404
    assert c.get("/v1/google/start", params={"name": got["name"], "r": "short", "state": "s" * 20}).status_code == 400


def test_the_service_hands_the_token_back_to_that_name_only(relay, g):
    app, got = relay
    c = TestClient(app, client=HOUSE, follow_redirects=False)
    start = c.get("/v1/google/start", params={"name": got["name"], "r": "r" * 43, "state": "s" * 20})
    sealed = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    back = c.get("/v1/google/callback", params={"code": "the-code", "state": sealed})
    assert back.status_code == 303
    assert back.headers["location"] == f"https://{got['host']}/auth/google/relay#id_token=ID.TOKEN.HERE&state={'s' * 20}"
    assert back.headers["referrer-policy"] == "no-referrer"
    sent = g.posted[0]
    assert sent["client_secret"] == ["s3cret"] and sent["code"] == ["the-code"] and sent["code_verifier"][0]
    forged = sealed[:-2] + ("AA" if not sealed.endswith("AA") else "BB")
    assert c.get("/v1/google/callback", params={"code": "x", "state": forged}).status_code == 400
    stopped = c.get("/v1/google/callback", params={"error": "access_denied", "state": sealed})
    assert stopped.headers["location"].endswith("#error=access_denied&state=" + "s" * 20)


def test_no_client_no_google(tmp_path):
    app = server.create_app(zones.Memory(DOMAIN), tmp_path / "names")
    c = TestClient(app, client=HOUSE)
    assert c.get("/v1/").json()["google_client_id"] == "" and c.get("/v1/google/start").status_code == 404


# ---- the token, on the family's server ----------------------------------------------------------------------------

def test_the_token_is_checked_against_googles_own_keys(g):
    http = httpx.Client(transport=httpx.MockTransport(g))
    nonce = accounts.relay_nonce(HOST, "r" * 43)
    assert accounts.google_token(g.token(nonce), CLIENT, nonce, http) == "pat.miller@example.com"
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    for bad, why in ((g.token(nonce, key=other), "signed by Google"),        # the service's own making
                     (g.token(accounts.relay_nonce("other-place-11." + DOMAIN, "r" * 43)), "started here"),
                     (g.token(nonce, aud="someone-else.apps.googleusercontent.com"), "meant for this site"),
                     (g.token(nonce, exp=time.time() - 10), "too long"),
                     (g.token(nonce, alg="none"), "signed by Google"),
                     ("not.a.token", "could not be read")):
        with pytest.raises(accounts.Problem, match=why):
            accounts.google_token(bad, CLIENT, nonce, http)


@pytest.fixture
def family(cfg, con, g, tmp_path):
    """A server with a free address whose sign-in goes through the service, and Pat as its admin."""
    admin = accounts.add(con, "pat.miller@example.com", None, "admin")
    con.commit()
    cert = tmp_path / "x.crt"
    cert.write_text("x")
    settings.put(con, address.KEY, {"service": f"https://{DOMAIN}", "name": "maple-river-42", "host": HOST,
                                    "token": "t", "mode": "outside", "port": 443, "cert": str(cert),
                                    "google_client": CLIENT})
    settings.save(con, cfg, {"accounts_enabled": True, "google_relay": True, "public_url": f"https://{HOST}",
                             "allowed_hosts": f"{HOST}, photos.example.com"})
    app = webapp.create_app(cfg, google_http=httpx.Client(transport=httpx.MockTransport(g)))
    return app, admin


def test_signing_in_through_the_service(family, g, con):
    app, admin = family
    c = TestClient(app, client=LAN, base_url=f"https://{HOST}", follow_redirects=False)
    assert "Sign in with Google" in c.get("/signin").text
    off = c.get("/auth/google?next=/people")
    to = urlparse(off.headers["location"])
    q = {k: v[0] for k, v in parse_qs(to.query).items()}
    assert to.netloc == DOMAIN and to.path == "/v1/google/start" and q["name"] == "maple-river-42"
    assert c.get("/auth/google/relay").status_code == 200  # the page whose script posts what came after the #
    token = g.token(accounts.relay_nonce(HOST, q["r"]))
    r = c.post("/auth/google/relay", json={"id_token": token, "state": q["state"]}, headers=APP)
    assert r.status_code == 200 and r.json()["go"] == "/people" and "__Host-" + accounts.COOKIE in r.cookies


def test_a_token_for_another_sign_in_or_another_name_is_refused(family, g):
    app, admin = family
    c = TestClient(app, client=LAN, base_url=f"https://{HOST}", follow_redirects=False)
    q = {k: v[0] for k, v in parse_qs(urlparse(c.get("/auth/google").headers["location"]).query).items()}
    elsewhere = g.token(accounts.relay_nonce(f"other-place-11.{DOMAIN}", q["r"]))
    r = c.post("/auth/google/relay", json={"id_token": elsewhere, "state": q["state"]}, headers=APP)
    assert r.status_code == 400 and "started here" in r.json()["detail"]
    stranger = TestClient(app, client=LAN, base_url=f"https://{HOST}")  # without this browser's sign-in cookie
    token = g.token(accounts.relay_nonce(HOST, q["r"]))
    assert stranger.post("/auth/google/relay", json={"id_token": token, "state": q["state"]},
                         headers=APP).status_code == 400


def test_a_sign_in_starts_on_the_free_address(family):
    app, admin = family
    c = TestClient(app, client=LAN, base_url="https://photos.example.com", follow_redirects=False)
    started = c.get("/auth/google?next=/albums")
    assert started.headers["location"] == f"https://{HOST}/auth/google?next=%2Falbums"


# ---- Family accounts -----------------------------------------------------------------------------------------------

def test_turning_it_on_pins_the_services_client(cfg, con, monkeypatch, tmp_path):
    cert = tmp_path / "x.crt"
    cert.write_text("x")
    settings.put(con, address.KEY, {"service": f"https://{DOMAIN}", "name": "maple-river-42", "host": HOST,
                                    "token": "t", "mode": "outside", "port": 443, "cert": str(cert)})
    accounts.add(con, "pat.miller@example.com", None, "admin")
    con.commit()
    settings.save(con, cfg, {"accounts_enabled": True})
    monkeypatch.setattr(address, "_api", lambda base, method, path, *a, **k: {"google_client_id": CLIENT})
    assert "is on" in address.google_relay(con, settings.current(con, cfg), True)
    assert settings.current(con, cfg).google_relay and address.state(con)["google_client"] == CLIENT
    address.google_relay(con, settings.current(con, cfg), False)
    assert not settings.current(con, cfg).google_relay


def test_your_own_client_is_tested_with_google(cfg, con, g):
    settings.save(con, cfg, {"google_client_id": CLIENT, "google_client_secret": "s"})
    http = httpx.Client(transport=httpx.MockTransport(g))
    g.error = "invalid_grant"  # the made-up code: the client itself is right
    assert "knows this client" in accounts.google_check(CLIENT, "s", "https://x/auth/google/callback", http)
    g.error = "invalid_client"
    with pytest.raises(accounts.Problem, match="doesn't know this client"):
        accounts.google_check(CLIENT, "wrong", "https://x/auth/google/callback", http)
    page = TestClient(webapp.create_app(cfg), client=LAN).get("/accounts").text
    assert "console.cloud.google.com/auth/clients/create" in page and 'data-copy="' in page
