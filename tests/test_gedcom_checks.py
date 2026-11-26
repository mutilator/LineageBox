"""Checking a family tree before it comes in (gedcom_checks): a doubtful match isn't made by itself and, made anyway,
waits to be confirmed; people the names miss are found through their families; and each clash is settled the way the
person chose on the import page."""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from lineagebox import events, family, gedcom_checks, gedcom_import
from lineagebox.web import app as webapp
from tests.conftest import LAN

APP = {"X-Requested-With": "lineagebox"}


def person(con, name, sex=None, born=None, given=None, surname=None, other=None):
    pid = con.execute("INSERT INTO person(name, sex, given_name, surname) VALUES (?, ?, ?, ?)",
                      (name, sex, given, surname)).lastrowid
    if born:
        con.execute("INSERT INTO event(person_id, tag, date, sort_date) VALUES (?, 'BIRT', ?, ?)", (pid, str(born), str(born)))
    for n, kind in (other or {}).items():
        events.add_name(con, pid, n, kind)
    return pid


def parents(con, child, *ps):
    for p in ps:
        family.add_parent(con, child, p)


def couple(con, a, b, current=True):
    family.add_partner(con, min(a, b), max(a, b), current, "married")


def gedcom(*records) -> bytes:
    return ("0 HEAD\n1 GEDC\n2 VERS 5.5.1\n2 FORM LINEAGE-LINKED\n1 CHAR UTF-8\n" + "\n".join(records)
            + "\n0 TRLR\n").encode()


def indi(x, name, sex=None, born=None, died=None, nick=None, famc=(), fams=()):
    lines = [f"0 @{x}@ INDI", f"1 NAME {name}"] + ([f"2 NICK {nick}"] if nick else [])
    lines += [f"1 SEX {sex}"] if sex else []
    lines += [f"1 BIRT\n2 DATE {born}"] if born else []
    lines += [f"1 DEAT\n2 DATE {died}"] if died else []
    lines += [f"1 FAMC @{f}@" for f in famc] + [f"1 FAMS @{f}@" for f in fams]
    return "\n".join(lines)


def fam(x, husb=None, wife=None, chil=(), marr=None):
    lines = [f"0 @{x}@ FAM"] + ([f"1 HUSB @{husb}@"] if husb else []) + ([f"1 WIFE @{wife}@"] if wife else [])
    lines += [f"1 CHIL @{c}@" for c in chil] + ([f"1 MARR\n2 DATE {marr}"] if marr else [])
    return "\n".join(lines)


def start(con, data):
    con.commit()
    iid = gedcom_import.start(con, "tree.ged", data)
    return iid, gedcom_import.load(con, iid)[2]


def items(con, iid):
    _, data, matches = gedcom_import.load(con, iid)
    return gedcom_checks.check(con, data, matches)


def test_one_word_alike_is_not_a_match_by_itself(con):
    """'Edna' in a file isn't Edna Baker (whom the family calls Edna) on that alone: unsure, and chosen anyway she
    waits to be confirmed; said not to be her, she isn't suggested again."""
    walter = person(con, "Walter Baker", "male")
    edna = person(con, "Edna Baker", "female", other={"Edna": "nickname"})
    couple(con, walter, edna)
    iid, matches = start(con, gedcom(indi("F", "Frank /Hollis/", "M", fams=["F1"]), indi("E", "Edna", "F", fams=["F1"]),
                                     fam("F1", "F", "E")))
    assert matches["@E@"]["choice"] is None and matches["@E@"]["how"] == "unsure"
    gedcom_import.set_choices(con, iid, {"@E@": str(edna)})
    doubt = next(i for i in items(con, iid) if i.xref == "@E@")
    assert doubt.stop and any("Only the one name Edna" in line for line in doubt.lines)
    assert any("Walter Baker" in line and "Frank Hollis" in line for line in doubt.lines)  # their partners differ
    with pytest.raises(gedcom_import.Problem, match="Check 1 match"):
        gedcom_import.apply(con, iid)
    gedcom_import.set_choices(con, iid, {"not:@E@": str(edna)})
    m = gedcom_import.load(con, iid)[2]["@E@"]
    assert m["choice"] is None and m["not"] == [edna]
    gedcom_import.apply(con, iid)
    assert con.execute("SELECT count(*) FROM person WHERE name LIKE 'Edna%'").fetchone()[0] == 2  # a new Edna
    assert family.load(con).partners[edna] == {walter: (True, "married")}  # Edna Baker's husband is still only Walter


def test_the_family_finds_married_names_and_parents_say_who_is_who(con):
    """Here Henry Miller Sr. is Clara's father; the file gives Clara another father, Arthur, and has a Henry born in
    1959: that Henry isn't matched to him, Clara's mother is found under her married name, and Arthur is suggested as
    the man here, then renamed as the file names him when that is chosen."""
    henry = person(con, "Henry Miller Sr.", "male", given="Henry", surname="Miller")
    margaret = person(con, "Margaret Dalton", "female", given="Margaret", surname="Dalton")
    clara = person(con, "Clara Porter", "female", given="Clara", surname="Porter", other={"Clara Miller": "birth"})
    parents(con, clara, henry, margaret)
    couple(con, henry, margaret, current=False)
    iid, matches = start(con, gedcom(
        indi("A", "Arthur James /Miller/", "M", "20 NOV 1901", nick="Art", fams=["F1"]),
        indi("K", "Margaret Ellen /Kerr/", "F", "14 OCT 1911", fams=["F1"]),
        indi("C", "Clara Ruth /Miller/", "F", "1 JAN 1933", famc=["F1"]),
        indi("H", "Henry Paul /Miller/", "M", "6 JAN 1959"),
        fam("F1", "A", "K", ["C"], marr="15 JUN 1930")))
    assert matches["@C@"]["choice"] == clara
    assert matches["@H@"]["choice"] is None and "Clara Ruth Miller's father is Arthur" in matches["@H@"]["why"]
    assert matches["@K@"]["choice"] == margaret and matches["@K@"]["how"] == "family"
    assert matches["@A@"]["choice"] is None
    checks = items(con, iid)
    suggest = next(i for i in checks if i.kind == "suggest")
    assert (suggest.xref, suggest.pid) == ("@A@", henry) and "the only father" in suggest.lines[0].lower()
    assert [i.xref for i in checks if i.stop] == ["@K@"]  # found through the family: to confirm
    gedcom_import.set_choices(con, iid, {"confirm:@K@": str(margaret), "@A@": str(henry), "confirm:@A@": str(henry)})
    checks = items(con, iid)
    assert not [i for i in checks if i.stop]
    name = next(i for i in checks if i.code == "name")
    assert (name.here, name.there) == ("Henry Miller Sr.", "Arthur James Miller")
    gedcom_import.set_choices(con, iid, {f"resolve:@A@:{name.key}": "theirs"})
    report = gedcom_import.apply(con, iid)
    assert report["renamed"] == ["Henry Miller Sr. → Arthur James Miller"]
    assert con.execute("SELECT name FROM person WHERE id = ?", (henry,)).fetchone()[0] == "Arthur James Miller"
    assert {r["name"] for r in events.other_names(con, henry)} >= {"Henry Miller Sr.", "Art"}
    assert family.load(con).parents[clara] == {henry, margaret}  # no third parent
    assert con.execute("SELECT count(*) FROM person WHERE name LIKE 'Margaret%'").fetchone()[0] == 1
    assert con.execute("SELECT date FROM event WHERE person_id = ? AND tag = 'BIRT'", (margaret,)).fetchone()[0] == \
        "14 OCT 1911"


def test_ages_and_photos_that_cannot_be(con):
    """Born in 1990 in the file, but here their son was born in 1933; dead in 1990 in the file, but here in photos
    named by hand from 2015: neither is matched by itself."""
    ann, tom = person(con, "Ann Baker", "female"), person(con, "Tom Baker", "male", born=1933)
    parents(con, tom, ann)
    carl = person(con, "Carl Whitfield", "male")
    image = con.execute("""INSERT INTO image(sha256, format, width, height, stage, taken_at, taken_at_source)
                           VALUES (?, 'jpeg', 400, 300, 1, '2015-06-01T10:00:00', 'exif')""", ("cd" * 32,)).lastrowid
    con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, embedding, person_id, assigned_by, quality)
                   VALUES (?, 0.4, 0.3, 0.6, 0.6, ?, ?, 'user', 25)""", (image, np.zeros(512, np.float32).tobytes(), carl))
    _, matches = start(con, gedcom(indi("A", "Ann /Baker/", "F", "1990"), indi("C", "Carl /Whitfield/", "M", died="1990")))
    assert matches["@A@"]["choice"] is None and "their son Tom Baker was born in 1933" in matches["@A@"]["why"]
    assert matches["@C@"]["choice"] is None and "in photos from 2015" in matches["@C@"]["why"]


def test_someone_who_would_be_their_own_ancestor(con):
    paul, hugh = person(con, "Paul Hale", "male"), person(con, "Hugh Hale", "male")
    parents(con, paul, hugh)  # here Hugh is Paul's father; the file has it the other way round
    _, matches = start(con, gedcom(indi("H", "Hugh /Hale/", "M", famc=["F1"]), indi("P", "Paul /Hale/", "M", fams=["F1"]),
                                   fam("F1", "P", None, ["H"])))
    assert [m["choice"] for m in matches.values()].count(None) == 1
    assert any("ancestor" in m["why"] or "descendant" in m["why"] for m in matches.values())


def test_a_couple_who_are_brother_and_sister_here(con):
    pat, amy, ben = person(con, "Pat Cole"), person(con, "Amy Cole", "female"), person(con, "Ben Cole", "male")
    parents(con, amy, pat)
    parents(con, ben, pat)
    _, matches = start(con, gedcom(indi("A", "Amy /Cole/", "F", fams=["F1"]), indi("B", "Ben /Cole/", "M", fams=["F1"]),
                                   fam("F1", "B", "A")))
    assert [m["choice"] for m in matches.values()].count(None) == 1
    assert any("brother and sister" in m["why"] for m in matches.values())


def test_two_in_the_file_who_fit_one_here_are_both_unsure(con, cfg):
    """Ted Steven Miller, and Robert Edward Miller known as Ted: both are a Ted Miller, so neither is matched to the one
    here by itself, and Edward Miller (Edward, Ted) isn't given him either while they are."""
    ted = person(con, "Ted Miller", "male", given="Ted", surname="Miller")
    _, matches = start(con, gedcom(indi("S", "Ted Steven /Miller/", "M"), indi("R", "Robert Edward /Miller/", "M", nick="Ted"),
                                   indi("D", "Edward /Miller/", "M")))
    assert matches["@S@"]["choice"] is None and matches["@R@"]["choice"] is None
    assert "Robert Edward Miller in the file fit as well" in matches["@S@"]["why"]
    assert matches["@D@"]["choice"] is None and matches["@D@"]["how"] == "unsure"  # nor to a weaker one meanwhile
    iid = gedcom_import.load(con, 1)[0]["id"]
    pick = next(i for i in items(con, iid) if i.kind == "pick")  # asked as one question: which of them is he?
    assert (pick.pid, sorted(n for _, n in pick.rivals)) == (ted, ["Robert Edward Miller", "Ted Steven Miller"])
    page = TestClient(webapp.create_app(cfg), client=LAN).get(f"/family/import/{iid}").text
    assert "here is which of them in the file?" in page and "None of them" in page
    gedcom_import.set_choices(con, iid, {"@R@": str(ted), "confirm:@R@": str(ted)})
    assert gedcom_import.load(con, iid)[2]["@R@"]["choice"] == ted
    assert not [i for i in items(con, iid) if i.kind == "pick" or i.stop]


def test_someone_chosen_for_two_must_be_sorted_out(con):
    ann = person(con, "Ann Lee", "female")
    iid, _ = start(con, gedcom(indi("A", "Ann /Lee/", "F"), indi("B", "Anna /Lee/", "F")))
    gedcom_import.set_choices(con, iid, {"@A@": str(ann), "@B@": str(ann)})
    assert {i.xref for i in items(con, iid) if i.stop} == {"@A@", "@B@"}
    with pytest.raises(gedcom_import.Problem, match="Check 2 matches"):
        gedcom_import.apply(con, iid)


@pytest.mark.parametrize("choice", ["keep", "replace", "same", "adopted"])
def test_a_second_mother_is_settled_as_chosen(con, choice):
    """Daniel's mother is Helen here and Rose in the file: kept out by default, or put in Helen's place, or Rose is
    Helen, or Rose is his adoptive mother too."""
    gary = person(con, "Gary Miller", "male", given="Gary", surname="Miller")
    helen = person(con, "Helen Miller", "female", given="Helen", surname="Miller")
    daniel = person(con, "Daniel Miller", "male", given="Daniel", surname="Miller")
    parents(con, daniel, gary, helen)
    couple(con, gary, helen)
    iid, matches = start(con, gedcom(
        indi("G", "Gary Allen /Miller/", "M", fams=["F1"]), indi("R", "Rose /Hayward/", "F", fams=["F1"]),
        indi("D", "Daniel Gary /Miller/", "M", famc=["F1"]), fam("F1", "G", "R", ["D"])))
    assert matches["@D@"]["choice"] == daniel and matches["@G@"]["choice"] == gary
    clash = next(i for i in items(con, iid) if i.code == "parent")
    assert (clash.xref, clash.here, clash.there, clash.same_pid) == ("@D@", "Helen Miller", "Rose Hayward", helen)
    value = {"replace": f"replace:{helen}", "same": f"same:{helen}"}.get(choice, choice)
    if choice != "keep":
        gedcom_import.set_choices(con, iid, {f"resolve:@D@:{clash.key}": value})
    report = gedcom_import.apply(con, iid)
    g = family.load(con)
    rose = con.execute("SELECT id FROM person WHERE name = 'Rose Hayward'").fetchone()
    if choice == "keep":
        assert g.parents[daniel] == {gary, helen} and rose and any("Rose" in k for k in report["kept"])
    elif choice == "replace":
        assert g.parents[daniel] == {gary, rose[0]} and any("in place of Helen" in r for r in report["replaced"])
    elif choice == "same":
        assert rose is None and g.parents[daniel] == {gary, helen}
        assert "Rose Hayward" in {r["name"] for r in events.other_names(con, helen)}
    else:
        assert g.parents[daniel] == {gary, helen, rose[0]} and g.how(daniel, rose[0]) == "adopted"


def test_a_life_event_from_the_file_when_chosen(con):
    owen = person(con, "Owen Miller", "male", born="15 JUN 2011")
    con.execute("UPDATE event SET sort_date = '2011-06-15' WHERE person_id = ?", (owen,))
    iid, matches = start(con, gedcom(indi("O", "Owen /Miller/", "M", "2012")))
    assert matches["@O@"]["choice"] == owen
    clash = next(i for i in items(con, iid) if i.code == "event")
    assert clash.key == "event:BIRT" and clash.chosen == "keep"
    gedcom_import.set_choices(con, iid, {f"resolve:@O@:{clash.key}": "theirs"})
    report = gedcom_import.apply(con, iid)
    assert con.execute("SELECT date FROM event WHERE person_id = ? AND tag = 'BIRT'", (owen,)).fetchone()[0] == "2012"
    assert report["replaced"] and not report["conflicts"]


def test_the_import_page_lists_what_to_check_and_its_buttons_work(con, cfg):
    walter, edna = person(con, "Walter Baker", "male"), person(con, "Edna Baker", "female")
    couple(con, walter, edna)
    owen = person(con, "Owen Miller", "male", born="2011")
    iid, _ = start(con, gedcom(indi("E", "Edna", "F"), indi("O", "Owen /Miller/", "M", "2012")))
    gedcom_import.set_choices(con, iid, {"@E@": str(edna)})
    c = TestClient(webapp.create_app(cfg), client=LAN)
    page = c.get(f"/family/import/{iid}").text
    assert "To check" in page and "⚠ Edna" in page and "The same person" in page and "disabled" in page
    assert 'name="resolve:@O@:event:BIRT"' in page and "Use the file&#39;s: 2012" in page
    url = f"/api/family/import/{iid}/choices"
    assert c.post(f"/api/family/import/{iid}/apply", headers=APP).status_code == 400
    assert c.post(url, json={"confirm:@E@": str(owen)}, headers=APP).status_code == 400  # not who she is matched to
    assert c.post(url, json={"resolve:@O@:event:BIRT": "nonsense"}, headers=APP).status_code == 400
    assert c.post(url, json={"confirm:@E@": str(edna), "resolve:@O@:event:BIRT": "theirs"}, headers=APP).status_code == 200
    page = c.get(f"/family/import/{iid}").text
    assert "You said they are the same person." in page and "Not the same after all" in page
    assert c.post(f"/api/family/import/{iid}/apply", headers=APP).status_code == 200
    assert "Taken from the file, as you chose" in c.get(f"/family/import/{iid}").text


@pytest.mark.parametrize("theirs, clash", [("2011", False), ("JUN 2011", False), ("ABT 2011", False),
                                           ("15 JUN 2011", False), ("16 JUN 2011", True), ("2012", True)])
def test_the_same_date_less_exactly_is_no_clash(con, theirs, clash):
    """'2011' in the file for June 15, 2011 here says the same, less exactly: nothing to ask. Another day or year is."""
    owen = person(con, "Owen Miller", "male", born="15 JUN 2011")
    con.execute("UPDATE event SET sort_date = '2011-06-15' WHERE person_id = ?", (owen,))
    iid, matches = start(con, gedcom(indi("O", "Owen /Miller/", "M", theirs)))
    assert matches["@O@"]["choice"] == owen
    assert any(i.code == "event" for i in items(con, iid)) is clash
