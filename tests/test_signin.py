"""Signing in: Sign in with Google (Google answered by a fake), admins' codes at home, sessions, sign-out, and the
accounts command line."""
import base64
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from lineagebox import accounts, cli, people, settings
from lineagebox.web import app as webapp
from tests.conftest import LAN

APP = {"X-Requested-With": "lineagebox"}
OUTSIDE = ("203.0.113.9", 50000)
CLIENT_ID = "123-abc.apps.googleusercontent.com"


def token(**claims) -> str:
    part = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{part({'alg': 'RS256'})}.{part(claims)}.signature"


class FakeGoogle:
    """Google's token endpoint, answering with whatever claims a test sets."""

    def __init__(self):
        self.claims, self.status, self.seen, self.error = {}, 200, [], None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(parse_qs(request.content.decode()))
        if self.error:  # refused, as Google answers: {"error": "invalid_client", ...}
            return httpx.Response(401, json={"error": self.error, "error_description": "Unauthorized"})
        return httpx.Response(self.status, json={"id_token": token(**self.claims)})


@pytest.fixture
def setup(con, cfg):
    daniel = people.person_id(con, "Daniel Miller")
    katherine = people.person_id(con, "Katherine Miller")
    admin = accounts.add(con, "daniel.miller@example.com", daniel, "admin")
    member = accounts.add(con, "katherine.miller@example.com", katherine)
    con.commit()
    settings.save(con, cfg, {"accounts_enabled": True, "google_client_id": CLIENT_ID, "google_client_secret": "s3cret",
                             "public_url": "https://photos.example.com"})
    google = FakeGoogle()
    return {"admin": admin, "member": member, "google": google, "cfg": cfg,
            "app": webapp.create_app(cfg, google_http=httpx.Client(transport=httpx.MockTransport(google)))}


def start(client, next_page="/people"):
    r = client.get(f"/auth/google?next={next_page}")
    assert r.status_code == 303
    url = urlparse(r.headers["location"])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == "accounts.google.com" and q["client_id"] == CLIENT_ID
    assert q["redirect_uri"] == "https://photos.example.com/auth/google/callback"
    assert q["code_challenge_method"] == "S256" and q["scope"] == "openid email profile"
    assert "lineagebox_signin" in r.cookies
    return q


def good_claims(q, email="katherine.miller@example.com"):
    return {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "exp": time.time() + 300, "nonce": q["nonce"],
            "email": email, "email_verified": True}


def test_sign_in_with_google(setup, con):
    c = TestClient(setup["app"], client=OUTSIDE, base_url="https://testserver", follow_redirects=False)
    q = start(c)
    setup["google"].claims = good_claims(q)
    r = c.get(f"/auth/google/callback?code=abc&state={q['state']}")
    assert r.status_code == 303 and r.headers["location"] == "/people"
    cookie = r.headers["set-cookie"]
    assert "lineagebox_session=" in cookie and "HttpOnly" in cookie and "Secure" in cookie and "samesite=lax" in cookie.lower()
    sent = setup["google"].seen[0]
    assert sent["code"] == ["abc"] and sent["client_secret"] == ["s3cret"] and len(sent["code_verifier"][0]) > 40
    assert c.get("/people").status_code == 200
    assert con.execute("SELECT count(*) FROM session").fetchone()[0] == 1
    assert len(con.execute("SELECT token_hash FROM session").fetchone()[0]) == 64  # only its hash is kept
    # signing out ends this browser's session
    assert c.post("/auth/signout", headers=APP).json()["go"] == "/signin"
    assert con.execute("SELECT count(*) FROM session").fetchone()[0] == 0
    assert c.get("/people").status_code == 303


@pytest.mark.parametrize("change, message", [
    ({"aud": "someone-else.apps.googleusercontent.com"}, "not meant for this site"),
    ({"iss": "https://evil.example.com"}, "not meant for this site"),
    ({"exp": time.time() - 10}, "took too long"),
    ({"nonce": "another"}, "not the one started here"),
    ({"email_verified": False}, "not verified"),
    ({"email": "stranger@gmail.com"}, "been invited to these photos"),
])
def test_google_sign_ins_that_are_refused(setup, con, change, message):
    c = TestClient(setup["app"], client=OUTSIDE, follow_redirects=False)
    q = start(c)
    setup["google"].claims = {**good_claims(q), **change}
    r = c.get(f"/auth/google/callback?code=abc&state={q['state']}")
    assert r.status_code == 200 and message in r.text and "lineagebox_session" not in r.cookies
    assert con.execute("SELECT count(*) FROM session").fetchone()[0] == 0


@pytest.mark.parametrize("error, message", [
    ("invalid_client", "client ID and secret on Settings"), ("redirect_uri_mismatch", "authorised redirect URIs"),
    ("invalid_grant", "took too long"),
])
def test_why_google_refused_is_said(setup, con, error, message):
    c = TestClient(setup["app"], client=OUTSIDE, follow_redirects=False)
    q = start(c)
    setup["google"].error = error
    r = c.get(f"/auth/google/callback?code=abc&state={q['state']}")
    assert r.status_code == 200 and message in r.text and "lineagebox_session" not in r.cookies
    assert con.execute("SELECT 1 FROM activity WHERE message = ?", (f"Google refused a sign-in: {error}",)).fetchone()


def test_a_sign_in_this_browser_did_not_start(setup, con):
    starter = TestClient(setup["app"], client=OUTSIDE, follow_redirects=False)
    q = start(starter)
    setup["google"].claims = good_claims(q)
    other = TestClient(setup["app"], client=OUTSIDE, follow_redirects=False)  # without the cookie: no signing in
    r = other.get(f"/auth/google/callback?code=abc&state={q['state']}")  # someone else into another's account
    assert "sign-in didn" in r.text and "lineagebox_session" not in r.cookies
    assert "sign-in didn" in starter.get(f"/auth/google/callback?code=abc&state={q['state']}").text  # used up
    assert "sign-in didn" in starter.get("/auth/google/callback?error=access_denied").text


def test_a_switched_off_account_cannot_sign_in(setup, con):
    accounts.update(con, setup["member"], disabled=True)
    con.commit()
    c = TestClient(setup["app"], client=OUTSIDE, follow_redirects=False)
    q = start(c)
    setup["google"].claims = good_claims(q)
    assert "been invited to these photos" in c.get(f"/auth/google/callback?code=abc&state={q['state']}").text


def test_local_codes(setup, con, monkeypatch):
    secret = accounts.set_local_secret(con, setup["admin"])
    now = time.time()
    code = accounts.code_at(secret, int(now // 30))
    home = TestClient(setup["app"], client=LAN, follow_redirects=False)
    away = TestClient(setup["app"], client=OUTSIDE, follow_redirects=False)
    body = {"email": "daniel.miller@example.com", "code": code, "next": "/settings"}
    assert away.post("/auth/local", json=body, headers=APP).status_code == 403  # at home only
    assert home.post("/auth/local", json={**body, "code": "000000"}, headers=APP).status_code == 400
    r = home.post("/auth/local", json=body, headers=APP)
    assert r.status_code == 200 and r.json()["go"] == "/settings" and "lineagebox_session" in r.cookies
    assert home.post("/auth/local", json=body, headers=APP).status_code == 400  # each code works once
    assert accounts.get(con, setup["admin"])["code_step"] == int(now // 30)  # in the catalog: a restart keeps it
    restarted = TestClient(webapp.create_app(setup["cfg"]), client=LAN, follow_redirects=False)
    assert restarted.post("/auth/local", json=body, headers=APP).status_code == 400
    with pytest.raises(accounts.Problem):  # members don't sign in with codes
        accounts.set_local_secret(con, setup["member"])
    for _ in range(5):
        home.post("/auth/local", json={**body, "code": "111111"}, headers=APP)
    assert home.post("/auth/local", json=body, headers=APP).status_code == 429  # enough tries for a while


def test_totp_matches_rfc_6238():
    # RFC 6238's SHA-1 test key, "12345678901234567890", at its test times (the last 6 of its 8 digits)
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert accounts.code_at(secret, 59 // 30) == "287082"
    assert accounts.code_at(secret, 1111111109 // 30) == "081804"
    assert accounts.code_at(secret, 1234567890 // 30) == "005924"


def test_turning_accounts_on_needs_a_way_in(con, cfg):
    with pytest.raises(settings.Invalid, match="admin account first"):
        settings.save(con, cfg, {"accounts_enabled": True})
    accounts.add(con, "daniel.miller@example.com", people.person_id(con, "Daniel Miller"), "admin")
    con.commit()
    c = TestClient(webapp.create_app(cfg), client=LAN)
    r = c.post("/api/settings/accounts", json={"accounts_enabled": True}, headers=APP)
    assert r.status_code == 400 and "nobody could get in" in r.json()["detail"]
    r = c.post("/api/settings/accounts", json={"accounts_enabled": True, "google_client_id": CLIENT_ID,
                                               "google_client_secret": "x"}, headers=APP)
    assert r.status_code == 200 and settings.current(con, cfg).accounts_enabled
    # the secret stays when the box is left empty, and is never put on the page
    c2 = TestClient(webapp.create_app(cfg), client=LAN)
    c2.cookies.set(accounts.COOKIE, accounts.new_session(con, 1, 1))
    c2.post("/api/settings/accounts", json={"google_client_secret": "", "session_days": 30}, headers=APP)
    assert settings.current(con, cfg).google_client_secret == "x"
    assert '"x"' not in c2.get("/accounts").text and "value=\"x\"" not in c2.get("/accounts").text
    with pytest.raises(accounts.Problem, match="only admin"):  # nobody takes away the last admin
        accounts.update(con, 1, role="member")


def test_the_accounts_command(con, cfg, capsys, monkeypatch):
    people.person_id(con, "Daniel Miller")
    con.commit()
    monkeypatch.setattr(cli.config, "load", lambda path=None: cfg)
    cli.main(["accounts", "add", "daniel.miller@example.com", "--admin", "--person", "daniel miller"])
    cli.main(["accounts", "local-code", "daniel.miller@example.com"])
    out = capsys.readouterr().out
    assert "Added daniel.miller@example.com as an admin" in out and "otpauth://totp/" in out
    cli.main(["accounts", "on"])
    cli.main(["accounts", "list"])
    out = capsys.readouterr().out
    assert "daniel.miller@example.com" in out and "admin" in out and "Family accounts are on" in out
    with pytest.raises(SystemExit, match="nobody in the family tree is called"):
        cli.main(["accounts", "add", "x@example.com", "--person", "Nobody Here"])


def test_the_codes_page(setup, con, monkeypatch):
    """Settings → Codes for signing in at home: a QR code shown once, a check that doesn't use the code up, and
    taking the code away."""
    c = TestClient(setup["app"], client=LAN, follow_redirects=False)
    c.cookies.set(accounts.COOKIE, accounts.new_session(con, setup["admin"], 30))
    page = c.get("/settings/codes").text
    # admins only
    assert "daniel.miller@example.com" in page and "no code yet" in page and "katherine.miller" not in page
    made = c.post(f"/api/accounts/{setup['admin']}/local-code", headers=APP).json()["html"]
    secret = accounts.get(con, setup["admin"])["local_secret"]
    assert "<svg" in made and "otpauth://totp/LineageBox:daniel.miller%40example.com" in made and secret[:4] in made
    assert secret not in c.get("/settings/codes").text  # shown only when made
    code = accounts.code_at(secret, int(time.time() // 30))
    check = f"/api/accounts/{setup['admin']}/local-code/check"
    assert "isn't the code" in c.post(check, json={"code": "000000"}, headers=APP).json()["message"]
    assert "the app is set up" in c.post(check, json={"code": code}, headers=APP).json()["message"]
    home = TestClient(setup["app"], client=LAN, follow_redirects=False)
    body = {"email": "daniel.miller@example.com", "code": code, "next": "/"}
    assert home.post("/auth/local", json=body, headers=APP).status_code == 200  # checking didn't use it up
    c.post(f"/api/accounts/{setup['admin']}/local-code/remove", headers=APP)
    assert accounts.get(con, setup["admin"])["local_secret"] is None
    assert c.post(check, json={"code": code}, headers=APP).status_code == 404
    member = TestClient(setup["app"], client=LAN, follow_redirects=False)
    member.cookies.set(accounts.COOKIE, accounts.new_session(con, setup["member"], 30))
    assert member.get("/settings/codes").status_code == 404
    assert member.post(f"/api/accounts/{setup['admin']}/local-code", headers=APP).status_code == 404


def test_the_profile_menu(setup, con):
    """The round button in the top bar says who is signed in; the links fold into the ☰ menu on small screens."""
    c = TestClient(setup["app"], client=LAN, follow_redirects=False)
    c.cookies.set(accounts.COOKIE, accounts.new_session(con, setup["member"], 30))
    page = c.get("/people").text
    assert ">KM</button>" in page and "katherine.miller@example.com" in page and "Family member" in page
    assert 'data-api="/auth/signout"' in page and 'aria-controls="menu"' in page
    assert "/settings/codes" not in page  # admins' only
    admin = TestClient(setup["app"], client=LAN, follow_redirects=False)
    admin.cookies.set(accounts.COOKIE, accounts.new_session(con, setup["admin"], 30))
    assert ">DM</button>" in admin.get("/people").text and "/settings/codes" in admin.get("/people").text
    signed_out = TestClient(setup["app"], client=LAN).get("/signin").text
    assert 'aria-controls="menu"' not in signed_out and 'class="avatar"' not in signed_out


def test_guessing_codes_from_many_addresses(setup, con):
    """Each address gets a few tries, and one email gets a few more from every address together."""
    accounts.set_local_secret(con, setup["admin"])
    body = {"email": "daniel.miller@example.com", "code": "000000", "next": "/"}
    answers = [TestClient(setup["app"], client=(f"192.168.7.{n}", 50000)).post("/auth/local", json=body, headers=APP)
               .status_code for n in range(2, 16)]
    assert answers[:10] == [400] * 10 and set(answers[10:]) == {429}


def test_nothing_large_before_signing_in(setup):
    away = TestClient(setup["app"], client=OUTSIDE)
    big = {"email": "x@example.com", "code": "1" * 100_000, "next": "/"}
    assert away.post("/auth/local", json=big, headers=APP).status_code == 413
    assert away.post("/auth/signout", content=b"", headers={**APP, "transfer-encoding": "chunked"}).status_code == 413


@pytest.mark.parametrize("where", ["/\t/evil.example", "/\n/evil.example", "//evil.example", "/\x5cevil.example",
                                   "https://evil.example", "/ok page"])
def test_after_signing_in_only_pages_of_this_site(where):
    assert webapp._local_path(where) == "/"
    assert webapp._local_path("/people?q=a") == "/people?q=a"


def test_the_sign_in_page_shows_no_words_from_its_address(setup):
    page = TestClient(setup["app"], client=OUTSIDE).get("/signin?error=Call+0800+to+unlock").text
    assert "0800" not in page
