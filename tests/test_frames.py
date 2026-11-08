"""Photo frame and TV mode: the frame page and its list of photos, device links made and turned off on Settings →
Photo frames, and what each shows (memories as one account sees them, a tour as its maker does). What else a device link can
open, nothing, is swept in tests/test_access_web.py."""
import hashlib
import re

import pytest
from fastapi.testclient import TestClient

from lineagebox import access, accounts, catalog, db, devices
from lineagebox.web import app as webapp
from lineagebox.web import frames
from tests.conftest import LAN
from tests.test_access_web import OUTSIDE, signed_in, site  # noqa: F401 - site is a fixture
from tests.test_home import dated  # noqa: F401 - a fixture: photos from this week five years ago

APP = {"X-Requested-With": "lineagebox"}
STATIC = webapp.HERE / "static"


def test_the_frame_at_home_without_accounts(cfg, con, dated):
    c = TestClient(webapp.create_app(cfg), client=LAN)
    page = c.get("/photo-frame")
    assert page.status_code == 200
    assert 'data-photos="/api/photo-frame"' in page.text and "/static/frame.js?v=" in page.text
    assert "/static/app.js" not in page.text  # a page of its own: an old browser needn't read the site's script
    assert page.headers["x-frame-options"] == "DENY"  # only a device link may be shown inside another page
    photos = c.get("/api/photo-frame").json()["photos"]
    shas = {p["poster"].rsplit("/", 1)[1] for p in photos}
    five_ago = {catalog.image(con, i)["sha256"] for n, i in dated.items() if n.startswith("ago5_")}
    assert five_ago <= shas  # this week five years ago
    assert all(p["when"] and set(p) == {"src", "poster", "video", "when", "caption"} for p in photos)  # no names
    # this week's first, then the best of other years fill it
    assert {p["poster"].rsplit("/", 1)[1] for p in photos[:len(five_ago) + 2]} >= five_ago
    outside = TestClient(c.app, client=OUTSIDE)
    assert outside.get("/photo-frame").status_code == 403  # the home network only, like the rest


def test_a_frame_shows_a_caption(cfg, con, dated):
    from lineagebox import comments
    comments.set_caption(con, dated["ago5_0.jpg"], "Grandpa's boat", None)
    con.commit()
    photos = TestClient(webapp.create_app(cfg), client=LAN).get("/api/photo-frame").json()["photos"]
    assert any(p["caption"] == "Grandpa's boat" for p in photos)


def test_the_frame_script_is_for_old_browsers_too():
    """No arrow functions, let/const, classes, template strings, fetch or async in frame.js: an old tablet's or TV's
    browser stops at the first one it can't read."""
    script = (STATIC / "frame.js").read_text(encoding="utf-8")
    code = re.sub(r"//[^\n]*", "", script)
    for word in ("=>", r"\blet\b", r"\bconst\b", r"\bclass\b", "`", r"\bfetch\(", r"\basync\b", r"\bawait\b",
                 r"\.\.\."):
        assert not re.search(word, code), word


def test_making_and_turning_off_a_device_link(site, cfg, con):
    sam = signed_in(cfg, con, site["who"]["Sam"])
    settings_page = sam.get("/settings").text
    assert 'id="devices"' in settings_page and 'data-later="/settings/devices"' in settings_page
    assert 'href="/settings#devices"' in settings_page
    panel = sam.get("/settings/devices").text
    assert "No photo frames yet" in panel and 'data-api="/api/devices"' in panel
    r = sam.post("/api/devices", json={"name": "  Kitchen   tablet ", "view": "memories",
                                       "account_id": str(site["who"]["Cora"]["id"]), "home_only": False,
                                       "seconds": "30"}, headers=APP)
    assert r.status_code == 200
    html = r.json()["html"]
    url = re.search(r'<code class="code">(http[^<]+/d/([\w-]+))</code>', html)
    assert url and "<svg" in html and "Kitchen tablet" in html  # the link, its QR code, once
    token = url[2]
    row = con.execute("SELECT * FROM device_link").fetchone()
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest() and token not in str(tuple(row))
    assert row["seconds"] == 30 and not row["home_only"] and row["made_by"] == site["who"]["Sam"]["id"]
    assert "memories as Cora sees them" in html and "not used yet" in html
    frame = TestClient(sam.app, client=OUTSIDE)  # from another house
    page = frame.get(f"/d/{token}")
    assert page.status_code == 200 and 'data-seconds="30"' in page.text and "Kitchen tablet" in page.text
    assert "last used" in sam.get("/settings/devices").text
    # a link that is no more isn't shown again, and anything can't be made of a bad form
    assert token not in sam.get("/settings/devices").text
    assert sam.post("/api/devices", json={"name": "", "view": "memories"}, headers=APP).status_code == 400
    assert sam.post("/api/devices", json={"name": "x", "view": "tour", "tour_id": "99999"},
                    headers=APP).status_code == 400
    assert sam.post("/api/devices", json={"name": "x", "view": "everything"}, headers=APP).status_code == 400
    # turned off: the frame says so, and it can be taken off the list
    r = sam.post(f"/api/devices/{row['id']}/revoke", headers=APP)
    assert "turned off" in r.json()["html"] and frame.get(f"/d/{token}").status_code == 404
    assert sam.post(f"/api/devices/{row['id']}/delete", headers=APP).status_code == 200
    assert con.execute("SELECT count(*) FROM device_link").fetchone()[0] == 0
    assert sam.post("/api/devices/99999/revoke", headers=APP).status_code == 404


def test_a_tour_on_a_frame_is_as_its_maker_sees_it(site, cfg, con):
    """Cora's own tour of everything: on a frame, only what she sees, not what an admin would."""
    _, token = devices.make(con, "Cora's frame", "tour", tour_id=site["tours"]["Cora"], home_only=False)
    c = TestClient(webapp.create_app(cfg), client=OUTSIDE)
    photos = c.get(f"/d/{token}/api/photo-frame").json()["photos"]
    shown = {p["poster"].rsplit("/", 1)[1] for p in photos}
    hers = {catalog.image(con, i)["sha256"] for i in access.compute(con, site["who"]["Cora"]).images}
    assert shown and shown <= hers
    assert catalog.image(con, site["photos"]["zed.jpg"])["sha256"] not in shown


def test_a_frame_of_a_switched_off_account_shows_nothing(site, cfg, con):
    _, token = devices.make(con, "Tina's", "memories", account_id=site["who"]["Tina"]["id"], home_only=False)
    c = TestClient(webapp.create_app(cfg), client=OUTSIDE)
    assert c.get(f"/d/{token}").status_code == 200
    accounts.update(con, site["who"]["Tina"]["id"], disabled=True)
    con.commit()
    c = TestClient(webapp.create_app(cfg), client=OUTSIDE)  # a new lookup (a page's is kept a few seconds)
    assert c.get(f"/d/{token}").status_code == 404
    accounts.remove(con, site["who"]["Tina"]["id"])
    con.commit()
    assert con.execute("SELECT count(*) FROM device_link").fetchone()[0] == 0  # the link goes with the account


def test_a_members_own_frame(site, cfg, con):
    cora = signed_in(cfg, con, site["who"]["Cora"])
    assert cora.get("/photo-frame").status_code == 200
    assert cora.get(f"/photo-frame?tour={site['tours']['Cora']}").status_code == 200
    assert cora.get(f"/photo-frame?tour={site['tours']['admins']}").status_code == 404  # not hers
    assert cora.get(f"/api/photo-frame?tour={site['tours']['Tina']}").status_code == 404
    photos = cora.get(f"/api/photo-frame?tour={site['tours']['shared']}").json()["photos"]  # shared with her
    hers = {catalog.image(con, i)["sha256"] for i in access.compute(con, site["who"]["Cora"]).images}
    assert photos and {p["poster"].rsplit("/", 1)[1] for p in photos} <= hers


def test_a_device_viewer_sees_nothing_but_its_photos(site, cfg, con):
    link = {"id": 1, "name": "x", "view": "memories", "account_id": None}
    v = access.device(link, {1, 2}, {"a" * 64})
    assert v.limited and v.device is link and v.account_id is None and v.me is None
    assert v.sees_image(1) and not v.sees_image(3) and not v.sees_person(1)
    scoped = access.scope(db.connect(cfg, migrate_schema=False), v)  # its connection holds only those
    try:
        assert {r[0] for r in scoped.execute("SELECT id FROM image")} <= {1, 2}
        for table in ("person", "device_link", "account", "outbox", "share", "tour"):
            assert scoped.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
    finally:
        scoped.close()
    assert frames.ROUTES == {"/photo-frame", "/api/photo-frame", "/p/{sha}", "/play/{image_id}"}


def test_an_album_on_a_frame_opens_only_that_albums_photos(site, cfg, con):
    """An album, in its order and with its own captions, as its maker may pass it on: Cora's album holds a photo she
    can't see (put there by hand), which the frame never shows; nor any photo outside the album."""
    from lineagebox import albums
    ph = site["photos"]
    mine = albums.create(con, "Picnic days", site["who"]["Cora"]["id"])
    albums.add(con, mine, [ph["cora_bart.jpg"], ph["rex.jpg"]])
    con.execute("INSERT INTO album_photo(album_id, image_id, position, added_at) VALUES (?, ?, 9, '2026-01-01')",
                (mine, ph["zed.jpg"]))
    albums.set_caption(con, mine, ph["rex.jpg"], "Grandpa at the lake")
    con.commit()
    _, token = devices.make(con, "Album frame", "album", album_id=mine, home_only=False)
    c = TestClient(webapp.create_app(cfg), client=OUTSIDE)
    d = f"/d/{token}"
    photos = c.get(f"{d}/api/photo-frame").json()["photos"]
    sha = {name: catalog.image(con, i)["sha256"] for name, i in ph.items()}
    assert [p["poster"] for p in photos] == [f"/p/{sha['cora_bart.jpg']}", f"/p/{sha['rex.jpg']}"]  # its order
    assert photos[1]["caption"] == "Grandpa at the lake"
    for name, s in sha.items():
        expected = 200 if name in ("cora_bart.jpg", "rex.jpg") else 404
        assert c.get(f"{d}/p/{s}").status_code == expected, name
    assert c.get(f"{d}/album/{mine}").status_code == 404 and c.get(f"{d}/albums").status_code == 404
    with pytest.raises(devices.Problem):
        devices.make(con, "No album", "album", album_id=99999)
    sam = signed_in(cfg, con, site["who"]["Sam"])
    assert "the album “Picnic days”" in sam.get("/settings/devices").text


def _shown(c, token) -> set[str]:
    return {p["poster"].rsplit("/", 1)[1] for p in c.get(f"/d/{token}/api/photo-frame").json()["photos"]}


def test_a_frame_never_shows_a_photo_hidden_from_the_family(site, cfg, con):
    """A photo an admin hid from family members is on no frame: not the whole library's, an admin's tour of
    everything, nor an admin's album that holds it (no share passes one on either). A frame is seen by whoever
    passes it."""
    from lineagebox import albums
    ph = site["photos"]
    hidden = catalog.image(con, ph["hidden.jpg"])["sha256"]
    album = albums.create(con, "Everything", None)
    albums.add(con, album, list(ph.values()))
    con.commit()
    tokens = [devices.make(con, "Hall", "memories", home_only=False)[1],
              devices.make(con, "Tour", "tour", tour_id=site["tours"]["admins"], home_only=False)[1],
              devices.make(con, "Album", "album", album_id=album, home_only=False)[1]]
    c = TestClient(webapp.create_app(cfg), client=OUTSIDE)
    for token in tokens:
        shown = _shown(c, token)
        assert shown and hidden not in shown, token
        assert c.get(f"/d/{token}/p/{hidden}").status_code == 404
    assert hidden in {catalog.image(con, i)["sha256"] for i in albums.photo_ids(con, album)}  # it is in the album


def test_a_deleted_albums_frame_never_shows_the_album_given_its_number_next(site, cfg, con):
    """An album's number is given again to the next one made once it is deleted (SQLite's rowid): a frame made for
    the deleted one shows nothing, not the newer album, someone else's."""
    from lineagebox import albums
    ph = site["photos"]
    first = albums.create(con, "Picnic days", site["who"]["Cora"]["id"])
    albums.add(con, first, [ph["cora_bart.jpg"]])
    con.execute("UPDATE album SET created_at = '2026-01-01T10:00:00' WHERE id = ?", (first,))
    con.commit()
    _, token = devices.make(con, "Kitchen", "album", album_id=first, home_only=False)
    con.execute("UPDATE device_link SET made_at = '2026-01-02T10:00:00'")
    albums.delete(con, first)
    later = albums.create(con, "Tina's own", site["who"]["Tina"]["id"])
    albums.add(con, later, [ph["hank.jpg"]])
    con.commit()
    assert later == first
    c = TestClient(webapp.create_app(cfg), client=OUTSIDE)
    assert _shown(c, token) == set()
    assert c.get(f"/d/{token}/p/{catalog.image(con, ph['hank.jpg'])['sha256']}").status_code == 404
    assert devices.every(con)[0]["shows"] == "an album that was deleted"


def test_path_tricks_under_a_device_link(site, cfg, con):
    """What a crafted address could make of /d/<token>/...: dot segments, encoded slashes, a second token, an empty
    one. The server takes the path as sent, %-escapes undone (a browser would tidy it, a script needn't), so this
    goes to the app as the server would hand it over."""
    import asyncio
    from urllib.parse import unquote
    _, token = devices.make(con, "Hall", "memories", home_only=False)
    _, other = devices.make(con, "Den", "memories", account_id=site["who"]["Cora"]["id"], home_only=False)
    app = webapp.create_app(cfg)

    def raw(path: str) -> int:
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
                 "path": unquote(path), "raw_path": path.encode(), "query_string": b"", "root_path": "",
                 "headers": [(b"host", b"testserver")], "client": OUTSIDE, "server": ("testserver", 80)}
        asyncio.run(app(scope, receive, send))
        return next(m["status"] for m in sent if m["type"] == "http.response.start")

    assert raw(f"/d/{token}") == 200 and raw(f"/d/{token}/api/photo-frame") == 200
    for path in (f"/d/{token}/../settings", f"/d/{token}/./settings", f"/d/{token}//settings",
                 f"/d/{token}/settings/../photo-frame/../people", f"/d/{token}/d/{other}/api/photo-frame",
                 f"/d/{token}/d/{other}", f"/d//settings", f"/d/{token}%2Fsettings", f"/d/{token}/%2e%2e/settings",
                 f"/d/{token}/healthz", f"/d/{token}/sw.js", f"/d/{token}/manifest.webmanifest",
                 f"/d/{token}/signin", f"/d/{token}/auth/invite"):
        assert raw(path) == 404, path
    assert raw("/settings") == 303  # and without a link: to the sign-in page


def test_a_switched_off_makers_tour_or_album_shows_nothing(site, cfg, con):
    """A tour or an album shows what its maker may pass on: nothing while the maker is switched off, as their shares
    stop too; a link for memories as an account goes off with it (by_token)."""
    from lineagebox import albums
    cora = site["who"]["Cora"]
    album = albums.create(con, "Picnic days", cora["id"])
    albums.add(con, album, [site["photos"]["cora_bart.jpg"]])
    con.commit()
    tokens = [devices.make(con, "Tour", "tour", tour_id=site["tours"]["Cora"], home_only=False)[1],
              devices.make(con, "Album", "album", album_id=album, home_only=False)[1]]
    c = TestClient(webapp.create_app(cfg), client=OUTSIDE)
    assert all(_shown(c, t) for t in tokens)
    accounts.update(con, cora["id"], disabled=True)
    con.commit()
    c = TestClient(webapp.create_app(cfg), client=OUTSIDE)  # a new web server: nothing kept
    for token in tokens:
        assert c.get(f"/d/{token}").status_code == 200 and _shown(c, token) == set()


def test_a_link_for_another_house_has_the_address_from_outside(site, cfg, con):
    from lineagebox import settings
    settings.save(con, cfg, {"public_url": "https://photos.example.org", "allowed_hosts": ["photos.example.org"]})
    sam = signed_in(cfg, con, site["who"]["Sam"])
    away = sam.post("/api/devices", json={"name": "Grandma's", "view": "memories", "home_only": False}, headers=APP)
    home = sam.post("/api/devices", json={"name": "Hall", "view": "memories", "home_only": True}, headers=APP)
    assert re.search(r'<code class="code">https://photos\.example\.org/d/[\w-]+</code>', away.json()["html"])
    assert re.search(r'<code class="code">http://testserver/d/[\w-]+</code>', home.json()["html"])
    with pytest.raises(devices.Problem):
        devices.make(con, "x", "memories", account_id=99999)
    _, token = devices.make(con, "x", "memories", seconds=7)  # not one offered: the usual
    assert con.execute("SELECT seconds FROM device_link WHERE name = 'x'").fetchone()[0] == 20
