"""Cleanup → Camera clocks (admins): a camera whose clock was wrong for a while, put right from its own photos
(camera_clock.py).

- GET /cleanup/camera-clock?camera=&year=&month=&page=&photo=&from=&to=: every camera, those with people in their
  photos before they were born first; for the one picked, a month of its photos in the order it took them, the
  stretches put right, the photo clicked (photo) with what can be done with it, and the stretch being picked (from a
  photo to another, or to "end").
- POST /api/camera-clock {camera, first_id, last_id, image_id, day, time}: a new stretch, with when one of its photos
  was really taken.
- POST /api/camera-clock/{id}/pin {image_id, day, time}: when another of its photos was really taken (or the same
  one, again); /unpin {image_id} forgets it; /bounds {first_id | last_id | to_end} starts or ends it at another
  photo; /remove puts its photos back to the camera's dates.

Every change is kept for Recent changes, with the camera's photos (and their copies, which take their dates).
"""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from lineagebox import camera_clock, changes, face_ages
from lineagebox.web.common import ADMIN, _by, _from_app
from lineagebox.web.deps import con, page, site, viewer
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/cleanup/camera-clock": ADMIN,
    "/api/camera-clock": ADMIN,
    "/api/camera-clock/{stretch_id}/pin": ADMIN,
    "/api/camera-clock/{stretch_id}/unpin": ADMIN,
    "/api/camera-clock/{stretch_id}/bounds": ADMIN,
    "/api/camera-clock/{stretch_id}/remove": ADMIN,
}
CHECKS_FOR = 60  # seconds the faces' age checks are kept for the page (any change clears them)


class NewStretch(BaseModel):
    camera: str = Field(min_length=1, max_length=200)
    first_id: int
    last_id: int | None = None  # none: on to every later photo
    image_id: int
    day: str = Field(max_length=20)
    time: str = Field("", max_length=20)  # none: the time of day the camera wrote


class PinBody(BaseModel):
    image_id: int
    day: str = Field(max_length=20)
    time: str = Field("", max_length=20)


class PhotoBody(BaseModel):
    image_id: int


class BoundsBody(BaseModel):
    first_id: int | None = None
    last_id: int | None = None
    to_end: bool = False


def _photos(n: int) -> str:
    return f"{n:,} photo{'' if n == 1 else 's'}"


@router.get("/cleanup/camera-clock", response_class=HTMLResponse)
def camera_clock_page(request: Request, camera: str = "", year: str = "", month: str = "", page_no: int = Query(
                      0, alias="page", ge=0), photo: int | None = None, from_: int | None = Query(None, alias="from"),
                      to: str = Query("", max_length=20), v=Depends(viewer), c=Depends(con),
                      s: Site = Depends(site)):
    """Every camera, those whose photos show people before they were born first; for the one picked, what
    camera_clock.page says to show."""
    checks = s.keep(v, ("age checks",), CHECKS_FOR, lambda: face_ages.age_checks(c))
    known = camera_clock.cameras(c, face_ages.before_birth_by_camera(c, checks))
    picked = next((k for k in known if k["camera"] == camera), None)
    shown = camera_clock.page(c, camera, checks, year[:4], month[:2], page_no, photo, from_, to) if picked else None
    state = {"camera": camera, "year": shown and shown["year"], "month": shown and shown["month"],
             "page": shown and shown["page"], "photo": photo, "start": from_, "end": to}

    def here(**changed) -> str:
        """This page's address with some of what it shows changed (None leaves it out): start and end are the
        stretch being picked (from, to)."""
        q = {k: x for k, x in {**state, **changed}.items() if x not in (None, "", 0)}
        return "/cleanup/camera-clock?" + urlencode({{"start": "from", "end": "to"}.get(k, k): x for k, x in q.items()})

    return page(request, "cleanup_camera_clock.html", nav="cleanup", known=known, picked=picked, shown=shown,
                here=here, amount_of=camera_clock.amount, per_page=camera_clock.PER_PAGE)


def _specs(camera: str):
    """What a change to a camera's stretches can touch: its photos, their copies, and its stretches."""
    return lambda _: [("image", "camera = ?", (camera,)),
                      ("image", """dup_group_id IN (SELECT dup_group_id FROM main.image WHERE camera = ?
                                   AND dup_group_id IS NOT NULL)""", (camera,)),
                      ("camera_clock", "camera = ?", (camera,))]


def _stretch_or_404(c, stretch_id: int) -> dict:
    s = camera_clock.stretch(c, stretch_id)
    if s is None:
        raise HTTPException(404, "No such stretch of photos")
    return s


def _held(c, s: dict) -> int:
    return camera_clock.count(camera_clock.photos(c, s["camera"]), s["first"], s["last"])


@router.post("/api/camera-clock", dependencies=[Depends(_from_app)])
def new_stretch(body: NewStretch, v=Depends(viewer), c=Depends(con)):
    """The camera's photos from one to another dated as one of them was really taken. Undoable."""
    if not c.execute("SELECT 1 FROM image WHERE camera = ?", (body.camera,)).fetchone():
        raise HTTPException(404, "No photos from that camera")
    with changes.tracking(c, v, "dates", f"Put right {body.camera}'s clock", _specs(body.camera)) as kept:
        new_id = camera_clock.create(c, body.camera, body.first_id, body.last_id, body.image_id, body.day, body.time,
                                     _by(v))
    return {"message": f"Moved the dates of {_photos(_held(c, camera_clock.stretch(c, new_id)))}.", "undo": kept.id}


@router.post("/api/camera-clock/{stretch_id}/pin", dependencies=[Depends(_from_app)])
def pin(stretch_id: int, body: PinBody, v=Depends(viewer), c=Depends(con)):
    """When another of the stretch's photos was really taken: the photos between the two follow the clock's drift."""
    s = _stretch_or_404(c, stretch_id)
    with changes.tracking(c, v, "dates", f"Put right {s['camera']}'s clock", _specs(s["camera"])) as kept:
        camera_clock.pin(c, s, body.image_id, body.day, body.time)
    return {"message": f"Dated. The {_photos(_held(c, s))} with it moved too.", "undo": kept.id, "reload": True}


@router.post("/api/camera-clock/{stretch_id}/unpin", dependencies=[Depends(_from_app)])
def unpin(stretch_id: int, body: PhotoBody, v=Depends(viewer), c=Depends(con)):
    s = _stretch_or_404(c, stretch_id)
    with changes.tracking(c, v, "dates", f"Put right {s['camera']}'s clock", _specs(s["camera"])) as kept:
        camera_clock.unpin(c, s, body.image_id)
    return {"message": "Forgotten: the other photos you dated say how far these move.", "undo": kept.id,
            "reload": True}


@router.post("/api/camera-clock/{stretch_id}/bounds", dependencies=[Depends(_from_app)])
def bounds(stretch_id: int, body: BoundsBody, v=Depends(viewer), c=Depends(con)):
    """The stretch made to start or end at another photo, or to run on to every later one."""
    s = _stretch_or_404(c, stretch_id)
    with changes.tracking(c, v, "dates", f"Put right {s['camera']}'s clock", _specs(s["camera"])) as kept:
        dropped = camera_clock.bounds(c, s, body.first_id, body.last_id, body.to_end)
    held = _photos(_held(c, camera_clock.stretch(c, stretch_id)))
    left = (f" {_photos(dropped)} you dated {'is' if dropped == 1 else 'are'} no longer in it, so "
            f"{'its date is' if dropped == 1 else 'their dates are'} forgotten." if dropped else "")
    return {"message": f"Now {held}.{left}", "undo": kept.id, "reload": True}


@router.post("/api/camera-clock/{stretch_id}/remove", dependencies=[Depends(_from_app)])
def remove(stretch_id: int, v=Depends(viewer), c=Depends(con)):
    s = _stretch_or_404(c, stretch_id)
    held = _held(c, s)
    with changes.tracking(c, v, "dates", f"Put back {s['camera']}'s dates", _specs(s["camera"])) as kept:
        camera_clock.remove(c, s)
    return {"message": f"Put back: {_photos(held)} {'has' if held == 1 else 'have'} the camera's dates again.",
            "undo": kept.id, "reload": True}
