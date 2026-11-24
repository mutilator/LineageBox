"""Cleanup → Family tree: what looks wrong or unfinished in the family tree, and putting it right."""
import re

from fastapi.testclient import TestClient

from lineagebox import events, family, family_check, people
from lineagebox.web import app as webapp
from tests.conftest import LAN
from tests.test_access_web import signed_in, site  # noqa: F401 - site is a fixture

APP = {"X-Requested-With": "lineagebox"}


def test_family_tree_problems(con, cfg):
    pid = lambda name: people.person_id(con, name)
    paul, linda, gina, nadia = pid("Paul Bishop"), pid("Linda Bishop"), pid("Gina Bishop"), pid("Nadia Bishop")
    family.link(con, paul, "wife", linda)
    family.link(con, gina, "father", paul)  # entered before her mother was: Paul's wife is offered
    family.link(con, nadia, "father", paul)
    cara, joel = pid("Cara Elliott"), pid("Joel Conway")
    family.link(con, cara, "brother", joel)  # siblings, no parents for either
    lyle, lena = pid("Lyle Hart"), pid("Lena Hart")
    family.link(con, lyle, "parent", lena)  # a parent with no sex, and one parent
    teddy, erin, ted = pid("Teddy Carver"), pid("Erin Moss"), pid("Ted Carver")
    family.link(con, teddy, "mother", erin)
    family.link(con, teddy, "father", ted)  # parents never entered as partners
    sam, sam_jr = pid("Sam"), pid("Sam Smith")
    family.link(con, sam_jr, "father", sam)  # the same first name, but father and son
    wendy, wendy_quist = pid("Wendy"), pid("Wendy Quist")  # maybe one person
    events.save(con, None, tag="BIRT", person_id=paul, date_text="1985")
    events.save(con, None, tag="BIRT", person_id=gina, date_text="1980")  # before her father
    con.commit()

    found = {}
    for i in family_check.check(con):
        found.setdefault(i.kind, []).append(i)
    text = lambda kind: [i.text for i in found.get(kind, [])]
    assert text("one_parent") == ["Gina Bishop: only Paul Bishop is entered", "Lyle Hart: only Lena Hart is entered",
                                  "Nadia Bishop: only Paul Bishop is entered", "Sam Smith: only Sam is entered"]
    assert found["one_parent"][0].choices == [linda]
    assert text("no_parents") == ["Cara Elliott and Joel Conway"]
    assert text("dates") == ["Gina Bishop: born in 1980, when Paul Bishop (born 1985) was not yet born"]
    assert text("twice") == ["Wendy and Wendy Quist"]  # not Sam and his son Sam Smith
    assert text("no_sex") == ["Lena Hart, parent of Lyle Hart"]  # Sam was entered as a father
    assert text("not_partners") == ["Erin Moss and Ted Carver, parents of Teddy Carver"]

    client = TestClient(webapp.create_app(cfg), client=LAN)
    assert 'href="/cleanup/family"' in client.get("/cleanup").text
    page = client.get("/cleanup/family").text
    assert "Only one parent" in page and "Linda Bishop is the other parent" in page and "Maybe one person" in page
    # the other parent, in one click from here or from the parent's page
    assert client.post("/api/family/link", json={"a": gina, "what": "parent", "b": linda}).status_code == 403
    assert client.post("/api/family/link", json={"a": gina, "what": "parent", "b": 9999}, headers=APP).status_code == 404
    assert client.post("/api/family/link", json={"a": gina, "what": "parent", "b": linda}, headers=APP).status_code == 200
    assert family.load(con).parents[gina] == {paul, linda}
    pauls = client.get(f"/person/{paul}").text
    assert f'data-body=\'{{"a": {nadia}, "b": {linda}, "what": "parent"}}\'' in pauls and "with Linda Bishop?" in pauls
    assert f'data-body=\'{{"a": {gina}, "b": {linda}, "what": "parent"}}\'' not in pauls  # she has both now
    # someone with one parent entered can be given another right in the list on their page
    assert "Another parent" in client.get(f"/person/{lyle}").text and "Another parent" not in client.get(f"/person/{gina}").text
    assert client.post(f"/api/people/{lyle}/link", json={"name": "Ray Hart", "word": "father"}, headers=APP).status_code == 200

    # right as it is: not listed again
    key = found["twice"][0].key
    assert client.post("/api/family/checks/ok", json={"key": key}, headers=APP).status_code == 200
    left = {i.key for i in family_check.check(con)}
    assert key not in left and f"one_parent:{gina}" not in left and f"one_parent:{lyle}" not in left


def test_a_member_is_shown_what_the_whole_tree_has(site, cfg, con):
    """A member's part of the tree leaves out the links to people outside it. Cora sees Jake, the son of her brother
    Sam's wife Tina, but not Jake's father Ed, Tina's former husband: Jake isn't a child with one parent for her, nor
    is Sam offered as his other parent. Hal, whom an admin shows her on his own, isn't "not placed yet": he has a
    brother she doesn't see. What the whole tree has wrong is listed to her as she sees it (the photos she sees), and
    an admin's fine as it is covers a photo problem even when she sees fewer of its photos."""
    p, ph, who = site["people"], site["photos"], site["who"]
    wes = people.person_id(con, "Wes Lane")  # linked to nobody at all
    for pid in (p["Hal"], wes):
        con.execute("INSERT INTO access_rule(account_id, person_id, show, branch) VALUES (?, ?, 1, 0)",
                    (who["Cora"]["id"], pid))
    events.save(con, None, tag="BIRT", person_id=p["Bart"], date_text="2015")  # cora_bart.jpg is from 2010
    con.execute("INSERT INTO image_person(image_id, person_id, added_at) VALUES (?, ?, '2026-01-01')",
                (ph["hidden.jpg"], p["Bart"]))  # and a photo from 2011 an admin hid from the family
    con.execute("UPDATE image SET taken_at = '2011-05-01T10:00:00', taken_at_source = 'exif' WHERE id = ?",
                (ph["hidden.jpg"],))
    con.commit()
    whole = {i.key: i for i in family_check.check(con)}
    assert f"one_parent:{p['Jake']}" not in whole and f"one_parent:{p['Cal']}" in whole
    assert whole[f"photos:{p['Bart']}:before:2"].text.startswith("Bart: in 2 photos")

    cora = signed_in(cfg, con, who["Cora"])
    page = cora.get("/cleanup/family").text
    assert "Cal: only Al is entered" in page  # the whole tree has it too
    assert "Jake: only Tina" not in page and "Sam is the other parent" not in page
    assert "Bart: in a photo from 2010, before being born in 2015" in page and "2 photos" not in page
    listed = len(re.findall(r'<li id="issue-', page))
    assert f'<span class="num">{listed}</span>' in cora.get("/cleanup/counts/family").text
    family_check.mark_ok(con, f"photos:{p['Bart']}:before:2")  # by an admin, who sees both photos
    con.commit()
    assert "Bart: in a photo" not in cora.get("/cleanup/family").text
    # brothers with no parents, both of whom Tina sees: listed to her as to an admin
    assert "Hal and Hank" in signed_in(cfg, con, who["Tina"]).get("/cleanup/family").text

    family_page = cora.get("/family").text
    unplaced = family_page.split("Not placed yet")[1]
    assert f'/person/{wes}"' in unplaced and f'/person/{p["Hal"]}"' not in unplaced
