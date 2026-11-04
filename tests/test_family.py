import re

import pytest
from fastapi.testclient import TestClient

from lineagebox import catalog, family, family_check, people, settings
from lineagebox.web import app as webapp
from tests.conftest import LAN


@pytest.fixture
def fam(con):
    """Daniel (me) and his brother Mike and sister Linda, their parents, partners, children and a friend."""
    ids = {name: people.person_id(con, name) for name in (
        "Grandpa", "Grandma", "Daniel", "Mike", "Linda", "Katherine", "Ana", "Leo", "Owen", "Lucas", "Diane", "Kyle",
        "Rosa", "Ava", "Emma", "Finn", "Nora", "Jake")}
    link = lambda who, word, other, **kw: family.link(con, ids[who], word, ids[other], **kw)
    link("Daniel", "father", "Grandpa")
    link("Daniel", "mother", "Grandma")
    link("Daniel", "brother", "Mike")  # Mike's parents come from Daniel's
    link("Grandma", "daughter", "Linda", with_parent=ids["Grandpa"])
    link("Daniel", "wife", "Katherine")
    link("Katherine", "sister", "Ana")
    link("Ana", "son", "Leo")
    link("Daniel", "son", "Owen", with_parent=ids["Katherine"])
    link("Katherine", "son", "Lucas", with_parent=ids["Daniel"])
    link("Mike", "ex-wife", "Diane")
    link("Mike", "son", "Kyle", with_parent=ids["Diane"])
    link("Mike", "wife", "Rosa")
    link("Rosa", "daughter", "Ava")
    link("Linda", "daughter", "Emma")
    link("Emma", "son", "Finn")
    link("Owen", "wife", "Nora")
    link("Owen", "friend", "Jake")
    settings.put(con, "me", ids["Daniel"])
    return ids


def test_what_everyone_is_to_me(con, fam):
    g = family.load(con)
    kin = {g.name(pid): k.label for pid, k in family.relations_to(g, fam["Daniel"]).items()}
    assert kin == {
        "Daniel": "you", "Grandpa": "father", "Grandma": "mother", "Mike": "brother", "Linda": "sister",
        "Katherine": "wife", "Ana": "sister-in-law", "Leo": "nephew", "Owen": "son", "Lucas": "son",
        "Diane": "former sister-in-law", "Kyle": "nephew", "Rosa": "sister-in-law", "Ava": "step-niece",
        "Emma": "niece", "Finn": "great-nephew", "Nora": "daughter-in-law"}  # Jake is a friend, not family
    # the same people from others' point of view: a child links to the right parents
    from_kyle = family.relations_to(g, fam["Kyle"])
    assert from_kyle[fam["Diane"]].label == "mother" and from_kyle[fam["Rosa"]].label == "stepmother"
    assert from_kyle[fam["Ava"]].label == "stepsister" and from_kyle[fam["Owen"]].label == "cousin"
    assert family.relations_to(g, fam["Rosa"])[fam["Kyle"]].label == "stepson"
    assert family.relations_to(g, fam["Diane"])[fam["Mike"]].label == "ex-husband"
    # whoever has no sex set gets the neutral word
    con.execute("UPDATE person SET sex = NULL WHERE id = ?", (fam["Emma"],))
    assert family.relations_to(family.load(con), fam["Daniel"])[fam["Emma"]].label == "niece or nephew"


def test_everyones_own_family_without_me(con, fam):
    """Each person's page works out their family from their own point of view, whoever is marked as "me"."""
    con.execute("DELETE FROM settings WHERE key = 'me'")
    g = family.load(con)
    theirs = lambda who: {group: {g.name(p): label for p, label in people}
                          for group, people in family.worked_out(g, fam[who])}
    assert theirs("Owen")["grandparents"] == {"Grandpa": "grandfather", "Grandma": "grandmother"}
    assert theirs("Owen")["aunts and uncles"] == {"Mike": "uncle", "Linda": "aunt", "Ana": "aunt",
                                                  "Rosa": "aunt"}  # uncle Mike's wife
    # a current partner's children from another relationship are step-family, both ways
    assert theirs("Mike")["step-family"] == {"Ava": "stepdaughter"}
    assert theirs("Ava")["step-family"] == {"Mike": "stepfather", "Kyle": "stepbrother"}
    assert theirs("Kyle")["step-family"] == {"Rosa": "stepmother", "Ava": "stepsister"}
    assert "step-family" not in theirs("Diane")  # Mike's former wife: Ava is not her stepdaughter
    assert theirs("Diane")["extended family"]["Ava"] == "ex-husband's wife's daughter"
    family.set_current(con, fam["Mike"], fam["Rosa"], False)  # divorced: no longer step-family
    g = family.load(con)
    assert "step-family" not in theirs("Kyle")


def test_one_current_partner_and_no_loops(con, fam):
    with pytest.raises(family.Problem, match="current partner is Rosa"):
        family.link(con, fam["Mike"], "wife", fam["Diane"])
    family.set_current(con, fam["Mike"], fam["Rosa"], False)
    family.link(con, fam["Mike"], "wife", fam["Diane"])  # remarried
    g = family.load(con)
    assert family.relations_to(g, fam["Daniel"])[fam["Rosa"]].label == "former sister-in-law"
    with pytest.raises(family.Problem, match="ancestor"):
        family.link(con, fam["Kyle"], "son", fam["Grandpa"])
    with pytest.raises(family.Problem, match="two parents"):
        family.link(con, fam["Kyle"], "father", fam["Jake"])
    a, b, pa = (con.execute("INSERT INTO person(name) VALUES (?)", (n,)).lastrowid for n in ("Ann", "Bob", "Pa"))
    family.link(con, a, "father", pa)
    family.link(con, a, "brother", b)  # Bob, with no parents entered, has his sister's
    with pytest.raises(family.Problem, match="Pa is already Bob's ancestor"):
        family.link(con, pa, "father", b)


def test_merging_never_makes_someone_their_own_ancestor(con):
    ids = {n: con.execute("INSERT INTO person(name) VALUES (?)", (n,)).lastrowid
           for n in ("John Smith", "John Smith Jr", "John Smith III")}
    family.link(con, ids["John Smith Jr"], "father", ids["John Smith"])
    family.link(con, ids["John Smith III"], "father", ids["John Smith Jr"])
    third = ids["John Smith III"]
    assert people.rename(con, third, "John Smith") == third  # his grandfather's name: someone else, kept apart
    con.commit()
    assert catalog.person(con, third)["name"] == "John Smith" and catalog.person(con, ids["John Smith"])
    with pytest.raises(people.Problem, match="can't be one person"):
        people.merge(con, third, ids["John Smith"])
    con.rollback()
    assert catalog.person(con, third) is not None  # nothing of the merge was kept
    con.execute("INSERT INTO parent(child_id, parent_id) VALUES (?, ?)", (ids["John Smith"], third))  # an old loop
    con.commit()
    loops = [i for i in family_check.check(con) if i.kind == "loop"]
    assert len(loops) == 1 and set(loops[0].people) == set(ids.values())


def test_search_words(con, fam):
    index = family.word_index(con)
    name = lambda ids: {family.load(con).name(i) for i in ids}
    assert name(index["nephew"]) == {"Kyle", "Leo"}
    assert name(index["brother"]) == {"Mike"}
    assert name(index["inlaw"]) == {"Ana", "Rosa", "Nora"}
    assert name(index["former"]) == {"Diane"} and name(index["friend"]) == {"Jake"}
    known = set(index)
    assert family.search_terms("my brothers-in-law 2024", known) == [("brotherinlaw", "brothers in law"),
                                                                     ("2024", "2024")]
    assert family.search_terms("great view", known) == [("great", "great"), ("view", "view")]
    assert family.search_terms("Owen's friends", known) == [("owen", "owen"), ("friend", "friends")]
    assert [k for k, _ in family.search_terms("ex-wife former family the in-laws", known)] == ["exwife", "former",
                                                                                              "inlaw"]


def test_tree_groups_children_by_their_parents(con, fam):
    g = family.load(con)
    assert family.tree_root(g, fam["Kyle"]) == fam["Grandpa"]
    t = family.tree(g, fam["Grandpa"])
    (union,) = t["unions"]
    assert union["partner"] == fam["Grandma"]
    mike = next(c for c in union["children"] if c["id"] == fam["Mike"])
    assert [(u["partner"], u["current"], [c["id"] for c in u["children"]], [c["id"] for c in u["step"]])
            for u in mike["unions"]] == [(fam["Rosa"], True, [], [fam["Ava"]]),  # Rosa's daughter, Mike's step
                                         (fam["Diane"], False, [fam["Kyle"]], [])]
    assert family.tops(g)[0] == (fam["Grandpa"], 8)  # 3 children, 4 grandchildren, a great-grandchild
    # siblings entered without their parents still share a tree, under stand-in parents
    assert family.tree_root(g, fam["Leo"]) < 0
    assert {c["id"] for c in family.tree(g, family.tree_root(g, fam["Ana"]))["unions"][0]["children"]} == {
        fam["Katherine"], fam["Ana"]}


def test_family_pages_and_api(con, cfg, fam):
    client = TestClient(webapp.create_app(cfg), client=LAN)
    app = {"X-Requested-With": "lineagebox"}
    page = client.get("/family").text
    assert "Relationships as seen from" in page and 'class="tree"' in page
    assert "Grandpa &amp; Grandma" in page and "former sister-in-law" in page
    assert "Owen&#39;s friend" in page  # Jake
    person = client.get(f"/person/{fam['Kyle']}").text
    assert "your nephew" in person and f'href="/person/{fam["Diane"]}">Diane</a>' in person
    for pid in fam.values():  # every kind of link renders
        assert client.get(f"/person/{pid}").status_code == 200
    # a child is added with no other parent unless one is picked, even for someone with partners
    mike = client.get(f"/person/{fam['Mike']}").text
    assert re.search(r'<select name="with_parent"[^>]*>\s*<option value="" selected>', mike)
    assert "Friend of" in client.get(f"/person/{fam['Jake']}").text
    # several pets: one entry, on the person's page and on the Family page
    for pet in ("Rex", "Biscuit", "Pepper"):
        family.link(con, fam["Daniel"], "pet", people.person_id(con, pet))
    daniel = client.get(f"/person/{fam['Daniel']}").text
    assert daniel.count("<dt>Pets</dt>") == 1 and "<dt>Pet</dt>" not in daniel
    pets = daniel.split("<dt>Pets</dt>")[1].split("</dd>")[0]
    assert [n for n in ("Biscuit", "Pepper", "Rex") if f">{n}</a>" in pets] == ["Biscuit", "Pepper", "Rex"]
    assert "Daniel&#39;s pets" in client.get("/family").text
    # worked-out words are not taken as other relationships
    for word in ("grandmother", "step-brother", "sister-in-law", "cousin"):
        bad = client.post(f"/api/people/{fam['Daniel']}/link", json={"word": word, "name": "Jake"}, headers=app)
        assert bad.status_code == 400 and "worked out" in bad.json()["detail"]
    owen = client.get(f"/person/{fam['Owen']}").text
    assert "Worked out from these" in owen and "grandfather" in owen
    assert 'class="step-tag">step</span>' in page  # Ava, under Mike and Rosa
    assert 'class="fcard step' not in page and 'class="fcard direct stepkid"' in page  # .step is the viewer's arrows
    # the family line has bold frames; partners married in do not
    assert f'class="fcard direct" href="/person/{fam["Mike"]}"' in page
    assert f'class="fcard" href="/person/{fam["Rosa"]}"' in page
    assert re.search(r'/static/app\.js\?v=[0-9a-f]{10}"', page)  # a new script is fetched after each update
    # Mike has had two wives: each is a branch with her own children under her
    branches = {seg.split('href="/person/')[1].split('"')[0]: seg for seg in page.split('<li class="union')[1:]}
    assert f'/person/{fam["Kyle"]}"' in branches[str(fam["Diane"])] and "former partner" in branches[str(fam["Diane"])]
    assert f'/person/{fam["Ava"]}"' in branches[str(fam["Rosa"])]
    assert f'/person/{fam["Kyle"]}"' not in branches[str(fam["Rosa"])]
    stand_in = family.tree_root(family.load(con), fam["Ana"])
    assert "Parents of Ana and Katherine" in client.get(f"/family?root={stand_in}").text

    # adding someone new: they join the catalog and the family
    r = client.post(f"/api/people/{fam['Kyle']}/link", json={"word": "daughter", "name": "Zoe", "with_parent": ""},
                    headers=app)
    assert r.status_code == 200
    zoe = con.execute("SELECT id, sex FROM person WHERE name = 'Zoe'").fetchone()
    assert zoe["sex"] == "female" and family.load(con).parents[zoe["id"]] == {fam["Kyle"]}
    assert "great-niece" in client.get(f"/person/{zoe['id']}").text
    bad = client.post(f"/api/people/{fam['Mike']}/link", json={"word": "wife", "name": "Diane"}, headers=app)
    assert bad.status_code == 400 and "current partner is Rosa" in bad.json()["detail"]
    client.post("/api/family/unlink", json={"kind": "parent", "a": zoe["id"], "b": fam["Kyle"]}, headers=app)
    assert zoe["id"] not in family.load(con).parents
    client.post("/api/family/me", json={"person_id": fam["Kyle"]}, headers=app)
    assert "your father" in client.get(f"/person/{fam['Mike']}").text


def test_merging_people_keeps_their_family(con, fam):
    family.merge(con, fam["Kyle"], fam["Jake"])  # say Kyle and Jake were one person named twice
    con.execute("DELETE FROM person WHERE id = ?", (fam["Kyle"],))
    g = family.load(con)
    assert g.parents[fam["Jake"]] == {fam["Mike"], fam["Diane"]}
