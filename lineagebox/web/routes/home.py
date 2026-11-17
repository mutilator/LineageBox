"""The start page: memories, the newest photos by the day they were taken (for a member, those of their own family
first), what others shared, the faces someone in the family asked about, and (for admins) how full the disks are."""
import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from lineagebox import access, catalog, clock, family, first_run, memories, outbox, storage, tours
from lineagebox.web.common import MEMBER, from_app, people_or_404, shared_with_me, years_of
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
MORE_MOST = 96  # of one person's photos whose date isn't certain, this many under their days
RECENT_DAYS = 7  # days named by their weekday (Yesterday, Tuesday); older ones are under their month
FORMERS = "formers"  # whether Home's family holds the partners someone once had: this browser's choice, in a cookie


def formers_shown(request: Request) -> bool:
    """Whether the partners someone once had are among Home's family: what was just chosen there (?formers=0 or 1),
    else what this browser chose before, else yes. An ex-wife of twenty years is in as many photos as anyone."""
    chosen = request.query_params.get(FORMERS)
    if chosen in ("0", "1"):
        return chosen == "1"
    return request.cookies.get(FORMERS) != "0"


def _remember_formers(request: Request, response: HTMLResponse, shown: bool) -> HTMLResponse:
    """Keep that choice for this browser, when it was just made."""
    if request.query_params.get(FORMERS) in ("0", "1"):
        response.set_cookie(FORMERS, "1" if shown else "0", max_age=365 * 86400, samesite="lax",
                            secure=request.url.scheme == "https")
    return response


def home_page(request: Request, v: access.Viewer, c) -> HTMLResponse:
    """Home: memories (loaded after the page), faces someone asked about that the viewer may know, what others
    shared with them, the newest photos by the day they were taken, older ones added lately, and their own latest
    uploads. A member sees the newest of their own family first: a catalog is mostly the photos of whoever keeps it,
    whose children would fill the member's Home otherwise. An admin also sees how full the disks are."""
    s = site(request)
    today = date.today()
    formers = formers_shown(request)
    kin = kin_of(v, c, formers)
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
        shared = shared_with_me(w, c, v)
    disks = s.keep(v, ("storage",), 60, lambda: storage.disks(c, s.live(c))) if v.admin else []
    me = own_person(v, c)
    buttons = faces_of(c, kin or closest(c, me, formers))  # theirs first, then their family's: an admin's too
    family_of = [p for p in buttons if p["id"] != me]
    had = me is not None and c.execute("SELECT 1 FROM partner WHERE NOT current AND ? IN (a, b) LIMIT 1",
                                       (me,)).fetchone() is not None
    shown = page(request, "home.html", nav="home", days=by_day(stream, today), older=older, added=added, mine=mine,
                 ours=by_day(ours, today), ours_older=ours_older, kin=[p["name"] for p in family_of], me=me,
                 buttons=buttons, kin_words=_kin_words(c, me, [p["id"] for p in family_of]) if family_of else {},
                 questions=who_is_this(c, v), first_name=you[0].split()[0] if you else None, shared=shared,
                 formers=formers, had_formers=had,
                 disks=disks, first_run=first_run.checklist(c, s.live(c)) if v.admin and not v.seeing_as else None)
    return _remember_formers(request, shown, formers)


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


def closest(c, me: int | None, formers: bool = True) -> list[int]:
    """Whose buttons Home shows: this person first, then their immediate family (family.immediate, the partners they
    once had among them unless `formers` is False); only them if they have none in the tree, nobody if me is None."""
    if me is None:
        return []
    g = family.load(c)
    return family.immediate(g, me, formers) if me in g.people else [me]


def kin_of(v: access.Viewer, c, formers: bool = True) -> list[int]:
    """A member's immediate family, them first (family.immediate), whose photos and memories their Home starts with;
    none for an admin, or a member who isn't anyone in the tree."""
    if v.admin or v.me is None:
        return []
    g = family.load(c)
    return family.immediate(g, v.me, formers) if v.me in g.people else []


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


def not_by_day(c, person_id: int, today: date, most: int = MORE_MOST) -> tuple[list, int]:
    """One person's photos that the days above them can't hold, newest first by whatever date they have, and how many
    there are in all. The days keep to photos dated to the day (memories.PRECISE): a date from when the file was
    copied, or from a folder's or file's name, would put an old photo under a recent day. Those photos are still
    theirs, though, and someone whose photos are all like that had nothing on their Home at all before this."""
    where = (f"{memories.of_people()} AND {catalog.VISIBLE} AND (i.dup_group_id IS NULL OR i.dup_group_id = i.id) "
             f"AND NOT (i.taken_at IS NOT NULL AND i.taken_at < :tomorrow AND {memories.PRECISE})")
    args = {"people": json.dumps([person_id]), "tomorrow": (today + timedelta(days=1)).isoformat(), "most": most}
    rows = c.execute(f"""SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.media, i.duration, i.width, i.height
                         FROM image i WHERE {where}
                         ORDER BY i.taken_at IS NULL, i.taken_at DESC, i.id DESC LIMIT :most""", args).fetchall()
    return rows, c.execute(f"SELECT count(*) FROM image i WHERE {where}", args).fetchone()[0]


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
        people_or_404(c, person)
    kin = [person] if person is not None else kin_of(v, c, formers_shown(request))
    ours, older = latest(c, date.today(), kin, FAMILY_MORE, FAMILY_MOST, before=before) if kin else ([], False)
    shown = tuple(int(x) for x in month.split("-")[:2]) if month[:4].isdecimal() and month[5:7].isdecimal() else None
    return page(request, "_home_days.html", part=True, days=by_day(ours, date.today(), shown), older=older,
                person=person)


@router.get("/home/person/{person_id}", response_class=HTMLResponse)
def person_home(request: Request, person_id: int, v=Depends(viewer), c=Depends(con)):
    """Home as it would be of one person (a button on Home, for the viewer and each of their closest family): their
    memories and their newest photos, however long ago, then older ones."""
    people_or_404(c, person_id)
    today = date.today()
    theirs, older = latest(c, today, [person_id], FAMILY, FAMILY_MOST)
    more, more_all = not_by_day(c, person_id, today)
    who, me = faces_of(c, [person_id])[0], own_person(v, c)
    return page(request, "home_person.html", nav="home", who=who, days=by_day(theirs, today), older=older,
                more=more, more_all=more_all,
                kin_word=_kin_words(c, me, [person_id]).get(person_id), you=person_id == me)


@router.get("/home/person/{person_id}/memories", response_class=HTMLResponse)
def person_memories(request: Request, person_id: int, v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    """That person's memories, loaded after the page: their biggest days of photos and their latest, and this week in
    earlier years in their photos; kept 10 minutes for each viewer."""
    people_or_404(c, person_id)
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
    people_or_404(c, person_id)
    found = years_of(s, v, c, person_id)
    return page(request, "_years_panel.html", who=catalog.person(c, person_id), years=found if len(found) > 1 else [])


REMAKE_AFTER = 3600  # seconds: for new photos alone (a library being read in), new memories at most this often


@router.get("/home/memories", response_class=HTMLResponse)
def home_memories(request: Request, v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    """Home's row of memories, loaded after the page (kept_memories)."""
    return page(request, "_memories.html", memories=kept_memories(s, v, c, formers_shown(request)))


def kept_memories(s: Site, v: access.Viewer, c, formers: bool) -> list[dict]:
    """The viewer's memories: those kept for them (memories.kept), until Settings → Browsing says new ones are due
    or they see new photos (at most every REMAKE_AFTER). Then new ones are made on the thread for what can wait
    (Site.later) and the kept ones are shown meanwhile: the next Home has the new ones, and nobody waits. They are
    made while the viewer waits only the first time, when they see fewer photos than before (a photo hidden, a share
    ended), or when they are someone else in the tree, so that nothing they no longer see stays on their Home."""
    seen = seen_photos(v, c)
    found = memories.kept(c, v.account_id, formers)
    if found is None or seen[0] < found["photos"][0] or seen[2] != found["photos"][2]:
        cards = memory_cards(s, v, c, date.today(), formers)
        with s.own_con() as w:
            memories.keep(w, v.account_id, formers, clock.now(), seen, cards)
        return cards
    now, cfg = clock.now_datetime(), s.live(c)
    if (memories.due(found["made_at"], now, cfg.memories_keep, cfg.memories_unit)
            or seen != found["photos"] and (now - found["made_at"]).total_seconds() >= REMAKE_AFTER):
        s.later(("memories", v.account_id, formers), lambda: _remake(s, v, formers))
    covers = {r[0] for r in c.execute(f"""SELECT i.sha256 FROM image i WHERE {catalog.VISIBLE}
                                          AND i.sha256 IN (SELECT value FROM json_each(?))""",
                                      (json.dumps([x["cover"] for x in found["cards"]]),))}
    return [x for x in found["cards"] if x["cover"] in covers]  # a photo gone since: its memory waits for the new ones


def _remake(s: Site, v: access.Viewer, formers: bool) -> None:
    """New memories for the viewer, made after the page that asked for them (Site.later), on a connection of their
    own: a member's sees only what they see (memories of what is theirs, not derived data for everyone)."""
    with s.own_con() as c:
        access.scope(c, v)
        seen, cards = seen_photos(v, c), memory_cards(s, v, c, date.today(), formers)
    with s.own_con() as w:
        memories.keep(w, v.account_id, formers, clock.now(), seen, cards)


def seen_photos(v: access.Viewer, c) -> list[int]:
    """[how many photos, the newest one's id, who they are in the tree]: what the viewer's memories are made from,
    which tells kept_memories when photos were added or taken away. A member's from what they see; an admin's from
    every photo, as cheaply as can be (a photo that went is left out when its memory is shown)."""
    if v.limited:
        return [len(v.images), max(v.images, default=0), v.me or 0]
    return [*c.execute("SELECT count(*), coalesce(max(id), 0) FROM main.image").fetchone(), v.me or 0]


def memory_cards(s: Site, v: access.Viewer, c, today: date, formers: bool) -> list[dict]:
    """Home's row of memories, worked out: seconds on a big library. Whoever is someone in the tree, admin or member,
    gets their own family's days of photos first, and this week in earlier years of them; suggestions of only them
    come first. Their own tours join it (an admin's: theirs, and those from before accounts)."""
    blurry = s.live(c).blurry_below
    saved = [t for t in tours.tours(c) if v.account is None or t["account_id"] == v.account_id
             or (v.admin and t["account_id"] is None)]
    kin = memory_kin(v, c, formers)
    found = suggestions(s, v, c)
    if kin:
        ours = set(kin)
        found = sorted(found, key=lambda x: 0 if {*x["spec"]["who"]["person_ids"]} <= ours and
                       x["spec"]["who"]["person_ids"] else 2 if x["spec"]["who"]["person_ids"] else 1)
    return memories.row(memories.years_ago(c, today, blurry, kin or None), memories.suggested(found, today),
                        memories.own_tours(c, saved, today, blurry),
                        memories.family_occasions(c, kin, today, blurry) if kin else [])


def memory_kin(v: access.Viewer, c, formers: bool = True) -> list[int]:
    """Whose memories come first: the viewer's immediate family, them first, admin or member (an admin's Home
    doesn't put their family's photos first, kin_of, but their memories are theirs: the user's choice, 2026-11-17).
    None for an account that isn't anyone in the tree, nor without family accounts (the house's memories)."""
    if v.me is None:
        return []
    g = family.load(c)
    return family.immediate(g, v.me, formers) if v.me in g.people else []


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


@router.post("/api/faces/{face_id}/ask", dependencies=[Depends(from_app)])
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


@router.post("/api/faces/{face_id}/skip", dependencies=[Depends(from_app)])
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
