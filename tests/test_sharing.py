"""Sharing: a member shares a folder of their uploads, one photo or a tour with another account or everyone, and they
see those photos; what isn't theirs can't be shared; it stops with Stop sharing, a switched-off account or an admin's
hiding. And managing uploads from the folder view: whole folders and ticked files to the Removed folder."""
import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from lineagebox import access, accounts, catalog, changes, settings, sharing, tours
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests import family_fixture as fx
from tests.conftest import LAN

APP = {"X-Requested-With": "lineagebox"}


def jpeg(color) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (120, 90), color).save(out, "JPEG")
    return out.getvalue()


@pytest.fixture
def site(con, cfg, library):
    fam = fx.build(con, cfg, library)
    p = fam["people"]
    fam["who"] = {"Sam": fx.account(con, p["Sam"], "sam@example.com", "admin"),
                  "Cora": fx.account(con, p["Cora"], "cora@example.com"),
                  "Tina": fx.account(con, p["Tina"], "tina@example.com"),
                  "Bart": fx.account(con, p["Bart"], "bart@example.com"),
                  "Zed": fx.account(con, p["Zed"], "zed@example.com")}  # nobody's family
    settings.save(con, cfg, {"accounts_enabled": True})
    fam["app"] = webapp.create_app(cfg)
    return fam


def client(site, con, who):
    """A browser signed in as one of the fixture's accounts; all share one app, as people on one server do."""
    c = TestClient(site["app"], client=LAN, follow_redirects=False)
    c.cookies.set(accounts.COOKIE, accounts.new_session(con, site["who"][who]["id"], 30))
    return c


def uploaded(c, con, cfg, files: dict[str, str], folder="") -> dict[str, int]:
    """Upload {path: colour}, let the worker read them, and give each file's photo."""
    for path, color in files.items():
        assert c.post("/api/upload", params={"path": path, "folder": folder}, content=jpeg(color),
                      headers=APP).status_code == 200
    scan(con, cfg)
    ingest(con, cfg)
    return {path: con.execute("SELECT image_id FROM file WHERE path LIKE ? AND NOT missing",
                              (f"%/{path}",)).fetchone()[0] for path in files}


def folder_id(con, path_end: str) -> int:
    return con.execute("SELECT id FROM folder WHERE path LIKE ?", (f"%{path_end}",)).fetchone()[0]


def share(c, **body):
    return c.post("/api/shares", json=body, headers=APP)


def later(site):
    """After a change made straight in the catalog (as the worker makes them): each view is worked out again within
    access.REFRESH seconds; the tests don't wait."""
    site["app"].state.site.viewers.changed()


SHARE_FORM = 'data-api="/api/shares"'


def test_sharing_a_folder_of_uploads(site, con, cfg):
    cora, tina, bart = (client(site, con, w) for w in ("Cora", "Tina", "Bart"))
    photos = uploaded(cora, con, cfg, {"Summer/beach.jpg": "yellow", "Summer/dunes.jpg": "orange"})
    summer = folder_id(con, "/Summer")
    beach = photos["Summer/beach.jpg"]
    assert tina.get(f"/image/{beach}").status_code == 404 and tina.get(f"/folder/{summer}").status_code == 404
    page = cora.get(f"/folder/{summer}").text
    assert SHARE_FORM in page and f'data-api="/api/folders/{summer}/remove"' in page
    assert ">Tina<" in page and "Zed" not in page  # whom she may share with: her part of the family only
    r = share(cora, folder_id=summer, to=str(site["who"]["Tina"]["id"]))
    assert r.status_code == 200, r.text
    assert tina.get(f"/image/{beach}").status_code == 200
    page = tina.get(f"/folder/{summer}").text
    assert "Cora shared this folder with you" in page and f'data-api="/api/folders/{summer}/remove"' not in page
    assert SHARE_FORM not in page
    assert "Summer" in tina.get("/folders").text and "from Cora" in tina.get("/folders").text
    home = tina.get("/home").text  # as a folder card, with a photo of it for its cover, above the newest photos
    assert f'class="folder-card" href="/folder/{summer}"' in home and "folder, from Cora" in home
    assert home.index("Shared with you") < home.index("New photos of the family")
    shas = {catalog.image(con, i)["sha256"] for i in photos.values()}
    assert any(f'src="/t/{sha}"' in home.split("New photos of the family")[0] for sha in shas)
    assert "Move 0 to Removed" not in tina.get(f"/folder/{summer}?select=1").text  # not hers to remove
    assert tina.post(f"/api/folders/{summer}/remove", headers=APP).status_code == 404
    assert tina.post(f"/api/folders/{summer}/remove-files", json={"image_ids": [beach]}, headers=APP).status_code == 404
    # what is added to the folder later is shared too; someone else sees none of it
    later = uploaded(cora, con, cfg, {"Summer/later.jpg": "pink"})["Summer/later.jpg"]
    assert tina.get(f"/image/{later}").status_code == 200
    assert bart.get(f"/image/{beach}").status_code == 404 and bart.get(f"/folder/{summer}").status_code == 404
    # on Cora's list, and an admin's; on Recent changes, to undo
    assert "with <b>Tina</b>" in cora.get("/folders").text
    assert "Summer" in client(site, con, "Sam").get("/folders").text
    assert con.execute("SELECT summary FROM change_log WHERE kind = 'sharing'").fetchone()[0] == \
        "Shared the folder “Summer” with Tina"
    # Tina can't stop Cora's share; Cora can
    share_id = con.execute("SELECT id FROM share").fetchone()[0]
    assert tina.post(f"/api/shares/{share_id}/delete", headers=APP).status_code == 404
    assert cora.post(f"/api/shares/{share_id}/delete", headers=APP).status_code == 200
    assert tina.get(f"/image/{beach}").status_code == 404


def test_an_admin_undoes_a_members_share(site, con, cfg):
    cora, tina, admin = (client(site, con, w) for w in ("Cora", "Tina", "Sam"))
    photo = uploaded(cora, con, cfg, {"cake.jpg": "white"})["cake.jpg"]
    assert share(cora, image_id=photo, to="everyone").status_code == 200
    assert tina.get(f"/image/{photo}").status_code == 200
    change = con.execute("SELECT id FROM change_log WHERE kind = 'sharing'").fetchone()[0]
    assert admin.post(f"/api/changes/{change}/undo", headers=APP).status_code == 200
    assert tina.get(f"/image/{photo}").status_code == 404


def test_shared_with_everyone_until_switched_off_or_hidden(site, con, cfg):
    cora, tina, bart, admin = (client(site, con, w) for w in ("Cora", "Tina", "Bart", "Sam"))
    photo = uploaded(cora, con, cfg, {"garden.jpg": "green"})["garden.jpg"]
    assert SHARE_FORM in cora.get(f"/image/{photo}").text
    assert share(cora, image_id=photo, to="everyone").status_code == 200
    assert share(cora, image_id=photo, to="everyone").status_code == 200  # twice is once
    assert con.execute("SELECT count(*) FROM share").fetchone()[0] == 1
    zed = client(site, con, "Zed")
    for c in (tina, bart, zed):
        assert c.get(f"/image/{photo}").status_code == 200
    assert admin.post(f"/api/images/{photo}/hidden", json={"hidden": True}, headers=APP).status_code == 200
    assert tina.get(f"/image/{photo}").status_code == 404 and cora.get(f"/image/{photo}").status_code == 200
    admin.post(f"/api/images/{photo}/hidden", json={"hidden": False}, headers=APP)
    accounts.update(con, site["who"]["Cora"]["id"], disabled=True)
    con.commit()
    later(site)
    assert tina.get(f"/image/{photo}").status_code == 404  # nothing she shared, while she is switched off


def test_only_what_is_theirs_can_be_shared(site, con, cfg, library):
    cora, admin = client(site, con, "Cora"), client(site, con, "Sam")
    ph = site["photos"]
    picnic = con.execute("SELECT folder_id FROM file WHERE image_id = ?", (ph["cora_bart.jpg"],)).fetchone()[0]
    assert share(cora, folder_id=picnic, to="everyone").status_code == 404  # she sees it; it is the admins'
    assert share(cora, image_id=ph["cora_bart.jpg"], to="everyone").status_code == 404
    assert share(cora, to="everyone").status_code == 400  # nothing to share
    mine = tours.save_tour(con, None, None, tours.clean({"title": "Mine"}), "Mine", site["who"]["Cora"]["id"])
    assert share(cora, tour_id=mine, to=str(site["who"]["Zed"]["id"])).status_code == 404  # not her family
    assert share(cora, tour_id=mine, to=str(site["who"]["Sam"]["id"])).status_code == 404  # an admin sees it anyway
    # an admin shares any folder, with anyone; never the whole library at once
    assert share(admin, folder_id=picnic, to=str(site["who"]["Zed"]["id"])).status_code == 200
    zed = client(site, con, "Zed")
    assert zed.get(f"/image/{ph['cora_bart.jpg']}").status_code == 200
    assert zed.get(f"/folder/{picnic}").status_code == 200
    assert share(admin, folder_id=catalog.root_folder_id(con), to="everyone").status_code == 400
    # an upload an admin moved into the library is no longer hers to share
    photo = uploaded(cora, con, cfg, {"moved.jpg": "navy"})["moved.jpg"]
    share(cora, image_id=photo, to=str(site["who"]["Tina"]["id"]))
    tina = client(site, con, "Tina")
    assert tina.get(f"/image/{photo}").status_code == 200
    con.execute("DELETE FROM upload WHERE image_id = ?", (photo,))
    src = next(cfg.share_root.rglob("moved.jpg"))
    src.rename(library / "Trips" / "moved.jpg")
    scan(con, cfg)
    ingest(con, cfg)
    later(site)
    assert tina.get(f"/image/{photo}").status_code == 404


def test_sharing_a_tour_shares_its_photos(site, con, cfg):
    p, ph = site["people"], site["photos"]
    cora, tina, bart = (client(site, con, w) for w in ("Cora", "Tina", "Bart"))
    # Mia (Cora's husband's sister) is in the scenery photo: Cora sees it, Tina doesn't
    con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_by)
                   VALUES (?, 0.1, 0.1, 0.5, 0.5, 0.9, 25, ?, ?, 'user')""",
                (ph["scenery.jpg"], fx.unit(700).tobytes(), p["Mia"]))
    con.commit()
    spec = tours.clean({"title": "Mia", "who": {"mode": "person", "person_ids": [p["Mia"]]}})
    tour = tours.save_tour(con, None, None, spec, "Photos of Mia", site["who"]["Cora"]["id"])
    assert tina.get(f"/tour/{tour}").status_code == 404 and tina.get(f"/image/{ph['scenery.jpg']}").status_code == 404
    assert SHARE_FORM in cora.get(f"/tour/{tour}").text
    assert share(cora, tour_id=tour, to=str(site["who"]["Tina"]["id"])).status_code == 200
    sha = catalog.image(con, ph["scenery.jpg"])["sha256"]
    page = tina.get(f"/tour/{tour}").text
    assert sha in page and "Cora shared this tour with you" in page and SHARE_FORM not in page
    assert f'href="/tour/{tour}/edit"' not in page
    photo = tina.get(f"/image/{ph['scenery.jpg']}?ctx=tour:{tour}")
    assert photo.status_code == 200 and f"/person/{p['Mia']}" not in photo.text  # named, without a link
    home = tina.get("/home").text.split("New photos of the family")[0]
    assert f'href="/tour/{tour}"' in home and f'src="/t/{sha}"' in home and "tour, from Cora" in home
    listed = tina.get("/tours").text
    assert "Photos of Mia" in listed and "from Cora" in listed
    names = zipfile.ZipFile(io.BytesIO(tina.get(f"/download/tour/{tour}").content)).namelist()
    assert names == ["scenery.jpg"]
    assert tina.post(f"/api/tours/{tour}", json={"name": "Mine now"}, headers=APP).status_code == 404
    # the tour picks again: a new photo of Mia joins it for Tina too, the next time it is opened
    con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_by)
                   VALUES (?, 0.1, 0.1, 0.5, 0.5, 0.9, 25, ?, ?, 'user')""",
                (ph["jim.jpg"], fx.unit(701).tobytes(), p["Mia"]))
    con.commit()
    later(site)
    assert catalog.image(con, ph["jim.jpg"])["sha256"] in tina.get(f"/tour/{tour}").text
    assert tina.get(f"/image/{ph['jim.jpg']}").status_code == 200
    assert bart.get(f"/tour/{tour}").status_code == 404  # shared with Tina only
    # deleting the tour stops it
    assert cora.post(f"/api/tours/{tour}/delete", headers=APP).status_code == 200
    assert tina.get(f"/image/{ph['scenery.jpg']}").status_code == 404


def test_a_shared_tour_passes_on_no_one_elses_shares(site, con, cfg):
    """A tour is shared as its maker's own family and uploads have it: what Tina shared with Cora alone doesn't reach
    Bart through Cora's tour."""
    cora, tina, bart = (client(site, con, w) for w in ("Cora", "Tina", "Bart"))
    theirs = uploaded(tina, con, cfg, {"for_cora.jpg": "teal"})["for_cora.jpg"]
    assert share(tina, image_id=theirs, to=str(site["who"]["Cora"]["id"])).status_code == 200
    everything = tours.save_tour(con, None, None, tours.clean({"title": "All"}), "All", site["who"]["Cora"]["id"])
    sha = catalog.image(con, theirs)["sha256"]
    assert sha in cora.get(f"/tour/{everything}").text  # Cora sees it in her own tour
    assert share(cora, tour_id=everything, to=str(site["who"]["Bart"]["id"])).status_code == 200
    page = bart.get(f"/tour/{everything}").text
    assert sha not in page and catalog.image(con, site["photos"]["rex.jpg"])["sha256"] in page
    assert bart.get(f"/image/{theirs}").status_code == 404
    v = access.compute(con, accounts.get(con, site["who"]["Cora"]["id"]), shared=False)
    assert theirs not in v.images and theirs in access.compute(con, accounts.get(con, site["who"]["Cora"]["id"])).images


def test_removing_a_folder_of_uploads(site, con, cfg, library):
    cora, tina = client(site, con, "Cora"), client(site, con, "Tina")
    photos = uploaded(cora, con, cfg, {"Trip/a.jpg": "red", "Trip/Day 2/b.jpg": "blue", "keep.jpg": "black"})
    trip, mine = folder_id(con, "/Trip"), folder_id(con, f"/{site['people']['Cora']}")
    share(cora, folder_id=trip, to="everyone")
    assert tina.get(f"/image/{photos['Trip/a.jpg']}").status_code == 200
    r = cora.post(f"/api/folders/{trip}/remove", headers=APP)
    assert r.status_code == 200 and r.json()["moved"] == 2 and r.json()["go"] == f"/folder/{mine}"
    removed = cfg.share_root / f"{library.name}-Removed" / "Family uploads" / str(site["people"]["Cora"])
    assert (removed / "Trip" / "a.jpg").is_file() and (removed / "Trip" / "Day 2" / "b.jpg").is_file()
    assert not con.execute("SELECT 1 FROM share").fetchone()  # what was shared of it, no longer
    assert tina.get(f"/image/{photos['Trip/a.jpg']}").status_code == 404
    assert cora.get(f"/image/{photos['keep.jpg']}").status_code == 200
    # her whole uploads folder: back to Folders, as there is nothing of hers above it
    r = cora.post(f"/api/folders/{mine}/remove", headers=APP)
    assert r.json()["moved"] == 1 and r.json()["go"] == "/folders"
    # an admin may remove a library folder, but not a watched one
    admin = client(site, con, "Sam")
    watched = con.execute("SELECT id FROM folder WHERE path = ?", (library.name,)).fetchone()[0]
    assert admin.post(f"/api/folders/{watched}/remove", headers=APP).status_code == 400
    picnic = con.execute("SELECT folder_id FROM file WHERE image_id = ?",
                         (site["photos"]["cora_bart.jpg"],)).fetchone()[0]
    assert cora.post(f"/api/folders/{picnic}/remove", headers=APP).status_code == 404
    assert admin.post(f"/api/folders/{picnic}/remove", headers=APP).json()["moved"] == 1


def test_ticked_photos_leave_only_this_folder(site, con, cfg, library):
    """On a folder's page, Move to Removed takes the ticked photos' files in that folder; their other copies stay."""
    admin = client(site, con, "Sam")
    photo = site["photos"]["rex.jpg"]
    src = next(library.rglob("rex.jpg"))
    (library / "Trips" / "rex copy.jpg").write_bytes(src.read_bytes())
    scan(con, cfg)
    ingest(con, cfg)
    trips = con.execute("SELECT folder_id FROM file WHERE path LIKE '%rex copy.jpg'").fetchone()[0]
    page = admin.get(f"/folder/{trips}?select=1").text
    assert f'data-select-api="/api/folders/{trips}/remove-files"' in page
    r = admin.post(f"/api/folders/{trips}/remove-files", json={"image_ids": [photo]}, headers=APP)
    assert r.json()["moved"] == 1
    assert [f["path"] for f in catalog.image_files(con, photo) if not f["missing"]] == ["lib/Family/Old/rex.jpg"]
    # a member's: only in a folder of her uploads
    cora = client(site, con, "Cora")
    mine = uploaded(cora, con, cfg, {"Box/x.jpg": "gray"})["Box/x.jpg"]
    box = folder_id(con, "/Box")
    assert cora.post(f"/api/folders/{trips}/remove-files", json={"image_ids": [photo]}, headers=APP).status_code == 404
    assert cora.post(f"/api/folders/{box}/remove-files", json={"image_ids": [mine]}, headers=APP).json()["moved"] == 1


def test_shares_go_with_what_they_share(site, con, cfg):
    """Deleting a tour or an account takes its shares with it; changes.KEYS knows the table, to undo."""
    cora = client(site, con, "Cora")
    tour = tours.save_tour(con, None, None, tours.clean({"title": "T"}), "T", site["who"]["Cora"]["id"])
    share(cora, tour_id=tour, to="everyone")
    assert con.execute("SELECT count(*) FROM tour_photo WHERE tour_id = ?", (tour,)).fetchone()[0] > 0
    accounts.remove(con, site["who"]["Cora"]["id"])
    con.commit()
    assert not con.execute("SELECT 1 FROM share").fetchone()
    assert "share" in changes.KEYS and sharing.KINDS == ("folder", "image", "tour")
