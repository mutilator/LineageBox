"""Answers the web app keeps a while to be quick on a big library (web/site.py Kept), and the Status page's list of
slow pages: nothing kept outlasts a change made on the site, signing out above all."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from fastapi.testclient import TestClient

from lineagebox import access, accounts, db, tagging, tour_suggestions
from lineagebox.web import app as webapp
from lineagebox.web import site as site_module
from tests.conftest import LAN
from tests.test_access_web import APP, signed_in, site  # noqa: F401 (site is a fixture)


def on_one_app(cfg, con, *signed_in_as):
    """Browsers of several accounts on the same app, as on a server: they share what it keeps."""
    app = webapp.create_app(cfg)
    out = []
    for account in signed_in_as:
        c = TestClient(app, client=LAN, follow_redirects=False)
        c.cookies.set(accounts.COOKIE, accounts.new_session(con, account["id"], 30))
        out.append(c)
    return out


def test_signing_out_ends_the_session_at_once(site, cfg, con):
    c = signed_in(cfg, con, site["who"]["Cora"])
    assert c.get("/people").status_code == 200  # her account is kept for the next requests now
    token = c.cookies.get(accounts.COOKIE)
    assert c.post("/auth/signout", headers=APP).status_code == 200
    c.cookies.set(accounts.COOKIE, token)  # the old cookie again, as if someone had copied it
    assert c.get("/people").status_code == 303
    assert c.get(f"/face/{site['open_face']}").status_code == 401


def test_an_admin_signing_someone_out_works_at_once(site, cfg, con):
    sam, cora = on_one_app(cfg, con, site["who"]["Sam"], site["who"]["Cora"])
    assert cora.get("/people").status_code == 200
    assert sam.post(f"/api/accounts/{site['who']['Cora']['id']}/signout", headers=APP).status_code == 200
    assert cora.get("/people").status_code == 303


def test_counts_are_kept_until_something_is_changed_on_the_site(site, cfg, con):
    sam, = on_one_app(cfg, con, site["who"]["Sam"])
    assert "lighthouse" not in sam.get("/tags").text and "lighthouse" not in sam.get("/api/tags/names").json()
    photo = site["photos"]["cora_bart.jpg"]
    tagging.add(con, [photo], "lighthouse")  # as the worker would: the page keeps its counts a minute
    con.commit()
    assert "lighthouse" not in sam.get("/tags").text and "lighthouse" not in sam.get("/api/tags/names").json()
    assert sam.post(f"/api/images/{photo}/tags", json={"tag": "pier"}, headers=APP).status_code == 200
    page = sam.get("/tags").text  # a change on the site: counted again, the worker's tag too
    assert "lighthouse" in page and "pier" in page
    assert sam.get("/tags?q=light").text.count('href="/tag/') == 1  # the filter picks from the kept list
    assert {"lighthouse", "pier"} <= set(sam.get("/api/tags/names").json())


def test_what_is_kept_is_each_viewers_own(site, cfg, con):
    """An admin's kept list of people is never a member's, nor one member's another's."""
    sam, cora, tina = on_one_app(cfg, con, site["who"]["Sam"], site["who"]["Cora"], site["who"]["Tina"])
    assert "Zed" in sam.get("/people").text  # kept now, for the admin: Zed is outside both members' family
    assert "Zed" not in cora.get("/people").text and "Zed" not in tina.get("/people").text
    assert cora.get("/people").text != tina.get("/people").text


def test_a_slow_page_is_listed_on_the_status_page(site, cfg, con, monkeypatch):
    sam, cora = on_one_app(cfg, con, site["who"]["Sam"], site["who"]["Cora"])
    monkeypatch.setattr(site_module, "SLOW_PAGE", 0.0)  # every page is slow
    r = cora.get("/people")
    assert r.headers["server-timing"].startswith("app;dur=")
    cora.get(f"/face/{site['open_face']}")  # pictures aren't listed: they take as long as they are big
    page = sam.get("/status").text
    listed = page[page.index('id="slow-pages"'):page.index("<h2>Catalog</h2>")]
    assert "<code>/people</code>" in listed and "Cora" in listed and "/face/" not in listed


def test_occasions_are_the_same_on_a_members_connection(site, cfg, con, monkeypatch):
    """A member who sees every photo gets the same occasions as an admin: the query reads each photo's tags once
    instead of looking each of a member's photos up for every tag, which took a minute on a big library."""
    monkeypatch.setattr(tour_suggestions, "OCCASION_PHOTOS", 3)
    monkeypatch.setattr(tour_suggestions, "OCCASION_YEARS", 3)
    photos = list(site["photos"].values())
    for i, photo in enumerate(photos):  # a birthday in June, a year apart
        con.execute("UPDATE image SET taken_at = ?, taken_at_source = 'exif' WHERE id = ?",
                    (f"{2010 + i}-06-12T12:00:00", photo))
    tagging.add(con, photos, "birthday")
    con.commit()
    ids = [r[0] for r in con.execute("SELECT id FROM image")]
    sees_all = access.Viewer(account={"id": 999, "person_id": None, "role": "member"}, admin=False,
                             images=frozenset(ids), lists={"person": "[]", "image": json.dumps(ids),
                                                           "folder": "[]", "tour": "[]"})
    member = access.scope(db.connect(cfg, migrate_schema=False), sees_all)

    def facts(c):
        day = dict(c.execute("SELECT id, substr(taken_at, 1, 10) FROM image WHERE taken_at IS NOT NULL"))
        return tour_suggestions._Facts(date.today(), 1, day, {})

    found = tour_suggestions._occasions(con, facts(con))
    assert [name for name, *_ in found] == ["birthday"]
    assert tour_suggestions._occasions(member, facts(member)) == found
    member.close()


def test_worked_out_once_however_many_ask_at_once(site, cfg, con, monkeypatch):
    """After a change, the thumbnails and face crops a page asks for at once all need the member's view, and several
    name boxes the same list: one request works each out, and the others wait for it instead of doing it too."""
    calls = []
    real = access.compute

    def slow(*args, **kwargs):
        calls.append(1)
        time.sleep(0.2)
        return real(*args, **kwargs)

    monkeypatch.setattr(access, "compute", slow)
    viewers = access.Viewers()
    cora = dict(accounts.get(con, site["who"]["Cora"]["id"]))

    def view(_):
        c = db.connect(cfg, migrate_schema=False, check_same_thread=False)
        try:
            return viewers.get(c, cora).images
        finally:
            c.close()

    with ThreadPoolExecutor(8) as pool:
        seen = list(pool.map(view, range(8)))
    assert len(calls) == 1 and all(s == seen[0] for s in seen)
    viewers.changed()  # a change: worked out again, once
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(view, range(8)))
    assert len(calls) == 2

    kept, worked = site_module.Kept(), []

    def names():
        worked.append(1)
        time.sleep(0.2)
        return ["Ann", "Bob"]

    with ThreadPoolExecutor(8) as pool:
        answers = list(pool.map(lambda _: kept.get(("names",), 60, names), range(8)))
    assert len(worked) == 1 and answers == [["Ann", "Bob"]] * 8
    kept.changed()
    assert kept.get(("names",), 60, names) == ["Ann", "Bob"] and len(worked) == 2
