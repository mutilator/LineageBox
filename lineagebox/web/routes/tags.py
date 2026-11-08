"""Tags across the whole library, for admins: rename one, merge it into another, or take it off every photo. Each is
kept on Recent changes with an Undo (changes.tracking)."""
import json
from urllib.parse import quote

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from lineagebox import changes, tagging
from lineagebox.catalog import tag_name
from lineagebox.web.common import ADMIN, _from_app, _photos
from lineagebox.web.deps import site, viewer, writer
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/api/tags/rename": ADMIN,
    "/api/tags/remove-everywhere": ADMIN,
}


class RenameBody(BaseModel):
    tag: str = Field(min_length=1, max_length=80)
    to: str = Field(min_length=1, max_length=80)
    merge: bool = False  # the new name is another tag's: put them together (asked first)


class TagBody(BaseModel):
    tag: str = Field(min_length=1, max_length=80)


def _spec(w, *names: str):
    """What a change to these tags can touch, for its undo: the tags themselves (by the ids they have now: a merge
    deletes one), every photo's say on them, and the AI's words that are read as them."""
    names = [tag_name(n) for n in names]
    ids = json.dumps([r[0] for r in w.execute("SELECT id FROM tag WHERE name IN (SELECT value FROM json_each(?))",
                                              (json.dumps(names),))])
    among = "IN (SELECT value FROM json_each(?))"
    return lambda new: [("tag", f"id {among} OR (id > ? AND name {among})", (ids, new["tag"], json.dumps(names))),
                        ("image_tag", f"tag_id {among}", (ids,)),
                        ("tag_alias", f"name {among} OR tag_id {among}", (json.dumps(names), ids))]


@router.post("/api/tags/rename", dependencies=[Depends(_from_app)])
def rename_tag(body: RenameBody, v=Depends(viewer), w=Depends(writer), s: Site = Depends(site)):
    """Rename a tag on every photo. A name another tag has is answered with a question first (app.js asks it, then
    sends merge)."""
    old, new = tag_name(body.tag), tag_name(body.to)
    try:
        with changes.tracking(w, v, "tags", f"Renamed the tag “{old}” to “{new}”" if not body.merge else
                              f"Put the tag “{old}” into “{new}”", _spec(w, old, new)) as kept:
            name, ids = tagging.rename(w, old, new, body.merge)
    except tagging.NeedsMerge as e:
        w.rollback()
        return {"confirm": str(e), "again": {"merge": True}}
    later = len(ids) > tagging.REINDEX_AT_ONCE
    if later:
        s.ask_worker()
    return {"message": f"“{old}” is “{name}” now, on {_photos(len(ids))}."
                       + (" Search finds them by it in a few minutes." if later else ""),
            "undo": kept.id, "go": f"/tag/{quote(name)}", "wait": 1.5}


@router.post("/api/tags/remove-everywhere", dependencies=[Depends(_from_app)])
def remove_tag_everywhere(body: TagBody, v=Depends(viewer), w=Depends(writer), s: Site = Depends(site)):
    """Take a tag off every photo, for good: the AI doesn't give it again (tagging.remove_everywhere)."""
    old = tag_name(body.tag)
    with changes.tracking(w, v, "tags", f"Took the tag “{old}” off every photo", _spec(w, old)) as kept:
        ids = tagging.remove_everywhere(w, old)
    if len(ids) > tagging.REINDEX_AT_ONCE:
        s.ask_worker()
    return {"message": f"“{old}” is off {_photos(len(ids))} for good, and the AI won't give it again.",
            "undo": kept.id, "go": "/tags", "wait": 1.5}
