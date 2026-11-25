"""Free addresses: the service that gives them out (names/server.py), this server getting and keeping one
(address.py), the Let's Encrypt client's arithmetic (acme.py), and what keeps one family's name from reaching into
another's (cookies, CORP, host names)."""
import base64
import dataclasses
import datetime
import hashlib
import json

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from lineagebox import accounts, acme, address, settings
from lineagebox import demo as demo_mod
from lineagebox.names import server, zone as zones
from lineagebox.web import app as webapp
from tests.conftest import LAN

APP = {"X-Requested-With": "lineagebox"}
HOUSE = ("93.184.216.34", 40000)    # a public IPv4 address: the house, as the service sees it
ACCOUNT = "https://acme-v02.api.letsencrypt.org/acme/acct/123456"
DOMAIN = "ourplace.test"


@pytest.fixture
def names(tmp_path):
    zone = zones.Memory(DOMAIN)
    app = server.create_app(zone, tmp_path / "names")
    return app, zone


def caller(app, where=HOUSE):
    return TestClient(app, client=where)


def register(app, mode="outside", where=HOUSE, **body):
    r = caller(app, where).post("/v1/names", json={"mode": mode, "account": ACCOUNT, **body})
    return r


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ---- the service ---------------------------------------------------------------------------------------------------

def test_a_name_for_outside_points_at_the_house_with_its_own_caa(names):
    app, zone = names
    r = register(app)
    assert r.status_code == 200, r.text
    got = r.json()
    assert server.NAME.fullmatch(got["name"]) and got["host"] == f"{got['name']}.{DOMAIN}"
    assert zone.get(got["host"], "A") == [HOUSE[0]]
    # only this account, only by the DNS check: no certificate for whoever answers on the address's port 80
    assert zone.get(got["host"], "CAA") == [(0, "issue", f"letsencrypt.org; accounturi={ACCOUNT}; "
                                                         "validationmethods=dns-01"), (0, "issuewild", ";")]
    row = app.state.store.run("SELECT * FROM name")[0]
    assert row["token"] == hashlib.sha256(got["token"].encode()).hexdigest()  # never the token itself
    assert HOUSE[0] not in row["asked_by"]  # nor the address that asked


def test_outside_needs_a_public_address_and_home_a_private_one(names):
    app, zone = names
    assert register(app, where=("10.0.0.5", 1)).status_code == 400  # nothing outside to point at
    assert register(app, mode="home", address="93.184.216.34").status_code == 400  # a public one, for "home"
    assert register(app, mode="home", address="127.0.0.1").status_code == 400
    r = register(app, mode="home", address="192.168.1.20")
    assert r.status_code == 200 and zone.get(r.json()["host"], "A") == ["192.168.1.20"]
    assert register(app, mode="home", address="100.101.102.103").status_code == 200  # Tailscale's addresses
    assert register(app, account="https://evil.example/acct/1").status_code == 400


def test_a_name_never_changes_from_home_to_outside(names):
    """DNS rebinding: a page on a public address that then points its name at the home network."""
    app, zone = names
    got = register(app, mode="home", address="192.168.1.20").json()
    c = caller(app)
    assert c.put(f"/v1/names/{got['name']}/address", json={"address": "93.184.216.34"},
                 headers=bearer(got["token"])).status_code == 400
    outside = register(app).json()
    # an outside name takes the address it is called from, never one it is told
    r = caller(app, ("93.184.216.99", 1)).put(f"/v1/names/{outside['name']}/address", json={"address": "10.0.0.1"},
                                              headers=bearer(outside["token"]))
    assert r.status_code == 200 and zone.get(outside["host"], "A") == ["93.184.216.99"] and r.json()["changed"]


def test_the_token_is_the_name(names):
    app, zone = names
    got = register(app).json()
    c = caller(app)
    for headers in ({}, bearer("wrong"), bearer(got["token"] + "x")):
        assert c.get(f"/v1/names/{got['name']}", headers=headers).status_code == 403
    assert c.get("/v1/names/no-such-99", headers=bearer(got["token"])).status_code == 403  # the same either way
    assert c.get(f"/v1/names/{got['name']}", headers=bearer(got["token"])).json()["address"] == HOUSE[0]


def test_the_challenge_record(names):
    app, zone = names
    got = register(app).json()
    c, h = caller(app), bearer(got["token"])
    value = acme.b64(hashlib.sha256(b"x").digest())
    assert c.put(f"/v1/names/{got['name']}/challenge", json={"value": "not a value"}, headers=h).status_code == 400
    assert c.put(f"/v1/names/{got['name']}/challenge", json={"value": value}, headers=h).status_code == 200
    assert zone.get(f"_acme-challenge.{got['host']}", "TXT") == [value]
    assert c.delete(f"/v1/names/{got['name']}/challenge", headers=h).status_code == 200
    assert zone.get(f"_acme-challenge.{got['host']}", "TXT") == []


def test_a_name_given_up_is_never_given_again(names, monkeypatch):
    app, zone = names
    got = register(app).json()
    c, h = caller(app), bearer(got["token"])
    assert c.delete(f"/v1/names/{got['name']}", headers=h).status_code == 200
    assert got["host"] not in zone.names() and c.get(f"/v1/names/{got['name']}", headers=h).status_code == 403
    row = app.state.store.run("SELECT * FROM name")[0]  # only the name and its dates are kept
    assert (row["token"], row["address"], row["account"], row["asked_by"]) == ("", None, "", "")
    picks = iter([got["name"], "fresh-name-42"])
    monkeypatch.setattr(server.words, "new_name", lambda: next(picks))
    assert register(app, where=("93.184.216.40", 1)).json()["name"] == "fresh-name-42"


def test_limits(names, monkeypatch):
    app, zone = names
    for _ in range(server.PER_DAY):
        assert register(app).status_code == 200
    assert register(app).status_code == 429  # one address, a day
    monkeypatch.setattr(server, "PER_WEEK", server.PER_DAY + 1)
    assert register(app, where=("93.184.216.41", 1)).status_code == 200
    assert register(app, where=("93.184.216.42", 1)).status_code == 429  # everyone, a week


def test_a_quiet_name_stops_resolving_and_comes_back(names):
    app, zone = names
    got = register(app).json()
    long_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=server.QUIET_DAYS + 1))
    app.state.store.run("UPDATE name SET seen_at = ?", (long_ago.isoformat(timespec="seconds"),))
    assert app.state.sweep() == 1 and got["host"] not in zone.names()
    r = caller(app).put(f"/v1/names/{got['name']}/address", json={}, headers=bearer(got["token"]))
    assert r.status_code == 200 and zone.get(got["host"], "A") == [HOUSE[0]] and zone.get(got["host"], "CAA")


def test_the_caller_behind_nginx(names):
    app, zone = names
    via_nginx = TestClient(app, client=("172.31.241.1", 1))
    assert via_nginx.get("/v1/address", headers={"X-Real-IP": "93.184.216.7"}).json()["address"] == "93.184.216.7"
    # from anywhere else the header is the caller's own words, and not believed
    assert caller(app).get("/v1/address", headers={"X-Real-IP": "1.2.3.4"}).json()["address"] == HOUSE[0]


def test_reaching_checks_public_addresses_only():
    assert not server.reach("192.168.1.20", "x.ourplace.test", 443)["reachable"]
    assert not server.reach("127.0.0.1", "x.ourplace.test", 443)["reachable"]


# ---- the Let's Encrypt client -----------------------------------------------------------------------------------------

def test_the_dns_value_and_the_signature():
    """RFC 8555 8.1 and 8.4: the key authorization is the token, a dot and the key's thumbprint (RFC 7638); the TXT
    record is its SHA-256. JWS ES256 signatures are r and s, 32 bytes each."""
    key_pem = acme.new_key_pem()
    client = acme.Client.__new__(acme.Client)
    client.key, client.kid, client.nonce = serialization.load_pem_private_key(key_pem.encode(), None), "", "n0nce"
    jwk = client.jwk()
    thumb = acme.b64(hashlib.sha256(json.dumps({"crv": "P-256", "kty": "EC", "x": jwk["x"], "y": jwk["y"]},
                                               separators=(",", ":")).encode()).digest())
    assert client.dns_value("tok") == acme.b64(hashlib.sha256(f"tok.{thumb}".encode()).digest())
    jws = client._sign("https://example.test/new", {"a": 1})
    raw = base64.urlsafe_b64decode(jws["signature"] + "==")
    signature = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    client.key.public_key().verify(signature, f"{jws['protected']}.{jws['payload']}".encode(),
                                   ec.ECDSA(hashes.SHA256()))  # raises if wrong
    assert json.loads(base64.urlsafe_b64decode(jws["protected"] + "=="))["jwk"] == jwk  # before an account: the key


# ---- this server getting one ---------------------------------------------------------------------------------------

def self_signed(host: str, days: int = 90) -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    start = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(start)
            .not_valid_after(start + datetime.timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
            .sign(key, hashes.SHA256()))
    return (cert.public_bytes(serialization.Encoding.PEM).decode(),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode())


class FakeLetsEncrypt:
    """acme.Client as address.py uses it: an account, then a certificate once the TXT record is published."""
    def __init__(self, zone):
        self.zone, self.issued = zone, []

    def __call__(self, directory, key_pem, kid=""):
        fake = self

        class Client:
            def account(self):
                return ACCOUNT

            def certificate(self, host, publish, unpublish):
                publish(acme.b64(hashlib.sha256(host.encode()).digest()))
                assert fake.zone.get(f"_acme-challenge.{host}", "TXT")  # what Let's Encrypt would look up
                unpublish()
                fake.issued.append(host)
                return self_signed(host)
        return Client()


@pytest.fixture
def free(names, cfg, monkeypatch):
    app, zone = names
    monkeypatch.setattr(address, "_http", lambda base: TestClient(app, client=HOUSE, base_url=base))
    le = FakeLetsEncrypt(zone)
    monkeypatch.setattr(address, "_acme", le)
    cfg = dataclasses.replace(cfg, address_service="https://ourplace.test", accounts_enabled=True)
    return app, zone, le, cfg


def test_getting_one_for_outside(free, con):
    app, zone, le, cfg = free
    s = address.get(con, cfg, "outside")
    host = s["host"]
    assert le.issued == [host] and zone.get(f"_acme-challenge.{host}", "TXT") == []
    now = settings.current(con, cfg)
    assert host in now.allowed_hosts and now.public_url == f"https://{host}"
    assert s["serving"] and now.tls_cert == s["cert"] and now.tls_key == s["key"]  # no proxy, no certificate: its own
    assert (cfg.data_dir / "certs" / f"{host}.crt").is_file()
    view = address.view(con, cfg)
    assert view["url"] == f"https://{host}" and "token" not in view and "acme_key" not in view
    with pytest.raises(address.Problem, match="has a free address already"):
        address.get(con, cfg, "outside")


def test_outside_needs_family_accounts(free, con):
    app, zone, le, cfg = free
    with pytest.raises(address.Problem, match="family accounts"):
        address.get(con, dataclasses.replace(cfg, accounts_enabled=False), "outside")
    assert not zone.names()


def test_behind_a_reverse_proxy_the_proxy_gets_the_certificate(free, con):
    app, zone, le, cfg = free
    settings.save(con, cfg, {"trusted_proxies": "172.18.0.3", "public_url": "https://photos.example.com"})
    s = address.get(con, cfg, "outside")
    now = settings.current(con, cfg)
    assert not s["serving"] and now.tls_cert == "" and now.public_url == "https://photos.example.com"
    assert address.make_main(con, cfg) is False  # the proxy still does HTTPS
    assert settings.current(con, cfg).public_url == f"https://{s['host']}"


def test_keeping_it_up_and_giving_it_up(free, con, monkeypatch):
    app, zone, le, cfg = free
    s = address.get(con, cfg, "outside")
    monkeypatch.setattr(address, "_http", lambda base: TestClient(app, client=("93.184.216.200", 1), base_url=base))
    address.keep_up(con, cfg)  # the house's address changed
    assert zone.get(s["host"], "A") == ["93.184.216.200"] and address.state(con)["address"] == "93.184.216.200"
    st = address.state(con)
    st["cert_from"], st["cert_until"] = "2020-01-01T00:00:00", "2020-03-31T00:00:00"
    settings.put(con, address.KEY, st)
    address.keep_up(con, cfg)  # two thirds through: renewed
    assert le.issued == [s["host"]] * 2 and not settings.problems(con).get("address")
    address.release(con, cfg)
    assert s["host"] not in zone.names() and address.state(con) is None
    assert s["host"] not in settings.current(con, cfg).allowed_hosts and not (cfg.data_dir / "certs" /
                                                                              f"{s['host']}.crt").exists()


def test_the_settings_page_offers_it_and_the_demo_locks_it(free, con):
    app, zone, le, cfg = free
    page = TestClient(webapp.create_app(dataclasses.replace(cfg, accounts_enabled=False)), client=LAN).get("/settings")
    assert "Get a free address" in page.text and "a name of your own</button>" in page.text  # the other way
    demo = dataclasses.replace(cfg, demo_enabled=True)  # its admins look, and change nothing: no names from it
    for path in ("/api/address", "/api/address/check", "/api/address/main", "/api/address/renew",
                 "/api/address/release"):
        assert demo_mod.closed(demo, path, "POST") and not demo_mod.closed(demo, path, "GET")


# ---- one family's name can't reach into another's ------------------------------------------------------------------

def test_shared_domain_wildcards_are_refused(cfg):
    cfg = dataclasses.replace(cfg, address_service="https://ourplace.here")
    with pytest.raises(settings.Invalid, match="every family's free address"):
        settings.host_names("*.ourplace.here", cfg)
    assert settings.host_names("maple-river-42.ourplace.here, *.example.com", cfg) == (
        "maple-river-42.ourplace.here", "*.example.com")


def test_no_other_site_may_load_what_lineagebox_answers(cfg):
    r = TestClient(webapp.create_app(cfg), client=LAN).get("/signin")
    assert r.headers["cross-origin-resource-policy"] == "same-origin"


def test_a_session_from_before_the_host_prefix_still_counts(cfg, con):
    """1.11 set the session cookie without __Host- over HTTPS: it signs in until it runs out, so the change signs
    nobody out; a sign-in from now on sets the __Host- one (test_signin)."""
    admin = accounts.add(con, "pat.miller@example.com", None, "admin")
    con.commit()
    settings.save(con, cfg, {"accounts_enabled": True, "public_url": "https://photos.example.com",
                             "allowed_hosts": "photos.example.com"})
    c = TestClient(webapp.create_app(cfg), client=LAN, base_url="https://photos.example.com", follow_redirects=False)
    assert c.get("/settings").status_code != 200  # nobody signed in
    c.cookies.set(accounts.COOKIE, accounts.new_session(con, admin, 30))
    assert c.get("/settings").status_code == 200


# ---- when Let's Encrypt is busy -------------------------------------------------------------------------------------

def test_lets_encrypt_busy_is_waited_out(monkeypatch):
    """"Service busy; retry later" (503) is asked again after a wait, not given up on."""
    import httpx
    monkeypatch.setattr(acme.time, "sleep", lambda s: None)
    asked = {"directory": 0, "account": 0}

    def answer(request):
        if request.url.path == "/directory":
            asked["directory"] += 1
            if asked["directory"] == 1:
                return httpx.Response(503, json={"type": "urn:ietf:params:acme:error:serverInternal",
                                                 "detail": "Service busy; retry later."}, headers={"Retry-After": "2"})
            return httpx.Response(200, json={"newNonce": "https://le.test/nonce", "newAccount": "https://le.test/acct"})
        if request.url.path == "/nonce":
            return httpx.Response(200, headers={"Replay-Nonce": "n1"})
        asked["account"] += 1
        if asked["account"] < 3:
            return httpx.Response(503, json={"detail": "Service busy; retry later."}, headers={"Replay-Nonce": "n2"})
        return httpx.Response(201, json={}, headers={"Location": "https://le.test/acct/7", "Replay-Nonce": "n3"})

    client = acme.Client("https://le.test/directory", acme.new_key_pem(),
                         http=httpx.Client(transport=httpx.MockTransport(answer)))
    assert client.account() == "https://le.test/acct/7" and asked == {"directory": 2, "account": 3}


def test_a_name_without_its_certificate_yet_is_kept_and_finished(free, con, monkeypatch):
    app, zone, le, cfg = free
    real = le.__call__

    def busy(directory, key_pem, kid=""):
        client = real(directory, key_pem, kid)

        def refused(host, publish, unpublish):
            raise acme.Problem("Let's Encrypt refused: Service busy; retry later.")
        client.certificate = refused
        return client
    monkeypatch.setattr(address, "_acme", busy)
    got = address.get(con, cfg, "outside")
    assert "Service busy" in got["pending"] and not got["serving"]
    assert address.state(con)["host"] == got["host"] and "is yours" in settings.problems(con)["address"]["message"]
    assert "pending" not in settings.get(con, address.KEY)  # what get() answered, not what is kept
    monkeypatch.setattr(address, "_acme", le)
    address.keep_up(con, cfg)  # tried a moment ago: not again yet
    assert le.issued == []
    st = settings.get(con, address.KEY)
    st["renew_tried_at"] = "2020-01-01T00:00:00"
    settings.put(con, address.KEY, st)
    address.keep_up(con, cfg)  # by itself, a while later
    assert le.issued == [got["host"]] and not settings.problems(con).get("address")
    now = settings.current(con, cfg)
    assert got["host"] in now.allowed_hosts and now.tls_cert  # put to use as get() would have


def test_asking_again_finishes_the_same_name(free, con, monkeypatch):
    """Get a free address pressed again, after the name came but its certificate didn't: the same name, finished."""
    app, zone, le, cfg = free
    monkeypatch.setattr(address, "_certificate", lambda *a, **k: (_ for _ in ()).throw(acme.Problem("busy")))
    got = address.get(con, cfg, "outside")
    monkeypatch.undo()
    monkeypatch.setattr(address, "_http", lambda base: TestClient(app, client=HOUSE, base_url=base))
    monkeypatch.setattr(address, "_acme", le)
    again = address.get(con, cfg, "outside")
    assert again["host"] == got["host"] and not again.get("pending") and len(app.state.store.run("SELECT * FROM name")) == 1


def test_settings_shows_one_way_to_a_name(free, con):
    """Address and HTTPS shows one way: a free address or a name of your own, chosen by two buttons. Before a choice,
    a new server sees the free one, one with a name of its own sees that, and one with a free address sees it."""
    app, zone, le, cfg = free
    cfg = dataclasses.replace(cfg, accounts_enabled=False)  # an admin at home, without signing in
    c = TestClient(webapp.create_app(cfg), client=LAN)

    def shown():
        page = c.get("/settings").text
        return {way for way in ("free-address", "own-name") if f'<div id="{way}">' in page}
    assert shown() == {"free-address"}
    settings.save(con, cfg, {"allowed_hosts": "photos.example.com"})
    assert shown() == {"own-name"}
    address.get(con, cfg, "home", "192.168.7.50", 8000)
    assert shown() == {"free-address"} and 'name="tls_cert"' not in c.get("/settings").text  # its own certificate
    assert c.post("/api/address/way", json={"way": "own"}, headers=APP).json() == {"reload": True}
    assert shown() == {"own-name"} and 'name="tls_cert"' in c.get("/settings").text
    assert c.post("/api/address/way", json={"way": "nope"}, headers=APP).status_code == 400


def test_choosing_the_free_address_serves_its_certificate(free, con):
    app, zone, le, cfg = free
    s = address.get(con, cfg, "outside")
    settings.save(con, cfg, {"tls_cert": "", "tls_key": ""})  # as if another certificate were served instead
    assert address.choose(con, settings.current(con, cfg), "own") is False
    assert address.choose(con, settings.current(con, cfg), "free") is True
    assert settings.current(con, cfg).tls_cert == s["cert"]


def test_names_to_choose_from(names):
    app, zone = names
    c = caller(app)
    offered = c.get("/v1/names/suggest", params={"count": 5}).json()["names"]
    assert len(set(offered)) == 5 and all(server.words.valid(n) for n in offered)
    got = register(app, name=offered[2]).json()
    assert got["name"] == offered[2]
    assert register(app, where=("93.184.216.50", 1), name=offered[2]).status_code == 409  # taken
    assert register(app, where=("93.184.216.51", 1), name="evil-bank").status_code == 400  # not one of the words


def test_changing_to_another_name(free, con):
    app, zone, le, cfg = free
    old = address.get(con, cfg, "outside")
    st = address.state(con)
    st["google_client"] = "123.apps.googleusercontent.com"
    settings.put(con, address.KEY, st)
    wanted = caller(app).get("/v1/names/suggest").json()["names"][0]
    new = address.change(con, settings.current(con, cfg), wanted)
    assert new["host"] == f"{wanted}.{DOMAIN}" and new["was"] == old["host"]
    now = settings.current(con, cfg)
    assert new["host"] in now.allowed_hosts and old["host"] not in now.allowed_hosts
    assert now.public_url == f"https://{new['host']}" and now.tls_cert == new["cert"] and new["serving"]
    assert address.state(con)["google_client"] == "123.apps.googleusercontent.com"
    assert old["host"] not in zone.names() and zone.get(new["host"], "A") == [HOUSE[0]]
    assert not (cfg.data_dir / "certs" / f"{old['host']}.crt").exists()


def test_a_change_that_cant_finish_changes_nothing(free, con, monkeypatch):
    app, zone, le, cfg = free
    old = address.get(con, cfg, "outside")
    monkeypatch.setattr(address, "_certificate", lambda *a, **k: (_ for _ in ()).throw(acme.Problem("busy")))
    with pytest.raises(acme.Problem):
        address.change(con, settings.current(con, cfg))
    assert address.state(con)["host"] == old["host"] and zone.names() == {old["host"]}
    assert settings.current(con, cfg).public_url == f"https://{old['host']}"


def test_names_are_two_words_and_those_with_a_number_still_count(names):
    """Two plain words, easy to say (the user's call, 2026-11-24); a name given before, with a number, still works."""
    app, zone = names
    got = register(app).json()
    assert got["name"].count("-") == 1 and got["name"].replace("-", "").isalpha()
    assert all(server.NAME.fullmatch(n) for n in ("maple-river", "maple-river-42"))
    assert not any(server.NAME.fullmatch(n) for n in ("maple-river-4", "maple", "maple-river-42-1", "Maple-river"))


def test_show_names_to_choose_from_on_the_page(free, con):
    """The button posts nothing (no data-body), as app.js sends it; the names come back as radio buttons."""
    app, zone, le, cfg = free
    c = TestClient(webapp.create_app(dataclasses.replace(cfg, accounts_enabled=False)), client=LAN)
    r = c.post("/api/address/suggest", headers=APP)
    assert r.status_code == 200, r.text
    assert r.json()["html"].count('type="radio" name="name"') == 5 and ".ourplace.test</label>" in r.json()["html"]
    chosen = r.json()["html"].split('value="')[1].split('"')[0]
    got = c.post("/api/address", json={"mode": "home", "home": "192.168.7.50", "port": 8000, "name": chosen},
                 headers=APP)
    assert got.status_code == 200, got.text
    assert address.state(con)["name"] == chosen
