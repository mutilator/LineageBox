"""The family's own words on a photo: a caption anyone who sees the photo may write, comments under it, who may change
them, their undo, and where they show (the photo page above the AI's words, a tour's slides)."""
import pytest
from fastapi.testclient import TestClient

from lineagebox import accounts, comments, settings, tours
from lineagebox.web import app as webapp
from tests import family_fixture as fx
from tests.conftest import LAN

APP = {"X-Requested-With": "lineagebox"}


@pytest.fixture
def fam(con, cfg, library):
    f = fx.build(con, cfg, library)
    p = f["people"]
    f["who"] = {"Sam": fx.account(con, p["Sam"], "sam@example.com", "admin"),
                "Cora": fx.account(con, p["Cora"], "cora@example.com"),
                "Tina": fx.account(con, p["Tina"], "tina@example.com")}
    settings.save(con, cfg, {"accounts_enabled": True})
    return f


def client(cfg, con, account):
    c = TestClient(webapp.create_app(cfg), client=LAN, follow_redirects=False)
    c.cookies.set(accounts.COOKIE, accounts.new_session(con, account["id"], 30))
    return c


def test_a_caption_at_home_shows_above_the_ai_and_comes_off(cfg, con, library):
    fam = fx.build(con, cfg, library)
    photo = fam["photos"]["cora_bart.jpg"]
    home = TestClient(webapp.create_app(cfg), client=LAN)
    r = home.post(f"/api/images/{photo}/caption", json={"text": "  Grandpa's  70th,\n\n\n\nat the lake  "}, headers=APP)
    assert r.status_code == 200, r.text
    assert comments.caption(con, photo) == "Grandpa's 70th,\n\nat the lake"
    page = home.get(f"/image/{photo}").text
    assert "Grandpa&#39;s 70th" in page and 'id="caption-view"' in page
    assert r.json()["undo"]  # kept for undo, at home too
    home.post(f"/api/images/{photo}/caption", json={"text": ""}, headers=APP)
    assert comments.caption(con, photo) is None
    assert home.post(f"/api/images/{photo}/caption", json={"text": "x" * 2100}, headers=APP).status_code == 400


def test_members_write_on_photos_they_see_only(fam, cfg, con):
    cora = client(cfg, con, fam["who"]["Cora"])
    mine, secret = fam["photos"]["cora_bart.jpg"], fam["photos"]["zed.jpg"]
    assert cora.post(f"/api/images/{mine}/caption", json={"text": "At the picnic"}, headers=APP).status_code == 200
    assert cora.post(f"/api/images/{secret}/caption", json={"text": "?"}, headers=APP).status_code == 404
    assert cora.post(f"/api/images/{secret}/comments", json={"text": "?"}, headers=APP).status_code == 404
    change = con.execute("SELECT * FROM change_log ORDER BY id DESC LIMIT 1").fetchone()
    assert change["account_id"] == fam["who"]["Cora"]["id"] and change["kind"] == "words"
    admin = client(cfg, con, fam["who"]["Sam"])
    assert admin.post(f"/api/changes/{change['id']}/undo", headers=APP).status_code == 200
    assert comments.caption(con, mine) is None


def test_comments_are_changed_by_whoever_wrote_them_or_an_admin(fam, cfg, con):
    cora, tina, admin = (client(cfg, con, fam["who"][n]) for n in ("Cora", "Tina", "Sam"))
    photo = fam["photos"]["cora_bart.jpg"]
    r = cora.post(f"/api/images/{photo}/comments", json={"text": "I remember this day"}, headers=APP)
    comment = r.json()["id"]
    page = cora.get(f"/image/{photo}").text
    assert "I remember this day" in page and f"/api/comments/{comment}/edit" in page  # hers to change
    assert cora.post(f"/api/images/{photo}/comments", json={"text": "   "}, headers=APP).status_code == 400
    assert tina.post(f"/api/comments/{comment}/edit", json={"text": "No"}, headers=APP).status_code in (403, 404)
    assert cora.post(f"/api/comments/{comment}/edit", json={"text": "I remember it well"}, headers=APP).status_code == 200
    assert comments.for_image(con, photo)["comments"][0]["text"] == "I remember it well"
    assert comments.for_image(con, photo)["comments"][0]["by"] == "Cora"
    assert admin.post(f"/api/comments/{comment}/delete", headers=APP).status_code == 200
    assert comments.for_image(con, photo)["comments"] == []


def test_a_tour_slide_says_the_familys_caption(fam, cfg, con):
    photo = fam["photos"]["cora_bart.jpg"]
    comments.set_caption(con, photo, "The picnic", None)
    con.commit()
    chosen = [{"id": photo, "taken": "2010-05-01T10:00:00", "faces": {}, "media": "photo", "playable": None,
               "sha": "x"}]
    assert tours.slides(con, chosen)[0]["caption"] == "The picnic"
