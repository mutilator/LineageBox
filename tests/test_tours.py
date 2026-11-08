import json
import re
from datetime import date

import numpy as np
import pytest
from fastapi.testclient import TestClient

from lineagebox import (access, events, family, favourites, memories, people, settings, slideshow,
                        tour_suggestions, tours, video)
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
    title, *slides = json.loads(re.search(r'<script type="application/json" id="slides">(.*?)</script>', tour_page,
                                          re.S)[1])
    assert title == {"title": "Winter 2023", "span": "December 25, 2023 to January 10, 2024", "about": "",
                     "count": "2 photos"}  # the title card comes first
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


def _image(con, name):
    return con.execute("SELECT image_id FROM file WHERE path = ?", (rel("Tours", name),)).fetchone()[0]


def test_dates_on_slides_are_as_exact_as_they_are_known(con, tlib):
    """A date set by hand as a season shows as it was said, a folder's year as the year; a camera's, the day."""
    owen = tours.clean({"title": "", "who": {"mode": "person", "person_ids": [tlib["Owen"]]}})
    con.execute("""UPDATE image SET taken_at = '1987-07-16T12:00:00', taken_at_source = 'user', date_text = 'summer 1987',
                   date_from = '1987-06-01', date_to = '1987-08-31' WHERE id = ?""", (_image(con, "dec22.jpg"),))
    con.execute("UPDATE image SET taken_at = '1990-01-01T00:00:00', taken_at_source = 'folder' WHERE id = ?",
                (_image(con, "mar22.jpg"),))
    con.execute("UPDATE image SET taken_at = '1991-06-01T00:00:00', taken_at_source = 'folder' WHERE id = ?",
                (_image(con, "xmas23.jpg"),))
    con.commit()
    chosen = tours.photos(con, owen)
    assert [s["when"] for s in tours.slides(con, chosen)] == ["Summer 1987", "1990", "June 1991", "January 10, 2024"]
    card = tours.title_card("Owen", chosen)
    assert card["span"] == "1987 to 2024" and card["count"] == "4 photos"
    assert tours.span_words(["2024-06-01T10:00", "2024-08-20T10:00"]) == "June to August 2024"
    assert tours.span_words(["2023-12-25T10:00", "2024-01-10T10:00"]) == "December 25, 2023 to January 10, 2024"
    assert tours.span_words([None]) == ""


def test_favourites_come_first(con, cfg, tlib):
    """A heart wins a photo its place in a year, and a memory's cover. Each account has its own hearts."""
    years = tours.answer(kind(con, "Throughout the years")["spec"], {"person": tlib["Owen"]})
    years["pick"]["count"] = 1
    in_2022 = lambda: [p["id"] for p in tours.photos(con, years) if p["taken"][:4] == "2022"]  # noqa: E731
    first = in_2022()
    loved = ({_image(con, "dec22.jpg"), _image(con, "mar22.jpg")} - set(first)).pop()  # the one not picked
    name = "dec22.jpg" if loved == _image(con, "dec22.jpg") else "mar22.jpg"
    client = TestClient(webapp.create_app(cfg), client=LAN)  # at home: one set of hearts, of nobody in particular
    r = client.post(f"/api/images/{loved}/favourite", json={"on": True}, headers=APP)
    assert r.json() == {"on": True}
    assert in_2022() == [loved]
    picked = tours.photos(con, years)
    assert memories.cover(picked, date(2026, 1, 1)) == next(p["sha"] for p in picked if p["hearts"])
    assert 'aria-pressed="true"' in client.get(f"/image/{loved}").text
    assert favourites.mine(con, access.EVERYONE) == [loved]
    assert "Your favourites" in client.get("/albums").text and _sha(con, name) in client.get("/favourites").text
    client.post(f"/api/images/{loved}/favourite", json={"on": False}, headers=APP)
    assert favourites.mine(con, access.EVERYONE) == [] and in_2022() == first
    # an account's heart is its own
    a, b = [access.Viewer(account={"id": con.execute("INSERT INTO account(email, role, created_at) VALUES (?, 'member',"
                                                     " 'x')", (f"{n}@example.com",)).lastrowid,
                                   "person_id": None, "role": "member"}, admin=False) for n in ("a", "b")]
    favourites.put(con, a, _image(con, "jun24.jpg"), True)
    assert favourites.has(con, a, _image(con, "jun24.jpg")) and not favourites.has(con, b, _image(con, "jun24.jpg"))


def _sha(con, name):
    return con.execute("SELECT sha256 FROM image WHERE id = ?", (_image(con, name),)).fetchone()[0]


def test_save_as_a_video(con, cfg, tlib):
    """Asking keeps the slides as the viewer sees them and wakes the worker; the worker makes an MP4 in the data folder
    with a title card, and the page offers it for download."""
    client = TestClient(webapp.create_app(cfg), client=LAN)
    owen = tours.answer(kind(con, "Throughout the years")["spec"], {"person": tlib["Owen"]})
    owen["seconds"] = 1
    tour_id = tours.save_tour(con, None, None, owen, "Owen's years")
    assert "Save as a video" in client.get(f"/tour/{tour_id}").text
    assert client.get(f"/download/tour/{tour_id}/video").status_code == 404
    assert client.post(f"/api/tours/{tour_id}/video", headers=APP).json()["reload"]
    asked = con.execute("SELECT * FROM slideshow_video").fetchone()
    shown = json.loads(asked["slides"])
    assert shown[0]["title"] == "Owen's years" and len(shown) == 5 and asked["state"] == "waiting"
    assert settings.get(con, slideshow.WAKE)  # the worker starts a pass for it
    page = client.get(f"/tour/{tour_id}").text
    assert "waiting its turn" in page and f'data-live="/tour/{tour_id}/video"' in page
    client.post(f"/api/tours/{tour_id}/video", headers=APP)  # asking again replaces it
    assert con.execute("SELECT count(*) FROM slideshow_video").fetchone()[0] == 1
    if not video.available():
        pytest.skip("ffmpeg isn't installed")
    assert slideshow.make_waiting(con, cfg) == 1
    made = slideshow.latest(con, None, "tour", tour_id)
    assert made["state"] == "done" and made["bytes"] > 1000
    assert "data-reload-page" in client.get(f"/tour/{tour_id}/video").text  # the page loads again to offer it
    r = client.get(f"/download/tour/{tour_id}/video")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4" and r.content[4:8] == b"ftyp"
    assert "Owen" in r.headers["content-disposition"]
    probe = video.probe(slideshow.folder(cfg) / made["file"])
    assert (probe.width, probe.height) == (slideshow.WIDTH, slideshow.HEIGHT)
    # a title card of 4 s, 4 photos of 1 s, and 4 crossfades
    assert abs(probe.duration - (slideshow.TITLE_SECONDS + 4 + 4 * slideshow.FADE)) < 0.5
    # deleting the tour takes its video with it, and the worker's tidying its file
    client.post(f"/api/tours/{tour_id}/delete", headers=APP)
    assert slideshow.tidy(con, cfg) == 1 and not list(slideshow.folder(cfg).iterdir())


def test_a_video_too_long_is_refused(con, cfg, tlib, monkeypatch):
    client = TestClient(webapp.create_app(cfg), client=LAN)
    tour_id = tours.save_tour(con, None, None, tours.clean({"title": "All", "seconds": 60}), "All")
    monkeypatch.setattr(slideshow, "MOST_MINUTES", 1)
    r = client.post(f"/api/tours/{tour_id}/video", headers=APP)
    assert r.status_code == 400 and "minutes" in r.json()["detail"]
    assert not con.execute("SELECT 1 FROM slideshow_video").fetchone()
