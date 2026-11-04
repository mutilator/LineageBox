"""Members' uploads, zips to download, and photos and folders hidden from members."""
import errno
import re
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from lineagebox import access, accounts, activity, catalog, db, scan, tours, uploads
from lineagebox.web.common import (ADMIN, MEMBER, PRIVATE, ZIP_BYTES, ZIP_MOST, HiddenBody, _attachment, _from_app,
                                   _image_or_404, _uploads_root, _within)
from lineagebox.web.deps import con, page, site, viewer, writer
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/upload": MEMBER,
    "/api/upload": MEMBER,
    "/download": MEMBER,
    "/download/tour/{tour_id}": MEMBER,
    "/api/images/{image_id}/hidden": ADMIN,
    "/api/folders/{folder_id}/hidden": ADMIN,
}


@router.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request, c=Depends(con), w=Depends(writer), s: Site = Depends(site)):
    """Uploading photos and whole folders, and how far the worker is with those uploaded."""
    account = getattr(request.state, "account", None)
    now = s.live(c)
    folder = account["upload_folder"] if account else None
    counts, mine, folder_id = {}, [], None
    if folder:  # their own folder: counted on a connection that also sees files not read yet
        counts = dict(w.execute("""SELECT count(*) AS files, coalesce(sum(f.image_id IS NULL), 0) AS waiting,
                                          coalesce(sum(i.stage & 2 != 0), 0) AS faces,
                                          coalesce(sum(EXISTS (SELECT 1 FROM analysis a WHERE a.image_id = i.id)),
                                                   0) AS described
                                   FROM file f LEFT JOIN image i ON i.id = f.image_id
                                   WHERE NOT f.missing AND f.path LIKE ? ESCAPE '!'""",
                                (catalog.under(folder),)).fetchone())
        # their own folder is theirs whatever their view holds (it is worked out again every half minute)
        mine = w.execute(f"""SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.media, i.duration FROM image i
                             WHERE {catalog.VISIBLE} AND i.id IN (SELECT image_id FROM file
                                                                 WHERE path LIKE ? ESCAPE '!')
                             ORDER BY i.id DESC LIMIT 60""", (catalog.under(folder),)).fetchall()
        row = w.execute("SELECT id FROM folder WHERE path = ?", (folder,)).fetchone()
        folder_id = row[0] if row else None
    return page(request, "upload.html", nav="upload", account=account, counts=counts, mine=mine,
                folder_id=folder_id, max_gb=now.upload_max_gb,
                extensions=sorted({*now.image_extensions, *now.video_extensions}))


@router.post("/api/upload", dependencies=[Depends(_from_app)])
async def upload_file(request: Request, path: str = Query(min_length=1, max_length=1000),
                      folder: str = Query("", max_length=200), s: Site = Depends(site)):
    """One file, as the request's body, into the uploader's own folder (uploads.py)."""
    account = getattr(request.state, "account", None)
    if account is None or account["person_id"] is None:
        raise HTTPException(400, "Uploading is for family accounts: each has a folder of its own.")
    if not s.upload_tries.allow(str(account["id"])):
        raise HTTPException(429, "That's a lot of files at once: wait an hour, then go on.")
    now = s.shared_cfg()
    most = now.upload_max_gb * 2**30

    def place():
        with s.own_con() as c:
            into = accounts.upload_folder(c, accounts.get(c, account["id"]), _uploads_root(now), now.folders)
            watched = next((f for f in now.folders if _within(into, f)), None)
            if watched is None:  # Settings keeps the uploads folder inside a watched one, so this shouldn't be
                raise uploads.Problem("Uploads can't be saved just now: their folder isn't one that is read here. "
                                      "Tell an admin.")
            if not scan.reachable(c, s.cfg, watched):
                raise uploads.Problem("The photo folder can't be reached just now. Is its disk or share connected? "
                                      "Try again later.")
            return uploads.destination(s.cfg.share_root, into, folder, path,
                                       {*now.image_extensions, *now.video_extensions}, now.ignore_dirs)

    try:
        target, rel = await run_in_threadpool(place)
        size = int(request.headers.get("content-length") or 0)
        if size > most:
            raise uploads.TooBig(f"{target.name} is over {now.upload_max_gb} GB, the most one upload may be.")
        await uploads.receive(request.stream(), target, most, size)
    except uploads.TooBig as e:
        raise HTTPException(413, str(e)) from None
    except (uploads.Problem, accounts.Problem) as e:
        raise HTTPException(400, str(e)) from None
    except OSError as e:  # a full disk between the checks, a folder that can't be written to, a name it refuses
        if e.errno == errno.ENOSPC:
            raise HTTPException(507, uploads.FULL) from None
        with s.own_con() as c:
            activity.log(c, f"An upload by {account['email']} could not be written: {e.strerror or e}")
        raise HTTPException(503, "The uploads folder can't be written to just now. Tell an admin: the Status "
                                 "page says why.") from None
    await run_in_threadpool(s.ask_worker)
    return {"saved": rel}


def zipped(s: Site, c, image_ids, name: str) -> StreamingResponse:
    """The viewer's photos among image_ids, as a zip made as it is sent (ZIP64, stored: photos are compressed
    already). A name in it twice gets " (2)"."""
    files, seen = [], set()
    for image_id in image_ids[:ZIP_MOST]:
        f = next((f for f in catalog.image_files(c, image_id) if not f["missing"]), None)
        if f is None:
            continue
        stem, dot, ext = f["path"].rsplit("/", 1)[-1].rpartition(".")
        entry, n = f"{stem}.{ext}" if dot else ext, 1
        while entry.lower() in seen:
            n += 1
            entry = f"{stem} ({n}).{ext}" if dot else f"{ext} ({n})"
        seen.add(entry.lower())
        files.append((s.cfg.source_path(f["path"]), entry))
    if not files:
        raise HTTPException(404, "None of those photos can be downloaded.")
    if sum(p.stat().st_size for p, _ in files if p.is_file()) > ZIP_BYTES:
        raise HTTPException(413, f"That's over {ZIP_BYTES // 2**30} GB: pick fewer photos.")
    return StreamingResponse(uploads.zip_stream(files), media_type="application/zip",
                             headers={"Content-Disposition": _attachment(f"{name}.zip"), **PRIVATE})


@router.post("/download")
async def download_ticked(request: Request, v=Depends(viewer), s: Site = Depends(site)):
    """The ticked photos of a page as a zip: a plain form post of image=<id> fields (it changes nothing)."""
    if v.limited and not s.zip_tries.allow(str(v.account_id)):
        raise HTTPException(429, "That's a lot of downloads in one hour. Wait a while, then try again.")
    fields = parse_qs((await request.body()).decode("utf-8", "replace"))
    ids = list(dict.fromkeys(int(i) for i in fields.get("image", []) if i.isdecimal()))

    def make():
        c = access.scope(db.connect(s.cfg, migrate_schema=False, check_same_thread=False), v)
        try:
            return zipped(s, c, [i for i in ids if catalog.image(c, i)], "photos")
        finally:
            c.close()
    return await run_in_threadpool(make)


@router.get("/download/tour/{tour_id}")
def download_tour(tour_id: int, v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    if v.limited and not s.zip_tries.allow(str(v.account_id)):
        raise HTTPException(429, "That's a lot of downloads in one hour. Wait a while, then try again.")
    t = tours.tour(c, tour_id)
    if t is None:
        raise HTTPException(404, "No such tour")
    chosen = tours.photos(c, t["spec"], s.live(c).blurry_below, seed=tour_id)
    return zipped(s, c, [p["id"] for p in chosen], re.sub(r"[^\w -]+", "", t["name"]).strip() or "tour")


@router.post("/api/images/{image_id}/hidden", dependencies=[Depends(_from_app)])
def hide_image(image_id: int, body: HiddenBody, c=Depends(con)):
    """Never shown to family members (papers, medical), whoever is in it; its uploader still sees it."""
    _image_or_404(c, image_id)
    c.execute("UPDATE image SET hidden = ? WHERE id = ?", (int(body.hidden), image_id))
    c.commit()
    return {"reload": True}


@router.post("/api/folders/{folder_id}/hidden", dependencies=[Depends(_from_app)])
def hide_folder(folder_id: int, body: HiddenBody, c=Depends(con)):
    """Nothing in this folder, or below it, is shown to family members (except to whoever uploaded it)."""
    if not c.execute("SELECT 1 FROM folder WHERE id = ?", (folder_id,)).fetchone():
        raise HTTPException(404, "No such folder")
    c.execute("UPDATE folder SET hidden = ? WHERE id = ?", (int(body.hidden), folder_id))
    c.commit()
    return {"reload": True}
