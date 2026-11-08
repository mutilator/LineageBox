"""Family features built on GEDCOM: importing a tree (with the AI's help), groups of acquaintances, people in search,
ages, documents, event tours and the world map."""
import dataclasses
import io
import json
import zipfile
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from lineagebox import documents, events, family, gedcom, gedcom_import, people, places, search, tours
from lineagebox.web import app as webapp
from tests import family_fixture as fx
from tests.conftest import LAN
from tests.test_gedcom import APP, fam  # noqa: F401 - the fixture
from tests.test_access_web import signed_in, site  # noqa: F401 - site is a fixture

FOREIGN = """0 HEAD
1 SOUR ANCESTRY
1 GEDC
2 VERS 5.5.1
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @P1@ INDI
1 NAME Daniel /Miller/
1 SEX M
0 @P2@ INDI
1 NAME Katherine /Miller/
2 TYPE married
1 NAME Katherine /Baker/
2 TYPE birth
1 SEX F
0 @P3@ INDI
1 NAME Lucas James /Miller/
2 NICK Luke
1 SEX M
1 BIRT
2 DATE 21 AUG 2014
2 PLAC Salem, Oregon, USA
1 FAMC @F1@
0 @P4@ INDI
1 NAME Owen /Miller/
1 SEX M
1 BIRT
2 DATE 2012
1 FAMC @F1@
0 @P5@ INDI
1 NAME Walter /Baker/
1 SEX M
1 BIRT
2 DATE ABT 1930
2 PLAC Salem, Oregon, USA
1 FAMC @F2@
0 @P6@ INDI
1 NAME Ruth /Baker/
1 SEX F
1 DEAT Y
0 @P7@ INDI
1 NAME Michael /Bakker/
1 SEX M
0 @F1@ FAM
1 HUSB @P1@
1 WIFE @P2@
1 CHIL @P3@
1 CHIL @P4@
1 MARR
2 DATE 22 SEP 2001
2 PLAC Salem, Oregon, USA
0 @F2@ FAM
1 WIFE @P6@
1 CHIL @P5@
0 TRLR
"""


def test_an_export_comes_back_as_the_same_people(fam, cfg):
    con, ids = fam
    data, _ = gedcom.export(con, cfg)
    import_id = gedcom_import.start(con, "back.ged", data)
    _, parsed, matches = gedcom_import.load(con, import_id)
    assert {m["how"] for m in matches.values()} == {"uid"} and len(matches) == con.execute(
        "SELECT count(*) FROM person").fetchone()[0]
    before = [con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("person", "parent", "partner", "event")]
    report = gedcom_import.apply(con, import_id)
    after = [con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("person", "parent", "partner", "event")]
    assert report["new"] == 0 and after == before  # nothing doubled
    assert gedcom_import.apply(con, import_id) == report  # applying twice changes nothing


def test_a_tree_from_elsewhere_is_merged(fam, cfg):
    con, ids = fam
    lucas = people.person_id(con, "Lucas")  # one word here, "Lucas James Miller" there
    con.execute("UPDATE person SET sex = 'male' WHERE id = ?", (lucas,))
    for p in (ids["Daniel Miller"], ids["Katherine Miller"]):
        con.execute("INSERT INTO parent(child_id, parent_id) VALUES (?, ?)", (lucas, p))
    con.commit()
    import_id = gedcom_import.start(con, "ancestry.ged", FOREIGN.encode())
    _, data, matches = gedcom_import.load(con, import_id)
    assert matches["@P1@"]["choice"] == ids["Daniel Miller"] and matches["@P1@"]["how"] == "sure"
    assert matches["@P2@"]["choice"] == ids["Katherine Miller"]
    assert matches["@P3@"]["choice"] == lucas and "parents" in matches["@P3@"]["why"]  # the parents match
    assert matches["@P6@"]["how"] == "new" and matches["@P7@"]["how"] == "unsure"   # Michael Bakker ~ Mike Baker?
    page = TestClient(webapp.create_app(cfg), client=LAN).get(f"/family/import/{import_id}").text
    assert "Michael Bakker" in page and "Ask the AI" in page
    gedcom_import.set_choices(con, import_id, {"@P7@": "new"})
    report = gedcom_import.apply(con, import_id)
    ruth = people.person_id(con, "Ruth Baker")
    assert ruth in family.load(con).parents[ids["Walter Baker"]]  # a new great-grandmother, with her link
    assert any("born" in c and "2012" in c for c in report["conflicts"])  # Owen: the date here was kept
    assert con.execute("SELECT date FROM event WHERE person_id = ? AND tag = 'BIRT'",
                       (ids["Owen Miller"],)).fetchone()[0] == "15 JUN 2011"
    a, b = sorted((ids["Daniel Miller"], ids["Katherine Miller"]))
    marr = con.execute("SELECT * FROM event WHERE couple_a = ? AND couple_b = ?", (a, b)).fetchall()
    assert [(e["tag"], e["date"], e["place"]) for e in marr] == [("MARR", "22 SEP 2001", "Salem, Oregon, USA")]
    names = {r["name"]: r["type"] for r in events.other_names(con, lucas)}
    assert names == {"Lucas James Miller": "aka", "Luke": "nickname"}
    assert {r["name"] for r in events.other_names(con, ids["Katherine Miller"])} == {"Katherine Baker"}
    born = con.execute("SELECT date, place FROM event WHERE person_id = ? AND tag = 'BIRT'", (lucas,)).fetchone()
    assert tuple(born) == ("21 AUG 2014", "Salem, Oregon, USA")
    assert con.execute("SELECT count(*) FROM person WHERE name LIKE 'Michael Bakker%'").fetchone()[0] == 1


def test_choosing_who_is_who(fam, cfg):
    """A name typed for someone in the file must be one person's: namesakes are told apart by picking one."""
    con, ids = fam
    anns = [people.person_id(con, "Ann Miller", among=set()) for _ in range(2)]  # two people, both Ann Miller
    con.commit()
    import_id = gedcom_import.start(con, "ancestry.ged", FOREIGN.encode())
    c = TestClient(webapp.create_app(cfg), client=LAN)
    url = f"/api/family/import/{import_id}/choices"
    r = c.post(url, json={"name:@P6@": "Ann Miller"}, headers=APP)
    assert r.status_code == 400 and "2 people here are called Ann Miller" in r.json()["detail"]
    assert c.post(url, json={"name:@P6@": "Nobody Here"}, headers=APP).status_code == 400
    assert c.post(url, json={"name:@P6@": "Ann Miller", "name:@P6@_id": anns[1]}, headers=APP).status_code == 200
    assert gedcom_import.load(con, import_id)[2]["@P6@"]["choice"] == anns[1]
    people.merge(con, anns[1], ids["Edna Baker"])  # merged away while the import waits
    con.commit()
    r = c.post(f"/api/family/import/{import_id}/apply", headers=APP)
    assert r.status_code == 400 and "Ruth Baker" in r.json()["detail"] and "merged or removed" in r.json()["detail"]


def test_a_damaged_or_huge_zip_is_refused(fam, cfg, monkeypatch):
    c = TestClient(webapp.create_app(cfg), client=LAN)
    r = c.post("/api/family/import?name=tree.gdz", content=b"PK\x03\x04 cut short", headers=APP)
    assert r.status_code == 400 and "damaged" in r.json()["detail"]
    monkeypatch.setattr(gedcom_import, "MOST", 10_000)
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED) as z:  # a small zip that unpacks past the limit
        z.writestr("gedcom.ged", FOREIGN + "0 @N1@ NOTE " + "x" * 50_000 + "\n")
    r = c.post("/api/family/import?name=tree.gdz", content=packed.getvalue(), headers=APP)
    assert r.status_code == 400 and "over" in r.json()["detail"]
    r = c.post("/api/family/import?name=tree.ged", content=b"0 HEAD\n" + b"x" * 20_000, headers=APP)
    assert r.status_code == 413


def test_the_ai_settles_unsure_matches(fam, cfg, monkeypatch):
    con, ids = fam
    asked = []

    class FakeOllama:
        def __init__(self, cfg):
            pass

        def chat_json(self, system, user, schema, temperature=0.1, max_tokens=1024):
            asked.append(user)
            return {"same": True, "confidence": 0.85, "reason": "Michael is Mike; same family name, misspelled"}, None

    monkeypatch.setattr("lineagebox.llm.Ollama", FakeOllama)
    import_id = gedcom_import.start(con, "ancestry.ged", FOREIGN.encode())
    c = TestClient(webapp.create_app(cfg), client=LAN)
    assert "looked at 1 unsure" in c.post(f"/api/family/import/{import_id}/ai", headers=APP).json()["message"]
    _, _, matches = gedcom_import.load(con, import_id)
    m = matches["@P7@"]
    assert (m["choice"], m["how"]) == (ids["Mike Baker"], "ai") and "misspelled" in m["ai"]["reason"]
    assert "Michael Bakker" in asked[0] and "Mike Baker" in asked[0]


def test_groups_of_acquaintances(fam, cfg):
    con, ids = fam
    assert people.add_to_group(con, ids["Jake"], "Teachers") == "Teachers"
    assert people.add_to_group(con, ids["Kevin Whitfield"], "teachers") == "Teachers"  # one group, written once
    with pytest.raises(people.Problem):
        people.add_to_group(con, ids["Jake"], " ")
    con.commit()
    assert people.groups(con) == {"Teachers": [ids["Jake"], ids["Kevin Whitfield"]]}
    assert set(search.people_matching(con, "teachers")) == {ids["Kevin Whitfield"], ids["Jake"]}
    assert set(search.people_matching(con, "teacher")) == {ids["Kevin Whitfield"], ids["Jake"]}
    c = TestClient(webapp.create_app(cfg), client=LAN)
    assert c.post(f"/api/people/{ids['Rex']}/groups", json={"name": "Foo Fighters"}, headers=APP).status_code == 200
    assert "Acquaintances, by group" in c.get("/family").text and "Foo Fighters" in c.get(f"/person/{ids['Rex']}").text
    recs = gedcom.parse(gedcom.export(con, cfg)[0])
    jake = next(r for r in recs if r.tag == "INDI" and r.value("NAME") == "Jake")
    assert jake.value("_GROUP") == "Teachers" and "_GROUP" in recs[0].first("SCHMA").all("TAG")[1].payload


def test_people_in_search_and_other_names(fam, cfg):
    con, ids = fam
    events.add_name(con, ids["Katherine Miller"], "Katherine Baker", "birth")
    events.add_name(con, ids["Katherine Miller"], "Kate", "nickname")
    con.commit()
    assert search.people_matching(con, "Katherine Baker") == [ids["Katherine Miller"]]
    assert search.people_matching(con, "kate") == [ids["Katherine Miller"]]
    assert ids["Katherine Miller"] in family.word_index(con)["baker"]
    page = TestClient(webapp.create_app(cfg), client=LAN).get("/search?q=miller").text
    assert "<h2>People" in page and "Daniel Miller" in page and "Katherine Miller" in page


def test_ages(fam, cfg):
    assert events.age("2011-06-15", date(2022, 6, 14)) == "10" and events.age("2011-06-15", date(2022, 6, 15)) == "11"
    assert events.age("1950", date(1980, 6, 1)) == "about 30" and events.age("1950-06", date(1980, 7, 1)) == "30"
    assert events.age("2010", date(2005, 1, 1)) is None
    con, ids = fam
    events.save(con, None, tag="BIRT", person_id=ids["Daniel Miller"], date_text="1974-09-17")
    con.execute("UPDATE image SET taken_at = '2010-06-01T10:00:00', taken_at_source = 'exif'")
    con.commit()
    image = con.execute("SELECT id FROM image").fetchone()[0]
    page = TestClient(webapp.create_app(cfg), client=LAN).get(f"/image/{image}").text
    assert '<span class="age">35</span>' in page
    from lineagebox.people import _before_birth
    face = {"taken_at": "2009-05-01T10:00:00", "taken_at_source": "exif"}
    assert _before_birth(face, "2011-06-15") and not _before_birth(face, "2009") and not _before_birth(face, "2001-01-01")
    assert not _before_birth({**face, "taken_at_source": "mtime"}, "2011-06-15")  # a file time says nothing


def test_documents_are_sources(fam, cfg):
    con, ids = fam
    image = con.execute("SELECT id FROM image").fetchone()[0]
    c = TestClient(webapp.create_app(cfg), client=LAN)
    assert c.post(f"/api/images/{image}/document", json={"kind": "Birth certificate", "title": "Owen's birth certificate"},
                  headers=APP).status_code == 200
    birth = con.execute("SELECT id FROM event WHERE person_id = ? AND tag = 'BIRT'", (ids["Owen Miller"],)).fetchone()[0]
    assert c.post(f"/api/images/{image}/citations", json={"target": f"e:{birth}"}, headers=APP).status_code == 200
    assert c.post(f"/api/images/{image}/citations", json={"target": f"p:{ids['Daniel Miller']}"}, headers=APP).status_code == 200
    assert con.execute("SELECT kept_at FROM image WHERE id = ?", (image,)).fetchone()[0]  # out of cleanup's lists
    assert [d["title"] for d in documents.for_person(con, ids["Owen Miller"])] == ["Owen's birth certificate"]
    assert "Owen Miller: Born June 15, 2011" in c.get(f"/image/{image}").text
    assert "Documents" in c.get(f"/person/{ids['Owen Miller']}").text
    recs = gedcom.parse(gedcom.export(con, cfg)[0])
    by = {r.xref: r for r in recs if r.xref}
    owen = next(r for r in recs if r.tag == "INDI" and r.value("NAME") == "Owen /Miller/")
    source = owen.first("BIRT").value("SOUR")
    assert by[source].tag == "SOUR" and by[source].value("TITL") == "Owen's birth certificate"
    daniel = next(r for r in recs if r.tag == "INDI" and r.value("NAME") == "Daniel /Miller/")
    assert daniel.value("SOUR") == source


def test_tours_around_life_events(fam):
    con, ids = fam
    daniel, katherine = ids["Daniel Miller"], ids["Katherine Miller"]
    events.save(con, None, tag="MARR", couple=(daniel, katherine), date_text="2001-09-22")
    events.save(con, None, tag="GRAD", person_id=daniel, date_text="May 1993")
    found = {n: {"taken": t} for n, t in enumerate(["2005-09-15T12:00:00", "2005-12-01T12:00:00", "1993-05-20T12:00:00",
                                                    "1993-07-20T12:00:00", "2001-09-23T12:00:00"])}
    spec = tours.clean({"who": {"mode": "person", "person_ids": [daniel]}, "when": {"mode": "anniversary", "days": 14}})
    assert sorted(tours._around_events(con, found, spec)) == [0, 4]
    spec["when"]["mode"], spec["when"]["days"] = "events", 7
    assert sorted(tours._around_events(con, found, spec)) == [2, 4]  # graduation month, wedding day
    with pytest.raises(tours.Problem):
        tours.clean({"who": {"mode": "anyone"}, "when": {"mode": "anniversary"}})
    line = tours.target_people(con, tours.clean({"who": {"mode": "line", "person_ids": [ids["Owen Miller"]]}}))
    assert {ids["Owen Miller"], daniel, katherine, ids["Gary Miller"], ids["Walter Baker"]} <= line
    assert {t["name"] for t in tours.types(con)} >= {"Anniversaries", "Life events", "Family line"}


def test_world_map(fam, cfg, monkeypatch):
    con, ids = fam
    folder = con.execute("INSERT INTO folder(path, name, place) VALUES ('lib', 'lib', 'Grand Canyon, Arizona')").lastrowid
    image = con.execute("SELECT id FROM image").fetchone()[0]
    con.execute("INSERT INTO file(path, folder_id, image_id, size, mtime, last_seen) VALUES ('lib/a.jpg', ?, ?, 1, 1, '')",
                (folder, image))
    con.execute("UPDATE image SET taken_at = '2012-06-03T10:00:00', taken_at_source = 'exif'")
    events.save(con, None, tag="BIRT", person_id=ids["Daniel Miller"], date_text="1974", place="Salem, Oregon, USA")
    con.commit()
    assert places.names_to_find(con) == ["Grand Canyon, Arizona", "Salem, Oregon, USA"]
    spots = {"Grand Canyon, Arizona": (36.06, -112.14, "Grand Canyon"), "Salem, Oregon, USA": (44.94, -123.03, "Salem")}
    monkeypatch.setattr(places, "look_up", lambda name, *args, **kw: spots.get(name))
    monkeypatch.setattr(places.time, "sleep", lambda s: None)
    assert places.find_new(con, cfg) == 2 and places.names_to_find(con) == []
    d = places.map_data(con)
    assert d["years"] == [{"year": 2012, "dated": 1, "undated": 0}]
    [p] = d["places"]
    assert (p["name"], p["count"], p["folder"], p["kind"]) == ("Grand Canyon, Arizona", 1, folder, "folder")
    [e] = d["events"]
    assert (e["who"], e["label"], e["date"]) == ("Daniel Miller", "born", "1974")
    assert places.map_data(con, 2011)["places"] == [] and places.map_data(con, 1974)["events"]
    c = TestClient(webapp.create_app(cfg), client=LAN)
    assert "leaflet" in c.get("/map").text and c.get("/api/map?year=2012").json()["places"][0]["count"] == 1
    # the pages send no Referer to other sites, but OpenStreetMap's tiles need one (osm.wiki/Blocked)
    assert c.get("/map").headers["referrer-policy"] == "same-origin"
    assert 'referrerPolicy: "strict-origin-when-cross-origin"' in c.get("/static/map.js").text
    # "Right place": the folder's place shows as confirmed, until it is taken back or looked up as somewhere else
    assert c.post("/api/places/confirm", json={"name": "Grand Canyon, Arizona"}, headers=APP).status_code == 200
    assert c.get("/api/map").json()["places"][0]["kind"] == "confirmed"
    c.post("/api/places/confirm", json={"name": "Grand Canyon, Arizona", "right": False}, headers=APP)
    assert c.get("/api/map").json()["places"][0]["kind"] == "folder"
    places.confirm(con, "Grand Canyon, Arizona")
    monkeypatch.setattr(places, "look_up", lambda q, *args, **kw: (36.06, -112.14, "Grand Canyon Village"))
    places.fix(con, cfg, "Grand Canyon, Arizona", "Grand Canyon Village")
    con.commit()
    assert places.map_data(con)["places"][0]["kind"] == "folder"  # it moved: to be checked again
    assert c.post("/api/places/confirm", json={"name": "Nowhere"}, headers=APP).status_code == 400
    assert c.post("/api/places/fix", json={"name": "Grand Canyon, Arizona"}, headers=APP).json() == {"on_map": False}
    assert places.map_data(con)["places"] == [] and places.map_data(con)["unplaced"] == 1


def test_the_family_around_one_person(con, cfg):
    """Four generations and more: the view shows two up and one down from whoever is in the middle, says how far the
    family goes on, and the way back to "me"."""
    ids = {n: people.person_id(con, n) for n in ("Ana", "Dad", "Mum", "Grandpa", "Grandma", "Great", "Sis", "Niece",
                                                  "Kid", "Grandkid", "Partner")}
    for who, word, other in (("Ana", "father", "Dad"), ("Ana", "mother", "Mum"), ("Dad", "father", "Grandpa"),
                             ("Dad", "mother", "Grandma"), ("Grandpa", "father", "Great"), ("Ana", "sister", "Sis"),
                             ("Sis", "daughter", "Niece"), ("Ana", "son", "Kid"), ("Kid", "son", "Grandkid"),
                             ("Ana", "wife", "Partner")):
        family.link(con, ids[who], word, ids[other])
    con.execute("UPDATE person SET birth_date = '1990-01-01' WHERE id = ?", (ids["Ana"],))
    con.execute("UPDATE person SET birth_date = '1985-01-01' WHERE id = ?", (ids["Sis"],))
    con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('me', ?)", (str(ids["Ana"]),))
    con.commit()
    g = family.load(con)
    v = family.view(g, ids["Ana"], ids["Ana"])
    assert v["grandparents"] == [(ids["Dad"], sorted([ids["Grandpa"], ids["Grandma"]], key=lambda p: g.name(p))),
                                 (ids["Mum"], [])]
    assert set(v["parents"]) == {ids["Dad"], ids["Mum"]} and v["older"] == [ids["Sis"]] and v["younger"] == []
    assert v["partners"] == [(ids["Partner"], True)]
    assert v["children"] == [(ids["Ana"], [ids["Kid"]]), (ids["Sis"], [ids["Niece"]])]
    assert (v["above"], v["below"]) == (1, 1)  # Great above the grandparents, Grandkid below the children
    assert v["up_from"] == {ids["Grandpa"]} and v["down_from"] == {ids["Kid"]} and v["path"] == [ids["Ana"]]
    up = family.view(g, ids["Grandpa"], ids["Ana"])  # climbed two generations: the way back is through Dad
    assert up["path"] == [ids["Ana"], ids["Dad"], ids["Grandpa"]] and up["parents"] == [ids["Great"]]
    assert up["children"] == [(ids["Grandpa"], [ids["Dad"]])] and up["below"] == 3

    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get("/family").text
    assert 'id="view"' in page and f'href="/family?center={ids["Grandpa"]}#view"' in page
    assert f'href="/person/{ids["Ana"]}"' in page  # the one in the middle opens their page
    assert f'data-double="/person/{ids["Grandpa"]}"' in page  # anyone else: a double click opens their page
    # the whole line down from one ancestor is a page of its own, from the top of the family of whoever is in the
    # middle, showing them
    assert f'href="/family/tree?root={ids["Great"]}&amp;person={ids["Ana"]}"' in page and 'class="tree"' not in page
    climbed = client.get(f"/family?center={ids['Grandpa']}").text
    assert 'class="hint-path"' in climbed and ">You</a>" in climbed and "1 more generation" not in climbed


def test_the_family_page_folds_away_all_but_the_family_around_you(con, cfg):
    """Reading into the tree at the top (admins), the family around you open, the groups below folded, each
    relationship a group of its own to glance at, and GEDCOM's Export and Import at the bottom."""
    ids = {n: people.person_id(con, n) for n in ("Ana", "Dad", "Mum", "Sis", "Cousin", "Aunt", "Rex", "Coach")}
    for who, word, other in (("Ana", "father", "Dad"), ("Ana", "mother", "Mum"), ("Ana", "sister", "Sis"),
                             ("Mum", "sister", "Aunt"), ("Aunt", "son", "Cousin"), ("Ana", "pet", "Rex")):
        family.link(con, ids[who], word, ids[other])
    people.add_to_group(con, ids["Coach"], "Teachers")
    con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('me', ?)", (str(ids["Ana"]),))
    con.commit()
    page = TestClient(webapp.create_app(cfg), client=LAN).get("/family").text
    main = page.split("<main", 1)[1]
    order = [main.index(s) for s in ('<h1>Family</h1>', 'id="read"', 'id="view"', "Everyone, by relationship",
                                     "Friends, pets and others", "Acquaintances, by group", 'id="gedcom"')]
    assert order == sorted(order)
    reading = main.split('id="read"', 1)[1].split("</details>", 1)[0]
    assert "Use local AI to read family events from text documents and pictures" in reading
    assert 'data-api="/api/family/read"' in reading and "presented for you to approve" in reading
    # every section below the family around you is folded until opened, and so is each group in it
    folded = main.split("Everyone, by relationship", 1)[0].rsplit("<details", 1)[1]
    assert " open" not in folded.split(">", 1)[0]
    assert main.count("<details") == main.count("</details>") and "<details open" not in main
    assert '<summary class="group-head">Cousins <small>1</small></summary>' in main
    assert main.count("data-glance") >= 4  # parents, sisters, aunts, cousins (and the pet, and the teachers)
    gedcom = main.split('id="gedcom"', 1)[1]
    assert "<h3>Export</h3>" in gedcom and "<h3>Import</h3>" in gedcom and "Read an obituary" not in gedcom


def test_the_whole_line_is_a_page_to_move_around_and_save(con, cfg):
    ids = {n: people.person_id(con, n) for n in ("Ana", "Dad", "Grandpa", "Kid")}
    for who, word, other in (("Ana", "father", "Dad"), ("Dad", "father", "Grandpa"), ("Ana", "son", "Kid")):
        family.link(con, ids[who], word, ids[other])
    con.commit()
    client = TestClient(webapp.create_app(cfg), client=LAN, follow_redirects=False)
    page = client.get(f"/family/tree?root={ids['Grandpa']}&person={ids['Kid']}").text
    assert "<title>The whole line down from Grandpa" in page and "data-pan-zoom" in page
    assert f'data-person="{ids["Kid"]}"' in page and "data-tree-picture" in page and 'data-zoom="fit"' in page
    assert f'class="fcard direct" href="/person/{ids["Kid"]}"' in page
    # someone not in the catalog is left out; with no root, the line goes from the top of whoever is asked for
    assert "data-person" not in client.get(f"/family/tree?root={ids['Grandpa']}&person=999999").text
    assert f'<option value="{ids["Grandpa"]}" selected>' in client.get(f"/family/tree?person={ids['Kid']}").text
    # the old address of the whole line, and a person's page's link to it
    old = client.get(f"/family?root={ids['Grandpa']}")
    assert old.status_code == 303 and old.headers["location"] == f"/family/tree?root={ids['Grandpa']}"
    person = client.get(f"/person/{ids['Kid']}").text
    assert f'href="/family/tree?root={ids["Grandpa"]}&amp;person={ids["Kid"]}"' in person


def test_only_admins_read_into_the_tree_from_the_family_page(site, cfg, con):
    sam = signed_in(cfg, con, site["who"]["Sam"]).get("/family").text
    cora = signed_in(cfg, con, site["who"]["Cora"]).get("/family").text
    assert 'id="read"' in sam and 'id="read"' not in cora and "/api/family/read" not in cora
    assert "<h3>Export</h3>" in cora and "<h3>Import</h3>" not in cora


def test_removing_someone_added_by_mistake(site, cfg, con):
    p = site["people"]
    sam = signed_in(cfg, con, site["who"]["Sam"])
    wrong = people.person_id(con, "Wrong Person")
    family.link(con, wrong, "mother", p["Old Nell"])
    events.save(con, None, tag="BIRT", person_id=wrong, date_text="1950")
    tour = tours.save_tour(con, None, None, tours.clean({"title": "Two", "who": {"mode": "people",
                                                                                "person_ids": [wrong, p["Cora"]]}}), "Two")
    con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('me', ?)", (str(wrong),))
    con.commit()
    page = sam.get(f"/person/{wrong}").text
    assert f'data-api="/api/people/{wrong}/remove"' in page and "Remove Wrong Person? Their family links" in page
    cora = sam.get(f"/person/{p['Cora']}").text.split('class="danger-zone"')[1]
    assert "/remove" not in cora and "Cora has an account (cora@example.com)" in cora  # she stays, and why
    r = sam.post(f"/api/people/{wrong}/remove", headers=APP)
    assert r.status_code == 200 and r.json()["message"] == "Wrong Person was removed."
    for sql in ("SELECT 1 FROM person WHERE id = ?", "SELECT 1 FROM parent WHERE child_id = ?",
                "SELECT 1 FROM event WHERE person_id = ?"):
        assert not con.execute(sql, (wrong,)).fetchone(), sql
    spec = con.execute("SELECT spec FROM tour WHERE id = ?", (tour,)).fetchone()[0]
    assert json.loads(spec)["who"]["person_ids"] == [p["Cora"]]
    assert not con.execute("SELECT 1 FROM settings WHERE key = 'me'").fetchone()
    assert sam.post(f"/api/people/{wrong}/remove", headers=APP).status_code == 404
    # someone with an account stays
    signs_in = people.person_id(con, "Signs In")
    fx.account(con, signs_in, "signs.in@example.com")
    r = sam.post(f"/api/people/{signs_in}/remove", headers=APP)
    assert r.status_code == 400 and "has an account (signs.in@example.com)" in r.json()["detail"]
    # members can't: for them there is no such thing
    assert signed_in(cfg, con, site["who"]["Cora"]).post(f"/api/people/{p['Jim']}/remove",
                                                         headers=APP).status_code == 404
    assert con.execute("SELECT 1 FROM person WHERE id = ?", (p["Jim"],)).fetchone()


def test_removing_someone_named_in_photos_unnames_their_faces(site, cfg, con):
    """Their name comes off every face that had it, which is unnamed again (to be grouped, recognised or named), and
    off the photos they were added to by hand; nothing else's faces change, and search no longer finds them there."""
    p, ph, fc = site["people"], site["photos"], site["faces"]
    sam = signed_in(cfg, con, site["who"]["Sam"])
    hank_faces = fc["hank.jpg"] + fc["hank_sam.jpg"][:1]
    sams_face = fc["hank_sam.jpg"][1]
    con.execute("UPDATE face SET named_by = ?, cluster_id = 5, assign_score = 0.9 WHERE id = ?",
                (site["who"]["Sam"]["id"], hank_faces[0]))
    con.execute("INSERT INTO face_not_person(face_id, person_id) VALUES (?, ?)", (sams_face, p["Hank"]))
    con.execute("INSERT INTO image_person(image_id, person_id, added_at) VALUES (?, ?, '2026-01-01')",
                (ph["scenery.jpg"], p["Hank"]))
    con.commit()
    search.rebuild(con)
    assert {ph["hank.jpg"], ph["scenery.jpg"]} <= {r["id"] for r in search.query(con, "Hank")}
    page = sam.get(f"/person/{p['Hank']}").text
    assert (f"Remove Hank? Their name comes off 3 photos: the 2 faces named Hank will be unnamed again. Their family "
            f"links") in page
    r = sam.post(f"/api/people/{p['Hank']}/remove", headers=APP)
    assert r.status_code == 200 and r.json()["message"] == "Hank was removed. 2 faces are unnamed again."
    assert not con.execute("SELECT 1 FROM person WHERE id = ?", (p["Hank"],)).fetchone()
    rows = con.execute("""SELECT person_id, assigned_by, assign_score, cluster_id, named_by FROM face
                          WHERE id IN (SELECT value FROM json_each(?))""", (json.dumps(hank_faces),)).fetchall()
    assert [tuple(r) for r in rows] == [(None,) * 5] * 2  # as if never named: free to be recognised or grouped
    sams = con.execute("SELECT person_id, assigned_by FROM face WHERE id = ?", (sams_face,)).fetchone()
    assert tuple(sams) == (p["Sam"], "user")  # the face beside theirs keeps its name
    for sql in ("SELECT 1 FROM image_person WHERE person_id = ?", "SELECT 1 FROM face_not_person WHERE person_id = ?",
                "SELECT 1 FROM parent WHERE parent_id = ?", "SELECT 1 FROM partner WHERE a = ?1 OR b = ?1"):
        assert not con.execute(sql, (p["Hank"],)).fetchone(), sql
    assert not {ph["hank.jpg"], ph["scenery.jpg"]} & {r["id"] for r in search.query(con, "Hank")}
    assert ph["hank_sam.jpg"] in {r["id"] for r in search.query(con, "Sam")}
    # what the reading's page asks before it offers Remove from the tree (its question says nothing of faces)
    assert people.in_photos(con, p["Jim"]) and not people.in_photos(con, p["Hal"])  # added by hand; in none


def test_place_lookups_follow_the_nominatim_policy(cfg, monkeypatch):
    """The server and a contact are settings, the app says who it is, and lookups keep their distance."""
    asked, waits = [], []
    monkeypatch.setattr(places.time, "sleep", waits.append)
    http = httpx.Client(transport=httpx.MockTransport(
        lambda r: asked.append(r) or httpx.Response(200, json=[{"lat": "1.5", "lon": "2.5", "display_name": "X"}])))
    mine = dataclasses.replace(cfg, places_url="https://geo.example.org/search", places_contact="me@example.com")
    assert places.look_up("Somewhere", mine, http) == (1.5, 2.5, "X")
    places.look_up("Elsewhere", mine, http, gap=places.WORKER_GAP)
    assert asked[0].url.host == "geo.example.org" and asked[0].url.params["email"] == "me@example.com"
    assert asked[0].headers["user-agent"].startswith("LineageBox/")
    assert waits[-1] > 10  # the worker's lookups are 15 s apart: 4 a minute


def test_map_tiles_come_from_the_config(cfg):
    mine = dataclasses.replace(cfg, map_tiles="https://tiles.example.org/{z}/{x}/{y}.png")
    page = TestClient(webapp.create_app(mine), client=LAN).get("/map").text
    assert '"https://tiles.example.org/{z}/{x}/{y}.png"' in page and "openstreetmap.org/copyright" in page
