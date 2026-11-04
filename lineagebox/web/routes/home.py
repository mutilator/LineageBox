"""The start page, and the faces someone in the family asked about."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from lineagebox import access, catalog
from lineagebox.web.common import MEMBER, _from_app
from lineagebox.web.deps import con, page, viewer, writer

router = APIRouter()
ACCESS = {
    "/home": MEMBER,
    "/api/faces/{face_id}/ask": MEMBER,
    "/api/faces/{face_id}/skip": MEMBER,
}


def home_page(request: Request, v: access.Viewer, c) -> HTMLResponse:
    """Home: faces someone asked about that the viewer may know, the newest photos of their family, and their own
    latest uploads."""
    recent = c.execute(f"""SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.media, i.duration FROM image i
                           WHERE {catalog.VISIBLE} AND (i.dup_group_id IS NULL OR i.dup_group_id = i.id)
                           ORDER BY i.id DESC LIMIT 24""").fetchall()
    mine = []
    if v.account_id is not None:
        folder = (v.account or {}).get("upload_folder") or "\0"
        mine = c.execute(f"""SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.media, i.duration FROM image i
                             WHERE {catalog.VISIBLE} AND (i.id IN (SELECT image_id FROM upload WHERE account_id = ?)
                                  OR i.id IN (SELECT image_id FROM file WHERE path LIKE ? ESCAPE '!'))
                             ORDER BY i.id DESC LIMIT 12""", (v.account_id, catalog.under(folder))).fetchall()
    you = c.execute("SELECT name FROM person WHERE id = ?", (v.me,)).fetchone()
    return page(request, "home.html", nav="home", recent=recent, mine=mine, questions=who_is_this(c, v),
                first_name=you[0].split()[0] if you else None)


def who_is_this(c, v: access.Viewer, limit: int = 12) -> list[dict]:
    """Faces someone asked about, still unnamed, in photos the viewer sees, that they haven't said they don't
    know."""
    rows = c.execute("""SELECT q.face_id, q.asked_at, f.image_id, i.sha256, p.name AS asker FROM face_question q
                        JOIN face f ON f.id = q.face_id JOIN image i ON i.id = f.image_id
                        LEFT JOIN account a ON a.id = q.account_id LEFT JOIN person p ON p.id = a.person_id
                        WHERE f.person_id IS NULL AND (f.assigned_by IS NULL OR f.assigned_by = 'rejected')
                          AND q.face_id NOT IN (SELECT face_id FROM face_question_skip WHERE account_id = ?)
                        ORDER BY q.asked_at DESC LIMIT ?""", (v.account_id or 0, limit)).fetchall()
    return [dict(r) for r in rows]


@router.get("/home", response_class=HTMLResponse)
def home_route(request: Request, v=Depends(viewer), c=Depends(con)):
    return home_page(request, v, c)


@router.post("/api/faces/{face_id}/ask", dependencies=[Depends(_from_app)])
def ask_who(face_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """Who is this? The face goes on Home for everyone who sees its photo, until someone names it."""
    f = catalog.face(c, face_id)
    if f is None:
        raise HTTPException(404, "No such face")
    if f["person_id"] is not None:
        raise HTTPException(400, "This face already has a name.")
    w.execute("INSERT OR IGNORE INTO face_question(face_id, account_id, asked_at) VALUES (?, ?, ?)",
              (face_id, v.account_id, datetime.now().isoformat(timespec="seconds")))
    w.commit()
    return {"message": "Asked: it is on Home for everyone who sees this photo."}


@router.post("/api/faces/{face_id}/skip", dependencies=[Depends(_from_app)])
def skip_who(face_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """Don't know them: not asked of this viewer again."""
    if catalog.face(c, face_id) is None:
        raise HTTPException(404, "No such face")
    if v.account_id is None:
        w.execute("DELETE FROM face_question WHERE face_id = ?", (face_id,))  # no accounts: nobody else to ask
    else:
        w.execute("INSERT OR IGNORE INTO face_question_skip(face_id, account_id) VALUES (?, ?)",
                  (face_id, v.account_id))
    w.commit()
    return {}
