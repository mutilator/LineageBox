import dataclasses
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests.conftest import LAN, rel, write_jpeg

ZOO = rel("2012", "Zoo")
APP = {"X-Requested-With": "lineagebox"}


@pytest.fixture
def client(con, cfg, library):
    # two more photos in 2012\Zoo so the folder has a first, middle and last image
    write_jpeg(library.parent / ZOO / "DSC_0002.jpg", color="navy", taken="2012:06:03 11:00:00")
    write_jpeg(library.parent / ZOO / "DSC_0003.jpg", color="teal", taken="2012:06:03 12:00:00")
    scan(con, cfg)
    ingest(con, cfg)
    return TestClient(webapp.create_app(cfg), client=LAN)


def folder_id(con, path):
    return con.execute("SELECT id FROM folder WHERE path = ?", (path,)).fetchone()[0]


def image_id(con, path):
    return con.execute("SELECT image_id FROM file WHERE path = ?", (path,)).fetchone()[0]


def test_empty_catalog(tmp_path, library):
    from lineagebox.config import Config
    c = TestClient(webapp.create_app(Config(share_root=tmp_path, folders=("lib",), data_dir=tmp_path / "empty")), client=LAN)
    assert "No photos yet" in c.get("/").text


def test_folder_pages(client, con):
    home = client.get("/")
    assert home.status_code == 200
    for name in ("2012", "Owen", "2016"):
        assert f'<span class="name">{name}</span>' in home.text
    # Portraits only holds an ignored Thumbs folder, so there is nothing in it to browse
    assert '<span class="name">Portraits</span>' not in home.text
    zoo = client.get(f"/folder/{folder_id(con, ZOO)}")
    assert zoo.text.count('class="tile"') == 3
    assert "All folders</a>" in zoo.text and ">lib</a>" in zoo.text and ">2012</a>" in zoo.text  # breadcrumbs
    thumbs = client.get(f"/folder/{folder_id(con, rel('Portraits', 'Thumbs'))}")
    assert "ignored by the scan settings" in thumbs.text
    assert client.get("/folder/99999").status_code == 404


def test_timeline(client):
    t = client.get("/timeline").text
    assert "/timeline/2012" in t and "/timeline/2016" in t
    y = client.get("/timeline/2012").text
    assert 'id="m6"' in y and y.count('class="tile"') == 3


def test_image_page_steps_through_context(client, con):
    zoo = folder_id(con, ZOO)
    first, middle, last = (image_id(con, rel(ZOO, f"DSC_000{i}.jpg")) for i in (1, 2, 3))
    page = client.get(f"/image/{middle}?ctx=folder:{zoo}").text
    assert f'href="/image/{first}?ctx=folder%3A{zoo}"' in page and 'rel="prev"' in page
    assert f'href="/image/{last}?ctx=folder%3A{zoo}"' in page and 'rel="next"' in page
    assert "2 of 3" in page
    assert 'rel="prev"' not in client.get(f"/image/{first}?ctx=folder:{zoo}").text
    # the first photo is also in Owen\Zoo: both paths are listed
    files = client.get(f"/image/{first}").text
    assert rel("Owen", "Zoo", "DSC_0001.jpg") in files and rel(ZOO, "DSC_0001.jpg") in files
    assert client.get("/image/99999").status_code == 404


def test_derivatives(client, con):
    sha = con.execute("SELECT sha256 FROM image WHERE error IS NULL LIMIT 1").fetchone()[0]
    t = client.get(f"/t/{sha}")
    assert t.status_code == 200 and t.headers["content-type"] == "image/webp"
    assert "immutable" in t.headers["cache-control"]
    assert client.get(f"/p/{sha}").headers["content-type"] == "image/jpeg"
    assert client.get("/t/" + "z" * 64).status_code == 404
    assert client.get("/p/..%5C..%5Clineagebox.db").status_code == 404


def test_original_download(client, con, library):
    fid = con.execute("SELECT id FROM file WHERE path = ?", (rel(ZOO, "DSC_0002.jpg"),)).fetchone()[0]
    r = client.get(f"/original/{fid}")
    assert r.status_code == 200
    assert r.content == (library.parent / ZOO / "DSC_0002.jpg").read_bytes()
    assert "DSC_0002.jpg" in r.headers["content-disposition"]
    assert client.get("/original/99999").status_code == 404


def test_copy_network_path(client, cfg, con):
    on_nas = TestClient(webapp.create_app(dataclasses.replace(cfg, share_path="\\\\nas\\photos")), client=LAN)
    image = con.execute("SELECT image_id FROM file WHERE path = ?", (rel(ZOO, "DSC_0002.jpg"),)).fetchone()[0]
    assert 'data-copy="\\\\nas\\photos\\lib\\2012\\Zoo\\DSC_0002.jpg"' in on_nas.get(f"/image/{image}").text
    assert webapp.network_path("/mnt/photos/", "2012/Zoo/x.jpg") == "/mnt/photos/2012/Zoo/x.jpg"
    assert webapp.network_path("P:\\Photos", "2012/Zoo/x.jpg") == "P:\\Photos\\2012\\Zoo\\x.jpg"


@pytest.mark.parametrize("host, allowed", [("10.20.30.40", True), ("192.168.1.9", True), ("172.20.0.3", True),
                                           ("127.0.0.1", True), ("::ffff:192.168.7.7", True), ("fd12::5", True),
                                           ("8.8.8.8", False), ("2001:4860::8888", False), ("testclient", False)])
def test_only_the_home_network_is_let_in(cfg, host, allowed):
    r = TestClient(webapp.create_app(cfg), client=(host, 1234)).get("/status")
    assert r.status_code == (200 if allowed else 403)


@pytest.mark.parametrize("host, allowed", [("192.168.7.1:8000", True), ("[fd00::1]:8000", True), ("nas", True),
                                           ("photos.lan:8000", True), ("nas.local", True), ("photos.home.arpa", True),
                                           ("photos.example.com", False), ("192.168.7.1.nip.io", False), ("", False)])
def test_only_home_network_names_are_answered(cfg, host, allowed):
    """A public name pointed at this server (DNS rebinding) must not get a page that can read the catalog."""
    r = TestClient(webapp.create_app(cfg), client=LAN).get("/status", headers={"host": host})
    assert r.status_code == (200 if allowed else 400)


def test_allowed_hosts(cfg):
    named = TestClient(webapp.create_app(dataclasses.replace(
        cfg, allowed_hosts=("photos.example.com", "*.home.example.org"))), client=LAN)
    assert named.get("/status", headers={"host": "photos.example.com"}).status_code == 200
    assert named.get("/status", headers={"host": "pc.home.example.org:443"}).status_code == 200
    assert named.get("/status", headers={"host": "example.org"}).status_code == 400


def test_behind_a_reverse_proxy(cfg):
    behind = dataclasses.replace(cfg, trusted_proxies=("172.18.0.0/16",), allowed_hosts=("photos.example.com",))
    proxy = TestClient(webapp.create_app(behind), client=("172.18.0.2", 1), base_url="http://photos.example.com")
    # the proxy's address is private, but it is the client behind it that is checked
    assert proxy.get("/status", headers={"x-forwarded-for": "8.8.8.8"}).status_code == 403
    https = {"x-forwarded-for": "192.168.1.20", "x-forwarded-proto": "https"}
    assert proxy.get("/status", headers=https).status_code == 200
    assert "secure" in proxy.get("/cleanup/faces?per=50", headers=https).headers["set-cookie"].lower()
    # nobody else's X-Forwarded-For is believed
    direct = TestClient(webapp.create_app(behind), client=("8.8.8.8", 1))
    assert direct.get("/status", headers={"x-forwarded-for": "192.168.1.20"}).status_code == 403


def test_health_and_plain_http_cookies(client):
    assert client.get("/healthz").text == "ok"
    assert "secure" not in client.get("/cleanup/faces?per=50").headers["set-cookie"].lower()


def test_network_paths_per_source(client, cfg, con):
    sources = dataclasses.replace(cfg, share_path="/srv/photos", network_paths=(("lib/2012", "\\\\nas\\y2012"),))
    assert webapp.locate(sources, "lib/2012/Zoo/x.jpg") == "\\\\nas\\y2012\\Zoo\\x.jpg"
    assert webapp.locate(sources, "lib/2012") == "\\\\nas\\y2012"
    assert webapp.locate(sources, "lib/2016/a.jpg") == "/srv/photos/lib/2016/a.jpg"
    assert webapp.locate(sources, "") == "/srv/photos"
    page = TestClient(webapp.create_app(sources), client=LAN).get(f"/image/{image_id(con, rel(ZOO, 'DSC_0002.jpg'))}")
    assert 'data-copy="\\\\nas\\y2012\\Zoo\\DSC_0002.jpg"' in page.text


def test_allow_list_is_configurable(cfg):
    lan_only = dataclasses.replace(cfg, allowed_networks=("192.168.7.0/24",))
    assert TestClient(webapp.create_app(lan_only), client=("192.168.7.9", 1)).get("/status").status_code == 200
    assert TestClient(webapp.create_app(lan_only), client=("192.168.1.9", 1)).get("/status").status_code == 403


def test_status_page(client):
    s = client.get("/status").text
    assert "Unique images" in s and "Camera (EXIF)" in s


def test_tags_pages(client, con, cfg):
    from lineagebox.describe import _record
    from lineagebox.folders import FolderIndex
    from tests.test_recognition import FakeOllama
    answer = FakeOllama(cfg).chat_json("", "Catalog this photo.", {}, b"x")[0]
    a, b = image_id(con, rel(ZOO, "DSC_0002.jpg")), image_id(con, rel(ZOO, "DSC_0003.jpg"))
    _record(con, a, "fake", answer, FolderIndex(con))
    _record(con, b, "fake", {**answer, "tags": ["goat", "Close-up"], "animals": []}, FolderIndex(con))
    con.commit()

    page = client.get("/tags").text
    assert '<span class="name">goat</span><span class="count">2</span>' in page
    assert "petting zoo" in page and "t shirt" in page  # stored lowercase, without hyphens
    narrowed = client.get("/tags?q=ZOO", headers={"X-Requested-With": "lineagebox"}).text
    assert "<html" not in narrowed and "petting zoo" in narrowed and ">goat<" not in narrowed  # just the list
    assert "No tags contain" in client.get("/tags?q=walrus").text

    goat = client.get("/tag/goat").text
    assert goat.count('class="tile"') == 2 and ">close up</a>" in goat  # related tags
    assert client.get("/tag/Close-up").status_code == 200  # as the photo page links it
    assert client.get("/tag/walrus").status_code == 404
    photo = client.get(f"/image/{a}?ctx=tag:goat").text
    assert 'href="/tag/goat" data-back' in photo and "1 of 2" in photo


def test_watched_folders_are_shown_where_lineagebox_reads_them(client, cfg, con):
    """In Docker, the mounted folder (/photos/local/Photos); the network path only as a hint, when there is one."""
    plain = client.get("/settings").text
    assert f"<b>{(cfg.share_root / 'lib').as_posix()}</b>" in plain and "on the network" not in plain
    shared = TestClient(webapp.create_app(dataclasses.replace(cfg, network_paths=(("lib", r"\\nas\photos"),))),
                        client=LAN)
    page = shared.get("/settings").text
    assert f"<b>{(cfg.share_root / 'lib').as_posix()}</b>" in page and r"on the network: \\nas\photos" in page
    assert f"<code>{(cfg.share_root / 'lib').as_posix()}</code>" in shared.get("/status").text
    assert webapp.on_network(cfg, "lib") == ""


def test_network_paths_are_set_on_the_settings_page(client, cfg, con):
    page = client.get("/settings").text
    assert 'name="net:"' in page and 'name="net:lib"' in page  # the share itself, and the watched folder's top
    r = client.post("/api/settings/network-paths", json={"net:": "", "net:lib": r"\\nas\photos"}, headers=APP)
    assert r.status_code == 200
    assert r"on the network: \\nas\photos" in client.get("/settings").text
    photo = image_id(con, rel(ZOO, "DSC_0002.jpg"))
    assert r'data-copy="\\nas\photos\2012\Zoo\DSC_0002.jpg"' in client.get(f"/image/{photo}").text
    bad = client.post("/api/settings/network-paths", json={"net:nowhere": r"\\nas\x"}, headers=APP)
    assert bad.status_code == 400 and "no folder nowhere" in bad.json()["detail"]
    assert client.post("/api/settings/network-paths", json={"net:lib": ""}, headers=APP).status_code == 200
    assert "on the network" not in client.get("/settings").text  # cleared: back to the folder itself


def test_errors_are_said_as_pages_and_as_messages(client):
    """Someone following a link gets a page that says what is wrong; the site's own scripts get the message."""
    page = client.get("/image/99999")
    assert page.status_code == 404 and page.headers["content-type"].startswith("text/html")
    assert "No such image" in page.text and 'href="/"' in page.text
    assert client.get("/no/such/page").status_code == 404 and "Not here" in client.get("/no/such/page").text
    bad = client.get("/image/abc")
    assert bad.status_code == 400 and "Image id should be a number." in bad.text
    api = client.get("/image/99999", headers={"X-Requested-With": "lineagebox"})
    assert api.json() == {"detail": "No such image"}
    r = client.post("/api/settings", json={"ingest_threads": 99}, headers=APP)  # a module's Problem, no wrapper
    assert r.status_code == 400 and "Photos read at a time" in r.json()["detail"]
    gone = client.post("/api/people/99999/sex", json={"sex": "male"}, headers=APP)  # merged away since the page
    assert gone.status_code == 404 and gone.json() == {"detail": "No such person"}


@pytest.mark.parametrize("url, body", [
    ("/api/tours/preview", {"type": "abc"}), ("/api/tours", {"spec": {"who": "person"}}),
    ("/api/tours", {"type": 1, "person": "abc"}), ("/api/tours", {"spec": {"media": ["x"]}}),
    ("/api/tour-types", {"flags": 5}), ("/api/settings", {"folders": 5}), ("/api/settings", {"ignore_dirs": 5}),
    ("/api/settings", {"network_paths": "x"}), ("/api/settings/address", {"allowed_hosts": 5}),
])
def test_a_body_of_the_wrong_kind_is_told_so(client, url, body):
    """The pages never send these, but a script might: a sentence, not an Internal Server Error."""
    r = client.post(url, json=body, headers=APP)
    assert 400 <= r.status_code < 500 and isinstance(r.json()["detail"], str), (r.status_code, r.text[:200])


def _json_put_in(text: str):
    """Where a template puts JSON made by tojson: each {{ }} (start, end), with tojson in it or a value set to it."""
    for m in re.finditer(r"tojson", text):
        start = text.rfind("{{", 0, m.start())
        if start < text.rfind("{%", 0, m.start()):  # {% set x = ...|tojson %}: where x is put, below
            continue
        yield start, text.index("}}", m.end()) + 2
    for name in re.findall(r"\{%-?\s*set\s+(\w+)\s*=[^%]*\|\s*tojson\s*-?%\}", text):
        for m in re.finditer(r"\{\{-?\s*" + name + r"\s*-?\}\}", text):
            yield m.start(), m.end()


def test_json_in_pages_cannot_end_its_attribute():
    """Jinja's tojson escapes < > & and ' but not ": its JSON is safe in an attribute in single quotes
    (data-body='...') or in a JSON script block, and would end one in double quotes early, the rest of it then read
    as HTML. Words anyone typed (names, tags, dates) go into these."""
    found = 0
    for path in (Path(webapp.__file__).parent / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for start, end in _json_put_in(text):
            found += 1
            in_attribute = text[start - 2:start] == "='" and text[end:end + 1] == "'"
            script = text.rfind("<script", 0, start)
            in_json_block = script > text.rfind("</script>", 0, start) and \
                'type="application/json"' in text[script:text.index(">", script)]
            assert in_attribute or in_json_block, f"{path.name}, line {text.count(chr(10), 0, start) + 1}"
    assert found > 40  # the templates' uses were found
