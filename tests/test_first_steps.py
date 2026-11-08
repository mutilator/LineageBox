"""What a new LineageBox and a family member see first: the shorter menu (More), the first steps on an admin's Home,
plain words on members' pages, one word for each answer about a face, and light or dark (base.html, first_run.py)."""
import re
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from lineagebox import accounts, first_run, people, settings
from lineagebox.config import Config
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests.conftest import LAN

APP = {"X-Requested-With": "lineagebox"}
STATIC = Path(webapp.__file__).parent / "static"


def read_library(con, cfg):
    scan(con, cfg)
    ingest(con, cfg)
    return [r[0] for r in con.execute("SELECT id FROM image ORDER BY id")]


def add_face(con, image_id, person_id=None, by=None, cluster=None):
    return con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding, person_id,
                                           assigned_by, cluster_id)
                          VALUES (?, 0.1, 0.1, 0.4, 0.4, 0.9, 22, ?, ?, ?, ?)""",
                       (image_id, np.zeros(512, np.float32).tobytes(), person_id, by, cluster)).lastrowid


def signed_in(cfg, con, account_id):
    c = TestClient(webapp.create_app(cfg), client=LAN, follow_redirects=False)
    c.cookies.set(accounts.COOKIE, accounts.new_session(con, account_id, 30))
    return c


def accounts_on(cfg, con, person_id):
    """Family accounts on, with an admin and a member who is person_id: their two browsers."""
    admin = accounts.add(con, "admin@example.com", None, "admin")
    member = accounts.add(con, "emma@example.com", person_id)
    con.commit()
    settings.save(con, cfg, {"accounts_enabled": True})
    return signed_in(cfg, con, admin), signed_in(cfg, con, member)


def nav_of(html):
    start = html.index('<nav id="menu"')
    return html[start:html.index("</nav>", start)]


# ---- the menu --------------------------------------------------------------------------------------------------

def test_the_menu_puts_the_pages_used_most_up_front(cfg, con):
    """Home, People, Family, (Upload,) Folders, then More with the rest; admins keep Status and Settings. The logo
    goes Home. The page open is marked, and More is marked when it is one of its own."""
    client = TestClient(webapp.create_app(cfg), client=LAN)
    html = client.get("/timeline").text
    nav = nav_of(html)
    up_front, more = nav.split('<div class="more', 1)
    assert re.findall(r'<a href="([^"]+)"', up_front) == ["/home", "/people", "/family", "/"]
    assert re.findall(r'<a href="([^"]+)"', more) == ["/timeline", "/map", "/tours", "/albums", "/favourites",
                                                      "/people/name", "/tags", "/cleanup", "/status", "/settings"]
    assert more.startswith(' here">')  # Timeline is under More
    assert 'data-panel="more-menu" aria-expanded="false" aria-controls="more-menu">More' in nav
    assert '<a href="/timeline" aria-current="page">Timeline</a>' in nav
    assert '<a class="brand" href="/home"' in html and 'src="/static/icons/icon.svg"' in html

    nav = nav_of(client.get("/people/name").text)
    assert 'aria-current="page">Name faces' in nav and 'aria-current="page">People' not in nav
    nav = nav_of(client.get("/people").text)
    assert '<a href="/people" aria-current="page">People</a>' in nav and '<div class="more">' in nav
    nav = nav_of(client.get("/favourites").text)
    assert 'aria-current="page">Favourites' in nav and 'aria-current="page">Tours' not in nav


def test_a_family_members_menu(cfg, con):
    emma = people.person_id(con, "Emma Miller")
    _, member = accounts_on(cfg, con, emma)
    nav = nav_of(member.get("/people").text)
    up_front, more = nav.split('<div class="more', 1)
    assert re.findall(r'<a href="([^"]+)"', up_front) == ["/home", "/people", "/family", "/upload", "/folders"]
    assert "/tags" in more and "/cleanup" in more and "/status" not in nav and "/settings" not in nav


def test_light_or_dark_is_chosen_before_the_page_is_drawn(cfg):
    """theme.js, a file of the site's own (no inline script gets past the Content-Security-Policy), comes before the
    stylesheet; the stylesheet has the dark colours for the system's choice and for one made in the browser, and a
    print style; the tree's picture is always drawn light."""
    html = TestClient(webapp.create_app(cfg), client=LAN).get("/people").text
    assert html.index('<script src="/static/theme.js') < html.index('<link rel="stylesheet" href="/static/app.css')
    assert "defer" not in html[html.index('<script src="/static/theme.js'):].split(">", 1)[0]
    for choice in ('data-theme-choice=""', 'data-theme-choice="light"', 'data-theme-choice="dark"'):
        assert choice in html, choice
    assert "lineagebox-theme" in (STATIC / "theme.js").read_text(encoding="utf-8")
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert ':root:not([data-theme="light"])' in css and ':root[data-theme="dark"] {' in css
    assert "@media print" in css and "#map .leaflet-popup-content-wrapper" in css
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    picture = js[js.index("async function treePicture"):]
    assert 'root.dataset.theme = "light"' in picture and 'const background = "#ffffff"' in picture


# ---- the first steps -----------------------------------------------------------------------------------------------

def test_a_new_lineagebox_opens_on_its_first_steps(tmp_path, library):
    cfg = Config(share_root=tmp_path, folders=(), data_dir=tmp_path / "new", update_repository="")
    client = TestClient(webapp.create_app(cfg), client=LAN)
    html = client.get("/").text
    assert "No photos yet" in html and 'id="first-run"' in html and "Pick a folder to watch" in html
    assert "0 of 5 done" in html  # accounts off: turning them on is for when the family is ready
    assert '<a class="step-title" href="/settings#folders">Watch your photos folder</a>' in html
    assert "Turn on family accounts when you" in html and "when you like" in html
    assert "python -m" not in html
    assert 'id="first-run"' in client.get("/home").text

    assert client.post("/api/first-run/hide").status_code == 403  # only from the site's own script
    assert client.post("/api/first-run/hide", headers=APP).status_code == 200
    html = client.get("/").text
    assert 'id="first-run"' not in html and "Pick a folder to watch" in html
    assert 'id="first-run"' not in client.get("/home").text


def test_each_step_ticks_itself_off_from_the_catalog(cfg, con):
    ids = read_library(con, cfg)
    now = settings.current(con, cfg)
    done = {s.key: s.done for s in first_run.steps(con, now)}
    assert done == {"folder": True, "ai": False, "me": False, "groups": False, "tree": False, "invite": False}

    settings.set_problem(con, "ollama", "the model can't look at pictures")
    ai = next(s for s in first_run.steps(con, now) if s.key == "ai")
    assert not ai.done and "the model can't look at pictures" in ai.note
    settings.save(con, cfg, {"describe_enabled": False})  # no AI, on purpose: nothing to check
    now = settings.current(con, cfg)

    # the largest groups: five people named, and no group left larger than the fifth's faces
    named = [people.person_id(con, n) for n in ("Ann Miller", "Ben Miller", "Cat Baker", "Dan Baker")]
    for pid in named:
        for i in ids[:3]:
            add_face(con, i, pid, "user")
    groups = next(s for s in first_run.steps(con, now) if s.key == "groups")
    assert not groups.done and groups.note == "4 of 5 named so far."
    fifth = people.person_id(con, "Eve Baker")
    for i in ids[:3]:
        add_face(con, i, fifth, "user")
    for i in ids[:4]:
        add_face(con, i, cluster=7)  # a group of 4 unnamed faces, more than the fifth person's 3
    assert not next(s for s in first_run.steps(con, now) if s.key == "groups").done
    con.execute("UPDATE face SET person_id = ?, assigned_by = 'user' WHERE cluster_id = 7", (fifth,))
    assert next(s for s in first_run.steps(con, now) if s.key == "groups").done

    settings.put(con, "me", named[0])
    con.execute("INSERT INTO parent(child_id, parent_id) VALUES (?, ?)", (named[0], named[1]))
    con.commit()
    steps = {s.key: s for s in first_run.steps(con, now)}
    assert steps["me"].done and steps["tree"].done and steps["tree"].href == f"/person/{named[0]}"
    assert steps["invite"].optional and not steps["invite"].done
    # all that counts is done: the list goes, and that is noted, so Home doesn't count faces again
    assert first_run.checklist(con, now) is None and settings.get(con, first_run.HIDDEN) == "done"
    client = TestClient(webapp.create_app(cfg), client=LAN)
    assert 'id="first-run"' not in client.get("/home").text


def test_with_family_accounts_inviting_someone_is_a_step_and_only_admins_see_them(cfg, con):
    ids = read_library(con, cfg)
    emma = people.person_id(con, "Emma Miller")
    add_face(con, ids[0], emma, "user")
    con.commit()
    admin = accounts.add(con, "admin@example.com", None, "admin")
    con.commit()
    settings.save(con, cfg, {"accounts_enabled": True})
    invite = next(s for s in first_run.steps(con, settings.current(con, cfg)) if s.key == "invite")
    assert invite.title == "Invite someone in the family" and not invite.optional and not invite.done
    me = next(s for s in first_run.steps(con, settings.current(con, cfg)) if s.key == "me")
    assert me.href == "/accounts"
    html = signed_in(cfg, con, admin).get("/home").text
    assert 'id="first-run"' in html and "of 6 done" in html

    member = signed_in(cfg, con, accounts.add(con, "emma@example.com", emma))
    con.commit()
    assert next(s for s in first_run.steps(con, settings.current(con, cfg)) if s.key == "invite").done
    assert 'id="first-run"' not in member.get("/home").text
    assert member.post("/api/first-run/hide", headers=APP).status_code == 404  # admins only: not there for others


# ---- plain words, and one word for each answer about a face ------------------------------------------------------

def test_members_get_plain_words_on_a_video_and_admins_the_details(cfg, con):
    ids = read_library(con, cfg)
    video = ids[0]
    con.execute("UPDATE image SET media = 'video', codecs = 'hevc/ac3', playable = 'needed' WHERE id = ?", (video,))
    emma = people.person_id(con, "Emma Miller")
    add_face(con, video, emma, "user")
    con.commit()
    admin, member = accounts_on(cfg, con, emma)
    seen = admin.get(f"/image/{video}").text
    assert "hevc/ac3" in seen and "720p copy" in seen
    seen = member.get(f"/image/{video}").text
    assert "hevc" not in seen and "720p" not in seen and "a copy that plays in the browser is being" in seen
    assert '<span class="kbd-hint">← → to step through' in seen  # not on a phone or tablet (app.css)


def test_one_word_for_each_answer_about_a_face(cfg, con):
    """Not them: a face isn't the person it is named, suggested or grouped as. Don't know: nobody knows them, so
    stop asking. The actions behind them are as they were."""
    ids = read_library(con, cfg)
    owen = people.person_id(con, "Owen Miller")
    named = add_face(con, ids[0], owen, "user")
    con.commit()
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get(f"/image/{ids[0]}").text
    fix = page[page.index(f'id="fix{named}"'):]
    fix = fix[:fix.index("</span>")]
    assert f'data-api="/api/faces/{named}/not"' in fix and ">not them</button>" in fix and ">don't know who<" not in fix
    faces = client.get(f"/person/{owen}/faces").text
    assert 'title="Not them: not Owen Miller"' in faces

    for i in ids[1:4]:
        add_face(con, i, cluster=3)
    con.commit()
    groups = client.get("/cleanup/faces?show=groups").text
    card = client.get("/people/name?only=groups").text
    for html in (groups, card):
        assert "Don't know them</button>" in html and ">Hide<" not in html and ">Strangers<" not in html
        assert 'title="Not them: not the same person as the others"' in html
    assert 'data-key="d" data-key-label="Don&#39;t know them' in card or "data-key=\"d\" data-key-label=\"Don't know them" in card


def test_the_family_page_folds_family_tree_programs_away(cfg, con):
    html = TestClient(webapp.create_app(cfg), client=LAN).get("/family").text
    assert '<details class="info-panel" id="gedcom" data-keep-open>' in html
    assert '<body class="family-page"' in html
    summary = html[html.index('id="gedcom"'):]
    assert "GEDCOM" not in summary[:summary.index("</summary>")]


def test_empty_pages_say_what_to_do_in_the_site(tmp_path, library):
    cfg = Config(share_root=tmp_path, folders=("lib",), data_dir=tmp_path / "empty", update_repository="")
    client = TestClient(webapp.create_app(cfg), client=LAN)
    for page in ("/people", "/timeline", "/cleanup/faces?show=groups"):
        html = client.get(page).text
        assert "python -m" not in html and "<code>" not in html.split("<main", 1)[1], page
    assert 'href="/status">Status</a>' in client.get("/timeline").text
