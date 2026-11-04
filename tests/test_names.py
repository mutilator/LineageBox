"""Two people may have the same name (migration 014): name boxes send who was picked, a name two people have must be
picked, and a family member's name that matches nobody they can see is someone new."""
import sqlite3

import numpy as np
import pytest
from fastapi.testclient import TestClient

from lineagebox import db, people, tagging
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests.conftest import LAN, rel

APP = {"X-Requested-With": "lineagebox"}


def test_migration_keeps_ids_links_and_faces(cfg):
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(cfg.db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    db.migrate(con, cfg, upto=13)
    ids = {n: con.execute("INSERT INTO person(name, uid) VALUES (?, ?)", (n, f"u{n}")).lastrowid
           for n in ("Ann", "Bob", "Cy")}
    con.execute("INSERT INTO parent(child_id, parent_id) VALUES (?, ?)", (ids["Cy"], ids["Ann"]))
    con.execute("INSERT INTO partner(a, b) VALUES (?, ?)", (ids["Ann"], ids["Bob"]))
    con.execute("INSERT INTO person_name(person_id, name, type) VALUES (?, 'Annie', 'nickname')", (ids["Ann"],))
    image = con.execute("INSERT INTO image(sha256, format) VALUES (?, 'jpeg')", ("a" * 64,)).lastrowid
    con.execute("INSERT INTO face(image_id, embedding, person_id, assigned_by) VALUES (?, ?, ?, 'user')",
                (image, np.zeros(512, np.float32).tobytes(), ids["Bob"]))
    con.commit()
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO person(name) VALUES ('Ann')")
    con.rollback()

    db.migrate(con, cfg)
    one = lambda sql, *a: con.execute(sql, a).fetchone()[0]
    assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1  # switched back on after the rebuild
    assert dict(con.execute("SELECT name, id FROM person")) == ids
    assert one("SELECT parent_id FROM parent WHERE child_id = ?", ids["Cy"]) == ids["Ann"]
    assert one("SELECT count(*) FROM partner") == 1 and one("SELECT count(*) FROM person_name") == 1
    assert one("SELECT person_id FROM face") == ids["Bob"]
    assert one("SELECT count(*) FROM sqlite_master WHERE name IN ('person_uid', 'person_lower_name')") == 2
    con.execute("INSERT INTO person(name, uid) VALUES ('Ann', 'another')")  # a second Ann is fine now
    assert one("SELECT count(*) FROM person WHERE name = 'Ann'") == 2
    with pytest.raises(sqlite3.IntegrityError):  # still one person per uid, and links still checked
        con.execute("INSERT INTO person(name, uid) VALUES ('Dee', 'uAnn')")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO parent(child_id, parent_id) VALUES (9999, ?)", (ids["Ann"],))
    con.execute("DELETE FROM person WHERE id = ?", (ids["Cy"],))  # cascades as before
    assert one("SELECT count(*) FROM parent") == 0
    con.close()


def test_person_id_by_pick_name_and_among(con):
    a1 = people.person_id(con, "John Smith")
    assert people.person_id(con, "john  smith") == a1  # one John Smith: found by name
    a2 = con.execute("INSERT INTO person(name) VALUES ('John Smith')").lastrowid
    with pytest.raises(people.Problem, match="2 people are called John Smith"):
        people.person_id(con, "John Smith")
    assert people.person_id(con, "John Smith", pid=a2) == a2  # picked from the suggestions
    with pytest.raises(people.Problem):  # a pick whose name was typed over is not used
        people.person_id(con, "John Smith", pid=people.person_id(con, "Jane Doe"))
    # a family member who sees only a2 gets a2; one who sees neither gets someone new, added by them
    assert people.person_id(con, "John Smith", among={a2}) == a2
    a3 = people.person_id(con, "John Smith", among=set(), added_by=None)
    assert a3 not in (a1, a2)
    with pytest.raises(people.Problem):
        people.person_id(con, "  ")


def test_rename_merges_only_when_asked_and_unambiguous(con):
    lukas, lucas = people.person_id(con, "lukas"), people.person_id(con, "Lucas")
    assert people.rename(con, lukas, "Lucas", merge_same=False) == lukas  # a family member's rename never merges
    assert con.execute("SELECT count(*) FROM person WHERE name = 'Lucas'").fetchone()[0] == 2
    other = people.person_id(con, "Other")
    assert people.rename(con, other, "Lucas") == other  # two Lucases already: which one is not known, so no merge
    solo = people.person_id(con, "Solo")
    target = people.person_id(con, "Target")
    assert people.rename(con, solo, "target") == target  # the admin's old way: one other with the name, merged


def test_name_boxes_send_who_was_picked(con, cfg, library):
    scan(con, cfg)
    ingest(con, cfg)
    image = con.execute("SELECT image_id FROM file WHERE path = ?",
                        (rel("2016", "IMG_20160811_182239.jpg"),)).fetchone()[0]
    first = con.execute("INSERT INTO person(name) VALUES ('Sam Lee')").lastrowid
    second = con.execute("INSERT INTO person(name) VALUES ('Sam Lee')").lastrowid
    con.commit()
    client = TestClient(webapp.create_app(cfg), client=LAN)
    names = client.get("/api/people/names").json()
    assert sorted(p["id"] for p in names if p["name"] == "Sam Lee") == [first, second]
    r = client.post(f"/api/images/{image}/people", json={"name": "Sam Lee"}, headers=APP)
    assert r.status_code == 400 and "pick the one you mean" in r.json()["detail"]
    r = client.post(f"/api/images/{image}/people", json={"name": "Sam Lee", "name_id": second}, headers=APP)
    assert r.json()["person_id"] == second
    assert [p["id"] for p in tagging.linked_people(con, image)] == [second]
