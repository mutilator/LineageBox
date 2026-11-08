"""Other languages (i18n.py, docs/translating.md): with English only nothing changes; an installed catalog translates
the pages, the help, the scripts' text and the formats of numbers and dates; each visitor gets their own language;
and no translation, whatever quotes or brackets it holds, can break a page's markup or its data for the scripts."""
import gettext
import html.parser
import json
import re
import struct

import pytest
from fastapi.testclient import TestClient

from lineagebox import i18n, storage
from lineagebox.web import app as webapp
from lineagebox.web import help as page_help
from lineagebox.web.common import fmt_date
from tests import family_fixture as fx
from tests.conftest import LAN

APP = {"X-Requested-With": "lineagebox"}
CTX = "\x04"  # a message's context and its text, as a .mo file keeps them


def write_mo(path, messages: dict) -> None:
    """A compiled catalog (what pybabel compile writes), so the tests need no Babel: {msgid: msgstr}, a plural as
    {"one\\0many": "form0\\0form1"}, a context as "context\\x04msgid"."""
    messages = {"": "Content-Type: text/plain; charset=UTF-8\nPlural-Forms: nplurals=2; plural=(n != 1);\n",
                **messages}
    keys = sorted(messages)
    ids = strs = b""
    spans = []
    for k in keys:
        kb, vb = k.encode(), messages[k].encode()
        spans.append((len(ids), len(kb), len(strs), len(vb)))
        ids += kb + b"\0"
        strs += vb + b"\0"
    n = len(keys)
    start = 7 * 4 + 16 * n
    table = b"".join(struct.pack("<2I", ln, start + o) for o, ln, _, _ in spans)
    table += b"".join(struct.pack("<2I", ln, start + len(ids) + o) for _, _, o, ln in spans)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<7I", 0x950412de, 0, n, 7 * 4, 7 * 4 + 8 * n, 0, 0) + table + ids + strs)


@pytest.fixture
def family(con, cfg, library):
    return fx.build(con, cfg, library)


@pytest.fixture
def xx(tmp_path, monkeypatch):
    """A made-up language "xx" installed, with a few messages translated (quotes and all)."""
    folder = tmp_path / "locale" / "xx" / "LC_MESSAGES"
    write_mo(folder / "lineagebox.mo", {
        "Skip to the page": 'Zur "Seite" springen',
        "Search photos": "Fotos durchsuchen: \"o'\" <b>",
        "Close help": "Hilfe zu",
        "open or close this help": "diese Hilfe öffnen oder schließen",
        f"thousands separator in numbers, as in 12,345{CTX},": ".",
        f"decimal mark in numbers, as in 3.5{CTX}.": ",",
        f"a date, as Jun 3, 2012{CTX}{{month}} {{day}}, {{year}}": "{day}. {month} {year}",
        f"month, short{CTX}Jun": "Juni",
        f"the name of this language, in this language{CTX}English": "Xxisch",
    })
    write_mo(folder / "lineagebox-js.mo", {"Done.": "Fertig.", "{n} photo\0{n} photos": "{n} Foto\0{n} Fotos"})
    monkeypatch.setattr(i18n, "LOCALE", tmp_path / "locale")
    i18n.forget()
    yield "xx"
    i18n.forget()


def test_english_only_changes_nothing(family, cfg):
    assert i18n.installed() == ("en",)
    assert i18n.choose("de-DE,de;q=0.9", "fr", "es") == "en"
    c = TestClient(webapp.create_app(cfg), client=LAN)
    page = c.get("/people", headers={"accept-language": "de"}).text
    assert '<html lang="en">' in page and "lineagebox-text" not in page and "/api/language" not in page
    assert "Skip to the page" in page


def test_the_formats_in_english_are_as_before():
    for iso in ("2012-06-03T10:05:00", "2012-06-03T00:00:00", "1987-12-31", None, ""):
        for with_time in (True, False):
            assert i18n.date(iso, with_time) == fmt_date(iso, with_time)
    for n in (0, 999, 1023, 1024, 5 * 2**20, int(99.96 * 2**30), 100 * 2**30, int(3.6 * 2**40), None):
        assert i18n.size(n) == storage.fmt_size(n)
    for n in (0, 1234, 1234567, 3.5, 1234.5, -1200):
        assert i18n.number(n) == f"{n:,}"
    assert [i18n.month_name(m) for m in (1, 12)] == ["January", "December"]
    assert i18n.weekday_name(0) == "Mon"


def test_choosing_a_language(xx):
    assert i18n.installed() == ("en", "xx")
    assert i18n.choose("xx-YY,en;q=0.5") == "xx"
    assert i18n.choose("de,en;q=0.5") == "en"
    assert i18n.choose("de", default="xx") == "xx"  # [web] language, when the browser asks for none of them
    assert i18n.choose("xx", chosen="en") == "en"  # picked in this browser
    assert i18n.choose("xx;q=0, en") == "en"


def test_a_page_in_another_language(xx, family, cfg):
    c = TestClient(webapp.create_app(cfg), client=LAN)
    page = c.get("/people", headers={"accept-language": "xx"}).text
    assert '<html lang="xx">' in page
    # HTML text: the translation as it is, its quotes as character references, so they can't end an attribute
    assert "Zur &#34;Seite&#34; springen" in page
    # plain text in an attribute: escaped like any other text
    assert 'aria-label="Fotos durchsuchen: &#34;o&#39;&#34; &lt;b&gt;"' in page
    # the help, in the viewer's language; and the choice of language in the menu
    assert "diese Hilfe öffnen oder schließen" in page and 'aria-label="Hilfe zu"' in page
    assert ">Xxisch</button>" in page and "data-api=\"/api/language\"" in page
    # the scripts' catalog, with each plural form and the form for each number
    found = re.search(r'<script type="application/json" id="lineagebox-text">(.*?)</script>', page).group(1)
    text = json.loads(found)
    assert text["text"]["Done."] == "Fertig." and text["text"]["{n} photo"] == ["{n} Foto", "{n} Fotos"]
    assert text["plural"][:3] == [1, 0, 1]
    # and English for whoever asks for it
    assert "Skip to the page" in c.get("/people", headers={"accept-language": "en-GB"}).text


def test_the_formats_follow_the_language(xx):
    with i18n.speaking("xx"):
        assert i18n.number(1234567.5) == "1.234.567,5"
        assert i18n.date("2012-06-03T00:00:00") == "3. Juni 2012"
        assert i18n.size(int(74.8 * 2**30)) == "74,8 GB"
        assert page_help.everywhere()[0] == ("?", "diese Hilfe öffnen oder schließen")
    assert i18n.number(1234567.5) == "1,234,567.5"  # outside a request: English


def test_a_browser_keeps_its_choice(xx, family, cfg):
    c = TestClient(webapp.create_app(cfg), client=LAN)
    r = c.post("/api/language", json={"lang": "xx"}, headers=APP)
    assert r.status_code == 200 and r.json() == {"reload": True}
    assert '<html lang="xx">' in c.get("/people", headers={"accept-language": "en"}).text
    assert c.post("/api/language", json={"lang": "klingon"}, headers=APP).status_code == 400
    c.post("/api/language", json={"lang": ""}, headers=APP)  # back to what the browser asks for
    assert '<html lang="en">' in c.get("/people", headers={"accept-language": "en"}).text


class _Shape(html.parser.HTMLParser):
    """A page's markup without its words: each tag and its attributes' names, and the JSON it hands its scripts."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.json, self._in_json = [], [], False

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, tuple(sorted(k for k, _ in attrs))))
        for k, v in attrs:
            if k == "data-body" and v:
                self.json.append(json.loads(v))
        self._in_json = tag == "script" and ("type", "application/json") in attrs

    def handle_endtag(self, tag):
        self.tags.append(("/" + tag, ()))
        self._in_json = False

    def handle_data(self, data):
        if self._in_json and data.strip():
            json.loads(data)  # still JSON


class _Pseudo(gettext.NullTranslations):
    """Every message translated, with double quotes and apostrophes in it (a < would be markup: the pages' text is
    HTML, and a translator writes &lt; for one, as the English does)."""
    @staticmethod
    def mark(m):
        return "\"«o'» " + m + ' "x\'"'

    def gettext(self, m):
        return self.mark(m)

    def ngettext(self, s, p, n):
        return self.mark(s if n == 1 else p)

    def pgettext(self, c, m):
        return m if "{" in m or len(m) == 1 else self.mark(m)  # the formats' patterns and separators stay

    def npgettext(self, c, s, p, n):
        return self.mark(s if n == 1 else p)


def test_no_translation_can_break_a_page(family, cfg, con, monkeypatch):
    """The safety net for every template's text: in a made-up language whose every message holds quotes and
    apostrophes, each page keeps exactly the tags and attributes it has in English, and the JSON it hands its
    scripts (data-body, application/json) is the same."""
    p, ph = family["people"], family["photos"]
    pages = ["/home", "/people", "/people/name", "/family", f"/person/{p['Cora']}", f"/image/{ph['cora_bart.jpg']}",
             f"/image/{ph['hank_sam.jpg']}", "/timeline", "/search?q=party", "/tags", "/cleanup", "/status",
             "/settings", "/tours", "/albums", "/upload", "/map", "/news"]
    c = TestClient(webapp.create_app(cfg), client=LAN)
    english = {}
    for url in pages:
        r = c.get(url)
        assert r.status_code == 200, url
        english[url] = _Shape()
        english[url].feed(r.text)
    monkeypatch.setattr(i18n, "installed", lambda: ("en", "zz"))
    real = i18n.translations
    monkeypatch.setattr(i18n, "translations", lambda lang, domain=i18n.DOMAIN:
                        _Pseudo() if lang == "zz" else real(lang, domain))
    for url in pages:
        r = c.get(url, headers={"accept-language": "zz"})
        assert r.status_code == 200, url
        assert "«o" in r.text, url  # it is in the made-up language
        shape = _Shape()
        shape.feed(r.text)
        assert shape.tags == english[url].tags, url
        assert shape.json == english[url].json, url  # what the buttons send is never translated


def test_the_scrubber_names_its_months_in_the_language(xx):
    """A long list's months at the side (pieces.marks) are named as the page's headings are, not in English."""
    from lineagebox.web import pieces
    rows = [{"id": i, "taken_at": f"2012-0{5 + i // 60}-03T10:00:00"} for i in range(120)]
    with i18n.speaking("xx"):
        months = pieces.marks(rows)[0]["months"]
    assert [m["label"] for m in months] == ["May", "Juni"]  # "May" isn't translated in xx
    assert [m["label"] for m in pieces.marks(rows)[0]["months"]] == ["May", "Jun"]


def test_plain_text_and_its_values():
    """_() in a template: plain text, escaped where it is shown; a value that is HTML already (a macro's output) stays
    HTML, and then the message around it is escaped here; %% is a % sign."""
    from markupsafe import Markup, escape
    assert i18n.text("Move %(what)s?", what="<b>") == "Move <b>?"  # a str: escaped by the page, as any text
    said = i18n.text("Is a < b in %(what)s?", what=Markup("<i>x</i>"))
    assert said == Markup("Is a &lt; b in <i>x</i>?") and escape(said) == said
    assert i18n.text("100%% sure") == "100% sure"
    assert i18n._asked("xx;q=1.2.3, de;q=0.5, en") == ["en", "de"]  # a weight that isn't a number: not asked for


def test_a_language_never_reaches_another_request(xx, family, cfg):
    """The language is set for each request alone (i18n.speaking, in the gate): pages answered at the same time on
    the app's threads each keep their own, and nothing outside a request is left speaking it."""
    import threading
    app = webapp.create_app(cfg)
    wrong = []

    def ask(lang, times=6):
        c = TestClient(app, client=LAN)
        for _ in range(times):
            page = c.get("/people", headers={"accept-language": lang}).text
            if f'<html lang="{lang}">' not in page:
                wrong.append(lang)

    threads = [threading.Thread(target=ask, args=(lang,)) for lang in ("xx", "en", "xx", "en")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert wrong == [] and i18n.language() == "en"
    seen = []
    with i18n.speaking("xx"):  # work started in a thread of its own (a reading, a download) speaks English
        t = threading.Thread(target=lambda: seen.append(i18n.language()))
        t.start()
        t.join()
    assert seen == ["en"]
