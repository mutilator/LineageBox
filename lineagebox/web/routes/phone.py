"""An app on the phone: the web manifest and the service worker that let a phone put LineageBox on its home screen,
start it fast, and send photos to it from the share sheet (Android).

Both are asked for by the phone on its own, signed in or not, so they hold nothing private (common._public_path).
Phones install a site only over HTTPS (or on the machine itself); over plain http at home the manifest is read and
nothing else changes, and app.js doesn't register the service worker at all.

The service worker (static/sw.js) keeps the site's own script, stylesheet, icons and an offline page, never a page or
a photo: those are always asked for, so signing out or switching an account off works as before. Photos shared to
LineageBox are posted to /upload/shared; the service worker keeps them on the phone and opens Upload, whose script
sends them as any picked file (routes/uploads.py). Should a share reach the server instead (the service worker not
running yet), the page says to pick them again.
"""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, RedirectResponse, Response

from lineagebox.web.common import HERE, MEMBER, PUBLIC
from lineagebox.web.deps import site
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/manifest.webmanifest": PUBLIC,
    "/sw.js": PUBLIC,
    "/upload/shared": MEMBER,
}
THEME = "#2f62c9"  # app.css --accent: the bar above the app on a phone
BACKGROUND = "#f6f6f3"  # app.css --bg: behind the icon while it starts
ICONS = "/static/icons"
SHARED = "/upload/shared"


def manifest() -> dict:
    """The web manifest (W3C Web Application Manifest), with Android's Web Share Target for photos and videos."""
    return {
        "name": "LineageBox", "short_name": "LineageBox", "id": "/home",
        "description": "The family's photos, videos and family tree.",
        "start_url": "/home", "scope": "/", "display": "standalone", "orientation": "any",
        "background_color": BACKGROUND, "theme_color": THEME,
        "icons": [{"src": f"{ICONS}/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": f"{ICONS}/icon-512.png", "sizes": "512x512", "type": "image/png"},
                  {"src": f"{ICONS}/maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
                  {"src": f"{ICONS}/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
        "share_target": {"action": SHARED, "method": "POST", "enctype": "multipart/form-data",
                         "params": {"title": "title", "text": "text",
                                    "files": [{"name": "files", "accept": ["image/*", "video/*"]}]}},
    }


@router.get("/manifest.webmanifest")
def web_manifest():
    return JSONResponse(manifest(), media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@router.get("/sw.js")
def service_worker(s: Site = Depends(site)):
    """The service worker, with this version's number in it: the browser compares it byte for byte, so after an update
    the new one takes over and drops the old copies of the site's files. At the root, so that it looks after every
    page (and the share sheet's post); asked for anew every time (no-cache)."""
    script = (HERE / "static" / "sw.js").read_text(encoding="utf-8").replace(
        "__VERSION__", s.templates.env.globals["static_version"])
    return Response(script, media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@router.post(SHARED)
def shared_without_the_app():
    """Photos shared from the phone that the service worker didn't catch (it wasn't running yet): nothing is read or
    kept; Upload says to pick them there instead. A plain redirect, so no app header is needed (it changes nothing)."""
    return RedirectResponse("/upload?shared=lost", 303)
