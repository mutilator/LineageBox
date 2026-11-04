import json
import re
from datetime import date

import numpy as np
import pytest
from fastapi.testclient import TestClient

from lineagebox import events, family, people, settings, tour_suggestions, tours
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests.conftest import LAN, rel, write_jpeg

SHOTS = {  # file: (taken, who is in it)
    "dec22.jpg": ("2022:12:20 10:00:00", ["Owen"]),
    "mar22.jpg": ("2022:03:03 10:00:00", ["Owen"]),
    "xmas23.jpg": ("2023:12:25 10:00:00", ["Owen"]),
    "jan24.jpg": ("2024:01:10 10:00:00", ["Owen", "Lucas"]),
    "jun24.jpg": ("2024:06:01 10:00:00", ["Lucas"]),
}
APP = {"X-Requested-With": "lineagebox"}


@pytest.fixture
def tlib(con, cfg, library):
    """Five dated photos of Daniel's sons Owen and Lucas; Daniel is "me"."""
    for i, (name, (taken, _)) in enumerate(SHOTS.items()):
        write_jpeg(library / "Tours" / name, size=(200, 200), color=(40 * i, 60, 90), taken=taken)
    scan(con, cfg)
    ingest(con, cfg)
    ids = {name: people.person_id(con, name) for name in ("Daniel", "Owen", "Lucas")}
    rng = np.random.default_rng(3)
    for name, (_, who) in SHOTS.items():
        image_id = con.execute("SELECT image_id FROM file WHERE path = ?", (rel("Tours", name),)).fetchone()[0]
        con.execute("UPDATE image SET sharpness = 100 WHERE id = ?", (image_id,))  # flat test colours look blurry
        for x, person in enumerate(who):
            emb = rng.standard_normal(512).astype(np.float32)
            con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding, person_id,
                                            assigned_by) VALUES (?, ?, 0, ?, 1, 0.9, 22, ?, ?, 'user')""",
                        (image_id, x / 2, x / 2 + 0.5, (emb / np.linalg.norm(emb)).tobytes(), ids[person]))
    con.commit()
    family.link(con, ids["Daniel"], "son", ids["Owen"])
    family.link(con, ids["Daniel"], "son", ids["Lucas"])
    settings.put(con, "me", ids["Daniel"])
    events.save(con, None, tag="BIRT", person_id=ids["Owen"], date_text="2011-12-28")  # as his page enters it
    con.commit()
    return ids


def kind(con, name):
    return next(t for t in tours.types(con) if t["name"] == name)


def names(con, chosen):
    return [con.execute("SELECT f.path FROM file f WHERE f.image_id = ?", (p["id"],)).fetchone()[0].rpartition("/")[2]
            for p in chosen]


def test_the_wizard_asks_what_a_type_leaves_open(con, tlib):
    season = kind(con, "A season")
    assert tours.questions(season["spec"]) == ["season", "year"]
    with pytest.raises(tours.Problem, match="season"):
        tours.answer(season["spec"], {"year": "2023"})
    spec = tours.answer(season["spec"], {"season": "winter", "year": "2023"})
    assert tours.title(con, spec) == "Winter 2023"
    assert names(con, tours.photos(con, spec)) == ["xmas23.jpg", "jan24.jpg"]  # December into February

    spec = tours.answer(kind(con, "Relatives in a year")["spec"], {"relation": "son", "year": "2024"})
    assert tours.title(con, spec) == "Sons 2024"
    assert names(con, tours.photos(con, spec)) == ["jan24.jpg", "jun24.jpg"]
    assert ("son", "Sons", 2) in tours.relation_choices(con)

    spec = tours.answer(kind(con, "Together")["spec"], {"people": [tlib["Owen"], tlib["Lucas"]]})
    assert tours.title(con, spec) == "Owen and Lucas together"
    assert names(con, tours.photos(con, spec)) == ["jan24.jpg"]


def test_picking(con, tlib):
    years = tours.answer(kind(con, "Throughout the years")["spec"], {"person": tlib["Owen"]})
    years["pick"]["count"] = 1
    assert [p["taken"][:4] for p in tours.photos(con, years)] == ["2022", "2023", "2024"]
    birthdays = tours.answer(kind(con, "Birthdays")["spec"], {"person": tlib["Owen"]})
    birthdays["words"] = ""  # the test photos have no descriptions
    assert names(con, tours.photos(con, birthdays)) == ["dec22.jpg", "xmas23.jpg", "jan24.jpg"]  # 28 Dec ± 21 days
    slide = tours.slides(con, tours.photos(con, birthdays))[0]
    assert slide["when"] == "December 20, 2022" and slide["people"] == "Owen (10)"
    assert family.plural("brother-in-law") == "brothers-in-law" and family.plural("child") == "children"


def test_tour_pages(con, cfg, tlib):
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get("/tours").text
    season = kind(con, "A season")
    assert f'href="/tours/new?type={season["id"]}"' in page and "asks for season, year" in page
    wizard = client.get(f"/tours/new?type={season['id']}").text
    assert 'name="season"' in wizard and 'name="year"' in wizard and 'name="person"' not in wizard
    answers = {"type": str(season["id"]), "season": "winter", "year": "2023"}
    preview = client.post("/api/tours/preview", json=answers, headers=APP).json()
    assert preview == {"count": 2, "name": "Winter 2023", "message": "2 photos"}
    r = client.post("/api/tours", json={**answers, "name": ""}, headers=APP)
    tour_url = r.json()["go"]
    tour_page = client.get(tour_url).text
    assert "<h1>Winter 2023</h1>" in tour_page and "data-play" in tour_page
    slides = json.loads(re.search(r'<script type="application/json" id="slides">(.*?)</script>', tour_page, re.S)[1])
    assert [s["when"] for s in slides] == ["December 25, 2023", "January 10, 2024"]
    tour_id = int(tour_url.rpartition("/")[2])
    photo = client.get(f"/image/{slides[0]['id']}?ctx=tour:{tour_id}").text
    assert f'href="/tour/{tour_id}" data-back' in photo and f"/image/{slides[1]['id']}?ctx=tour%3A{tour_id}" in photo
    assert 'value="winter" required checked' in client.get(f"{tour_url}/edit").text
    client.post(f"/api/tours/{tour_id}", json={"season": "summer", "year": "2024", "name": "Summer fun"}, headers=APP)
    assert "<h1>Summer fun</h1>" in client.get(tour_url).text
    assert client.post(f"/api/tours/{tour_id}/delete", headers=APP).json() == {"go": "/tours"}
    assert client.get(tour_url).status_code == 404


def test_tour_types_on_the_settings_page(con, cfg, tlib):
    client = TestClient(webapp.create_app(cfg), client=LAN)
    assert "Year in review" in client.get("/settings").text
    for t in tours.types(con):  # every kind of question and setting renders
        assert client.get(f"/tours/new?type={t['id']}").status_code == 200
        assert client.get(f"/settings/tour-types/{t['id']}").status_code == 200
    assert 'name="who"' in client.get("/settings/tour-types/new").text
    form = {"name": "Brothers by year", "title": "{relation} {year}", "who": "relation", "relation": "son",
            "flags": ["with_children"], "when": "year", "year": "", "pick": "spread", "max": "50", "count": "2",
            "media": "both", "order": "date", "seconds": "6", "person": str(tlib["Owen"])}
    assert client.post("/api/tour-types", json=form, headers=APP).json() == {"go": "/settings#tours"}
    made = kind(con, "Brothers by year")
    assert made["spec"]["who"] == {"mode": "relation", "person_ids": [], "relation": "son", "together": False,
                                   "with_partners": False, "with_children": True}
    assert tours.questions(made["spec"]) == ["year"]
    bad = client.post("/api/tour-types", json={**form, "seconds": "600"}, headers=APP)
    assert bad.status_code == 400 and "Seconds" in bad.json()["detail"]


def tag(con, tags: dict[str, str]):
    """Give the test photos tags, in the search too: {file: "tag, tag"}."""
    for name, given in tags.items():
        image_id = con.execute("SELECT image_id FROM file WHERE path = ?", (rel("Tours", name),)).fetchone()[0]
        con.execute("INSERT INTO search_fts(rowid, image_id, tags) VALUES (?, ?, ?)", (image_id, image_id, given))
        for t in given.split(", "):
            con.execute("INSERT OR IGNORE INTO tag(name) VALUES (?)", (t,))
            con.execute("INSERT INTO image_tag(image_id, tag_id, source) SELECT ?, id, 'ai' FROM tag WHERE name = ?",
                        (image_id, t))
    con.commit()


def test_suggested_tours_come_from_what_the_catalog_holds(con, tlib, monkeypatch):
    monkeypatch.setattr(tour_suggestions, "OCCASION_PHOTOS", 2)  # the test library is five photos
    monkeypatch.setattr(tour_suggestions, "OCCASION_YEARS", 2)
    tag(con, {"dec22.jpg": "christmas morning, birthday cake, outdoor", "xmas23.jpg": "christmas morning, outdoor",
              "jun24.jpg": "beach, outdoor", "mar22.jpg": "outdoor", "jan24.jpg": "march"})
    found = tour_suggestions.suggest(con, today=date(2024, 12, 10), least=1)
    assert len(found) >= 3
    names = [s["name"] for s in found]
    # what comes up soonest first: a tag on photos from late December every year, then Owen's birthday on the 28th
    assert names[:2] == ["Christmas morning through the years", "Owen's birthdays"]
    assert found[0]["why"] == "Coming up: around December 23, in photos from 2 years" and found[0]["count"] == 2
    assert found[0]["body"] == {"spec": found[0]["spec"]} and found[0]["spec"]["words"] == '"christmas morning"'
    assert found[1]["why"] == "Birthday in 18 days"
    assert found[1]["body"] == {"type": kind(con, "Birthdays")["id"], "person": tlib["Owen"]}
    # then the most popular answers to the other kinds' questions, from the photos themselves
    assert "Winter 2023" in names and "Owen and Lucas together" in names
    assert next(s for s in found if s["name"] == "Winter 2023")["why"] == \
        "This time of year: the winter with the most photos"
    # "outdoor" is on any day of the year, "beach" on one photo, "march" names a month: none of them is an occasion
    assert not {"Outdoor through the years", "Beach through the years", "March through the years"} & set(names)
    assert all(s["covers"] and s["count"] >= 1 for s in found)
    assert tours.word_list('"christmas morning"  cake "" "') == ["christmas morning", "cake"]
    assert tours.describe(con, found[0]["spec"])[0] == "about: christmas morning"


def test_suggested_tours_on_the_tours_page(con, cfg, tlib, monkeypatch):
    monkeypatch.setattr(tour_suggestions, "LEAST", 1)
    client = TestClient(webapp.create_app(cfg), client=LAN)
    assert 'data-later="/tours/suggested"' in client.get("/tours").text  # worked out after the page is shown
    def bodies():
        part = client.get("/tours/suggested").text
        assert "Make this tour" in part
        return [json.loads(b) for b in re.findall(r"data-body='([^']*)'", part)]

    suggested = bodies()
    assert len(suggested) >= 3
    together = next(b for b in suggested if b.get("type") == kind(con, "Together")["id"])
    assert together["people"] == [tlib["Owen"], tlib["Lucas"]]
    tour_url = client.post("/api/tours", json=together, headers=APP).json()["go"]
    assert "<h1>Owen and Lucas together</h1>" in client.get(tour_url).text
    after = bodies()  # made: not suggested again, and the next one kept moves up
    assert together not in after and after[:len(suggested) - 1] == [b for b in suggested if b != together]

    tag(con, {"dec22.jpg": "christmas morning", "xmas23.jpg": "christmas morning"})
    spec = tours.clean({"title": "Christmas morning through the years", "words": '"christmas morning"',
                        "pick": {"mode": "per_year", "count": 5}})
    tour_url = client.post("/api/tours", json={"spec": spec}, headers=APP).json()["go"]  # an occasion: no kind asks
    assert "<h1>Christmas morning through the years</h1>" in client.get(tour_url).text
    assert "only its name can change" in client.get(f"{tour_url}/edit").text
