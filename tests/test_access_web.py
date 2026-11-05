"""Family accounts in the web app: every route has a rule, nobody gets in without signing in, and a member gets nothing
from outside their part of the family from any page, API or picture, nor changes anything there."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from lineagebox import access, accounts, catalog, memories, settings, sharing, tours
from lineagebox.web import app as webapp
from lineagebox.web import site as site_module
from lineagebox.web.routes import cleanup as cleanup_routes
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
    # an unnamed face beside Cora and Bart, and one in Zed's photo, grouped together by the clustering
    fam["open_face"] = con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding, cluster_id)
                                      VALUES (?, 0.7, 0.1, 0.9, 0.5, 0.9, 25, ?, 77)""",
                                   (fam["photos"]["cora_bart.jpg"], fx.unit(901).tobytes())).lastrowid
    fam["zed_open_face"] = con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding,
                                                           cluster_id) VALUES (?, 0.6, 0.1, 0.9, 0.5, 0.9, 25, ?, 77)""",
                                       (fam["photos"]["zed.jpg"], fx.unit(902).tobytes())).lastrowid
    con.execute("INSERT INTO citation(image_id, person_id) VALUES (?, ?)", (fam["photos"]["zed.jpg"], p["Zed"]))
    con.commit()
    settings.save(con, cfg, {"accounts_enabled": True})
    fam["who"] = who
    return fam


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
        "year": [r[0] for r in con.execute("SELECT DISTINCT CAST(substr(taken_at, 1, 4) AS INT) FROM image")],
        "name": [r[0] for r in con.execute("SELECT name FROM tag")] + ["nothing"],
        "sha": [r[0] for r in con.execute("SELECT sha256 FROM image")],
        "value": ["gps", "videos", "format/jpeg"], "import_id": [1], "kind": ["papers"], "type_id": ["new", "1"],
        "part": list(cleanup_routes.COUNTS),
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
    queries += [f"/family?root={i}" for i in site["people"].values()]
    queries += [f"/image/{i}?ctx=person:{p}" for i in ids["image_id"][:3] for p in site["people"].values()]
    # tours not saved yet (Home's memories): of everyone's photos, and of each person
    queries += [memories.try_href({"spec": {"title": "Everything", "media": "both"}})]
    queries += [memories.try_href({"spec": {"title": "{person}", "who": {"mode": "person", "person_ids": [i]}}})
                for i in site["people"].values()]
    queries += ["/api/map?year=2014", "/tours/new?type=1", "/family/export", "/family/export?photos=true",
                "/cleanup/faces?show=finish", "/cleanup/faces?show=groups", "/cleanup/faces?show=together",
                "/tags?q=zebra", "/settings?browse=lib"]
    return out + queries


@pytest.mark.parametrize("who", ["Cora", "Tina"])
def test_the_leak_sweep(site, cfg, con, who):
    """Every page, API and picture, with every id there is: what isn't theirs is not found, nothing breaks, and no
    answer holds anything from outside their part of the family."""
    hidden = _hidden(site, con, who)
    c = signed_in(cfg, con, site["who"][who])
    app = c.app
    hank_sam = site["photos"]["hank_sam.jpg"]
    checked = 0
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
    # and what is not theirs is "not found", like something that isn't there
    for i in hidden["images"]:
        assert c.get(f"/image/{i}").status_code == 404
        assert c.get(f"/play/{i}").status_code == 404
    for sha in hidden["shas"]:
        assert c.get(f"/t/{sha}").status_code == 404 and c.get(f"/p/{sha}").status_code == 404
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
              "face_question", "face_not_person", "file", "share")
    out = {t: con.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall() for t in tables}
    out["image"] = con.execute("SELECT id, taken_at, taken_at_source, hidden FROM image ORDER BY id").fetchall()
    return {t: [tuple(r) for r in rows] for t, rows in out.items()}


def test_the_write_sweep(site, cfg, con):
    """Every change a member can ask for, about things outside their part of the family: refused, and nothing in
    the catalog changes."""
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
    t = site["tours"]
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
