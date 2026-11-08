"""Cleanup → Suggested dates (admins): scans dated by the day they were scanned, each with what can tell its real date
(scan_dates.py), to date with one click or by typing it. Dating one goes through POST /api/images/{id}/date, as on
its page (undoable); it then leaves the list.

- GET /cleanup/dates?show=documents|photos|young&page=: the documents, the scanned photos, or the photos where
  someone looks too young for their date (scan_dates.young), with their suggestions.
- POST /api/images/{id}/read-date: the AI reads the dates written on it now (about 20 seconds), kept for the list.
- POST /api/images/{id}/date-ok: its date is right as it is; it leaves the list.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from lineagebox import catalog, dates, events, scan_dates, settings
from lineagebox.web.common import ADMIN, _from_app
from lineagebox.web.deps import con, page, paging, site, viewer
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/cleanup/dates": ADMIN,
    "/api/images/{image_id}/read-date": ADMIN,
    "/api/images/{image_id}/date-ok": ADMIN,
}
TABS = {"documents": "Documents", "photos": "Scanned photos", "young": "Looks too young"}
FOUND_FOR = 60  # seconds the list of scans is kept for the page (any change clears it)


def found(s: Site, v, c) -> dict[str, list[dict]]:
    return s.keep(v, ("scan dates",), FOUND_FOR, lambda: scan_dates.scans(c))


@router.get("/cleanup/dates", response_class=HTMLResponse)
def suggested_dates_page(request: Request, show: str = "documents", pg: int = Query(1, alias="page", ge=1),
                         per: str | None = None, v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    """The documents (or scanned photos) dated by when they were scanned, a page at a time, each with its
    suggestions: those with a date to use first, then the rest, each in the order of their files."""
    if show not in TABS:
        show = "documents"
    lists = found(s, v, c)
    south, day_first = dates.south(c), events.day_first(c)
    items = [{**item, "suggestions": scan_dates.suggestions(c, item, south, day_first)} for item in lists[show]]
    items.sort(key=lambda i: not any(x["text"] for x in i["suggestions"]))  # stable: their files' order otherwise
    p = paging(request, "scan_dates", pg, per, "25")
    p.total = len(items)
    p.pg = min(p.pg, p.pages)  # past the end (the last ones were just dated): its last page
    shown = items[p.offset:p.offset + p.size]
    for item in shown:  # what the AI read on it: None before it has been read
        row = scan_dates.reading(c, item["id"])
        item["read_dates"] = json.loads(row["dates"]) if row else None
    return page(request, "cleanup_dates.html", nav="cleanup", show=show, tabs=TABS,
                counts={k: len(x) for k, x in lists.items()}, items=shown, paging=p, page_query={"show": show},
                ai_on=s.live(c).describe_enabled)


@router.post("/api/images/{image_id}/read-date", dependencies=[Depends(_from_app)])
def read_date(image_id: int, c=Depends(con), s: Site = Depends(site)):
    """The AI reads the dates written on it, now."""
    if catalog.image(c, image_id) is None:
        raise HTTPException(404, "No such photo")
    try:
        said = scan_dates.read(c, s.live(c), image_id)
    except scan_dates.Problem as e:
        raise HTTPException(400, str(e)) from None
    return {"message": f"It says {', '.join(d['written'] for d in said)}." if said else "No date is written on it.",
            "reload": True}


@router.post("/api/images/{image_id}/date-ok", dependencies=[Depends(_from_app)])
def date_ok(image_id: int, c=Depends(con)):
    """Its date is right as it is: off the list."""
    if catalog.image(c, image_id) is None:
        raise HTTPException(404, "No such photo")
    settings.put(c, scan_dates.OK_KEY, sorted(set(settings.get(c, scan_dates.OK_KEY, [])) | {image_id}))
    return {}
