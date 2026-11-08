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


def test_not_a_demo(cfg, con, fam):
    anon = TestClient(webapp.create_app(cfg), client=LAN, follow_redirects=False)
    assert anon.post("/auth/demo", json={"next": "/"}, headers=APP).status_code == 404
    assert "This is a demo" not in anon.get("/").text


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
