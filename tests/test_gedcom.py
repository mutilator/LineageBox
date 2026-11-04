"""GEDCOM: carrying the family over (migration 009), dates, events and names, and the export (7.0, 5.5.1, GEDZIP)."""
import io
import sqlite3
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from lineagebox import db, events, gedcom, people
from lineagebox.web import app as webapp
from tests.conftest import LAN

APP = {"X-Requested-With": "lineagebox"}
PEOPLE = ["Walter Baker", "Edna Baker", "Mike Baker", "Linda Baker", "Katherine Miller", "Rosa Baker",
          "Daniel Miller", "Gary Miller", "Helen Porter", "Diane Miller", "Kevin Whitfield", "Anna Van Dyke",
          "Rex", "Owen Miller", "Jake"]


@pytest.fixture
def fam(cfg):
    """A family entered before migration 009, as an older catalog has it: grandparents and step-siblings entered as plain
    relationships, a sister with no parents, couples of unknown sex, a pet and a friend."""
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(cfg.db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    db.migrate(con, cfg, upto=8)
    ids = {n: con.execute("INSERT INTO person(name) VALUES (?)", (n,)).lastrowid for n in PEOPLE}
    for name, sex in (("Walter Baker", "male"), ("Edna Baker", "female"), ("Rosa Baker", "female"),
                      ("Gary Miller", "male"), ("Owen Miller", "male")):
        con.execute("UPDATE person SET sex = ? WHERE id = ?", (sex, ids[name]))
    con.execute("UPDATE person SET birth_date = '2011-06-15' WHERE id = ?", (ids["Owen Miller"],))
    con.execute("UPDATE person SET deceased = 1 WHERE id = ?", (ids["Walter Baker"],))
    for child, parents in (("Mike Baker", ("Walter Baker", "Edna Baker")), ("Katherine Miller", ("Mike Baker", "Linda Baker")),
                           ("Daniel Miller", ("Gary Miller", "Helen Porter")), ("Kevin Whitfield", ("Diane Miller",)),
                           ("Anna Van Dyke", ("Diane Miller",)), ("Owen Miller", ("Daniel Miller", "Katherine Miller"))):
        for p in parents:
            con.execute("INSERT INTO parent(child_id, parent_id) VALUES (?, ?)", (ids[child], ids[p]))
    a, b = sorted((ids["Rosa Baker"], ids["Katherine Miller"]))
    con.execute("INSERT INTO sibling(a, b) VALUES (?, ?)", (a, b))
    for x, y, current in (("Walter Baker", "Edna Baker", 1), ("Mike Baker", "Linda Baker", 1),
                          ("Daniel Miller", "Katherine Miller", 1), ("Gary Miller", "Helen Porter", 0),
                          ("Gary Miller", "Diane Miller", 1)):
        a, b = sorted((ids[x], ids[y]))
        con.execute("INSERT INTO partner(a, b, current, married) VALUES (?, ?, ?, 1)", (a, b, current))
    for who, kind, of in (("Walter Baker", "grandfather", "Rosa Baker"), ("Edna Baker", "grandmother", "Katherine Miller"),
                          ("Kevin Whitfield", "step-brother", "Daniel Miller"), ("Anna Van Dyke", "step-sister", "Daniel Miller"),
                          ("Rex", "pet", "Daniel Miller"), ("Jake", "friend", "Owen Miller")):
        con.execute("INSERT INTO relation(person_id, of_id, kind) VALUES (?, ?, ?)", (ids[who], ids[of], kind))
    # Daniel's faces look male: that decides which of Daniel and Katherine is the husband
    image = con.execute("INSERT INTO image(sha256, format, width, height, stage) VALUES (?, 'jpeg', 400, 300, 1)",
                        ("ab" * 32,)).lastrowid
    con.execute("INSERT INTO file(path, folder_id, image_id, size, mtime, last_seen) VALUES ('lib/a.jpg', "
                "(SELECT id FROM folder LIMIT 1), ?, 1, 1, '')", (image,)) if con.execute(
        "SELECT 1 FROM folder").fetchone() else None
    for _ in range(10):
        con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, embedding, person_id, assigned_by, est_sex, quality)
                       VALUES (?, 0.4, 0.3, 0.6, 0.6, ?, ?, 'user', 'M', 25)""",
                    (image, np.zeros(512, np.float32).tobytes(), ids["Daniel Miller"]))
    con.commit()
    db.migrate(con, cfg)
    yield con, ids
    con.close()


def test_the_family_is_carried_over(fam):
    con, ids = fam
    one = lambda sql, *a: con.execute(sql, a).fetchone()
    assert one("PRAGMA user_version")[0] >= 9
    assert one("SELECT count(*) FROM person WHERE uid IS NULL")[0] == 0
    assert one("SELECT count(DISTINCT uid) FROM person")[0] == len(PEOPLE)
    assert one("SELECT count(*) FROM partner WHERE uid IS NULL")[0] == 0
    assert {r[0] for r in con.execute("SELECT DISTINCT pedi FROM parent")} == {"birth"}
    # Rosa, Katherine's sister, has Katherine's parents now; the sibling link isn't needed
    rosa = {r[0] for r in con.execute("SELECT parent_id FROM parent WHERE child_id = ?", (ids["Rosa Baker"],))}
    assert rosa == {ids["Mike Baker"], ids["Linda Baker"]} and one("SELECT count(*) FROM sibling")[0] == 0
    # family words that the links say anyway are gone; the pet and the friend stay
    assert sorted(r[0] for r in con.execute("SELECT kind FROM relation")) == ["friend", "pet"]
    sex = dict(con.execute("SELECT name, sex FROM person"))
    assert (sex["Kevin Whitfield"], sex["Anna Van Dyke"]) == ("male", "female")   # step-brother, step-sister
    assert (sex["Helen Porter"], sex["Diane Miller"]) == ("female", "female")     # Gary's wives
    assert (sex["Daniel Miller"], sex["Katherine Miller"]) == ("male", "female")  # the faces decided
    assert (sex["Mike Baker"], sex["Linda Baker"]) == (None, None)                # nothing to go by
    assert sex["Rex"] is None
    rows = {r["name"]: r for r in con.execute("SELECT * FROM person")}
    assert (rows["Anna Van Dyke"]["given_name"], rows["Anna Van Dyke"]["surname"]) == ("Anna", "Van Dyke")
    assert (rows["Rex"]["given_name"], rows["Rex"]["surname"]) == ("Rex", None)
    assert rows["Daniel Miller"]["name_type"] == "married" and rows["Kevin Whitfield"]["name_type"] == "birth"
    assert not rows["Daniel Miller"]["names_checked"]
    born = one("SELECT date, sort_date FROM event WHERE person_id = ? AND tag = 'BIRT'", ids["Owen Miller"])
    assert tuple(born) == ("15 JUN 2011", "2011-06-15") and rows["Owen Miller"]["birth_date"] == "2011-06-15"
    assert one("SELECT count(*) FROM event WHERE person_id = ? AND tag = 'DEAT'", ids["Walter Baker"])[0] == 1


@pytest.mark.parametrize("typed, gedcom, sort", [
    ("1984-06-12", "12 JUN 1984", "1984-06-12"), ("June 12, 1984", "12 JUN 1984", "1984-06-12"),
    ("12 Jun 1984", "12 JUN 1984", "1984-06-12"), ("6/12/1984", "12 JUN 1984", "1984-06-12"),
    ("Jun 1984", "JUN 1984", "1984-06"), ("1984-06", "JUN 1984", "1984-06"), ("1984", "1984", "1984"),
    ("about 1950", "ABT 1950", "1950"), ("ABT 1950", "ABT 1950", "1950"), ("before June 1960", "BEF JUN 1960", "1960-06"),
    ("between 1950 and 1955", "BET 1950 AND 1955", "1950"), ("1950-1955", "BET 1950 AND 1955", "1950"),
    ("from 1990 to 1995", "FROM 1990 TO 1995", "1990")])
def test_dates_as_people_type_them(typed, gedcom, sort):
    d = events.parse_date(typed)
    assert (d.gedcom, d.sort) == (gedcom, sort)
    assert events.parse_date(d.gedcom) == d  # what is stored reads back the same


def test_dates_for_people():
    assert events.show_date("12 JUN 1984") == "June 12, 1984"
    assert events.show_date("ABT 1950") == "about 1950" and events.show_date("BET 1950 AND 1955") == "between 1950 and 1955"
    for bad in ("1984-02-30", "soon", "13/45/2000"):
        with pytest.raises(events.Problem):
            events.parse_date(bad)


def _records(text):
    recs = gedcom.parse(text)
    by_xref = {r.xref: r for r in recs if r.xref}
    indi = {r.value("NAME"): r for r in recs if r.tag == "INDI"}
    return recs, by_xref, indi


def test_export_gedcom_7(fam, cfg):
    con, ids = fam
    daniel, katherine = ids["Daniel Miller"], ids["Katherine Miller"]
    events.save(con, None, tag="MARR", couple=(daniel, katherine), date_text="September 22 2001",
                place="Salem, Oregon, USA")
    con.commit()
    data, name = gedcom.export(con, cfg)
    assert name.endswith(".ged") and data.startswith("﻿0 HEAD".encode())
    recs, by_xref, indi = _records(data)
    head = recs[0]
    assert head.first("GEDC").value("VERS") == "7.0" and head.first("SCHMA").value("TAG").startswith("_PET urn:uuid:")
    assert recs[-1].tag == "TRLR"
    s = indi["Daniel /Miller/"]
    name_node = s.first("NAME")
    assert (name_node.value("TYPE"), name_node.value("GIVN"), name_node.value("SURN")) == ("MARRIED", "Daniel", "Miller")
    assert s.value("SEX") == "M" and len(s.value("UID")) == 36
    own = by_xref[s.value("FAMS")]
    assert own.value("HUSB") == f"@I{daniel}@" and own.value("WIFE") == f"@I{katherine}@"
    assert own.value("CHIL") == f"@I{ids['Owen Miller']}@"
    marr = own.first("MARR")
    assert (marr.value("DATE"), marr.value("PLAC"), marr.payload) == ("22 SEP 2001", "Salem, Oregon, USA", "")
    parents = by_xref[s.value("FAMC")]
    assert s.first("FAMC").value("PEDI") == "BIRTH" and parents.value("DIV") == "Y"  # Gary and Helen, divorced
    assert by_xref[indi["Rosa /Baker/"].value("FAMC")] is by_xref[indi["Katherine /Miller/"].value("FAMC")]
    assert indi["Owen /Miller/"].first("BIRT").value("DATE") == "15 JUN 2011"
    assert indi["Walter /Baker/"].value("DEAT") == "Y"
    pet = indi["Rex"]
    assert pet.value("_PET") == "Y" and pet.value("SEX") == "U"
    asso = s.first("ASSO")
    assert asso.payload == f"@I{ids['Rex']}@" and asso.value("ROLE") == "OTHER" and asso.first("ROLE").value("PHRASE") == "Pet"
    assert indi["Owen /Miller/"].first("ASSO").value("ROLE") == "FRIEND"  # Jake
    assert all(r.value("UID") for r in recs if r.tag in ("INDI", "FAM"))


def test_export_gedcom_551_and_privacy(fam, cfg):
    con, ids = fam
    data, _ = gedcom.export(con, cfg, "5.5.1", private_living=True)
    recs, by_xref, indi = _records(data)
    head = recs[0]
    assert head.first("GEDC").value("VERS") == "5.5.1" and head.value("CHAR") == "UTF-8"
    assert by_xref[head.value("SUBM")].tag == "SUBM"
    walter = indi["Walter /Baker/"]  # died: nothing private
    assert walter.value("_UID") and not walter.value("UID") and walter.value("DEAT") == "Y"
    living = indi["Living /Miller/"]
    assert living.value("RESN") == "privacy" and not living.first("BIRT") and not living.first("ASSO")
    assert not any(n.first("BIRT") for n in indi.values() if n.value("RESN"))
    assert "Owen" not in data.decode() and "Daniel" not in data.decode()


def test_export_with_portraits(fam, cfg):
    con, ids = fam
    sha = "ab" * 32
    path = cfg.preview_path(sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 300), "white").save(path, "JPEG")
    data, name = gedcom.export(con, cfg, photos=True)
    assert name.endswith(".gdz")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert sorted(z.namelist()) == ["gedcom.ged", f"media/{sha}.jpg"]
        recs, by_xref, indi = _records(z.read("gedcom.ged"))
    pointer = indi["Daniel /Miller/"].first("OBJE")
    crop = pointer.first("CROP")
    assert {t: int(crop.value(t)) for t in ("TOP", "LEFT", "HEIGHT", "WIDTH")} == {"TOP": 54, "LEFT": 119, "HEIGHT": 162, "WIDTH": 162}
    obje = by_xref[pointer.payload]
    assert obje.value("FILE") == f"media/{sha}.jpg" and obje.first("FILE").value("FORM") == "image/jpeg"
    # from the web app, the same zip, sent as it is made
    r = TestClient(webapp.create_app(cfg), client=LAN).get("/family/export?photos=true")
    assert r.status_code == 200 and r.headers["cache-control"] == "private"
    assert ".gdz" in r.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert z.namelist() == ["gedcom.ged", f"media/{sha}.jpg"] and z.testzip() is None
        assert _records(z.read("gedcom.ged"))[2].keys() == indi.keys()


def test_events_and_names_on_the_pages(fam, cfg):
    con, ids = fam
    daniel, katherine = ids["Daniel Miller"], ids["Katherine Miller"]
    c = TestClient(webapp.create_app(cfg), client=LAN)
    assert "Life events" in c.get(f"/person/{daniel}").text
    r = c.post(f"/api/people/{daniel}/events", json={"tag": "MARR", "partner": str(katherine),
                                                     "date": "September 22 2001", "place": "Salem"}, headers=APP)
    assert r.status_code == 200
    assert c.post(f"/api/people/{daniel}/events", json={"tag": "DIV", "date": "2001"}, headers=APP).status_code == 400
    page = c.get(f"/person/{katherine}").text
    assert "Married" in page and "September 22, 2001" in page and "Salem" in page  # the couple's: on both pages
    assert c.post(f"/api/people/{daniel}/details", json={"birth_date": "about 1974", "deceased": []},
                  headers=APP).status_code == 200
    born = con.execute("SELECT date FROM event WHERE person_id = ? AND tag = 'BIRT'", (daniel,)).fetchone()[0]
    assert born == "ABT 1974" and con.execute("SELECT birth_date FROM person WHERE id = ?", (daniel,)).fetchone()[0] is None
    assert c.post(f"/api/people/{daniel}/details", json={"birth_date": "soon", "deceased": []},
                  headers=APP).status_code == 400
    assert c.post(f"/api/people/{katherine}/names", json={"name": "Katherine Baker", "type": "birth"},
                  headers=APP).status_code == 200
    assert c.post(f"/api/people/{daniel}/name-parts", json={"given": "Daniel J", "surname": "Miller", "type": "birth"},
                  headers=APP).status_code == 200
    names = c.get("/family/names").text
    assert "Anna Van Dyke" in names and f'/api/people/{daniel}/name-parts' not in names  # checked now
    c.post("/api/family/names/confirm", headers=APP)
    assert con.execute("SELECT count(*) FROM person WHERE NOT names_checked").fetchone()[0] == 0
    r = c.get("/family/export?version=7.0")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    recs, by_xref, indi = _records(r.content)
    ch = indi["Katherine /Miller/"]
    assert [n.payload for n in ch.all("NAME")] == ["Katherine /Miller/", "Katherine /Baker/"]
    assert ch.all("NAME")[1].value("TYPE") == "BIRTH"


def test_merging_carries_events_over(fam):
    con, ids = fam
    typo = people.person_id(con, "Mike Bakker")
    events.save(con, None, tag="BIRT", person_id=typo, date_text="1950")
    a, b = sorted((typo, ids["Walter Baker"]))
    con.execute("INSERT INTO partner(a, b, current, married, uid) VALUES (?, ?, 0, 0, 'x')", (a, b))
    events.save(con, None, tag="EVEN", couple=(typo, ids["Walter Baker"]), kind="fishing trip", date_text="1980")
    people.merge(con, typo, ids["Mike Baker"])
    con.commit()
    born = con.execute("SELECT date FROM event WHERE person_id = ? AND tag = 'BIRT'", (ids["Mike Baker"],)).fetchone()
    assert born[0] == "1950"
    trip = con.execute("SELECT couple_a, couple_b FROM event WHERE type = 'fishing trip'").fetchone()
    assert sorted(trip) == sorted((ids["Mike Baker"], ids["Walter Baker"]))
