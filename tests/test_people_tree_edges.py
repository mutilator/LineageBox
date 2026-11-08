"""Edge cases of people, faces and the family tree, found checking the new code before it went to a live family:
long notes and odd files in GEDCOM, dates no calendar has, how a child belongs to a
parent changed from the page, a group named and taken back, kin words across adoption, fostering, step and former
families, and the family book with loose dates, people without photos and a branch at its limit."""
from collections import defaultdict

import pytest
from fastapi.testclient import TestClient

from lineagebox import book, catalog, events, family, gedcom, gedcom_import, life, people
from lineagebox.web import app as webapp
from tests.conftest import LAN
from tests.test_access_web import signed_in, site  # noqa: F401 - site is a fixture
from tests.test_face_checks import A, face, like, photo

APP = {"X-Requested-With": "lineagebox"}


# ---- GEDCOM -------------------------------------------------------------------------------------------------------

def test_a_long_note_comes_back_whole_from_gedcom_551(con, cfg):
    """5.5.1 splits long lines into CONC lines. Split after a space, the line lost it (the space ends the line, and
    other programs strip it too); split between the two @ of an escaped @, the @ came back doubled."""
    pid = people.person_id(con, "Ruth Baker")
    note = (("word " * 40) + "x" * 199 + "@ written by ruth@example.com " + "and more words " * 30).strip()
    con.execute("UPDATE person SET notes = ?, biography = ? WHERE id = ?", (note, "One line.\nAnother.", pid))
    con.commit()
    text = gedcom.export(con, cfg, "5.5.1")[0]
    assert max(len(line) for line in text.decode("utf-8-sig").splitlines()) <= 255  # GEDCOM 5.5.1's longest line
    indi = next(r for r in gedcom.parse(text) if r.xref == f"@I{pid}@")
    assert [n.payload for n in indi.all("NOTE")] == ["One line.\nAnother.", note]
    seven = next(r for r in gedcom.parse(gedcom.export(con, cfg, "7.0")[0]) if r.xref == f"@I{pid}@")
    assert seven.all("NOTE")[1].payload == note


def test_a_utf16_gedcom_file_is_read(con):
    """Some programs save GEDCOM 5.5.1 as UNICODE (UTF-16, with its byte order mark)."""
    ged = "0 HEAD\r\n1 GEDC\r\n2 VERS 5.5.1\r\n1 CHAR UNICODE\r\n0 @I1@ INDI\r\n1 NAME Zoë /Müller/\r\n0 TRLR\r\n"
    for encoding in ("utf-16", "utf-16-be"):
        data = ged.encode(encoding) if encoding == "utf-16" else b"\xfe\xff" + ged.encode(encoding)
        assert gedcom_import.read(data)["people"]["@I1@"]["names"][0]["full"] == "Zoë Müller"


def test_dates_no_calendar_has_are_kept_as_written(con, cfg):
    """A tree may hold a placeholder year (0000, 9999): kept as the file wrote it, but sorting nowhere, so a page
    that works out ages from it (the life by age, Ages to check) doesn't break on a year Python has no day for."""
    ged = """0 HEAD
1 GEDC
2 VERS 5.5.1
0 @I1@ INDI
1 NAME Zero /Year/
1 BIRT
2 DATE 0000
0 @I2@ INDI
1 NAME Far /Future/
1 BIRT
2 DATE 1990
1 DEAT
2 DATE 31 DEC 9999
0 @I3@ INDI
1 NAME Long /Ago/
1 BIRT
2 DATE 12 MAR 1700
0 TRLR
"""
    gedcom_import.apply(con, gedcom_import.start(con, "tree.ged", ged.encode()))
    dates = {(r[0], r[1]): (r[2], r[3]) for r in con.execute(
        "SELECT p.name, e.tag, e.date, e.sort_date FROM event e JOIN person p ON p.id = e.person_id")}
    assert dates[("Zero Year", "BIRT")] == ("0000", None)
    assert dates[("Far Future", "DEAT")] == ("31 DEC 9999", None)
    assert dates[("Far Future", "BIRT")] == ("1990", "1990") and dates[("Long Ago", "BIRT")][1] == "1700-03-12"
    client = TestClient(webapp.create_app(cfg), client=LAN)
    for pid, in con.execute("SELECT id FROM person"):
        assert client.get(f"/person/{pid}").status_code == 200
        life.life(con, pid)


# ---- how a child belongs to a parent, from their page ---------------------------------------------------------------

def test_a_child_said_to_be_adopted_from_the_page(con, cfg):
    paul, gina, zed = (people.person_id(con, n) for n in ("Paul Miller", "Gina Miller", "Zed Baker"))
    family.link(con, paul, "daughter", gina)
    con.commit()
    client = TestClient(webapp.create_app(cfg), client=LAN)
    url = "/api/family/kind"
    assert client.post(url, json={"child": gina, "parent": paul, "kind": "adopted"}).status_code == 403  # no header
    r = client.post(url, json={"child": gina, "parent": paul, "kind": "adopted"}, headers=APP)
    assert r.status_code == 200 and r.json()["undo"]
    assert family.load(con).how(gina, paul) == "adopted"
    assert family.relations_to(family.load(con), paul)[gina].label == "adopted daughter"
    assert "Gina Miller was adopted by Paul Miller" in con.execute("SELECT summary FROM change_log").fetchone()[0]
    assert client.post(f"/api/changes/{r.json()['undo']}/undo", headers=APP).status_code == 200
    assert family.load(con).how(gina, paul) == "birth"
    assert client.post(url, json={"child": gina, "parent": zed, "kind": "adopted"}, headers=APP).status_code == 404
    assert client.post(url, json={"child": gina, "parent": 999, "kind": "foster"}, headers=APP).status_code == 404
    bad = client.post(url, json={"child": gina, "parent": paul, "kind": "stolen"}, headers=APP)
    assert bad.status_code == 400 and family.load(con).how(gina, paul) == "birth"


def test_a_member_says_how_a_child_belongs_only_in_their_part_of_the_family(site, cfg, con):
    p = site["people"]
    cora = signed_in(cfg, con, site["who"]["Cora"])
    r = cora.post("/api/family/kind", json={"child": p["Bella"], "parent": p["Bea"], "kind": "foster"}, headers=APP)
    assert r.status_code == 200 and family.load(con).how(p["Bella"], p["Bea"]) == "foster"
    # Tina's father Hank is outside Cora's part of the family
    r = cora.post("/api/family/kind", json={"child": p["Tina"], "parent": p["Hank"], "kind": "adopted"}, headers=APP)
    assert r.status_code == 404 and family.load(con).how(p["Tina"], p["Hank"]) == "birth"


# ---- a group of faces named, and taken back ---------------------------------------------------------------------------

def test_a_group_named_at_once_can_be_undone_at_once(con, cfg):
    """Naming a group names every face in it: the answer carries the Undo, as every other naming does."""
    ruth = people.person_id(con, "Ruth Baker")
    ids = [face(con, photo(con, n, "2012-05-01T10:00:00"), like(A), cluster=7) for n in (1, 2, 3)]
    con.commit()
    client = TestClient(webapp.create_app(cfg), client=LAN)
    r = client.post("/api/clusters/7/name?" + "&".join(f"face={f}" for f in ids),
                    json={"name": "Ruth Baker", "name_id": ruth}, headers=APP)
    assert r.status_code == 200 and r.json()["person_id"] == ruth and r.json()["undo"]
    assert {f: catalog.face(con, f)["assigned_by"] for f in ids} == dict.fromkeys(ids, "user")
    assert client.post(f"/api/changes/{r.json()['undo']}/undo", headers=APP).status_code == 200
    faces = [catalog.face(con, f) for f in ids]
    assert [(f["person_id"], f["assigned_by"], f["cluster_id"]) for f in faces] == [(None, None, 7)] * 3


# ---- kin words ----------------------------------------------------------------------------------------------------

def _graph(people_sexes, parents, partners, pedi):
    rows = {n: {"id": n, "name": name, "sex": sex, "deceased": 0, "birth_date": None}
            for n, (name, sex) in enumerate(people_sexes, 1)}
    by = {r["name"]: n for n, r in rows.items()}
    ps, cs, pairs = defaultdict(set), defaultdict(set), defaultdict(dict)
    for child, parent in parents:
        ps[by[child]].add(by[parent])
        cs[by[parent]].add(by[child])
    for a, b, current in partners:
        pairs[by[a]][by[b]] = pairs[by[b]][by[a]] = (current, True)
    g = family.Graph(rows, dict(ps), dict(cs), {}, dict(pairs), [],
                     pedi={(by[c], by[p]): how for c, p, how in pedi})
    return g, by


def test_kin_words_across_adoption_fostering_step_and_former_families():
    who = [("Ego", "male"), ("Dad", "male"), ("Mom", "female"), ("Stepmom", "female"), ("Stepbro", "male"),
           ("Wife", "female"), ("Son", "male"), ("Adopted", "male"), ("BirthMom", "female"), ("Fostered", "male"),
           ("WifeKid", "female"), ("Ex", "female"), ("ExMom", "female"), ("Grandpa", "male"), ("Aunt", "female"),
           ("Cousin", "female"), ("CousinKid", "male"), ("DadsFoster", "male"), ("StepmomFoster", "male")]
    parents = [("Ego", "Dad"), ("Ego", "Mom"), ("Stepbro", "Stepmom"), ("Son", "Ego"), ("Son", "Wife"),
               ("Adopted", "Ego"), ("Adopted", "Wife"), ("Adopted", "BirthMom"), ("Fostered", "Ego"),
               ("WifeKid", "Wife"), ("Ex", "ExMom"), ("Dad", "Grandpa"), ("Aunt", "Grandpa"), ("Cousin", "Aunt"),
               ("CousinKid", "Cousin"), ("DadsFoster", "Dad"), ("StepmomFoster", "Stepmom")]
    partners = [("Dad", "Mom", False), ("Dad", "Stepmom", True), ("Ego", "Wife", True), ("Ego", "Ex", False)]
    pedi = [("Adopted", "Ego", "adopted"), ("Adopted", "Wife", "adopted"), ("Fostered", "Ego", "foster"),
            ("DadsFoster", "Dad", "foster"), ("StepmomFoster", "Stepmom", "foster")]
    g, by = _graph(who, parents, partners, pedi)
    words = lambda ego: {name: k.label for name, n in by.items() if (k := family.relations_to(g, by[ego]).get(n))}
    ego = words("Ego")
    assert (ego["Adopted"], ego["Fostered"], ego["DadsFoster"]) == ("adopted son", "foster son", "foster brother")
    assert (ego["Stepbro"], ego["Stepmom"], ego["WifeKid"]) == ("stepbrother", "stepmother", "stepdaughter")
    assert ego["CousinKid"] == "cousin once removed" and ego["ExMom"] == "former mother-in-law"
    assert ego.get("StepmomFoster") != "stepbrother"  # a stepparent's foster child isn't step-family
    adopted = words("Adopted")
    assert (adopted["Ego"], adopted["Wife"], adopted["BirthMom"]) == ("adoptive father", "adoptive mother",
                                                                      "birth mother")
    assert adopted["Son"] == "brother" and adopted["Grandpa"] == "great-grandfather"  # adoption makes family
    assert adopted["Fostered"] == "foster brother" and adopted["Ex"] == "former stepmother"
    fostered = words("Fostered")
    assert fostered["Ego"] == "foster father" and fostered["Dad"] == "foster grandfather"
    assert fostered["Son"] == "foster brother" and fostered["Cousin"] == "foster cousin once removed"
    assert words("BirthMom")["Adopted"] == "son"


# ---- the family book ------------------------------------------------------------------------------------------------

def test_a_book_of_someone_known_only_loosely(con, cfg):
    """Dates as loose as "about 1950" and "before 2001", no photos, no family: the book still reads."""
    ruth, alone = people.person_id(con, "Ruth Baker"), people.person_id(con, "Nobody Known")
    events.save(con, None, tag="BIRT", person_id=ruth, date_text="about 1950", place="Salem")
    events.save(con, None, tag="DEAT", person_id=ruth, date_text="before 2001")
    events.save(con, None, tag="OCCU", person_id=ruth, kind="Teacher")
    con.commit()
    made = book.book(con, ruth)
    (chapter,) = made["chapters"]
    assert chapter["years"] == "1950–2001" and chapter["photos"] == [] and chapter["portrait"] is None
    assert [e["when"] for e in chapter["life"]] == ["about 1950", "before 2001", ""]
    assert book.years(con, alone) == ""
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get(f"/book/person/{ruth}").text
    assert "1950–2001" in page and "about 1950" in page and "Teacher" in page
    assert "Nothing more is known of Nobody Known here yet." in client.get(f"/book/person/{alone}").text
    assert client.get(f"/book/branch/{alone}").status_code == 200  # a branch of one
    assert client.get("/book/person/999").status_code == 404
    # a death with no date says only that they died
    events.set_deceased(con, alone, True)
    con.commit()
    assert book.years(con, alone) == ""


def test_a_branch_book_at_its_limit(con, cfg, monkeypatch):
    """A generation at a time, the nearest first; the rest are counted, not shown."""
    names = ["Old Miller", "Ann Miller", "Ben Miller", "Cal Miller", "Dot Miller", "Eve Miller"]
    ids = {n: people.person_id(con, n) for n in names}
    for parent, child, born in (("Old Miller", "Ann Miller", "1952-03-01"), ("Old Miller", "Ben Miller", "1950-05-05"),
                                ("Ann Miller", "Cal Miller", "1975-01-01"), ("Ben Miller", "Dot Miller", "1979-01-01"),
                                ("Cal Miller", "Eve Miller", "2001-01-01")):
        family.link(con, ids[parent], "son", ids[child])
        events.save(con, None, tag="BIRT", person_id=ids[child], date_text=born)
    con.commit()
    g = family.load(con)
    assert [(g.name(p), d) for p, d in book.descendants(g, ids["Old Miller"], most=4)] == [
        ("Old Miller", 0), ("Ben Miller", 1), ("Ann Miller", 1), ("Cal Miller", 2)]  # by birth in a generation
    monkeypatch.setattr(book.descendants, "__defaults__", (4,))
    made = book.book(con, ids["Old Miller"], branch=True)
    assert len(made["chapters"]) == 4 and made["left_out"] == 2
    page = TestClient(webapp.create_app(cfg), client=LAN).get(f"/book/branch/{ids['Old Miller']}").text
    assert "2 more descendants would make the book too long" in page and "in 3 generations" in page


# ---- dates for photos without one of their own ------------------------------------------------------------------------

def test_a_scans_date_from_its_folder_and_who_is_in_it(con):
    """Each measured rule (date_hints): the folder's photos within a month or two years, the two beside it within a
    year, a folder that says before someone in it was born, and who is in it, alone or contradicting each other."""
    from lineagebox import date_hints
    from tests.test_face_checks import born
    # first in its folder by name (nothing before it): the others, within a month, give the month
    for n, day in ((2, "2012-06-02"), (3, "2012-06-10"), (4, "2012-06-20")):
        photo(con, n, f"{day}T10:00:00", path=f"f1/IMG_{n:04d}.jpg")
    first = photo(con, 1, "2020-01-01T00:00:00", "mtime", path="f1/IMG_0001.jpg")
    assert date_hints.suggest(con, first)["text"] == "June 2012"
    # the two beside it, months apart in one year: about that year
    photo(con, 10, "2012-01-10T10:00:00", folder=2, path="f2/a.jpg")
    between = photo(con, 11, "2020-01-01T00:00:00", "mtime", folder=2, path="f2/b.jpg")
    photo(con, 12, "2012-09-01T10:00:00", folder=2, path="f2/c.jpg")
    photo(con, 13, "1990-01-01T10:00:00", folder=2, path="f2/d.jpg")
    hint = date_hints.suggest(con, between)
    assert hint["text"] == "about 2012" and "taken in 2012" in hint["why"][0]
    # the folder's photos within two years, nothing beside it
    for n, day in ((20, "2010-03-01"), (21, "2011-01-01"), (22, "2011-08-01")):
        photo(con, n, f"{day}T10:00:00", folder=3, path=f"f3/{n}.jpg")
    alone = photo(con, 23, "2020-01-01T00:00:00", "folder", folder=3, path="f3/00.jpg")
    hint = date_hints.suggest(con, alone)
    assert hint["text"] == "about 2011" and "taken in 2010 and 2011" in hint["why"][0]
    # the folder says 2011, but someone in it was born in 2014: not that; who is in it bounds it instead
    kid, gran = people.person_id(con, "Owen Miller"), people.person_id(con, "Ruth Baker")
    born(con, kid, "2014-07-01")
    face(con, alone, like(A), kid, "user")
    con.commit()
    assert date_hints.suggest(con, alone) == {"text": None, "label": "Not before 2014",
                                              "why": ["Owen Miller, in it, was born in 2014."]}
    # and someone who died in 2016 is in it too: between the two
    con.execute("INSERT INTO event(person_id, tag, date, sort_date) VALUES (?, 'DEAT', '2016', '2016')", (gran,))
    face(con, alone, like(A), gran, "user", x=0.5)
    con.commit()
    hint = date_hints.suggest(con, alone)
    assert hint["text"] == "between 2014 and 2016" and hint["label"] == "Probably between 2014 and 2016"
    assert hint["why"] == ["Owen Miller, in it, was born in 2014.", "Ruth Baker, in it, died in 2016."]
    # a death before a birth: a wrong name or date, so who is in it says nothing, and the folder is heard again
    con.execute("UPDATE event SET date = '2013', sort_date = '2013' WHERE person_id = ? AND tag = 'DEAT'", (gran,))
    con.commit()
    hint = date_hints.suggest(con, alone)
    assert hint["text"] == "about 2011" and len(hint["why"]) == 1


def test_ages_to_check_with_a_birth_known_to_the_month_and_a_date_set_by_hand(con):
    from lineagebox import face_ages, tagging
    from tests.test_face_checks import born
    kid = people.person_id(con, "Owen Miller")
    born(con, kid, "2010-06")
    early = face(con, photo(con, 1, "2012-01-01T00:00:00", "mtime"), like(A), kid, "user")
    later = face(con, photo(con, 2, "2012-01-01T00:00:00", "mtime"), like(A), kid, "user")
    tagging.set_date(con, [1], "May 2010")  # set by hand: all of it before June 2010
    tagging.set_date(con, [2], "2010")  # all of 2010: he may be in it
    con.commit()
    assert [(c["face_id"], c["why"]) for c in face_ages.age_checks(con)] == [(early, "born after")]
    assert later not in {c["face_id"] for c in face_ages.age_checks(con)}
    assert face_ages._last("2010-06") == face_ages._last("2010-06-30") and face_ages._first("2010").month == 1


# ---- a person's details: what can't be found -------------------------------------------------------------------------

def test_a_persons_details_refused_when_not_there(con, cfg):
    ruth, paul = people.person_id(con, "Ruth Baker"), people.person_id(con, "Paul Miller")
    pauls = face(con, photo(con, 1, "2012-05-01T10:00:00"), like(A), paul, "user")
    con.commit()
    client = TestClient(webapp.create_app(cfg), client=LAN)
    post = lambda url, body: client.post(url, json=body, headers=APP)
    assert post(f"/api/people/{ruth}/story", {}).json() == {}  # nothing sent, nothing changed
    assert post("/api/people/999/story", {"biography": "x"}).status_code == 404
    assert post(f"/api/people/{ruth}/portrait", {"face_id": pauls}).status_code == 404  # not her face
    assert post("/api/people/999/portrait", {"face_id": None}).status_code == 404
    assert post(f"/api/people/{ruth}/sources", {"source_id": 999, "target": f"p:{ruth}"}).status_code == 404
    assert post("/api/people/999/sources", {"title": "A book", "target": "p:999"}).status_code == 404
    assert post(f"/api/people/{ruth}/sources", {"title": "", "target": f"p:{ruth}"}).status_code == 400
    assert post(f"/api/people/{ruth}/sources", {"title": "A book", "target": "x:1"}).status_code == 404
    assert post("/api/sources/999", {"title": "A book"}).status_code == 404
    assert post("/api/source-citations/999/delete", {}).status_code == 404
    assert con.execute("SELECT count(*) FROM source").fetchone()[0] == 0


# ---- what a folder means --------------------------------------------------------------------------------------------

def test_a_folders_date_set_and_taken_off_by_hand(con, monkeypatch):
    """Only photos dated by their file's time or a folder take its date; taken off, they go back to their file's time.
    A camera's date and one set by hand stay whatever the folder says."""
    from lineagebox import dates, folders, search, tagging
    file_time, camera, by_hand = (photo(con, 1, "2019-05-05T12:00:00", "mtime"),
                                  photo(con, 2, "2012-06-03T10:00:00"), photo(con, 3, "2019-05-05T12:00:00", "mtime"))
    tagging.set_date(con, [by_hand], "1987")
    con.commit()
    taken = lambda i: tuple(con.execute("SELECT taken_at, taken_at_source FROM image WHERE id = ?", (i,)).fetchone())
    stays = taken(camera), taken(by_hand)

    def mean(when="", **said):
        folders.set_meaning(con, 1, said.get("place", ""), said.get("event", ""), said.get("people", ""), when)
        con.commit()
        return folders.reflow(con, 1)

    assert mean("June 1990") == 3 and taken(file_time) == ("1990-06-01T00:00:00", "folder")
    assert mean("1990-06-14") and taken(file_time) == ("1990-06-14T00:00:00", "folder")
    assert mean("1990") and taken(file_time) == ("1990-01-01T00:00:00", "folder")
    assert mean() and taken(file_time) == (dates.from_mtime(1), "mtime")  # its file's time again
    assert (taken(camera), taken(by_hand)) == stays
    for when in ("summer 1990", "the 1980s", "not a date"):
        with pytest.raises(folders.Problem):
            folders.set_meaning(con, 1, "", "", "", when)
    kind = lambda: con.execute("SELECT kind FROM folder WHERE id = 1").fetchone()[0]
    for said, expected in (({"place": "Salem"}, "place"), ({"people": "Ann, Ben"}, "person"), ({}, "misc"),
                           ({"event": "Picnic", "place": "Salem"}, "event")):
        mean(**said)
        assert kind() == expected, said
    mean("1991")
    assert kind() == "date"
    con.execute("UPDATE folder SET kind = 'camera_or_device_dump' WHERE id = 1")
    mean()
    assert kind() == "camera_or_device_dump"  # nothing said: still a camera's folder, whose context is left out
    # a folder with more photos than are searched again at once: left to the worker's next pass
    search.rebuild(con)
    monkeypatch.setattr(folders, "REINDEX_AT_ONCE", 1)
    assert mean(place="Lake Tahoe") == 3
    assert con.execute("SELECT count(*) FROM search_fts").fetchone()[0] == 0
    search.index_new(con)
    assert {r["id"] for r in search.query(con, "tahoe")} == {file_time, camera, by_hand}
    assert folders.images_under(con, 999) == []


def test_face_check_answers_for_what_isnt_there(con, cfg):
    client = TestClient(webapp.create_app(cfg), client=LAN)
    assert client.post("/api/faces/999/age-ok", headers=APP).status_code == 404
    assert client.post("/api/images/999/faces-found", headers=APP).status_code == 404