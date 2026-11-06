"""Passkeys: an admin's link makes the first, it signs in, more are added soon after signing in, and they are taken
away. A pretend phone (Phone) makes and uses real P-256 passkeys, answering as a browser would."""
import base64
import dataclasses
import hashlib
import json
import os
import re
import struct
from datetime import datetime, timedelta

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from lineagebox import accounts, cli, passkeys, people, settings
from lineagebox.web import app as webapp
from lineagebox.web import site as site_module
from tests.conftest import LAN

APP = {"X-Requested-With": "lineagebox"}
OUTSIDE = ("203.0.113.9", 50000)
DOMAIN = "photos.example.com"
ORIGIN = f"https://{DOMAIN}"
CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
READY = {"public_url": ORIGIN, "allowed_hosts": [DOMAIN], "trusted_proxies": "172.18.0.0/16"}


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Phone:
    """The passkeys on one phone, answering navigator.credentials as its browser would (attestation "none")."""

    def __init__(self, origin: str = ORIGIN, rp_id: str = DOMAIN):
        self.origin, self.rp_id = origin, rp_id
        self.unlocked, self.synced, self.counts, self.count = True, True, False, 0
        self.keys: dict[bytes, tuple] = {}  # credential id -> (private key, user handle)

    def _flags(self, made: bool) -> bytes:
        # user present, verified (unlocked), backed up and eligible (synced), attested credential data (made)
        return bytes([0x01 | (0x04 if self.unlocked else 0) | (0x18 if self.synced else 0) | (0x40 if made else 0)])

    def create(self, options: dict) -> dict:
        key, cred_id = ec.generate_private_key(ec.SECP256R1()), os.urandom(16)
        self.keys[cred_id] = (key, unb64(options["user"]["id"]))
        n = key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})
        auth = (hashlib.sha256(self.rp_id.encode()).digest() + self._flags(True) + struct.pack(">I", 0) + bytes(16)
                + struct.pack(">H", len(cred_id)) + cred_id + cose)
        client = json.dumps({"type": "webauthn.create", "challenge": options["challenge"], "origin": self.origin,
                             "crossOrigin": False}).encode()
        return {"id": b64(cred_id), "rawId": b64(cred_id), "type": "public-key", "authenticatorAttachment": "platform",
                "clientExtensionResults": {},
                "response": {"clientDataJSON": b64(client), "transports": ["internal", "hybrid"],
                             "attestationObject": b64(cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth}))}}

    def get(self, options: dict, cred_id: bytes | None = None) -> dict:
        cred_id = cred_id or next(iter(self.keys))
        key, user = self.keys[cred_id]
        if self.counts:
            self.count += 1
        auth = hashlib.sha256(self.rp_id.encode()).digest() + self._flags(False) + struct.pack(">I", self.count)
        client = json.dumps({"type": "webauthn.get", "challenge": options["challenge"],
                             "origin": self.origin}).encode()
        signature = key.sign(auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": b64(cred_id), "rawId": b64(cred_id), "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": b64(client), "authenticatorData": b64(auth), "signature": b64(signature),
                             "userHandle": b64(user)}}


@pytest.fixture
def setup(con, cfg, monkeypatch):
    monkeypatch.setattr(site_module, "SIGNED_IN_FOR", 0)  # sessions changed in the catalog count at once
    admin = accounts.add(con, "daniel.miller@example.com", people.person_id(con, "Daniel Miller"), "admin")
    member = accounts.add(con, "katherine.miller@example.com", people.person_id(con, "Katherine Miller"))
    con.commit()
    settings.save(con, cfg, {"accounts_enabled": True, **READY})
    return {"admin": admin, "member": member, "cfg": cfg, "app": webapp.create_app(cfg)}


def browser(setup, base: str = ORIGIN, where=OUTSIDE, **headers) -> TestClient:
    return TestClient(setup["app"], client=where, base_url=base, follow_redirects=False, headers=headers)


def signed_in(setup, con, account_id: int, **options) -> TestClient:
    c = browser(setup, **options)
    c.cookies.set(accounts.COOKIE, accounts.new_session(con, account_id, 30))
    return c


def make_link(admin: TestClient, account_id: int) -> str:
    r = admin.post(f"/api/accounts/{account_id}/invite", headers=APP)
    assert r.status_code == 200, r.text
    link = re.search(r'data-copy="([^"]+)"', r.json()["html"]).group(1)
    assert link.startswith(f"{ORIGIN}/auth/invite#")
    return link.split("#", 1)[1]


def use_link(c: TestClient, phone: Phone, token: str):
    r = c.post("/auth/invite/start", json={"token": token}, headers=APP)
    assert r.status_code == 200, r.text
    return c.post("/auth/invite/finish", json={"credential": phone.create(r.json()["options"])}, headers=APP)


def sign_in(c: TestClient, phone: Phone, next_page: str = "/people", cred_id: bytes | None = None):
    r = c.post("/auth/passkey/start", json={"next": next_page}, headers=APP)
    assert r.status_code == 200, r.text
    return c.post("/auth/passkey/finish", json={"credential": phone.get(r.json()["options"], cred_id)}, headers=APP)


def katherines_phone(setup, con) -> Phone:
    phone = Phone()
    r = use_link(browser(setup), phone, make_link(signed_in(setup, con, setup["admin"]), setup["member"]))
    assert r.status_code == 200, r.text
    return phone


def test_a_link_makes_a_passkey_that_signs_in(setup, con):
    admin = signed_in(setup, con, setup["admin"])
    made = admin.post(f"/api/accounts/{setup['member']}/invite", headers=APP).json()["html"]
    assert "<svg" in made and "katherine.miller@example.com" in made  # a QR code for the phone's camera
    token = re.search(r'data-copy="[^"#]+#([^"]+)"', made).group(1)
    kept = con.execute("SELECT * FROM passkey_invite").fetchall()
    assert len(kept) == 1 and kept[0]["token_hash"] == hashlib.sha256(token.encode()).hexdigest()  # never the token
    assert datetime.fromisoformat(kept[0]["expires_at"]) - datetime.now() > timedelta(days=6, hours=23)

    phone, c = Phone(), browser(setup, **{"user-agent": CHROME})
    page = c.get("/auth/invite")
    assert page.status_code == 200 and "Make my passkey" in page.text and "data-invite" in page.text
    assert c.post("/auth/invite/check", json={"token": token}, headers=APP).json() == {
        "who": "Katherine Miller", "email": "katherine.miller@example.com"}
    options = c.post("/auth/invite/start", json={"token": token}, headers=APP).json()["options"]
    assert options["rp"]["id"] == DOMAIN and options["user"]["name"] == "katherine.miller@example.com"
    assert options["authenticatorSelection"]["residentKey"] == "required"  # nobody types an email
    assert options["authenticatorSelection"]["userVerification"] == "required"  # the passkey alone signs in
    assert options["attestation"] == "none" and options["excludeCredentials"] == []
    assert b"katherine" not in unb64(options["user"]["id"]) and len(unb64(options["user"]["id"])) == 32
    r = c.post("/auth/invite/finish", json={"credential": phone.create(options)}, headers=APP)
    assert r.status_code == 200 and r.json()["go"] == "/passkeys?made=1" and accounts.COOKIE in r.cookies
    assert c.post("/auth/invite/check", json={"token": token}, headers=APP).status_code == 404  # used up
    page = c.get("/passkeys?made=1").text
    assert "Your passkey is made" in page and "Chrome on Windows" in page and "kept in a password manager" in page
    assert "you signed in with it here" in page

    # another day, on the same phone: no email typed, the passkey says who it is
    later = browser(setup)
    r = sign_in(later, phone)
    assert r.status_code == 200 and r.json()["go"] == "/people" and accounts.COOKIE in r.cookies
    assert later.get("/people").status_code == 200
    row = con.execute("SELECT * FROM passkey").fetchone()
    assert row["last_used_at"] and row["synced"] == 1 and row["rp_id"] == DOMAIN
    assert json.loads(row["transports"]) == ["internal", "hybrid"]
    # the sign-in page offers passkeys now that someone has one
    assert 'data-passkey="signin"' in browser(setup).get("/signin").text


def test_what_a_passkey_sign_in_turns_away(setup, con):
    phone = katherines_phone(setup, con)
    c = browser(setup)

    def refused(answer_from: Phone, why: str = passkeys.NOT_CHECKED):
        r = sign_in(c, answer_from)
        assert r.status_code == 400 and why in r.json()["detail"], r.text
        assert accounts.COOKIE not in r.cookies

    elsewhere = Phone(origin="https://photos.example.net")  # another site's page asked for it
    elsewhere.keys = phone.keys
    refused(elsewhere)
    other_name = Phone(rp_id="example.net")
    other_name.keys = phone.keys
    refused(other_name)
    phone.unlocked = False  # pressed, but not unlocked: not enough when the passkey alone signs in
    refused(phone)
    phone.unlocked = True
    # someone else's account named by the phone
    cred_id = next(iter(phone.keys))
    phone.keys[cred_id] = (phone.keys[cred_id][0], os.urandom(32))
    refused(phone)
    phone.keys[cred_id] = (phone.keys[cred_id][0], bytes(con.execute("SELECT passkey_user FROM account WHERE id = ?",
                                                                     (setup["member"],)).fetchone()[0]))
    # an answer sent twice: the start it answered is used up
    options = c.post("/auth/passkey/start", json={"next": "/"}, headers=APP).json()["options"]
    answer = phone.get(options)
    assert c.post("/auth/passkey/finish", json={"credential": answer}, headers=APP).status_code == 200
    again = c.post("/auth/passkey/finish", json={"credential": answer}, headers=APP)
    assert again.status_code == 400 and "took too long" in again.json()["detail"]
    # an answer to an older start
    first = c.post("/auth/passkey/start", json={"next": "/"}, headers=APP).json()["options"]
    c.post("/auth/passkey/start", json={"next": "/"}, headers=APP)
    assert c.post("/auth/passkey/finish", json={"credential": phone.get(first)}, headers=APP).status_code == 400
    # a key that counts, going back: a copy of it
    phone.synced, phone.counts = False, True
    assert sign_in(c, phone).status_code == 200
    phone.count = 0
    refused(phone)
    # its account switched off; the passkey taken away
    phone.count = 5
    accounts.update(con, setup["member"], disabled=True)
    con.commit()
    refused(phone, "switched off")
    accounts.update(con, setup["member"], disabled=False)
    con.execute("DELETE FROM passkey")
    con.commit()
    refused(phone, "isn't one of this site's")
    # nor on another address: the browser would refuse it there anyway
    home = browser(setup, base="http://192.168.1.20:8000", where=LAN)
    r = home.post("/auth/passkey/start", json={"next": "/"}, headers=APP)
    assert r.status_code == 400 and f"work only at {ORIGIN}" in r.json()["detail"]


def test_passkeys_need_https_and_one_exact_name(cfg):
    ready = dataclasses.replace(cfg, public_url=ORIGIN, allowed_hosts=(DOMAIN,), trusted_proxies=("172.18.0.0/16",))
    assert passkeys.why_not(ready) is None and passkeys.domain(ready) == DOMAIN
    for change, words in [({"public_url": ""}, "Address from outside"),
                          ({"public_url": "https://10.0.0.2"}, "not an address like 10.0.0.2"),
                          ({"trusted_proxies": ()}, "Passkeys need HTTPS"),
                          ({"trusted_proxies": (), "tls_cert": "/nowhere/cert.pem"}, "can't be read"),
                          ({"allowed_hosts": (DOMAIN, "*.example.com")}, "*.example.com, a name with *"),
                          ({"allowed_hosts": ("photos.example.org",)}, f"List {DOMAIN} under Host names")]:
        assert words in passkeys.why_not(dataclasses.replace(ready, **change)), change
    assert passkeys.origin(dataclasses.replace(ready, public_url=f"{ORIGIN}:443")) == ORIGIN
    assert passkeys.origin(dataclasses.replace(ready, public_url=f"{ORIGIN}:8443")) == f"{ORIGIN}:8443"


def test_the_pages_say_what_passkeys_need(setup, con, cfg):
    admin = signed_in(setup, con, setup["admin"])
    assert "Ready, for photos.example.com" in admin.get("/accounts").text
    assert "Ready, for photos.example.com" in admin.get("/settings").text
    settings.save(con, cfg, {"trusted_proxies": ""})  # plain HTTP
    for path in ("/accounts", "/settings"):
        page = admin.get(path).text
        assert "Passkeys can't be set up yet" in page and "Passkeys need HTTPS" in page, path
    assert "<button type=\"button\" disabled>Make a passkey link</button>" in admin.get("/accounts").text
    r = admin.post(f"/api/accounts/{setup['member']}/invite", headers=APP)
    assert r.status_code == 400 and "Passkeys need HTTPS" in r.json()["detail"]
    r = admin.post("/api/passkeys/start", headers=APP)
    assert r.status_code == 400 and "Passkeys need HTTPS" in r.json()["detail"]
    settings.save(con, cfg, {"trusted_proxies": "172.18.0.0/16", "allowed_hosts": [DOMAIN, "*.example.com"]})
    assert "a name with *" in admin.get("/settings").text
    r = admin.post(f"/api/accounts/{setup['member']}/invite", headers=APP)
    assert r.status_code == 400 and "*.example.com" in r.json()["detail"]
    # the link's page: the reason is for admins, the family are told to ask
    assert "a name with *" not in browser(setup).get("/auth/invite").text
    assert "passkeys aren't set up on this site" in browser(setup).get("/auth/invite").text


def test_adding_a_passkey_soon_after_signing_in(setup, con):
    """Within 3 hours of the account's first sign-in, else 15 minutes of signing in."""
    def session_made(c, ago: timedelta):
        con.execute("UPDATE session SET created_at = ? WHERE token_hash = ?",
                    ((datetime.now() - ago).isoformat(timespec="seconds"),
                     hashlib.sha256(c.cookies[accounts.COOKIE].encode()).hexdigest()))
        con.commit()

    first = signed_in(setup, con, setup["member"])  # her first sign-in ever
    session_made(first, timedelta(hours=2, minutes=50))
    assert first.post("/api/passkeys/start", headers=APP).status_code == 200
    session_made(first, timedelta(hours=3, minutes=10))
    r = first.post("/api/passkeys/start", headers=APP)
    assert r.status_code == 400 and "sign out and in again" in r.json()["detail"]
    assert "sign out and in again" in first.get("/passkeys").text

    later = signed_in(setup, con, setup["member"])  # not her first
    session_made(later, timedelta(minutes=20))
    assert later.post("/api/passkeys/start", headers=APP).status_code == 400
    session_made(later, timedelta(minutes=10))
    phone, laptop = Phone(), Phone()
    for device in (phone, laptop):
        options = later.post("/api/passkeys/start", headers=APP).json()["options"]
        r = later.post("/api/passkeys/finish", json={"credential": device.create(options)}, headers=APP)
        assert r.status_code == 200 and r.json() == {"reload": True}
    # each device makes one: the browser is told which the account has already
    options = later.post("/api/passkeys/start", headers=APP).json()["options"]
    assert {c["id"] for c in options["excludeCredentials"]} == {b64(k) for k in (*phone.keys, *laptop.keys)}
    assert len(passkeys.of_account(con, setup["member"])) == 2
    # not on another address: the browser makes passkeys only for their name
    home = signed_in(setup, con, setup["member"], base="http://192.168.1.20:8000", where=LAN)
    assert home.post("/api/passkeys/start", headers=APP).status_code == 400
    assert f'href="{ORIGIN}/passkeys"' in home.get("/passkeys").text


def test_taking_a_passkey_away_signs_out_where_it_signed_in(setup, con):
    phone = katherines_phone(setup, con)
    with_it = browser(setup)
    assert sign_in(with_it, phone).status_code == 200
    with_google = signed_in(setup, con, setup["member"])  # a session of her own, not from the passkey
    mine = passkeys.of_account(con, setup["member"])[0]["id"]
    # someone else's passkey is not found for her: the admin's
    admin_phone = Phone()
    admin = signed_in(setup, con, setup["admin"])
    options = admin.post("/api/passkeys/start", headers=APP).json()["options"]
    assert admin.post("/api/passkeys/finish", json={"credential": admin_phone.create(options)},
                      headers=APP).status_code == 200
    theirs = passkeys.of_account(con, setup["admin"])[0]["id"]
    for url, body in ((f"/api/passkeys/{theirs}/remove", None), (f"/api/passkeys/{theirs}/rename", {"name": "x"})):
        assert with_google.post(url, json=body, headers=APP).status_code == 404
    assert passkeys.get(con, theirs)["name"] != "x"
    assert with_google.post(f"/api/passkeys/{mine}/rename", json={"name": "Katherine's phone"},
                            headers=APP).status_code == 200
    assert "Katherine&#39;s phone" in with_google.get("/passkeys").text
    assert "Katherine&#39;s phone" in admin.get("/accounts").text  # the admin sees it on her account
    assert with_google.post(f"/api/passkeys/{mine}/remove", headers=APP).status_code == 200
    assert with_it.get("/people").status_code == 303  # signed out where the passkey signed her in
    assert with_google.get("/people").status_code == 200
    assert sign_in(browser(setup), phone).status_code == 400
    # an admin takes anyone's away
    assert admin.post(f"/api/passkeys/{theirs}/remove", headers=APP).status_code == 200
    assert passkeys.get(con, theirs) is None


def test_links_work_once_and_run_out(setup, con):
    admin = signed_in(setup, con, setup["admin"])
    first = make_link(admin, setup["member"])
    second = make_link(admin, setup["member"])  # replaces the first
    gone = lambda token: browser(setup).post("/auth/invite/check", json={"token": token}, headers=APP)
    assert gone(first).status_code == 404 and "a newer one was made" in gone(first).json()["detail"]
    # two pages with one link: only one makes a passkey
    a, b = browser(setup), browser(setup)
    starts = [c.post("/auth/invite/start", json={"token": second}, headers=APP).json()["options"] for c in (a, b)]
    assert a.post("/auth/invite/finish", json={"credential": Phone().create(starts[0])}, headers=APP).status_code == 200
    assert b.post("/auth/invite/finish", json={"credential": Phone().create(starts[1])}, headers=APP).status_code == 404
    assert len(passkeys.of_account(con, setup["member"])) == 1
    # stopped, run out
    third = make_link(admin, setup["member"])
    assert "A link is out, until" in admin.get("/accounts").text
    admin.post(f"/api/accounts/{setup['member']}/invite/cancel", headers=APP)
    assert gone(third).status_code == 404
    fourth = make_link(admin, setup["member"])
    con.execute("UPDATE passkey_invite SET expires_at = ?", ((datetime.now() - timedelta(minutes=1)).isoformat(),))
    con.commit()
    assert gone(fourth).status_code == 404
    # an admin's account opens everything: its link lasts a day
    make_link(admin, setup["admin"])
    until = con.execute("SELECT expires_at FROM passkey_invite WHERE account_id = ?", (setup["admin"],)).fetchone()[0]
    assert timedelta(hours=23) < datetime.fromisoformat(until) - datetime.now() <= timedelta(hours=24)
    # none for an account switched off, and a member can't make one
    accounts.update(con, setup["member"], disabled=True)
    con.commit()
    r = admin.post(f"/api/accounts/{setup['member']}/invite", headers=APP)
    assert r.status_code == 400 and "switched off" in r.json()["detail"]
    accounts.update(con, setup["member"], disabled=False)
    con.commit()
    member = signed_in(setup, con, setup["member"])
    assert member.post(f"/api/accounts/{setup['member']}/invite", headers=APP).status_code == 404


def test_an_admins_passkey_lets_accounts_be_turned_on(con, cfg):
    """The first admin without Google: a link made at home while accounts are off, then accounts on."""
    admin = accounts.add(con, "daniel.miller@example.com", people.person_id(con, "Daniel Miller"), "admin")
    con.commit()
    settings.save(con, cfg, READY)
    home = TestClient(webapp.create_app(cfg), client=LAN, base_url=ORIGIN, follow_redirects=False)
    r = home.post("/api/settings/accounts", json={"accounts_enabled": True}, headers=APP)
    assert r.status_code == 400 and "an admin's passkey" in r.json()["detail"]
    token = make_link(home, admin)
    assert use_link(home, Phone(), token).status_code == 200
    assert home.post("/api/settings/accounts", json={"accounts_enabled": True}, headers=APP).status_code == 200
    # a new address from outside: the passkeys made for the old name are said to stop working
    r = home.post("/api/settings/accounts", json={"public_url": "https://family.example.org"}, headers=APP)
    assert "won't work on family.example.org" in r.json()["message"]


def test_the_accounts_command_makes_links(con, cfg, capsys, monkeypatch):
    accounts.add(con, "daniel.miller@example.com", None, "admin")
    con.commit()
    monkeypatch.setattr(cli.config, "load", lambda path=None: cfg)
    with pytest.raises(SystemExit, match="Address from outside"):
        cli.main(["accounts", "invite", "daniel.miller@example.com"])
    settings.save(con, cfg, READY)
    cli.main(["accounts", "invite", "daniel.miller@example.com"])
    cli.main(["accounts", "passkeys", "daniel.miller@example.com"])
    out = capsys.readouterr().out
    assert f"{ORIGIN}/auth/invite#" in out and "0 passkeys for daniel.miller@example.com" in out


@pytest.mark.parametrize("agent, name", [
    (CHROME, "Chrome on Windows"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 "
     "Mobile/15E148 Safari/604.1", "Safari on iPhone"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36 "
     "Edg/141.0", "Edge on Windows"),
    ("testclient", "A passkey")])
def test_a_passkey_is_named_after_its_browser(agent, name):
    assert passkeys.name_from(agent) == name
