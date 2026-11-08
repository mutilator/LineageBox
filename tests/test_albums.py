"""Albums picked by hand: made from the photos one sees, put in order, captioned, a cover picked, shared like a tour
(passing on only the maker's own part of the family), undone; and the family book."""
import io
import json
import re
import zipfile

from fastapi.testclient import TestClient

from lineagebox import access, accounts, albums, catalog, comments, export, sharing
from lineagebox.web import app as webapp
from tests import family_fixture as fx
from tests.conftest import LAN
from tests.test_sharing import APP, SHARE_FORM, client, later, share, site, uploaded  # noqa: F401 (site: a fixture)


def slides_of(page: str) -> list[dict]:
    return json.loads(re.search(r'<script type="application/json" id="slides">(.*?)</script>', page, re.S)[1])


def add(c, name, *image_ids):
    return c.post("/api/albums/add", json={"album": name, "image_ids": list(image_ids)}, headers=APP)


def test_a_member_makes_an_album(site, con, cfg):
    cora = client(site, con, "Cora")
    ph = site["photos"]
    cb, rex, hank_sam = ph["cora_bart.jpg"], ph["rex.jpg"], ph["hank_sam.jpg"]
    # from photos she sees only: one she doesn't makes nothing at all
    assert add(cora, "The picnic", cb, ph["zed.jpg"]).status_code == 404
    assert not con.execute("SELECT 1 FROM album").fetchone()
    r = add(cora, "The picnic", cb, rex)
    assert r.status_code == 200 and "Added 2" in r.json()["message"] and r.json()["undo"]
    album_id = r.json()["album_id"]
    a = albums.get(con, album_id)
    assert a["account_id"] == site["who"]["Cora"]["id"] and a["name"] == "The picnic"
    # by its name again (any case), from a photo's page: at the end; one already in it stays where it is
    assert cora.post("/api/albums/add", json={"album": "the PICNIC", "image_id": hank_sam}, headers=APP).json()[
        "album_id"] == album_id
    assert "already" in add(cora, "The picnic", cb).json()["message"]
    assert albums.photo_ids(con, album_id) == [cb, rex, hank_sam]
    assert cora.get("/api/albums/names").json() == ["The picnic"]
    # in order, with a cover and captions: the album's own, else the photo's family caption
    assert cora.post(f"/api/albums/{album_id}/order", json={"image_ids": [hank_sam, cb]}, headers=APP).json() == {}
    assert albums.photo_ids(con, album_id) == [hank_sam, cb, rex]
    cora.post(f"/api/albums/{album_id}/cover", json={"image_id": rex}, headers=APP)
    cora.post(f"/api/albums/{album_id}/caption", json={"image_id": cb, "text": "  Cora and Bart, at the lake "},
              headers=APP)
    comments.set_caption(con, rex, "Grandpa Rex", None)
    con.commit()
    page = cora.get(f"/album/{album_id}").text
    assert 'data-album="' in page and 'value="Cora and Bart, at the lake"' in page and SHARE_FORM in page
    title, *shown = slides_of(page)
    assert title["title"] == "The picnic" and title["count"] == "3 photos"
    assert [s["id"] for s in shown] == [hank_sam, cb, rex]
    assert [s["caption"] for s in shown] == ["", "Cora and Bart, at the lake", "Grandpa Rex"]
    assert catalog.image(con, rex)["sha256"] in albums.listing(con)[0]["cover"]
    # on a photo's page: the albums it is in; ← → go through the album, Back returns to it
    photo = cora.get(f"/image/{cb}?ctx=album:{album_id}").text
    assert f'href="/album/{album_id}"' in photo and f"/image/{rex}?ctx=album%3A{album_id}" in photo
    assert "The picnic" in cora.get(f"/image/{rex}").text
    assert cora.get("/albums").text.count(f'href="/album/{album_id}"') == 1
    assert 'id="bulk-album"' in cora.get(f"/search?q=&select=1&people={site['people']['Cora']}").text
    # renamed; a photo taken out, then put back with Undo
    cora.post(f"/api/albums/{album_id}", json={"name": "Picnic 2012", "about": "At the lake"}, headers=APP)
    assert albums.get(con, album_id)["about"] == "At the lake"
    undo = cora.post(f"/api/albums/{album_id}/remove", json={"image_ids": [rex]}, headers=APP).json()["undo"]
    assert albums.photo_ids(con, album_id) == [hank_sam, cb] and albums.get(con, album_id)["cover_id"] is None
    assert cora.post(f"/api/changes/{undo}/undo-own", headers=APP).status_code == 200
    assert albums.photo_ids(con, album_id) == [hank_sam, cb, rex] and albums.get(con, album_id)["cover_id"] == rex
    # the zip has the photos she sees
    names = zipfile.ZipFile(io.BytesIO(cora.get(f"/download/album/{album_id}").content)).namelist()
    assert sorted(n for n in names if not n.endswith(".xmp")) == ["cora_bart.jpg", "hank_sam.jpg", "rex.jpg"]


def test_others_may_look_but_not_change(site, con, cfg):
    cora, tina, bart, sam = (client(site, con, w) for w in ("Cora", "Tina", "Bart", "Sam"))
    ph = site["photos"]
    album_id = add(cora, "Ours", ph["cora_bart.jpg"]).json()["album_id"]
    assert tina.get(f"/album/{album_id}").status_code == 404  # not shared: not there for her
    for url, body in ((f"/api/albums/{album_id}", {"name": "x"}), (f"/api/albums/{album_id}/order",
                      {"image_ids": [ph["cora_bart.jpg"]]}), (f"/api/albums/{album_id}/cover",
                      {"image_id": ph["cora_bart.jpg"]}), (f"/api/albums/{album_id}/caption",
                      {"image_id": ph["cora_bart.jpg"], "text": "x"}), (f"/api/albums/{album_id}/remove",
                      {"image_ids": [ph["cora_bart.jpg"]]}), (f"/api/albums/{album_id}/delete", None),
                      ("/api/albums/add", {"album_id": album_id, "image_ids": [ph["rex.jpg"]]}),
                      ("/api/shares", {"album_id": album_id, "to": "everyone"})):
        assert tina.post(url, json=body, headers=APP).status_code == 404, url
    assert share(cora, album_id=album_id, to=str(site["who"]["Tina"]["id"])).status_code == 200
    page = tina.get(f"/album/{album_id}").text
    assert "Cora shared this album with you" in page and 'data-album="' not in page and SHARE_FORM not in page
    assert tina.post(f"/api/albums/{album_id}/delete", headers=APP).status_code == 404  # to look at, not to change
    assert "Ours" in tina.get("/albums").text and "album, from Cora" in tina.get("/folders").text
    # Tina's own album of the same name is another one
    mine = add(tina, "Ours", ph["rex.jpg"]).json()["album_id"]
    assert mine != album_id and albums.photo_ids(con, album_id) == [ph["cora_bart.jpg"]]
    assert bart.get(f"/album/{album_id}").status_code == 404  # shared with Tina only
    # an admin sees and changes any album
    assert sam.get(f"/album/{album_id}").status_code == 200
    assert sam.post(f"/api/albums/{album_id}/caption", json={"image_id": ph["cora_bart.jpg"], "text": "Ok"},
                    headers=APP).status_code == 200


def test_sharing_an_album_shares_its_photos(site, con, cfg):
    """Shared, an album lets them see its photos as its maker's own part of the family has them; what others shared
    with the maker isn't passed on. Deleting it stops it; Undo brings it back, shared as it was."""
    p, ph = site["people"], site["photos"]
    cora, tina, bart = (client(site, con, w) for w in ("Cora", "Tina", "Bart"))
    # Mia (Cora's husband's sister) is in the scenery photo: Cora sees it, Tina doesn't
    con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_by)
                   VALUES (?, 0.1, 0.1, 0.5, 0.5, 0.9, 25, ?, ?, 'user')""",
                (ph["scenery.jpg"], fx.unit(700).tobytes(), p["Mia"]))
    con.commit()
    later(site)
    theirs = uploaded(bart, con, cfg, {"for_cora.jpg": "teal"})["for_cora.jpg"]  # Bart's, shared with Cora alone
    assert share(bart, image_id=theirs, to=str(site["who"]["Cora"]["id"])).status_code == 200
    album_id = add(cora, "Summer", ph["scenery.jpg"], theirs, ph["rex.jpg"]).json()["album_id"]
    assert tina.get(f"/image/{ph['scenery.jpg']}").status_code == 404
    assert share(cora, album_id=album_id, to=str(site["who"]["Tina"]["id"])).status_code == 200
    page = tina.get(f"/album/{album_id}").text
    assert catalog.image(con, ph["scenery.jpg"])["sha256"] in page and catalog.image(con, theirs)["sha256"] not in page
    assert tina.get(f"/image/{ph['scenery.jpg']}?ctx=album:{album_id}").status_code == 200
    assert tina.get(f"/image/{theirs}").status_code == 404
    assert [s["id"] for s in slides_of(page)[1:]] == [ph["scenery.jpg"], ph["rex.jpg"]]
    v = access.compute(con, accounts.get(con, site["who"]["Tina"]["id"]))
    assert album_id in v.albums and ph["scenery.jpg"] in v.granted
    # a photo an admin hid is never shared
    con.execute("UPDATE image SET hidden = 1 WHERE id = ?", (ph["scenery.jpg"],))
    con.commit()
    later(site)
    assert tina.get(f"/image/{ph['scenery.jpg']}").status_code == 404
    con.execute("UPDATE image SET hidden = 0 WHERE id = ?", (ph["scenery.jpg"],))
    con.commit()
    later(site)
    # deleted, and undone
    undo = cora.post(f"/api/albums/{album_id}/delete", headers=APP).json()["undo"]
    assert tina.get(f"/album/{album_id}").status_code == 404 and tina.get(f"/image/{ph['scenery.jpg']}").status_code \
        == 404
    assert cora.post(f"/api/changes/{undo}/undo-own", headers=APP).status_code == 200
    later(site)
    assert sharing.is_shared(con, "album", album_id)
    assert tina.get(f"/album/{album_id}").status_code == 200  # opening it keeps what it passes on again
    assert tina.get(f"/image/{ph['scenery.jpg']}").status_code == 200


def test_the_family_book(site, con, cfg):
    """A person's book, and a branch's, made from what the viewer sees: a member's has no one from outside their part
    of the family."""
    p, ph = site["people"], site["photos"]
    sam, cora = client(site, con, "Sam"), client(site, con, "Cora")
    con.execute("INSERT INTO event(person_id, tag, date, sort_date, place) VALUES (?, 'BIRT', '1950', '1950', 'Elm Town')",
                (p["Rex"],))
    con.execute("UPDATE image SET width = 200, height = 160 WHERE width IS NULL OR width = 0")
    comments.set_caption(con, ph["rex.jpg"], "Rex at the lake", None)
    con.commit()
    page = sam.get(f"/book/person/{p['Rex']}").text
    assert "<h1>Rex</h1>" in page and "born 1950" in page and "Elm Town" in page and "Rex at the lake" in page
    assert "data-print" in page and 'class="portrait' in page and "/static/book.css" in page
    assert "Cora" in page and "Children" in page  # his family
    branch = sam.get(f"/book/branch/{p['Rex']}").text
    assert "The family of Rex" in branch and 'id="p%d"' % p["Bella"] in branch and "Contents" in branch
    assert sam.get("/book/person/99999").status_code == 404
    # Cora sees Rex's side, not Zed, and not Tina's parents
    assert cora.get(f"/book/person/{p['Zed']}").status_code == 404
    mine = cora.get(f"/book/branch/{p['Rex']}").text
    assert "Hank" not in mine and fx.SECRET not in mine and "Zed" not in mine
    assert f'href="/book/person/{p["Rex"]}"' in cora.get(f"/person/{p['Rex']}").text


def test_albums_and_favourites_are_in_the_export(site, con, cfg, tmp_path):
    cora = client(site, con, "Cora")
    ph = site["photos"]
    album_id = add(cora, "Picnic", ph["cora_bart.jpg"], ph["rex.jpg"]).json()["album_id"]
    cora.post(f"/api/albums/{album_id}/caption", json={"image_id": ph["rex.jpg"], "text": "Grandpa"}, headers=APP)
    cora.post(f"/api/images/{ph['rex.jpg']}/favourite", json={"on": True}, headers=APP)
    out = tmp_path / "x.zip"
    counts = export.write(con, cfg, out)
    assert counts["albums"] == 1 and counts["favourites"] == 1
    data = json.loads(zipfile.ZipFile(out).read(export.PHOTOS))
    sha = {i: catalog.image(con, i)["sha256"] for i in ph.values()}
    assert data["albums"] == [{"name": "Picnic", "made_by": {"email": "cora@example.com",
                                                              "person": site["people"]["Cora"]},
                               "created_at": data["albums"][0]["created_at"],
                               "photos": [{"sha256": sha[ph["cora_bart.jpg"]]},
                                          {"sha256": sha[ph["rex.jpg"]], "caption": "Grandpa"}],
                               "cover": sha[ph["cora_bart.jpg"]]}]
    rex = next(r for r in data["photos"] if r["sha256"] == sha[ph["rex.jpg"]])
    assert rex["favourite_of"] == [{"email": "cora@example.com", "person": site["people"]["Cora"]}]


def test_albums_at_home(cfg, con, library):
    """Without family accounts anyone at home makes and changes albums; they are everyone's."""
    from lineagebox.ingest import ingest
    from lineagebox.scan import scan
    scan(con, cfg)
    ingest(con, cfg)
    ids = [r[0] for r in con.execute("SELECT id FROM image ORDER BY id")][:2]
    c = TestClient(webapp.create_app(cfg), client=LAN)
    r = c.post("/api/albums", json={"name": "Zoo day", "image_ids": ids}, headers=APP)
    album_id = int(r.json()["go"].rpartition("/")[2])
    assert albums.get(con, album_id)["account_id"] is None and albums.photo_ids(con, album_id) == ids
    page = c.get(f"/album/{album_id}").text
    assert "Zoo day" in page and "Save as a video" in page and SHARE_FORM not in page  # sharing is for accounts
    assert "Zoo day" in c.get("/tours").text
