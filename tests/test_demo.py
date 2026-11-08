"""A public demo (demo.py): one button to sign in, other ways in and sharing off, nothing reached on the network, a few
small uploads from each address that their uploader alone sees, and everything put back as it was saved."""
import dataclasses
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from lineagebox import access, accounts, demo, settings
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests import family_fixture as fx
from tests.conftest import LAN

APP = {"X-Requested-With": "lineagebox"}
ELSEWHERE = ("192.168.7.51", 50000)  # another visitor's address


def jpeg(color="red") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (120, 90), color).save(out, "JPEG")
    return out.getvalue()


@pytest.fixture
def fam(con, cfg, library):
    return fx.build(con, cfg, library)


@pytest.fixture
def demo_cfg(cfg, fam):
    return dataclasses.replace(cfg, demo_enabled=True, demo_person="Cora", demo_uploads=3, demo_upload_max_mb=1,
                               demo_resets="every night")


def visitor(app, client=LAN) -> TestClient:
    """Someone who clicked Sign in to demo."""
    c = TestClient(app, client=client, follow_redirects=False)
    r = c.post("/auth/demo", json={"next": "/home"}, headers=APP)
    assert r.status_code == 200, r.text
    assert r.json()["go"] == "/home"
    return c


def visitors(con) -> list:
    return con.execute("SELECT id, email, role, person_id FROM account WHERE email LIKE 'visitor-%' ORDER BY id"
                       ).fetchall()


def upload(c, path, data):
    return c.post("/api/upload", params={"path": path}, content=data, headers=APP)


def test_one_button_and_no_other_way_in(demo_cfg, con, fam):
    app = webapp.create_app(demo_cfg)
    anon = TestClient(app, client=LAN, follow_redirects=False)
    page = anon.get("/signin").text
    assert "Sign in to demo" in page and "Sign in with Google" not in page and "authenticator" not in page
    assert anon.get("/home").status_code == 303  # signed in first, as with family accounts
    a = visitor(app)
    visitor(app)
    made = visitors(con)
    assert len(made) == 2 and made[0][1] != made[1][1]  # an account each: their own uploads and undo
    assert all(r[2] == "member" and r[3] == fam["people"]["Cora"] for r in made)
    home = a.get("/home")
    assert home.status_code == 200
    assert "This is a demo" in home.text and "every night" in home.text
    assert "@demo.invalid" not in home.text  # their made-up email isn't shown
    assert anon.get("/auth/google").status_code == 403  # other ways in
    assert a.get("/auth/google").status_code == 403 and a.get("/passkeys").status_code == 403
    assert anon.post("/auth/local", json={"email": "sam@example.com", "code": "123456"},
                     headers=APP).status_code == 403
    assert anon.post("/auth/passkey/start", json={"next": "/"}, headers=APP).status_code == 403
    # what one visitor made would reach the others
    assert a.post("/api/shares", json={"image_id": 1, "to": "everyone"}, headers=APP).status_code == 403
    assert a.post("/api/stories", headers=APP).status_code == 403


def test_the_admin_button_is_locked_down(demo_cfg, con, fam):
    """An admin sees Settings, accounts and Status, changes no setting, sees no other machine's address, and may not
    upload (an admin there sees every visitor's uploads)."""
    from lineagebox import activity
    private = dataclasses.replace(demo_cfg, ollama_url="http://10.9.8.7:11434", trusted_proxies=("172.31.240.1",))
    app = webapp.create_app(private)
    anon = TestClient(app, client=LAN, follow_redirects=False)
    assert "Sign in as an admin" in anon.get("/signin").text
    a = TestClient(app, client=LAN, follow_redirects=False)
    assert a.post("/auth/demo", json={"next": "/", "admin": True}, headers=APP).status_code == 200
    assert visitors(con)[0][2] == "admin"
    activity.log(con, "worker started: watching Family archive; Ollama http://10.1.2.149:11434")
    for path in ("/settings", "/accounts", "/status", "/status/live"):
        r = a.get(path)
        assert r.status_code == 200, path
        assert not any(secret in r.text for secret in ("10.9.8.7", "172.31.240.1", "10.1.2.149")), path
    assert 'class="demo-locked" disabled' in a.get("/settings").text
    assert 'class="demo-locked" disabled' in a.get("/accounts").text
    assert "/settings/codes" not in a.get("/settings").text and a.get("/settings/codes").status_code == 403
    for path, body in (("/api/settings", {"blurry_below": 10}), ("/api/settings/address", {}),
                       ("/api/accounts", {"email": "x@example.com"}), ("/api/ollama/check", None),
                       ("/api/backup", None), ("/api/folders/watch", {"folder": "x"}), ("/api/update", None)):
        assert a.post(path, json=body, headers=APP).status_code == 403, path
    assert settings.current(con, demo_cfg).blurry_below != 10
    up = upload(a, "mine.jpg", jpeg())
    assert up.status_code == 403 and "family member" in up.json()["detail"]
    assert "family member" in a.get("/upload").text
    assert a.post("/api/people/identify", headers=APP).status_code == 200  # the admins' tools work


def _stored_settings(con) -> list:
    return con.execute(f"SELECT key, value FROM settings WHERE key IN ({','.join('?' * len(settings.EDITABLE))}) "
                       "ORDER BY key", settings.EDITABLE).fetchall()


def test_every_route_that_changes_settings_is_locked():
    """A route that stores settings (or watched folders) must be on demo.LOCKED, and every path there must be a real
    route: a misspelt one would leave its route open without anyone noticing."""
    import inspect
    every = set(webapp.ROUTES)
    assert demo.LOCKED <= every and demo.CLOSED <= every, sorted((demo.LOCKED | demo.CLOSED) - every)
    for module in webapp.routes.MODULES:
        for route in module.router.routes:
            source = inspect.getsource(route.endpoint)
            if any(call in source for call in ("settings.save(", "settings.watch(", "settings.unwatch(")):
                assert route.path in demo.LOCKED | demo.CLOSED, f"{route.path} changes settings and isn't locked"


def test_forged_requests_change_no_setting(demo_cfg, cfg, con, fam):
    """Whatever an admin's browser sends, with the site's own header or as a plain form, no setting changes: refused
    before the route runs (demo.LOCKED), and by settings.save itself should a route be missed."""
    app = webapp.create_app(demo_cfg)
    a = TestClient(app, client=LAN, follow_redirects=False)
    assert a.post("/auth/demo", json={"next": "/", "admin": True}, headers=APP).status_code == 200
    before = _stored_settings(con)
    forged = {"ollama_url": "http://203.0.113.5:11434", "trusted_proxies": "0.0.0.0/0", "folders": ["/"],
              "allowed_hosts": "evil.example", "accounts_enabled": False, "path": "/", "email": "x@example.com"}
    for path in sorted(demo.LOCKED):
        url = path.replace("{account_id}", "1").replace("{device_id}", "1")
        assert a.post(url, json=forged, headers=APP).status_code == 403, url
        assert a.post(url, data=forged).status_code in (400, 403), url  # as a plain form, without the site's header
        assert a.put(url, json=forged, headers=APP).status_code in (403, 405), url
    assert _stored_settings(con) == before
    with pytest.raises(settings.Invalid, match="switched off in this demo"):
        settings.save(con, demo_cfg, {"blurry_below": 5})
    settings.save(con, cfg, {"blurry_below": 5})  # not a demo: as ever


def test_link_previews(demo_cfg, con, fam):
    shown = next(path for path, sha in con.execute("SELECT f.path, i.sha256 FROM file f JOIN image i "
                                                   "ON i.id = f.image_id ORDER BY f.id")
                 if demo_cfg.preview_path(sha).is_file())
    app = webapp.create_app(dataclasses.replace(demo_cfg, demo_preview=shown, public_url="https://demo.example.com"))
    anon = TestClient(app, client=LAN, follow_redirects=False)
    page = anon.get("/signin").text
    assert '<meta property="og:image" content="https://demo.example.com/demo-preview.jpg">' in page
    assert 'og:title' in page and "twitter:card" in page
    picture = anon.get("/demo-preview.jpg")  # without signing in: a chat app's preview asks for it
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/jpeg"
    assert TestClient(webapp.create_app(demo_cfg), client=LAN).get("/demo-preview.jpg").status_code == 404  # none set


def test_not_a_demo(cfg, con, fam):
    anon = TestClient(webapp.create_app(cfg), client=LAN, follow_redirects=False)
    assert anon.post("/auth/demo", json={"next": "/"}, headers=APP).status_code == 404
    page = anon.get("/").text
    assert "This is a demo" not in page and "og:title" not in page
    assert anon.get("/demo-preview.jpg").status_code == 404
    assert anon.post("/api/settings", json={"blurry_below": 12}, headers=APP).status_code == 200  # not locked


def test_the_demo_person_must_be_in_the_tree(demo_cfg, con, fam):
    app = webapp.create_app(dataclasses.replace(demo_cfg, demo_person="Nobody Atall"))
    r = TestClient(app, client=LAN).post("/auth/demo", json={"next": "/"}, headers=APP)
    assert r.status_code == 400 and "nobody called Nobody Atall" in r.json()["detail"]
    by_id = webapp.create_app(dataclasses.replace(demo_cfg, demo_person=str(fam["people"]["Sam"])))
    visitor(by_id)
    assert visitors(con)[0][3] == fam["people"]["Sam"]


def test_the_demo_reaches_nothing(demo_cfg, cfg, con):
    """Ollama, Whisper, OpenStreetMap and GitHub stay off whatever the catalog's settings say."""
    settings.save(con, cfg, {"describe_enabled": True, "transcribe_enabled": True, "places_lookup": True,
                             "update_check": True, "sidecars_enabled": True})
    now = settings.current(con, demo_cfg)
    assert not (now.describe_enabled or now.transcribe_enabled or now.places_lookup or now.update_check
                or now.sidecars_enabled or now.import_enabled or now.backup_enabled)
    assert now.whisper_url == "" and now.ollama_embed_model == "" and now.google_client_id == ""
    assert now.accounts_enabled  # signed in, always
    assert settings.current(con, cfg).describe_enabled  # not a demo: as saved


def test_a_few_small_uploads_from_each_address(demo_cfg, con, fam):
    app = webapp.create_app(demo_cfg)
    a = visitor(app)
    page = a.get("/upload").text
    assert "3 uploads left of the 3 allowed from here" in page and "up to 1 MB a file" in page
    big = upload(a, "big.jpg", b"\xff\xd8\xff" + b"0" * 2**20)
    assert big.status_code == 413 and "over 1 MB" in big.json()["detail"]
    assert upload(a, "notes.jpg", b"this is not a photo at all").status_code == 400  # refused: doesn't count
    for n in range(3):
        assert upload(a, f"p{n}.jpg", jpeg()).status_code == 200
    full = upload(a, "p3.jpg", jpeg())
    assert full.status_code == 403 and "3 in this demo" in full.json()["detail"]
    assert "0 uploads left" in a.get("/upload").text
    again = visitor(app)  # signing in again from the same address: the same count
    assert upload(again, "q.jpg", jpeg()).status_code == 403
    assert upload(visitor(app, ELSEWHERE), "r.jpg", jpeg()).status_code == 200
    assert demo.uploads_left(con, LAN[0], 3) == 0 and demo.uploads_left(con, ELSEWHERE[0], 3) == 2


def test_what_a_visitor_uploads_is_theirs_alone(demo_cfg, con, fam):
    app = webapp.create_app(demo_cfg)
    a, b = visitor(app), visitor(app, ELSEWHERE)
    a_id, b_id = (r[0] for r in visitors(con))
    seen = sorted(access.compute(con, dict(accounts.get(con, b_id)), alone=True).images)
    library_photo = seen[0]
    path = con.execute("SELECT path FROM file WHERE image_id = ? AND NOT missing", (library_photo,)).fetchone()[0]
    assert upload(a, "garden.jpg", jpeg("green")).status_code == 200
    assert upload(a, "copy.jpg", demo_cfg.source_path(path).read_bytes()).status_code == 200
    scan(con, demo_cfg)
    ingest(con, demo_cfg)
    accounts.claim_uploads(con)  # as the worker does: recorded as hers
    garden = con.execute("SELECT image_id FROM file WHERE path LIKE '%garden.jpg'").fetchone()[0]
    # a face in it named as someone every visitor sees
    con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_by)
                   VALUES (?, 0.1, 0.1, 0.5, 0.5, 0.9, 25, ?, ?, 'user')""",
                (garden, fx.unit(5).tobytes(), fam["people"]["Cora"]))
    con.commit()
    sees = {who: access.compute(con, dict(accounts.get(con, who)), alone=True).images for who in (a_id, b_id)}
    assert garden in sees[a_id] and garden not in sees[b_id]
    assert library_photo in sees[b_id]  # her copy of a library photo doesn't hide it from the others
    assert garden in access.compute(con, dict(accounts.get(con, b_id))).images  # not a demo: Cora is in it
    fresh = TestClient(webapp.create_app(demo_cfg), client=ELSEWHERE, follow_redirects=False)
    fresh.cookies.set(accounts.COOKIE, accounts.new_session(con, b_id, 30))
    assert fresh.get(f"/image/{garden}").status_code == 404
    assert fresh.get(f"/image/{library_photo}").status_code == 200


def test_reset_puts_back_what_was_saved(demo_cfg, cfg, con, fam, library):
    backups = cfg.data_dir / "backups"
    backups.mkdir(exist_ok=True)
    (backups / "before.db").write_text("kept")
    saved = demo.save(con, demo_cfg)
    assert saved["folders"] == [library.name]
    assert not (demo.seed_dir(cfg) / "data" / "backups").exists()  # nor the seed in itself
    cora = fam["people"]["Cora"]
    # a day of visitors
    upload(visitor(webapp.create_app(demo_cfg)), "mine.jpg", jpeg())
    scan(con, demo_cfg)
    ingest(con, demo_cfg)
    con.execute("UPDATE person SET name = 'Someone else' WHERE id = ?", (cora,))
    con.commit()
    photo = next(p for p in sorted(library.rglob("*.jpg")) if "Family uploads" not in p.as_posix())
    was = photo.read_bytes()
    photo.write_bytes(b"changed")
    made = cfg.data_dir / "thumbs" / "zz" / "made-since.webp"
    made.parent.mkdir(parents=True, exist_ok=True)
    made.write_bytes(b"x")
    (backups / "since.db").write_text("kept too")

    done = demo.reset(demo_cfg)
    assert done["removed"] >= 2 and done["copied"] >= 1
    assert visitors(con) == []
    assert con.execute("SELECT name FROM person WHERE id = ?", (cora,)).fetchone()[0] == "Cora"
    assert not list(library.rglob("mine.jpg")) and not (library / "Family uploads").exists()
    assert photo.read_bytes() == was
    assert not made.exists()
    assert (backups / "before.db").exists() and (backups / "since.db").exists()  # left alone
    assert demo.reset(demo_cfg)["copied"] == 0  # nothing changed since: nothing copied


def test_reset_only_in_a_demo_with_a_seed(demo_cfg, cfg, con):
    with pytest.raises(demo.Problem, match="isn't a demo"):
        demo.reset(cfg)  # it would delete every photo added since a save
    with pytest.raises(demo.Problem, match="nothing to put back"):
        demo.reset(demo_cfg)


def test_reset_puts_back_all_or_nothing(demo_cfg, cfg, con, fam, library, monkeypatch):
    """Where the photos are read-only (Docker's worker), nothing is put back: not the catalog without the files."""
    demo.save(con, demo_cfg)
    visitor(webapp.create_app(demo_cfg))
    monkeypatch.setattr(demo.os, "access", lambda path, mode: path != library)
    with pytest.raises(demo.Problem, match="can't be written here, so nothing was put back"):
        demo.reset(demo_cfg)
    assert len(visitors(con)) == 1  # the catalog as it was
