"""The family's words on a photo: its caption and the comments under it (comments.py). Anyone who can see the photo
may write the caption or a comment; a comment is changed or taken away by whoever wrote it, or an admin. Members'
writes are kept for undo like their other changes, and the search index takes the words in at once."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lineagebox import changes, comments, search
from lineagebox.folders import FolderIndex
from lineagebox.web.common import MEMBER, _from_app, _image_or_404
from lineagebox.web.deps import con, viewer, writer

router = APIRouter()
ACCESS = {
    "/api/images/{image_id}/caption": MEMBER,
    "/api/images/{image_id}/comments": MEMBER,
    "/api/comments/{comment_id}/edit": MEMBER,  # whoever wrote it, or an admin (comments.may_change)
    "/api/comments/{comment_id}/delete": MEMBER,
}


class WordsBody(BaseModel):
    text: str = Field(default="", max_length=comments.MOST + 200)  # tidied and checked by comments._text


def _spec(image_id: int):
    return lambda new: [("comment", "image_id = ?", (image_id,))]


def _indexed(w, image_id: int) -> None:
    """The words are found by search at once, on this photo and on its near-duplicates (search shows one of them)."""
    search.index_group(w, image_id, FolderIndex(w))
    w.commit()


def _comment_to_change(c, w, v, comment_id: int):
    row = comments.get(c, comment_id)  # c: a member sees only the comments on photos they see
    if row is None:
        raise HTTPException(404, "No such comment")
    if not comments.may_change(row, v.account_id, v.admin):
        raise HTTPException(403, "Only whoever wrote a comment, or an admin, can change it.")
    return row


@router.post("/api/images/{image_id}/caption", dependencies=[Depends(_from_app)])
def save_caption(image_id: int, body: WordsBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    _image_or_404(c, image_id)
    summary = "Wrote a photo's caption" if body.text.strip() else "Took a photo's caption off"
    with changes.tracking(w, v, "words", summary, _spec(image_id)) as kept:
        text = comments.set_caption(w, image_id, body.text, v.account_id)
    _indexed(w, image_id)
    return {"caption": text, "undo": kept.id, "reload": True}


@router.post("/api/images/{image_id}/comments", dependencies=[Depends(_from_app)])
def add_comment(image_id: int, body: WordsBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    _image_or_404(c, image_id)
    with changes.tracking(w, v, "words", "Wrote a comment on a photo", _spec(image_id)):
        comment_id = comments.add_comment(w, image_id, body.text, v.account_id)
    _indexed(w, image_id)
    return {"id": comment_id, "reload": True}


@router.post("/api/comments/{comment_id}/edit", dependencies=[Depends(_from_app)])
def edit_comment(comment_id: int, body: WordsBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    row = _comment_to_change(c, w, v, comment_id)
    with changes.tracking(w, v, "words", "Changed a comment on a photo", _spec(row["image_id"])):
        comments.edit_comment(w, comment_id, body.text)
    _indexed(w, row["image_id"])
    return {"reload": True}


@router.post("/api/comments/{comment_id}/delete", dependencies=[Depends(_from_app)])
def delete_comment(comment_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    row = _comment_to_change(c, w, v, comment_id)
    with changes.tracking(w, v, "words", "Took a comment off a photo", _spec(row["image_id"])) as kept:
        comments.delete_comment(w, comment_id)
    _indexed(w, row["image_id"])
    return {"undo": kept.id}
