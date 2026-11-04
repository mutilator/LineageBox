"""Who a family member sees (access.py): the rules of docs/access-control.md on a made-up family, the photos that
follow, and a member's connection holding only those."""
import pytest

from lineagebox import access, catalog, db, family, search, tagging
from tests import family_fixture as fx


@pytest.fixture
def fam(con, cfg, library):
    return fx.build(con, cfg, library)


def names(fam, ids):
    by_id = {v: k for k, v in fam["people"].items()}
    return {by_id[i] for i in ids}


@pytest.mark.parametrize("who", ["Cora", "Sam", "Tina"])
def test_the_rules_on_the_example(con, fam, who):
    g = family.load(con)
    assert names(fam, access.people_for(g, fam["people"][who])) == fx.SEES[who]


def test_a_couple_sees_the_same_family(con, fam):
    g = family.load(con)
    p = fam["people"]
    assert access.people_for(g, p["Sam"]) == access.people_for(g, p["Tina"])
    # Cora sees Sam's wife and her son from before, but not Tina's parents, sister or former husband
    cora = names(fam, access.people_for(g, p["Cora"]))
    assert {"Tina", "Jake"} <= cora and not {"Hank", "Hazel", "Rosa", "Ed", "Hal"} & cora
    # a former partner counts only with a child in the tree; friends and pets are outside it
    assert "Eve" in cora and "Fay" not in cora and not {"Jim", "Rufus", "Zed", "Zoe"} & cora


def test_exceptions_and_people_added(con, fam):
    g = family.load(con)
    p = fam["people"]
    cora = p["Cora"]
    assert "Jim" in names(fam, access.people_for(g, cora, added={p["Jim"]}))  # her friend, whom she added
    shown = access.people_for(g, cora, rules=[(p["Zed"], 1, 0)])
    assert "Zed" in names(fam, shown) and "Zoe" not in names(fam, shown)  # alone
    assert {"Zed", "Zoe"} <= names(fam, access.people_for(g, cora, rules=[(p["Zed"], 1, 1)]))  # with his family
    hidden = names(fam, access.people_for(g, cora, rules=[(p["Bart"], 0, 0)]))
    assert "Bart" not in hidden and "Bella" in hidden
    # "not shown" wins over "shown", and a branch taken away goes with its partners and children
    gone = names(fam, access.people_for(g, cora, rules=[(p["Milo"], 0, 1), (p["Milo"], 1, 0)]))
    assert not {"Milo", "Mel", "Meg", "Mia"} & gone
    assert access.people_for(g, None) == set()  # an account tied to nobody sees nobody


def test_stand_in_parents_and_loops(con, fam):
    p = fam["people"]
    con.execute("INSERT INTO parent(child_id, parent_id) VALUES (?, ?)", (p["Old Rex"], p["Cal"]))  # bad data
    con.commit()
    g = family.load(con)
    assert names(fam, access.people_for(g, p["Cora"])) >= fx.SEES["Cora"]  # finishes, and loses nobody
    assert "Hal" in names(fam, access.people_for(g, p["Tina"]))  # Hank's brother, through their stand-in parent


def test_photos_follow_the_people(con, cfg, fam):
    cora = fx.account(con, fam["people"]["Cora"], "cora@example.com")
    v = access.compute(con, cora)
    ph = fam["photos"]
    seen = {name for name, i in ph.items() if i in v.images}
    # her picnic, her father (recognised), her brother with Tina's father; not Tina's father alone, Zed, the dog,
    # her friend added by hand, a photo with nobody in it, nor the photo an admin hid
    assert seen == {"cora_bart.jpg", "rex.jpg", "hank_sam.jpg"}
    assert all(catalog.image(con, i)["sha256"] in v.shas for i in v.images)
    tina = access.compute(con, fx.account(con, fam["people"]["Tina"], "tina@example.com"))
    assert {"hank.jpg", "hank_sam.jpg"} <= {n for n, i in ph.items() if i in tina.images}
    admin = access.compute(con, fx.account(con, fam["people"]["Sam"], "sam@example.com", "admin"))
    assert admin.admin and admin.sees_image(ph["zed.jpg"])


def test_hidden_folders_and_uploads(con, cfg, fam):
    ph, p = fam["photos"], fam["people"]
    cora = fx.account(con, p["Cora"], "cora@example.com")
    con.execute("UPDATE folder SET hidden = 1 WHERE path LIKE '%/Family'")  # everything below it too
    con.commit()
    assert not access.compute(con, cora).images & {ph["cora_bart.jpg"], ph["rex.jpg"]}
    # her own uploads stay hers, hidden or not, and photos with nobody in them are seen by whoever uploaded them
    folder = f"{cfg.folders[0]}/Trips"
    con.execute("UPDATE account SET upload_folder = ? WHERE id = ?", (folder, cora["id"]))
    con.commit()
    v = access.compute(con, dict(cora, upload_folder=folder))
    assert ph["scenery.jpg"] in v.images


def test_a_members_connection_holds_only_what_they_see(con, cfg, fam):
    p, ph = fam["people"], fam["photos"]
    v = access.compute(con, fx.account(con, p["Cora"], "cora@example.com"))
    c = access.scope(db.connect(cfg, migrate_schema=False), v)
    try:
        assert catalog.image(c, ph["zed.jpg"]) is None and catalog.image(c, ph["cora_bart.jpg"]) is not None
        assert catalog.person(c, p["Zed"]) is None and catalog.person(c, p["Bart"]) is not None
        listed = {r["name"] for r in catalog.people(c)}
        assert "Zed" not in listed and "Hank" not in listed and {"Cora", "Bart", "Rex", "Sam"} <= listed
        assert search.query(c, fx.SECRET) == [] and search.query(c, "Zed") == []
        assert fx.SECRET not in str(catalog.tags(c)) and tagging.names(c) == []
        # Tina's father is not hers to see, but he is in a photo of her brother: that one is hers
        assert {r["id"] for r in catalog.person_images(c, p["Hank"])} == {ph["hank_sam.jpg"]}
        g = family.load(c)  # her part of the tree only, worked out from her
        assert set(g.people) == v.people and family.me(c) == p["Cora"]
        assert c.execute("SELECT count(*) FROM event").fetchone()[0] == 0  # Zed's birth
        # a write through one of her views fails instead of reaching rows she can't see
        with pytest.raises(Exception, match="view"):
            c.execute("UPDATE face SET person_id = NULL")
        with pytest.raises(Exception, match="view"):
            c.execute("INSERT INTO person(name) VALUES ('x')")
    finally:
        c.close()
    # an admin's connection is untouched
    c = access.scope(db.connect(cfg, migrate_schema=False), access.EVERYONE)
    assert catalog.image(c, ph["zed.jpg"]) is not None
    c.close()


def test_viewers_are_kept_and_refreshed(con, fam):
    viewers = access.Viewers()
    cora = fx.account(con, fam["people"]["Cora"], "cora@example.com")
    first = viewers.get(con, cora)
    assert viewers.get(con, cora) is first
    viewers.changed()
    assert viewers.get(con, cora) is not first
