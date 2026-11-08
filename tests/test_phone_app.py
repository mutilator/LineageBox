"""An app on the phone: the web manifest, the icons, the service worker (which keeps only the site's own files, never a
page or a photo) and the share sheet's way to Upload."""
import re

from fastapi.testclient import TestClient
from PIL import Image

from lineagebox.web import app as webapp
from lineagebox.web.routes import phone
from tests.conftest import LAN
from tests.test_access_web import OUTSIDE, signed_in, site  # noqa: F401 - site is a fixture

STATIC = webapp.HERE / "static"


def test_the_manifest_and_icons(cfg):
    c = TestClient(webapp.create_app(cfg), client=LAN)
    r = c.get("/manifest.webmanifest")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/manifest+json")
    m = r.json()
    assert m["start_url"] == "/home" and m["scope"] == "/" and m["display"] == "standalone"
    sizes = {}
    for icon in m["icons"]:
        assert c.get(icon["src"]).status_code == 200, icon["src"]
        if icon["type"] == "image/png":
            with Image.open(STATIC / icon["src"].removeprefix("/static/")) as im:
                sizes[icon.get("purpose", "any")] = sizes.get(icon.get("purpose", "any"), []) + [im.size]
                assert f"{im.size[0]}x{im.size[1]}" == icon["sizes"]
    assert (192, 192) in sizes["any"] and (512, 512) in sizes["any"] and sizes["maskable"] == [(512, 512)]
    with Image.open(STATIC / "icons" / "apple-touch-icon.png") as im:
        assert im.size == (180, 180) and im.mode == "RGB"  # iOS rounds it itself; no see-through corners
    share = m["share_target"]
    assert share["action"] == "/upload/shared" and share["method"] == "POST"
    assert share["enctype"] == "multipart/form-data"
    assert share["params"]["files"][0]["accept"] == ["image/*", "video/*"]
    page = c.get("/people").text
    for tag in ('<link rel="manifest" href="/manifest.webmanifest">', 'rel="apple-touch-icon"',
                '<meta name="theme-color"'):
        assert tag in page, tag


def test_the_service_worker_keeps_only_the_sites_own_files(cfg):
    c = TestClient(webapp.create_app(cfg), client=LAN)
    r = c.get("/sw.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert r.headers["cache-control"] == "no-cache"  # a new version reaches phones at once
    version = c.app.state.site.templates.env.globals["static_version"]
    assert "__VERSION__" not in r.text and f'const VERSION = "{version}";' in r.text
    kept = re.search(r"const KEEP = \[(.*?)\];", r.text, re.S)[1]
    for url in ("/static/app.css?v=${VERSION}", "/static/app.js?v=${VERSION}", "OFFLINE", "/static/icons/icon.svg",
                "/static/icons/icon-192.png"):
        assert url in kept, url  # what a phone keeps from the first visit: each is there to keep
        assert c.get(url.replace("${VERSION}", version).replace("OFFLINE", "/static/offline.html")).status_code == 200
    # what it keeps: /static/ only; pages from the server, every time; photos and the rest untouched
    script = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert script.count("cache.put(") == 2 and 'url.pathname.startsWith("/static/")' in script
    assert re.search(r'ownFile[\s\S]*?searchParams\.get\("v"\) === VERSION', script)
    assert "/p/" not in script and "/t/" not in script and "/api/" not in script.split("// anything else")[0]


def test_the_app_is_offered_only_where_phones_take_it():
    """Over plain http at home nothing changes: the service worker is registered only in a secure context."""
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    assert re.search(r'"serviceWorker" in navigator && window\.isSecureContext\)\s*\{\s*addEventListener\("load", '
                     r'\(\) => navigator\.serviceWorker\.register\("/sw\.js"\)', script)


def test_installing_needs_nothing_private_before_signing_in(site, cfg, con):
    """A phone asks for the manifest and the service worker on its own, without its cookie."""
    c = TestClient(webapp.create_app(cfg), client=OUTSIDE, follow_redirects=False)
    assert c.get("/manifest.webmanifest").status_code == 200 and c.get("/sw.js").status_code == 200
    assert c.get("/static/icons/icon-192.png").status_code == 200
    assert c.get("/static/offline.html").status_code == 200
    assert c.get("/upload").status_code == 303  # the rest still needs signing in
    assert c.post("/upload/shared", data={"title": "x"}).status_code == 401


def test_photos_shared_without_the_service_worker(site, cfg, con):
    """Should the phone's share reach the server (the service worker wasn't running yet), nothing is read or kept:
    Upload says to pick them there."""
    cora = signed_in(cfg, con, site["who"]["Cora"])
    r = cora.post("/upload/shared", files={"files": ("a.jpg", b"\xff\xd8not much", "image/jpeg")})
    assert r.status_code == 303 and r.headers["location"] == "/upload?shared=lost"
    page = cora.get("/upload?shared=lost")
    assert page.status_code == 200 and "data-upload" in page.text


def test_the_site_still_refuses_to_be_framed(site, cfg, con):
    cora = signed_in(cfg, con, site["who"]["Cora"])
    for url in ("/home", "/upload", "/news", "/photo-frame"):
        r = cora.get(url)
        assert r.headers["x-frame-options"] == "DENY", url
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"], url
    assert phone.manifest()["theme_color"] == "#2f62c9"  # app.css --accent
