"""What a folder's name means (its place, occasion, date and the names in it), corrected by an admin on the folder's
page: one misread folder would otherwise mislabel every photo in it. Kept as theirs, and undone from Recent changes."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lineagebox import changes, folders
from lineagebox.web.common import ADMIN, _from_app
from lineagebox.web.deps import con, viewer

router = APIRouter()
ACCESS = {
    "/api/folders/{folder_id}/meaning": ADMIN,
    "/api/folders/{folder_id}/meaning/again": ADMIN,
}


class MeaningBody(BaseModel):
    place: str = Field(default="", max_length=200)
    event: str = Field(default="", max_length=200)
    people: str = Field(default="", max_length=600)  # names, commas between
    when: str = Field(default="", max_length=80)


def _folder(c, folder_id: int):
    row = c.execute("SELECT id, name, path FROM folder WHERE id = ?", (folder_id,)).fetchone()
    if row is None or row["path"] == "":
        raise HTTPException(404, "No such folder")
    return row


def _done(n: int) -> str:
    if n > folders.REINDEX_AT_ONCE:
        return f"Saved. Search finds its {n:,} photos by it after the worker's next pass."
    return f"Saved for its {n:,} photo{'' if n == 1 else 's'}."


@router.post("/api/folders/{folder_id}/meaning", dependencies=[Depends(_from_app)])
def save_meaning(folder_id: int, body: MeaningBody, v=Depends(viewer), c=Depends(con)):
    row = _folder(c, folder_id)
    with changes.tracking(c, v, "folders", f"Said what the folder {row['name']} means",
                          lambda new: [("folder", "id = ?", (folder_id,))]) as kept:
        folders.set_meaning(c, folder_id, body.place, body.event, body.people, body.when)
    n = folders.reflow(c, folder_id)
    return {"message": _done(n), "undo": kept.id, "reload": True}


@router.post("/api/folders/{folder_id}/meaning/again", dependencies=[Depends(_from_app)])
def read_meaning_again(folder_id: int, v=Depends(viewer), c=Depends(con)):
    row = _folder(c, folder_id)
    with changes.tracking(c, v, "folders", f"Had the folder {row['name']} read again",
                          lambda new: [("folder", "id = ?", (folder_id,))]) as kept:
        folders.read_again(c, folder_id)
    folders.reflow(c, folder_id)
    return {"message": "The AI reads its name again on the worker's next pass.", "undo": kept.id, "reload": True}
