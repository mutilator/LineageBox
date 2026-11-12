"""Cleanup → Private papers: photos that look like identity cards, money and tax papers, health records and certificates
of living people, found in the AI's descriptions (private.py); hidden from family members or said fine to show, each
as a change to undo; the card first on Cleanup, and the alert to admins."""
import json
import re

from fastapi.testclient import TestClient

from lineagebox import outbox, people, private, settings
from lineagebox.web import app as webapp
from tests.conftest import LAN
from tests.test_face_checks import APP, A, B, face, like, photo

SSN = "123-45-6789"  # made up, in the text the AI read


def described(con, n, caption, photo_type="document/screenshot", text=None, tags=(), taken="2020-05-01T10:00:00"):
    image_id = photo(con, n, taken)
    con.execute("""INSERT INTO analysis(image_id, model, prompt_version, created_at, caption, photo_type, text_visible,
                                        raw_json) VALUES (?, 'test', 1, '2026-01-01', ?, ?, ?, ?)""",
                (image_id, caption, photo_type, text, json.dumps({"objects": [], "tags": list(tags)})))
    return image_id


def test_what_looks_private():
    seen = lambda caption, kind="document/screenshot", text=None, tags=(): private.looks_private(  # noqa: E731
        caption, json.dumps({"tags": list(tags)}), text, kind)
    assert seen("A driver's license with a photo of an adult woman.") == ("id", "driver's license")
    assert seen("Two copies of a 2019 W-2 Wage and Tax Statement.") == ("money", "w-2")
    assert seen("A close-up of an insurance card on a table.") == ("health", "insurance card")
    assert seen("A certified copy of a birth certificate.") == ("certificates", "birth certificate")
    assert seen("A receipt", tags=["tax form"]) == ("money", "tax form")
    # on a photo of people the phrases are mostly something else
    assert seen("A boy wears a lanyard with an identification card.", "candid") is None
    assert seen("A couple signs a marriage license at their wedding.", "portrait") is None
    assert seen("A receipt for prescription sunglasses.") is None and seen("A prescription form for a child.")
    # what the AI read: a number set out like a Social Security number, on any photo; never the number itself
    assert seen("A card on a table.", "candid", text=f"SSN {SSN}") == (
        "id", "a number set out like a Social Security number")
    assert seen("A letter.", text="DOB 01/02/1990") == ("id", "a date of birth")
    assert seen("A party.", "candid", text="DOB 01/02/1990") is None
    assert seen("A sunset over the lake.", "landscape") is None and seen("A cake.", "object/still life") is None


def test_found_hidden_and_fine_to_show(con, cfg):
    owen = people.person_id(con, "Owen")
    licence = described(con, 1, "A driver's license with a photo of an adult man.")
    face(con, licence, like(A), owen, "auto")  # recognised: Owen's family sees it
    taxes = described(con, 2, "A partially filled IRS Form 1099-MISC.", tags=["tax form"], taken="2021-01-01T10:00:00")
    card = described(con, 3, "A card on a table.", "object/still life", text=f"Number {SSN}")
    described(con, 4, "A birthday cake with candles.", "object/still life")
    # certificates: a child's is listed, a great-grandmother's (died) is family history, one of nobody named is listed
    child, gran = people.person_id(con, "Lucas"), people.person_id(con, "Ruth Baker")
    con.execute("INSERT INTO event(person_id, tag) VALUES (?, 'DEAT')", (gran,))
    con.execute("UPDATE person SET deceased = 1 WHERE id = ?", (gran,))
    theirs = described(con, 5, "A certified copy of a birth certificate for a child.")
    face(con, theirs, like(B), child, "user")
    hers = described(con, 6, "A birth certificate issued by the state.")
    con.execute("INSERT INTO image_person(image_id, person_id, added_at) VALUES (?, ?, '2026-01-01')", (hers, gran))
    nobody = described(con, 7, "A marriage certificate on a desk.")
    con.commit()

    found = private.suggestions(con)
    assert [f["id"] for f in found] == [licence, theirs, card, taxes, nobody]  # someone named first, then by kind
    assert {f["id"]: (f["kind"], f["why"], f["named"]) for f in found}[card] == (
        "id", "a number set out like a Social Security number", False)

    client = TestClient(webapp.create_app(cfg), client=LAN)
    cleanup = client.get("/cleanup").text
    assert cleanup.index('data-later="/cleanup/counts/private"') < cleanup.index('data-later="/cleanup/counts/copies"')
    card_html = client.get("/cleanup/counts/private").text
    assert 'class="card private-card waiting"' in card_html and '<span class="num">5</span>' in card_html
    assert "2 have someone named in them" in card_html

    page = client.get("/cleanup/private").text
    assert "Hide 0 from family members" in page and "Fine to show: 0" in page
    assert re.search(r">Identity cards <span>2</span>", page) and re.search(r">Money and taxes <span>1</span>", page)
    assert 'title="Identity cards">driver&#39;s license<br><b>someone named in it</b>' in page
    assert SSN not in page and "Number" not in page  # what the AI read is never shown
    assert "2 of them have someone named in them" in page
    only = client.get("/cleanup/private?kind=certificates").text
    assert f'value="{theirs}"' in only and f'value="{nobody}"' in only and f'value="{licence}"' not in only

    url = "/api/images/hide-private"
    assert client.post(url, json={"image_ids": [licence, theirs]}).status_code == 403  # the site's scripts only
    assert client.post(url, json={"image_ids": [99999]}, headers=APP).status_code == 404
    hid = client.post(url, json={"image_ids": [licence, theirs]}, headers=APP).json()
    assert hid["message"] == "Hidden from family members: 2 photos." and hid["undo"]
    assert {r[0] for r in con.execute("SELECT id FROM image WHERE hidden")} == {licence, theirs}
    fine = client.post("/api/images/private-ok", json={"image_ids": [nobody]}, headers=APP).json()
    assert fine["undo"] and settings.get(con, private.OK_KEY) == [nobody]
    assert [f["id"] for f in private.suggestions(con)] == [card, taxes]

    # undone: hidden no more, and listed again
    assert client.post(f"/api/changes/{hid['undo']}/undo-own", headers=APP).status_code == 200
    assert con.execute("SELECT count(*) FROM image WHERE hidden").fetchone()[0] == 0
    assert client.post(f"/api/changes/{fine['undo']}/undo-own", headers=APP).status_code == 200
    assert len(private.suggestions(con)) == 5


def test_admins_are_told_of_new_ones_once(con):
    licence = described(con, 1, "A driver's license on a table.")
    con.commit()
    assert outbox.private_papers(con) == 1
    assert outbox.private_papers(con) == 0  # told already
    told = con.execute("SELECT kind, admins, data FROM outbox WHERE kind = 'private'").fetchone()
    assert told["admins"] and json.loads(told["data"]) == {"photos": 1, "named": 0}
    said = outbox.alert("private", json.loads(told["data"]))
    assert said["href"] == "/cleanup/private" and said["text"].startswith("1 photo looks like private papers")
    # one more found: told of that one only
    described(con, 2, "A Social Security card.")
    con.commit()
    assert outbox.private_papers(con) == 1
    assert json.loads(con.execute("SELECT data FROM outbox WHERE kind = 'private' ORDER BY id DESC").fetchone()[0]) == {
        "photos": 1, "named": 0}
    con.execute("UPDATE image SET hidden = 1 WHERE id = ?", (licence,))
    con.commit()
    assert outbox.private_papers(con) == 0


def test_members_hide_their_own_uploads(con, cfg, library):
    """A family member's Private papers are the photos they uploaded: theirs to hide from the rest of the family, never
    a photo someone else uploaded that they see; an admin's are the whole library's."""
    from lineagebox import access, accounts
    from lineagebox.ingest import ingest
    from lineagebox.scan import scan
    from tests import family_fixture as fx
    from tests.test_uploads import client, jpeg, upload

    site = fx.build(con, cfg, library)
    p, photos = site["people"], site["photos"]
    sam = fx.account(con, p["Sam"], "sam@example.com", "admin")
    cora = fx.account(con, p["Cora"], "cora@example.com")
    bart = fx.account(con, p["Bart"], "bart@example.com")
    settings.save(con, cfg, {"accounts_enabled": True})
    for name, color in (("licence.jpg", "red"), ("cake.jpg", "green")):
        assert upload(client(cfg, con, cora), name, jpeg(color), folder="Papers").status_code == 200
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    up = {n: con.execute("SELECT image_id FROM file WHERE path LIKE ?", (f"%/Papers/{n}",)).fetchone()[0]
          for n in ("licence.jpg", "cake.jpg")}
    seen = photos["cora_bart.jpg"]  # a photo she sees, not one she uploaded
    for image_id, caption in ((up["licence.jpg"], "A driver's license with a photo of a woman."),
                              (up["cake.jpg"], "A birthday cake with candles."),
                              (seen, "A driver's license on a table.")):
        con.execute("""INSERT INTO analysis(image_id, model, prompt_version, created_at, caption, photo_type, raw_json)
                       VALUES (?, 'test', 1, '2026-01-01', ?, 'document/screenshot', '{}')""", (image_id, caption))
    # Cora is named in her upload: her brother Bart sees it
    con.execute("INSERT INTO image_person(image_id, person_id, added_at) VALUES (?, ?, '2026-01-01')",
                (up["licence.jpg"], p["Cora"]))
    con.commit()
    assert up["licence.jpg"] in access.compute(con, bart).images

    as_cora = client(cfg, con, cora)
    assert 'data-later="/cleanup/counts/private"' in as_cora.get("/cleanup").text
    card = as_cora.get("/cleanup/counts/private").text
    assert "photos you uploaded" in card and '<span class="num">1</span>' in card
    page = as_cora.get("/cleanup/private").text
    assert "Photos you uploaded" in page and f'value="{up["licence.jpg"]}"' in page and f'value="{seen}"' not in page
    assert 'href="/cleanup/changes"' not in page  # an admin's page

    url = "/api/images/hide-private"
    assert as_cora.post(url, json={"image_ids": [seen]}, headers=APP).status_code == 403
    assert as_cora.post("/api/images/private-ok", json={"image_ids": [seen]}, headers=APP).status_code == 403
    assert as_cora.post(url, json={"image_ids": [photos["zed.jpg"]]}, headers=APP).status_code == 404  # unseen
    hid = as_cora.post(url, json={"image_ids": [up["licence.jpg"]]}, headers=APP)
    assert hid.status_code == 200, hid.text
    assert con.execute("SELECT hidden FROM image WHERE id = ?", (up["licence.jpg"],)).fetchone()[0] == 1
    assert up["licence.jpg"] not in access.compute(con, bart).images  # the rest of the family no longer sees it
    assert up["licence.jpg"] in access.compute(con, dict(accounts.get(con, cora["id"]))).images  # she still does
    assert as_cora.get(f"/image/{up['licence.jpg']}").status_code == 200

    # her own change, undone
    assert as_cora.post(f"/api/changes/{hid.json()['undo']}/undo-own", headers=APP).status_code == 200
    assert up["licence.jpg"] in access.compute(con, bart).images

    everyone = client(cfg, con, sam).get("/cleanup/private").text  # an admin's: the whole library's
    assert f'value="{up["licence.jpg"]}"' in everyone and f'value="{seen}"' in everyone
