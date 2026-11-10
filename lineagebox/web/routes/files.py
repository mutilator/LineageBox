"""Pictures and files: thumbnails, previews, frames, videos to play, originals and their XMP sidecars; and the health
check."""
import mimetypes

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, Response

from lineagebox import access, catalog, copies, sidecars, video
from lineagebox.web.common import MEMBER, PRIVATE, PUBLIC, SHA256, _attachment, _derivative
from lineagebox.web.deps import con, site, viewer
from lineagebox.web import tiles
from lineagebox.web.site import Site

router = APIRouter()
# Python's own list has no .jfif (Windows adds it from the registry, Linux only with a mime.types file): without it
# a .jfif original would be sent as a download of unknown kind rather than shown
mimetypes.add_type("image/jpeg", ".jfif")
ACCESS = {
    "/healthz": PUBLIC,
    "/t/{sha}": MEMBER,
    "/t2/{sha}": MEMBER,
    "/p/{sha}": MEMBER,
    "/frame/{sha}/{idx}": MEMBER,
    "/play/{image_id}": MEMBER,
    "/original/{file_id}": MEMBER,
    "/original/{file_id}/xmp": MEMBER,
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


@router.get("/t2/{sha}")
def sharp_thumbnail(sha: str, v=Depends(viewer), s: Site = Depends(site)):
    """A tile twice the thumbnail's size, for sharp screens (the grids' srcset): made from the preview the first time
    it is asked for, and kept (tiles.sharp). With no preview to make it from, the thumbnail: never an empty tile."""
    if not shown(v, sha):
        raise HTTPException(404)
    made = tiles.sharp(s.cfg, sha)
    if made:
        return _derivative(made, "image/webp")
    # the thumbnail stands in only for now (no preview yet, the disk nearly full, busy making others): not kept by the
    # browser for a year like the tile itself, but asked for again the next time the page shows it
    thumb = s.cfg.thumb_path(sha)
    if not thumb.is_file():
        raise HTTPException(404)
    return FileResponse(thumb, media_type="image/webp", headers={"Cache-Control": "private, no-cache"})


@router.get("/p/{sha}")
def preview(sha: str, v=Depends(viewer), s: Site = Depends(site)):
    return _derivative(s.cfg.preview_path(sha) if shown(v, sha) else None, "image/jpeg")


@router.get("/frame/{sha}/{idx}")
def video_frame(sha: str, idx: int, v=Depends(viewer), s: Site = Depends(site)):
    return _derivative(s.cfg.frame_path(sha, idx) if shown(v, sha) and 0 <= idx < 1000 else None, "image/jpeg")


@router.get("/play/{image_id}")
def play(image_id: int, c=Depends(con), s: Site = Depends(site)):
    """The video (or sound recording) as browsers can play it: the original, or the 720p copy next to it. Range
    requests let the player seek without downloading the whole file."""
    img = catalog.image(c, image_id)
    if img is None or img["media"] not in ("video", "audio"):
        raise HTTPException(404, "No such video")
    path = None
    if img["playable"] == "original" and (present := copies.present_files(c, image_id)):
        path = s.cfg.source_path(present[0])
    elif img["playable"] == "copy":
        path = copies.find_copy(c, s.cfg, image_id)
    if path is None or not path.is_file():
        raise HTTPException(404, "There is no playable copy of this video yet")
    if img["media"] == "audio":  # a recording, as its kind of sound (a copy is AAC in MP4)
        return FileResponse(path, media_type=video.SOUND_TYPES.get(path.suffix.lower(), "audio/mpeg"), headers=PRIVATE)
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


@router.get("/original/{file_id}/xmp")
def original_xmp(file_id: int, c=Depends(con)):
    """The XMP sidecar that goes with a download of the original (IMG_1234.JPG.xmp): the names, caption, tags, date
    and place the family gave the photo, for other programs. The original itself is sent as it is on the share. On a
    member's connection, only the people they can see are named."""
    f = catalog.file(c, file_id)
    data = sidecars.packet(c, f["image_id"]) if f and f["image_id"] else None
    if data is None:
        raise HTTPException(404, "Nothing has been given to this photo to go with it")
    name = f["path"].rsplit("/", 1)[-1] + sidecars.SUFFIX
    return Response(data, media_type="application/rdf+xml", headers={"Content-Disposition": _attachment(name), **PRIVATE})
