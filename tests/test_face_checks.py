"""Faces and dates that find more: names that don't fit an age, a birth year from the faces, groups of two or three,
who a group looks like, one person named twice, photos with faces missed, a copy's camera date, and a probable date
for a scan. A camera's clock put right is test_camera_clock.py."""
import html
import re

import numpy as np
import pytest
from fastapi.testclient import TestClient

from lineagebox import date_hints, dedup, face_ages, faces, people, scan_dates, settings
from lineagebox.web import app as webapp
from tests.conftest import LAN, rel, write_jpeg

RNG = np.random.default_rng(11)
DIM = 512
APP = {"X-Requested-With": "lineagebox"}


def unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


A, B = unit(RNG.standard_normal(DIM)), unit(RNG.standard_normal(DIM))


def like(v, noise=0.3):
    return unit(v + noise * unit(RNG.standard_normal(DIM)))


def toward(v, cosine):
    """A face scoring about `cosine` against v: someone they look a little like."""
    other = unit(RNG.standard_normal(DIM))
    other = unit(other - (other @ v) * v)
    return unit(cosine * v + np.sqrt(1 - cosine ** 2) * other)


def photo(con, n, taken=None, source="exif", folder=1, path=None, camera=None, width=200, height=200):
    """A photo in the catalog (no file on disk: these tests don't open it)."""
    con.execute("INSERT OR IGNORE INTO folder(id, path, name) VALUES (?, ?, ?)", (folder, f"f{folder}", f"f{folder}"))
    con.execute("""INSERT INTO image(id, sha256, format, media, width, height, phash, taken_at, taken_at_source, camera)
                   VALUES (?, ?, 'jpeg', 'photo', ?, ?, ?, ?, ?, ?)""",
                (n, f"{n:064x}", width, height, n * 0x1000003, taken, source, camera))
    con.execute("""INSERT INTO file(path, folder_id, image_id, size, mtime, last_seen, missing)
                   VALUES (?, ?, ?, 1, 1, '', 0)""", (path or f"f{folder}/{n:04d}.jpg", folder, n))
    return n


def face(con, image_id, emb, person=None, by=None, age=30, x=0.0, cluster=None):
    return con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, est_age, embedding, person_id,
                                           assigned_by, cluster_id)
                          VALUES (?, ?, 0, ?, 1, 0.9, 22.0, ?, ?, ?, ?, ?)""",
                       (image_id, x, x + 0.4, age, emb.tobytes(), person, by, cluster)).lastrowid


def born(con, pid, sort_date):
    con.execute("INSERT INTO event(person_id, tag, date, sort_date) VALUES (?, 'BIRT', ?, ?)", (pid, sort_date, sort_date))


def test_names_that_dont_fit_an_age(con):
    """Named before they were born (a camera's clock, mostly), or a young child's face named as a grown-up."""
    kid, gran = people.person_id(con, "Owen Miller"), people.person_id(con, "Ruth Baker")
    born(con, kid, "2010-11-27")
    born(con, gran, "1950")
    early = face(con, photo(con, 1, "2009-05-01T10:00:00", camera="Cam"), like(A), kid, "auto", age=28)
    fine = face(con, photo(con, 2, "2012-05-01T10:00:00"), like(A), kid, "user", age=30)
    child = face(con, photo(con, 3, "2012-05-01T10:00:00"), like(B), gran, "user", age=6)
    adult = face(con, photo(con, 4, "2012-06-01T10:00:00"), like(B), gran, "user", age=40)
    undated = face(con, photo(con, 5, "2001-01-01T00:00:00", "mtime"), like(A), kid, "user", age=30)  # a file's time
    con.commit()
    found = {c["face_id"]: c["why"] for c in face_ages.age_checks(con)}
    # a child's face on a photo dated when she was grown up: its date is the likelier mistake (Suggested dates → Looks
    # too young), until someone says its date is right; then it is the name
    assert found == {early: "born after"} and 3 in scan_dates.young(con)
    settings.put(con, scan_dates.OK_KEY, [3])
    found = {c["face_id"]: c["why"] for c in face_ages.age_checks(con)}
    assert found == {early: "born after", child: "child"} and 3 not in scan_dates.young(con, {3})
    assert fine not in found and adult not in found and undated not in found
    assert [c["face_id"] for c in face_ages.age_checks(con, {early})] == [child]
    assert face_ages.before_birth_by_camera(con) == {"Cam": 1}


def test_a_birth_year_from_child_faces(con):
    """Only a face estimated under 15 says anything: born at most 12 years before, and not after the first photo."""
    pid = people.person_id(con, "Lucas Miller")
    face(con, photo(con, 1, "2013-01-01T10:00:00"), like(A), pid, "user", age=31)
    for n, day in enumerate(("2014-03-01", "2015-06-01", "2016-07-01"), 2):
        face(con, photo(con, n, f"{day}T10:00:00"), like(A), pid, "user", age=5)
    con.commit()
    assert face_ages.birth_hint(con, pid) == (2003, 2013)  # the second-latest child day, 2015, less 12
    adult = people.person_id(con, "Ann Baker")
    for n in range(10, 14):
        face(con, photo(con, n, f"201{n - 10}-01-01T10:00:00"), like(B), adult, "user", age=12 + n)
    assert face_ages.birth_hint(con, adult) is None  # grown-up faces say nothing
    face(con, photo(con, 20, "1990-01-01T10:00:00"), like(A), pid, "user", age=40)
    assert face_ages.birth_hint(con, pid) is None  # a photo of 1990 can't be of someone born 2003-2013
    born(con, pid, "2004")
    assert face_ages.birth_hint(con, pid) is None


def test_groups_of_two_or_three(con):
    """Faces HDBSCAN leaves alone are grouped in twos and threes when plainly one person, never two of one photo."""
    a1, a2 = face(con, photo(con, 1), like(A, 0.2)), face(con, photo(con, 2), like(A, 0.2))
    b1 = face(con, photo(con, 3), like(B, 0.2))
    b2 = face(con, 3, like(B, 0.2), x=0.5)  # the same photo: two people
    stranger = face(con, photo(con, 4), unit(RNG.standard_normal(DIM)))
    con.commit()
    groups, grouped = people.cluster(con)
    cluster = dict(con.execute("SELECT id, cluster_id FROM face"))
    assert (groups, grouped) == (1, 2)
    assert cluster[a1] == cluster[a2] is not None
    assert cluster[b1] is None and cluster[b2] is None and cluster[stranger] is None


def test_who_a_group_looks_like(con, cfg):
    """An unnamed group whose faces each fall short of recognition, but together plainly look like someone."""
    owen, lucas = people.person_id(con, "Owen"), people.person_id(con, "Lucas")
    for n in range(1, 4):
        face(con, photo(con, n, f"2012-0{n}-01T10:00:00"), like(A), owen, "user")
        face(con, photo(con, 10 + n, f"2012-0{n}-01T10:00:00"), like(B), lucas, "user")
    group = [face(con, photo(con, 20 + n, "2013-05-01T10:00:00"), toward(A, 0.47), cluster=7) for n in range(4)]
    con.commit()
    assert people.identify(con, group) == 0  # under recognition's bar, one by one
    found = people.looks_like(con, [7])
    assert list(found) == [7] and found[7][0] == owen and found[7][1] >= people.GROUP_SIMILARITY and found[7][2] == 0
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get("/cleanup/faces?show=groups").text
    assert f'data-picked-id="{owen}"' in page and "they look like" in page
    card = client.get("/people/name?only=groups").text
    assert "Are these" in card and f'"name_id": {owen}' in html.unescape(card)
    people.not_this_person(con, owen, group[:3])  # three of four said not to be him: under half can be
    assert people.looks_like(con, [7]) == {}
    con.execute("DELETE FROM face_not_person")
    born(con, owen, "2014-01-01")  # all the photos are from before he was born
    con.commit()
    assert people.looks_like(con, [7]) == {}


def test_one_person_named_twice_is_found_by_face(con, cfg):
    owen, owie = people.person_id(con, "Owen Miller"), people.person_id(con, "Owie")
    lucas = people.person_id(con, "Lucas Miller")
    for n in range(1, 5):
        face(con, photo(con, n), like(A), owen, "user")
        face(con, photo(con, 10 + n), like(B), lucas, "user")
    for n in range(20, 22):
        face(con, photo(con, n), like(A), owie, "user")
    con.commit()
    assert [(a, b) for a, b, _ in people.same_by_face(con)] == [(owie, owen)]
    found = people.maybe_same(con)
    assert [(m["added"], m["other"], m["why"]) for m in found] == [(owie, owen, "face")]
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get("/cleanup/same").text
    assert "look like one person" in page and f'value="{owie}"' in page
    # said to be different people: not listed again
    assert client.post("/api/same/different", json={"added": owie, "other": owen}, headers=APP).status_code == 200
    assert people.maybe_same(con, set(settings.get(con, people.SAME_OK_KEY, []))) == []
    settings.put(con, people.SAME_OK_KEY, [])
    face(con, 1, like(A), owie, "user", x=0.5)  # named in one photo together: two people
    con.commit()
    assert people.same_by_face(con) == []


def test_photos_with_faces_missed(con, cfg):
    group = photo(con, 1, "2012-05-01T10:00:00")
    face(con, group, like(A), people.person_id(con, "Owen"), "user")
    alone = photo(con, 2, "2012-05-01T10:00:00")
    for image_id, count in ((group, 6), (alone, 1)):
        con.execute("""INSERT INTO analysis(image_id, model, prompt_version, created_at, people_count, raw_json)
                       VALUES (?, 'test', 1, '2026-01-01', ?, '{}')""", (image_id, count))
        # the AI's careful count (headcount.py), which the list goes by
        con.execute("""INSERT INTO headcount(image_id, model, version, counted_at, people, people_sure)
                       VALUES (?, 'test', 1, '2026-01-01', ?, 0.85)""", (image_id, count))
    con.commit()
    rows, total = faces.missed(con)
    assert total == 1 and rows[0]["id"] == group and (rows[0]["people"], rows[0]["found"], rows[0]["sure"]) == (6, 1, 85)
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get("/cleanup/faces?show=missed").text
    assert f'data-api="/api/images/{group}/faces"' in page and "6 people (85% sure), 1 face found" in page
    assert client.post(f"/api/images/{group}/faces-found").status_code == 403
    assert client.post(f"/api/images/{group}/faces-found", headers=APP).status_code == 200
    assert faces.missed(con)[1] == 0


def test_a_copy_takes_its_groups_camera_date(con):
    base = 0x0F0F0F0F0F0F0F0F
    for n, phash, taken, source, pixels in ((1, base, "2012-06-03T10:00:00", "exif", 400),
                                            (2, base ^ 1, "2019-05-05T12:00:00", "mtime", 100),
                                            (3, base ^ 3, "1987-07-01T00:00:00", "user", 100)):
        photo(con, n, taken, source, width=pixels, height=1)
        con.execute("UPDATE image SET phash = ? WHERE id = ?", (phash, n))
    con.commit()
    dedup.group(con)
    dated = {r[0]: tuple(r[1:]) for r in con.execute("SELECT id, taken_at, taken_at_source FROM image")}
    assert dated[2] == ("2012-06-03T10:00:00", "copy")
    assert dated[3] == ("1987-07-01T00:00:00", "user")  # a date set by hand stays
    assert dedup.share_dates(con) == 0  # already taken


def test_a_probable_date_for_a_scan(con, cfg):
    """A photo dated by its file's time takes a suggestion from its folder's dated photos, or who is in it."""
    for n, day in enumerate(("2012-06-02", "2012-06-10", "2012-06-15", "2012-06-20"), 1):
        photo(con, n, f"{day}T10:00:00", path=f"f1/IMG_{n:04d}.jpg")
    scan_ = photo(con, 9, "2020-01-05T10:00:00", "mtime", path="f1/IMG_0003b.jpg")
    hint = date_hints.suggest(con, scan_)
    assert hint["text"] == "June 2012" and "just before and after it" in hint["why"][0]
    assert date_hints.suggest(con, 1) is None  # a camera date needs no help
    # nothing beside it: who is in it says when it can't be from
    alone = photo(con, 20, "2020-01-05T10:00:00", "mtime", folder=2)
    kid = people.person_id(con, "Owen Miller")
    born(con, kid, "2014-07-01")
    face(con, alone, like(A), kid, "user", age=30)
    con.commit()
    assert date_hints.suggest(con, alone) == {"text": None, "label": "Not before 2014",
                                              "why": ["Owen Miller, in it, was born in 2014."]}
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get(f"/image/{scan_}").text
    assert "Probably June 2012" in page
    body = re.search(r"data-body='(\{&#34;date&#34;: &#34;June 2012&#34;\}|\{\"date\": \"June 2012\"\})'", page)
    assert body
    assert client.post(f"/api/images/{scan_}/date", json={"date": "June 2012"}, headers=APP).status_code == 200
    assert con.execute("SELECT date_text FROM image WHERE id = ?", (scan_,)).fetchone()[0] == "June 2012"
    assert date_hints.suggest(con, scan_) is None  # dated by hand now


def test_ages_to_check_page(con, cfg):
    kid = people.person_id(con, "Owen Miller")
    born(con, kid, "2010-11-27")
    early = face(con, photo(con, 1, "2009-05-01T10:00:00", camera="Cam"), like(A), kid, "auto")
    con.commit()
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get("/cleanup/faces?show=ages").text
    assert 'aria-current="page">Ages to check' in page and f'id="age{early}"' in page
    assert "before they were born" in page and "/cleanup/camera-clock?camera=Cam" in page
    assert client.post(f"/api/faces/{early}/age-ok", headers=APP).status_code == 200
    assert f'id="age{early}"' not in client.get("/cleanup/faces?show=ages").text
    assert "Cam" in client.get("/cleanup/camera-clock").text


def test_the_confirm_faces_card_adds_up_its_tabs(con, cfg):
    """The Cleanup card's number is every tab of Confirm faces together, as the tabs count them."""
    kid, mom = people.person_id(con, "Owen Miller"), people.person_id(con, "Ruth Miller")
    born(con, kid, "2010-11-27")
    face(con, photo(con, 1, "2009-05-01T10:00:00", camera="Cam"), like(A), kid, "user")  # before his birth
    both = photo(con, 2, "2012-05-01T10:00:00")
    face(con, both, like(A), kid, "user")
    face(con, both, like(B), mom, "user", x=0.3)
    face(con, both, like(A), x=0.6)  # unnamed beside them: Seen together, and a photo to finish
    for n in (3, 4, 5):
        face(con, photo(con, n, "2013-01-01T10:00:00"), like(B), cluster=7)  # a group nobody named
    con.commit()
    client = TestClient(webapp.create_app(cfg), client=LAN)
    card = client.get("/cleanup/counts/faces").text
    total = int(re.search(r'<span class="num">([\d,]+)</span>', card)[1].replace(",", ""))
    tabs = re.search(r'<nav class="tabs".*?</nav>', client.get("/cleanup/faces?show=groups").text, re.S)[0]
    numbers = [int(x.replace(",", "")) for x in re.findall(r"<small>([\d,]+)", tabs)]
    assert len(numbers) == 5 and total == sum(numbers) and min(numbers[:4]) > 0
    assert "1 unnamed face beside people you've named" in card and "1 age to check" in card
