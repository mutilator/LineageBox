"""Family links by id, how a child belongs to a parent (birth, adopted, foster), the family checks, partners together
or not, and the person marked as me."""
from fastapi import APIRouter, Depends, HTTPException

from lineagebox import catalog, changes, family, family_check, settings
from lineagebox.web.common import (ADMIN, MEMBER, CheckBody, MeBody, PediBody, UnlinkBody, _among, _family_spec,
                                   _from_app, _people_or_404)
from lineagebox.web.deps import con, viewer, writer

router = APIRouter()
ACCESS = {
    "/api/family/unlink": MEMBER,
    "/api/family/link": MEMBER,
    "/api/family/checks/ok": ADMIN,
    "/api/family/current": MEMBER,
    "/api/family/me": ADMIN,
    "/api/family/kind": MEMBER,
}


@router.post("/api/family/unlink", dependencies=[Depends(_from_app)])
def unlink(body: UnlinkBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    _people_or_404(c, body.a, body.b)
    with changes.tracking(w, v, "family", f"Took off a family link ({body.kind})", _family_spec(v, body.a, body.b)):
        family.unlink(w, body.kind, body.a, body.b, body.what)
    return {}


@router.post("/api/family/link", dependencies=[Depends(_from_app)])
def link_ids(body: UnlinkBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """b is a's <what> ("parent", "mother"), for people already known by id (the name may be shared)."""
    if catalog.person(c, body.a) is None or catalog.person(c, body.b) is None:
        raise HTTPException(404, "No such person")
    names = (catalog.person(c, body.b)["name"], catalog.person(c, body.a)["name"])
    with changes.tracking(w, v, "family", f"Linked {names[0]} as {names[1]}'s {body.what}",
                          _family_spec(v, body.a, body.b)):
        family.link(w, body.a, body.what, body.b, shown=_among(v))
    return {}


@router.post("/api/family/checks/ok", dependencies=[Depends(_from_app)])
def family_check_ok(body: CheckBody, c=Depends(con)):
    """A problem on Cleanup → Family tree that is right as it is: not listed again."""
    family_check.mark_ok(c, body.key)
    return {}


@router.post("/api/family/current", dependencies=[Depends(_from_app)])
def set_current_partner(body: UnlinkBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    _people_or_404(c, body.a, body.b)
    with changes.tracking(w, v, "family", "Changed whether partners are together", _family_spec(v, body.a, body.b)):
        family.set_current(w, body.a, body.b, body.what == "current", _among(v))
    return {}


@router.post("/api/family/me", dependencies=[Depends(_from_app)])
def set_me(body: MeBody, c=Depends(con)):
    if catalog.person(c, body.person_id) is None:
        raise HTTPException(404, "No such person")
    settings.put(c, "me", body.person_id)
    return {}


@router.post("/api/family/kind", dependencies=[Depends(_from_app)])
def set_kind(body: PediBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """A child entered as born to a parent was adopted, or fostered, or the other way round."""
    _people_or_404(c, body.child, body.parent)
    if not c.execute("SELECT 1 FROM parent WHERE child_id = ? AND parent_id = ?", (body.child, body.parent)).fetchone():
        raise HTTPException(404, "They aren't entered as parent and child.")
    names = (catalog.person(c, body.child)["name"], catalog.person(c, body.parent)["name"])
    word = {"birth": "born to", "adopted": "adopted by", "foster": "fostered by"}.get(body.kind, body.kind)
    with changes.tracking(w, v, "family", f"Said {names[0]} was {word} {names[1]}",
                          _family_spec(v, body.child, body.parent)) as kept:
        family.set_pedi(w, body.child, body.parent, body.kind, _among(v))
    return {"undo": kept.id}
