"""Sharing (sharing.py): Folders, where a member finds their uploads, what others share with them and what they share
themselves (an admin: what everyone shares), and sharing a folder, a photo or a tour, or no longer."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from lineagebox import access, catalog, changes, sharing, tours
from lineagebox.web.common import MEMBER, ShareBody, _from_app, _shared_with_me, _upload_labels
from lineagebox.web.deps import con, page, site, viewer, writer
from lineagebox.web.routes.tours import maker_photos
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/folders": MEMBER,
    "/api/shares": MEMBER,
    "/api/shares/{share_id}/delete": MEMBER,
}


@router.get("/folders", response_class=HTMLResponse)
def folders_page(request: Request, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """A member's folders: their uploads, what others share with them, and what they share, each with whom. For an
    admin, everything anyone shares (the library's folders are on Folders, at /)."""
    tree = catalog.folder_tree(c)
    by_path = {f.path: f for f in tree.values()}
    mine = v.account["upload_folder"] if v.account else None
    uploads = by_path.get(mine) if mine else None
    shares = []
    for x in sharing.everything(w, None if v.admin else v.account_id):
        if v.limited and not (x["kind"] == "folder" and x["item_id"] in v.folders or x["kind"] == "tour"
                              or x["kind"] == "image" and v.sees_image(x["item_id"])):
            continue  # no longer theirs to see (an upload an admin moved away and hid)
        x["with"] = sharing.EVERYONE if x["with_id"] is None else sharing.who(v, x["with_person"], x["with_name"],
                                                                                x["with_email"])
        x["by"] = None if x["by_id"] == v.account_id else sharing.who(v, x["by_person"], x["by_name"], x["by_email"])
        shares.append(x)
    labels = _upload_labels(w, v)
    for x in shares:
        if x["kind"] == "folder":
            x["folder_name"] = labels.get(x["folder_path"]) or x["folder_name"] or "All folders"
    return page(request, "folders.html", nav="folders", uploads=uploads,
                subfolders=[f for f in uploads.children if f.total] if uploads else [],
                shared=_shared_with_me(w, c, v), shares=shares)


def _shareable(c, v: access.Viewer, kind: str, item_id: int) -> str:
    """What is to be shared, in words for Recent changes, once it is checked to be the viewer's to share: anything for
    an admin; a member's own uploads (a folder of them, or a photo) and own tours. Otherwise it isn't there."""
    uploads = v.account["upload_folder"] if v.limited else None
    if kind == "folder":
        row = c.execute("SELECT path, name FROM folder WHERE id = ?", (item_id,)).fetchone()
        if row is None or v.limited and not access.within(row["path"], uploads):
            raise HTTPException(404, "No such folder among your uploads" if v.limited else "No such folder")
        if row["path"] == "":
            raise HTTPException(400, "The whole library can't be shared at once: share the folders in it.")
        return "your uploads folder" if row["path"] == uploads else f"the folder “{row['name']}”"
    if kind == "tour":
        t = tours.tour(c, item_id)
        if t is None or v.limited and t["account_id"] != v.account_id:
            raise HTTPException(404, "No such tour of yours" if v.limited else "No such tour")
        return f"the tour “{t['name']}”"
    if catalog.image(c, item_id) is None or v.limited and not access.theirs_to_share(c, {
            "folder_id": None, "tour_id": None, "image_id": item_id, "account_id": v.account_id,
            "by_uploads": uploads}):
        raise HTTPException(404, "Only photos you uploaded yourself can be shared.")
    return "a photo"


@router.post("/api/shares", dependencies=[Depends(_from_app)])
def share(body: ShareBody, v=Depends(viewer), c=Depends(con), w=Depends(writer), s: Site = Depends(site)):
    """Share one folder, photo or tour with an account, or with everyone who has one."""
    if v.account_id is None:
        raise HTTPException(400, "Sharing is for family accounts: turn them on under Settings → Family accounts.")
    picked = [(k, getattr(body, f"{k}_id")) for k in sharing.KINDS if getattr(body, f"{k}_id") is not None]
    if len(picked) != 1:
        raise HTTPException(400, "Share one folder, photo or tour at a time.")
    kind, item_id = picked[0]
    what = _shareable(c, v, kind, item_id)
    if body.to == "everyone":
        with_id, whom = None, sharing.EVERYONE
    else:
        found = next((r for r in sharing.recipients(w, v) if str(r["id"]) == body.to), None)
        if found is None:
            raise HTTPException(404, "There is no such family account to share with.")
        with_id, whom = found["id"], found["name"]
    with changes.tracking(w, v, "sharing", f"Shared {what} with {whom}",
                          lambda new: [("share", "id > ? AND account_id = ?", (new["share"], v.account_id))]):
        sharing.share(w, v.account_id, kind, item_id, with_id)
    if kind == "tour":  # what it shows its maker is what they may see
        sharing.remember(w, item_id, [p["id"] for p in maker_photos(s, tours.tour(w, item_id))])
    return {"reload": True}


@router.post("/api/shares/{share_id}/delete", dependencies=[Depends(_from_app)])
def unshare(share_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """Stop sharing something with someone: what they shared themselves, for a member (their connection holds only
    those); any, for an admin."""
    row = c.execute("SELECT * FROM share WHERE id = ?", (share_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "No such share")
    kind = next(k for k in sharing.KINDS if row[f"{k}_id"] is not None)
    name = {"folder": "a folder", "image": "a photo", "tour": "a tour"}[kind]
    with changes.tracking(w, v, "sharing", f"Stopped sharing {name}", lambda new: [("share", "id = ?", (share_id,))]):
        w.execute("DELETE FROM main.share WHERE id = ?", (share_id,))
    return {"reload": True}
