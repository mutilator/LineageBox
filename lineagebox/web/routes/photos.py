"""Changes to photos: tags, people in them, dates, ticked photos, Removed (photos, copies and whole folders),
turning, keeping."""
import json

from fastapi import APIRouter, Depends, HTTPException, Query

from lineagebox import catalog, changes, cleanup, orientation, sharing, tagging, writeback
from lineagebox.web.common import (ADMIN, MEMBER, DateBody, ImageIds, NameBody, Selection, TagBody, TurnBody, added_by,
                                   dates_spec, from_app, image_or_404, images_or_404, moved_answer, name_choices,
                                   own_files, own_folder, photo_people_spec, photos_text, seen_people, tags_spec,
                                   within)
from lineagebox.web.deps import con, site, viewer, writer
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/api/people/names": MEMBER,
    "/api/tags/names": MEMBER,
    "/api/images/{image_id}/tags": MEMBER,
    "/api/images/{image_id}/tags/remove": MEMBER,
    "/api/images/{image_id}/people": MEMBER,
    "/api/images/{image_id}/people/{person_id}/remove": MEMBER,
    "/api/selection/tag": MEMBER,
    "/api/selection/date": MEMBER,
    "/api/images/{image_id}/date": MEMBER,
    "/api/selection/untag": MEMBER,
    "/api/selection/person": MEMBER,
    "/api/images/remove": MEMBER,
    "/api/images/{image_id}/remove": MEMBER,
    "/api/files/{file_id}/remove": MEMBER,
    "/api/folders/{folder_id}/remove": MEMBER,
    "/api/folders/{folder_id}/remove-files": MEMBER,
    "/api/files/{file_id}/restore": ADMIN,
    "/api/removed/restore": ADMIN,
    "/api/removed/empty": ADMIN,
    "/api/images/turn": ADMIN,
    "/api/images/turn-ok": ADMIN,
    "/api/images/{image_id}/turn": MEMBER,
    "/api/images/keep": ADMIN,
    "/api/images/unkeep": ADMIN,
    "/api/similar/{group_id}/keep": ADMIN,
    "/api/cleanup/copies/keep": ADMIN,
    "/api/cleanup/copies/unkeep": ADMIN,
}


@router.get("/api/people/names")
def people_names(v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    """Kept a minute, like the tags: every page with a name box asks. Under the viewer's own person too, since
    "your brother" depends on who is looking (two admins see everyone, but not as the same relatives)."""
    return s.keep(v, ("people names", v.me), TAG_NAMES_FOR, lambda: name_choices(c))


TAG_NAMES_FOR = 60  # seconds the names offered while typing a tag or a person's name are kept: each photo asks


@router.get("/api/tags/names")
def tag_names(v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    return s.keep(v, ("tag names",), TAG_NAMES_FOR, lambda: tagging.names(c))


@router.post("/api/images/{image_id}/tags", dependencies=[Depends(from_app)])
def add_tag(image_id: int, body: TagBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    image_or_404(c, image_id)
    with changes.tracking(w, v, "tags", f"Tagged a photo “{body.tag}”", tags_spec([image_id], body.tag)):
        tagging.add(w, [image_id], body.tag)
    return {}


@router.post("/api/images/{image_id}/tags/remove", dependencies=[Depends(from_app)])
def remove_tag(image_id: int, body: TagBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    image_or_404(c, image_id)
    with changes.tracking(w, v, "tags", f"Took the tag “{body.tag}” off a photo",
                          tags_spec([image_id], body.tag)) as kept:
        tagging.remove(w, [image_id], body.tag)
    return {"message": f"“{body.tag}” is off this photo for good.", "undo": kept.id}


@router.post("/api/images/{image_id}/people", dependencies=[Depends(from_app)])
def add_photo_person(image_id: int, body: NameBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    image_or_404(c, image_id)
    with changes.tracking(w, v, "photo people", f"Added {body.name} to a photo",
                          photo_people_spec(v, [image_id])):
        pid, _ = tagging.add_person(w, [image_id], body.name, body.name_id, seen_people(v),
                                    added_by(v))
    return {"person_id": pid}


@router.post("/api/images/{image_id}/people/{person_id}/remove", dependencies=[Depends(from_app)])
def remove_photo_person(image_id: int, person_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    images_or_404(c, v, [image_id])
    if not v.sees_person(person_id):
        raise HTTPException(403, "Someone from outside your part of the family: only an admin can change that.")
    with changes.tracking(w, v, "photo people", "Took someone off a photo",
                          photo_people_spec(v, [image_id])) as kept:
        tagging.remove_person(w, image_id, person_id)
    return {"undo": kept.id}


@router.post("/api/selection/tag", dependencies=[Depends(from_app)])
def tag_selection(body: Selection, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    images_or_404(c, v, body.image_ids)
    with changes.tracking(w, v, "tags", f"Tagged {photos_text(len(body.image_ids))} “{body.add_tag}”",
                          tags_spec(body.image_ids, body.add_tag)) as kept:
        n = tagging.add(w, body.image_ids, body.add_tag)
    return {"message": f"Tagged {photos_text(n)} “{catalog.tag_name(body.add_tag)}”.", "undo": kept.id}


@router.post("/api/selection/date", dependencies=[Depends(from_app)])
def date_selection(body: Selection, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    if not body.date.strip():
        raise HTTPException(400, "Type a date first, like summer 1987.")
    images_or_404(c, v, body.image_ids)
    with changes.tracking(w, v, "dates", f"Dated {photos_text(len(body.image_ids))} {body.date}",
                          dates_spec(body.image_ids)) as kept:
        n, span = tagging.set_date(w, body.image_ids, body.date)
    return {"message": f"Dated {photos_text(n)} {span.text}.", "undo": kept.id}


@router.post("/api/images/{image_id}/date", dependencies=[Depends(from_app)])
def date_image(image_id: int, body: DateBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    if catalog.image(c, image_id) is None:
        raise HTTPException(404, "No such photo")
    with changes.tracking(w, v, "dates", f"Dated a photo {body.date or 'automatically'}", dates_spec([image_id])):
        tagging.set_date(w, [image_id], body.date)
    return {}


@router.post("/api/selection/untag", dependencies=[Depends(from_app)])
def untag_selection(body: Selection, tag: str = Query(min_length=1, max_length=80), v=Depends(viewer),
                    c=Depends(con), w=Depends(writer)):
    images_or_404(c, v, body.image_ids)
    with changes.tracking(w, v, "tags", f"Took the tag “{tag}” off {photos_text(len(body.image_ids))}",
                          tags_spec(body.image_ids, tag)) as kept:
        tagging.remove(w, body.image_ids, tag)
    return {"message": f"“{tag}” is off {photos_text(len(body.image_ids))} for good.", "undo": kept.id}


@router.post("/api/selection/person", dependencies=[Depends(from_app)])
def person_selection(body: Selection, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    images_or_404(c, v, body.image_ids)
    with changes.tracking(w, v, "photo people", f"Added {body.person} to {photos_text(len(body.image_ids))}",
                          photo_people_spec(v, body.image_ids)) as kept:
        pid, n = tagging.add_person(w, body.image_ids, body.person, body.person_id,
                                    seen_people(v), added_by(v))
    had = len(body.image_ids) - n
    return {"message": f"Added {catalog.person(w, pid)['name']} to {photos_text(n)}"
                       + (f" ({photos_text(had)} already had them)." if had else "."), "undo": kept.id}


@router.post("/api/images/remove", dependencies=[Depends(from_app)])
def remove_images(body: ImageIds, v=Depends(viewer), c=Depends(con), w=Depends(writer), s: Site = Depends(site)):
    if v.limited:  # only the copies they uploaded (the last copy too: it is theirs); any others are the admins'
        return moved_answer(cleanup.remove_files(w, s.live(c), own_files(c, v, body.image_ids), body.reason,
                                           keep_last_copy=False))
    return moved_answer(cleanup.remove_images(c, s.live(c), body.image_ids, body.reason))


@router.post("/api/images/{image_id}/remove", dependencies=[Depends(from_app)])
def remove_image(image_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer), s: Site = Depends(site)):
    if v.limited:
        return moved_answer(cleanup.remove_files(w, s.live(c), own_files(c, v, [image_id]), "removed on its photo page",
                                           keep_last_copy=False))
    return moved_answer(cleanup.remove_images(c, s.live(c), [image_id], "removed on its photo page"))


@router.post("/api/files/{file_id}/remove", dependencies=[Depends(from_app)])
def remove_file(file_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer), s: Site = Depends(site)):
    if v.limited:
        f = catalog.file(c, file_id)
        if f is None or not within(f["path"], v.account["upload_folder"]):
            raise HTTPException(404, "No such file among your uploads")
    return moved_answer(cleanup.remove_files(w, s.live(c), [file_id], "a copy removed on its photo page"))


@router.post("/api/folders/{folder_id}/remove", dependencies=[Depends(from_app)])
def remove_folder(folder_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer), s: Site = Depends(site)):
    """Move a folder, with every file and folder in it, to the Removed folder (a member: a folder of their uploads),
    then go up to the folder it was in. What was shared of it is shared no more."""
    folder = own_folder(c, v, folder_id)
    now = s.live(c)
    if folder["path"] == "" or any(within(watched, folder["path"]) for watched in now.folders):
        raise HTTPException(400, "A watched folder, or a folder with watched folders in it, isn't removed here: stop "
                                 "watching it on the Settings page instead.")
    files = [r[0] for r in w.execute("SELECT id FROM main.file WHERE NOT missing AND path LIKE ? ESCAPE '!'",
                                     (catalog.under(folder["path"]),))]
    result = cleanup.remove_files(w, now, files, f"the folder {folder['name']} removed", keep_last_copy=False)
    if not result.errors:
        sharing.drop_folder(w, folder["path"])
        w.commit()
    up = c.execute("SELECT id, path FROM folder WHERE id = ?", (folder["parent_id"],)).fetchone()
    mine = v.account["upload_folder"] if v.limited else None
    go = f"/folder/{up['id']}" if up and (v.admin or within(up["path"], mine)) else "/folders"
    return {**moved_answer(result), "go": go}


@router.post("/api/folders/{folder_id}/remove-files", dependencies=[Depends(from_app)])
def remove_folder_files(folder_id: int, body: ImageIds, v=Depends(viewer), c=Depends(con), w=Depends(writer),
                        s: Site = Depends(site)):
    """The ticked photos' files in this folder, to the Removed folder; their copies elsewhere stay (a member: only in
    a folder of their uploads)."""
    own_folder(c, v, folder_id)
    images_or_404(c, v, body.image_ids)
    files = [r[0] for r in w.execute("""SELECT id FROM main.file WHERE folder_id = ? AND NOT missing
                                        AND image_id IN (SELECT value FROM json_each(?))""",
                                     (folder_id, json.dumps(body.image_ids)))]
    return moved_answer(cleanup.remove_files(w, s.live(c), files, body.reason, keep_last_copy=False))


@router.post("/api/files/{file_id}/restore", dependencies=[Depends(from_app)])
def restore_file(file_id: int, c=Depends(con), s: Site = Depends(site)):
    return moved_answer(cleanup.restore(c, s.live(c), [file_id]))


@router.post("/api/removed/restore", dependencies=[Depends(from_app)])
def restore_batch(batch: int = Query(), c=Depends(con), s: Site = Depends(site)):
    return moved_answer(cleanup.restore(c, s.live(c), batch=batch))


@router.post("/api/removed/empty", dependencies=[Depends(from_app)])
def empty_removed(c=Depends(con), s: Site = Depends(site)):
    return moved_answer(cleanup.empty_removed(c, s.live(c)))


@router.post("/api/images/turn", dependencies=[Depends(from_app)])
def turn_images(body: ImageIds, c=Depends(con), s: Site = Depends(site)):
    """Turn the ticked photos on Cleanup → Turned photos the way their faces say, in their files too."""
    found = orientation.candidates(c, 1)
    done, errors = 0, []
    for image_id in body.image_ids:
        try:
            if image_id not in found:
                raise writeback.Problem("its faces no longer say it is turned")
            orientation.turn(c, s.cfg, image_id, found[image_id][0])
            done += 1
        except writeback.Problem as e:
            errors.append(f"photo {image_id}: {e}")
    return {"moved": done, "errors": errors[:50], "more_errors": max(0, len(errors) - 50)}


@router.post("/api/images/turn-ok", dependencies=[Depends(from_app)])
def turn_ok(body: ImageIds, c=Depends(con)):
    orientation.mark_fine(c, body.image_ids)
    return {}


@router.post("/api/images/{image_id}/turn", dependencies=[Depends(from_app)])
def turn_image(image_id: int, body: TurnBody, v=Depends(viewer), c=Depends(con), w=Depends(writer),
               s: Site = Depends(site)):
    """Turn one photo by hand, from its page. It is turned in its files too, so a member only turns photos whose
    every copy they uploaded themselves."""
    if catalog.image(c, image_id) is None:
        raise HTTPException(404, "No such photo")
    if v.limited and any(not within(f["path"], v.account["upload_folder"])
                         for f in catalog.image_files(c, image_id) if not f["missing"]):
        raise HTTPException(403, "Only photos you uploaded yourself can be turned: it changes the file.")
    try:
        orientation.turn(w, s.cfg, image_id, body.degrees)
    except writeback.Problem as e:
        raise HTTPException(409, str(e)) from None
    return {}


@router.post("/api/images/keep", dependencies=[Depends(from_app)])
def keep_images(body: ImageIds, c=Depends(con)):
    return {"kept": cleanup.keep_images(c, body.image_ids)}


@router.post("/api/images/unkeep", dependencies=[Depends(from_app)])
def unkeep_images(body: ImageIds, c=Depends(con)):
    return {"kept": cleanup.keep_images(c, body.image_ids, keep=False)}


@router.post("/api/similar/{group_id}/keep", dependencies=[Depends(from_app)])
def keep_group(group_id: int, c=Depends(con)):
    return {"kept": cleanup.keep_group(c, group_id)}


@router.post("/api/cleanup/copies/keep", dependencies=[Depends(from_app)])
def keep_copies(a: int = Query(), b: int = Query(), c=Depends(con)):
    cleanup.keep_copies(c, a, b)
    return {}


@router.post("/api/cleanup/copies/unkeep", dependencies=[Depends(from_app)])
def unkeep_copies(a: int = Query(), b: int = Query(), c=Depends(con)):
    cleanup.keep_copies(c, a, b, keep=False)
    return {}
