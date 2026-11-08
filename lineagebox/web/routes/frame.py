"""Photo frame and TV mode: a full-screen page cycling memories, a tour or an album, for an old tablet, a smart TV or a
Home Assistant dashboard; and the device links an admin makes for one (Settings → Photo frames, devices.py).

Opened by a device link (/d/<token>, web/frames.py) it shows that link's one view and nothing else can be opened with
it. Opened by someone signed in (or at home without family accounts) it shows their own memories, or a tour of
theirs: /photo-frame?tour=<id>.
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from lineagebox import accounts, activity, albums, devices, tours
from lineagebox.web import frames
from lineagebox.web.common import ADMIN, MEMBER, _from_app
from lineagebox.web.deps import con, page, site, viewer, writer
from lineagebox.web.routes.tours import shown
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/photo-frame": MEMBER,  # and a device link's (frames.ROUTES)
    "/api/photo-frame": MEMBER,
    "/settings/devices": ADMIN,
    "/api/devices": ADMIN,
    "/api/devices/{device_id}/revoke": ADMIN,
    "/api/devices/{device_id}/delete": ADMIN,
}


def _base(request: Request) -> str:
    """Where the frame's own requests go: under its device link, or the site itself."""
    token = request.scope.get(frames.TOKEN)
    return f"{frames.PREFIX}{token}" if token else ""


@router.get("/photo-frame", response_class=HTMLResponse)
def frame_page(request: Request, tour: int | None = None, seconds: int | None = None, v=Depends(viewer),
               c=Depends(con)):
    """The frame itself: the page asks for its list of photos (/api/photo-frame) and cycles through them."""
    link = v.device
    if link is not None:
        title, every, query = link["name"], link["seconds"], ""
    else:
        t = tours.tour(c, tour) if tour is not None else None
        if tour is not None and t is None:
            raise HTTPException(404, "No such tour")
        title, every = (t["name"] if t else "Memories"), frames.seconds_from(seconds)
        query = f"?tour={tour}" if t else ""
    return page(request, "photo_frame.html", title=title, seconds=every, base=_base(request),
                photos_url=f"{_base(request)}/api/photo-frame{query}", refresh=frames.REFRESH)


@router.get("/api/photo-frame")
def frame_photos(tour: int | None = None, v=Depends(viewer), c=Depends(con), w=Depends(writer),
                 s: Site = Depends(site)):
    """The frame's list: each photo's picture, date and caption. A device link's is its own view's; anyone else's
    their memories, or a tour they see (as its page shows it), kept 10 minutes."""
    if v.device is not None:
        return {"title": v.device["name"], "seconds": v.device["seconds"], "refresh": frames.REFRESH,
                "photos": frames.shown(s, v.device)["slides"]}
    t = tours.tour(c, tour) if tour is not None else None
    if tour is not None and t is None:
        raise HTTPException(404, "No such tour")
    today = date.today()

    def work():
        if t is not None:
            chosen = shown(s, v, c, w, t)[0]
        else:
            chosen = frames.memory_photos(c, today, s.live(c).blurry_below)
        return frames.slideshow(c, chosen[:frames.MOST])
    photos = s.keep(v, ("photo frame", tour, today.isoformat()), frames.PHOTOS_KEPT, work)
    return {"title": t["name"] if t else "Memories", "refresh": frames.REFRESH, "photos": photos}


# ---- Settings → Photo frames ---------------------------------------------------------------------------------------

class DeviceBody(BaseModel):
    name: str = Field(default="", max_length=200)
    view: str = Field(default="memories", max_length=20)
    account_id: str = Field(default="", max_length=20)  # "" or "all": the whole library
    tour_id: str = Field(default="", max_length=20)
    album_id: str = Field(default="", max_length=20)
    home_only: bool = True
    seconds: str = Field(default="20", max_length=4)


def _address(request: Request, s: Site, home_only: bool) -> str:
    """The start of a link's address: the one the admin uses now, or, for a frame away from home, the address people
    use from outside (public_url) when there is one."""
    outside = s.shared_cfg().public_url
    if outside and not home_only:
        return outside.rstrip("/")
    return str(request.base_url).rstrip("/")


def _panel(request: Request, s: Site, c, made: dict | None = None) -> str:
    """The list of links and the form for a new one, as HTML for the panel (and for answers that change it)."""
    everyone = accounts.everyone(c)
    return s.templates.get_template("_devices.html").render(
        links=devices.every(c), accounts=everyone, tours=tours.tours(c), albums=albums.listing(c), views=devices.VIEWS,
        seconds=devices.SECONDS, made=made, accounts_on=s.shared_cfg().accounts_enabled,
        frame_here="/photo-frame", request=request)


@router.get("/settings/devices", response_class=HTMLResponse)
def devices_panel(request: Request, c=Depends(con), s: Site = Depends(site)):
    """Settings → Photo frames, loaded after the page."""
    return HTMLResponse(_panel(request, s, c))


@router.post("/api/devices", dependencies=[Depends(_from_app)])
def make_device(request: Request, body: DeviceBody, v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    """A new device link, shown this once (only its hash is kept), with a QR code for a tablet to open it by."""
    account_id = int(body.account_id) if body.account_id.isdecimal() else None
    tour_id = int(body.tour_id) if body.tour_id.isdecimal() else None
    album_id = int(body.album_id) if body.album_id.isdecimal() else None
    link_id, token = devices.make(c, body.name, body.view, account_id=account_id, tour_id=tour_id, album_id=album_id,
                                  home_only=body.home_only, seconds=frames.seconds_from(body.seconds),
                                  made_by=v.account_id)
    url = f"{_address(request, s, body.home_only)}{frames.PREFIX}{token}"
    activity.log(c, f"A photo frame link was made: {devices.get(c, link_id)['name']}")
    made = {"id": link_id, "url": url, "qr": accounts.setup_qr(url), "name": devices.get(c, link_id)["name"]}
    return {"html": _panel(request, s, c, made)}


@router.post("/api/devices/{device_id}/revoke", dependencies=[Depends(_from_app)])
def revoke_device(request: Request, device_id: int, c=Depends(con), s: Site = Depends(site)):
    """Turn a link off: the frame shows that it was turned off from its next picture on."""
    row = devices.get(c, device_id)
    if row is None:
        raise HTTPException(404, "No such link")
    if devices.revoke(c, device_id):
        activity.log(c, f"A photo frame link was turned off: {row['name']}")
    return {"html": _panel(request, s, c), "message": f"{row['name']}: turned off."}


@router.post("/api/devices/{device_id}/delete", dependencies=[Depends(_from_app)])
def delete_device(request: Request, device_id: int, c=Depends(con), s: Site = Depends(site)):
    """Take a turned-off link off the list (one still on is turned off first)."""
    row = devices.get(c, device_id)
    if row is None:
        raise HTTPException(404, "No such link")
    devices.revoke(c, device_id)
    devices.remove(c, device_id)
    return {"html": _panel(request, s, c)}
