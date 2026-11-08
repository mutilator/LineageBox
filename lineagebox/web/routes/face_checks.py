"""Checks on faces: faces whose name doesn't fit the person's age and photos with faces missed (two more lists of
Cleanup → Confirm faces: browse.py shows them through tab()). Cameras with their clock wrong, which Ages to check
points to, are routes/camera_clock.py.

- POST /api/faces/{id}/age-ok: the name is right whatever its age says; the face leaves Ages to check.
- POST /api/images/{id}/faces-found: every face of the photo was found; it leaves Faces missed. Looking for faces
  again (routes/faces.py) takes it off the list too.

Cleanup → No faces found (its own card): photos where the AI's description sees people and no face was found
(faces.no_faces), to look at again or set aside.
- GET /cleanup/no-faces?page=: a page of them, the newest first.
- POST /api/images/look-again {image_ids}: the worker looks at these again, more closely (faces.look_again_waiting).
- POST /api/images/no-faces-ok {image_ids}: no faces to find in these (people facing away, too far off): not listed
  again. Undoable.
"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from lineagebox import camera_clock, catalog, changes, face_ages, faces, people, settings
from lineagebox.catalog import VISIBLE
from lineagebox.web.common import ADMIN, ImageIds, _from_app
from lineagebox.web.deps import con, page, paging, site, viewer
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/api/faces/{face_id}/age-ok": ADMIN,
    "/api/images/{image_id}/faces-found": ADMIN,
    "/cleanup/no-faces": ADMIN,
    "/api/images/look-again": ADMIN,
    "/api/images/no-faces-ok": ADMIN,
}

TABS = {"ages": "Ages to check", "missed": "Faces missed"}  # Cleanup → Confirm faces' lists of this module
COUNTS_FOR = 60  # seconds the lists' counts are kept for each viewer


def _ok(c) -> set[int]:
    return set(settings.get(c, face_ages.OK_KEY, []))


def counts(s: Site, v, c) -> dict:
    """How many each of this module's lists has, for the tabs of Confirm faces; kept a minute, through changes."""
    return s.keep(v, ("face checks",), COUNTS_FOR, lambda: {"ages": len(face_ages.age_checks(c, _ok(c))),
                                                           "missed": faces.missed(c, 0, 0)[1]}, through_changes=True)


def tab(request: Request, show: str, pg: int, per: str | None, c) -> dict:
    """What Confirm faces shows for one of TABS (see browse.cleanup_faces_page)."""
    if show == "ages":
        p = paging(request, "ages", pg, per, "100")
        found = face_ages.age_checks(c, _ok(c))
        p.total = len(found)
        shown = found[p.offset:p.offset + p.size]
        by_person: dict[int, dict] = {}
        for check in shown:
            by_person.setdefault(check["person_id"], {"id": check["person_id"], "name": check["name"],
                                                      "born": check["born"], "faces": []})["faces"].append(check)
        cameras = {}
        for check in found:
            if check["why"] == "born after" and check["camera"] and check["source"] in camera_clock.OWN:
                cameras[check["camera"]] = cameras.get(check["camera"], 0) + 1
        return {"paging": p, "aged": list(by_person.values()), "aged_total": p.total,
                "clock_cameras": sorted(cameras.items(), key=lambda kv: -kv[1])}
    p = paging(request, "missed", pg, per, "25")
    photos, p.total = faces.missed(c, p.offset, p.size)
    return {"paging": p, "missed_photos": photos}


def looks_like(v, c, w, cluster_ids) -> dict[int, dict]:
    """cluster -> who its faces look like (people.looks_like, against everyone on w), for a group card: their id,
    name, clearest face named by hand, and whether some of the group's photos are dated before they were born. Only
    people the viewer sees."""
    out = {}
    for cid, (pid, score, unborn) in people.looks_like(w, cluster_ids).items():
        row = catalog.person(c, pid) if v.sees_person(pid) else None
        if row is None:
            continue
        cover = c.execute(f"""SELECT f.id FROM face f JOIN image i ON i.id = f.image_id
                              WHERE f.person_id = ? AND f.assigned_by = 'user' AND {VISIBLE}
                              ORDER BY f.quality DESC LIMIT 1""", (pid,)).fetchone()
        out[cid] = {"id": pid, "name": row["name"], "face": cover[0] if cover else None, "score": score,
                    "born_after": unborn > 0}
    return out


@router.post("/api/faces/{face_id}/age-ok", dependencies=[Depends(_from_app)])
def age_ok(face_id: int, c=Depends(con)):
    """Named right, whatever its age says: not listed again."""
    if catalog.face(c, face_id) is None:
        raise HTTPException(404, "No such face")
    settings.put(c, face_ages.OK_KEY, sorted(_ok(c) | {face_id}))
    return {}


@router.post("/api/images/{image_id}/faces-found", dependencies=[Depends(_from_app)])
def faces_found(image_id: int, c=Depends(con)):
    """Every face was found: off the list of photos with faces missed."""
    if catalog.image(c, image_id) is None:
        raise HTTPException(404, "No such photo")
    c.execute("UPDATE image SET faces_again = datetime('now', 'localtime') WHERE id = ?", (image_id,))
    c.commit()
    return {}


@router.get("/cleanup/no-faces", response_class=HTMLResponse)
def no_faces_page(request: Request, pg: int = Query(1, alias="page", ge=1), per: str | None = None,
                  c=Depends(con), s: Site = Depends(site)):
    """Photos where the AI's description sees people and no face was found, a page at a time."""
    p = paging(request, "no_faces", pg, per, "50")
    photos, p.total = faces.no_faces(c, p.offset, p.size)
    if p.pg > p.pages:  # past the end (the last ones were just set aside): its last page
        p.pg = p.pages
        photos, p.total = faces.no_faces(c, p.offset, p.size)
    return page(request, "cleanup_no_faces.html", nav="cleanup", photos=photos, paging=p,
                waiting=len(settings.get(c, faces.LOOK_AGAIN_KEY, [])),
                set_aside=len(settings.get(c, faces.NONE_OK_KEY, [])), faces_on=s.live(c).faces_enabled)


def _known(c, ids: list[int]) -> list[int]:
    """Of these photo ids, those in the catalog, in their order, each once."""
    there = {r[0] for r in c.execute("SELECT id FROM image WHERE id IN (SELECT value FROM json_each(?))",
                                     (json.dumps(ids),))}
    return list(dict.fromkeys(i for i in ids if i in there))


@router.post("/api/images/look-again", dependencies=[Depends(_from_app)])
def look_again_later(body: ImageIds, c=Depends(con)):
    """The worker looks at these again, more closely, as soon as it can (a pass starts now, as Check now starts one)."""
    ids = _known(c, body.image_ids)
    if not ids:
        raise HTTPException(404, "No such photo")
    waiting = settings.get(c, faces.LOOK_AGAIN_KEY, [])
    settings.put(c, faces.LOOK_AGAIN_KEY, waiting + [i for i in ids if i not in set(waiting)])
    settings.put(c, "run_requested", datetime.now().isoformat(timespec="seconds"))
    n = len(ids)
    return {"message": f"{n} photo{'' if n == 1 else 's'} will be looked at again in the background, a few a minute; "
                       "the faces found are recognised where they clearly match.", "reload": True}


@router.post("/api/images/no-faces-ok", dependencies=[Depends(_from_app)])
def no_faces_ok(body: ImageIds, v=Depends(viewer), c=Depends(con)):
    """No faces to find in these photos: not listed again (Undo, or Recent changes, lists them again)."""
    ids = _known(c, body.image_ids)
    if not ids:
        raise HTTPException(404, "No such photo")
    n = len(ids)
    spec = lambda _: [("settings", "key = ?", (faces.NONE_OK_KEY,))]  # noqa: E731
    with changes.tracking(c, v, "faces", f"Said {n} photo{'' if n == 1 else 's'} had no faces to find", spec) as kept:
        settings.put(c, faces.NONE_OK_KEY, sorted(set(settings.get(c, faces.NONE_OK_KEY, [])) | set(ids)))
    return {"undo": kept.id}
