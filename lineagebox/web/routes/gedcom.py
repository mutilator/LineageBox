"""Family trees in and out: importing a GEDCOM file (read, matched, checked, brought in) and exporting."""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from lineagebox import events, family, gedcom, gedcom_import, people, uploads
from lineagebox.web.common import ADMIN, MEMBER, PRIVATE, _attachment, _from_app
from lineagebox.web.deps import con, page, site, viewer
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/api/family/import": ADMIN,
    "/family/import/{import_id}": ADMIN,
    "/api/family/import/{import_id}/choices": ADMIN,
    "/api/family/import/{import_id}/ai": ADMIN,
    "/api/family/import/{import_id}/apply": ADMIN,
    "/family/export": MEMBER,
}


@router.post("/api/family/import", dependencies=[Depends(_from_app)])
async def import_gedcom(request: Request, name: str = "family.ged", c=Depends(con)):
    """A GEDCOM file (the request's body) to merge in: read and matched, then shown for checking. The reading and
    matching take a while for a big tree, so they run beside the site, not in its way."""
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > gedcom_import.MOST:
            raise HTTPException(413, f"That file is over {gedcom_import.MOST // 2**20} MB.")
    if not raw:
        raise HTTPException(400, "Pick a GEDCOM file.")
    try:
        import_id = await run_in_threadpool(gedcom_import.start, c, name, bytes(raw))
    except gedcom_import.Problem as e:
        raise HTTPException(400, str(e)) from None
    return {"go": f"/family/import/{import_id}"}


@router.get("/family/import/{import_id}", response_class=HTMLResponse)
def import_page(request: Request, import_id: int, c=Depends(con), s: Site = Depends(site)):
    found = gedcom_import.load(c, import_id)
    if found is None:
        raise HTTPException(404, "No such import")
    row, data, matches = found
    rows = gedcom_import.review_rows(c, data, matches)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["how"]] = counts.get(r["how"], 0) + 1
    return page(request, "family_import.html", nav="family", imp=row, rows=rows, counts=counts,
                families=len(data["families"]), report=json.loads(row["report"]) if row["report"] else None,
                ai_on=s.live(c).describe_enabled)


@router.post("/api/family/import/{import_id}/choices", dependencies=[Depends(_from_app)])
def import_choices(import_id: int, body: dict, c=Depends(con)):
    if gedcom_import.load(c, import_id) is None:
        raise HTTPException(404, "No such import")
    choices = {k: v for k, v in body.items() if not k.startswith("name:")}
    for k, v in body.items():  # a name typed as "someone else": the person picked from the suggestions, if one was
        if k.startswith("name:") and not k.endswith("_id") and str(v).strip():
            choices[k[5:]] = str(body.get(f"{k}_id") or v)
    try:
        gedcom_import.set_choices(c, import_id, choices)
    except gedcom_import.Problem as e:
        raise HTTPException(400, str(e)) from None
    return {"message": "Saved."}


@router.post("/api/family/import/{import_id}/ai", dependencies=[Depends(_from_app)])
def import_ai(import_id: int, c=Depends(con), s: Site = Depends(site)):
    found = gedcom_import.load(c, import_id)
    if found is None:
        raise HTTPException(404, "No such import")
    now = s.live(c)
    if not now.describe_enabled:
        raise HTTPException(400, "The AI is turned off on the Settings page.")
    _, data, matches = found
    n = gedcom_import.ask_ai(c, now, data, matches)
    gedcom_import.save_matches(c, import_id, matches)
    return {"message": f"The AI looked at {n} unsure match{'' if n == 1 else 'es'}."}


@router.post("/api/family/import/{import_id}/apply", dependencies=[Depends(_from_app)])
def import_apply(import_id: int, c=Depends(con)):
    if gedcom_import.load(c, import_id) is None:
        raise HTTPException(404, "No such import")
    try:
        gedcom_import.apply(c, import_id)
    except (gedcom_import.Problem, family.Problem, events.Problem, people.Problem) as e:
        raise HTTPException(400, str(e)) from None
    return {"go": f"/family/import/{import_id}"}


@router.get("/family/export")
def export_family(version: str = "7.0", photos: bool = False, private: bool = False, v=Depends(viewer),
                  c=Depends(con), s: Site = Depends(site)):
    """The family tree as GEDCOM, for genealogy programs; with photos, a GEDZIP sent as it is made."""
    if version not in gedcom.VERSIONS:
        raise HTTPException(400, "Unknown GEDCOM version")
    if photos and v.limited and not s.zip_tries.allow(str(v.account_id)):
        raise HTTPException(429, "That's a lot of downloads in one hour. Wait a while, then try again.")
    data, media, name = gedcom.export_parts(c, s.cfg, version, photos=photos, private_living=private)
    headers = {"Content-Disposition": _attachment(name), **PRIVATE}
    if not photos:
        return Response(data, media_type="text/vnd.familysearch.gedcom", headers=headers)
    files = [(source, path) for path, source in media.items()]
    return StreamingResponse(uploads.zip_stream(files, first=("gedcom.ged", data), zip64=False),
                             media_type="application/zip", headers=headers)
