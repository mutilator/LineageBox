"""A group's family too: putting a family friend in a group asks whether their own family (wife, children...) goes in it
as well, listing only those not in it yet, and never the family's own people; with nobody to ask about, it is done at
once. Each is one change to undo."""
from fastapi.testclient import TestClient

from lineagebox import family, people
from lineagebox.web import app as webapp
from tests.conftest import LAN

APP = {"X-Requested-With": "lineagebox"}


def tree(con):
    """Ana is "me", with her daughter Dee. Bob Baker, a friend of the family: his wife Jane, his sons Sid and Tom (Tom
    married Dee, so he is Ana's son-in-law), his mother Mo (in the group already, written another way), his former
    wife Exie. Zed and Zoe: neighbors."""
    ids = {n: people.person_id(con, n) for n in ("Ana", "Dee", "Bob Baker", "Jane", "Sid", "Tom", "Mo", "Exie", "Zed",
                                                 "Zoe")}
    for who, word, other in (("Ana", "daughter", "Dee"), ("Bob Baker", "wife", "Jane"), ("Bob Baker", "son", "Sid"),
                             ("Bob Baker", "son", "Tom"), ("Bob Baker", "mother", "Mo"), ("Bob Baker", "ex-wife", "Exie"),
                             ("Tom", "wife", "Dee"), ("Zed", "wife", "Zoe")):
        family.link(con, ids[who], word, ids[other])
    people.add_to_group(con, ids["Mo"], "family friends")
    con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('me', ?)", (str(ids["Ana"]),))
    con.commit()
    return ids


def test_their_family_not_in_it_yet_and_not_ours(con):
    ids = tree(con)
    # Mo is in it already (whatever way the group is written); Tom married into Ana's family and Dee is her daughter;
    # Ana is "me"; Exie is former family
    found = people.group_family(con, ids["Bob Baker"], "Family  Friends")
    assert {f["id"]: (f["name"], f["how"]) for f in found} == {ids["Jane"]: ("Jane", "wife"), ids["Sid"]: ("Sid", "son")}
    assert people.group_family(con, 99999, "Family friends") == []


def test_asked_first_then_as_ticked(con, cfg):
    ids = tree(con)
    client = TestClient(webapp.create_app(cfg), client=LAN)
    url = f"/api/people/{ids['Bob Baker']}/groups"
    asked = client.post(url, json={"name": "Family friends"}, headers=APP).json()
    ask = asked["choose"]
    assert ask["title"] == "Put Bob's family in family friends too?" and ask["field"] == "also"
    assert {i["id"]: (i["label"], i["detail"]) for i in ask["items"]} == {ids["Jane"]: ("Jane", "wife"),
                                                                        ids["Sid"]: ("Sid", "son")}
    assert ask["yes"] == "Add the ticked ones too" and ask["no"] == "Only Bob"
    assert people.person_groups(con, ids["Bob Baker"]) == []  # nothing is done until it is answered

    # Jane ticked, Sid not; Ana, who isn't his family, can't be slipped in
    said = client.post(url, json={"name": "Family friends", "also": [ids["Jane"], ids["Ana"]]}, headers=APP).json()
    assert said["message"] == "Put Bob Baker and 1 of their family in family friends."
    assert set(people.groups(con)["family friends"]) == {ids["Mo"], ids["Bob Baker"], ids["Jane"]}  # as first written
    undo = con.execute("SELECT max(id) FROM change_log").fetchone()[0]

    # Sid's family is in it now, but for his brother Tom, who married into Ana's: done at once, nothing asked
    sid = client.post(f"/api/people/{ids['Sid']}/groups", json={"name": "Family friends"}, headers=APP).json()
    assert "choose" not in sid and ids["Sid"] in people.groups(con)["family friends"]

    # Only Zed: his wife stays out
    zed = client.post(f"/api/people/{ids['Zed']}/groups", json={"name": "Neighbors"}, headers=APP).json()
    assert [i["label"] for i in zed["choose"]["items"]] == ["Zoe"] and zed["choose"]["no"] == "Only Zed"
    client.post(f"/api/people/{ids['Zed']}/groups", json={"name": "Neighbors", "also": []}, headers=APP)
    assert people.groups(con)["Neighbors"] == [ids["Zed"]]

    # undone as one change: Bob and Jane both out again
    assert client.post(f"/api/changes/{undo}/undo-own", headers=APP).status_code == 200
    assert set(people.groups(con)["family friends"]) == {ids["Mo"], ids["Sid"]}
