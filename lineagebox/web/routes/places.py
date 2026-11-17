"""The map, putting places right, photos and folders put on the map by hand, and writing what the family gave a photo
into its file."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import json

from lineagebox import albums, catalog, changes, journeys, places, towns, writeback
from lineagebox.web.common import ADMIN, MEMBER, ImageIds, PlaceBody, PlaceConfirmBody, from_app, images_or_404
from lineagebox.web.deps import con, page, site, viewer, writer
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/map": MEMBER,
    "/api/map": MEMBER,
    "/api/places/fix": ADMIN,
    "/api/places/confirm": ADMIN,
    "/api/places/target": MEMBER,
    "/api/towns": MEMBER,
    "/api/images/{image_id}/place": MEMBER,
    "/api/selection/place": MEMBER,
    "/api/folders/{folder_id}/place": ADMIN,
    "/api/images/{image_id}/write-file": ADMIN,
}


class SpotBody(BaseModel):
    """Where on the map, or clear: take the place by hand away."""
    lat: float | None = None
    lon: float | None = None
    clear: bool = False


class SpotsBody(ImageIds):
    """The same, for the photos ticked on a page or the photos of an album: one place for all of them."""
    lat: float | None = None
    lon: float | None = None
    clear: bool = False


def _photos(n: int) -> str:
    return "1 photo" if n == 1 else f"{n:,} photos"


@router.get("/map", response_class=HTMLResponse)
def map_page(request: Request, c=Depends(con), s: Site = Depends(site)):
    return page(request, "map.html", nav="map", cfg=s.live(c))


JOURNEYS_FOR = 60  # seconds the map's journeys are kept for each viewer: they read every album's photos


@router.get("/api/map")
def map_api(year: int | None = None, v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    data = places.map_data(c, year)
    if v.limited:  # place names still to look up are the admins' business
        data.update(to_find=0, not_found=0)
    # the journeys of the albums the viewer sees (journeys.py), for the map's own layer: worked out for every year
    # at once and kept a minute, since the year is changed by dragging a slider
    every = s.keep(v, ("map journeys",), JOURNEYS_FOR, lambda: journeys.on_map(c))
    data["journeys"] = [j for j in every if year is None or year in j["years"]]
    return data


@router.post("/api/places/fix", dependencies=[Depends(from_app)])
def fix_place(body: PlaceBody, c=Depends(con), s: Site = Depends(site)):
    try:
        on_map = places.fix(c, s.cfg, body.name, body.query)
    except places.Problem as e:
        raise HTTPException(400, str(e)) from None
    c.commit()
    return {"on_map": on_map}


@router.post("/api/places/confirm", dependencies=[Depends(from_app)])
def confirm_place(body: PlaceConfirmBody, c=Depends(con)):
    """A folder's place is where the map shows it (or that is taken back)."""
    try:
        places.confirm(c, body.name, body.right)
    except places.Problem as e:
        raise HTTPException(400, str(e)) from None
    c.commit()
    return {}


@router.get("/api/places/target")
def place_target(kind: str = Query(pattern="^(image|folder|album)$"), id: int = Query(), v=Depends(viewer), c=Depends(con)):
    """What the map is placing by hand (a photo, an album's photos, or for an admin a folder), and where it is
    now, if anywhere. An album answers with its photos' ids: they are placed together, as ticked photos are."""
    if kind == "image":
        img = catalog.image(c, id)
        if img is None:
            raise HTTPException(404, "No such photo")
        where = places.photo_place(c, id, img)
        return {"kind": kind, "id": id, "sha": img["sha256"], "title": "this photo", "back": f"/image/{id}",
                "lat": where["lat"] if where else None, "lon": where["lon"] if where else None,
                "how": where["how"] if where else None, "label": where["label"] if where else None,
                "by_hand": bool(where and where["how"] == "photo")}
    if kind == "album":
        album = albums.get(c, id)
        if album is None:
            raise HTTPException(404, "No such album")
        ids = albums.photo_ids(c, id)
        if not ids:
            raise HTTPException(404, "No photos in that album")
        first = catalog.image(c, ids[0])
        return {"kind": kind, "id": id, "ids": ids, "sha": first["sha256"] if first else None,
                "title": f"the {_photos(len(ids))} of “{album['name']}”", "back": f"/album/{id}",
                "lat": None, "lon": None, "how": None, "label": None, "by_hand": False}
    if v.limited:
        raise HTTPException(404, "No such folder")
    folder = c.execute("SELECT id, name, path FROM folder WHERE id = ?", (id,)).fetchone()
    if folder is None:
        raise HTTPException(404, "No such folder")
    spot = c.execute("SELECT lat, lon FROM folder_place WHERE folder_id = ?", (id,)).fetchone()
    return {"kind": kind, "id": id, "sha": None, "title": f"the folder {folder['name'] or folder['path']}",
            "back": f"/folder/{id}", "lat": spot["lat"] if spot else None, "lon": spot["lon"] if spot else None,
            "how": "folder" if spot else None, "label": towns.label(spot["lat"], spot["lon"]) if spot else None,
            "by_hand": spot is not None}


@router.get("/api/towns")
def find_towns(q: str = Query("", max_length=80)):
    """Towns whose names start as typed, from the list that comes with LineageBox: nothing is asked online."""
    return {"towns": towns.search(q)}


@router.post("/api/images/{image_id}/place", dependencies=[Depends(from_app)])
def place_image(image_id: int, body: SpotBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """Put a photo on the map by hand (or move it, or with clear take its place by hand away): anyone who sees it."""
    images_or_404(c, v, [image_id])
    if catalog.image(c, image_id) is None:
        raise HTTPException(404, "No such photo")
    spec = lambda new: [("image_place", "image_id = ?", (image_id,))]  # noqa: E731
    try:
        with changes.tracking(w, v, "places", "Took a photo's place off the map" if body.clear
                              else "Put a photo on the map", spec) as kept:
            if body.clear:
                places.unplace_photo(w, image_id)
            else:
                places.place_photo(w, image_id, body.lat, body.lon, v.account_id)
    except places.Problem as e:
        raise HTTPException(400, str(e)) from None
    where = places.photo_place(c, image_id, catalog.image(c, image_id))
    return {"undo": kept.id, "label": where["label"] if where else None,
            "message": "Taken off the map." if body.clear else "Saved where it was taken."}


@router.post("/api/selection/place", dependencies=[Depends(from_app)])
def place_selection(body: SpotsBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """Put photos on the map at one place, all at once: those ticked on any page of photos, or an album's. Anyone may
    place the photos they see, and the one message undoes the lot. A place given here wins over the photos' own GPS,
    and over a place one of them was given by hand before."""
    ids = list(dict.fromkeys(body.image_ids))
    images_or_404(c, v, ids)
    if not body.clear and (body.lat is None or body.lon is None):
        raise HTTPException(400, "Click the map where they were taken first.")
    there = json.dumps(ids)
    spec = lambda new: [("image_place", "image_id IN (SELECT value FROM json_each(?))", (there,))]  # noqa: E731
    summary = (f"Took {_photos(len(ids))} off the map" if body.clear else f"Put {_photos(len(ids))} on the map")
    try:
        with changes.tracking(w, v, "places", summary, spec) as kept:
            for image_id in ids:
                if body.clear:
                    places.unplace_photo(w, image_id)
                else:
                    places.place_photo(w, image_id, body.lat, body.lon, v.account_id)
    except places.Problem as e:
        raise HTTPException(400, str(e)) from None
    label = None if body.clear else towns.label(body.lat, body.lon)
    message = (f"Took {_photos(len(ids))} off the map." if body.clear else
               f"Saved {_photos(len(ids))}" + (f" at {label}." if label else " where they were taken."))
    return {"undo": kept.id, "label": label, "message": message}


@router.post("/api/folders/{folder_id}/place", dependencies=[Depends(from_app)])
def place_folder(folder_id: int, body: SpotBody, v=Depends(viewer), c=Depends(con)):
    """Put a folder on the map by hand: its photos, and those of the folders in it, without a place of their own."""
    if c.execute("SELECT 1 FROM folder WHERE id = ?", (folder_id,)).fetchone() is None:
        raise HTTPException(404, "No such folder")
    spec = lambda new: [("folder_place", "folder_id = ?", (folder_id,))]  # noqa: E731
    try:
        with changes.tracking(c, v, "places", "Took a folder's place off the map" if body.clear
                              else "Put a folder on the map", spec) as kept:
            if body.clear:
                places.unplace_folder(c, folder_id)
            else:
                places.place_folder(c, folder_id, body.lat, body.lon, v.account_id)
    except places.Problem as e:
        raise HTTPException(400, str(e)) from None
    return {"undo": kept.id, "message": "Taken off the map." if body.clear else "Saved where its photos were taken."}


@router.post("/api/images/{image_id}/write-file", dependencies=[Depends(from_app)])
def write_file(image_id: int, c=Depends(con), s: Site = Depends(site)):
    """Write the names, caption, tags, date and place the family gave a photo into its file (every copy)."""
    if catalog.image(c, image_id) is None:
        raise HTTPException(404, "No such photo")
    writeback.write_family(c, s.cfg, image_id)  # its Problem is answered as a 409 with its words
    return {"message": "Written into the file.", "reload": True}
