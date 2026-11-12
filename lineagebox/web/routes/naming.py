"""People → Name faces: one face, or one group of faces, at a time, with who it most likely is to say yes or no to, or
a box to type who it is; app.js brings the next card after each answer without loading the page again (see _next for
the order). And the answers about one face that this page, Check faces and any other page asking about a face (one
tapped on its photo) use:

- GET /api/faces/{id}/who: what to ask about the face, as JSON: where it is on its photo, its name now (and whom a
  recognised face looks more like now, if anyone), who it most likely is, and whether someone asked who it is. GET
  /people/name/face/{id}?one=1: the same as a card to show.
- POST /api/faces/{id}/yes {"person_id"}: it is them, named by hand. POST /api/faces/{id}/no {"person_id", "also"}:
  it isn't, nor any of also (the name comes off if it was theirs; never suggested or recognised as them again); /who
  then says who it may be instead.
- Who it is, typed: POST /api/faces/{id}/name {"name", "name_id"} (routes/faces.py). Don't know: POST
  /api/faces/{id}/skip (routes/home.py), never asked of this account again.
- POST /api/people/{id}/yes {"yes": [face ids]}: Check faces' ticked faces are them.
- POST /api/faces/sure-yes {"step", "at_least"}: yes to every face of the queue's step (recognised, or looks like
  someone) that is at least so sure (people.how_sure), as one change to undo.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from lineagebox import access, catalog, changes, errors, people
from lineagebox.catalog import VISIBLE
from lineagebox.folders import FolderIndex
from lineagebox.web.common import MEMBER, _by, _face_to_change, _faces_spec, _from_app, _people_or_404
from lineagebox.web.deps import con, page, site, viewer, writer
from lineagebox.web.routes import face_checks
from lineagebox.web.routes.browse import COUNTS_FOR, _faces_to_confirm
from lineagebox.web.routes.home import who_is_this
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/people/name": MEMBER,
    "/people/name/next": MEMBER,
    "/people/name/face/{face_id}": MEMBER,
    "/api/faces/{face_id}/who": MEMBER,
    "/api/faces/{face_id}/yes": MEMBER,
    "/api/faces/{face_id}/no": MEMBER,
    "/api/people/{person_id}/yes": MEMBER,
    "/api/faces/sure-yes": MEMBER,
}

QUEUE_FOR = 300  # seconds the faces recognised and the groups are kept for each viewer (_kept)
STEPS = {"asked": "Asked about", "suggested": "Looks like someone", "recognised": "Recognised, to check",
         "groups": "Groups to name"}
MOST_PASSED = 2000  # faces, and groups, a page passes over at most (Skip): the oldest are asked again
GROUP_SHOWN = 12  # faces of a group on its card
SURE_STEPS = ("suggested", "recognised")  # the steps whose faces can be said yes to at once, the surest


class Problem(errors.Problem):
    """An answer about a face that someone else's answer, given since the card was shown, outranks."""
    status = 409


class PersonBody(BaseModel):
    person_id: int


class NoBody(PersonBody):
    also: list[int] = Field(default_factory=list, max_length=5)  # nor any of these (Neither)


class YesBody(BaseModel):
    yes: list[int] = Field(min_length=1, max_length=1000)


class SureBody(BaseModel):
    step: str
    at_least: int = Field(ge=50, le=100)  # percent


def _ids(text: str) -> list[int]:
    """'12,40,7' -> [12, 40, 7]: what a page passed over, as it sends it (anything else in it is left out)."""
    return [int(x) for x in text.split(",") if x.strip().isdecimal()][-MOST_PASSED:]


def _state(only: str, skip: str, skip_groups: str) -> dict:
    """Where a page is in the queue: one step of it only (or all), and the faces and groups it passed over."""
    return {"only": only if only in STEPS else "", "skip": _ids(skip), "skip_groups": _ids(skip_groups)}


def _kept(s: Site, v: access.Viewer, c) -> dict:
    """The faces recognised automatically and the groups, in the queue's order. Listing them reads every face, so they
    are kept QUEUE_FOR seconds for each viewer, through the changes the answers make, and what was answered since is
    taken out of them as the queue is walked (_walk)."""
    return s.keep(v, ("name faces",), QUEUE_FOR, lambda: {"recognised": people.recognised_to_check(c),
                                                           "groups": people.unnamed_groups(c)}, through_changes=True)


def _walk(kept: dict, key: str, still, passed: set[int], key_of) -> tuple[object | None, int]:
    """The first of kept[key] that is still waiting (still(some) gives those of them that are) and wasn't passed over,
    checking a few at first and more each time after; those answered since are left out of the kept list. And how many
    are left, not passed over: those not checked yet count as waiting."""
    items, start, window, found = kept[key], 0, 40, None
    while found is None and start < len(items):
        chunk = items[start:start + window]
        alive = still(chunk)
        waiting = [x for x in chunk if x in alive]
        items = items[:start] + waiting + items[start + window:]
        found = next((x for x in waiting if key_of(x) not in passed), None)
        start, window = start + len(waiting), window * 4
    kept[key] = items
    return found, sum(key_of(x) not in passed for x in items)


def _still_recognised(c, chunk) -> set:
    return {(r[0], r[1]) for r in c.execute("""SELECT id, person_id FROM face WHERE assigned_by = 'auto'
                                               AND id IN (SELECT value FROM json_each(?))""",
                                            (json.dumps([f for f, _ in chunk]),))}


def _still_grouped(c, chunk) -> set:
    return {r[0] for r in c.execute("""SELECT DISTINCT cluster_id FROM face WHERE person_id IS NULL
                                       AND cluster_id IN (SELECT value FROM json_each(?))""", (json.dumps(chunk),))}


def _next(s: Site, v: access.Viewer, c, w, state: dict, every_count: bool = False) -> tuple[dict | None, dict]:
    """The next card of the queue, or of one step of it (state["only"]), leaving out what the page passed over and the
    faces the viewer said they don't know; and how many each step has left (every step's for every_count, else those
    up to the card's). The order:
    1. faces someone in the family asked about, the newest question first: someone is waiting for the answer;
    2. faces beside people named in their photo that look like someone else named (people.together, as Cleanup →
       Confirm faces): right 99.7% of the time at its bar, one tap each, and the face then counts for that person
       (their page, search, tours, and who sees the photo); person by person, the largest first, the likeliest face
       first;
    3. faces recognised automatically, the least certain first (people.recognised_to_check);
    4. the groups of faces nobody has named, the largest first: a name there names many faces at once, but it takes
       someone who knows who they are, and many groups are strangers. One whose faces together look like someone
       named (people.looks_like) asks whether they are them."""
    passed = set(state["skip"]) | {r[0] for r in c.execute(
        "SELECT face_id FROM face_question_skip WHERE account_id = ?", (v.account_id or 0,))}
    passed_groups, only = set(state["skip_groups"]), state["only"]

    def asked():
        waiting = [q for q in who_is_this(c, v, 1000) if q["face_id"] not in passed]
        return (waiting[0] if waiting else None), len(waiting)

    def suggested():
        groups, _ = _faces_to_confirm(s, v, c)
        waiting = [(f["id"], g["person_id"], f["score"], f.get("sure")) for g in groups for f in g["faces"]
                   if f["id"] not in passed]
        return (waiting[0] if waiting else None), len(waiting)

    def recognised():
        return _walk(_kept(s, v, c), "recognised", lambda chunk: _still_recognised(c, chunk), passed, lambda x: x[0])

    def groups():
        return _walk(_kept(s, v, c), "groups", lambda chunk: _still_grouped(c, chunk), passed_groups, lambda x: x)

    for _ in range(5):  # again only when what was found went meanwhile (named that moment, on another page)
        counts, found = {}, None
        for step, work in (("asked", asked), ("suggested", suggested), ("recognised", recognised), ("groups", groups)):
            if not every_count and (found is not None or only not in ("", step)):
                continue
            first, counts[step] = work()
            if found is None and first is not None and only in ("", step):
                found = (step, first)
        if found is None:
            return None, counts
        step, first = found
        if step == "groups":
            card = _group_card(s, v, c, first, w)
        elif step == "asked":
            card = _face_card(v, c, w, first["face_id"])
        elif step == "suggested":
            card = _face_card(v, c, w, first[0], suggest=first[1:])
        else:
            card = _face_card(v, c, w, first[0])
        if card is not None:
            card["step"] = step
            return card, counts
        if step == "groups":
            passed_groups.add(first)
        else:
            passed.add(first["face_id"] if step == "asked" else first[0])
    return None, counts


def _where(c, f) -> dict:
    """A face's place on its photo, and the picture to show it on: the photo's preview, or the video frame it is on."""
    img = c.execute("SELECT sha256, width, height, taken_at, media FROM image WHERE id = ?",
                    (f["image_id"],)).fetchone()
    frame = f["frame"]
    return {"face": {"id": f["id"], "image_id": f["image_id"], "box": [f["x1"], f["y1"], f["x2"], f["y2"]],
                     "frame": frame},
            "photo": {"src": f"/frame/{img['sha256']}/{frame}" if frame is not None else f"/p/{img['sha256']}",
                      "thumb": None if frame is not None else f"/t/{img['sha256']}",
                      "width": None if frame is not None else img["width"],
                      "height": None if frame is not None else img["height"],
                      "taken_at": img["taken_at"], "video": img["media"] == "video"}}


def _who(c, pid: int, face_id: int) -> dict | None:
    """Someone the viewer sees, with the face to show for them (their clearest named by hand, not this face)."""
    row = catalog.person(c, pid)
    if row is None:
        return None
    cover = c.execute(f"""SELECT f.id FROM face f JOIN image i ON i.id = f.image_id
                          WHERE f.person_id = ? AND f.id != ? AND {VISIBLE}
                          ORDER BY f.assigned_by = 'user' DESC, f.quality DESC LIMIT 1""", (pid, face_id)).fetchone()
    return {"id": pid, "name": row["name"], "face": cover[0] if cover else None}


def _face_card(v: access.Viewer, c, w, face_id: int, suggest: tuple | None = None) -> dict | None:
    """What to ask about one face the viewer sees (None if they don't): how it is named now ("how": "recognised"
    automatically or "named" by hand, as "person"), or who it most likely is ("suggested", worked out against
    everyone, as on its photo's page, and shown only if the viewer sees them; suggest: (person, score, how sure)
    worked out already), else nobody ("open": who is it?). "outside": named as someone outside a member's part of the
    family, whom only an admin can change. "sure": how sure a recognised or suggested name is (people.how_sure).
    "more_like": someone a recognised face looks more like than its name (people.looks_more_like), with how sure, whom
    the card asks about instead. Also whether someone asked who it is, and who else is named in the photo."""
    f = catalog.face(c, face_id)
    if f is None:
        return None
    card = {"kind": "face", **_where(c, f), "person": None, "score": None, "sure": None, "more_like": None,
            "asked": None, "with": []}
    if f["person_id"] is not None:
        card["person"] = _who(c, f["person_id"], face_id)
        card["how"] = "outside" if card["person"] is None else "recognised" if f["assigned_by"] == "auto" else "named"
        if card["how"] == "recognised":
            card["sure"] = people.how_sure(f["assign_score"], f["assign_lead"])
            if (f["assign_lead"] or 0) < 0 and (more := people.looks_more_like(w, face_id)) \
                    and (other := _who(c, more[0], face_id)):
                card["more_like"] = {**other, "score": more[1], "sure": more[2]}
    else:
        who = _who(c, suggest[0], face_id) if suggest else None
        if who is None and f["assigned_by"] != "not_a_face":
            found = people.suggest_for_face(w, face_id)
            if found and v.sees_person(found[0]):
                who, suggest = _who(c, found[0], face_id), found
        card.update(person=who, score=suggest[1] if who else None, sure=suggest[2] if who else None,
                    how="suggested" if who else "open")
    q = c.execute("""SELECT q.asked_at, p.name AS asker FROM face_question q LEFT JOIN account a ON a.id = q.account_id
                     LEFT JOIN person p ON p.id = a.person_id WHERE q.face_id = ?""", (face_id,)).fetchone()
    card["asked"] = dict(q) if q else None
    them = card["person"]["id"] if card["person"] else None
    card["with"] = [n for pid, n in c.execute("""
        SELECT p.id, p.name FROM face o JOIN person p ON p.id = o.person_id
        WHERE o.image_id = :i AND o.id != :f AND o.assigned_by IN ('user', 'auto')
        UNION SELECT p.id, p.name FROM image_person l JOIN person p ON p.id = l.person_id WHERE l.image_id = :i
        ORDER BY 2""", {"i": f["image_id"], "f": face_id}) if pid != them]
    return card


def _group_card(s: Site, v: access.Viewer, c, cluster_id: int, w=None) -> dict | None:
    """A group of faces nobody has named: its clearest faces, how many it has in how many photos, the clearest one on
    its photo, who its faces look like (people.looks_like, worked out on w against everyone) and the name the folders
    its photos are in suggest (as Cleanup → Confirm faces)."""
    faces = people.group_faces(c, cluster_id)
    if not faces:
        return None
    images = list(dict.fromkeys(f["image_id"] for f in faces))
    # names of people in photos already, which the folders' suggestion skips: counting everyone's photos takes a
    # moment, and each answer would have them counted again for the next card
    taken = s.keep(v, ("names in photos",), COUNTS_FOR, lambda: {p["name"].lower() for p in catalog.people(c)},
                   through_changes=True)
    return {"kind": "group", "cluster_id": cluster_id, **_where(c, catalog.face(c, faces[0]["id"])),
            "faces": [dict(f) for f in faces[:GROUP_SHOWN]], "size": len(faces), "photos": len(images),
            "suggestion": people.suggest_name(FolderIndex(c), images, taken),
            "looks_like": face_checks.looks_like(v, c, w, [cluster_id]).get(cluster_id) if w is not None else None}


def _sure_enough(s: Site, v: access.Viewer, c, step: str, bar: float) -> dict[int, list[int]]:
    """The faces of a step of the queue (SURE_STEPS) at least bar sure (people.how_sure), by person, but for those the
    viewer said they don't know."""
    if step == "recognised":
        found = people.recognised_sure(c, bar)
    else:
        groups, _ = _faces_to_confirm(s, v, c)
        found = {g["person_id"]: [f["id"] for f in g["faces"] if (f.get("sure") or 0) >= bar - 1e-9] for g in groups}
    unknown = {r[0] for r in c.execute("SELECT face_id FROM face_question_skip WHERE account_id = ?",
                                       (v.account_id or 0,))}
    found = {pid: [f for f in faces if f not in unknown] for pid, faces in found.items()}
    return {pid: faces for pid, faces in found.items() if faces}


@router.get("/people/name", response_class=HTMLResponse)
def name_faces_page(request: Request, only: str = "", skip: str = "", skip_groups: str = "", sure: int = 98,
                    v=Depends(viewer), c=Depends(con), w=Depends(writer), s: Site = Depends(site)):
    """One face at a time, with how many each step of the queue has left above it. On a step whose names can be sure
    (SURE_STEPS), how many of its faces are at least each of people.SURE_BARS sure, to say yes to at once: sure, the
    bar picked (percent)."""
    state = _state(only, skip, skip_groups)
    card, counts = _next(s, v, c, w, state, every_count=True)
    surest = None
    if state["only"] in SURE_STEPS:
        bars = [(round(bar * 100), sum(map(len, _sure_enough(s, v, c, state["only"], bar).values())))
                for bar in people.SURE_BARS]
        if any(n for _, n in bars):
            picked = sure if sure in dict(bars) else bars[0][0]
            surest = {"bars": bars, "bar": picked, "faces": dict(bars)[picked]}
    return page(request, "name_faces.html", nav="people", card=card, counts=counts, steps=STEPS, one=False,
                surest=surest, **state)


@router.get("/people/name/next", response_class=HTMLResponse)
def next_card(request: Request, only: str = "", skip: str = "", skip_groups: str = "", v=Depends(viewer),
              c=Depends(con), w=Depends(writer), s: Site = Depends(site)):
    """The next card, for app.js after an answer."""
    state = _state(only, skip, skip_groups)
    card, counts = _next(s, v, c, w, state)
    return page(request, "_name_card.html", card=card, counts=counts, steps=STEPS, one=False, **state)


@router.get("/people/name/face/{face_id}", response_class=HTMLResponse)
def face_card(request: Request, face_id: int, one: bool = False, only: str = "", skip: str = "",
              skip_groups: str = "", v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """One face's card: after No, the same face with who else it may be, or a box for who it is. one: for a page that
    asks about a face of its own (a face tapped on its photo), without the queue around it."""
    card = _face_card(v, c, w, face_id)
    if card is None:
        raise HTTPException(404, "No such face")
    return page(request, "_name_card.html", card=card, counts={}, steps=STEPS, one=one,
                **_state(only, skip, skip_groups))


@router.get("/api/faces/{face_id}/who")
def who(face_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """What to ask about one face (see _face_card): named is its name now ({"outside": true} for someone a member
    doesn't see), suggest who it most likely is when it has none; card is the same as a card to show."""
    card = _face_card(v, c, w, face_id)
    if card is None:
        raise HTTPException(404, "No such face")
    person, how = card["person"], card["how"]
    named = suggest = None
    if how == "outside":
        named = {"outside": True}
    elif how in ("recognised", "named"):
        named = {"person_id": person["id"], "name": person["name"], "by": "auto" if how == "recognised" else "user"}
        if card["sure"] is not None:
            named["sure"] = round(card["sure"], 3)
        if more := card["more_like"]:
            named["more_like"] = {"person_id": more["id"], "name": more["name"], "face": more["face"],
                                  "score": round(more["score"], 3), "sure": round(more["sure"], 3)}
    elif how == "suggested":
        suggest = {"person_id": person["id"], "name": person["name"], "face": person["face"],
                   "score": round(card["score"], 3), "sure": round(card["sure"], 3)}
    return {"face_id": face_id, "image_id": card["face"]["image_id"], "box": card["face"]["box"],
            "photo": {k: card["photo"][k] for k in ("src", "thumb", "width", "height")}, "named": named,
            "suggest": suggest, "asked": card["asked"] is not None, "card": f"/people/name/face/{face_id}?one=1"}


def _answerable(c, v: access.Viewer, face_id: int, person_id: int) -> tuple:
    """The face and the person an answer is about, when both are the viewer's to see and the face theirs to change."""
    f = catalog.face(c, face_id)
    if f is None:
        raise HTTPException(404, "No such face")
    _face_to_change(c, v, face_id)
    _people_or_404(c, person_id)
    return f, catalog.person(c, person_id)["name"]


@router.post("/api/faces/{face_id}/yes", dependencies=[Depends(_from_app)])
def yes(face_id: int, body: PersonBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """It is them: named as them by hand (people.is_them), unless someone has since named it as someone else by hand,
    or said it isn't a face."""
    f, name = _answerable(c, v, face_id, body.person_id)
    if f["assigned_by"] == "not_a_face":
        raise Problem("Someone said this isn't a face. Look for faces again, on its photo, brings it back.")
    if f["assigned_by"] == "user" and f["person_id"] != body.person_id:
        raise Problem("Someone has named this face by hand since: Fix, on its photo, puts a wrong name right.")
    with changes.tracking(w, v, "faces", f"Named a face {name}", _faces_spec(v, [face_id])) as kept:
        people.is_them(w, body.person_id, [face_id], _by(v))
    return {"person_id": body.person_id, "message": f"Named {name}.", "undo": kept.id}


@router.post("/api/faces/{face_id}/no", dependencies=[Depends(_from_app)])
def no(face_id: int, body: NoBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """It isn't them (people.not_this_person), nor any of also (Neither: not the name a face was recognised as, nor
    whom it looks more like). /api/faces/{id}/who then says who else it may be."""
    pids = list(dict.fromkeys([body.person_id, *body.also]))
    names = [_answerable(c, v, face_id, pid)[1] for pid in pids]
    said = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} or {names[-1]}"
    with changes.tracking(w, v, "faces", f"Said a face isn't {said}", _faces_spec(v, [face_id])) as kept:
        for pid in pids:
            people.not_this_person(w, pid, [face_id])
    return {"message": f"Not {said}: noted.", "undo": kept.id}


@router.post("/api/faces/sure-yes", dependencies=[Depends(_from_app)])
def sure_yes(body: SureBody, v=Depends(viewer), c=Depends(con), w=Depends(writer), s: Site = Depends(site)):
    """Yes to every face of a step of the queue (SURE_STEPS) at least so sure, worked out again now: each named as its
    person by hand (people.is_them; recognition then learns from it), all as one change to undo."""
    if body.step not in SURE_STEPS:
        raise HTTPException(400, "Only recognised faces and those that look like someone can be said yes to at once.")
    found = _sure_enough(s, v, c, body.step, body.at_least / 100)
    ids = [f for faces in found.values() for f in faces]
    if not ids:
        return {"message": "No faces are that sure just now.", "reload": True}
    with changes.tracking(w, v, "faces", f"Said yes to {len(ids):,} faces at least {body.at_least}% sure",
                          _faces_spec(v, ids)) as kept:
        named = sum(len(people.is_them(w, pid, faces, _by(v))) for pid, faces in found.items())
    return {"named": named, "undo": kept.id, "reload": True,
            "message": f"Said yes to {named:,} face{'' if named == 1 else 's'}."}


@router.post("/api/people/{person_id}/yes", dependencies=[Depends(_from_app)])
def yes_faces(person_id: int, body: YesBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """Check faces: the faces ticked are them (people.is_them); one named by hand as someone else since keeps that."""
    _people_or_404(c, person_id)
    for face_id in body.yes:
        if catalog.face(c, face_id) is None:
            raise HTTPException(404, "No such face")
        _face_to_change(c, v, face_id)
    name = catalog.person(c, person_id)["name"]
    with changes.tracking(w, v, "faces", f"Confirmed {len(body.yes)} faces as {name}",
                          _faces_spec(v, body.yes)) as kept:
        named = people.is_them(w, person_id, body.yes, _by(v))
    return {"named": len(named), "undo": kept.id,
            "message": f"{len(named):,} face{'' if len(named) == 1 else 's'} confirmed as {name}."}
