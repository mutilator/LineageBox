"""The start page: memories, the newest photos by the day they were taken, what others shared, the faces someone in
the family asked about, and (for admins) how full the disks are."""
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from lineagebox import access, catalog, memories, storage, tours
from lineagebox.web.common import MEMBER, _from_app, _shared_with_me
from lineagebox.web.deps import con, page, site, viewer, writer
from lineagebox.web.routes.tours import suggestions
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/home": MEMBER,
    "/home/memories": MEMBER,
    "/api/faces/{face_id}/ask": MEMBER,
    "/api/faces/{face_id}/skip": MEMBER,
}


LATEST = 120  # photos on Home by the day they were taken: whole days, about this many...
LATEST_MOST = 240  # ...and never more, however many the newest day has
RECENT_DAYS = 7  # days named by their weekday (Yesterday, Tuesday); older ones are under their month


def home_page(request: Request, v: access.Viewer, c) -> HTMLResponse:
    """Home: memories (loaded after the page), faces someone asked about that the viewer may know, what others
    shared with them, the newest photos by the day they were taken, older ones added lately, and their own latest
    uploads. An admin also sees how full the disks are."""
    s = site(request)
    today = date.today()
    stream, older = latest(c, today)
    shown = {r["id"] for r in stream}
    recent = c.execute(f"""SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.media, i.duration FROM image i
                           WHERE {catalog.VISIBLE} AND (i.dup_group_id IS NULL OR i.dup_group_id = i.id)
                           ORDER BY i.id DESC LIMIT 48""").fetchall()
    added = [r for r in recent if r["id"] not in shown][:12]  # taken longer ago (scans, old folders), or undated
    mine = []
    if v.account_id is not None:
        folder = (v.account or {}).get("upload_folder") or "\0"
        mine = c.execute(f"""SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.media, i.duration FROM image i
                             WHERE {catalog.VISIBLE} AND (i.id IN (SELECT image_id FROM upload WHERE account_id = ?)
                                  OR i.id IN (SELECT image_id FROM file WHERE path LIKE ? ESCAPE '!'))
                             ORDER BY i.id DESC LIMIT 12""", (v.account_id, catalog.under(folder))).fetchall()
    you = c.execute("SELECT name FROM person WHERE id = ?", (v.me,)).fetchone()
    with site(request).own_con() as w:
        shared = _shared_with_me(w, c, v)
    disks = s.keep(v, ("storage",), 60, lambda: storage.disks(c, s.live(c))) if v.admin else []
    return page(request, "home.html", nav="home", days=by_day(stream, today), older=older, added=added, mine=mine,
                questions=who_is_this(c, v), first_name=you[0].split()[0] if you else None, shared=shared,
                disks=disks)


def latest(c, today: date) -> tuple[list, bool]:
    """The newest photos by the day they were taken (dated to the day, none after today), one of each group of
    similar shots, in whole days; and whether there are older ones."""
    rows = c.execute(f"""SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.media, i.duration, i.width, i.height
                         FROM image i
                         WHERE i.taken_at IS NOT NULL AND i.taken_at < ? AND {memories.PRECISE}
                           AND {catalog.VISIBLE} AND (i.dup_group_id IS NULL OR i.dup_group_id = i.id)
                         ORDER BY i.taken_at DESC, i.id DESC LIMIT ?""",
                     ((today + timedelta(days=1)).isoformat(), LATEST_MOST + 1)).fetchall()
    out = []
    for r in rows[:LATEST_MOST]:
        if len(out) >= LATEST and r["taken_at"][:10] != out[-1]["taken_at"][:10]:
            break
        out.append(r)
    return out, len(rows) > len(out)


def by_day(rows, today: date) -> list[dict]:
    """Photos in date order, newest first, by the day they were taken: each day's label (Yesterday, Tuesday, then
    'Sat, Oct 31'), a month's name before the first day of each month past the last week, and its photos with their
    shape (ratio: width over height), which the page sets them side by side by."""
    out, month = [], None
    for r in rows:
        try:
            day = date.fromisoformat(r["taken_at"][:10])
        except ValueError:
            continue
        if not out or out[-1]["day"] != day:
            ago, heading = (today - day).days, None
            if ago < RECENT_DAYS:
                label = "Today" if ago == 0 else "Yesterday" if ago == 1 else f"{day:%A}"
            else:
                label = f"{day:%a}, {day:%b} {day.day}" + ("" if day.year == today.year else f", {day.year}")
                if (day.year, day.month) != month:
                    month = (day.year, day.month)
                    heading = f"{day:%B}" + ("" if day.year == today.year else f" {day.year}")
            out.append({"day": day, "label": label, "month": heading, "photos": []})
        w, h = r["width"], r["height"]
        ratio = round(min(2.5, max(0.4, w / h)), 3) if w and h else 1.0  # panoramas and slivers kept in bounds
        out[-1]["photos"].append({**dict(r), "ratio": ratio})
    return out


@router.get("/home/memories", response_class=HTMLResponse)
def home_memories(request: Request, v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    """Home's row of memories, loaded after the page: picking their photos takes a moment on a big catalog, so the
    row is kept 10 minutes for each viewer (and worked out again after a change)."""
    today = date.today()

    def work():
        blurry = s.live(c).blurry_below
        saved = [t for t in tours.tours(c) if v.admin or t["account_id"] == v.account_id]
        return memories.row(memories.years_ago(c, today, blurry), memories.suggested(suggestions(s, v, c), today),
                            memories.own_tours(c, saved, today, blurry))
    return page(request, "_memories.html", memories=s.keep(v, ("memories", today.isoformat()), 600, work))


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
