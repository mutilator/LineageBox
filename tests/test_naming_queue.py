"""People → Name faces: one face at a time, in the queue's order (asked about, looks like someone, recognised, groups),
the answers (yes, no, a name, skip, don't know), Check faces' yes, and a member's queue holding nothing from outside
their part of the family."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from lineagebox import catalog, people, settings
from lineagebox.web import app as webapp
from tests import family_fixture as fx
from tests.conftest import LAN, rel
from tests.test_people import RNG, DIM, A, B, add_face, image, lib, like, unit  # noqa: F401 (lib is a fixture)

APP = {"X-Requested-With": "lineagebox"}
C = unit(RNG.standard_normal(DIM))  # a third person, nobody named yet


def face_row(con, f):
    return tuple(con.execute("SELECT person_id, assigned_by FROM face WHERE id = ?", (f,)).fetchone())


def card_face(html):
    """The face a card is about, or the group."""
    m = re.search(r'class="naming-card[^"]*" data-(face|group)="(\d+)"', html)
    return (m.group(1), int(m.group(2))) if m else None


@pytest.fixture
def queue(lib, cfg):
    """Owen and Lucas named by hand on three photos each; a face someone asked about, one beside Lucas that looks like
    Owen, one recognised as Owen, and a group of four faces of someone else."""
    con = lib
    o = [image(con, rel("Owen", f"o{i}.jpg")) for i in range(6)]
    lu = [image(con, rel("Lucas", f"l{i}.jpg")) for i in range(6)]
    owen = people.name_faces(con, [add_face(con, i, like(A)) for i in o[:3]], "Owen")
    lucas = people.name_faces(con, [add_face(con, i, like(B)) for i in lu[:3]], "Lucas")
    asked = add_face(con, o[3], unit(RNG.standard_normal(DIM)))  # like nobody
    con.execute("INSERT INTO face_question(face_id, account_id, asked_at) VALUES (?, NULL, '2026-01-02T10:00:00')",
                (asked,))
    brother = add_face(con, lu[0], unit(A + 1.1 * B), x=0.5)  # beside Lucas: suggested as Owen
    recognised = add_face(con, o[4], like(A))
    group = [add_face(con, i, like(C), x=0.5) for i in (lu[3], lu[4], lu[5], o[5])]
    con.commit()
    assert people.identify(con) == 1 and face_row(con, recognised) == (owen, "auto")
    con.executemany("UPDATE face SET cluster_id = 5 WHERE id = ?", [(f,) for f in group])
    con.commit()
    return {"con": con, "owen": owen, "lucas": lucas, "asked": asked, "brother": brother, "recognised": recognised,
            "group": group, "o": o, "lu": lu}


def test_one_face_at_a_time_in_order(queue, cfg):
    q, con = queue, queue["con"]
    client = TestClient(webapp.create_app(cfg), client=LAN)
    assert 'href="/people/name"' in client.get("/people").text  # reached from People

    page = client.get("/people/name").text
    for step, n in (("All", 4), ("Asked about", 1), ("Looks like someone", 1), ("Recognised, to check", 1),
                    ("Groups to name", 1)):
        assert re.search(rf">{step} <span>{n}</span>", page), step
    # 1. the face someone asked about, which looks like nobody: a box for who it is, Don't know, Skip
    assert card_face(page) == ("face", q["asked"]) and "Who is this?" in page and "Someone asked" in page
    assert f'data-url="/api/faces/{q["asked"]}/name"' in page and f'data-url="/api/faces/{q["asked"]}/skip"' in page
    assert f'src="/face/{q["asked"]}"' in page and 'class="naming-box"' in page  # the face, and on its photo
    for key in ("n", "d", "s"):
        assert f'data-key="{key}"' in page, key
    assert f'href="/people/name?only=&amp;skip={q["asked"]}&amp;skip_groups="' in page  # Skip without the script

    # 2. Skip: the face beside Lucas, suggested as Owen
    skipped = f"skip={q['asked']}"
    card = client.get(f"/people/name/next?{skipped}").text
    assert card_face(card) == ("face", q["brother"]) and "Is this" in card and ">Owen</a>?" in card
    assert "isn't named in this photo yet" in card and "With Lucas" in card
    assert 'data-key="y"' in card and 'data-key="n"' in card and "<html" not in card  # a card, not a page
    yes = client.post(f"/api/faces/{q['brother']}/yes", json={"person_id": q["owen"]})
    assert yes.status_code == 403  # only from the site's own scripts
    yes = client.post(f"/api/faces/{q['brother']}/yes", json={"person_id": q["owen"]}, headers=APP)
    assert yes.status_code == 200 and yes.json()["message"] == "Named Owen."
    assert face_row(con, q["brother"]) == (q["owen"], "user")

    # 3. the face recognised automatically, least certain first; No takes the name off, and nobody else is close
    card = client.get(f"/people/name/next?{skipped}").text
    assert card_face(card) == ("face", q["recognised"]) and "Recognised automatically" in card
    assert f'data-again="/people/name/face/{q["recognised"]}"' in card
    no = client.post(f"/api/faces/{q['recognised']}/no", json={"person_id": q["owen"]}, headers=APP)
    assert no.status_code == 200 and "Not Owen" in no.json()["message"]
    assert face_row(con, q["recognised"]) == (None, None)
    assert [r[0] for r in con.execute("SELECT person_id FROM face_not_person WHERE face_id = ?",
                                      (q["recognised"],))] == [q["owen"]]
    again = client.get(f"/people/name/face/{q['recognised']}").text
    assert card_face(again) == ("face", q["recognised"]) and "Who is this?" in again and "Is this" not in again
    assert people.identify(con, [q["recognised"]]) == 0  # nor recognised as him again

    # 4. the group: its faces, a name box
    card = client.get(f"/people/name/next?skip={q['asked']},{q['recognised']}").text
    assert card_face(card) == ("group", 5) and re.search(r"4 faces\s+in 4 photos", card)
    url = re.search(r'data-url="(/api/clusters/5/name\?[^"]+)"', card).group(1).replace("&amp;", "&")
    named = client.post(url, json={"name": "Cleo"}, headers=APP)
    assert named.status_code == 200
    assert {face_row(con, f) for f in q["group"]} == {(named.json()["person_id"], "user")}

    # nothing left but what was skipped
    card = client.get(f"/people/name/next?skip={q['asked']},{q['recognised']}").text
    assert card_face(card) is None and "the ones you skipped" in card
    # Don't know: never asked of them again (without accounts, the question is taken off)
    assert client.post(f"/api/faces/{q['asked']}/skip", headers=APP).status_code == 200
    assert card_face(client.get("/people/name/next").text) is None


def test_one_step_only_and_the_kept_queue(queue, cfg):
    q, con = queue, queue["con"]
    client = TestClient(webapp.create_app(cfg), client=LAN)
    assert card_face(client.get("/people/name?only=groups").text) == ("group", 5)
    assert card_face(client.get("/people/name?only=recognised").text) == ("face", q["recognised"])
    # answered elsewhere (on the photo's page, by the worker): left out of the kept queue as it is walked
    people.name_faces(con, [q["recognised"]], "Owen")
    assert card_face(client.get("/people/name/next?only=recognised").text) is None
    con.execute("UPDATE face SET cluster_id = NULL WHERE cluster_id = 5")
    con.commit()
    assert card_face(client.get("/people/name/next?only=groups").text) is None
    assert card_face(client.get("/people/name/next?only=nonsense").text) == ("face", q["asked"])  # all of them


def test_yes_and_no_keep_what_people_said(queue, cfg):
    q, con = queue, queue["con"]
    client = TestClient(webapp.create_app(cfg), client=LAN)
    lucas_face = con.execute("SELECT id FROM face WHERE person_id = ? LIMIT 1", (q["lucas"],)).fetchone()[0]
    # a face named by hand as someone else since the card was shown keeps that name
    r = client.post(f"/api/faces/{lucas_face}/yes", json={"person_id": q["owen"]}, headers=APP)
    assert r.status_code == 409 and "Fix" in r.json()["detail"]
    assert face_row(con, lucas_face) == (q["lucas"], "user")
    people.set_aside(con, [q["asked"]], "not_a_face")
    assert client.post(f"/api/faces/{q['asked']}/yes", json={"person_id": q["owen"]}, headers=APP).status_code == 409
    assert client.post("/api/faces/99999/yes", json={"person_id": q["owen"]}, headers=APP).status_code == 404
    assert client.post(f"/api/faces/{q['brother']}/yes", json={"person_id": 99999}, headers=APP).status_code == 404
    # yes to someone it was said not to be: them after all; yes again: as it was
    people.not_this_person(con, q["owen"], [q["brother"]])
    assert client.post(f"/api/faces/{q['brother']}/yes", json={"person_id": q["owen"]}, headers=APP).status_code == 200
    assert face_row(con, q["brother"]) == (q["owen"], "user")
    assert con.execute("SELECT count(*) FROM face_not_person WHERE face_id = ?", (q["brother"],)).fetchone()[0] == 0
    assert people.is_them(con, q["owen"], [q["brother"]]) == [q["brother"]]
    # no to someone else than the face's name: noted, and the name stays
    assert client.post(f"/api/faces/{lucas_face}/no", json={"person_id": q["owen"]}, headers=APP).status_code == 200
    assert face_row(con, lucas_face) == (q["lucas"], "user")


def test_who_says_what_to_ask_about_a_face(queue, cfg):
    """For a page that asks about a face tapped on its photo: as JSON, or as a card of its own."""
    q = queue
    client = TestClient(webapp.create_app(cfg), client=LAN)
    who = client.get(f"/api/faces/{q['brother']}/who").json()
    assert who["named"] is None and who["suggest"]["person_id"] == q["owen"] and who["suggest"]["name"] == "Owen"
    assert who["suggest"]["face"] and who["photo"]["src"].startswith("/p/") and len(who["box"]) == 4
    assert who["card"] == f"/people/name/face/{q['brother']}?one=1" and who["asked"] is False
    who = client.get(f"/api/faces/{q['recognised']}/who").json()
    assert who["named"] == {"person_id": q["owen"], "name": "Owen", "by": "auto"} and who["suggest"] is None
    who = client.get(f"/api/faces/{q['asked']}/who").json()
    assert who["named"] is None and who["suggest"] is None and who["asked"] is True
    assert client.get("/api/faces/99999/who").status_code == 404
    card = client.get(f"/people/name/face/{q['brother']}?one=1").text
    assert card_face(card) == ("face", q["brother"]) and "naming-steps" not in card
    assert '<button type="button" data-answer="skip" data-key="s"' in card  # no link on to the queue


def test_check_faces_says_yes(queue, cfg):
    q, con = queue, queue["con"]
    more = add_face(con, q["o"][5], like(A), x=0.1)
    con.commit()
    assert people.identify(con, [more]) == 1
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get(f"/person/{q['owen']}/faces").text
    assert f'data-yes-faces="/api/people/{q["owen"]}/yes"' in page
    assert f'data-api="/api/faces/{q["recognised"]}/yes"' in page and f'data-api="/api/faces/{more}/not"' in page
    url = f"/api/people/{q['owen']}/yes"
    assert client.post(url, json={"yes": [q["recognised"]]}).status_code == 403
    assert client.post(url, json={"yes": [99999]}, headers=APP).status_code == 404
    r = client.post(url, json={"yes": [q["recognised"], more]}, headers=APP)
    assert r.status_code == 200 and r.json()["named"] == 2 and "2 faces confirmed as Owen" in r.json()["message"]
    assert face_row(con, q["recognised"]) == face_row(con, more) == (q["owen"], "user")
    assert "Recognised automatically" not in client.get(f"/person/{q['owen']}/faces").text


def test_people_without_a_named_face_show_a_photo_or_their_initial(queue, cfg):
    q, con = queue, queue["con"]
    added = people.person_id(con, "Nana Miller")
    con.execute("INSERT INTO image_person(image_id, person_id, added_at) VALUES (?, ?, '2026-01-01')",
                (q["lu"][5], added))
    con.commit()
    row = next(p for p in catalog.people(con) if p["id"] == added)
    sha = catalog.image(con, q["lu"][5])["sha256"]
    assert row["cover_face"] is None and row["cover_photo"] == sha
    assert next(p for p in catalog.people(con) if p["id"] == q["owen"])["cover_photo"] is None
    page = TestClient(webapp.create_app(cfg), client=LAN).get("/people").text
    assert "/face/None" not in page and f'src="/t/{sha}"' in page


# ---- a member -------------------------------------------------------------------------------------------------------

@pytest.fixture
def family(con, cfg, library):
    fam = fx.build(con, cfg, library)
    p, ph = fam["people"], fam["photos"]
    fx.account(con, p["Sam"], "sam@example.com", "admin")
    fam["cora"] = fx.account(con, p["Cora"], "cora@example.com")
    hank = con.execute("SELECT embedding FROM face WHERE person_id = ?", (p["Hank"],)).fetchone()[0]
    rex = con.execute("SELECT embedding FROM face WHERE person_id = ?", (p["Rex"],)).fetchone()[0]

    def face(photo, emb, x, **how):
        cols = ", ".join(how)
        return con.execute(f"""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding
                                                {', ' + cols if how else ''})
                               VALUES (?, ?, 0.1, ?, 0.5, 0.9, 25, ? {', ?' * len(how)})""",
                           (ph[photo], x, x + 0.1, emb, *how.values())).lastrowid

    fam["new"] = {
        # beside Cora and Bart: plainly Hank, whom Cora doesn't see (his photo with Sam she does)
        "like_hank": face("cora_bart.jpg", hank, 0.85),
        # Zed's photo, which Cora doesn't see: asked about, recognised as Zed, and in a group
        "zed_asked": face("zed.jpg", fx.unit(500).tobytes(), 0.7),
        "zed_auto": face("zed.jpg", fx.unit(501).tobytes(), 0.8, person_id=p["Zed"], assigned_by="auto",
                         assign_score=0.6),
        "zed_grouped": face("zed.jpg", fx.unit(502).tobytes(), 0.9, cluster_id=9),
        # recognised as Hank in the photo of him with Sam, which she sees: his name is not hers to check
        "hank_auto": face("hank_sam.jpg", hank, 0.85, person_id=p["Hank"], assigned_by="auto", assign_score=0.55),
        # Cora's own: one asked about, and one in the group
        "asked": face("cora_bart.jpg", fx.unit(503).tobytes(), 0.95),
        "grouped": face("rex.jpg", rex, 0.6, cluster_id=9),
    }
    for f in ("zed_asked", "asked"):
        con.execute("INSERT INTO face_question(face_id, account_id, asked_at) VALUES (?, NULL, '2026-01-01T09:00')",
                    (fam["new"][f],))
    con.commit()
    settings.save(con, cfg, {"accounts_enabled": True})
    return fam


def member(cfg, con, account):
    from lineagebox import accounts
    c = TestClient(webapp.create_app(cfg), client=LAN, follow_redirects=False)
    c.cookies.set(accounts.COOKIE, accounts.new_session(con, account["id"], 30))
    return c


def test_a_members_queue_holds_only_their_part_of_the_family(family, cfg, con):
    p, new = family["people"], family["new"]
    v = __import__("lineagebox.access", fromlist=["compute"]).compute(con, family["cora"])
    hidden_names = [n for n in fx.PEOPLE if n not in fx.SEES["Cora"]]
    hidden_faces = [r[0] for r in con.execute("SELECT id FROM face") if not v.sees_image(
        con.execute("SELECT image_id FROM face WHERE id = ?", (r[0],)).fetchone()[0])]
    hidden_faces += [new["hank_auto"]]  # in a photo she sees, but named as someone she doesn't
    c = member(cfg, con, family["cora"])
    shown, skip, groups, pages = [], [], [], []
    page = c.get("/people/name").text
    pages.append(page)
    while (found := card_face(page)) is not None:
        shown.append(found)
        (groups if found[0] == "group" else skip).append(found[1])
        page = c.get(f"/people/name/next?skip={','.join(map(str, skip))}&skip_groups={','.join(map(str, groups))}").text
        pages.append(page)
        assert len(shown) < 20
    assert ("face", new["asked"]) in shown and ("group", 9) in shown
    assert ("face", family["faces"]["rex.jpg"][0]) in shown  # Rex, recognised automatically: hers to check
    for kind, i in shown:
        assert kind == "group" or i not in hidden_faces, i
    for text in pages:
        for name in hidden_names:
            assert not re.search(rf"\b{name}\b", text), name
        for f in hidden_faces:
            assert f"/face/{f}\"" not in text and f"/api/faces/{f}/" not in text, f
    # the face that is plainly Hank is asked as "who is this?", Hank never suggested
    card = c.get(f"/people/name/face/{new['like_hank']}").text
    assert "Who is this?" in card and "Hank" not in card
    who = c.get(f"/api/faces/{new['hank_auto']}/who")
    assert who.status_code == 200 and who.json()["named"] == {"outside": True} and "Hank" not in who.text
    # the group shows only her faces of it
    card = c.get("/people/name/next?only=groups").text
    assert f'/face/{new["grouped"]}"' in card and f'/face/{new["zed_grouped"]}"' not in card

    # her answers: refused outside her part of the family, tracked inside it
    for url, body, status in ((f"/api/faces/{new['zed_auto']}/yes", {"person_id": p["Cora"]}, 404),
                              (f"/api/faces/{new['zed_auto']}/no", {"person_id": p["Zed"]}, 404),
                              (f"/api/faces/{new['hank_auto']}/no", {"person_id": p["Hank"]}, 403),
                              (f"/api/faces/{new['like_hank']}/yes", {"person_id": p["Hank"]}, 404),
                              (f"/api/people/{p['Hank']}/yes", {"yes": [new["hank_auto"]]}, 404),
                              (f"/api/people/{p['Cora']}/yes", {"yes": [new["zed_auto"]]}, 404)):
        r = c.post(url, json=body, headers=APP)
        assert r.status_code == status, (url, r.status_code, r.text)
    assert face_row(con, new["zed_auto"]) == (p["Zed"], "auto") and face_row(con, new["hank_auto"]) == (p["Hank"],
                                                                                                         "auto")
    rex_face = family["faces"]["rex.jpg"][0]
    r = c.post(f"/api/faces/{rex_face}/yes", json={"person_id": p["Rex"]}, headers=APP)
    assert r.status_code == 200 and r.json()["undo"]
    named = con.execute("SELECT person_id, assigned_by, named_by FROM face WHERE id = ?", (rex_face,)).fetchone()
    assert tuple(named) == (p["Rex"], "user", family["cora"]["id"])
    change = con.execute("SELECT account_id, summary, undo FROM change_log ORDER BY id DESC LIMIT 1").fetchone()
    assert change["account_id"] == family["cora"]["id"] and change["summary"] == "Named a face Rex"
    assert str(rex_face) in json.dumps(json.loads(change["undo"]))
