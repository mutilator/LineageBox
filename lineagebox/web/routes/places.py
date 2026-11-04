"""The map, and putting places right."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from lineagebox import places
from lineagebox.web.common import ADMIN, MEMBER, PlaceBody, PlaceConfirmBody, _from_app
from lineagebox.web.deps import con, page, site, viewer
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/map": MEMBER,
    "/api/map": MEMBER,
    "/api/places/fix": ADMIN,
    "/api/places/confirm": ADMIN,
}


@router.get("/map", response_class=HTMLResponse)
def map_page(request: Request, c=Depends(con), s: Site = Depends(site)):
    return page(request, "map.html", nav="map", cfg=s.live(c))


@router.get("/api/map")
def map_api(year: int | None = None, v=Depends(viewer), c=Depends(con)):
    data = places.map_data(c, year)
    if v.limited:  # place names still to look up are the admins' business
        data.update(to_find=0, not_found=0)
    return data


@router.post("/api/places/fix", dependencies=[Depends(_from_app)])
def fix_place(body: PlaceBody, c=Depends(con), s: Site = Depends(site)):
    try:
        on_map = places.fix(c, s.cfg, body.name, body.query)
    except places.Problem as e:
        raise HTTPException(400, str(e)) from None
    c.commit()
    return {"on_map": on_map}


@router.post("/api/places/confirm", dependencies=[Depends(_from_app)])
def confirm_place(body: PlaceConfirmBody, c=Depends(con)):
    """A folder's place is where the map shows it (or that is taken back)."""
    try:
        places.confirm(c, body.name, body.right)
    except places.Problem as e:
        raise HTTPException(400, str(e)) from None
    c.commit()
    return {}
