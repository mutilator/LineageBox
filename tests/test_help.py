"""The help panel: every page has its own, and the keys a page offers are on its boxes and buttons."""
import dataclasses
import re
from pathlib import Path

from fastapi.testclient import TestClient

from lineagebox import people
from lineagebox.web import app as webapp
from lineagebox.web import help as page_help
from tests.conftest import LAN

WEB = Path(webapp.__file__).parent  # the routes are in web/routes/


def test_every_page_has_help():
    code = "".join(f.read_text(encoding="utf-8") for f in WEB.rglob("*.py"))
    pages = set(re.findall(r'page\(request, "([a-z_]+)\.html"', code))
    assert len(pages) > 30  # the routes were found
    assert {p for p in pages if not p.startswith("_")} <= set(page_help.PAGES)  # _name: part of a page
    assert {name for _, name, _ in page_help.MENU} == {name for _, _, name in page_help.GO}


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
