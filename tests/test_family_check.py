"""Cleanup → Family tree: what looks wrong or unfinished in the family tree, and putting it right."""
from fastapi.testclient import TestClient

from lineagebox import events, family, family_check, people
from lineagebox.web import app as webapp
from tests.conftest import LAN

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
