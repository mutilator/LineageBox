"""Checks on faces: faces whose name doesn't fit the person's age and photos with faces missed (two more lists of
Cleanup → Confirm faces: browse.py shows them through tab()). Cameras with their clock wrong, which Ages to check
points to, are routes/camera_clock.py.

- POST /api/faces/{id}/age-ok: the name is right whatever its age says; the face leaves Ages to check.
- POST /api/images/{id}/faces-found: every face of the photo was found; it leaves Faces missed. Looking for faces
  again (routes/faces.py) takes it off the list too.
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from lineagebox import camera_clock, catalog, face_ages, faces, people, settings
from lineagebox.catalog import VISIBLE
from lineagebox.web.common import ADMIN, _from_app
from lineagebox.web.deps import con, paging
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/api/faces/{face_id}/age-ok": ADMIN,
    "/api/images/{image_id}/faces-found": ADMIN,
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

