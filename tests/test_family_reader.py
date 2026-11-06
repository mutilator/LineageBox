"""Reading writing into the family tree (family_reader.py): what the AI's answer is taken to mean, who in it is who in
the tree, the changes offered, making the ticked ones, and the pages. The AI is never asked: its answer is the one the
prompt shows it, or one with the mistakes small models make."""
import base64
import copy
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from lineagebox import events, family, family_reader as fr, llm
from lineagebox.web import app as webapp
from lineagebox.web.routes import reading as reading_routes
from tests.conftest import LAN

APP = {"X-Requested-With": "lineagebox"}
SOURCE = "https://news.example.com/obituaries/peggy-miller"
TEXT = fr.EXTRACT_SYSTEM.split("Text:\n", 1)[1].split("\nAnswer:", 1)[0]
ANSWER = json.loads(fr.EXTRACT_SYSTEM.split("Answer:\n", 1)[1])


def person(con, name, sex=None, given=None, surname=None):
    return con.execute("INSERT INTO person(name, sex, given_name, surname, names_checked) VALUES (?, ?, ?, ?, 1)",
                       (name, sex, given, surname)).lastrowid


@pytest.fixture
def tree(con):
    """Peggy's family as the tree has it before her obituary: her (born 1931, not 2 June 1930), her husband Tom, her
    father by the name he went by (Bud), her daughter Susan, Susan's husband, and her son James; and a Susan Carter
    who is someone else."""
    ids = {
        "peggy": person(con, "Peggy Miller", "female", "Margaret", "Miller"),
        "tom": person(con, "Tom Miller", "male", "Tom", "Miller"),
        "bud": person(con, "Bud Baker", "male", "Bud", "Baker"),
        "susan": person(con, "Susan Carter", "female", "Susan", "Carter"),
        "david": person(con, "David Carter", "male", "David", "Carter"),
        "james": person(con, "James Miller", "male", "James", "Miller"),
        "other_susan": person(con, "Susan Carter (Ohio)", "female", "Susan", "Carter"),
    }
    events.save(con, None, tag="BIRT", person_id=ids["peggy"], date_text="1931")
    for child, parent in (("peggy", "bud"), ("susan", "peggy"), ("susan", "tom"), ("james", "peggy"),
                          ("james", "tom")):
        con.execute("INSERT INTO parent(child_id, parent_id) VALUES (?, ?)", (ids[child], ids[parent]))
    for a, b in (("peggy", "tom"), ("susan", "david")):
        x, y = sorted((ids[a], ids[b]))
        con.execute("INSERT INTO partner(a, b, current, married) VALUES (?, ?, 1, 1)", (x, y))
    con.commit()
    return ids


def read(con, answer=ANSWER):
    found = fr.tidy(copy.deepcopy(answer))
    return found, fr.match(con, found)


def test_what_the_answer_is_taken_to_mean():
    found = fr.tidy(copy.deepcopy(ANSWER))
    p = {x["id"]: x for x in found["people"]}
    assert found["written"] == "3 MAR 2015" and found["kind"] == "obituary"
    facts = {f["tag"]: f for f in p["p1"]["facts"]}
    assert (facts["BIRT"]["date"], facts["BIRT"]["place"]) == ("2 JUN 1930", "Lima, Ohio, USA")
    assert facts["DEAT"]["note"] == "At Mercy Hospital"
    assert facts["BURI"]["place"] == "Oak Hill Cemetery, Lakeview, Ohio, USA"
    assert facts["OCCU"]["note"] == "School cook for 25 years"
    # those who had died before her, with no date given: before the obituary
    assert [f["date"] for f in p["p2"]["facts"]] == ["BEF 3 MAR 2015"]
    assert [f["date"] for f in p["p4"]["facts"]] == ["2001"]
    # where those living lived, then
    assert [(f["tag"], f["date"], f["place"]) for f in p["p5"]["facts"]] == [("RESI", "2015", "Dayton, Ohio, USA")]
    # surnames the notice leaves out: her sister Alice was born a Baker, her daughter Susan a Miller (her father's)
    assert (p["p12"]["surname"], p["p12"]["guessed"]) == ("Baker", ["surname"])
    assert p["p5"]["birth_surname"] == "Miller"
    assert found["marriages"][0]["date"] == "5 MAY 1951"


def test_the_mistakes_small_models_make_are_put_right():
    answer = {"kind": "obituary", "written": "2012", "people": [
        {"id": "p1", "given": "Henry", "surname": "Baker", "sex": "male", "living": "deceased", "died": "May 1, 2012"},
        {"id": "p2", "given": "Ann", "surname": "Jones", "sex": "female", "living": "deceased", "is": "wife",
         "of": "p1"},
        {"id": "p3", "given": "Susan", "surname": "Carter", "sex": "female", "living": "living", "is": "daughter",
         "of": "p1"},
        # given to the daughter by the surname, where the obituary lists him as Henry's
        {"id": "p4", "given": "Ben", "surname": "Carter", "sex": "male", "living": "living", "is": "grandson",
         "of": "p3"},
        {"id": "p5", "given": "John", "surname": "Baker", "sex": "male", "living": "living", "is": "son", "of": "p1"},
        # "John (Marty) Baker": Marty made a husband from the first name
        {"id": "p6", "given": "Marty", "surname": "Baker", "sex": "male", "living": "living", "is": "husband",
         "of": "p5"},
        {"id": "p7", "given": "Walter", "surname": "Baker", "sex": "male", "living": "deceased", "is": "father",
         "of": "p1"},
        {"id": "p8", "given": "Edna", "surname": "Baker", "sex": "female", "living": "deceased", "is": "mother",
         "of": "p1"}],
        "marriages": [{"a": "p1", "b": "p2", "date": "June 1, 1950"}]}
    found = fr.tidy(answer)
    p = {x["id"]: x for x in found["people"]}
    assert ["p4", "grandson", "p1"] in found["relatives"]
    assert ["p6", "spouse", "p5"] in found["relatives"] and p["p6"]["sex"] is None and p["p5"]["sex"] is None
    assert (p["p2"]["surname"], p["p2"]["birth_surname"]) == ("Baker", "Jones")  # "Henry married Ann Jones"
    assert ["p7", "husband", "p8"] in found["relatives"]  # his parents, a couple


def test_a_couple_told_both_ways_is_one_link():
    answer = {"kind": "obituary", "people": [
        {"id": "p1", "given": "Ann", "surname": "Baker", "sex": "female", "living": "deceased"},
        {"id": "p2", "given": "Susan", "surname": "Carter", "sex": "female", "living": "living", "is": "daughter",
         "of": "p1", "also": [{"is": "wife", "of": "p3"}]},
        {"id": "p3", "given": "David", "surname": "Carter", "sex": "male", "living": "living", "is": "husband",
         "of": "p2"}]}
    links = fr._links(fr.tidy(answer))
    assert [link for link in links if link[1] != "parent"] == [("p2", "partner", "p3")]


def test_the_answer_is_checked_against_the_text():
    text = ("Ann Baker died. She is survived by her brother Tom Baker, her daughter Jane (Rick Lee) Miller, and her "
            "dear friends Rose and Mabel. She was preceded in death by her sister Joyce Spoor.")
    answer = {"kind": "obituary", "people": [
        {"id": "p1", "given": "Ann", "surname": "Baker", "sex": "female", "living": "deceased"},
        {"id": "p2", "given": "Tom", "surname": "Baker", "sex": "male", "living": "living", "is": "brother",
         "of": "p1"},
        {"id": "p3", "given": "Jane", "surname": "Miller", "sex": "female", "living": "living", "is": "daughter",
         "of": "p1"},
        {"id": "p4", "given": "Rick", "surname": "Miller", "sex": "male", "living": "living", "is": "husband",
         "of": "p3"},
        {"id": "p5", "given": "Rose", "surname": "", "sex": "female", "living": "living", "is": "daughter",
         "of": "p1"},
        {"id": "p6", "given": "Mabel", "surname": "", "sex": "female", "living": "living", "is": "daughter",
         "of": "p1"},
        {"id": "p7", "given": "Joyce", "surname": "Spoor", "sex": "female", "living": "deceased", "is": "sister",
         "of": "p1"},
        {"id": "p8", "given": "Spoor", "surname": "", "sex": "male", "living": "deceased", "is": "husband",
         "of": "p7"}]}
    found = fr.tidy(answer, text=text)
    p = {x["id"]: x for x in found["people"]}
    assert set(p) == {"p1", "p2", "p3", "p4", "p7"}  # the friends, and the husband made of a surname, are out
    assert p["p4"]["surname"] == "Lee" and ["p4", "partner", "p3"] in found["relatives"]
    assert p["p7"]["birth_surname"] == ""  # Ann may have married into Baker: her sister's birth name isn't known


def test_a_long_text_is_read_in_halves(cfg, monkeypatch):
    """An answer cut off for length: the text is read in two halves, each beginning with whom it is about, and the
    two answers made one, with someone named in both halves once."""
    text = ("Ann Baker, 90, died May 1, 2015. Her son Tom Baker was at her side. She worked as a nurse. She is "
            "survived by her son Tom Baker. Her daughter Jane Miller survives her. She was born in Lima.")
    asked = []

    def chat_json(self, system, user, schema, images=(), temperature=0.1, max_tokens=1024):
        asked.append(user)
        if len(asked) == 1:
            raise llm.CutOff("cut off")
        people = [{"id": "p1", "given": "Ann", "surname": "Baker", "sex": "female", "living": "deceased",
                   "died": "May 1, 2015" if len(asked) == 2 else "", "events": [{"kind": "worked as",
                                                                                 "what": "nurse"}]}]
        if "Tom" in user:
            people.append({"id": "p2", "given": "Tom", "surname": "Baker", "sex": "male", "living": "living",
                           "is": "son", "of": "p1"})
        if "Jane" in user:
            people.append({"id": "p3" if "Tom" in user else "p2", "given": "Jane", "surname": "Miller",
                           "sex": "female", "living": "living", "is": "daughter", "of": "p1"})
        about = {"died": "May 1, 2015" if len(asked) == 2 else "", "born_place": "" if len(asked) == 2 else "Lima"}
        return ({"kind": "obituary", "written": "", "about": about, "people": people, "marriages": []},
                llm.Usage(10, 10, 1.0))

    monkeypatch.setattr(llm.Ollama, "chat_json", chat_json)
    result = fr.extract(cfg, text)
    assert result["parts"] == 2 and len(asked) == 3
    first, second = asked[1], asked[2]
    assert first.startswith("Text:\nAnn Baker, 90, died") and second.startswith("Text:\nAnn Baker, 90, died")
    names = [(p["id"], p["given"], p.get("is")) for p in result["answer"]["people"]]
    assert names == [("p1", "Ann", None), ("p2", "Tom", "son"), ("p3", "Jane", "daughter")]
    assert result["answer"]["people"][0]["died"] == "May 1, 2015"
    assert result["answer"]["about"] == {"died": "May 1, 2015", "born_place": "Lima"}  # what either half says


def test_places_and_weddings_are_checked_against_the_text():
    text = ("Ann Baker, 80, of Lima, Ohio, died May 1, 2015 at Mercy Medical Center. She worked for Acme Industries in "
            "Lima. She was preceded in death by her husband Tom Baker in 1988. Cremation has taken place.")
    answer = {"kind": "obituary", "people": [
        {"id": "p1", "given": "Ann", "surname": "Baker", "sex": "female", "living": "deceased", "died": "May 1, 2015",
         "died_place": "Mercy Medical Center, Ohio",
         "events": [{"kind": "worked as", "what": "clerk", "place": "Acme Industries, Lima, Ohio"}]},
        {"id": "p2", "given": "Tom", "surname": "Baker", "sex": "male", "living": "deceased", "died": "1988",
         "is": "husband", "of": "p1"}],
        "marriages": [{"a": "p1", "b": "p2", "date": "1988"}]}  # the year he died, not a wedding
    found = fr.tidy(answer, text=text)
    facts = {f["tag"]: f for f in found["people"][0]["facts"]}
    assert (facts["DEAT"]["place"], facts["DEAT"]["note"]) == (None, "At Mercy Medical Center")
    assert (facts["OCCU"]["place"], facts["OCCU"]["note"]) == ("Lima, Ohio, USA", "Clerk, Acme Industries")
    assert "CREM" in facts  # the obituary's cremation is hers, though the AI left it out
    assert found["marriages"] == []


def test_what_small_models_misread_is_checked_against_the_text():
    text = ("Ruth Mae (Klein) Baker, 90, died June 3, 2019. She was born to Walter and Edna (Shantz) Klein. She worked "
            "as a nurse at Mercy Hospital. She enjoyed being involved in her church, coordinating fellowship dinners, "
            "and was an avid quilter. She was a driver for the Lima School Band. She is survived by her son Tom. "
            "Services were held at Grace Church in Lima, and cremation will follow.")
    answer = {"kind": "obituary", "written": "", "about": {"died": "June 3, 2019", "buried": "June 7, 2019",
                                                          "buried_place": "Grace Church, Lima, Ohio", "age": 90},
              "people": [
                  {"id": "p1", "given": "Ruth Mae", "nickname": "Klein", "surname": "Baker", "sex": "female",
                   "living": "deceased", "events": [
                       {"kind": "worked as", "what": "nurse at Mercy Hospital"},
                       {"kind": "worked as", "what": "coordinating fellowship dinners"},
                       {"kind": "worked as", "what": "avid quilter"},
                       {"kind": "moved", "what": "Lima School Band", "place": "Lima School Band"}]},
                  {"id": "p2", "given": "Edna", "surname": "Klein", "sex": "female", "living": "deceased",
                   "born_place": "Shantz", "is": "mother", "of": "p1"},
                  {"id": "p3", "given": "Tom", "surname": "", "sex": "male", "living": "living", "is": "son",
                   "of": "p1"},
                  {"id": "p4", "given": "Tom", "surname": "Baker", "sex": "male", "living": "living", "is": "son",
                   "of": "p1"}],  # listed twice
              "marriages": []}
    found = fr.tidy(answer, text=text)
    p = {x["id"]: x for x in found["people"]}
    assert (p["p1"]["nickname"], p["p1"]["birth_surname"]) == ("", "Klein")  # a maiden name, not a nickname
    assert p["p2"]["birth_surname"] == "Shantz" and not p["p2"]["facts"][:-1]  # nor a birthplace
    facts = [(f["tag"], f["note"]) for f in p["p1"]["facts"]]
    assert ("DEAT", None) in facts and ("CREM", None) in facts  # from the about block, and the text
    assert not any(tag == "BURI" for tag, _ in facts)  # the church the service was at; and she was cremated
    assert [note for tag, note in facts if tag in ("OCCU", "RESI")] == ["Nurse at Mercy Hospital"]
    assert set(p) == {"p1", "p2", "p3"} and p["p3"]["surname"] == "Baker"  # Tom once


def test_relationships_the_text_states_in_so_many_words():
    """What small models leave out, put back from the text: a couple's children, "son of", a divorce, a maiden name in
    parentheses, a friend named before "a friend", and a mother born with her own surname. And what they mustn't
    become: a middle name taken for someone, a planned wedding taken for a marriage."""
    text = ("Carl Jensen married Rita Moss in 1960; they divorced in 1975. In 1978 Carl married Joan Weber. Carl and "
            "Rita had two sons, Erik and Lars. Clara Jensen, daughter of Carl and Joan, was born in 1980. Ann (Hayes) "
            "Weber and Kevin Lowe, a friend of the family, were there. Their grandson is Owen Carl Jensen.")
    p = lambda i, given, surname, sex, **more: {"id": i, "given": given, "surname": surname, "sex": sex,
                                                "living": "unknown", **more}
    answer = {"kind": "notes", "people": [
        p("p1", "Carl", "Jensen", "male"), p("p2", "Rita", "Moss", "female", **{"is": "wife", "of": "p1"}),
        p("p3", "Joan", "Weber", "female", **{"is": "wife", "of": "p1"}),
        p("p4", "Erik", "Jensen", "male", **{"is": "son", "of": "p2"}), p("p5", "Lars", "Jensen", "male"),
        p("p6", "Clara", "Jensen", "female"), p("p7", "Ann", "Weber", "female", **{"is": "mother", "of": "p3"}),
        p("p8", "Kevin", "Lowe", "male", **{"is": "other relative", "of": "p1"}),
        p("p9", "Owen", "Jensen", "male", **{"is": "grandson", "of": "p1"})],
        "marriages": [{"a": "p1", "b": "p2", "date": "1960"}, {"a": "p1", "b": "p3", "date": "1978"}]}
    found = fr.tidy(answer, text=text)
    rel = {tuple(r) for r in found["relatives"]}
    people = {x["id"]: x for x in found["people"]}
    assert {("p4", "son", "p1"), ("p5", "son", "p1"), ("p5", "son", "p2")} <= rel  # "Carl and Rita had two sons"
    assert {("p6", "daughter", "p1"), ("p6", "daughter", "p3")} <= rel  # "daughter of Carl and Joan"
    assert ("p2", "former wife", "p1") in rel and ("p3", "wife", "p1") in rel  # only Rita divorced him
    assert people["p7"]["birth_surname"] == "Hayes" and "p8" not in people
    assert not any(a == "p1" and b == "p1" for a, w, b in rel)  # "Owen Carl Jensen" names no second Carl

    engaged = {"kind": "engagement announcement", "people": [
        p("p1", "Hannah", "Lind", "female"), p("p2", "Marcus", "Okafor", "male", **{"is": "fiance", "of": "p1"})],
        "marriages": [{"a": "p1", "b": "p2", "date": ""}]}
    found = fr.tidy(engaged, text="Hannah Lind is engaged to Marcus Okafor. A June 2017 wedding is planned.")
    assert found["marriages"] == [] and found["people"][0]["surname"] == "Lind"  # not his by a wedding to come


def test_the_name_someone_goes_by_here_makes_the_text_s_their_full_name(con):
    jack = person(con, "Jack Smith", "male", "Jack", "Smith")
    con.commit()
    answer = {"kind": "obituary", "people": [
        {"id": "p1", "given": "Henry L.", "nickname": "Jack", "surname": "Smith", "sex": "male",
         "living": "deceased"}]}
    found, choices = read(con, answer)
    assert choices["p1"]["choice"] == jack
    texts = [c.text for c in fr.propose(con, found, choices).changes]
    assert "Full name: Henry L. Smith" in texts and not any(t.startswith("Also known as") for t in texts)


def test_dates_and_places_as_the_tree_keeps_them():
    assert fr.tidy_date("Saturday, January 30, 2021").gedcom == "30 JAN 2021"
    assert fr.tidy_date("the 5th of July, 1955").gedcom == "5 JUL 1955"
    assert fr.tidy_date("Jan. 4, 2001").gedcom == "4 JAN 2001"
    assert fr.tidy_date("many years") is None and fr.tidy_date("unknown") is None
    assert fr.tidy_date("2999") is None  # still to come: misread
    assert fr.tidy_place("Lima, OH") == "Lima, Ohio, USA"
    assert fr.tidy_place("Lima OH") == "Lima, Ohio, USA"
    assert fr.tidy_place("Lima, Mich.") == "Lima, Michigan, USA"
    assert fr.tidy_place("Lakeview", "Ohio") == "Lakeview, Ohio, USA"
    assert fr.tidy_place("Toronto, Ontario, Canada") == "Toronto, Ontario, Canada"
    assert fr.tidy_place("unknown") is None


def test_who_is_who_goes_by_the_relatives(con, tree):
    found, choices = read(con)
    chosen = {x: m["choice"] for x, m in choices.items()}
    assert chosen["p1"] == tree["peggy"]  # by her name and nickname, though the birth year differs by one
    assert chosen["p4"] == tree["tom"]  # her husband, Thomas for Tom
    assert chosen["p2"] == tree["bud"] and "only father" in choices["p2"]["why"]  # the one father the tree has
    assert chosen["p5"] == tree["susan"]  # her daughter, not the other Susan Carter
    assert chosen["p6"] == tree["david"] and chosen["p7"] == tree["james"]
    for x in ("p3", "p8", "p9", "p10", "p11", "p12"):
        assert chosen[x] is None, x


def test_the_changes_offered(con, tree):
    found, choices = read(con)
    prop = fr.propose(con, found, choices, SOURCE)
    by_text = {c.text: c for c in prop.changes}
    assert not by_text["Born June 2, 1930"].on  # the text and the tree differ: offered, not ticked
    assert by_text["Born June 2, 1930"].now == "the tree has 1931"
    assert by_text["Born: 1931 in Lima, Ohio, USA"].on  # the place, which the tree lacks
    assert "Nickname: Peggy" not in by_text  # she goes by it here already
    assert by_text["Birth name: Margaret Baker"].on
    assert "Add Ruth Baker (born Ruth Klein)" in by_text
    assert by_text["Peggy Miller is Ruth Baker's daughter"].needs == ["person-p3"]
    assert "Add Kim Miller" in by_text and "Kim Miller is James Miller's wife" in by_text
    # her sister: a daughter of her parents, the one in the tree and the one to add
    sister = next(c for c in prop.changes if c.who == "p12" and c.kind == "link")
    assert sister.text == "Alice Baker is Bud Baker and Ruth Baker's daughter"
    assert set(sister.needs) == {"person-p12", "person-p3"}
    # grandchildren whose parents the text doesn't name: listed, not added
    assert {u["name"] for u in prop.unplaced} == {"Emma Carter", "Jake Carter"}
    assert not any(c.who in ("p9", "p10") for c in prop.changes)
    # what the tree has already isn't offered again
    assert not any("Tom Miller is" in c.text for c in prop.changes)


def test_making_the_ticked_changes(con, tree):
    found, choices = read(con)
    prop = fr.propose(con, found, choices, SOURCE)
    report = fr.apply(con, found, choices, {c.key for c in prop.changes if c.on}, SOURCE)
    assert not report["skipped"], report["skipped"]
    ruth = report["added"]["p3"]
    assert con.execute("SELECT name, sex, given_name, surname, name_type FROM person WHERE id = ?",
                       (ruth,)).fetchone()[:] == ("Ruth Baker", "female", "Ruth", "Baker", "married")
    assert con.execute("SELECT name FROM person_name WHERE person_id = ? AND type = 'birth'",
                       (ruth,)).fetchone()[0] == "Ruth Klein"
    g = family.load(con)
    assert g.parents[tree["peggy"]] == {tree["bud"], ruth}
    assert ruth in g.partners[tree["bud"]]  # her parents, a couple
    alice = report["added"]["p12"]
    assert g.parents[alice] == {tree["bud"], ruth}
    death = con.execute("SELECT date, place, note FROM event WHERE person_id = ? AND tag = 'DEAT'",
                        (tree["peggy"],)).fetchone()
    assert death[:2] == ("3 MAR 2015", "Lima, Ohio, USA") and death[2] == f"At Mercy Hospital. Obituary: {SOURCE}."
    assert con.execute("SELECT date FROM event WHERE person_id = ? AND tag = 'BIRT'",
                       (tree["peggy"],)).fetchone()[0] == "1931"  # unticked: what the tree had stays
    assert f"Obituary: {SOURCE}" in con.execute("SELECT notes FROM person WHERE id = ?", (tree["peggy"],)).fetchone()[0]
    assert f"Named in: {SOURCE}" in con.execute("SELECT notes FROM person WHERE id = ?", (ruth,)).fetchone()[0]
    marr = con.execute("SELECT date FROM event WHERE tag = 'MARR'").fetchone()
    assert marr[0] == "5 MAY 1951"
    # read again, it finds everyone, and offers only what was left unticked
    again = fr.propose(con, found, fr.match(con, found, keep=choices), SOURCE)
    assert not [c.text for c in again.changes if c.on]
    assert {c.text for c in again.changes} == {c.text for c in prop.changes if not c.on}


def test_a_change_for_someone_new_needs_them(con, tree):
    found, choices = read(con)
    prop = fr.propose(con, found, choices, SOURCE)
    link = next(c for c in prop.changes if c.text == "Kim Miller is James Miller's wife")
    report = fr.apply(con, found, choices, {link.key}, SOURCE)
    assert report["done"] == [] and "only with Add Kim Miller" in report["skipped"][0]
    assert not con.execute("SELECT 1 FROM person WHERE name = 'Kim Miller'").fetchone()


def test_a_refused_change_leaves_the_others(con, tree):
    """Each change is made on its own: one that can't be made is reported, with those that need it, and the rest are
    made."""
    found, choices = read(con)
    prop = fr.propose(con, found, choices, SOURCE)
    jo = person(con, "Jo Miller", "female")  # James's partner now, entered meanwhile
    x, y = sorted((jo, tree["james"]))
    con.execute("INSERT INTO partner(a, b, current, married) VALUES (?, ?, 1, 1)", (x, y))
    con.commit()
    report = fr.apply(con, found, choices, {c.key for c in prop.changes if c.on}, SOURCE)
    assert report["skipped"] == ["Kim Miller is James Miller's wife: James Miller's current partner is Jo Miller. "
                                 "Mark that one as former first (on James Miller's page), or add this one as an ex."]
    assert {"p3", "p8", "p12"} <= set(report["added"])  # Kim is added all the same; the rest is made
    assert not family.load(con).partners.get(report["added"]["p8"])


def test_choices_by_hand(con, tree):
    rid = fr.start(con, TEXT, SOURCE)
    found = fr.tidy(copy.deepcopy(ANSWER))
    con.execute("UPDATE family_reading SET state = 'ready', found = ?, choices = ? WHERE id = ?",
                (json.dumps(found), json.dumps(fr.match(con, found)), rid))
    con.commit()
    fr.set_choices(con, rid, {"p8": "skip", "p5": str(tree["other_susan"])})
    _, found, choices = fr.load(con, rid)
    assert choices["p8"]["how"] == "skip" and choices["p5"]["choice"] == tree["other_susan"]
    prop = fr.propose(con, found, choices, SOURCE)
    assert not any(c.who == "p8" for c in prop.changes)
    with pytest.raises(fr.Problem, match="Nobody in the tree is called"):
        fr.set_choices(con, rid, {"p6": "Nobody Here"})


@pytest.fixture
def client(cfg, con, tree, monkeypatch):
    """The site, with the AI's reading done at once, in the request, from the prompt's own example."""
    monkeypatch.setattr(fr, "extract", lambda cfg_, text: {"answer": copy.deepcopy(ANSWER), "seconds": 1.0,
                                                           "tokens": [100, 100]})
    monkeypatch.setattr(fr, "transcribe", lambda cfg_, photos: f"{len(photos)} photos read")
    monkeypatch.setattr(reading_routes, "_read_beside", lambda s, now, rid, photos: fr.run(
        reading_routes.db.connect(s.cfg, migrate_schema=False, check_same_thread=False), now, rid, photos))
    return TestClient(webapp.create_app(cfg), client=LAN)


def test_reading_pages(client, con, tree):
    page = client.get("/family/read")
    assert page.status_code == 200 and "Read into the family tree" in page.text
    r = client.post("/api/family/read", json={"text": TEXT, "source": SOURCE}, headers=APP)
    assert r.status_code == 200, r.text
    url = r.json()["go"]
    rid = int(url.rsplit("/", 1)[1])
    assert client.get(f"{url}/progress").text == "<span data-reload-page></span>"  # done: the page loads again
    page = client.get(url).text
    assert "Obituary of Peggy Miller" in page and "Add Ruth Baker" in page and "Emma Carter" in page
    row, found, choices = fr.load(con, rid)
    keys = [c.key for c in fr.propose(con, found, choices, SOURCE).changes if c.on]
    r = client.post(f"/api/family/read/{rid}/apply", json={"do": keys}, headers=APP)
    assert r.status_code == 200 and r.json()["message"].endswith("made.")
    assert con.execute("SELECT 1 FROM person WHERE name = 'Ruth Baker'").fetchone()
    assert "changes made" in client.get(url).text
    # who is who, picked on the page: a name typed and picked from the suggestions
    r = client.post(f"/api/family/read/{rid}/choices", json={"p8": "new", "name:p8": "Susan Carter (Ohio)",
                                                              "name:p8_id": str(tree["other_susan"])}, headers=APP)
    assert r.status_code == 200
    assert fr.load(con, rid)[2]["p8"]["choice"] == tree["other_susan"]
    assert "Read it again" in client.get(url).text
    r = client.post(f"/api/family/read/{rid}/again", json={"text": TEXT}, headers=APP)
    assert r.status_code == 200 and fr.load(con, rid)[0]["state"] == "ready"
    r = client.post(f"/api/family/read/{rid}/delete", headers=APP)
    assert r.json() == {"go": "/family/read"} and fr.load(con, rid) is None
    assert client.get(url).status_code == 404


def _ticked(con, rid):
    """The keys the page sends: the ticked changes, less those switched off for want of one not ticked (app.js)."""
    row, found, choices = fr.load(con, rid)
    changes = fr.propose(con, found, choices, SOURCE).changes
    on = {c.key for c in changes if c.on}
    return [c.key for c in changes if c.on and set(c.needs) <= on]


def test_ticks_are_kept_when_the_page_loads_again(client, con, tree):
    """Someone new unticked stays unticked when picking who someone else is loads the page again, and isn't added.
    (Before, the page loaded again with every tick as it began, and everyone was added.)"""
    url = client.post("/api/family/read", json={"text": TEXT, "source": SOURCE}, headers=APP).json()["go"]
    rid = int(url.rsplit("/", 1)[1])
    row, found, choices = fr.load(con, rid)
    changes = fr.propose(con, found, choices, SOURCE).changes
    ruth = next(c for c in changes if c.text.startswith("Add Ruth Baker"))
    page = client.get(url).text
    assert f'data-ticks="/api/family/read/{rid}/ticks" autocomplete="off"' in page
    assert f'value="{ruth.key}" checked' in page
    ticked = {c.key: c.on for c in changes} | {ruth.key: False}
    assert client.post(f"/api/family/read/{rid}/ticks", json={"ticked": ticked}, headers=APP).status_code == 200
    assert client.post(f"/api/family/read/{rid}/choices", json={"p8": "skip"}, headers=APP).status_code == 200
    page = client.get(url).text
    assert f'value="{ruth.key}">' in page  # unticked still
    assert ruth.key not in _ticked(con, rid)
    r = client.post(f"/api/family/read/{rid}/apply", json={"do": _ticked(con, rid)}, headers=APP)
    assert r.status_code == 200
    assert not con.execute("SELECT 1 FROM person WHERE name = 'Ruth Baker'").fetchone()
    # reading it again starts the ticks over
    client.post(f"/api/family/read/{rid}/again", json={"text": TEXT}, headers=APP)
    assert "ticked" not in fr.load(con, rid)[1]
    assert client.post("/api/family/read/999/ticks", json={"ticked": {}}, headers=APP).status_code == 404


def test_once_made_only_what_changed_is_shown(client, con, tree):
    url = client.post("/api/family/read", json={"text": TEXT, "source": SOURCE}, headers=APP).json()["go"]
    rid = int(url.rsplit("/", 1)[1])
    assert "What changed" not in client.get(url).text
    assert client.post(f"/api/family/read/{rid}/apply", json={"do": _ticked(con, rid)}, headers=APP).status_code == 200
    page = client.get(url).text
    assert "What changed" in page and "Added to the family tree" in page and "Read it again" in page
    assert 'name="do[]"' not in page and "Make the ticked changes" not in page and "Left out" not in page
    report = json.loads(fr.load(con, rid)[0]["report"])
    ruth = report["added"]["p3"]
    assert all(isinstance(d, dict) and d["text"] for d in report["done"])
    assert f'<h4 class="read-made-who"><a href="/person/{ruth}">Ruth Baker</a></h4>' in page
    assert f'data-api="/api/people/{ruth}/remove"' in page  # added by mistake: taken out again here
    r = client.post(f"/api/people/{ruth}/remove", headers=APP)
    assert r.status_code == 200 and r.json()["message"] == "Ruth Baker was removed."
    assert 'Ruth Baker <span class="hint inline">removed from the tree since' in client.get(url).text
    # read again, what is still missing is listed, with a word on what was made before
    client.post(f"/api/family/read/{rid}/again", json={"text": TEXT}, headers=APP)
    page = client.get(url).text
    assert 'name="do[]"' in page and "made from an earlier reading of it" in page and "What changed" not in page


def test_while_the_ai_reads_its_page_says_so(client, con, monkeypatch):
    monkeypatch.setattr(reading_routes, "_read_beside", lambda s, now, rid, photos: None)  # the AI never answers
    url = client.post("/api/family/read", json={"text": "<b>Some</b> notes"}, headers=APP).json()["go"]
    page = client.get(url).text
    assert 'class="hourglass"' in page and "The AI is reading it" in page and "so far." in page
    assert 'data-live="' + url + '/progress" data-every="3"' in page
    part = client.get(f"{url}/progress").text
    assert "so far." in part and "<html" not in part and "Make the ticked changes" not in part


def test_reading_photos(client, con):
    out = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(out, "PNG")
    photo = "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()
    r = client.post("/api/family/read", json={"text": "", "photos": [photo, photo]}, headers=APP)
    rid = int(r.json()["go"].rsplit("/", 1)[1])
    assert fr.load(con, rid)[0]["text"] == "2 photos read"
    bad = "data:image/png;base64," + base64.b64encode(b"not a photo").decode()
    r = client.post("/api/family/read", json={"photos": [bad]}, headers=APP)
    assert r.status_code == 400 and "can't be opened" in r.json()["detail"]
    r = client.post("/api/family/read", json={"text": ""}, headers=APP)
    assert r.status_code == 400


def test_a_reading_that_fails_says_why(client, con, monkeypatch):
    def broken(cfg_, text):
        raise fr.Problem("The AI isn't answering (ConnectError). Is the computer it runs on switched on?")

    monkeypatch.setattr(fr, "extract", broken)
    r = client.post("/api/family/read", json={"text": TEXT}, headers=APP)
    page = client.get(r.json()["go"]).text
    assert "answering (ConnectError)" in page and "Read it again" in page


def test_reading_needs_the_ai_on(client, con):
    con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('describe_enabled', 'false')")
    con.commit()
    r = client.post("/api/family/read", json={"text": TEXT}, headers=APP)
    assert r.status_code == 400 and "turned off" in r.json()["detail"]
