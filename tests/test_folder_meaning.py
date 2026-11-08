"""What a folder means, corrected by an admin on its page: kept as theirs, flowed into its photos' dates and search,
and undone from Recent changes."""
from fastapi.testclient import TestClient

from lineagebox import catalog, search
from lineagebox.web import app as webapp
from tests import family_fixture as fx
from tests.conftest import LAN

APP = {"X-Requested-With": "lineagebox"}


def _folder(con, path_end):
    return con.execute("SELECT * FROM folder WHERE path LIKE ?", (f"%{path_end}",)).fetchone()


def test_a_folder_corrected_by_hand_flows_into_its_photos(cfg, con, library):
    fam = fx.build(con, cfg, library)
    picnic = _folder(con, "Family/Picnic")
    photo = fam["photos"]["cora_bart.jpg"]
    con.execute("UPDATE image SET taken_at_source = 'mtime' WHERE id = ?", (photo,))  # only a file time
    con.commit()
    home = TestClient(webapp.create_app(cfg), client=LAN)
    assert "What this folder means" in home.get(f"/folder/{picnic['id']}").text
    r = home.post(f"/api/folders/{picnic['id']}/meaning", headers=APP,
                  json={"place": "Lake Tahoe", "event": "Grandpa's picnic", "when": "June 1990", "people": "Bart, Al"})
    assert r.status_code == 200, r.text
    row = _folder(con, "Family/Picnic")
    assert (row["source"], row["kind"], row["place"], row["year"], row["month"], row["day"]) == \
        ("user", "event", "Lake Tahoe", 1990, 6, None)
    assert catalog.image(con, photo)["taken_at"].startswith("1990-06")  # dated by the folder now
    assert photo in [r["id"] for r in search.query(con, "tahoe")]
    page = home.get(f"/folder/{picnic['id']}").text
    assert 'value="Lake Tahoe"' in page and "set by hand" in page

    assert home.post(f"/api/folders/{picnic['id']}/meaning", json={"when": "the 1980s"}, headers=APP).status_code == 400

    assert home.post(f"/api/changes/{r.json()['undo']}/undo-own", headers=APP).status_code == 200
    row = _folder(con, "Family/Picnic")
    assert row["source"] == picnic["source"] and row["place"] == picnic["place"]
    assert photo not in [r["id"] for r in search.query(con, "tahoe")]

    home.post(f"/api/folders/{picnic['id']}/meaning", json={"place": "Lake Tahoe"}, headers=APP)
    assert home.post(f"/api/folders/{picnic['id']}/meaning/again", headers=APP).status_code == 200
    row = _folder(con, "Family/Picnic")
    assert row["source"] == "auto" and row["kind"] is None  # the AI reads its name again on the next pass


def test_only_an_admin_corrects_a_folder(cfg, con, library):
    from lineagebox import accounts, settings
    fam = fx.build(con, cfg, library)
    fx.account(con, fam["people"]["Sam"], "sam@example.com", "admin")
    cora = fx.account(con, fam["people"]["Cora"], "cora@example.com")
    settings.save(con, cfg, {"accounts_enabled": True})
    member = TestClient(webapp.create_app(cfg), client=LAN, follow_redirects=False)
    member.cookies.set(accounts.COOKIE, accounts.new_session(con, cora["id"], 30))
    picnic = _folder(con, "Family/Picnic")
    assert member.post(f"/api/folders/{picnic['id']}/meaning", json={"place": "x"}, headers=APP).status_code == 404
    assert _folder(con, "Family/Picnic")["place"] == picnic["place"]
