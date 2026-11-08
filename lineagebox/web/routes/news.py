"""New for you (outbox.py): what happened that each account may want to know, as they may see it now; and the alerts
on the Status page for admins."""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from lineagebox import outbox
from lineagebox.web.common import ADMIN, MEMBER
from lineagebox.web.deps import con, forget_news_count, page, site, viewer, writer
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/news/alerts": ADMIN,  # before /news/{event_id}, which would take "alerts" for an event's number
    "/news": MEMBER,
    "/news/{event_id}": MEMBER,
}
ALERTS_SHOWN = 8  # on the Status page, the newest


@router.get("/news/alerts", response_class=HTMLResponse)
def alerts(request: Request, v=Depends(viewer), c=Depends(con)):
    """The newest alerts, for the Status page (loaded after it): those not seen yet stand out."""
    found = [e for e in outbox.for_viewer(c, v) if e["alert"]][:ALERTS_SHOWN]
    return page(request, "_news_alerts.html", alerts=found)


@router.get("/news", response_class=HTMLResponse)
def news_page(request: Request, v=Depends(viewer), c=Depends(con), w=Depends(writer), s: Site = Depends(site)):
    """Everything new for them, newest first; opening it marks it all seen."""
    found = outbox.for_viewer(c, v)
    if found:
        outbox.mark_seen(w, v, max(e["id"] for e in found))
        forget_news_count(s, v)
    return page(request, "news.html", nav="news", events=[e for e in found if not e["alert"]],
                alerts=[e for e in found if e["alert"]])


@router.get("/news/{event_id}", response_class=HTMLResponse)
def news_photos(request: Request, event_id: int, v=Depends(viewer), c=Depends(con)):
    """The photos one event of new photos names, those the viewer sees (another event leads where it says)."""
    event = outbox.event_for(c, v, event_id)
    if event is None or event["kind"] != "new_photos":
        raise HTTPException(404, "That isn't there any more")
    images = c.execute("""SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.media, i.duration FROM image i
                          WHERE i.id IN (SELECT value FROM json_each(?)) ORDER BY i.taken_at DESC, i.id DESC""",
                       (json.dumps(event["ids"]),)).fetchall()
    return page(request, "news_photos.html", nav="news", event=event, images=images)
