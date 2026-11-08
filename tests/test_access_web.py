"""Family accounts in the web app: every route has a rule, nobody gets in without signing in, and a member gets nothing
from outside their part of the family from any page, API or picture, nor changes anything there."""
import json
import re
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from lineagebox import access, accounts, catalog, devices, memories, outbox, settings, sharing, tours
from lineagebox import albums, cleanup, comments, documents, face_ages, favourites, life, places, stories
from lineagebox.web import app as webapp
from lineagebox.web import site as site_module
from lineagebox.web.common import _from_app
from lineagebox.web.routes import cleanup as cleanup_routes
from lineagebox.web.routes import naming as naming_routes
from tests import family_fixture as fx
from tests.conftest import LAN

APP = {"X-Requested-With": "lineagebox"}
OUTSIDE = ("203.0.113.9", 50000)  # not the home network


@pytest.fixture
def site(con, cfg, library):
    fam = fx.build(con, cfg, library)
    p = fam["people"]
    who = {"Sam": fx.account(con, p["Sam"], "sam@example.com", "admin"),
           "Cora": fx.account(con, p["Cora"], "cora@example.com"),
           "Tina": fx.account(con, p["Tina"], "tina@example.com")}
    spec = tours.clean({"title": "Everything"})
    rex = tours.clean({"title": "Rex", "who": {"mode": "person", "person_ids": [p["Rex"]]}})  # both members see him
    fam["tours"] = {"admins": tours.save_tour(con, None, None, spec, "Admins' own"),
                    "shared": tours.save_tour(con, None, None, rex, "Shared with the family"),
                    "Cora": tours.save_tour(con, None, None, spec, "Cora's", who["Cora"]["id"]),
                    "Tina": tours.save_tour(con, None, None, spec, "Tina's", who["Tina"]["id"])}
    sharing.share(con, who["Sam"]["id"], "tour", fam["tours"]["shared"], None)
    # albums (albums.py): the admins' own and each member's, of every photo (Zed's captioned with the marker), and one
    # of Rex the admins share with everyone (an admin's album passes on all its photos, as an admin's tour does)
    ph_all = list(fam["photos"].values())
    fam["albums"] = {}
    for key, name, owner in (("admins", "Admins' album", None), ("Cora", "Cora's album", who["Cora"]["id"]),
                             ("Tina", "Tina's album", who["Tina"]["id"])):
        fam["albums"][key] = albums.create(con, name, owner)
        albums.add(con, fam["albums"][key], ph_all)
        albums.set_caption(con, fam["albums"][key], fam["photos"]["zed.jpg"], f"{fx.SECRET} album caption")
    fam["albums"]["shared"] = albums.create(con, "Album for the family", None)
    albums.add(con, fam["albums"]["shared"], [fam["photos"]["rex.jpg"]])
    sharing.share(con, who["Sam"]["id"], "album", fam["albums"]["shared"], None)
    sharing.remember_album(con, fam["albums"]["shared"], [fam["photos"]["rex.jpg"]])
    favourites.put(con, access.Viewer(account=who["Tina"], admin=False), fam["photos"]["zed.jpg"], True)
    # an unnamed face beside Cora and Bart, and one in Zed's photo, grouped together by the clustering
    fam["open_face"] = con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id)
                                      VALUES (?, 0.7, 0.1, 0.9, 0.5, 0.9, 25, ?, 77)""",
                                   (fam["photos"]["cora_bart.jpg"], fx.unit(901).tobytes())).lastrowid
    fam["zed_open_face"] = con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding,
                                                           cluster_id) VALUES (?, 0.6, 0.1, 0.9, 0.5, 0.9, 25, ?, 77)""",
                                       (fam["photos"]["zed.jpg"], fx.unit(902).tobytes())).lastrowid
    con.execute("INSERT INTO citation(image_id, person_id) VALUES (?, ?)", (fam["photos"]["zed.jpg"], p["Zed"]))
    # New for you (outbox.py): events about photos each member may or may not see, and an admin's alert, for the sweeps
    zed = fam["photos"]["zed.jpg"]
    con.execute("INSERT INTO face_question(face_id, account_id, asked_at) VALUES (?, ?, '2026-01-01')",
                (fam["zed_open_face"], who["Sam"]["id"]))
    outbox.asked(con, fam["zed_open_face"], who["Sam"]["id"])
    outbox.add(con, "new_photos", data={"ids": sorted(fam["photos"].values())})
    words = con.execute("""INSERT INTO comment(image_id, kind, text, account_id, at)
                           VALUES (?, 'comment', ?, ?, '2026-01-01')""",
                        (zed, f"{fx.SECRET} words", who["Sam"]["id"])).lastrowid
    for name in ("Cora", "Tina"):  # as if they had a reason to be told: they can't see the photo, so they aren't
        outbox.add(con, "comment", to=who[name]["id"], by=who["Sam"]["id"], image_id=zed, comment_id=words)
    outbox.add(con, "damaged", admins=True, data={"files": 3})
    con.commit()
    settings.save(con, cfg, {"accounts_enabled": True})
    fam["who"] = who
    return fam


@pytest.fixture
def swept(site, cfg, con):
    """The site with what the newer features keep about Zed, whom no member sees: a recorded story about him and
    one on his photo, a written source cited only for him, his photo put on the map by hand, a photo frame's link;
    and, on the picnic photo both members see, a story and a comment of Sam's (not theirs to change)."""
    p, ph = site["people"], site["photos"]
    zed, sam = ph["zed.jpg"], site["who"]["Sam"]["id"]
    sound = b"OggS" + bytes(200)
    site["stories"] = {
        "zed": stories.save(con, cfg, sound, sam, person_id=p["Zed"], title=f"{fx.SECRET} story"),
        "zed_photo": stories.save(con, cfg, sound, sam, image_id=zed, title=f"{fx.SECRET} photo story"),
        "picnic": stories.save(con, cfg, sound, sam, image_id=ph["cora_bart.jpg"], title="At the picnic")}
    site["source"] = documents.save_source(con, None, f"{fx.SECRET} diary")
    site["source_citation"] = documents.cite_source(con, site["source"], f"p:{p['Zed']}")
    places.place_photo(con, zed, 51.5, -0.1, sam)
    site["sams_comment"] = comments.add_comment(con, ph["cora_bart.jpg"], "Sam remembers", sam)
    site["device"] = devices.make(con, "Hall", "memories", home_only=False)[0]
    con.commit()
    return site


def signed_in(cfg, con, account, client=LAN, **app_options):
    c = TestClient(webapp.create_app(cfg, **app_options), client=client, follow_redirects=False)
    c.cookies.set(accounts.COOKIE, accounts.new_session(con, account["id"], 30))
    return c


def test_every_route_has_a_rule(cfg):
    app = webapp.create_app(cfg)
    paths = {r.path for r in webapp.all_routes(app) if hasattr(r, "methods")}
    assert len(paths) > 150
    assert paths - set(webapp.ROUTES) == set(), "routes with no rule in ROUTES"
    assert set(webapp.ROUTES) - paths == set(), "rules for routes that don't exist"


# POSTs that come without the app's header, as plain posts that change nothing: the phone's share sheet (which can't
# add one; it only leads to Upload), and the ticked photos' zip (a form, so the browser saves the file)
PLAIN_POSTS = {"/upload/shared", "/download"}


def test_every_change_comes_from_the_apps_own_scripts(cfg):
    """Every POST needs X-Requested-With (common._from_app), which a page on another site can't send: so no other
    site can make a signed-in browser change anything."""
    def needs(dependant):
        return any(d.call is _from_app or needs(d) for d in dependant.dependencies)
    app = webapp.create_app(cfg)
    posts = {r.path: r for r in webapp.all_routes(app) if "POST" in getattr(r, "methods", ())}
    assert len(posts) > 100
    assert {path for path, r in posts.items() if not needs(r.dependant)} == PLAIN_POSTS
    c = TestClient(app, client=LAN)
    assert c.post("/api/images/1/caption", json={"text": "x"}).status_code == 403  # without it


def test_the_accounts_page_folds_each_account_away(site, cfg, con):
    """Add an account first, a search box, and each account folded under its person's name: Sam (admin) first."""
    page = signed_in(cfg, con, site["who"]["Sam"]).get("/accounts").text
    section = page.split('id="accounts"')[1].split("</section>")[0]
    assert section.index("Add an account") < section.index('data-filter=".account-row"') < section.index("<details")
    rows = re.findall(r'<details class="account-row" id="a(\d+)" data-keep-open\s+data-text="([^"]*)">\s*'
                      r'<summary><b>([^<]+)</b>', section)
    assert [(int(i), name) for i, _, name in rows] == [(site["who"][n]["id"], n) for n in ("Sam", "Cora", "Tina")]
    assert "cora@example.com member" in rows[1][1] and " open" not in section.split("<summary>")[0]


def test_nobody_gets_in_without_signing_in(site, cfg, con):
    for where in (LAN, OUTSIDE):  # at home too, once accounts are on
        c = TestClient(webapp.create_app(cfg), client=where, follow_redirects=False)
        r = c.get("/people")
        assert r.status_code == 303 and r.headers["location"] == "/signin?next=%2Fpeople"
        sha = catalog.image(con, site["photos"]["cora_bart.jpg"])["sha256"]
        for url in ("/api/people/names", f"/t/{sha}", f"/face/{site['faces']['cora_bart.jpg'][0]}", "/api/map"):
            assert c.get(url).status_code == 401, url
        assert c.post("/api/images/1/tags", json={"tag": "x"}, headers=APP).status_code == 401
        assert c.get("/healthz").text == "ok"
        page = c.get("/signin")
        assert page.status_code == 200 and "Sign in" in page.text and "<nav>" not in page.text
    # a stale or made-up cookie is nobody
    c = TestClient(webapp.create_app(cfg), client=OUTSIDE, follow_redirects=False)
    c.cookies.set(accounts.COOKIE, "made-up")
    assert c.get("/people").status_code == 303


def test_from_outside_once_signed_in(site, cfg, con, monkeypatch):
    c = signed_in(cfg, con, site["who"]["Cora"], client=OUTSIDE)
    assert c.get("/people").status_code == 200
    # switched off outside the site (the accounts command): out once her sign-in is looked up again, SIGNED_IN_FOR
    # seconds at most (here none). Through the site it is at once (test_kept.py).
    monkeypatch.setattr(site_module, "SIGNED_IN_FOR", 0)
    accounts.update(con, site["who"]["Cora"]["id"], disabled=True)
    con.commit()
    assert c.get("/people").status_code == 303


def test_admins_see_everything(site, cfg, con):
    c = signed_in(cfg, con, site["who"]["Sam"])
    zed = site["photos"]["zed.jpg"]
    assert c.get(f"/image/{zed}").status_code == 200 and fx.SECRET in c.get(f"/image/{zed}").text
    assert c.get(f"/person/{site['people']['Zed']}").status_code == 200
    assert c.get("/settings").status_code == 200 and c.get("/status").status_code == 200
    assert c.get("/accounts").status_code == 200


def _hidden(site, con, who):
    v = access.compute(con, site["who"][who])
    p, ph = site["people"], site["photos"]
    return {
        "viewer": v,
        "names": [n for n in fx.PEOPLE if n not in fx.SEES[who]],
        "people": [p[n] for n in fx.PEOPLE if n not in fx.SEES[who]],
        "images": [i for i in ph.values() if i not in v.images],
        "shas": [catalog.image(con, i)["sha256"] for i in ph.values() if i not in v.images],
        "faces": [f for name, fs in site["faces"].items() for f in fs if ph[name] not in v.images]
                 + [site["zed_open_face"]],
        "files": [r[0] for r in con.execute("SELECT id FROM file WHERE image_id IN (SELECT value FROM json_each(?))",
                                            (json.dumps([i for i in ph.values() if i not in v.images]),))],
    }


def _urls(app, con, site):
    """Every GET route with every id of the catalog in it, and query strings that name things."""
    ids = {
        "image_id": [r[0] for r in con.execute("SELECT id FROM image")],
        "person_id": list(site["people"].values()) + [99999],
        "face_id": [r[0] for r in con.execute("SELECT id FROM face")],
        "file_id": [r[0] for r in con.execute("SELECT id FROM file")],
        "folder_id": [r[0] for r in con.execute("SELECT id FROM folder")],
        "tour_id": list(site["tours"].values()),
        "album_id": list(site["albums"].values()),
        "year": [r[0] for r in con.execute("SELECT DISTINCT CAST(substr(taken_at, 1, 4) AS INT) FROM image")],
        "name": [r[0] for r in con.execute("SELECT name FROM tag")] + ["nothing"],
        "sha": [r[0] for r in con.execute("SELECT sha256 FROM image")],
        "value": ["gps", "videos", "format/jpeg"], "import_id": [1], "reading_id": [1], "kind": ["papers"], "type_id": ["new", "1"],
        "part": list(cleanup_routes.COUNTS),
        "event_id": [r[0] for r in con.execute("SELECT id FROM outbox")] + [99999],
        "story_id": [r[0] for r in con.execute("SELECT id FROM story")] + [99999],
    }
    out = []
    for route in webapp.all_routes(app):
        if "GET" not in getattr(route, "methods", ()) or route.path.startswith("/auth/google"):
            continue
        params = re.findall(r"{(\w+)(?::\w+)?}", route.path)
        if not params:
            out.append(route.path)
            continue
        template = re.sub(r"{(\w+):\w+}", r"{\1}", route.path)
        first, *rest = params
        for value in ids[first]:
            out.append(template.format(**{first: value, **{k: 0 for k in rest}}))
    queries = [f"/search?q={n}" for n in fx.PEOPLE + [fx.SECRET, "party"]]
    queries += [f"/family?center={i}" for i in site["people"].values()]
    queries += [f"/family/tree?root={i}" for i in site["people"].values()]
    queries += [f"/family/tree?person={i}" for i in site["people"].values()]
    queries += [f"/image/{i}?ctx=person:{p}" for i in ids["image_id"][:3] for p in site["people"].values()]
    # tours not saved yet (Home's memories): of everyone's photos, and of each person
    queries += [memories.try_href({"spec": {"title": "Everything", "media": "both"}})]
    queries += [memories.try_href({"spec": {"title": "{person}", "who": {"mode": "person", "person_ids": [i]}}})
                for i in site["people"].values()]
    queries += ["/api/map?year=2014", "/tours/new?type=1", "/family/export", "/family/export?photos=true",
                "/cleanup/faces?show=finish", "/cleanup/faces?show=groups", "/cleanup/faces?show=together",
                "/cleanup/faces?show=ages", "/cleanup/faces?show=missed", "/cleanup/camera-clock?camera=x",
                "/cleanup/camera-clock?camera=x&from=1&to=end&photo=1", "/cleanup/dates?show=photos",
                "/tags?q=zebra", "/settings?browse=lib"]
    # what is typed into search, letter by letter, and its filters; a photo's ←/→ through each kind of list
    queries += [f"/api/search/suggest?q={n[:k]}" for n in fx.PEOPLE + [fx.SECRET, "party"] for k in (2, 3, 99)]
    queries += [f"/search?people={i}" for i in site["people"].values()]
    queries += [f"/search?q=party&people={i}&sort=newest" for i in site["people"].values()]
    queries += ["/search?unnamed=1", "/search?undated=1", "/search?media=videos", f"/search?place={fx.SECRET}"]
    first = ids["image_id"][:3]
    queries += [f"/image/{i}?" + urlencode({"ctx": f"search:?people={p}"}) for i in first
                for p in site["people"].values()]
    queries += [f"/image/{i}?ctx={kind}:{x}" for i in first for kind, xs in (("album", ids["album_id"]),
                                                                          ("tour", ids["tour_id"])) for x in xs]
    queries += [f"/image/{i}?ctx=tag:{n}" for i in first for n in ids["name"]]
    # a frame of a tour, the queue of faces to name a step at a time, cleanup's lists of their own uploads
    queries += [f"{where}?tour={t}" for where in ("/photo-frame", "/api/photo-frame") for t in ids["tour_id"]]
    queries += [f"/people/name?only={step}" for step in naming_routes.STEPS]
    queries += [f"/cleanup/similar?kind={k}" for k in cleanup.SIMILAR_KINDS]
    # each group of everyone's life by age, as an admin's page links to them
    queries += [f"/person/{pid}/life/{x.key}" for pid in site["people"].values()
                for x in life.life(con, pid).periods] + [f"/person/{pid}/life/undated" for pid in site["people"].values()]
    # long lists a piece at a time, and with their photos to tick (pieces.py): the pieces app.js loads, of every list
    queries += [f"/folder/{i}?{q}" for i in ids["folder_id"] for q in ("select=1", "skip=0&upto=9&piece=1")]
    queries += [f"/tour/{i}?{q}" for i in ids["tour_id"] for q in ("select=1", "select=1&piece=1")]
    queries += [f"/timeline/{y}?select=1&piece=1" for y in ids["year"]] + [f"/tag/{n}?piece=1" for n in ids["name"]]
    queries += [f"/person/{pid}?select=1" for pid in site["people"].values()]
    queries += [f"/person/{pid}/life/{x.key}?select=1" for pid in site["people"].values()
                for x in life.life(con, pid).periods]
    return out + queries


@pytest.mark.parametrize("who", ["Cora", "Tina"])
def test_the_leak_sweep(swept, cfg, con, who):
    """Every page, API and picture, with every id there is: what isn't theirs is not found, nothing breaks, no
    answer holds anything from outside their part of the family, and none of them changes anything."""
    site = swept
    hidden = _hidden(site, con, who)
    c = signed_in(cfg, con, site["who"][who])
    app = c.app
    hank_sam = site["photos"]["hank_sam.jpg"]
    checked = 0
    c.get("/home")  # her session's first use is written now (accounts.TOUCH_EVERY), not during the sweep
    before = _snapshot(con)
    for url in _urls(app, con, site):
        r = c.get(url)
        assert r.status_code < 500, (url, r.status_code, r.text[:300])
        if r.status_code != 200:
            continue
        text = r.content.decode("utf-8", "replace")
        checked += 1
        assert fx.SECRET in url or fx.SECRET not in text, url  # a word searched for is echoed back
        for sha in hidden["shas"]:
            assert sha not in text, (url, "a hidden photo's picture")
        for pid in hidden["people"]:
            assert not re.search(rf'/person/{pid}\b|"person_id": ?{pid}\b|"id": ?{pid}\b', text), (url, pid)
        for fid in hidden["faces"]:
            assert f"/face/{fid}\"" not in text and f"/api/faces/{fid}/" not in text, (url, fid)
        for name in hidden["names"]:
            # a name typed in a search is echoed back; Tina's father may be named, without a link, on the photo of
            # him with her husband, which Cora sees
            if name in url or (name == "Hank" and url.startswith(f"/image/{hank_sam}")):
                continue
            assert not re.search(rf"\b{name}\b", text), (url, name)
    assert checked > 100
    after = _snapshot(con)
    assert {t for t in before if before[t] != after[t]} == set(), "a GET changed the catalog"
    # and what is not theirs is "not found", like something that isn't there
    for story in (site["stories"]["zed"], site["stories"]["zed_photo"]):
        assert c.get(f"/story/{story}").status_code == 404
    assert c.get(f"/story/{site['stories']['picnic']}").status_code == 200
    for i in hidden["images"]:
        assert c.get(f"/image/{i}").status_code == 404
        assert c.get(f"/play/{i}").status_code == 404
    for sha in hidden["shas"]:
        assert c.get(f"/t/{sha}").status_code == 404 and c.get(f"/p/{sha}").status_code == 404
        assert c.get(f"/t2/{sha}").status_code == 404  # nor the sharp tile (tiles.py), made or not
    for pid in hidden["people"]:
        assert c.get(f"/person/{pid}").status_code == 404 and c.get(f"/person/{pid}/faces").status_code == 404
    for fid in hidden["faces"]:
        assert c.get(f"/face/{fid}").status_code == 404
    for file_id in hidden["files"]:
        assert c.get(f"/original/{file_id}").status_code == 404
    for path in ("/settings", "/status", "/accounts", "/cleanup/removed", "/cleanup/copies", "/photos/gps",
                 f"/folder/{catalog.root_folder_id(con)}", f"/tour/{site['tours']['admins']}",
                 f"/tour/{site['tours']['Tina' if who == 'Cora' else 'Cora']}"):
        assert c.get(path).status_code == 404, path
    assert c.get(f"/tour/{site['tours']['shared']}").status_code == 200
    assert c.get(f"/tour/{site['tours'][who]}").status_code == 200
    other = "Tina" if who == "Cora" else "Cora"
    for album in (site["albums"]["admins"], site["albums"][other]):  # nor others' albums, their videos or zips
        for path in (f"/album/{album}", f"/album/{album}/video", f"/download/album/{album}",
                     f"/download/album/{album}/video"):
            assert c.get(path).status_code == 404, path
    assert c.get(f"/album/{site['albums']['shared']}").status_code == 200
    assert c.get(f"/album/{site['albums'][who]}").status_code == 200
    for pid in hidden["people"]:  # nor a tour of them, saved or not
        spec = {"title": "{person}", "who": {"mode": "person", "person_ids": [pid]}}
        assert c.get(memories.try_href({"spec": spec})).status_code == 404
    assert "/status#storage" not in c.get("/home").text  # how full the disks are is for admins


def test_pictures_are_not_kept_by_proxies(site, cfg, con):
    c = signed_in(cfg, con, site["who"]["Cora"])
    sha = catalog.image(con, site["photos"]["cora_bart.jpg"])["sha256"]
    r = c.get(f"/t/{sha}")
    assert r.status_code == 200 and r.headers["cache-control"].startswith("private")
    assert r.headers["x-content-type-options"] == "nosniff"


def test_pages_run_only_the_sites_own_scripts(site, cfg, con):
    """A Content-Security-Policy that allows no inline script, which only works while no page has one."""
    policy = signed_in(cfg, con, site["who"]["Cora"]).get("/map").headers["content-security-policy"]
    assert "script-src 'self';" in policy and "https://tile.openstreetmap.org" in policy
    inline = re.compile(r"<script(?![^>]*\b(src|type=\"application/json\"))[^>]*>|\son[a-z]+=|javascript:", re.I)
    for template in (webapp.HERE / "templates").glob("*.html"):
        assert not inline.search(template.read_text(encoding="utf-8")), template.name


def test_what_a_member_sees_of_a_photo(site, cfg, con):
    c = signed_in(cfg, con, site["who"]["Cora"])
    page = c.get(f"/image/{site['photos']['hank_sam.jpg']}").text
    hank = site["people"]["Hank"]
    assert "Hank" in page and f"/person/{hank}" not in page  # named, without a link
    assert f"/api/faces/{site['faces']['hank_sam.jpg'][0]}/not" not in page  # and not hers to change
    assert "lib/Bakers" not in page and "Copy network path" not in page and "Download the original" in page
    assert "Remove photo" not in page and "Keep (don" not in page  # not her upload


def _snapshot(con):
    tables = ("person", "face", "parent", "partner", "sibling", "relation", "image_tag", "tag", "image_person",
              "event", "person_name", "person_group", "document", "citation", "tour", "account", "access_rule",
              "face_question", "face_not_person", "file", "share", "album", "album_photo", "favourite",
              "slideshow_video", "comment", "story", "source", "source_citation", "image_place", "folder_place",
              "describe_request", "device_link", "outbox", "tag_alias", "camera_clock", "scan_reading")
    out = {t: con.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall() for t in tables}
    out["image"] = con.execute("SELECT id, taken_at, taken_at_source, hidden, faces_again FROM image "
                               "ORDER BY id").fetchall()
    out["ages_ok"] = con.execute("SELECT key, value FROM settings WHERE key IN (?, 'scan_dates_ok')",
                                 (face_ages.OK_KEY,)).fetchall()
    return {t: [tuple(r) for r in rows] for t, rows in out.items()}


def test_the_write_sweep(swept, cfg, con):
    """Every change a member can ask for, about things outside their part of the family: refused, and nothing in
    the catalog changes."""
    site = swept
    c = signed_in(cfg, con, site["who"]["Cora"])
    p, ph, f = site["people"], site["photos"], site["faces"]
    zed, zp, bart, cora = ph["zed.jpg"], p["Zed"], p["Bart"], p["Cora"]
    zface, hank_face = f["zed.jpg"][0], f["hank_sam.jpg"][0]
    event = con.execute("SELECT id FROM event WHERE person_id = ?", (zp,)).fetchone()[0]
    nick = con.execute("SELECT id FROM person_name WHERE person_id = ?", (zp,)).fetchone()[0]
    citation = con.execute("SELECT id FROM citation").fetchone()[0]
    zfile = con.execute("SELECT id FROM file WHERE image_id = ?", (zed,)).fetchone()[0]
    cora_file = con.execute("SELECT id FROM file WHERE image_id = ?", (ph["cora_bart.jpg"],)).fetchone()[0]
    sel = {"image_ids": [ph["cora_bart.jpg"], zed]}
    picnic = con.execute("SELECT folder_id FROM file WHERE image_id = ?", (ph["cora_bart.jpg"],)).fetchone()[0]
    zed_folder = con.execute("SELECT folder_id FROM file WHERE image_id = ?", (zed,)).fetchone()[0]
    admins_share = con.execute("SELECT id FROM share").fetchone()[0]
    t, st = site["tours"], site["stories"]
    zed_words = con.execute("SELECT id FROM comment WHERE image_id = ?", (zed,)).fetchone()[0]
    sam = signed_in(cfg, con, site["who"]["Sam"])  # an admin's change Cora's Undo mustn't take back
    sams_change = sam.post(f"/api/images/{ph['rex.jpg']}/caption", json={"text": "Grandpa"}, headers=APP).json()["undo"]
    attempts = [
        # sharing what isn't hers: a library folder, a photo she sees but didn't upload, a hidden one, others' tours,
        # her own tour with an account that isn't there; stopping an admin's share; removing library folders
        ("/api/shares", {"folder_id": picnic, "to": "everyone"}),
        ("/api/shares", {"folder_id": zed_folder, "to": "everyone"}),
        ("/api/shares", {"image_id": ph["cora_bart.jpg"], "to": "everyone"}),
        ("/api/shares", {"image_id": zed, "to": "everyone"}),
        ("/api/shares", {"tour_id": t["Tina"], "to": "everyone"}),
        ("/api/shares", {"tour_id": t["admins"], "to": "everyone"}),
        ("/api/shares", {"tour_id": t["shared"], "to": "everyone"}),
        ("/api/shares", {"tour_id": t["Cora"], "to": "99999"}),
        (f"/api/shares/{admins_share}/delete", None),
        (f"/api/folders/{picnic}/remove", None), (f"/api/folders/{zed_folder}/remove", None),
        (f"/api/folders/{picnic}/remove-files", {"image_ids": [ph["cora_bart.jpg"]]}),
        (f"/api/people/{zp}/link", {"word": "son", "name": "New Kid"}),
        (f"/api/people/{cora}/link", {"word": "son", "name": "New Kid", "with_parent": str(zp)}),
        (f"/api/images/{zed}/document", {"kind": "Letter", "title": "x"}),
        (f"/api/images/{zed}/document/remove", None),
        (f"/api/images/{zed}/citations", {"target": f"p:{cora}"}),
        (f"/api/citations/{citation}/delete", None),
        (f"/api/people/{zp}/groups", {"name": "Friends"}), (f"/api/people/{zp}/groups/remove", {"name": "Friends"}),
        (f"/api/people/{zp}/sex", {"sex": "female"}), (f"/api/people/{zp}/details", {"birth_date": "1960"}),
        (f"/api/people/{zp}/events", {"tag": "DEAT", "date": "2000"}),
        (f"/api/people/{cora}/events", {"tag": "MARR", "partner": str(zp)}),
        (f"/api/events/{event}/delete", None), (f"/api/people/{zp}/name-parts", {"given": "Z", "surname": "Q"}),
        (f"/api/people/{zp}/names", {"name": "Zee", "type": "nickname"}), (f"/api/names/{nick}/delete", None),
        ("/api/family/unlink", {"kind": "partner", "a": zp, "b": p["Zoe"]}),
        ("/api/family/link", {"a": cora, "b": zp, "what": "father"}),
        ("/api/family/current", {"a": zp, "b": p["Zoe"], "what": "former"}),
        (f"/api/people/{zp}/rename", {"name": "Someone"}), (f"/api/people/{bart}/merge", {"into": cora}),
        (f"/api/people/{zp}/remove", None), (f"/api/people/{cora}/remove", None),
        (f"/api/people/{zp}/faces", {"yes": [], "no": []}),
        (f"/api/people/{cora}/faces", {"yes": [zface], "no": []}),
        (f"/api/faces/{zface}/name", {"name": "Cora"}), (f"/api/faces/{hank_face}/name", {"name": "Cora"}),
        (f"/api/faces/{zface}/not", None), (f"/api/faces/{hank_face}/not", None),
        (f"/api/faces/{zface}/not-a-face", None), (f"/api/faces/{zface}/reject", None),
        (f"/api/faces/{site['zed_open_face']}/ask", None), (f"/api/faces/{site['zed_open_face']}/skip", None),
        (f"/api/clusters/77/name?face={site['zed_open_face']}", {"name": "Cora"}),
        (f"/api/images/{zed}/faces", None), (f"/api/images/{zed}/tags", {"tag": "x"}),
        (f"/api/images/{zed}/tags/remove", {"tag": f"{fx.SECRET}tag"}),
        (f"/api/images/{zed}/people", {"name": "Cora"}), (f"/api/images/{zed}/people/{zp}/remove", None),
        ("/api/selection/tag", {**sel, "add_tag": "x"}), ("/api/selection/date", {**sel, "date": "1999"}),
        ("/api/selection/untag?tag=x", sel), ("/api/selection/person", {**sel, "person": "Cora"}),
        (f"/api/images/{zed}/date", {"date": "1999"}),
        ("/api/images/remove", {"image_ids": [ph["cora_bart.jpg"]]}),  # hers to see, not hers to remove
        (f"/api/images/{ph['cora_bart.jpg']}/remove", None), (f"/api/files/{cora_file}/remove", None),
        (f"/api/files/{zfile}/remove", None), (f"/api/images/{ph['cora_bart.jpg']}/turn", {"degrees": 90}),
        (f"/api/tours/{site['tours']['admins']}", {"name": "x"}),
        (f"/api/tours/{site['tours']['shared']}", {"name": "x"}),
        (f"/api/tours/{site['tours']['Tina']}/delete", None),
        # albums: others' (an admin's shared with her too), and photos she can't see in her own; a heart on one
        (f"/api/albums/{site['albums']['Tina']}", {"name": "x"}),
        (f"/api/albums/{site['albums']['shared']}/delete", None),
        (f"/api/albums/{site['albums']['admins']}/order", {"image_ids": [zed]}),
        (f"/api/albums/{site['albums']['shared']}/caption", {"image_id": ph["rex.jpg"], "text": "x"}),
        (f"/api/albums/{site['albums']['Tina']}/remove", {"image_ids": [ph["rex.jpg"]]}),
        (f"/api/albums/{site['albums']['Cora']}/cover", {"image_id": zed}),
        (f"/api/albums/{site['albums']['Cora']}/caption", {"image_id": zed, "text": "x"}),
        (f"/api/albums/{site['albums']['Cora']}/remove", {"image_ids": [zed]}),
        ("/api/albums/add", {"album_id": site["albums"]["Cora"], "image_ids": [zed]}),
        ("/api/albums/add", {"album_id": site["albums"]["shared"], "image_ids": [ph["rex.jpg"]]}),
        ("/api/albums/add", {"album": "New", "image_ids": [ph["rex.jpg"], zed]}),
        ("/api/albums", {"name": "New", "image_ids": [zed]}),
        ("/api/shares", {"album_id": site["albums"]["Tina"], "to": "everyone"}),
        ("/api/shares", {"album_id": site["albums"]["shared"], "to": "everyone"}),
        (f"/api/albums/{site['albums']['Tina']}/video", None),
        (f"/api/images/{zed}/favourite", {"on": True}),
        # a tour of people she can't see: its name would have been made from theirs
        ("/api/tours", {"spec": {"title": "{people}", "who": {"mode": "people", "person_ids": [zp, p["Hank"]]}},
                        "name": ""}),
        ("/api/family/me", {"person_id": zp}), ("/api/family/names/confirm", None),
        ("/api/settings", {"blurry_below": 10}), ("/api/worker/run", None), ("/api/people/identify", None),
        ("/api/accounts", {"email": "x@example.com", "name": "Cora"}),
        (f"/api/accounts/{site['who']['Cora']['id']}", {"role": "admin"}),
        ("/api/see-as", {"account_id": site["who"]["Tina"]["id"]}),
        ("/api/settings/accounts", {"accounts_enabled": False}),
        ("/api/images/keep", {"image_ids": [ph["cora_bart.jpg"]]}), ("/api/removed/empty", None),
        ("/api/places/fix", {"name": "x"}),
        # captions and comments on a photo she can't see; Sam's comment on one she sees, which isn't hers to change
        (f"/api/images/{zed}/caption", {"text": "x"}), (f"/api/images/{zed}/comments", {"text": "x"}),
        (f"/api/comments/{zed_words}/edit", {"text": "x"}), (f"/api/comments/{zed_words}/delete", None),
        (f"/api/comments/{site['sams_comment']}/edit", {"text": "x"}),
        (f"/api/comments/{site['sams_comment']}/delete", None),
        # stories about Zed, his photo, his life event; Sam's story on the picnic photo
        (f"/api/stories?image={zed}", None), (f"/api/stories?person={zp}", None),
        (f"/api/stories?person={cora}&event={event}", None),
        (f"/api/stories/{st['zed']}/words", {"title": "x"}), (f"/api/stories/{st['zed']}/remove", None),
        (f"/api/stories/{st['zed_photo']}/remove", None), (f"/api/stories/{st['picnic']}/words", {"title": "x"}),
        (f"/api/stories/{st['picnic']}/remove", None), (f"/api/images/{zed}/transcript", {"start": 0, "text": "x"}),
        # his life story, portrait and sources; a portrait of her from a face that isn't hers; his source cited for her
        (f"/api/people/{zp}/story", {"biography": "x"}), (f"/api/people/{zp}/portrait", {"face_id": zface}),
        (f"/api/people/{cora}/portrait", {"face_id": hank_face}),
        (f"/api/people/{zp}/sources", {"target": f"p:{zp}", "title": "x"}),
        (f"/api/people/{cora}/sources", {"target": f"e:{event}", "title": "x"}),
        (f"/api/people/{cora}/sources", {"target": f"p:{cora}", "source_id": site["source"]}),
        (f"/api/sources/{site['source']}", {"title": "x"}),
        (f"/api/source-citations/{site['source_citation']}/delete", None),
        # the naming queue's answers about his face, or naming hers as him
        (f"/api/faces/{zface}/yes", {"person_id": cora}), (f"/api/faces/{zface}/no", {"person_id": zp}),
        (f"/api/faces/{f['cora_bart.jpg'][0]}/yes", {"person_id": zp}), (f"/api/people/{zp}/yes", {"yes": [zface]}),
        (f"/api/people/{cora}/yes", {"yes": [zface]}),
        # the AI asked again, places by hand, and what is the admins' alone
        (f"/api/images/{zed}/describe-again", {}), ("/api/selection/describe-again", sel),
        (f"/api/images/{zed}/place", {"lat": 1, "lon": 1}), (f"/api/images/{zed}/place", {"clear": True}),
        (f"/api/folders/{picnic}/place", {"lat": 1, "lon": 1}), (f"/api/images/{zed}/write-file", None),
        (f"/api/folders/{picnic}/meaning", {"place": "x"}), (f"/api/folders/{picnic}/meaning/again", None),
        ("/api/tags/rename", {"tag": f"{fx.SECRET}tag", "to": "x"}),
        ("/api/tags/remove-everywhere", {"tag": f"{fx.SECRET}tag"}),
        ("/api/camera-clock", {"camera": "x", "first_id": zed, "image_id": zed, "day": "2010-01-01"}),
        ("/api/camera-clock/1/pin", {"image_id": zed, "day": "2010-01-01"}),
        ("/api/camera-clock/1/unpin", {"image_id": zed}), ("/api/camera-clock/1/bounds", {"to_end": True}),
        ("/api/camera-clock/1/remove", None),
        (f"/api/images/{zed}/read-date", None), (f"/api/images/{zed}/date-ok", None),
        (f"/api/faces/{hank_face}/age-ok", None), (f"/api/images/{ph['cora_bart.jpg']}/faces-found", None),
        (f"/api/devices/{site['device']}/revoke", None), (f"/api/devices/{site['device']}/delete", None),
        ("/api/devices", {"name": "Mine", "view": "memories"}),
        # undoing an admin's change, just made
        (f"/api/changes/{sams_change}/undo-own", None), (f"/api/changes/{sams_change}/undo", None),
    ]
    c.get("/home")  # her session's first use is written now (accounts.TOUCH_EVERY), not during the attempts
    before = _snapshot(con)
    for url, body in attempts:
        r = c.post(url, json=body, headers=APP)
        allowed = url == "/api/family/names/confirm"  # hers to confirm: only her people's names are touched
        assert allowed or r.status_code in (403, 404, 409), (url, r.status_code, r.text[:200])
    after = _snapshot(con)
    changed = {t for t in before if before[t] != after[t]}
    # confirming names touched only her people; nothing else moved
    assert changed <= {"person"}, changed
    if "person" in changed:
        diff = set(after["person"]) - set(before["person"])
        assert {row[0] for row in diff} <= set(access.compute(con, site["who"]["Cora"]).people)


def test_a_members_changes_stay_in_her_part(site, cfg, con):
    c = signed_in(cfg, con, site["who"]["Cora"])
    p, ph = site["people"], site["photos"]
    zed = p["Zed"]
    # a name she can't see is someone new, added by her, whom she sees from then on
    r = c.post(f"/api/faces/{site['open_face']}/name", json={"name": "Zed"}, headers=APP)
    new = r.json()["person_id"]
    assert new != zed and catalog.person(con, new)["added_by"] == site["who"]["Cora"]["id"]
    assert catalog.person(con, zed)["name"] == "Zed"
    assert c.get(f"/person/{new}").status_code == 200
    # renaming someone to a hidden person's name never merges them
    assert c.post(f"/api/people/{p['Bart']}/rename", json={"name": "Zoe"}, headers=APP).json()["person_id"] == p["Bart"]
    assert catalog.person(con, p["Zoe"]) is not None
    # naming a group of faces names only those in her photos
    con.execute("UPDATE face SET cluster_id = 77, person_id = NULL, assigned_by = NULL WHERE id = ?",
                (site["open_face"],))
    con.commit()
    c.post(f"/api/clusters/77/name?face={site['open_face']}", json={"name": "Zoe", "name_id": p["Bart"]}, headers=APP)
    assert con.execute("SELECT person_id FROM face WHERE id = ?", (site["open_face"],)).fetchone()[0] == p["Bart"]
    assert con.execute("SELECT person_id FROM face WHERE id = ?", (site["zed_open_face"],)).fetchone()[0] is None
    # her tags reach the search index everyone uses
    assert c.post(f"/api/images/{ph['cora_bart.jpg']}/tags", json={"tag": "picnic"}, headers=APP).status_code == 200
    admin = signed_in(cfg, con, site["who"]["Sam"])
    assert f"/image/{ph['cora_bart.jpg']}" in admin.get("/search?q=picnic").text
    # a tour of her own
    r = c.post("/api/tours", json={"spec": tours.clean({"title": "Mine"}), "name": "Mine"}, headers=APP)
    tour_id = int(r.json()["go"].rsplit("/", 1)[1])
    assert tours.tour(con, tour_id)["account_id"] == site["who"]["Cora"]["id"]


def test_a_refused_change_names_nobody_she_cant_see(site, cfg, con):
    """The family's checks look at everyone, but a member is only told about people she sees."""
    p = site["people"]
    jillian = con.execute("INSERT INTO person(name) VALUES ('Jillian Quux')").lastrowid
    con.execute("INSERT INTO partner(a, b, current, married, uid) VALUES (?, ?, 1, 1, 'jj')",
                (min(p["Jake"], jillian), max(p["Jake"], jillian)))
    for name in ("Bo Quux", "Bee Quux"):  # Bea's parents, whom Cora doesn't see
        parent = con.execute("INSERT INTO person(name) VALUES (?)", (name,)).lastrowid
        con.execute("INSERT INTO parent(child_id, parent_id) VALUES (?, ?)", (p["Bea"], parent))
    con.commit()
    cora, sam = signed_in(cfg, con, site["who"]["Cora"]), signed_in(cfg, con, site["who"]["Sam"])
    wife = {"name": "Brand New", "word": "wife"}
    r = cora.post(f"/api/people/{p['Jake']}/link", json=wife, headers=APP)
    assert r.status_code == 400 and "Jake already has a current partner" in r.text and "Quux" not in r.text
    r = cora.post(f"/api/people/{p['Bea']}/link", json={"name": "Brand New", "word": "mother"}, headers=APP)
    assert r.status_code == 400 and "An admin can change that" in r.text and "Quux" not in r.text
    assert "current partner is Jillian Quux" in sam.post(f"/api/people/{p['Jake']}/link", json=wife, headers=APP).text
    assert con.execute("SELECT count(*) FROM person WHERE name = 'Brand New'").fetchone()[0] == 0  # nobody left over


def test_confirm_faces_offers_a_member_only_her_people(site, cfg, con):
    p, ph = site["people"], site["photos"]
    hank = con.execute("SELECT embedding FROM face WHERE id = ?", (site["faces"]["hank_sam.jpg"][0],)).fetchone()[0]
    con.execute("UPDATE face SET embedding = ? WHERE id = ?", (hank, site["open_face"]))  # beside Cora, looks like Hank
    con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding)
                   VALUES (?, 0.7, 0.1, 0.9, 0.5, 0.9, 25, ?)""", (ph["hank_sam.jpg"], fx.unit(903).tobytes()))
    con.commit()
    cora, sam = signed_in(cfg, con, site["who"]["Cora"]), signed_in(cfg, con, site["who"]["Sam"])
    hanks = (f"/person/{p['Hank']}", f"/api/people/{p['Hank']}/faces")
    together = cora.get("/cleanup/faces?show=together")
    assert together.status_code == 200 and not any(h in together.text for h in hanks)
    finish = cora.get("/cleanup/faces?show=finish")  # Hank and Sam are named in a photo she sees: Hank without a link
    assert finish.status_code == 200 and "Hank" in finish.text and not any(h in finish.text for h in hanks)
    assert hanks[1] in sam.get("/cleanup/faces?show=together").text  # an admin is offered him


def test_who_is_this(site, cfg, con):
    cora, tina = signed_in(cfg, con, site["who"]["Cora"]), signed_in(cfg, con, site["who"]["Tina"])
    face = site["open_face"]
    assert cora.post(f"/api/faces/{face}/ask", headers=APP).status_code == 200
    assert f"/face/{face}" in tina.get("/home").text  # Tina sees that photo: she is asked too
    assert tina.post(f"/api/faces/{face}/skip", headers=APP).status_code == 200
    assert f"/face/{face}" not in tina.get("/home").text and f"/face/{face}" in cora.get("/home").text
    cora.post(f"/api/faces/{face}/name", json={"name": "Bart"}, headers=APP)
    assert f"/face/{face}" not in cora.get("/home").text  # named: answered for everyone


def test_see_as(site, cfg, con):
    admin = signed_in(cfg, con, site["who"]["Sam"])
    zed = site["photos"]["zed.jpg"]
    r = admin.post("/api/see-as", json={"account_id": site["who"]["Cora"]["id"]}, headers=APP)
    assert r.json()["go"] == "/"
    admin.cookies.set(webapp.SEE_AS, r.cookies[webapp.SEE_AS])
    assert admin.get(f"/image/{zed}").status_code == 404 and admin.get("/settings").status_code == 404
    assert "seeing the site as cora@example.com" in admin.get("/home").text
    r = admin.post(f"/api/images/{site['photos']['cora_bart.jpg']}/tags", json={"tag": "x"}, headers=APP)
    assert r.status_code == 403  # nothing changes while seeing as someone
    assert admin.post("/api/see-as/stop", headers=APP).status_code == 200
    admin.cookies.delete(webapp.SEE_AS)
    over_https = TestClient(webapp.create_app(cfg), client=LAN, base_url="https://testserver", follow_redirects=False)
    over_https.cookies.set(accounts.COOKIE, accounts.new_session(con, site["who"]["Sam"]["id"], 30))
    r = over_https.post("/api/see-as", json={"account_id": site["who"]["Cora"]["id"]}, headers=APP)
    assert "Secure" in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"]
    assert admin.get(f"/image/{zed}").status_code == 200
    # a member's own See-as cookie does nothing
    cora = signed_in(cfg, con, site["who"]["Cora"])
    cora.cookies.set(webapp.SEE_AS, str(site["who"]["Tina"]["id"]))
    assert cora.get(f"/image/{site['photos']['hank.jpg']}").status_code == 404


def test_the_crawler(site, cfg, con):
    """From Home, every link, picture and part loaded later that Cora's pages offer, as far as they go: none breaks,
    none leads outside her part of the family."""
    hidden = _hidden(site, con, "Cora")
    hank_sam = site["photos"]["hank_sam.jpg"]
    c = signed_in(cfg, con, site["who"]["Cora"])
    seen, todo = set(), ["/"]
    while todo and len(seen) < 600:
        url = todo.pop()
        if url in seen or url.startswith(("/static/", "/auth/", "/download")):
            continue
        seen.add(url)
        r = c.get(url)
        assert r.status_code < 500, (url, r.status_code)
        if r.status_code in (301, 303) and r.headers["location"].startswith("/"):
            todo.append(r.headers["location"])
            continue
        assert r.status_code in (200, 404), (url, r.status_code)  # a link to what isn't hers would be a leak too
        if r.status_code == 404 or "text/html" not in r.headers.get("content-type", ""):
            continue
        text = r.text
        assert fx.SECRET not in text, url
        for sha in hidden["shas"]:
            assert sha not in text, url
        for pid in hidden["people"]:
            assert f"/person/{pid}\"" not in text and f"/person/{pid}?" not in text, (url, pid)
        for name in hidden["names"]:
            if not (name == "Hank" and url.startswith(f"/image/{hank_sam}")):
                assert not re.search(rf"\b{name}\b", text), (url, name)
        for link in re.findall(r'(?:href|src|data-later|data-live|data-double)="(/[^"#]*)"', text):
            todo.append(link.replace("&amp;", "&"))
    assert len(seen) > 40
    assert not {u for u in seen if c.get(u).status_code == 404 and not u.startswith(("/t/", "/p/", "/face/"))} - {
        "/settings", "/status"}


def _device_client(cfg, client=OUTSIDE):
    return TestClient(webapp.create_app(cfg), client=client, follow_redirects=False)


def test_a_device_link_opens_its_frame_and_nothing_else(site, cfg, con):
    """A photo frame's link (devices.py): its frame, its list and the pictures on that list, from anywhere when made
    so, without signing in; every other page, API and picture is not there for it, whatever cookie comes along."""
    cora = site["who"]["Cora"]
    _, token = devices.make(con, "Kitchen", "memories", account_id=cora["id"], home_only=False)
    c = _device_client(cfg)
    c.cookies.set(accounts.COOKIE, accounts.new_session(con, site["who"]["Sam"]["id"], 30))  # an admin's, even
    d = f"/d/{token}"
    page = c.get(d)
    assert page.status_code == 200 and f'data-photos="{d}/api/photo-frame"' in page.text
    assert "frame-ancestors *" in page.headers["content-security-policy"] and "x-frame-options" not in page.headers
    photos = c.get(f"{d}/api/photo-frame").json()["photos"]
    shown = {p["poster"].rsplit("/", 1)[1] for p in photos}
    seen_by_cora = access.compute(con, cora)
    allowed = {catalog.image(con, i)["sha256"] for i in seen_by_cora.images}
    assert shown and shown <= allowed  # memories as Cora sees them, nothing of anyone else's
    for p in photos:
        assert c.get(d + p["src"]).status_code == 200
    for sha in [r[0] for r in con.execute("SELECT sha256 FROM image")]:
        if sha not in shown:
            assert c.get(f"{d}/p/{sha}").status_code == 404 and c.get(f"{d}/t/{sha}").status_code == 404
    for image_id in [r[0] for r in con.execute("SELECT id FROM image")]:
        assert c.get(f"{d}/play/{image_id}").status_code == 404  # no video on it
    # every other GET, with every id there is: not there
    refused = 0
    for url in _urls(c.app, con, site):
        r = c.get(d + url)
        path = url.split("?")[0]
        if path in ("/", "/photo-frame", "/api/photo-frame") or re.fullmatch(r"/p/[0-9a-f]{64}", path):  # /: the frame
            continue
        assert r.status_code == 404, (url, r.status_code)
        assert fx.SECRET not in r.text, url
        refused += 1
    assert refused > 200
    # and every change, of every kind
    for route in webapp.all_routes(c.app):
        if "POST" not in getattr(route, "methods", ()):
            continue
        url = re.sub(r"{\w+(?::\w+)?}", "1", route.path)
        r = c.post(d + url, json={}, headers=APP)
        assert r.status_code == 404, (url, r.status_code, r.text[:200])
    # an admin's session in the same browser still works without the link
    assert c.get("/settings").status_code == 200


def test_device_links_turned_off_or_away_from_home(site, cfg, con):
    """A revoked link, a made-up one and one for the home network asked for from outside: the frame and its pictures
    are all refused, at once after revoking (the lookup isn't kept through a change on the site)."""
    sam = site["who"]["Sam"]
    link_id, token = devices.make(con, "Hall", "tour", tour_id=site["tours"]["admins"], home_only=True)
    at_home = _device_client(cfg, LAN)
    outside = TestClient(at_home.app, client=OUTSIDE, follow_redirects=False)  # one app: one web server's memory
    d = f"/d/{token}"
    assert at_home.get(d).status_code == 200
    photos = at_home.get(f"{d}/api/photo-frame").json()["photos"]
    assert photos and at_home.get(d + photos[0]["src"]).status_code == 200
    r = outside.get(d)
    assert r.status_code == 404 and "home network" in r.text
    assert outside.get(d + photos[0]["src"]).status_code == 404
    assert at_home.get("/d/made-up-token").status_code == 404
    assert at_home.get("/d/").status_code == 404
    # turned off by an admin on Settings → Photo frames
    admin = TestClient(at_home.app, client=LAN, follow_redirects=False)
    admin.cookies.set(accounts.COOKIE, accounts.new_session(con, sam["id"], 30))
    assert admin.post(f"/api/devices/{link_id}/revoke", headers=APP).status_code == 200
    for url in (d, f"{d}/api/photo-frame", d + photos[0]["src"]):
        r = at_home.get(url)
        assert r.status_code == 404 and "turned off" in r.text, url
    assert con.execute("SELECT token_hash FROM device_link WHERE id = ?", (link_id,)).fetchone()[0] != token


def test_members_cant_make_device_links(site, cfg, con):
    cora = signed_in(cfg, con, site["who"]["Cora"])
    before = con.execute("SELECT count(*) FROM device_link").fetchone()[0]
    r = cora.post("/api/devices", json={"name": "Mine", "view": "memories", "account_id": "all"}, headers=APP)
    assert r.status_code == 404 and con.execute("SELECT count(*) FROM device_link").fetchone()[0] == before
    assert cora.get("/settings/devices").status_code == 404


def test_new_for_you_shows_a_member_only_what_she_sees(site, cfg, con):
    """Events about photos she can't see (a question about Zed's face, words on his photo) are not hers to see, nor
    the admins' alerts; the new photos she sees are counted, the others not even named."""
    cora = signed_in(cfg, con, site["who"]["Cora"])
    page = cora.get("/news").text
    hidden = _hidden(site, con, "Cora")
    assert fx.SECRET not in page and "files found damaged on the photo disk" not in page and "asked who this is" not in page
    for sha in hidden["shas"]:
        assert sha not in page
    seen = access.compute(con, site["who"]["Cora"])
    visible = [i for i in site["photos"].values() if i in seen.images
               and con.execute(f"SELECT 1 FROM image i WHERE id = ? AND {catalog.VISIBLE}", (i,)).fetchone()]
    assert f"{len(visible)} new photos of your family" in page
    admin = signed_in(cfg, con, site["who"]["Sam"]).get("/news").text
    assert "3 files found damaged" in admin and f"{len(site['photos'])} new photos" in admin
    assert "Sam asked who this is" not in admin  # his own question is no news to him
