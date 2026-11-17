"""Settings → Reorganize folders (admins): the photo folders' files moved into year and month folders by LineageBox
(reorganize.py), after a preview of where everything goes, and the latest reorganization put back. The moving runs
beside the request; the page follows it (/settings/reorganize/progress)."""
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from lineagebox import db, reorganize
from lineagebox.web.common import ADMIN, from_app
from lineagebox.web.deps import con, page, site, viewer, writer
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/settings/reorganize": ADMIN,
    "/settings/reorganize/progress": ADMIN,
    "/api/reorganize/start": ADMIN,
    "/api/reorganize/stop": ADMIN,
    "/api/reorganize/undo": ADMIN,
}


class StartBody(BaseModel):
    by_type: bool = False
    remove_empty: bool = False
    remove_junk: bool = False


def _last(w) -> dict | None:
    """The latest reorganization, for the page: its row, and its problems as a list."""
    row = reorganize.latest(w)
    if row is None:
        return None
    total = row["planned"]  # while putting back: what it moved
    if row["state"] == "undoing":
        total = w.execute("SELECT count(*) FROM reorganize_move WHERE run_id = ?", (row["id"],)).fetchone()[0]
    return {**dict(row), "problems": json.loads(row["problems"] or "[]"), "total": total,
            "active": row["state"] in ("moving", "undoing")}


def _connect(s: Site):
    return lambda: db.connect(s.cfg, migrate_schema=False, check_same_thread=False)


@router.get("/settings/reorganize", response_class=HTMLResponse)
def reorganize_page(request: Request, preview: bool = False, by_type: bool = False, remove_empty: bool = False,
                    remove_junk: bool = False, c=Depends(con), w=Depends(writer), s: Site = Depends(site)):
    """What a reorganization would do (preview, with the options ticked), or how the one under way is going, and the
    latest to put back. Before a preview, taking away empty folders and deleting junk are ticked."""
    now = s.live(c)
    last = _last(w)
    if not preview:
        remove_empty = remove_junk = True
    plan = reorganize.plan(c, now, by_type, remove_junk=remove_junk) \
        if preview and not (last and last["active"]) and now.folders else None
    return page(request, "settings_reorganize.html", nav="settings", cfg=now, last=last, plan=plan, by_type=by_type,
                remove_empty=remove_empty, remove_junk=remove_junk, undoable=reorganize.undoable(w),
                kinds=reorganize.KINDS, other=reorganize.OTHER)


@router.get("/settings/reorganize/progress", response_class=HTMLResponse)
def progress(request: Request, w=Depends(writer)):
    """How the reorganization under way is going, every few seconds (data-live); once it is over, a mark that makes
    the page load again."""
    last = _last(w)
    if last is None or not last["active"]:
        return HTMLResponse("<span data-reload-page></span>")
    return page(request, "_reorganize_progress.html", last=last)


@router.post("/api/reorganize/start", dependencies=[Depends(from_app)])
def start(body: StartBody, v=Depends(viewer), c=Depends(con), w=Depends(writer), s: Site = Depends(site)):
    account_id = v.account["id"] if v.account else None
    run_id = reorganize.start(w, s.live(c), body.by_type, account_id, connect=_connect(s),
                              remove_empty=body.remove_empty, remove_junk=body.remove_junk)
    return {"ok": True, "id": run_id, "go": "/settings/reorganize"}


@router.post("/api/reorganize/stop", dependencies=[Depends(from_app)])
def stop():
    reorganize.stop()
    return {"ok": True}


@router.post("/api/reorganize/undo", dependencies=[Depends(from_app)])
def undo(c=Depends(con), w=Depends(writer), s: Site = Depends(site)):
    run_id = reorganize.undo(w, s.live(c), connect=_connect(s))
    return {"ok": True, "id": run_id, "go": "/settings/reorganize"}
