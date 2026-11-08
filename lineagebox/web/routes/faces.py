"""Faces: crops, naming groups and single faces, not them, not a face, looking again, renaming and merging people,
confirming suggestions."""
import httpx
from PIL import Image
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from lineagebox import access, catalog, changes, faces, media, people
from lineagebox.web.common import (ADMIN, MEMBER, FacesBody, MergeBody, NameBody, RenameBody, _among, _by,
                                   _face_to_change, _faces_spec, _family_spec, _from_app, _merge_spec, _people_or_404,
                                   _same_group)
from lineagebox.web.deps import con, site, viewer, writer
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/face/{face_id}": MEMBER,
    "/api/faces/{face_id}/photo": MEMBER,
    "/api/clusters/{cluster_id}/name": MEMBER,
    "/api/clusters/{cluster_id}/ignore": MEMBER,
    "/api/faces/{face_id}/name": MEMBER,
    "/api/faces/{face_id}/not": MEMBER,
    "/api/faces/{face_id}/not-a-face": MEMBER,
    "/api/images/{image_id}/faces": MEMBER,
    "/api/faces/{face_id}/reject": MEMBER,
    "/api/people/{person_id}/rename": MEMBER,
    "/api/people/{person_id}/merge": ADMIN,
    "/api/people/{person_id}/remove": ADMIN,
    "/api/people/{person_id}/faces": MEMBER,
    "/api/people/identify": ADMIN,
}


def _face_seen(s: Site, v: access.Viewer, face_id: int):
    """The face, if the viewer sees its photo. Checked against their Viewer on a connection of its own rather than on
    their scoped one (con), which copies all they see into it first: 20 ms for a member who sees 40,000 photos, for
    each of the dozens of faces a page shows, and for each face pointed at."""
    with s.own_con() as c:
        f = catalog.face(c, face_id)
        size = c.execute("SELECT width, height FROM image WHERE id = ?", (f["image_id"],)).fetchone() if f else None
    return (f, size) if f is not None and v.sees_image(f["image_id"]) else (None, None)


@router.get("/face/{face_id}")
def face_crop(face_id: int, v=Depends(viewer), s: Site = Depends(site)):
    f, _ = _face_seen(s, v, face_id)
    if f is None:
        raise HTTPException(404)
    path = s.cfg.face_crop_path(face_id, f["x1"], f["y1"])
    if not path.is_file():
        picture = s.cfg.picture_path(f["sha256"], f["frame"])
        if not picture.is_file():  # its preview is gone (made again on the worker's daily check): nothing to crop yet
            raise HTTPException(404, "This face's picture isn't there yet")
        im = Image.open(picture)
        w, h = im.size
        cx, cy = (f["x1"] + f["x2"]) / 2 * w, (f["y1"] + f["y2"]) / 2 * h
        half = max((f["x2"] - f["x1"]) * w, (f["y2"] - f["y1"]) * h) * 0.75
        crop = im.convert("RGB").crop((cx - half, cy - half, cx + half, cy + half))
        media._atomic_save(crop.resize((160, 160), Image.Resampling.LANCZOS), path, "JPEG", quality=88)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/api/faces/{face_id}/photo")
def face_photo(face_id: int, v=Depends(viewer), s: Site = Depends(site)):
    """The whole picture a face is on and where on it the face is (as shares of its width and height), for the
    larger look that pointing at a face gives (app.js): who is around them tells who they are."""
    f, size = _face_seen(s, v, face_id)
    if f is None:
        raise HTTPException(404, "No such face")
    video = f["frame"] is not None  # found on one of a video's frames: that frame, not the video's thumbnail
    return {"src": f"/frame/{f['sha256']}/{f['frame']}" if video else f"/p/{f['sha256']}",
            "thumb": None if video else f"/t/{f['sha256']}", "box": [f["x1"], f["y1"], f["x2"], f["y2"]],
            "width": size["width"] if size else None, "height": size["height"] if size else None}


@router.post("/api/clusters/{cluster_id}/name", dependencies=[Depends(_from_app)])
def name_cluster(cluster_id: int, body: NameBody, face: list[int] = Query(), v=Depends(viewer), c=Depends(con),
                 w=Depends(writer)):
    _same_group(c, cluster_id, face)
    faces = people.cluster_faces(c, cluster_id)  # a member's: those in photos they see
    with changes.tracking(w, v, "faces", f"Named {len(faces)} faces {body.name}", _faces_spec(v, faces)) as kept:
        pid = people.name_faces(w, faces, body.name, body.name_id, _among(v), _by(v))
    return {"person_id": pid, "undo": kept.id}


@router.post("/api/clusters/{cluster_id}/ignore", dependencies=[Depends(_from_app)])
def ignore_cluster(cluster_id: int, face: list[int] = Query(), v=Depends(viewer), c=Depends(con),
                   w=Depends(writer)):
    _same_group(c, cluster_id, face)
    ids = people.cluster_faces(c, cluster_id)
    with changes.tracking(w, v, "faces", f"Hid a group of {len(ids)} faces", _faces_spec(v, ids)) as kept:
        people.set_aside(w, ids)
    return {"faces": len(ids), "undo": kept.id}


@router.post("/api/faces/{face_id}/name", dependencies=[Depends(_from_app)])
def name_face(face_id: int, body: NameBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    _face_to_change(c, v, face_id)
    with changes.tracking(w, v, "faces", f"Named a face {body.name}", _faces_spec(v, [face_id])) as kept:
        pid = people.name_faces(w, [face_id], body.name, body.name_id, _among(v), _by(v))
    return {"person_id": pid, "undo": kept.id}


@router.post("/api/faces/{face_id}/not", dependencies=[Depends(_from_app)])
def not_them(face_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    if catalog.face(c, face_id) is None:
        raise HTTPException(404, "No such face")
    _face_to_change(c, v, face_id)
    with changes.tracking(w, v, "faces", "Took a name off a face", _faces_spec(v, [face_id])) as kept:
        people.not_them(w, [face_id])
    return {"undo": kept.id}


@router.post("/api/faces/{face_id}/not-a-face", dependencies=[Depends(_from_app)])
def not_a_face(face_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    if catalog.face(c, face_id) is None:
        raise HTTPException(404, "No such face")
    _face_to_change(c, v, face_id)
    with changes.tracking(w, v, "faces", "Marked something as not a face", _faces_spec(v, [face_id])) as kept:
        people.set_aside(w, [face_id], "not_a_face")
    return {"undo": kept.id}


@router.post("/api/images/{image_id}/faces", dependencies=[Depends(_from_app)])
def look_again(image_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer), s: Site = Depends(site)):
    """Look for faces in the photo again, more closely; new ones are recognised where they clearly match."""
    if catalog.image(c, image_id) is None:
        raise HTTPException(404, "No such photo")
    if v.limited and not s.look_tries.allow(str(v.account_id)):
        raise HTTPException(429, "That's a lot of looking for one hour: try again later.")
    try:
        new, back = faces.look_again(w, s.cfg, image_id)
    except FileNotFoundError as e:
        raise HTTPException(409, str(e)) from None
    except faces.Unavailable as e:
        raise HTTPException(503, str(e)) from None
    except (OSError, httpx.HTTPError) as e:  # the models are missing and could not be downloaded
        raise HTTPException(503, f"Face recognition isn't available: {e}") from None
    if not new and not back:
        return {"message": "No other faces found."}
    recognised = people.identify(w, new + back)  # against everyone, as the worker would
    found = [f"found {len(new)} more face{'' if len(new) == 1 else 's'}" if new else "",
             f"brought back {len(back)}" if back else "", f"recognised {recognised}" if recognised else ""]
    return {"message": "; ".join(s for s in found if s).capitalize() + ".", "reload": True}


@router.post("/api/faces/{face_id}/reject", dependencies=[Depends(_from_app)])
def reject_face(face_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    _face_to_change(c, v, face_id)
    with changes.tracking(w, v, "faces", "Set a face aside", _faces_spec(v, [face_id])) as kept:
        people.set_aside(w, [face_id])
    return {"undo": kept.id}


@router.post("/api/people/{person_id}/rename", dependencies=[Depends(_from_app)])
def rename_person(person_id: int, body: RenameBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """A new name. An admin's rename to the name of someone else merges the two (people.rename), so it is asked
    first: the answer is {"confirm": ...} until the same rename comes back with merge set."""
    _people_or_404(c, person_id)
    old = (catalog.person(c, person_id) or {"name": "?"})["name"]
    into = people.same_name(w, person_id, body.name) if v.admin else None
    if into is not None:
        other = catalog.person(w, into)["name"]
        if not body.merge:
            return {"confirm": f"{other} is already in the catalog. Renaming {old} to that makes the two one person: "
                               f"{old}'s faces, photos and family go to {other}. Merge them? (Cleanup → Recent "
                               f"changes can undo it.)"}
        with changes.tracking(w, v, "person", f"Merged {old} into {other}", _merge_spec(w, v, person_id, into)):
            pid = people.rename(w, person_id, body.name, merge_same=True)
        return {"person_id": pid}
    with changes.tracking(w, v, "person", f"Renamed {old} to {body.name}", _family_spec(v, person_id)):
        pid = people.rename(w, person_id, body.name, merge_same=v.admin)
    return {"person_id": pid}


@router.post("/api/people/{person_id}/merge", dependencies=[Depends(_from_app)])
def merge_person(person_id: int, body: MergeBody, v=Depends(viewer), c=Depends(con)):
    if catalog.person(c, person_id) is None or catalog.person(c, body.into) is None:
        raise HTTPException(404, "No such person")
    if body.into == person_id:
        raise HTTPException(400, "Pick someone else to merge into.")
    names = [catalog.person(c, p)["name"] for p in (person_id, body.into)]
    with changes.tracking(c, v, "person", "Merged {} into {}".format(*names), _merge_spec(c, v, person_id, body.into)):
        pid = people.merge(c, person_id, body.into)
    return {"person_id": pid}


@router.post("/api/people/{person_id}/remove", dependencies=[Depends(_from_app)])
def remove_person(person_id: int, v=Depends(viewer), c=Depends(con)):
    """Someone taken out of the catalog, their name off every face that had it (people.remove says who can't be).
    Kept on Recent changes, so a removal by mistake can be undone."""
    if catalog.person(c, person_id) is None:
        raise HTTPException(404, "No such person")
    with changes.tracking(c, v, "person", f"Removed {catalog.person(c, person_id)['name']}",
                          _merge_spec(c, v, person_id)) as kept:
        name, unnamed = people.remove(c, person_id)
    faces = f" {unnamed:,} face{'' if unnamed == 1 else 's'} {'is' if unnamed == 1 else 'are'} unnamed again."
    return {"message": f"{name} was removed." + (faces if unnamed else ""), "undo": kept.id}


@router.post("/api/people/{person_id}/faces", dependencies=[Depends(_from_app)])
def confirm_faces(person_id: int, body: FacesBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    if catalog.person(c, person_id) is None:
        raise HTTPException(404, "No such person")
    for face_id in body.yes + body.no:
        _face_to_change(c, v, face_id)
    name = catalog.person(c, person_id)["name"]
    with changes.tracking(w, v, "faces", f"Confirmed {len(body.yes)} faces as {name}",
                          _faces_spec(v, body.yes + body.no)):
        named = people.confirm(w, person_id, body.yes, body.no, _by(v))
    return {"named": named}


@router.post("/api/people/identify", dependencies=[Depends(_from_app)])
def identify(c=Depends(con)):
    return {"recognised": people.identify(c), "groups": people.cluster(c)[0]}
