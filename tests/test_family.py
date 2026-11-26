import re

import pytest
from fastapi.testclient import TestClient

from lineagebox import catalog, db, family, family_check, people, settings
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


def test_a_brother_is_never_a_parent_too(con):
    """Siblings share their parents (Graph), so someone entered as both a brother and a father, either way round, or
    as a sibling of a grandparent's, would be their own ancestor: refused, and the tree stays without loops."""
    ann, bob, cy, gran = (con.execute("INSERT INTO person(name) VALUES (?)", (n,)).lastrowid
                          for n in ("Ann", "Bob", "Cy", "Gran"))
    family.link(con, ann, "brother", bob)
    with pytest.raises(family.Problem, match="Bob is entered as Ann's brother or sister"):
        family.link(con, ann, "father", bob)
    with pytest.raises(family.Problem, match="Bob is entered as Ann's brother or sister"):
        family.link(con, bob, "daughter", ann)
    family.link(con, cy, "father", bob)  # Bob is Cy's father: Cy can't be Ann's brother as well
    with pytest.raises(family.Problem, match="can't be brothers or sisters"):
        family.link(con, ann, "brother", cy)
    with pytest.raises(family.Problem, match="A brother or sister of Ann is already Cy's ancestor"):
        family.link(con, ann, "father", cy)  # Bob's son as his sister's father
    family.link(con, gran, "son", bob)  # Bob's mother is Ann's too: she can't be her daughter
    with pytest.raises(family.Problem, match="Gran is already Ann's ancestor"):
        family.link(con, ann, "daughter", gran)
    family.unlink(con, "sibling", ann, bob)
    family.link(con, ann, "father", bob)
    with pytest.raises(family.Problem, match="Bob is already Ann's ancestor"):
        family.link(con, ann, "brother", bob)
    g = family.load(con)
    assert all(p not in g.ancestors[p] or g.ancestors[p][p] == 0 for p in g.people)
    assert all(p not in g.effective_parents.get(p, ()) for p in g.people)


def test_a_couple_who_never_married(con, cfg, fam):
    """A boyfriend or a girlfriend is a couple in the tree like any other, so their children and photos are the
    family's; but nobody married in, so there are no in-laws and no step-family."""
    linda = fam["Linda"]
    pete, pam, sean = (people.person_id(con, n) for n in ("Pete", "Pam", "Sean"))
    family.link(con, linda, "boyfriend", pete)
    family.link(con, pete, "mother", pam)
    family.link(con, pete, "son", sean)
    g = family.load(con)
    assert g.sex(pete) == "male" and g.partners[linda][pete] == (True, "dating")  # the word says which
    (couple,) = family.links(g, linda)["partners"]
    assert (couple["id"], couple["bond"], couple["word"], couple["current"]) == (pete, "dating", "boyfriend", True)
    hers = {g.name(p): k.label for p, k in family.relations_to(g, linda).items()}
    assert hers["Pete"] == "boyfriend"
    assert (hers["Pam"], hers["Sean"]) == ("boyfriend's mother", "boyfriend's son")  # no mother-in-law, no stepson
    assert family.relations_to(g, fam["Daniel"])[pete].label == "sister's boyfriend"  # and no brother-in-law
    assert family.relations_to(g, pete)[fam["Emma"]].label == "girlfriend's daughter"
    # a child of theirs belongs to them both, and is family on both sides
    tim = people.person_id(con, "Tim")
    family.link(con, linda, "son", tim, with_parent=pete)
    g = family.load(con)
    assert {g.name(p) for p in g.effective_parents[tim]} == {"Linda", "Pete"}
    assert family.relations_to(g, fam["Daniel"])[tim].label == "nephew"
    assert family.relations_to(g, pam)[tim].label == "grandson"
    # the word finds them in search, and the person's page says it
    settings.put(con, "me", linda)
    assert {g.name(i) for i in family.word_index(con)["boyfriend"]} == {"Pete"}
    known = set(family.word_index(con))
    assert family.search_terms("my girlfriends 2024", known) == [("girlfriend", "girlfriends"), ("2024", "2024")]
    page = TestClient(webapp.create_app(cfg), client=LAN).get(f"/person/{linda}").text
    assert re.search(r'>Pete</a>\s*<span class="hint inline">boyfriend</span>', page)
    assert '<option value="girlfriend">' in page and '<option value="ex-girlfriend">' in page


def test_a_couple_who_never_married_can_be_former(con, fam):
    linda = fam["Linda"]
    pete, rob = (people.person_id(con, n) for n in ("Pete", "Rob"))
    family.link(con, linda, "boyfriend", pete)
    with pytest.raises(family.Problem, match="current partner is Pete"):
        family.link(con, linda, "boyfriend", rob)  # one current partner at a time, married or not
    family.set_current(con, linda, pete, False)
    family.link(con, linda, "ex boyfriend", rob)  # typed by hand, without the hyphen the page offers
    g = family.load(con)
    assert {g.name(q): (cur, bond) for q, (cur, bond) in g.partners[linda].items()} == {
        "Pete": (False, "dating"), "Rob": (False, "dating")}
    kin = family.relations_to(g, linda)
    assert kin[pete].label == "ex-boyfriend" and kin[pete].group == "former family"
    assert kin[rob].label == "ex-boyfriend"
    assert [q["word"] for q in family.links(g, linda)["partners"]] == ["boyfriend", "boyfriend"]  # ", former" is shown
    # someone the family entered as a girlfriend before there was an option for it (migration 038)
    dating = db._module(db.MIGRATIONS_DIR / "038_dating.py")
    sue, ann = (people.person_id(con, n) for n in ("Sue", "Ann"))
    con.execute("INSERT INTO relation(person_id, of_id, kind) VALUES (?, ?, 'girlfriend')", (sue, fam["Jake"]))
    con.execute("INSERT INTO relation(person_id, of_id, kind) VALUES (?, ?, 'Ex-Girlfriend')", (ann, fam["Jake"]))
    con.execute("INSERT INTO relation(person_id, of_id, kind) VALUES (?, ?, 'girlfriend')",
                (people.person_id(con, "Zoe"), fam["Daniel"]))  # Daniel has a wife: left to be sorted out by hand
    dating.couples_from_labels(con)
    g = family.load(con)
    assert g.partners[fam["Jake"]] == {sue: (True, "dating"), ann: (False, "dating")}
    assert g.sex(sue) == "female" and not g.partners.get(fam["Daniel"], {}).get(people.person_id(con, "Zoe"))
    assert [r[0] for r in con.execute("SELECT kind FROM relation WHERE of_id = ?", (fam["Jake"],))] == []
    assert con.execute("SELECT count(*) FROM relation WHERE kind = 'girlfriend'").fetchone()[0] == 1


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


def test_everyone_connected_on_one_chart(con, fam):
    """The classic tree can show everyone linked by parents, children and partners at once, each person once: a row a
    generation, partners side by side, children a row under their parents, no card over another, a line between
    partners (dashed for a former one) and down from them to each child."""
    g = family.load(con)
    c = family.chart(g, fam["Daniel"])
    at = {n["id"]: (n["x"], n["y"]) for n in c["nodes"]}
    # all but the friend: the married-in wife's sister and her son too, through their parents (not entered)
    assert {g.name(p) for p in at if p > 0} == set(fam) - {"Jake"} and c["people"] == len(fam) - 1
    assert sum(1 for p in at if p < 0) == 1
    row = lambda name: at[fam[name]][1]
    assert row("Daniel") == row("Katherine") == row("Ana") == row("Mike") == row("Diane") == row("Rosa")
    assert row("Grandpa") == row("Grandma") == row("Daniel") - family.ROW
    assert row("Owen") == row("Nora") == row("Leo") == row("Daniel") + family.ROW == row("Finn") - family.ROW
    beside = family.CARD_W + family.PARTNER_GAP
    assert abs(at[fam["Daniel"]][0] - at[fam["Katherine"]][0]) == beside
    assert abs(at[fam["Mike"]][0] - at[fam["Diane"]][0]) == beside == abs(at[fam["Mike"]][0] - at[fam["Rosa"]][0])
    for a, (ax, ay) in at.items():
        for b, (bx, by) in at.items():
            assert a == b or ay != by or abs(ax - bx) >= family.CARD_W + family.PARTNER_GAP, (g.name(a), g.name(b))
    lines = c["lines"]
    couple = lambda a, b, dashed: {"x": min(at[fam[a]][0], at[fam[b]][0]) + family.CARD_W,
                                   "y": at[fam[a]][1] + family.COUPLE_Y, "w": family.PARTNER_GAP, "h": 0,
                                   "dashed": dashed, "faint": False}  # Mike is Daniel's blood
    assert couple("Mike", "Diane", True) in lines and couple("Mike", "Rosa", False) in lines
    for p, (x, y) in at.items():  # a line comes down to every child, to the middle of the top of its card
        if any(q in at for q in g.effective_parents.get(p, ())):
            assert any(l["w"] == 0 and l["x"] == round(x + family.CARD_W / 2) and l["y"] + l["h"] == y for l in lines)
    # from anyone in it, the same chart; one for the whole family, the friend apart (no family of his own)
    assert {n["id"] for n in family.chart(g, fam["Finn"])["nodes"]} == set(at)
    assert [n for _, _, n in family.charts(g)] == [len(fam) - 1]
    # bold: Daniel's own blood; those who married in, and the in-laws' side, plain
    direct = {g.name(n["id"]) for n in c["nodes"] if n["direct"] and n["id"] > 0}
    assert {"Grandpa", "Daniel", "Linda", "Emma", "Finn", "Kyle", "Owen"} <= direct
    assert not direct & {"Katherine", "Ana", "Leo", "Diane", "Rosa", "Nora"}
    # the lines of a family his blood isn't in (Katherine's sister Ana and her son) are drawn lighter
    leo = at[fam["Leo"]]
    assert any(l["faint"] and l["w"] == 0 and l["x"] == round(leo[0] + family.CARD_W / 2) and l["y"] + l["h"] == leo[1]
               for l in lines)


def test_the_everyone_connected_page(con, cfg, fam):
    client = TestClient(webapp.create_app(cfg), client=LAN)
    one = client.get(f"/family/tree?person={fam['Owen']}").text
    assert '<a href="/family/tree?root=' in one and 'aria-current="page" title="Everyone descended' in one
    page = client.get(f"/family/tree?person={fam['Owen']}&everyone=1").text
    assert 'class="tree-sheet all"' in page and 'aria-current="page" title="Every family' in page
    root = re.search(r'data-root="(-?\d+)"', page)[1]
    for name, pid in fam.items():  # a card for each, its click gliding onto them in this chart
        assert (f'href="/family/tree?root={root}&amp;person={pid}&amp;everyone=1" data-id="{pid}" '
                f'data-line="{root}"' in page) == (name != "Jake"), name
    assert f'data-person="{fam["Owen"]}" data-mode="everyone"' in page and "17 people" in page
    assert '<input type="hidden" name="everyone" value="1">' in page and "· 17 people</option>" in page


def test_roots(con, cfg, fam):
    """A trimmed Everyone connected from one person: every family on their generation and above (their wife's too)
    with all its descendants, but below them those who married in come without their own families: Daniel's roots
    have his son's wife, not her father. From the son's roots, his wife's father is there."""
    ned = people.person_id(con, "Ned")
    family.link(con, fam["Nora"], "father", ned)
    con.commit()
    g = family.load(con)
    names = lambda ids: {g.name(p) for p in ids if p > 0}
    assert names(family.roots(g, fam["Daniel"])) == set(fam) - {"Jake"}  # all of Everyone connected but Ned
    assert ned in family.connected(g, fam["Daniel"])
    assert ned in family.roots(g, fam["Owen"]) and names(family.roots(g, fam["Owen"])) == set(fam) - {"Jake"} | {"Ned"}
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get(f"/family/tree?person={fam['Daniel']}&roots=1").text
    assert "<h1>Roots of Daniel</h1>" in page and f'data-root="{fam["Daniel"]}"' in page and 'data-mode="roots"' in page
    assert f'data-id="{ned}"' not in page
    for name in ("Katherine", "Leo", "Nora", "Ava"):  # a click shows their own roots
        pid = fam[name]
        assert f'href="/family/tree?root={pid}&amp;person={pid}&amp;roots=1" data-id="{pid}" data-line="{pid}"' in page
    assert f'data-id="{ned}"' in client.get(f"/family/tree?person={fam['Daniel']}&everyone=1").text
    # one person's roots are theirs alone, whoever their partner is
    assert "<h1>Roots of Owen</h1>" in client.get(f"/family/tree?root={fam['Owen']}&person={fam['Owen']}&roots=1").text


def test_family_pages_and_api(con, cfg, fam):
    client = TestClient(webapp.create_app(cfg), client=LAN)
    app = {"X-Requested-With": "lineagebox"}
    page = client.get("/family").text
    assert "Relationships as seen from" in page and "former sister-in-law" in page
    assert "Owen&#39;s friend" in page  # Jake
    tree = client.get("/family/tree").text  # the whole line down from one ancestor: a page of its own
    assert 'class="tree"' in tree and "Grandpa &amp; Grandma" in tree
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
    assert 'class="step-tag">step</span>' in tree  # Ava, under Mike and Rosa
    assert 'class="fcard step' not in tree and 'class="fcard direct stepkid"' in tree  # .step is the viewer's arrows
    # the family line has bold frames; partners married in do not
    assert re.search(rf'class="fcard direct" href="[^"]*" data-id="{fam["Mike"]}"', tree)
    assert re.search(rf'class="fcard" href="[^"]*" data-id="{fam["Rosa"]}"', tree)
    assert re.search(r'/static/app\.js\?v=[0-9a-f]{10}"', page)  # a new script is fetched after each update
    # Mike has had two wives: each is a branch with her own children under her
    branches = {seg.split('data-id="')[1].split('"')[0]: seg for seg in tree.split('<li class="union')[1:]}
    assert f'data-id="{fam["Kyle"]}"' in branches[str(fam["Diane"])] and "former partner" in branches[str(fam["Diane"])]
    assert f'data-id="{fam["Ava"]}"' in branches[str(fam["Rosa"])]
    assert f'data-id="{fam["Kyle"]}"' not in branches[str(fam["Rosa"])]
    stand_in = family.tree_root(family.load(con), fam["Ana"])
    assert "Parents of Ana and Katherine" in client.get(f"/family/tree?root={stand_in}").text

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


def test_people_with_no_photos_are_shown_and_can_be_tidied_up(con, cfg, fam):
    """Someone no photo shows was left off the People page and off Not placed yet, so there was no way to open them:
    a name typed by mistake could never be edited or taken out again (the user's ask)."""
    client = TestClient(webapp.create_app(cfg), client=LAN)
    app = {"X-Requested-With": "lineagebox"}
    stray = people.person_id(con, "g")  # a name typed by mistake: no photos, no family, no group
    con.commit()

    page = client.get("/people").text
    assert f'href="/person/{stray}"' in page and f'href="/person/{fam["Jake"]}"' in page
    assert 'data-value="no photos"' in page  # the chip that picks them out of the list

    shown = client.get("/family").text
    unplaced = shown.split("Not placed yet")[1]
    assert f'/person/{stray}"' in unplaced and "no photos" in unplaced
    assert f'data-api="/api/people/{stray}/remove"' in unplaced  # × for an admin: nothing else refers to them
    assert f'data-api="/api/people/{fam["Jake"]}/remove"' not in shown  # Jake is a friend of Owen's: placed

    r = client.post(f"/api/people/{stray}/remove", headers=app)
    assert r.status_code == 200 and r.json()["undo"]
    assert con.execute("SELECT count(*) FROM person WHERE name = 'g'").fetchone()[0] == 0
    assert "Not placed yet" not in client.get("/family").text


def test_merging_people_keeps_their_family(con, fam):
    family.merge(con, fam["Kyle"], fam["Jake"])  # say Kyle and Jake were one person named twice
    con.execute("DELETE FROM person WHERE id = ?", (fam["Kyle"],))
    g = family.load(con)
    assert g.parents[fam["Jake"]] == {fam["Mike"], fam["Diane"]}
