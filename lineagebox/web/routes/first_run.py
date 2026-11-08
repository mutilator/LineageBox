"""The first steps on a new LineageBox (first_run.py), on an admin's Home until they are done: putting the list away
for good. Home and the empty start page work the list out themselves (home.py, browse.py)."""
from fastapi import APIRouter, Depends

from lineagebox import first_run
from lineagebox.web.common import ADMIN, _from_app
from lineagebox.web.deps import con

router = APIRouter()
ACCESS = {
    "/api/first-run/hide": ADMIN,
}


@router.post("/api/first-run/hide", dependencies=[Depends(_from_app)])
def hide(c=Depends(con)):
    """Put away for good, for every admin: the steps are what any LineageBox needs once, not one person's to-do."""
    first_run.hide(c)
    return {"message": "Put away. Settings has everything it pointed to."}
