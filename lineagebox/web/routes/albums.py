"""Albums picked by hand (albums.py): the list, an album with its slideshow, putting photos in it and in order,
captions, the cover, sharing it, and saving it as a video; and favourites (favourites.py), the hearts on photos."""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from lineagebox import access, accounts, albums, catalog, changes, favourites, sharing, slideshow, tours
from lineagebox.catalog import VISIBLE
from lineagebox.web.common import MEMBER, _by, _from_app, _images_or_404, _share_panel
from lineagebox.web.deps import con, page, site, viewer, writer
from lineagebox.web.routes.tours import ask_for_video, video_file, video_part
from lineagebox.web.routes.uploads import zipped
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/albums": MEMBER,
    "/favourites": MEMBER,
    "/album/{album_id}": MEMBER,
    "/album/{album_id}/video": MEMBER,
    "/download/album/{album_id}": MEMBER,
    "/download/album/{album_id}/video": MEMBER,
    "/api/albums": MEMBER,
    "/api/albums/add": MEMBER,
    "/api/albums/names": MEMBER,
    "/api/albums/{album_id}": MEMBER,
    "/api/albums/{album_id}/order": MEMBER,
    "/api/albums/{album_id}/cover": MEMBER,
    "/api/albums/{album_id}/caption": MEMBER,
    "/api/albums/{album_id}/remove": MEMBER,
    "/api/albums/{album_id}/delete": MEMBER,
    "/api/albums/{album_id}/video": MEMBER,
    "/api/images/{image_id}/favourite": MEMBER,
}
SECONDS = 5  # each photo's in an album's slideshow


class AlbumBody(BaseModel):
    name: str = Field(min_length=1, max_length=albums.NAME)
    about: str = Field("", max_length=albums.ABOUT)
    image_ids: list[int] = Field(default=[], max_length=albums.MOST)


class AddBody(BaseModel):
    """Photos for an album: the ticked ones of a page (image_ids), or one photo's (image_id); the album by its id, or
    by its name (a new one, when the viewer has none of that name)."""
    album: str = Field("", max_length=albums.NAME)
    album_id: int | None = None
    image_ids: list[int] = Field(default=[], max_length=albums.MOST)
    image_id: int | None = None


class PhotosBody(BaseModel):
    image_ids: list[int] = Field(min_length=1, max_length=albums.MOST)


class PhotoBody(BaseModel):
    image_id: int


class CaptionBody(BaseModel):
    image_id: int
    text: str = Field("", max_length=albums.CAPTION * 2)


class HeartBody(BaseModel):
    on: bool


def _album_or_404(c, album_id: int) -> dict:
    """An album the viewer sees (a member: their own and those shared with them)."""
    a = albums.get(c, album_id)
    if a is None:
        raise HTTPException(404, "No such album")
    return a


def _own_album(c, v: access.Viewer, album_id: int) -> dict:
    """An album the viewer may change: any, for an admin; a member's own. Others are not there for them."""
    a = albums.get(c, album_id)
    if a is None or not albums.theirs(v, a):
        raise HTTPException(404, "No such album of yours")
    return a


def _seen(c, v: access.Viewer, image_ids) -> list[int]:
    """The photos, once each, refused as a whole when any of them isn't the viewer's to see (or isn't there)."""
    ids = list(dict.fromkeys(image_ids))
    _images_or_404(c, v, ids)
    if ids and c.execute(f"SELECT count(*) FROM image i WHERE i.id IN (SELECT value FROM json_each(?)) AND {VISIBLE}",
                         (_json(ids),)).fetchone()[0] != len(ids):
        raise HTTPException(404, "No such photo")
    return ids


def _json(ids) -> str:
    return json.dumps(list(ids))


def passed_on(s: Site, album: dict) -> list[int]:
    """The photos of an album its maker may pass on when it is shared: all of them for an admin's; for a member's,
    those of their own part of the family and uploads (not what others shared with them), as a tour's are."""
    with s.own_con() as c:
        maker = accounts.get(c, album["account_id"]) if album["account_id"] is not None else None
        if maker is not None and maker["role"] != "admin":
            access.scope(c, s.viewers.get(c, dict(maker), shared=False))
        return albums.photo_ids(c, album["id"])


def remember(s: Site, w, album: dict) -> None:
    """Keep what a shared album passes on, now that it may have changed; those it is shared with see it at once."""
    if sharing.is_shared(w, "album", album["id"]) and sharing.remember_album(w, album["id"], passed_on(s, album)):
        s.viewers.changed()


def _track(w, v: access.Viewer, summary: str, album_ids):
    """changes.tracking of what a change does to albums (the albums, their photos, whom they are shared with and
    what they pass on), so that it can be undone. album_ids: those it touches; one it makes is above `top`."""
    ids = _json(album_ids)
    return changes.tracking(w, v, "album", summary, lambda new: [
        ("album", "id IN (SELECT value FROM json_each(?))", (ids,)),
        ("album_photo", "album_id IN (SELECT value FROM json_each(?))", (ids,)),
        ("album_passed", "album_id IN (SELECT value FROM json_each(?))", (ids,)),
        ("share", "album_id IN (SELECT value FROM json_each(?))", (ids,))])


# ---- pages ---------------------------------------------------------------------------------------------------------

@router.get("/albums", response_class=HTMLResponse)
def albums_page(request: Request, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """Their own albums (an admin's: every album), those others shared with them, and their favourites."""
    every = albums.listing(c)
    shared = {r[0] for r in w.execute("SELECT DISTINCT album_id FROM main.share WHERE album_id IS NOT NULL")}
    from_whom = {x["item_id"]: x["by_name"] for x in sharing.with_me(w, v.account_id) if x["kind"] == "album"} \
        if v.limited else {}
    for a in every:
        a["shared"] = a["id"] in shared
        a["from"] = from_whom.get(a["id"])
    mine = [a for a in every if albums.theirs(v, a)]
    loved = [i for i in favourites.mine(w, v) if v.sees_image(i)]
    cover = catalog.image(c, loved[0]) if loved else None
    return page(request, "albums.html", nav="tours", albums=mine, others=[a for a in every if a not in mine],
                favourites=len(loved), favourite_cover=cover["sha256"] if cover else None)


@router.get("/favourites", response_class=HTMLResponse)
def favourites_page(request: Request, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """The photos the viewer gave a heart, the latest first, to look through, play, or make an album of."""
    order = favourites.mine(w, v)
    rows = {r["id"]: r for r in c.execute(f"""SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.media, i.duration
                                              FROM image i WHERE i.id IN (SELECT value FROM json_each(?))
                                              AND {VISIBLE}""", (_json(order),))}
    images = [rows[i] for i in order if i in rows]
    chosen = tours.picked(c, [im["id"] for im in images])
    slides = [tours.title_card("Favourites", chosen), *tours.slides(c, chosen)] if chosen else []
    return page(request, "favourites.html", nav="tours", images=images, slides=slides, seconds=SECONDS,
                ids=[im["id"] for im in images])


@router.get("/album/{album_id}", response_class=HTMLResponse)
def album_page(request: Request, album_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer),
               s: Site = Depends(site)):
    a = _album_or_404(c, album_id)
    mine = albums.theirs(v, a)
    remember(s, w, a)  # what it passes on follows its maker's part of the family, as a tour's does
    photos = albums.photos(c, album_id)
    chosen = tours.picked(c, [p["id"] for p in photos])
    slides = [tours.title_card(a["name"], chosen, a["about"]),
              *tours.slides(c, chosen, {p["id"]: p["caption"] for p in photos})] if chosen else []
    from_whom = next((x["by_name"] for x in sharing.with_me(w, v.account_id)
                      if x["kind"] == "album" and x["item_id"] == album_id), None) if not mine else None
    signed_in = getattr(request.state, "account", None)
    cover = albums.cover(c, a)
    return page(request, "album.html", nav="tours", album=a, photos=photos, slides=slides, mine=mine,
                from_whom=from_whom, ctx=f"album:{album_id}", seconds=SECONDS, cover=cover,
                sharing=_share_panel(w, v, "album", album_id) if mine and signed_in else None,
                video=video_part("album", album_id, slideshow.latest(w, v.account_id, "album", album_id)))


@router.get("/album/{album_id}/video", response_class=HTMLResponse)
def album_video_part(request: Request, album_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """How far the album's video is, for its page to show as it goes."""
    _album_or_404(c, album_id)
    return page(request, "_slideshow_video.html",
                video=video_part("album", album_id, slideshow.latest(w, v.account_id, "album", album_id), polled=True))


@router.get("/download/album/{album_id}")
def download_album(album_id: int, v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    """The album's photos that the viewer sees, as a zip."""
    if v.limited and not s.zip_tries.allow(str(v.account_id)):
        raise HTTPException(429, "That's a lot of downloads in one hour. Wait a while, then try again.")
    a = _album_or_404(c, album_id)
    return zipped(s, c, albums.photo_ids(c, album_id), a["name"])


@router.get("/download/album/{album_id}/video")
def download_album_video(album_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer),
                         s: Site = Depends(site)):
    a = _album_or_404(c, album_id)
    return video_file(s, w, v, "album", album_id, a["name"])


@router.get("/api/albums/names")
def album_names(v=Depends(viewer), w=Depends(writer)):
    """The names of the albums the viewer may add photos to, for the box that picks one."""
    return list(dict.fromkeys(a["name"] for a in albums.changeable(w, v)))


# ---- changes -------------------------------------------------------------------------------------------------------

@router.post("/api/albums", dependencies=[Depends(_from_app)])
def create_album(body: AlbumBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """A new album, with the photos given (favourites made into an album), or none yet."""
    ids = _seen(c, v, body.image_ids)
    album_id = albums.create(w, body.name, _by(v), body.about)
    albums.add(w, album_id, ids)
    w.commit()
    return {"go": f"/album/{album_id}"}


@router.post("/api/albums/add", dependencies=[Depends(_from_app)])
def add_to_album(body: AddBody, v=Depends(viewer), c=Depends(con), w=Depends(writer), s: Site = Depends(site)):
    """Put photos at the end of an album of the viewer's, picked by its id or by its name; with a name they have no
    album of, a new album of that name."""
    ids = _seen(c, v, body.image_ids + ([body.image_id] if body.image_id is not None else []))
    if not ids:
        raise HTTPException(400, "Tick the photos to add first.")
    if body.album_id is not None:
        a = _own_album(c, v, body.album_id)
    elif not body.album.strip():
        raise HTTPException(400, "Type the name of an album (a new name makes a new album).")
    else:
        a = albums.by_name(w, body.album, v)
    top = w.execute("SELECT coalesce(max(id), 0) FROM main.album").fetchone()[0]
    with _track(w, v, f"Added photos to the album “{a['name'] if a else body.album.strip()}”",
                [a["id"] if a else top + 1]) as kept:
        if a is None:
            a = albums.get(w, albums.create(w, body.album, _by(v)))
        added = albums.add(w, a["id"], ids)
    remember(s, w, a)
    there = len(ids) - added
    message = (f"Added {added} to “{a['name']}”" + (f" ({there} already in it)" if there else "")
               if added else f"{'It is' if len(ids) == 1 else 'They are'} in “{a['name']}” already")
    return {"message": message + ".", "undo": kept.id, "album_id": a["id"]}


@router.post("/api/albums/{album_id}", dependencies=[Depends(_from_app)])
def rename_album(album_id: int, body: AlbumBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    _own_album(c, v, album_id)
    albums.rename(w, album_id, body.name, body.about)
    w.commit()
    return {"reload": True}


@router.post("/api/albums/{album_id}/order", dependencies=[Depends(_from_app)])
def order_album(album_id: int, body: PhotosBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """The album's photos in a new order (dragged, or moved up and down): those the viewer sees, in the order sent."""
    _own_album(c, v, album_id)
    albums.reorder(w, album_id, body.image_ids)
    w.commit()
    return {}


@router.post("/api/albums/{album_id}/cover", dependencies=[Depends(_from_app)])
def album_cover(album_id: int, body: PhotoBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    _own_album(c, v, album_id)
    _seen(c, v, [body.image_id])
    albums.set_cover(w, album_id, body.image_id)
    w.commit()
    return {"reload": True}


@router.post("/api/albums/{album_id}/caption", dependencies=[Depends(_from_app)])
def album_caption(album_id: int, body: CaptionBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """The album's words for one of its photos ("" takes them off: the photo's own caption shows instead)."""
    _own_album(c, v, album_id)
    _seen(c, v, [body.image_id])
    text = albums.set_caption(w, album_id, body.image_id, body.text)
    w.commit()
    return {"message": "Caption saved." if text else "Caption taken off."}


@router.post("/api/albums/{album_id}/remove", dependencies=[Depends(_from_app)])
def remove_from_album(album_id: int, body: PhotosBody, v=Depends(viewer), c=Depends(con), w=Depends(writer),
                      s: Site = Depends(site)):
    """Take photos out of the album; the photos themselves stay where they are."""
    a = _own_album(c, v, album_id)
    ids = _seen(c, v, body.image_ids)
    with _track(w, v, f"Took {len(ids)} photo{'' if len(ids) == 1 else 's'} out of the album “{a['name']}”",
                [album_id]) as kept:
        albums.remove(w, album_id, ids)
    remember(s, w, a)
    return {"message": "Taken out of the album.", "undo": kept.id}


@router.post("/api/albums/{album_id}/delete", dependencies=[Depends(_from_app)])
def delete_album(album_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer), s: Site = Depends(site)):
    a = _own_album(c, v, album_id)
    shared = sharing.is_shared(w, "album", album_id)
    with _track(w, v, f"Deleted the album “{a['name']}”", [album_id]) as kept:
        albums.delete(w, album_id)
    if shared:  # those it was shared with see its photos no more
        s.viewers.changed()
    return {"go": "/albums", "message": f"The album “{a['name']}” was deleted; its photos are where they were.",
            "undo": kept.id}


@router.post("/api/albums/{album_id}/video", dependencies=[Depends(_from_app)])
def album_video(album_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """Save as a video: the album's slideshow as the viewer sees it, made by the worker."""
    a = _album_or_404(c, album_id)
    photos = albums.photos(c, album_id)
    chosen = tours.picked(c, [p["id"] for p in photos])
    slides = [tours.title_card(a["name"], chosen, a["about"]),
              *tours.slides(c, chosen, {p["id"]: p["caption"] for p in photos})] if chosen else []
    return ask_for_video(w, v, "album", album_id, a["name"], slides, SECONDS)


@router.post("/api/images/{image_id}/favourite", dependencies=[Depends(_from_app)])
def favourite(image_id: int, body: HeartBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """The viewer's heart on a photo, given or taken off. Only theirs: it changes nothing anyone else sees, so it
    isn't kept on Recent changes (pressing ♡ again takes it back)."""
    _seen(c, v, [image_id])
    favourites.put(w, v, image_id, body.on)
    w.commit()
    return {"on": body.on}
