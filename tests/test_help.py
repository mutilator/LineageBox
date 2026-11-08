"""The help panel: every page has its own, and the keys a page offers are on its boxes and buttons."""
import dataclasses
import re
from pathlib import Path

from fastapi.testclient import TestClient

from lineagebox import accounts, people, settings
from lineagebox.web import app as webapp
from lineagebox.web import help as page_help
from tests.conftest import LAN

WEB = Path(webapp.__file__).parent  # the routes are in web/routes/


def test_every_page_has_help():
    code = "".join(f.read_text(encoding="utf-8") for f in WEB.rglob("*.py"))
    pages = set(re.findall(r'page\(request, "([a-z_]+)\.html"', code))
    assert len(pages) > 30  # the routes were found
    assert {p for p in pages if not p.startswith("_")} <= set(page_help.PAGES)  # _name: part of a page
    assert {(href, name) for _, href, name in page_help.GO} <= {(href, name) for href, name, _ in page_help.MENU}


def test_every_page_list_matches_the_menu(cfg, con):
    """The help's "Every page" list has a line for each page of the menu, up front and under More, and no others: an
    admin's menu with Status and Settings, a family member's without them and with their own Folders."""
    def menu_and_list(html):
        menu = html[html.index('<nav id="menu"'):html.index("</nav>", html.index('<nav id="menu"'))]
        listed = html[html.index('<dl class="pages-list">'):]
        listed = listed[:listed.index("</dl>")]
        return (set(re.findall(r'<a href="(/[^"]*)"', menu)) - {"/status#updates"},
                set(re.findall(r'<dt><a href="(/[^"]*)">', listed)))

    home = TestClient(webapp.create_app(cfg), client=LAN)  # no accounts: everyone is an admin, nobody uploads
    shown, listed = menu_and_list(home.get("/people").text)
    assert listed - shown == {"/news"} and shown <= listed  # the bell, at the top, not in the menu
    assert {"/status", "/settings", "/", "/albums", "/people/name", "/tags"} <= shown
    assert "/upload" not in shown and "/folders" not in shown

    emma = people.person_id(con, "Emma Miller")
    accounts.add(con, "admin@example.com", None, "admin")
    con.commit()
    settings.save(con, cfg, {"accounts_enabled": True})
    member = TestClient(webapp.create_app(cfg), client=LAN, follow_redirects=False)
    member.cookies.set(accounts.COOKIE, accounts.new_session(con, accounts.add(con, "emma@example.com", emma), 30))
    shown, listed = menu_and_list(member.get("/people").text)
    assert listed - shown == {"/news"} and shown <= listed
    assert {"/upload", "/folders", "/albums", "/favourites", "/tags", "/cleanup"} <= shown
    assert not {"/status", "/settings", "/"} & shown


def test_the_help_is_about_the_page_it_is_on(cfg, con):
    pid = people.person_id(con, "Lucas Miller")
    con.commit()
    client = TestClient(webapp.create_app(cfg), client=LAN)
    html = client.get(f"/person/{pid}").text
    assert '<dialog id="help"' in html and "Help: A person" in html and "data-help" in html
    for key in ("n", "b", "d", "s", "c", "o", "r", "e", "t", "m", "f"):
        assert f'data-key="{key}"' in html, key
    assert 'data-refocus="e"' in html  # adding a life event leaves the box ready for the next
    assert "Help: Status" in client.get("/status").text
    assert "Help: Settings" in client.get("/settings").text


def test_the_help_says_whose_it_is_and_under_which_licence(cfg):
    """The AGPL's notices: a changed copy has to keep showing them, so the original has to show them."""
    html = TestClient(webapp.create_app(cfg), client=LAN).get("/people").text
    assert "GNU Affero General Public License" in html and "no warranty" in html and "Its source code" not in html
    cfg = dataclasses.replace(cfg, update_repository="example/lineagebox")
    html = TestClient(webapp.create_app(cfg), client=LAN).get("/people").text
    assert '<a href="https://github.com/example/lineagebox" target="_blank" rel="noopener">Its source code</a>' in html
