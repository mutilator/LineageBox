"""The start page: memories, the newest photos by the day they were taken (for a member, those of their own family
first), what others shared, the faces someone in the family asked about, and (for admins) how full the disks are."""
import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from lineagebox import access, catalog, family, first_run, memories, outbox, storage, tours
from lineagebox.web.common import MEMBER, _from_app, _people_or_404, _shared_with_me, years_of
from lineagebox.web.deps import con, page, site, viewer, writer
from lineagebox.web.routes.tours import suggestions
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/home": MEMBER,
    "/home/memories": MEMBER,
    "/home/family": MEMBER,
    "/home/person/{person_id}": MEMBER,
    "/home/person/{person_id}/memories": MEMBER,
    "/home/person/{person_id}/years": MEMBER,
    "/api/faces/{face_id}/ask": MEMBER,
    "/api/faces/{face_id}/skip": MEMBER,
}


LATEST = 120  # photos on Home by the day they were taken: whole days, about this many...
LATEST_MOST = 240  # ...and never more, however many the newest day has
FAMILY = 48  # those of a member's own family, above them: whole days, about this many...
FAMILY_MOST = 96  # ...and never more
FAMILY_MORE = 60  # and this many more each time they ask for older ones
RECENT_DAYS = 7  # days named by their weekday (Yesterday, Tuesday); older ones are under their month


def home_page(request: Request, v: access.Viewer, c) -> HTMLResponse:
    """Home: memories (loaded after the page), faces someone asked about that the viewer may know, what others
    shared with them, the newest photos by the day they were taken, older ones added lately, and their own latest
    uploads. A member sees the newest of their own family first: a catalog is mostly the photos of whoever keeps it,
    whose children would fill the member's Home otherwise. An admin also sees how full the disks are."""
    s = site(request)
    today = date.today()
    kin = kin_of(v, c)
    ours, ours_older = latest(c, today, kin, FAMILY, FAMILY_MOST) if kin else ([], False)
    stream, older = latest(c, today, leave_out=[r["id"] for r in ours])
    shown = {r["id"] for r in stream} | {r["id"] for r in ours}
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
    me = own_person(v, c)
    buttons = faces_of(c, kin or closest(c, me))  # theirs first, then their family's: an admin's too
    family_of = [p for p in buttons if p["id"] != me]
    return page(request, "home.html", nav="home", days=by_day(stream, today), older=older, added=added, mine=mine,
                ours=by_day(ours, today), ours_older=ours_older, kin=[p["name"] for p in family_of], me=me,
                buttons=buttons, kin_words=_kin_words(c, me, [p["id"] for p in family_of]) if family_of else {},
                questions=who_is_this(c, v), first_name=you[0].split()[0] if you else None, shared=shared,
                disks=disks, first_run=first_run.checklist(c, s.live(c)) if v.admin and not v.seeing_as else None)


def faces_of(c, people: list[int]) -> list[dict]:
    """These people, in this order, with the face to show for each (their clearest named one), for Home's buttons."""
    rows = {r["id"]: dict(r) for r in c.execute(f"""
        SELECT p.id, p.name, (SELECT f.id FROM face f JOIN image i ON i.id = f.image_id
                              WHERE f.person_id = p.id AND {catalog.VISIBLE}
                              ORDER BY f.assigned_by = 'user' DESC, f.quality DESC LIMIT 1) AS face
        FROM person p WHERE p.id IN (SELECT value FROM json_each(?))""", (json.dumps(people),))}
    return [rows[p] for p in people if p in rows]


def _kin_words(c, me: int | None, people: list[int]) -> dict[int, str]:
    """person id -> what they are to the viewer ('mother', 'brother'), from the family tree."""
    if me is None:
        return {}
    kin = family.relations_to(family.load(c), me)
    return {p: kin[p].label for p in people if p in kin}


def own_person(v: access.Viewer, c) -> int | None:
    """Who the viewer is in the tree, whose own Home (/home/person) their You button on Home opens: the person their
    account is for, or with accounts off the one marked as "me" (This is me). None for an account that isn't anyone:
    the "me" setting is whoever keeps the catalog, not necessarily them."""
    return family.me(c) if v.account is None or v.me is not None else None


def closest(c, me: int | None) -> list[int]:
    """Whose buttons Home shows: this person first, then their immediate family (family.immediate); only them if
    they have none in the tree, nobody if me is None."""
    if me is None:
        return []
    g = family.load(c)
    return family.immediate(g, me) if me in g.people else [me]


def kin_of(v: access.Viewer, c) -> list[int]:
    """A member's immediate family, them first (family.immediate), whose photos and memories their Home starts with;
    none for an admin, or a member who isn't anyone in the tree."""
    if v.admin or v.me is None:
        return []
    g = family.load(c)
    return family.immediate(g, v.me) if v.me in g.people else []


def latest(c, today: date, people: list[int] | None = None, about: int = LATEST, most: int = LATEST_MOST,
           leave_out: list[int] = (), before: date | None = None) -> tuple[list, bool]:
    """The newest photos by the day they were taken (dated to the day, none after today, or before `before`), one of
    each group of similar shots, in whole days, however long ago; and whether there are older ones. people: only
    photos someone of them is named in (a face, or added to the photo); leave_out: photos shown already."""
    where = ""
    if people is not None:
        where += f" AND {memories.of_people()}"
    if leave_out:
        where += " AND i.id NOT IN (SELECT value FROM json_each(:leave_out))"
    rows = c.execute(f"""SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.media, i.duration, i.width, i.height
                         FROM image i
                         WHERE i.taken_at IS NOT NULL AND i.taken_at < :tomorrow AND {memories.PRECISE}
                           AND {catalog.VISIBLE} AND (i.dup_group_id IS NULL OR i.dup_group_id = i.id){where}
                         ORDER BY i.taken_at DESC, i.id DESC LIMIT :most""",
                     {"tomorrow": (before or today + timedelta(days=1)).isoformat(), "people": json.dumps(people or []),
                      "leave_out": json.dumps(list(leave_out)), "most": most + 1}).fetchall()
    out = []
    for r in rows[:most]:
        if len(out) >= about and r["taken_at"][:10] != out[-1]["taken_at"][:10]:
            break
        out.append(r)
    return out, len(rows) > len(out)


def by_day(rows, today: date, month: tuple[int, int] | None = None) -> list[dict]:
    """Photos in date order, newest first, by the day they were taken: each day's label (Yesterday, Tuesday, then
    'Sat, Oct 31'), a month's name before the first day of each month past the last week, and its photos with their
    shape (ratio: width over height), which the page sets them side by side by. month: (year, month) of the photos
    just above these, whose name is shown already."""
    out = []
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


@router.get("/home/family", response_class=HTMLResponse)
def home_family(request: Request, before: date, month: str = "", person: int | None = None, v=Depends(viewer),
                c=Depends(con)):
    """The next older photos of a member's family on Home (Show older photos), back to the first; or of one person
    (their Home, /home/person)."""
    if person is not None:
        _people_or_404(c, person)
    kin = [person] if person is not None else kin_of(v, c)
    ours, older = latest(c, date.today(), kin, FAMILY_MORE, FAMILY_MOST, before=before) if kin else ([], False)
    shown = tuple(int(x) for x in month.split("-")[:2]) if month[:4].isdecimal() and month[5:7].isdecimal() else None
    return page(request, "_home_days.html", part=True, days=by_day(ours, date.today(), shown), older=older,
                person=person)


@router.get("/home/person/{person_id}", response_class=HTMLResponse)
def person_home(request: Request, person_id: int, v=Depends(viewer), c=Depends(con)):
    """Home as it would be of one person (a button on Home, for the viewer and each of their closest family): their
    memories and their newest photos, however long ago, then older ones."""
    _people_or_404(c, person_id)
    today = date.today()
    theirs, older = latest(c, today, [person_id], FAMILY, FAMILY_MOST)
    who, me = faces_of(c, [person_id])[0], own_person(v, c)
    return page(request, "home_person.html", nav="home", who=who, days=by_day(theirs, today), older=older,
                kin_word=_kin_words(c, me, [person_id]).get(person_id), you=person_id == me)


@router.get("/home/person/{person_id}/memories", response_class=HTMLResponse)
def person_memories(request: Request, person_id: int, v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    """That person's memories, loaded after the page: their biggest days of photos and their latest, and this week in
    earlier years in their photos; kept 10 minutes for each viewer."""
    _people_or_404(c, person_id)
    today = date.today()

    def work():
        blurry = s.live(c).blurry_below
        return memories.row(memories.years_ago(c, today, blurry, [person_id]), [], [],
                            memories.family_occasions(c, [person_id], today, blurry))
    return page(request, "_memories.html", memories=s.keep(v, ("memories", person_id, today.isoformat()), 600, work))


@router.get("/home/person/{person_id}/years", response_class=HTMLResponse)
def person_years_panel(request: Request, person_id: int, v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    """Through the years as a panel on their Home, loaded after the page: their face a year, playing on its own, which
    opens the whole of it (/person/{id}/years). Nothing for someone with fewer than two years to show."""
    _people_or_404(c, person_id)
    found = years_of(s, v, c, person_id)
    return page(request, "_years_panel.html", who=catalog.person(c, person_id), years=found if len(found) > 1 else [])


@router.get("/home/memories", response_class=HTMLResponse)
def home_memories(request: Request, v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    """Home's row of memories, loaded after the page: picking their photos takes a moment on a big catalog, so the
    row is kept 10 minutes for each viewer (and worked out again after a change). A member's starts with their own
    family's days of photos, and this week in earlier years is of them; suggestions of only them come first."""
    today = date.today()

    def work():
        blurry = s.live(c).blurry_below
        saved = [t for t in tours.tours(c) if v.admin or t["account_id"] == v.account_id]
        kin = kin_of(v, c)
        found = suggestions(s, v, c)
        if kin:
            ours = set(kin)
            found = sorted(found, key=lambda x: 0 if {*x["spec"]["who"]["person_ids"]} <= ours and
                           x["spec"]["who"]["person_ids"] else 2 if x["spec"]["who"]["person_ids"] else 1)
        return memories.row(memories.years_ago(c, today, blurry, kin or None), memories.suggested(found, today),
                            memories.own_tours(c, saved, today, blurry),
                            memories.family_occasions(c, kin, today, blurry) if kin else [])
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
    asked = w.execute("INSERT OR IGNORE INTO face_question(face_id, account_id, asked_at) VALUES (?, ?, ?)",
                      (face_id, v.account_id, datetime.now().isoformat(timespec="seconds"))).rowcount
    if asked:  # and on New for you, for everyone who sees the photo (outbox.py)
        outbox.asked(w, face_id, v.account_id)
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
