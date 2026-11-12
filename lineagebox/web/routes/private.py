"""Cleanup → Private papers: photos that look like identity cards, money and tax papers, health records and
certificates of living people (private.py), to hide from family members or say are fine to show, a page at a time.
An admin's are the whole library's; a family member's, the photos they uploaded and nobody else has (as their Blank and
blurry: cleanup._own_uploads), which they manage themselves. Either is one change, to undo."""
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from lineagebox import access, changes, private, settings
from lineagebox.web.common import MEMBER, ImageIds, _from_app
from lineagebox.web.deps import con, page, paging, site, viewer, writer
from lineagebox.web.routes.cleanup import _own_uploads
from lineagebox.web.routes.face_checks import _known
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {  # a member: of the photos they uploaded (_theirs)
    "/cleanup/private": MEMBER,
    "/api/images/hide-private": MEMBER,
    "/api/images/private-ok": MEMBER,
}
FOUND_FOR = 60  # seconds the photos found are kept for each viewer: finding them reads every description


def _theirs(c, v: access.Viewer) -> set[int] | None:
    """The photos this viewer manages here: a member's own uploads; None for an admin (all of them)."""
    return _own_uploads(c, v) if v.limited else None


def found(s: Site, v: access.Viewer, c) -> list[dict]:
    """private.suggestions, of the photos the viewer manages (_theirs), kept a while (any change made on the site
    finds them again). Cleanup's card counts these too."""
    def work():
        own = _theirs(c, v)
        return [f for f in private.suggestions(c) if own is None or f["id"] in own]
    return s.keep(v, ("private papers",), FOUND_FOR, work)


def _ids(c, v: access.Viewer, image_ids: list[int]) -> list[int]:
    """The photos asked about, when they are all the viewer's to manage here."""
    ids = _known(c, image_ids)
    if not ids:
        raise HTTPException(404, "No such photo")
    own = _theirs(c, v)
    if own is not None and not set(ids) <= own:
        raise HTTPException(403, "Only photos you uploaded can be hidden or kept here; an admin looks after the rest.")
    return ids


@router.get("/cleanup/private", response_class=HTMLResponse)
def private_page(request: Request, kind: str = "", pg: int = Query(1, alias="page", ge=1), per: str | None = None,
                 v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    """The photos that look private, those family members may see first; kind: one kind only."""
    everything = found(s, v, c)
    counts = {k: sum(f["kind"] == k for f in everything) for k in private.KINDS}
    kind = kind if kind in private.KINDS else ""
    shown = [f for f in everything if not kind or f["kind"] == kind]
    p = paging(request, "private", pg, per, "50")
    p.total = len(shown)
    if p.pg > p.pages:  # past the end (the last ones were just hidden): its last page
        p.pg = p.pages
    own = _theirs(c, v)
    hidden = {r[0] for r in c.execute("SELECT id FROM image WHERE hidden")}
    fine = set(settings.get(c, private.OK_KEY, []))
    return page(request, "cleanup_private.html", nav="cleanup", photos=shown[p.offset:p.offset + p.size], paging=p,
                kind=kind, kinds=private.KINDS, counts=counts, total=len(everything),
                named=sum(f["named"] for f in everything),
                fine=len(fine if own is None else fine & own), hidden=len(hidden if own is None else hidden & own),
                page_query={"kind": kind} if kind else {})


@router.post("/api/images/hide-private", dependencies=[Depends(_from_app)])
def hide_private(body: ImageIds, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """Never shown to family members, whoever is in them (whoever uploaded one still sees it): as the photo page's
    Hide from family members, for the ticked photos at once."""
    ids = _ids(c, v, body.image_ids)
    n = len(ids)
    spec = lambda _: [("image", "id IN (SELECT value FROM json_each(?))", (json.dumps(ids),))]  # noqa: E731
    with changes.tracking(w, v, "photos", f"Hid {n} photo{'' if n == 1 else 's'} of private papers from family "
                                          f"members", spec) as kept:
        w.executemany("UPDATE image SET hidden = 1 WHERE id = ?", ((i,) for i in ids))
    return {"undo": kept.id, "message": f"Hidden from family members: {n} photo{'' if n == 1 else 's'}."}


@router.post("/api/images/private-ok", dependencies=[Depends(_from_app)])
def private_ok(body: ImageIds, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """Fine for family members to see: not listed again (Undo, or Recent changes, lists them again)."""
    ids = _ids(c, v, body.image_ids)
    n = len(ids)
    spec = lambda _: [("settings", "key = ?", (private.OK_KEY,))]  # noqa: E731
    with changes.tracking(w, v, "photos", f"Said {n} photo{'' if n == 1 else 's'} of papers were fine to show",
                          spec) as kept:
        settings.put(w, private.OK_KEY, sorted(set(settings.get(w, private.OK_KEY, [])) | set(ids)))
    return {"undo": kept.id, "message": f"Fine to show: {n} photo{'' if n == 1 else 's'}, not listed again."}
