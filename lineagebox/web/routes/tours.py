"""Photo tours: the list, suggestions, the wizard, a tour and its slideshow, and kinds of tours."""
import dataclasses
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from lineagebox import access, accounts, catalog, dates, sharing, tour_suggestions, tours
from lineagebox.web.common import (ADMIN, MEMBER, _by, _covers, _from_app, _own_tour, _people_or_404,
                                   _share_panel)
from lineagebox.web.deps import con, page, site, viewer, writer
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/tours": MEMBER,
    "/tours/suggested": MEMBER,
    "/tours/new": MEMBER,
    "/tour/{tour_id}": MEMBER,
    "/tour/{tour_id}/edit": MEMBER,
    "/api/tours/preview": MEMBER,
    "/api/tours": MEMBER,
    "/api/tours/{tour_id}": MEMBER,
    "/api/tours/{tour_id}/delete": MEMBER,
    "/settings/tour-types/{type_id}": ADMIN,
    "/api/tour-types": ADMIN,
    "/api/tour-types/{type_id}": ADMIN,
    "/api/tour-types/{type_id}/delete": ADMIN,
}


def maker_photos(s: Site, t: dict) -> list[dict]:
    """A tour's photos as its maker's own part of the family and uploads have them: what sharing it shares. An admin's
    tour picks from everything; photos others shared with a member are not passed on."""
    with s.own_con() as c:
        maker = accounts.get(c, t["account_id"]) if t["account_id"] is not None else None
        if maker is not None and maker["role"] != "admin":
            access.scope(c, s.viewers.get(c, dict(maker), shared=False))
        return tours.photos(c, t["spec"], s.live(c).blurry_below, seed=t["id"])


def again(s: Site, v: access.Viewer) -> access.Viewer:
    """A member's view worked out anew, now that what is shared with them changed."""
    if v.admin:
        return v
    with s.own_con() as c:
        return dataclasses.replace(s.viewers.get(c, dict(v.account)), seeing_as=v.seeing_as)


def shown(s: Site, v: access.Viewer, c, w, t: dict) -> tuple[list[dict], access.Viewer]:
    """A tour's photos for whoever is looking, and their view as it is now: picked from what they see, or, from a tour
    someone shared with them, those its maker has (maker_photos). A shared tour's photos are kept again each time it
    is shown (sharing.remember); when that changes what they may see, their view is worked out again."""
    theirs = v.admin or t["account_id"] == v.account_id
    if theirs:
        chosen = tours.photos(c, t["spec"], s.live(c).blurry_below, seed=t["id"])
        if not sharing.is_shared(w, "tour", t["id"]):
            return chosen, v
        # what they pick from is what its maker has: an admin's own tour, or a member with nothing shared with them
        same = (v.admin and t["account_id"] is None) or (v.limited and not v.granted)
        kept = chosen if same else maker_photos(s, t)
    else:
        chosen = kept = maker_photos(s, t)
    if sharing.remember(w, t["id"], [p["id"] for p in kept]):
        s.viewers.changed()
        v = again(s, v)
    return (chosen if theirs else [p for p in chosen if v.sees_image(p["id"])]), v


@router.get("/tours", response_class=HTMLResponse)
def tours_page(request: Request, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """Their own tours (an admin's: every tour), and those others shared with them."""
    saved = tours.tours(c)
    shared = {r[0] for r in w.execute("SELECT DISTINCT tour_id FROM main.share WHERE tour_id IS NOT NULL")}
    from_whom = {x["item_id"]: x["by_name"] for x in sharing.with_me(w, v.account_id) if x["kind"] == "tour"} \
        if v.limited else {}
    for t in saved:
        t["about"] = tours.describe(c, t["spec"])
        t["shared"] = t["id"] in shared
        t["from"] = from_whom.get(t["id"])
    kinds = tours.types(c)
    for t in kinds:
        t["asks"] = tours.questions(t["spec"])
    mine = [t for t in saved if v.admin or t["account_id"] == v.account_id]
    return page(request, "tours.html", nav="tours", tours=mine, others=[t for t in saved if t not in mine],
                types=kinds)


@router.get("/tours/suggested", response_class=HTMLResponse)
def suggested_tours(request: Request, v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    """The Tours page's suggestions, loaded after the page: on a big catalog they take seconds to work out, so
    they are kept for 10 minutes (less any made into tours since)."""
    mine = s.suggested.setdefault(v.account_id if v.limited else None, {"at": -1e9, "list": []})
    if time.monotonic() - mine["at"] > 600:
        found = tour_suggestions.suggest(c, s.live(c).blurry_below)  # faces in a member's photos can be anyone
        mine.update(at=time.monotonic(), list=[s for s in found
                                               if all(map(v.sees_person, s["spec"]["who"]["person_ids"]))])
    return page(request, "_suggested_tours.html", suggestions=tour_suggestions.unmade(c, mine["list"])[:6])


def wizard(request: Request, c, tour_type: dict | None, tour: dict | None):
    spec = tour["spec"] if tour else tour_type["spec"]
    named = catalog.people(c)
    return page(request, "tour_wizard.html", nav="tours", type=tour_type, tour=tour, spec=spec,
                asked=tours.questions(tour_type["spec"]) if tour_type else [],
                people=named, covers=_covers(named), relations=tours.relation_choices(c),
                years=sorted(catalog.timeline(c), reverse=True), seasons=tours.SEASONS, south=dates.south(c))


@router.get("/tours/new", response_class=HTMLResponse)
def new_tour(request: Request, type: int = Query(), c=Depends(con)):
    t = tours.tour_type(c, type)
    if t is None:
        raise HTTPException(404, "No such tour type")
    return wizard(request, c, t, None)


@router.get("/tour/{tour_id}", response_class=HTMLResponse)
def tour_page(request: Request, tour_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer),
              s: Site = Depends(site)):
    t = tours.tour(c, tour_id)
    if t is None:
        raise HTTPException(404, "No such tour")
    chosen, _ = shown(s, v, c, w, t)
    images = [{"id": p["id"], "sha256": p["sha"], "taken_at": p["taken"], "taken_at_source": None,
               "media": p["media"], "duration": p["duration"]} for p in chosen]
    theirs = v.admin or t["account_id"] == v.account_id
    from_whom = next((x["by_name"] for x in sharing.with_me(w, v.account_id)
                      if x["kind"] == "tour" and x["item_id"] == tour_id), None) if not theirs else None
    signed_in = getattr(request.state, "account", None)
    return page(request, "tour.html", nav="tours", tour=t, images=images, slides=tours.slides(c, chosen),
                about=tours.describe(c, t["spec"]), ctx=f"tour:{tour_id}", theirs=theirs, from_whom=from_whom,
                sharing=_share_panel(w, v, "tour", tour_id) if theirs and signed_in else None)


@router.get("/tour/{tour_id}/edit", response_class=HTMLResponse)
def edit_tour(request: Request, tour_id: int, c=Depends(con)):
    t = tours.tour(c, tour_id)
    if t is None:
        raise HTTPException(404, "No such tour")
    return wizard(request, c, tours.tour_type(c, t["type_id"]) if t["type_id"] else None, t)


def tour_from(c, v: access.Viewer, body: dict, tour_id: int | None = None) -> tuple[dict, int | None]:
    """The spec the wizard's answers make: from the tour's type, or the tour as it is when its type is gone. A
    suggested occasion, which no type asks for, comes as its whole spec. A member's tour is only of people they
    see."""
    old = tours.tour(c, tour_id) if tour_id else None
    if old is None and isinstance(body.get("spec"), dict):
        spec, type_id = tours.clean(body["spec"]), None
    else:
        asked = str(body.get("type") or "")
        type_id = old["type_id"] if old else int(asked) if asked.isdecimal() else None
        t = tours.tour_type(c, type_id) if type_id else None
        if t is None and old is None:
            raise HTTPException(404, "No such tour type")
        spec = tours.answer(t["spec"], body) if t else old["spec"]
    _people_or_404(c, *spec["who"]["person_ids"])
    return spec, type_id


def tour_name(c, body: dict, spec: dict) -> str:
    """The name typed, else one made from the spec on the viewer's own connection (the writer's sees everyone)."""
    return str(body.get("name") or "").strip() or tours.title(c, spec)


@router.post("/api/tours/preview", dependencies=[Depends(_from_app)])
def preview_tour(body: dict, tour: int | None = None, v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    try:
        spec, _ = tour_from(c, v, body, tour)
    except tours.Problem as e:
        return {"count": None, "message": str(e)}
    n = len(tours.photos(c, spec, s.live(c).blurry_below))
    return {"count": n, "name": tours.title(c, spec), "message": f"{n:,} photo{'' if n == 1 else 's'}"}


@router.post("/api/tours", dependencies=[Depends(_from_app)])
def create_tour(body: dict, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    spec, type_id = tour_from(c, v, body)
    return {"go": f"/tour/{tours.save_tour(w, None, type_id, spec, tour_name(c, body, spec), _by(v))}"}


@router.post("/api/tours/{tour_id}", dependencies=[Depends(_from_app)])
def update_tour(tour_id: int, body: dict, v=Depends(viewer), c=Depends(con), w=Depends(writer),
                s: Site = Depends(site)):
    _own_tour(c, v, tour_id)
    spec, type_id = tour_from(c, v, body, tour_id)
    tours.save_tour(w, tour_id, type_id, spec, tour_name(c, body, spec))
    if sharing.is_shared(w, "tour", tour_id):  # those it is shared with see what it picks now
        sharing.remember(w, tour_id, [p["id"] for p in maker_photos(s, tours.tour(w, tour_id))])
    return {"go": f"/tour/{tour_id}"}


@router.post("/api/tours/{tour_id}/delete", dependencies=[Depends(_from_app)])
def delete_tour(tour_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    _own_tour(c, v, tour_id)
    w.execute("DELETE FROM tour WHERE id = ?", (tour_id,))
    w.commit()
    return {"go": "/tours"}


@router.get("/settings/tour-types/{type_id}", response_class=HTMLResponse)
def tour_type_page(request: Request, type_id: str, c=Depends(con)):
    t = None if type_id == "new" else tours.tour_type(c, int(type_id)) if type_id.isdecimal() else None
    if t is None and type_id != "new":
        raise HTTPException(404, "No such tour type")
    named = catalog.people(c)
    return page(request, "tour_type.html", nav="settings", type=t,
                spec=t["spec"] if t else tours.clean({"title": ""}), people=named,
                relations=tours.relation_choices(c), seasons=tours.SEASONS, who=tours.WHO, when=tours.WHEN,
                pick=tours.PICK, media=tours.MEDIA)


@router.post("/api/tour-types", dependencies=[Depends(_from_app)])
def create_tour_type(body: dict, c=Depends(con), s: Site = Depends(site)):
    tours.save_type(c, None, body)
    s.suggested.clear()  # suggestions are made from the types
    return {"go": "/settings#tours"}


@router.post("/api/tour-types/{type_id}", dependencies=[Depends(_from_app)])
def update_tour_type(type_id: int, body: dict, c=Depends(con), s: Site = Depends(site)):
    tours.save_type(c, type_id, body)
    s.suggested.clear()
    return {"go": "/settings#tours"}


@router.post("/api/tour-types/{type_id}/delete", dependencies=[Depends(_from_app)])
def delete_tour_type(type_id: int, c=Depends(con), s: Site = Depends(site)):
    c.execute("DELETE FROM tour_type WHERE id = ?", (type_id,))
    c.commit()
    s.suggested.clear()
    return {"go": "/settings#tours"}
