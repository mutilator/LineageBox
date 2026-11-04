"""Pictures and files: thumbnails, previews, frames, videos to play, originals; and the health check."""
import mimetypes

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

from lineagebox import access, catalog, copies
from lineagebox.web.common import MEMBER, PRIVATE, PUBLIC, SHA256, _derivative
from lineagebox.web.deps import con, site, viewer
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/healthz": PUBLIC,
    "/t/{sha}": MEMBER,
    "/p/{sha}": MEMBER,
    "/frame/{sha}/{idx}": MEMBER,
    "/play/{image_id}": MEMBER,
    "/original/{file_id}": MEMBER,
}


@router.get("/healthz", response_class=PlainTextResponse)
def healthz(c=Depends(con)):
    """For monitoring and reverse proxies: the catalog answers."""
    c.execute("SELECT 1 FROM settings LIMIT 1").fetchone()
    return "ok"


def shown(v: access.Viewer, sha: str) -> bool:
    return bool(SHA256.match(sha)) and (v.admin or sha in v.shas)


@router.get("/t/{sha}")
def thumbnail(sha: str, v=Depends(viewer), s: Site = Depends(site)):
    return _derivative(s.cfg.thumb_path(sha) if shown(v, sha) else None, "image/webp")


@router.get("/p/{sha}")
def preview(sha: str, v=Depends(viewer), s: Site = Depends(site)):
    return _derivative(s.cfg.preview_path(sha) if shown(v, sha) else None, "image/jpeg")


@router.get("/frame/{sha}/{idx}")
def video_frame(sha: str, idx: int, v=Depends(viewer), s: Site = Depends(site)):
    return _derivative(s.cfg.frame_path(sha, idx) if shown(v, sha) and 0 <= idx < 100 else None, "image/jpeg")


@router.get("/play/{image_id}")
def play(image_id: int, c=Depends(con), s: Site = Depends(site)):
    """The video as browsers can play it: the original, or the 720p copy next to it. Range requests let the
    player seek without downloading the whole file."""
    img = catalog.image(c, image_id)
    if img is None or img["media"] != "video":
        raise HTTPException(404, "No such video")
    path = None
    if img["playable"] == "original" and (present := copies.present_files(c, image_id)):
        path = s.cfg.source_path(present[0])
    elif img["playable"] == "copy":
        path = copies.find_copy(c, s.cfg, image_id)
    if path is None or not path.is_file():
        raise HTTPException(404, "There is no playable copy of this video yet")
    # MOV with H.264 is the same as MP4 to a browser, which may refuse it as video/quicktime
    return FileResponse(path, media_type="video/mp4", headers=PRIVATE)


@router.get("/original/{file_id}")
def original(file_id: int, c=Depends(con), s: Site = Depends(site)):
    f = catalog.file(c, file_id)
    path = s.cfg.source_path(f["path"]) if f else None
    if path is None or not path.is_file():
        raise HTTPException(404, "That file isn't there any more")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name, headers=PRIVATE)
