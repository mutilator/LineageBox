"""A person's page as a life story (their story, notes, portrait, the timeline by age, sources) and a deeper tree:
adoption and fostering in kin words, written sources, more life events, GEDCOM media, sources and coordinates, and the
tree's checks against photos."""
import json

import pytest
from fastapi.testclient import TestClient

from lineagebox import family, people, settings
from lineagebox.web import app as webapp
from tests.conftest import LAN

APP = {"X-Requested-With": "lineagebox"}


@pytest.fixture
def adopted(con):
    """The Millers: Paul and Linda adopted Gina, whose birth mother Carol is entered too; they also foster Max, and
    Paul's mother is Ruth. Linda's son by birth is Sam."""
    ids = {n: people.person_id(con, n) for n in (
        "Paul Miller", "Linda Miller", "Gina Miller", "Carol Baker", "Max Baker", "Ruth Miller", "Sam Miller",
        "Owen Miller")}
    link = lambda who, word, other, **kw: family.link(con, ids[who], word, ids[other], **kw)
    link("Paul Miller", "wife", "Linda Miller")
    link("Paul Miller", "mother", "Ruth Miller")
    link("Paul Miller", "adopted daughter", "Gina Miller", with_parent=ids["Linda Miller"])
    link("Gina Miller", "mother", "Carol Baker")
    link("Linda Miller", "foster son", "Max Baker", with_parent=ids["Paul Miller"])
    link("Linda Miller", "son", "Sam Miller", with_parent=ids["Paul Miller"])
    link("Gina Miller", "son", "Owen Miller")
    family.set_sex(con, ids["Paul Miller"], "male")
    con.commit()
    return ids


def test_adoption_and_fostering_in_kin_words(con, adopted):
    ids = adopted
    g = family.load(con)
    from_gina = {g.name(p): k.label for p, k in family.relations_to(g, ids["Gina Miller"]).items()}
    assert from_gina["Paul Miller"] == "adoptive father" and from_gina["Linda Miller"] == "adoptive mother"
    assert from_gina["Carol Baker"] == "birth mother"
    assert from_gina["Ruth Miller"] == "grandmother"  # adoption makes family as birth does
    assert from_gina["Sam Miller"] == "brother" and from_gina["Max Baker"] == "foster brother"
    from_paul = {g.name(p): k.label for p, k in family.relations_to(g, ids["Paul Miller"]).items()}
    assert from_paul["Gina Miller"] == "adopted daughter" and from_paul["Sam Miller"] == "son"
    assert from_paul["Max Baker"] == "foster son" and from_paul["Owen Miller"] == "grandson"
    from_max = {g.name(p): k.label for p, k in family.relations_to(g, ids["Max Baker"]).items()}
    assert from_max["Linda Miller"] == "foster mother" and from_max["Ruth Miller"] == "foster grandmother"
    assert from_max["Gina Miller"] == "foster sister"
    # in search: "daughters" finds an adopted one, "foster" and "adopted" find them as such
    settings.put(con, "me", ids["Paul Miller"])
    index = family.word_index(con)
    assert ids["Gina Miller"] in index["daughter"] and ids["Gina Miller"] in index["adopted"]
    assert ids["Max Baker"] in index["foster"] and ids["Max Baker"] not in index.get("son", set())
    assert [k for k, _ in family.search_terms("adopted daughters 2020")] == ["adopteddaughter", "2020"]
    assert [k for k, _ in family.search_terms("foster kids")] == ["fosterchild"]


def test_two_birth_parents_and_two_adoptive_ones(con, adopted):
    ids = adopted
    gina = ids["Gina Miller"]
    family.link(con, gina, "father", people.person_id(con, "Ray Baker"))  # her birth father: two by birth now
    with pytest.raises(family.Problem, match="two parents"):
        family.link(con, gina, "parent", people.person_id(con, "Someone Else"))
    with pytest.raises(family.Problem, match="two adoptive parents"):
        family.link(con, gina, "adoptive parent", people.person_id(con, "Third Adopter"))
    family.link(con, gina, "foster mother", people.person_id(con, "Nora Foster"))  # foster parents: no limit of two
    g = family.load(con)
    assert len(g.parents[gina]) == 5 and g.how(gina, ids["Paul Miller"]) == "adopted"
    # an entered link is changed to another kind, as long as there's room for it
    family.set_pedi(con, gina, ids["Carol Baker"], "foster")
    assert family.load(con).how(gina, ids["Carol Baker"]) == "foster"
    with pytest.raises(family.Problem, match="two adoptive parents"):
        family.set_pedi(con, gina, ids["Carol Baker"], "adopted")
    # a plain word for a link already entered as adoptive leaves it so
    family.link(con, gina, "father", ids["Paul Miller"])
    assert family.load(con).how(gina, ids["Paul Miller"]) == "adopted"
    links = family.links(family.load(con), ids["Paul Miller"])
    gina_link = next(c for c in links["children"] if c["id"] == gina)
    assert gina_link["how"] == "adopted" and gina_link["with"] == [ids["Linda Miller"]]
    tree = family.tree(family.load(con), ids["Ruth Miller"])
    kids = [k for u in tree["unions"] for k in u["children"]]
    paul = next(k for k in kids if k["id"] == ids["Paul Miller"])
    assert {k["id"]: k["how"] for u in paul["unions"] for k in u["children"]}[gina] == "adopted"


# ---- the person page: life story, notes, portrait, sources, the life by age ----------------------------------------

from lineagebox import catalog, documents, events, family_check, gedcom, gedcom_import, life  # noqa: E402
from tests import family_fixture as fx  # noqa: E402
from tests.test_access_web import signed_in, site  # noqa: E402,F401 - site is a fixture


def test_anyone_who_sees_someone_writes_their_life_story(site, cfg, con):
    p = site["people"]
    cora = signed_in(cfg, con, site["who"]["Cora"])
    story = "Bart grew up on the farm.\n\nHe drove a truck for thirty years."
    r = cora.post(f"/api/people/{p['Bart']}/story", json={"biography": story}, headers=APP)
    assert r.status_code == 200 and r.json()["undo"]
    assert catalog.person(con, p["Bart"])["biography"] == story
    page = cora.get(f"/person/{p['Bart']}").text
    assert "He drove a truck for thirty years." in page and 'id="story" open' in page
    # the notes are another field: what readings and other trees put there stays apart
    r_notes = cora.post(f"/api/people/{p['Bart']}/story", json={"notes": "Obituary: a link"}, headers=APP)
    assert r_notes.status_code == 200
    assert catalog.person(con, p["Bart"])["biography"] == story and catalog.person(con, p["Bart"])["notes"]
    # not for someone she doesn't see; and her own change she can take back at once
    assert cora.post(f"/api/people/{p['Zed']}/story", json={"biography": "x"}, headers=APP).status_code == 404
    assert cora.post(f"/api/changes/{r.json()['undo']}/undo-own", headers=APP).status_code == 409  # notes came since
    con.execute("UPDATE person SET biography = ? WHERE id = ?", (story, p["Bart"]))
    con.commit()
    # GEDCOM: the story is the first NOTE, marked as such, and comes back as the story
    recs = gedcom.parse(gedcom.export(con, cfg)[0])
    bart = next(r for r in recs if r.tag == "INDI" and r.xref == f"@I{p['Bart']}@")
    note = bart.first("NOTE")
    assert note.payload == story and note.value("_BIO") == "Y"
    assert any(n.payload.startswith("_BIO urn:uuid:") for n in recs[0].first("SCHMA").all("TAG"))
    them = gedcom_import.read(gedcom.export(con, cfg)[0])["people"][f"@I{p['Bart']}@"]
    assert them["biography"] == story and them["note"] == "Obituary: a link"


def test_a_life_story_is_undone(site, cfg, con):
    p = site["people"]
    cora = signed_in(cfg, con, site["who"]["Cora"])
    r = cora.post(f"/api/people/{p['Bart']}/story", json={"biography": "A short story."}, headers=APP)
    assert cora.post(f"/api/changes/{r.json()['undo']}/undo-own", headers=APP).status_code == 200
    assert catalog.person(con, p["Bart"])["biography"] is None


def test_a_portrait_chosen_by_hand(site, cfg, con):
    p, f = site["people"], site["faces"]
    cora_face, hidden_face = f["cora_bart.jpg"][0], f["hidden.jpg"][0]
    cora = signed_in(cfg, con, site["who"]["Cora"])
    # from her named faces, in photos she sees: not from a hidden photo, nor someone else's face
    assert cora.post(f"/api/people/{p['Cora']}/portrait", json={"face_id": hidden_face}, headers=APP).status_code == 404
    assert cora.post(f"/api/people/{p['Cora']}/portrait", json={"face_id": f["cora_bart.jpg"][1]},
                     headers=APP).status_code == 404
    choices = cora.get(f"/person/{p['Cora']}/portraits").text
    assert f"/face/{cora_face}" in choices and f"/face/{hidden_face}" not in choices
    sam = signed_in(cfg, con, site["who"]["Sam"])
    assert sam.post(f"/api/people/{p['Cora']}/portrait", json={"face_id": hidden_face}, headers=APP).status_code == 200
    covers = {r["id"]: r["cover_face"] for r in catalog.people(con)}
    assert covers[p["Cora"]] == hidden_face and life.portrait(con, p["Cora"]) == hidden_face
    assert f'src="/face/{hidden_face}" alt="Cora"' in sam.get(f"/person/{p['Cora']}").text
    assert f'src="/face/{hidden_face}"' not in cora.get(f"/person/{p['Cora']}").text  # hers: one she sees
    assert cora.post(f"/api/people/{p['Cora']}/portrait", json={"face_id": cora_face}, headers=APP).status_code == 200
    assert {r["id"]: r["cover_face"] for r in catalog.people(con)}[p["Cora"]] == cora_face
    assert f"/face/{cora_face}" in sam.get("/people").text
    # a face named someone else since is no longer it
    con.execute("UPDATE face SET person_id = ? WHERE id = ?", (p["Bart"], cora_face))
    con.commit()
    assert {r["id"]: r["cover_face"] for r in catalog.people(con)}[p["Cora"]] == hidden_face


def test_a_portrait_chosen_from_a_year_or_on_a_photo(site, cfg, con):
    """Their page's portrait opens the faces to choose from, the clearest or one year's; a photo's page makes the face
    of someone named in it their portrait, and says which one is."""
    p, f, ph = site["people"], site["faces"], site["photos"]
    cora_face = f["cora_bart.jpg"][0]
    con.execute("UPDATE image SET taken_at = '2010-06-14T10:00:00' WHERE id = ?", (ph["cora_bart.jpg"],))
    con.execute("UPDATE image SET taken_at = '2003-01-01T10:00:00' WHERE id = ?", (ph["hidden.jpg"],))
    con.commit()
    sam = signed_in(cfg, con, site["who"]["Sam"])
    page = sam.get(f"/person/{p['Cora']}").text
    assert 'class="portrait-pick"' in page and 'class="portrait-change"' in page
    choices = sam.get(f"/person/{p['Cora']}/portraits").text
    assert 'data-swap="[data-on-open]"' in choices and "?year=2003" in choices and "?year=2010" in choices
    year = sam.get(f"/person/{p['Cora']}/portraits", params={"year": "2010"}).text
    assert f"/face/{cora_face}" in year and f"/face/{f['hidden.jpg'][0]}" not in year
    assert "No faces named Cora in photos from 1990" in sam.get(f"/person/{p['Cora']}/portraits?year=1990").text
    photo = sam.get(f"/image/{ph['cora_bart.jpg']}").text
    portrait = life.portrait(con, p["Cora"])
    if portrait == cora_face:  # the one shown already: said, not offered
        assert "★ portrait" in photo
    assert sam.post(f"/api/people/{p['Cora']}/portrait", json={"face_id": f["hidden.jpg"][0]},
                    headers=APP).status_code == 200
    photo = sam.get(f"/image/{ph['cora_bart.jpg']}").text
    assert f"data-body='{{\"face_id\": {cora_face}}}'" in photo and "☆ portrait" in photo
    assert sam.post(f"/api/people/{p['Cora']}/portrait", json={"face_id": cora_face}, headers=APP).status_code == 200
    assert life.portrait(con, p["Cora"]) == cora_face and "★ portrait" in sam.get(f"/image/{ph['cora_bart.jpg']}").text


def test_the_life_by_age(site, cfg, con):
    p, ph = site["people"], site["photos"]
    cora = p["Cora"]
    events.save(con, None, tag="BIRT", person_id=cora, date_text="2005-06-15")
    events.save(con, None, tag="GRAD", person_id=cora, date_text="May 2009", place="Salem")
    con.execute("UPDATE image SET taken_at = '2003-01-01T10:00:00', taken_at_source = 'exif' WHERE id = ?",
                (ph["hidden.jpg"],))
    con.execute("UPDATE image SET taken_at = '2010-06-14T10:00:00' WHERE id = ?", (ph["cora_bart.jpg"],))
    con.commit()
    by = {x.key: x for x in life.life(con, cora).periods}
    assert list(by) == ["before", "age:0", "age:3", "age:4"] and by["before"].photos == 1  # June 14th: still 4
    assert by["age:4"].photos == 1 and by["age:4"].label == "Age 4" and by["age:4"].when == "2009–10"
    assert [e["label"] for e in by["age:0"].events] == ["Born"] and by["age:0"].photos == 0
    assert [e["label"] for e in by["age:3"].events] == ["Graduated"]
    images, total = life.photos(con, cora, "age:4")
    assert [i["id"] for i in images] == [ph["cora_bart.jpg"]] and total == 1
    assert life.photos(con, cora, "age:99")[1] == 0 and life.photos(con, cora, "nonsense") == ([], 0)
    assert life.photos(con, cora, "age:9999") == ([], 0)  # past the year 9999: no such group, not an error
    sam, member = signed_in(cfg, con, site["who"]["Sam"]), signed_in(cfg, con, site["who"]["Cora"])
    page = sam.get(f"/person/{cora}").text
    assert "Life, by age" in page and "Before they were born" in page and f"/person/{cora}/life/age:4" in page
    assert f"/image/{ph['cora_bart.jpg']}?ctx=person%3A{cora}" in sam.get(f"/person/{cora}/life/age:4").text
    assert f"/image/{ph['cora_bart.jpg']}" in sam.get(f"/search?people={cora}&sort=oldest").text  # All in one list
    # a member's: only photos she sees (not the hidden one), and nobody she doesn't see
    assert "Before they were born" not in member.get(f"/person/{cora}").text
    assert member.get(f"/person/{p['Zed']}/life/age:4").status_code == 404
    # without a birth date: by year
    con.execute("DELETE FROM event WHERE person_id = ? AND tag = 'BIRT'", (cora,))
    con.commit()
    assert [x.key for x in life.life(con, cora).periods] == ["year:2003", "year:2009", "year:2010"]


def test_written_sources(site, cfg, con):
    p = site["people"]
    bart = p["Bart"]
    birth = events.save(con, None, tag="BIRT", person_id=bart, date_text="1980")
    con.commit()
    cora = signed_in(cfg, con, site["who"]["Cora"])
    body = {"title": "Salem register of births", "author": "Marion County", "page": "page 12, line 4",
            "target": f"e:{birth}"}
    r = cora.post(f"/api/people/{bart}/sources", json=body, headers=APP)
    assert r.status_code == 200 and r.json()["undo"]
    r = cora.post(f"/api/people/{bart}/sources", json={"title": "salem register of births", "target": f"p:{bart}"},
                  headers=APP)
    assert r.status_code == 200 and con.execute("SELECT count(*) FROM source").fetchone()[0] == 1  # the same one
    cited = documents.sources_for(con, bart)
    assert [(c["what"], c["page"]) for c in cited] == [("them", None), ("Born 1980", "page 12, line 4")]
    page = cora.get(f"/person/{bart}").text
    assert "Salem register of births" in page and "page 12, line 4" in page
    # an event of someone she doesn't see, or someone else's on his page: refused
    zed_birth = con.execute("SELECT id FROM event WHERE person_id = ?", (p["Zed"],)).fetchone()[0]
    for target in (f"e:{zed_birth}", f"p:{p['Zed']}"):
        assert cora.post(f"/api/people/{bart}/sources", json={"title": "x", "target": target},
                         headers=APP).status_code == 404
    source_id = cited[0]["source_id"]
    assert cora.post(f"/api/sources/{source_id}", json={"title": "Salem births, 1980", "note": "Copied out"},
                     headers=APP).status_code == 200
    # GEDCOM: a SOUR record, cited with where in it
    recs = gedcom.parse(gedcom.export(con, cfg)[0])
    by = {x.xref: x for x in recs if x.xref}
    born = by[f"@I{bart}@"].first("BIRT")
    sour = by[born.value("SOUR")]
    assert sour.value("TITL") == "Salem births, 1980" and sour.value("UID") and sour.value("NOTE") == "Copied out"
    assert born.first("SOUR").value("PAGE") == "page 12, line 4" and by[f"@I{bart}@"].value("SOUR") == sour.xref
    # taking the last citation off takes the source with it
    for c in documents.sources_for(con, bart):
        assert cora.post(f"/api/source-citations/{c['id']}/delete", headers=APP).status_code == 200
    assert con.execute("SELECT count(*) FROM source").fetchone()[0] == 0
    # a member sees only the sources cited for people she sees
    other = documents.save_source(con, None, f"{fx.SECRET} diary")
    documents.cite_source(con, other, f"p:{p['Zed']}")
    con.commit()
    assert fx.SECRET not in cora.get(f"/person/{bart}").text


def test_photos_that_dont_fit_the_dates(site, cfg, con):
    p, ph = site["people"], site["photos"]
    events.save(con, None, tag="BIRT", person_id=p["Cora"], date_text="2011")  # cora_bart.jpg is from 2010
    events.save(con, None, tag="DEAT", person_id=p["Rex"], date_text="2005")  # rex.jpg is from 2011
    events.save(con, None, tag="BIRT", person_id=p["Sam"], date_text="2013")
    con.execute("UPDATE image SET taken_at = '2012-05-01T10:00:00' WHERE id = ?", (ph["hank.jpg"],))
    con.execute("INSERT INTO parent(child_id, parent_id) VALUES (?, ?)", (p["Hank"], p["Sam"]))  # a wrong link
    con.commit()
    found = [i for i in family_check.check(con) if i.kind == "photos"]
    texts = {i.text for i in found}
    assert "Cora: in a photo from 2010, before being born in 2011" in texts
    assert "Rex: in a photo from 2011, years after dying in 2005" in texts
    assert "Hank: in a photo from 2012, before Sam (a parent) was born in 2013" in texts
    assert all(i.href.startswith("/search?people=") for i in found)
    page = signed_in(cfg, con, site["who"]["Sam"]).get("/cleanup/family").text
    assert "Photos that don&#39;t fit the dates" in page and "see the photos" in page
    # fine as it is, until another such photo is named
    cora_issue = next(i for i in found if i.people == [p["Cora"]])
    family_check.mark_ok(con, cora_issue.key)
    assert cora_issue.key not in {i.key for i in family_check.check(con)}
    con.execute("UPDATE image SET taken_at = '2009-05-01T10:00:00' WHERE id = ?", (ph["jim.jpg"],))
    con.execute("INSERT INTO image_person(image_id, person_id, added_at) VALUES (?, ?, '2026-01-01')",
                (ph["jim.jpg"], p["Cora"]))
    con.commit()
    again = [i for i in family_check.check(con) if i.kind == "photos" and i.people == [p["Cora"]]]
    assert again and again[0].text.startswith("Cora: in 2 photos from 2009–2010")
    # a file's time says nothing
    con.execute("UPDATE image SET taken_at_source = 'mtime' WHERE id IN (?, ?)", (ph["cora_bart.jpg"], ph["jim.jpg"]))
    con.commit()
    assert not [i for i in family_check.check(con) if i.kind == "photos" and p["Cora"] in i.people]


# ---- GEDCOM: a deeper tree out and back in ---------------------------------------------------------------------------

from lineagebox import db  # noqa: E402
from lineagebox.config import Config  # noqa: E402

ELSEWHERE = """0 HEAD
1 SOUR ANCESTRY
1 GEDC
2 VERS 5.5.1
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @P1@ INDI
1 NAME Walter /Baker/
1 SEX M
1 OCCU Carpenter
1 RESI
2 PLAC Salem, Oregon, USA
3 MAP
4 LATI N44.9429
4 LONG W123.0351
1 NOTE @N1@
1 SOUR @S1@
2 PAGE Sheet 4B, line 12
1 OBJE
2 FORM jpg
2 FILE C:\\Users\\someone\\Pictures\\Zoo\\DSC_0001.jpg
2 TITL At the zoo
1 OBJE @M1@
1 FAMS @F1@
0 @P2@ INDI
1 NAME Ruth /Baker/
1 SEX F
1 BIRT
2 DATE 1931
2 SOUR Family Bible of Ruth Baker
3 PAGE front page
1 FAMS @F1@
0 @P3@ INDI
1 NAME Gina /Baker/
1 SEX F
1 ADOP
2 DATE 1962
2 FAMC @F1@
1 BARM
2 DATE 1975
1 FAMC @F1@
0 @P4@ INDI
1 NAME Max /Foster/
1 SEX M
1 FAMC @F1@
2 PEDI foster
0 @F1@ FAM
1 HUSB @P1@
1 WIFE @P2@
1 CHIL @P3@
1 CHIL @P4@
1 MARR
2 DATE 1955
0 @S1@ SOUR
1 TITL 1940 United States Federal Census
1 AUTH United States of America, Bureau of the Census
1 PUBL National Archives and Records Administration
0 @M1@ OBJE
1 FILE https://example.com/trees/walter-wedding.jpg
2 FORM jpg
3 TYPE photo
2 TITL Walter's wedding
0 @N1@ NOTE He built the barn himself.
0 TRLR
"""


def test_a_tree_from_elsewhere_brings_its_sources_media_and_places(con, cfg):
    from lineagebox.ingest import ingest
    from lineagebox.scan import scan
    scan(con, cfg)
    ingest(con, cfg)
    zoo = con.execute("SELECT image_id FROM file WHERE path LIKE '%2012/Zoo/DSC_0001.jpg'").fetchone()[0]
    import_id = gedcom_import.start(con, "ancestry.ged", ELSEWHERE.encode())
    report = gedcom_import.apply(con, import_id)
    pid = lambda name: con.execute("SELECT id FROM person WHERE name = ?", (name,)).fetchone()[0]
    walter, ruth, gina, max_ = pid("Walter Baker"), pid("Ruth Baker"), pid("Gina Baker"), pid("Max Foster")
    g = family.load(con)
    assert g.how(gina, walter) == "adopted" and g.how(max_, ruth) == "foster"  # ADOP.FAMC, and FAMC.PEDI
    assert family.relations_to(g, gina)[ruth].label == "adoptive mother"
    occu = con.execute("SELECT type FROM event WHERE person_id = ? AND tag = 'OCCU'", (walter,)).fetchone()
    assert occu[0] == "Carpenter"  # the payload, as 7.0 and 5.5.1 write it
    assert [r[0] for r in con.execute("SELECT tag FROM event WHERE person_id = ? AND tag IN ('ADOP', 'BARM') "
                                      "ORDER BY tag", (gina,))] == ["ADOP", "BARM"]
    assert catalog.person(con, walter)["notes"] == "He built the barn himself."  # a shared note
    # its sources, cited with where in them; the one written in place too
    cited = {(c["title"], c["page"], c["what"]) for c in documents.sources_for(con, walter)}
    assert cited == {("1940 United States Federal Census", "Sheet 4B, line 12", "them")}
    assert [(c["title"], c["page"], c["what"]) for c in documents.sources_for(con, ruth)] == [
        ("Family Bible of Ruth Baker", "front page", "Born 1931")]
    assert report["sources"] == 1 and report["citations"] == 2
    # the zoo photo is in the library (a camera's name, found by its folder too): Walter is added to it
    assert con.execute("SELECT 1 FROM image_person WHERE image_id = ? AND person_id = ?", (zoo, walter)).fetchone()
    kept = con.execute("SELECT file, title FROM tree_media WHERE person_id = ?", (walter,)).fetchall()
    assert [tuple(k) for k in kept] == [("https://example.com/trees/walter-wedding.jpg", "Walter's wedding")]
    assert report["photos_found"] == 1 and report["files_kept"] == 1
    # the place, on the map from the file's coordinates
    at = con.execute("SELECT lat, lon, source FROM place WHERE name = 'Salem, Oregon, USA'").fetchone()
    assert (round(at[0], 4), round(at[1], 4), at[2]) == (44.9429, -123.0351, "gedcom") and report["places"] == 1
    # brought in again: nothing doubles
    tables = ("person", "parent", "event", "source", "source_citation", "tree_media", "image_person", "place")
    counts = [con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables]
    again = gedcom_import.start(con, "ancestry.ged", ELSEWHERE.encode())
    for x, m in gedcom_import.load(con, again)[2].items():
        assert m["choice"], x  # matched by name now
    gedcom_import.apply(con, again)
    assert [con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables] == counts
    # and out again: the links kept as the other tree's, the coordinates, the attribute's value, the adoption
    recs = gedcom.parse(gedcom.export(con, cfg)[0])
    by = {r.xref: r for r in recs if r.xref}
    out_walter = by[f"@I{walter}@"]
    assert out_walter.value("OCCU") == "Carpenter"
    assert out_walter.first("RESI").first("PLAC").first("MAP").value("LATI") == "N44.942900"
    files = [by[o.payload].value("FILE") for o in out_walter.all("OBJE")]
    assert files == ["https://example.com/trees/walter-wedding.jpg"]
    famc = {f.payload: f.value("PEDI") for f in by[f"@I{gina}@"].all("FAMC")}
    assert list(famc.values()) == ["ADOPTED"]
    assert [f.value("PEDI") for f in by[f"@I{max_}@"].all("FAMC")] == ["FOSTER"]


def test_an_export_comes_back_whole_in_another_catalog(con, cfg, tmp_path, adopted):
    """Everything a LineageBox export carries, brought into an empty catalog: stories, adoptions, sources, events."""
    ids = adopted
    gina, paul = ids["Gina Miller"], ids["Paul Miller"]
    con.execute("UPDATE person SET biography = 'Gina loved the sea.', notes = 'Named in: a letter' WHERE id = ?",
                (gina,))
    occu = events.save(con, None, tag="OCCU", person_id=paul, kind="Teacher", date_text="1990", place="Salem")
    events.save(con, None, tag="RESI", person_id=paul, place="Portland, Oregon, USA")
    events.save(con, None, tag="CENS", person_id=paul, date_text="1950")
    src = documents.save_source(con, None, "School records", author="Salem schools")
    documents.cite_source(con, src, f"e:{occu}", "page 3")
    con.execute("INSERT INTO place(name, lat, lon, source, looked_up) VALUES ('Salem', 44.9, -123.0, 'user', 'x')")
    con.commit()
    for version in ("7.0", "5.5.1"):
        other = Config(share_root=cfg.share_root, folders=cfg.folders, data_dir=tmp_path / f"other-{version}",
                       update_repository="")
        there = db.connect(other)
        try:
            import_id = gedcom_import.start(there, "back.ged", gedcom.export(con, cfg, version)[0])
            gedcom_import.apply(there, import_id)
            pid = lambda name: there.execute("SELECT id FROM person WHERE name = ?", (name,)).fetchone()[0]
            g = family.load(there)
            assert g.how(pid("Gina Miller"), pid("Paul Miller")) == "adopted"
            assert g.how(pid("Max Baker"), pid("Linda Miller")) == "foster"
            row = there.execute("SELECT biography, notes FROM person WHERE id = ?", (pid("Gina Miller"),)).fetchone()
            if version == "7.0":  # 5.5.1 has no place for the mark: the story comes back as a note
                assert tuple(row) == ("Gina loved the sea.", "Named in: a letter")
            else:
                assert row[1] and "Gina loved the sea." in row[1]
            evts = {(e["tag"], e["type"], e["place"]) for e in events.for_person(there, pid("Paul Miller"))}
            assert {("OCCU", "Teacher", "Salem"), ("RESI", None, "Portland, Oregon, USA"), ("CENS", None, None)} <= evts
            assert [(c["title"], c["page"], c["what"]) for c in documents.sources_for(there, pid("Paul Miller"))] == [
                ("School records", "page 3", "Worked as 1990")]
            assert there.execute("SELECT lat FROM place WHERE name = 'Salem'").fetchone()[0] == 44.9
        finally:
            there.close()


def test_the_reader_keeps_adopted_children_adopted(con):
    import copy
    import json
    from lineagebox import family_reader as fr
    answer = json.loads(fr.EXTRACT_SYSTEM.split("Answer:\n", 1)[1])
    james = next(p for p in answer["people"] if p["given"] == "James")
    james["is"] = "adopted son"
    lily = copy.deepcopy(next(p for p in answer["people"] if p["given"] == "Alice"))
    lily.update(id="p13", given="Lily", surname="Miller", **{"is": "adopted daughter", "of": "p1"})
    answer["people"].append(lily)
    peggy = con.execute("INSERT INTO person(name, sex, given_name, surname, names_checked) "
                        "VALUES ('Peggy Miller', 'female', 'Margaret', 'Miller', 1)").lastrowid
    jim = con.execute("INSERT INTO person(name, sex, given_name, surname, names_checked) "
                      "VALUES ('James Miller', 'male', 'James', 'Miller', 1)").lastrowid
    con.execute("INSERT INTO parent(child_id, parent_id) VALUES (?, ?)", (jim, peggy))
    events.save(con, None, tag="BIRT", person_id=peggy, date_text="1931")
    con.commit()
    found = fr.tidy(answer)
    choices = fr.match(con, found)
    proposal = fr.propose(con, found, choices)
    texts = {c.text: c for c in proposal.changes}
    assert texts["Lily Miller is Peggy Miller's adopted daughter"].on
    fix = texts["James Miller is Peggy Miller's adopted son"]
    assert not fix.on and "by birth" in fix.now  # what the tree has differs: left for someone to tick
    fr.apply(con, found, choices, {c.key for c in proposal.changes if c.on} | {fix.key})
    g = family.load(con)
    lily_id = con.execute("SELECT id FROM person WHERE given_name = 'Lily'").fetchone()[0]
    assert g.how(lily_id, peggy) == "adopted" and g.how(jim, peggy) == "adopted"


def test_the_export_carries_life_stories_portraits_adoptions_and_sources(site, cfg, con):
    import io
    import zipfile
    from lineagebox import export
    p, f = site["people"], site["faces"]
    con.execute("UPDATE person SET biography = 'Cora sang.', cover_face_id = ? WHERE id = ?",
                (f["cora_bart.jpg"][0], p["Cora"]))
    family.set_pedi(con, p["Cora"], p["Rex"], "adopted")
    src = documents.save_source(con, None, "Parish register", author="St Mary's")
    documents.cite_source(con, src, f"p:{p['Cora']}", "folio 3")
    con.commit()
    buf = io.BytesIO()
    counts = export.write(con, cfg, buf)
    data = json.loads(zipfile.ZipFile(buf).read(export.PHOTOS))
    cora = next(x for x in data["people"] if x["id"] == p["Cora"])
    assert cora["life_story"] == "Cora sang." and cora["parents_not_by_birth"] == [{"person": p["Rex"], "as": "adopted"}]
    sha = catalog.image(con, site["photos"]["cora_bart.jpg"])["sha256"]
    assert cora["portrait"]["photo"] == sha and len(cora["portrait"]["box"]) == 4
    assert data["sources"] == [{"title": "Parish register", "author": "St Mary's", "uid": data["sources"][0]["uid"],
                                "gedcom": f"@T{src}@", "cited_for": [{"person": p["Cora"], "page": "folio 3"}]}]
    assert counts["sources"] == 1
    assert "life_story" not in next(x for x in data["people"] if x["id"] == p["Bart"])  # only what someone has


def test_a_file_kept_from_another_tree_is_the_same_file_when_it_comes_back():
    assert gedcom_import._file(r"C:\Users\someone\Pictures\Ruth.jpg") == "C:/Users/someone/Pictures/Ruth.jpg"
    assert gedcom_import._file("file:///C:/Users/someone/Pictures/Ruth.jpg") == "C:/Users/someone/Pictures/Ruth.jpg"
    assert gedcom_import._file("https://example.com/ruth.jpg") == "https://example.com/ruth.jpg"


def test_a_birth_in_year_0000_from_an_older_import_does_not_break_the_page(cfg, con, library):
    """Trees imported before the import checked dates may hold year 0000 or 9999 as a sort date: the person's page
    still opens, the date shown as written and placed nowhere."""
    from fastapi.testclient import TestClient
    from lineagebox import life
    from lineagebox.web import app as webapp
    from tests.conftest import LAN
    pid = con.execute("INSERT INTO person(name) VALUES ('Old Ezra Miller')").lastrowid
    con.execute("INSERT INTO event(person_id, tag, date, sort_date) VALUES (?, 'BIRT', '0000', '0000')", (pid,))
    con.execute("INSERT INTO event(person_id, tag, date, sort_date) VALUES (?, 'DEAT', '9999', '9999')", (pid,))
    con.commit()
    assert life.life_dates(con, pid) == (None, None)
    assert TestClient(webapp.create_app(cfg), client=LAN).get(f"/person/{pid}").status_code == 200
