"""A family book (book.py): a person's, or a branch's (a person and their descendants), as a page made to be printed
or saved as a PDF from the browser."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from lineagebox import book, catalog
from lineagebox.web.common import MEMBER
from lineagebox.web.deps import con, page

router = APIRouter()
ACCESS = {
    "/book/person/{person_id}": MEMBER,
    "/book/branch/{person_id}": MEMBER,
}


def _book(request: Request, c, person_id: int, branch: bool):
    """Made on the viewer's own connection: a member's book has only the people and photos they see."""
    if catalog.person(c, person_id) is None:
        raise HTTPException(404, "No such person")
    return page(request, "book.html", nav="family", book=book.book(c, person_id, branch), person_id=person_id)


@router.get("/book/person/{person_id}", response_class=HTMLResponse)
def person_book(request: Request, person_id: int, c=Depends(con)):
    return _book(request, c, person_id, branch=False)


@router.get("/book/branch/{person_id}", response_class=HTMLResponse)
def branch_book(request: Request, person_id: int, c=Depends(con)):
    return _book(request, c, person_id, branch=True)
