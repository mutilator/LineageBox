"""Members' uploads (into their own folder, checked, never half-read), downloads as zips of only what they see, and
photos and folders an admin hides from members."""
import asyncio
import io
import shutil
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from lineagebox import access, accounts, settings, tours, uploads
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests import family_fixture as fx
from tests.conftest import LAN

APP = {"X-Requested-With": "lineagebox"}


def jpeg(color="red") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (120, 90), color).save(out, "JPEG")
    return out.getvalue()


@pytest.fixture
def site(con, cfg, library):
    fam = fx.build(con, cfg, library)
    p = fam["people"]
    fam["who"] = {"Sam": fx.account(con, p["Sam"], "sam@example.com", "admin"),
                  "Cora": fx.account(con, p["Cora"], "cora@example.com"),
                  "Tina": fx.account(con, p["Tina"], "tina@example.com")}
    settings.save(con, cfg, {"accounts_enabled": True})
    return fam


def client(cfg, con, account, app=None):
    """A browser signed in as account; clients of one app share what it keeps (each account's view)."""
    c = TestClient(app or webapp.create_app(cfg), client=LAN, follow_redirects=False)
    c.cookies.set(accounts.COOKIE, accounts.new_session(con, account["id"], 30))
    return c


def upload(c, path, data, folder=""):
    return c.post("/api/upload", params={"path": path, "folder": folder}, content=data, headers=APP)


def test_uploads_go_into_their_own_folder(site, cfg, con, library):
    cora = client(cfg, con, site["who"]["Cora"])
    pid = site["people"]["Cora"]
    r = upload(cora, "Trip/beach.jpg", jpeg(), folder="Summer 2025")
    assert r.status_code == 200, r.text
    folder = f"{library.name}/Family uploads/{pid}"
    assert r.json()["saved"] == f"{folder}/Summer 2025/Trip/beach.jpg"
    assert (cfg.share_root / folder / "Summer 2025" / "Trip" / "beach.jpg").read_bytes() == jpeg()
    assert accounts.get(con, site["who"]["Cora"]["id"])["upload_folder"] == folder
    assert settings.get(con, "run_requested")  # the worker is asked to come
    again = upload(cora, "Trip/beach.jpg", jpeg("blue"), folder="Summer 2025")
    assert again.json()["saved"].endswith("Trip/beach (2).jpg")  # never over another
    assert "Uploaded so far" not in cora.get("/upload").text  # nothing read yet: the worker hasn't come
    scan(con, cfg)
    ingest(con, cfg)
    page = cora.get("/upload").text
    assert "Uploaded so far" in page and "your uploads folder" in page


def test_no_uploads_while_the_photo_folder_is_away(site, cfg, con, library):
    """An unmounted share is an empty folder: an upload written there would be hidden once it is mounted again."""
    scan(con, cfg)
    for entry in list(library.iterdir()):  # the mount point, with nothing mounted on it
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    r = upload(client(cfg, con, site["who"]["Cora"]), "beach.jpg", jpeg())
    assert r.status_code == 400 and "can't be reached" in r.json()["detail"]
    assert not any(library.iterdir())


@pytest.mark.parametrize("path, data, why", [
    ("../../evil.jpg", None, "dot"), (".hidden.jpg", None, "dot"), ("x.svg", b"<svg onload=alert(1)>", "only photos"),
    ("page.html", b"<script>", "only photos"), ("fake.jpg", b"<html><script>alert(1)</script></html>", "isn't a JPG"),
    ("empty.jpg", b"", "is empty"), ("clip.mp4", b"\xff\xd8\xff not a video at all", "isn't a MP4"),
    ("Thumbs/a.jpg", None, "never shown here"), ("Trip-Removed/a.jpg", None, "never shown here"),
])
def test_uploads_that_are_refused(site, cfg, con, path, data, why):
    cora = client(cfg, con, site["who"]["Cora"])
    r = upload(cora, path, jpeg() if data is None else data)
    assert r.status_code == 400 and why in r.json()["detail"]
    root = cfg.share_root
    assert not list(root.rglob("evil.jpg")) and not list(root.rglob("*.part"))
    assert not [p for p in root.rglob("*") if p.suffix in (".svg", ".html")]


def test_names_are_cleaned_and_stay_inside(site, cfg, con, library):
    cora = client(cfg, con, site["who"]["Cora"])
    folder = cfg.share_root / library.name / "Family uploads" / str(site["people"]["Cora"])
    for path in ("C:\\Users\\me\\a.jpg", "/etc/b.jpg", "x/../../c.jpg", "d\x00e.jpg", "con.jpg", "f<>:|?.jpg"):
        r = upload(cora, path, jpeg())
        if r.status_code == 200:
            saved = cfg.share_root / r.json()["saved"]
            assert folder in saved.parents and saved.is_file()
    assert {p.name for p in folder.rglob("*.jpg")} >= {"a.jpg", "b.jpg", "de.jpg", "_con.jpg", "f.jpg"}


def test_a_long_name_keeps_its_type(site, cfg, con):
    r = upload(client(cfg, con, site["who"]["Cora"]), "é" * 110 + ".jpg", jpeg())  # 224 bytes, as disks count
    assert r.status_code == 200 and r.json()["saved"].endswith("é.jpg")
    assert len(r.json()["saved"].rpartition("/")[2].encode()) == uploads.NAME_BYTES


def test_two_accounts_never_share_a_folder(site, cfg, con, library):
    """An account tied to the wrong person keeps the folder it uploaded into once it is put right."""
    p = site["people"]
    first = fx.account(con, p["Bart"], "first@example.com")
    assert upload(client(cfg, con, first), "a.jpg", jpeg()).status_code == 200
    con.execute("UPDATE account SET person_id = ? WHERE id = ?", (p["Milo"], first["id"]))
    con.commit()
    bart = fx.account(con, p["Bart"], "bart@example.com")
    r = upload(client(cfg, con, bart), "b.jpg", jpeg())
    assert r.json()["saved"] == f"{library.name}/Family uploads/{p['Bart']}-{bart['id']}/b.jpg"


def test_uploads_follow_the_watched_folders(site, cfg, con, library):
    cora = client(cfg, con, site["who"]["Cora"])
    pid = site["people"]["Cora"]
    assert upload(cora, "one.jpg", jpeg()).status_code == 200
    (cfg.share_root / "Newer").mkdir()
    settings.watch(con, cfg, "Newer")
    settings.unwatch(con, cfg, library.name)
    cora = client(cfg, con, site["who"]["Cora"])  # a site that has seen the change
    r = upload(cora, "two.jpg", jpeg())  # her folder is no longer read, so it moves, keeping its name
    assert r.status_code == 200 and r.json()["saved"] == f"Newer/Family uploads/{pid}/two.jpg"
    settings.watch(con, cfg, library.name)
    settings.save(con, cfg, {"uploads_into": "Newer"})
    with pytest.raises(settings.Invalid, match="Members' uploads go into Newer"):
        settings.unwatch(con, cfg, "Newer")
    with pytest.raises(settings.Invalid, match="skipped"):
        settings.save(con, cfg, {"uploads_folder": "Previews"})


def test_a_full_disk_or_a_folder_that_cant_be_written(site, cfg, con, monkeypatch):
    cora = client(cfg, con, site["who"]["Cora"])
    real = shutil.disk_usage

    def usage(path):  # the uploads folder's disk is full; the media root's own disk is not
        u = real(path)
        return u._replace(free=0) if "Family uploads" in str(path) else u

    monkeypatch.setattr(uploads.shutil, "disk_usage", usage)
    r = upload(cora, "a.jpg", jpeg())
    assert r.status_code == 400 and "nearly full" in r.json()["detail"]
    monkeypatch.setattr(uploads.shutil, "disk_usage", real)

    async def refused(*args):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(uploads, "receive", refused)
    r = upload(cora, "a.jpg", jpeg())
    assert r.status_code == 503 and "Tell an admin" in r.json()["detail"]
    assert con.execute("SELECT 1 FROM activity WHERE message LIKE '%cora@example.com%Permission denied%'").fetchone()


def test_too_big_and_half_written(cfg, tmp_path):
    async def chunks():
        for _ in range(4):
            yield b"\xff\xd8\xff" + b"x" * 1000
    target = tmp_path / "big.jpg"
    with pytest.raises(uploads.TooBig):
        asyncio.run(uploads.receive(chunks(), target, 2500))
    assert not target.exists() and not list(tmp_path.glob(".*.part"))  # nothing kept


def test_part_files_are_never_scanned(site, cfg, con, library):
    part = library / "Family uploads" / "9" / ".half.jpg.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(jpeg()[:100])
    scan(con, cfg)
    assert not con.execute("SELECT 1 FROM file WHERE path LIKE '%half%'").fetchone()


def test_who_sees_an_upload(site, cfg, con, library):
    cora, tina = client(cfg, con, site["who"]["Cora"]), client(cfg, con, site["who"]["Tina"])
    upload(cora, "garden.jpg", jpeg("green"))
    scan(con, cfg)
    ingest(con, cfg)
    image = con.execute("SELECT image_id FROM file WHERE path LIKE '%garden.jpg'").fetchone()[0]
    assert cora.get(f"/image/{image}").status_code == 200  # hers, with nobody in it
    assert "You uploaded it" in cora.get(f"/image/{image}").text and "Remove photo" in cora.get(f"/image/{image}").text
    assert tina.get(f"/image/{image}").status_code == 404
    # someone Tina sees is in it: then she does too
    con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_by)
                   VALUES (?, 0.1, 0.1, 0.5, 0.5, 0.9, 25, ?, ?, 'user')""",
                (image, fx.unit(5).tobytes(), site["people"]["Gus"]))
    con.commit()
    tina = client(cfg, con, site["who"]["Tina"])
    assert tina.get(f"/image/{image}").status_code == 200
    assert "Remove photo" not in tina.get(f"/image/{image}").text  # not hers to remove
    # recorded as hers, so it stays hers when an admin moves the file into the library
    assert accounts.claim_uploads(con) == 1
    src = next(cfg.share_root.rglob("garden.jpg"))
    src.rename(library / "Family" / "garden.jpg")
    scan(con, cfg)
    ingest(con, cfg)
    assert image in access.compute(con, accounts.get(con, site["who"]["Cora"]["id"])).images
    # she can remove only what she uploaded: the copy now in the library is the admins'
    assert cora.post(f"/api/images/{image}/remove", headers=APP).status_code == 404


def test_removing_her_own_upload(site, cfg, con):
    cora = client(cfg, con, site["who"]["Cora"])
    upload(cora, "oops.jpg", jpeg("purple"))
    scan(con, cfg)
    ingest(con, cfg)
    image = con.execute("SELECT image_id FROM file WHERE path LIKE '%oops.jpg'").fetchone()[0]
    r = cora.post(f"/api/images/{image}/remove", headers=APP)
    assert r.status_code == 200 and r.json()["moved"] == 1  # her only copy too: it is hers
    assert not (cfg.share_root / r.json().get("path", "lib/Family uploads/9/oops.jpg")).exists()
    assert list(cfg.share_root.glob("lib-Removed/Family uploads/*/oops.jpg"))  # in the Removed folder, to put back


def test_uploading_needs_an_account(cfg, con, library):
    c = TestClient(webapp.create_app(cfg), client=LAN)
    r = c.post("/api/upload", params={"path": "a.jpg"}, content=jpeg(), headers=APP)
    assert r.status_code == 400 and "family accounts" in r.json()["detail"]


def test_downloads_hold_only_what_they_see(site, cfg, con):
    cora = client(cfg, con, site["who"]["Cora"])
    ph = site["photos"]
    ids = [ph["cora_bart.jpg"], ph["rex.jpg"], ph["zed.jpg"], ph["hank.jpg"]]
    r = cora.post("/download", data={"image": [str(i) for i in ids]})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert sorted(n for n in names if not n.endswith(".xmp")) == ["cora_bart.jpg", "rex.jpg"]
    assert "cora_bart.jpg.xmp" in names  # its names, beside it (sidecars.py), only those she sees
    assert fx.SECRET not in "".join(z.read(n).decode("utf-8") for n in names if n.endswith(".xmp"))
    only_hidden = cora.post("/download", data={"image": [str(ph["zed.jpg"])]})
    assert only_hidden.status_code == 404
    tour = tours.save_tour(con, None, None, tours.clean({"title": "All"}), "All", site["who"]["Cora"]["id"])
    r = cora.get(f"/download/tour/{tour}")
    got = sorted(n for n in zipfile.ZipFile(io.BytesIO(r.content)).namelist() if not n.endswith(".xmp"))
    assert got == sorted(n for n, i in ph.items() if i in access.compute(con, site["who"]["Cora"]).images)


def test_admins_hide_photos_and_folders(site, cfg, con):
    app = webapp.create_app(cfg)
    admin, cora = client(cfg, con, site["who"]["Sam"], app), client(cfg, con, site["who"]["Cora"], app)
    ph = site["photos"]
    photo = ph["cora_bart.jpg"]
    assert "Hide from family members" in admin.get(f"/image/{photo}").text
    assert admin.post(f"/api/images/{photo}/hidden", json={"hidden": True}, headers=APP).status_code == 200
    assert cora.get(f"/image/{photo}").status_code == 404
    admin.post(f"/api/images/{photo}/hidden", json={"hidden": False}, headers=APP)
    assert cora.get(f"/image/{photo}").status_code == 200
    folder = con.execute("SELECT folder_id FROM file WHERE image_id = ?", (ph["rex.jpg"],)).fetchone()[0]
    parent = con.execute("SELECT parent_id FROM folder WHERE id = ?", (folder,)).fetchone()[0]  # lib/Family
    admin.post(f"/api/folders/{parent}/hidden", json={"hidden": True}, headers=APP)
    assert "Hidden from family members" in admin.get(f"/folder/{parent}").text
    assert cora.get(f"/image/{ph['rex.jpg']}").status_code == 404  # in a folder below it
    assert cora.post(f"/api/images/{photo}/hidden", json={"hidden": False}, headers=APP).status_code == 404
