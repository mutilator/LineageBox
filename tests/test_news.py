"""New for you (outbox.py): events written once by the code that knows of them, and each shown only to those who may
see it now; admins' alerts; the house's one list without family accounts; nothing sent anywhere."""
import dataclasses
import re
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from lineagebox import accounts, access, activity, catalog, comments, outbox, settings, sharing, storage, tours, updates
from lineagebox.faces import STAGE_FACES
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests import family_fixture as fx
from tests.conftest import LAN, rel, write_jpeg

APP = {"X-Requested-With": "lineagebox"}


def client(cfg, con, account=None):
    c = TestClient(webapp.create_app(cfg), client=LAN, follow_redirects=False)
    if account is not None:
        c.cookies.set(accounts.COOKIE, accounts.new_session(con, account["id"], 30))
    return c


def texts(con, account) -> list[str]:
    return [e["text"] for e in outbox.for_viewer(con, access.compute(con, account))]


@pytest.fixture
def fam(con, cfg, library):
    f = fx.build(con, cfg, library)
    p = f["people"]
    f["who"] = {"Sam": fx.account(con, p["Sam"], "sam@example.com", "admin"),
                "Cora": fx.account(con, p["Cora"], "cora@example.com"),
                "Tina": fx.account(con, p["Tina"], "tina@example.com")}
    settings.save(con, cfg, {"accounts_enabled": True})
    return f


# ---- new photos, from the worker ------------------------------------------------------------------------------------

def test_new_photos_are_told_once_their_faces_were_looked_for(con, cfg, library):
    scan(con, cfg)
    ingest(con, cfg)
    assert outbox.new_photos(con, cfg) == 0  # a new catalog read the first time: all of it is new, none of it news
    write_jpeg(library / "New" / "a.jpg", color="purple")
    write_jpeg(library / "New" / "b.jpg", color="orange", size=(80, 40))
    scan(con, cfg)
    ingest(con, cfg)
    new = [con.execute("SELECT image_id FROM file WHERE path = ?", (rel("New", n),)).fetchone()[0]
           for n in ("a.jpg", "b.jpg")]
    assert outbox.new_photos(con, cfg) == 0  # faces not looked for yet: they wait
    con.execute(f"UPDATE image SET stage = stage | {STAGE_FACES} WHERE id = ?", (new[0],))
    con.commit()
    assert outbox.new_photos(con, cfg) == 1
    con.execute(f"UPDATE image SET stage = stage | {STAGE_FACES} WHERE id = ?", (new[1],))
    con.commit()
    assert outbox.new_photos(con, cfg) == 1 and outbox.new_photos(con, cfg) == 0  # each told once
    told = [r[0] for r in con.execute("SELECT data FROM outbox WHERE kind = 'new_photos' ORDER BY id")]
    assert told == [f'{{"ids":[{new[0]}]}}', f'{{"ids":[{new[1]}]}}']
    # without face finding, a photo is news as soon as it is read
    write_jpeg(library / "New" / "c.jpg", color="pink")
    scan(con, cfg)
    ingest(con, cfg)
    assert outbox.new_photos(con, dataclasses.replace(cfg, faces_enabled=False)) == 1


def test_a_worker_pass_tells_of_new_photos(con, cfg, library, monkeypatch):
    from lineagebox import worker
    quiet = dataclasses.replace(cfg, faces_enabled=False, describe_enabled=False, places_lookup=False)
    worker.run_once(con, quiet)
    noise = Image.effect_noise((120, 90), 80).convert("RGB")  # not a near-duplicate of the flat test photos
    (library / "Later").mkdir()
    noise.save(library / "Later" / "x.jpg", "JPEG")
    worker.run_once(con, quiet)
    rows = con.execute("SELECT data FROM outbox WHERE kind = 'new_photos'").fetchall()
    x = con.execute("SELECT image_id FROM file WHERE path = ?", (rel("Later", "x.jpg"),)).fetchone()[0]
    assert len(rows) == 1 and str(x) in rows[0][0]
    page = client(quiet, con).get("/news").text  # without accounts: the house's list, as an admin
    assert "1 new photo<" in page and f"/t/{catalog.image(con, x)['sha256']}" in page


# ---- what a member is told -------------------------------------------------------------------------------------------

def test_shares_are_news_for_whoever_they_are_shared_with(fam, con, cfg):
    who = fam["who"]
    t = tours.save_tour(con, None, None, tours.clean({"title": "Rex"}), "Rex's years", who["Sam"]["id"])
    sharing.share(con, who["Sam"]["id"], "tour", t, who["Tina"]["id"])
    con.commit()
    assert any("shared the tour “Rex's years” with you" in x for x in texts(con, who["Tina"]))
    assert not any("Rex's years" in x for x in texts(con, who["Cora"]))  # not shared with her
    assert not any("Rex's years" in x for x in texts(con, who["Sam"]))  # his own doing
    con.execute("DELETE FROM share")  # stopped sharing: no longer news
    con.commit()
    assert not any("Rex's years" in x for x in texts(con, who["Tina"]))


def test_a_shared_photo_is_news_only_while_it_can_be_seen(fam, con, cfg):
    """Tina's own upload shared with everyone: news for Cora, with the photo, until an admin hides it (no share
    passes a hidden photo on); a folder an admin shares is told by its name."""
    who, ph = fam["who"], fam["photos"]
    rufus = ph["rufus.jpg"]  # Sam's dog: in nobody's part of the family, but Tina uploaded it
    con.execute("INSERT INTO upload(image_id, account_id, uploaded_at) VALUES (?, ?, '2026-01-01')",
                (rufus, who["Tina"]["id"]))
    sharing.share(con, who["Tina"]["id"], "image", rufus, None)
    trips = con.execute("SELECT folder_id FROM file WHERE image_id = ?", (ph["scenery.jpg"],)).fetchone()[0]
    sharing.share(con, who["Sam"]["id"], "folder", trips, who["Cora"]["id"])
    con.commit()
    told = outbox.for_viewer(con, access.compute(con, who["Cora"]))
    photo = next(e for e in told if e["text"] == "Tina shared a photo with everyone in the family")
    assert [p["id"] for p in photo["photos"]] == [rufus] and photo["href"] == f"/image/{rufus}"
    assert "Sam shared the folder “Trips” with you" in [e["text"] for e in told]
    assert "Tina shared a photo with everyone in the family" in texts(con, who["Sam"])  # an admin sees every share
    con.execute("UPDATE image SET hidden = 1 WHERE id = ?", (rufus,))
    con.commit()
    assert not any("shared a photo" in x for x in texts(con, who["Cora"]))
    assert "Tina shared a photo with everyone in the family" in texts(con, who["Sam"])  # admins see hidden photos


def test_who_is_this_is_asked_of_those_who_see_the_photo(fam, con, cfg):
    who, p, ph = fam["who"], fam["people"], fam["photos"]
    face = con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding)
                          VALUES (?, 0.7, 0.1, 0.9, 0.5, 0.9, 25, ?)""",
                       (ph["cora_bart.jpg"], fx.unit(901).tobytes())).lastrowid
    zed_face = con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding)
                              VALUES (?, 0.6, 0.1, 0.9, 0.5, 0.9, 25, ?)""",
                           (ph["zed.jpg"], fx.unit(902).tobytes())).lastrowid
    con.commit()
    cora, tina = client(cfg, con, who["Cora"]), client(cfg, con, who["Tina"])
    assert cora.post(f"/api/faces/{face}/ask", headers=APP).status_code == 200
    assert cora.post(f"/api/faces/{face}/ask", headers=APP).status_code == 200  # asked twice: told once
    sam = client(cfg, con, who["Sam"])
    assert sam.post(f"/api/faces/{zed_face}/ask", headers=APP).status_code == 200
    assert con.execute("SELECT count(*) FROM outbox WHERE kind = 'who_is_this'").fetchone()[0] == 2
    asked = "Cora asked who this is"
    tina_page = tina.get("/news").text
    assert asked in tina_page and f"/face/{face}" in tina_page
    assert f"/face/{zed_face}" not in tina_page  # Zed's photo isn't hers to see
    assert asked not in cora.get("/news").text  # her own question
    assert asked in sam.get("/news").text
    tina.post(f"/api/faces/{face}/skip", headers=APP)  # she doesn't know them
    assert asked not in tina.get("/news").text
    cora.post(f"/api/faces/{face}/name", json={"name": "Bart"}, headers=APP)  # named: answered for everyone
    assert asked not in sam.get("/news").text


def test_comments_are_news_for_the_uploader_and_whoever_is_in_the_photo(fam, con, cfg):
    who, ph = fam["who"], fam["photos"]
    picnic = ph["cora_bart.jpg"]
    con.execute("INSERT INTO upload(image_id, account_id, uploaded_at) VALUES (?, ?, '2026-01-01')",
                (picnic, who["Tina"]["id"]))
    con.commit()
    sam = client(cfg, con, who["Sam"])
    assert sam.post(f"/api/images/{picnic}/comments", json={"text": "What a day that was"},
                    headers=APP).status_code == 200
    assert "Sam wrote on your photo: “What a day that was”" in texts(con, who["Tina"])  # she uploaded it
    assert "Sam wrote on a photo you're in: “What a day that was”" in texts(con, who["Cora"])  # she is in it
    assert not any("What a day" in x for x in texts(con, who["Sam"]))
    cora = client(cfg, con, who["Cora"])
    cora.post(f"/api/images/{picnic}/comments", json={"text": "I remember"}, headers=APP)
    assert "Cora wrote on a photo you wrote on: “I remember”" in texts(con, who["Sam"])  # a reply reaches him
    # a comment taken away takes its news with it
    cid = con.execute("SELECT id FROM comment WHERE text = 'What a day that was'").fetchone()[0]
    comments.delete_comment(con, cid)
    con.commit()
    assert not any("What a day" in x for x in texts(con, who["Tina"]))


def test_a_comment_taken_away_never_lends_its_news_to_the_next_one(fam, con, cfg):
    """A comment taken away leaves its number to the next one written: the old news must not show the new words as
    the first writer's (nor a caption that took the number)."""
    who, ph = fam["who"], fam["photos"]
    picnic = ph["cora_bart.jpg"]
    sam, tina = client(cfg, con, who["Sam"]), client(cfg, con, who["Tina"])
    first = sam.post(f"/api/images/{picnic}/comments", json={"text": "Lovely"}, headers=APP).json()["id"]
    assert sam.post(f"/api/comments/{first}/delete", headers=APP).status_code == 200
    again = tina.post(f"/api/images/{picnic}/comments", json={"text": "That was my hat"}, headers=APP).json()["id"]
    assert again == first  # SQLite gave the number again
    told = [t for t in texts(con, who["Cora"]) if "That was my hat" in t]
    assert told == ["Tina wrote on a photo you're in: “That was my hat”"]  # once, and by whoever wrote it
    assert tina.post(f"/api/comments/{again}/delete", headers=APP).status_code == 200
    assert tina.post(f"/api/images/{picnic}/caption", json={"text": "Our picnic"}, headers=APP).status_code == 200
    assert con.execute("SELECT id FROM comment WHERE kind = 'caption'").fetchone()[0] == first
    assert not any("Our picnic" in t for t in texts(con, who["Cora"]))  # a caption is no comment


def test_new_photos_a_member_is_told_of_are_those_she_sees(fam, con, cfg):
    who, ph = fam["who"], fam["photos"]
    outbox.add(con, "new_photos", data={"ids": sorted(ph.values())})
    con.commit()
    v = access.compute(con, who["Cora"])
    mine = [e for e in outbox.for_viewer(con, v) if e["kind"] == "new_photos"][0]
    assert set(mine["ids"]) <= v.images and ph["zed.jpg"] not in mine["ids"] and ph["hidden.jpg"] not in mine["ids"]
    assert mine["text"] == f"{len(mine['ids'])} new photos of your family"
    page = client(cfg, con, who["Cora"]).get(f"/news/{mine['id']}")
    assert page.status_code == 200
    assert catalog.image(con, ph["zed.jpg"])["sha256"] not in page.text
    for i in mine["ids"]:
        assert f"/image/{i}?" in page.text
    # her own uploads are no news to her
    con.execute("INSERT INTO upload(image_id, account_id, uploaded_at) VALUES (?, ?, '2026-01-01')",
                (ph["cora_bart.jpg"], who["Cora"]["id"]))
    con.commit()
    again = [e for e in outbox.for_viewer(con, access.compute(con, who["Cora"])) if e["kind"] == "new_photos"][0]
    assert ph["cora_bart.jpg"] not in again["ids"]


def test_seen_marks_and_the_count_at_the_top(fam, con, cfg):
    who = fam["who"]
    outbox.add(con, "new_photos", data={"ids": [fam["photos"]["cora_bart.jpg"]]})
    con.commit()
    cora = client(cfg, con, who["Cora"])
    home = cora.get("/home").text
    assert re.search(r'class="news-button has-news".*?<span class="count">1</span>', home, re.S)
    page = cora.get("/news").text
    assert 'class="news-item new"' in page
    assert '<span class="count">' not in cora.get("/home").text  # seen: no count, at once
    assert 'class="news-item new"' not in cora.get("/news").text
    # Tina has not seen hers: marks are each account's own
    assert outbox.unseen(con, access.compute(con, who["Tina"])) == 1


def test_seeing_as_a_member_marks_nothing_seen(fam, con, cfg):
    who = fam["who"]
    outbox.add(con, "new_photos", data={"ids": [fam["photos"]["cora_bart.jpg"]]})
    con.commit()
    v = dataclasses.replace(access.compute(con, who["Cora"]), seeing_as=True)
    outbox.mark_seen(con, v, 10**6)
    assert outbox.unseen(con, access.compute(con, who["Cora"])) == 1


# ---- admins' alerts --------------------------------------------------------------------------------------------------

def test_alerts_are_for_admins_and_told_once(fam, con, cfg, monkeypatch):
    who = fam["who"]
    activity.note_problem(con, "ollama", "The model can't see pictures")
    activity.note_problem(con, "ollama", None)
    activity.note_problem(con, "ollama", "The model can't see pictures")  # again the same day: not told again
    assert con.execute("SELECT count(*) FROM outbox WHERE kind = 'service'").fetchone()[0] == 1
    # files the check found damaged, a disk nearly full, a newer version, Ollama away for days
    file_id = con.execute("SELECT id FROM file WHERE image_id IS NOT NULL LIMIT 1").fetchone()[0]
    con.execute("UPDATE file SET damage = 'cut short', checked_at = '2030-01-01T00:00:00' WHERE id = ?", (file_id,))
    con.commit()
    settings.put(con, updates.KEY, {"version": "99.0.0", "url": "", "checked_at": "2030-01-01T00:00:00"})
    settings.put(con, "worker", {"ollama": False})
    settings.put(con, outbox.OLLAMA_UP, (datetime.now() - timedelta(days=4)).isoformat(timespec="seconds"))
    with monkeypatch.context() as m:
        m.setattr(storage, "disks", lambda con, cfg: [{"low": True, "free": 5 * 2**30, "total": 500 * 2**30,
                                                       "folders": ["lib"], "data": False}])
        assert outbox.after_pass(con, cfg) == 4
        settings.put(con, outbox.DISKS_ON, "")  # looked at again: still full, but told only once a week
        assert outbox.after_pass(con, cfg) == 0  # each told once
    said = texts(con, who["Sam"])
    assert "1 file found damaged on the photo disk: a copy from a backup may be needed" in said
    assert any(x.startswith("A disk is nearly full: 5.0 GB left of 500 GB on the one with lib") for x in said)
    assert "LineageBox 99.0.0 is out" in said and "Ollama: The model can't see pictures" in said
    assert any(x.startswith("Ollama: It hasn't answered since") for x in said)
    assert not [x for x in texts(con, who["Cora"])]  # none of it for a member
    sam, cora = client(cfg, con, who["Sam"]), client(cfg, con, who["Cora"])
    alerts = sam.get("/news/alerts")
    assert alerts.status_code == 200 and "LineageBox 99.0.0 is out" in alerts.text
    assert 'data-later="/news/alerts"' in sam.get("/status").text
    assert cora.get("/news/alerts").status_code == 404


def test_events_are_kept_ninety_days(con, cfg):
    old = (datetime.now() - timedelta(days=outbox.KEEP_DAYS + 1)).isoformat(timespec="seconds")
    con.execute("INSERT INTO outbox(at, kind, admins, data) VALUES (?, 'update', 1, '{}')", (old,))
    con.commit()
    outbox.after_pass(con, cfg)
    assert con.execute("SELECT count(*) FROM outbox WHERE at = ?", (old,)).fetchone()[0] == 0


def test_without_accounts_the_house_has_one_list(con, cfg, library):
    scan(con, cfg)
    ingest(con, cfg)
    image = con.execute("SELECT id FROM image WHERE error IS NULL LIMIT 1").fetchone()[0]
    outbox.add(con, "new_photos", data={"ids": [image]})
    outbox.add(con, "damaged", admins=True, data={"files": 2})
    con.commit()
    c = client(cfg, con)
    assert '<span class="count">2</span>' in c.get("/home").text
    page = c.get("/news").text
    assert "1 new photo<" in page and "2 files found damaged" in page
    assert '<span class="count">' not in TestClient(c.app, client=LAN).get("/home").text  # seen, by the house
    assert con.execute("SELECT account_id FROM outbox_seen").fetchall()[0][0] == 0
