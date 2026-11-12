"""A person's details: family links typed on their page, documents and citations, written sources, groups, sex,
life events and names, their life story and notes, their portrait, and checking how names are split."""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from lineagebox import catalog, changes, documents, events, family, people
from lineagebox.web.common import (MEMBER, CitationBody, DetailsBody, DocumentBody, EventBody, GroupBody, LinkBody,
                                   NamePartsBody, OtherNameBody, PortraitBody, SexBody, SourceBody, SourceEditBody,
                                   StoryBody, _among, _by, _document_spec, _family_spec, _from_app, _images_or_404,
                                   _people_or_404)
from lineagebox.web.deps import con, page, viewer, writer

router = APIRouter()
ACCESS = {
    "/api/people/{person_id}/link": MEMBER,
    "/api/images/{image_id}/document": MEMBER,
    "/api/images/{image_id}/document/remove": MEMBER,
    "/api/images/{image_id}/citations": MEMBER,
    "/api/citations/{citation_id}/delete": MEMBER,
    "/api/people/{person_id}/groups": MEMBER,
    "/api/people/{person_id}/groups/remove": MEMBER,
    "/api/people/{person_id}/sex": MEMBER,
    "/api/people/{person_id}/details": MEMBER,
    "/api/people/{person_id}/events": MEMBER,
    "/api/events/{event_id}/delete": MEMBER,
    "/api/people/{person_id}/name-parts": MEMBER,
    "/api/people/{person_id}/names": MEMBER,
    "/api/names/{name_id}/delete": MEMBER,
    "/family/names": MEMBER,
    "/api/family/names/confirm": MEMBER,
    "/api/people/{person_id}/story": MEMBER,
    "/api/people/{person_id}/portrait": MEMBER,
    "/api/people/{person_id}/sources": MEMBER,
    "/api/sources/{source_id}": MEMBER,
    "/api/source-citations/{citation_id}/delete": MEMBER,
}


@router.post("/api/people/{person_id}/link", dependencies=[Depends(_from_app)])
def link_person(person_id: int, body: LinkBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    if catalog.person(c, person_id) is None:
        raise HTTPException(404, "No such person")
    with_parent = int(body.with_parent) if body.with_parent.isdecimal() else None
    if with_parent is not None and catalog.person(c, with_parent) is None:  # removed since the page was made
        raise HTTPException(404, "No such person: the other parent was merged or removed since. Reload the page.")
    name = catalog.person(c, person_id)["name"]
    # someone already in the tree is known before the change, so undoing it leaves them there; someone new is
    # found by the spec as a person the member added
    existing = people.existing_person_id(w, body.name, body.name_id, _among(v))

    def link():
        other = existing or people.person_id(w, body.name, body.name_id, _among(v), _by(v))
        family.link(w, person_id, body.word, other, with_parent, _among(v))

    with changes.tracking(w, v, "family", f"Added {body.name} as {name}'s {body.word}",
                          _family_spec(v, person_id, with_parent, existing)):
        link()
    return {}


@router.post("/api/images/{image_id}/document", dependencies=[Depends(_from_app)])
def save_document(image_id: int, body: DocumentBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    if catalog.image(c, image_id) is None:
        raise HTTPException(404, "No such image")
    with changes.tracking(w, v, "documents", f"Marked a photo as a document ({body.kind})",
                          _document_spec(image_id)):
        documents.save(w, image_id, body.kind, body.title)
    return {"message": "Saved."}


@router.post("/api/images/{image_id}/document/remove", dependencies=[Depends(_from_app)])
def remove_document(image_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    _images_or_404(c, v, [image_id])
    with changes.tracking(w, v, "documents", "Said a photo is not a document", _document_spec(image_id)):
        documents.remove(w, image_id)
    return {}


@router.post("/api/images/{image_id}/citations", dependencies=[Depends(_from_app)])
def add_citation(image_id: int, body: CitationBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    if v.limited:
        kind, _, target = body.target.partition(":")
        table = {"p": "person", "e": "event"}.get(kind)
        if documents.get(c, image_id) is None or table is None or not target.isdecimal() or not c.execute(
                f"SELECT 1 FROM {table} WHERE id = ?", (int(target),)).fetchone():
            raise HTTPException(404, "No such document, person or event")
    with changes.tracking(w, v, "documents", "Said what a document is a record of", _document_spec(image_id)):
        documents.cite(w, image_id, body.target)
    return {}


@router.post("/api/citations/{citation_id}/delete", dependencies=[Depends(_from_app)])
def delete_citation(citation_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    found = c.execute("SELECT image_id FROM citation WHERE id = ?", (citation_id,)).fetchone()
    if found is None:
        raise HTTPException(404, "No such citation")
    with changes.tracking(w, v, "documents", "Took off what a document is a record of",
                          _document_spec(found[0])):
        documents.uncite(w, citation_id)
    return {}


@router.post("/api/people/{person_id}/groups", dependencies=[Depends(_from_app)])
def add_to_group(person_id: int, body: GroupBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """Put someone in a group. Their own family not in it yet (people.group_family: a family friend's wife and
    children) is asked about first: the answer lists them to tick (app.js choose), and comes back with also, those
    ticked ([]: only them). Without such family it is done at once. All of it is one change, to undo."""
    person = catalog.person(c, person_id)
    if person is None:
        raise HTTPException(404, "No such person")
    kin = people.group_family(c, person_id, body.name)
    group = people.group_name(c, body.name)
    if body.also is None and kin:
        first = person["name"].split()[0] if person["name"].split() else person["name"]
        return {"choose": {
            "title": f"Put {first}'s family in {group} too?",
            "text": f"They are {person['name']}'s family and aren't in {group} yet. Untick anyone who isn't.",
            "items": [{"id": k["id"], "label": k["name"], "detail": k["how"]} for k in kin],
            "field": "also", "yes": "Add the ticked ones too", "no": f"Only {first}"}}
    also = [k["id"] for k in kin if k["id"] in set(body.also or ())]
    summary = f"Put {person['name']}" + (f" and {len(also)} of their family" if also else "") + f" in {group}"
    with changes.tracking(w, v, "person", summary, _family_spec(v, person_id, *also)):
        group = people.add_to_group(w, person_id, body.name, _by(v))
        for x in also:
            people.add_to_group(w, x, group, _by(v))
    return {"message": summary + "."}


@router.post("/api/people/{person_id}/groups/remove", dependencies=[Depends(_from_app)])
def remove_from_group(person_id: int, body: GroupBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    _people_or_404(c, person_id)
    with changes.tracking(w, v, "person", f"Took someone out of {body.name}", _family_spec(v, person_id)):
        people.remove_from_group(w, person_id, body.name)
    return {}


@router.post("/api/people/{person_id}/sex", dependencies=[Depends(_from_app)])
def set_sex(person_id: int, body: SexBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    _people_or_404(c, person_id)
    with changes.tracking(w, v, "person", "Changed someone's sex", _family_spec(v, person_id)):
        family.set_sex(w, person_id, body.sex or None)
    return {}


@router.post("/api/people/{person_id}/details", dependencies=[Depends(_from_app)])
def set_details(person_id: int, body: DetailsBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """The birth date and "passed away": a birth event (kept, with its place, when the date is cleared) and a
    death with no date."""
    if catalog.person(c, person_id) is None:
        raise HTTPException(404, "No such person")
    with changes.tracking(w, v, "person", f"Changed {catalog.person(c, person_id)['name']}'s birth date",
                          _family_spec(v, person_id)):
        return save_details(person_id, body, c, w)


def save_details(person_id: int, body: DetailsBody, c, w):
    birth = c.execute("SELECT id, place FROM event WHERE person_id = ? AND tag = 'BIRT'", (person_id,)).fetchone()
    if body.birth_date or (birth and birth["place"]):
        events.save(w, birth["id"] if birth else None, tag="BIRT", person_id=person_id,
                    date_text=body.birth_date, place=(birth["place"] or "") if birth else "")
    elif birth:
        events.delete(w, birth["id"])
    events.set_deceased(w, person_id, bool(body.deceased))
    w.commit()
    return {"message": "Saved."}


@router.post("/api/people/{person_id}/events", dependencies=[Depends(_from_app)])
def save_event(person_id: int, body: EventBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """Add or change one of the person's events, or one of a couple of theirs (with partner)."""
    if catalog.person(c, person_id) is None:
        raise HTTPException(404, "No such person")
    if body.id is not None:
        row = c.execute("SELECT * FROM event WHERE id = ?", (body.id,)).fetchone()
        if row is None or person_id not in (row["person_id"], row["couple_a"], row["couple_b"]):
            raise HTTPException(404, "No such event")
    partner = int(body.partner) if str(body.partner).isdecimal() else None
    _people_or_404(c, partner)
    tag = body.tag.upper()
    if tag not in events.COUPLE_EVENTS:
        partner = None  # the form's partner choice is for couples' events only
    elif tag != "EVEN" and partner is None:
        raise HTTPException(400, "Pick who they married, were engaged to or divorced.")
    with changes.tracking(w, v, "person", f"Saved a life event of {catalog.person(c, person_id)['name']}",
                          _family_spec(v, person_id, partner)):
        events.save(w, body.id, tag=body.tag, person_id=None if partner else person_id,
                    couple=(person_id, partner) if partner else None, date_text=body.date,
                    place=body.place, kind=body.type, note=body.note)
    return {"message": "Saved."}


@router.post("/api/events/{event_id}/delete", dependencies=[Depends(_from_app)])
def delete_event(event_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    if v.limited and not c.execute("SELECT 1 FROM event WHERE id = ?", (event_id,)).fetchone():
        raise HTTPException(404, "No such event")
    e = c.execute("SELECT person_id, couple_a, couple_b FROM event WHERE id = ?", (event_id,)).fetchone()
    with changes.tracking(w, v, "person", "Deleted a life event", _family_spec(v, *(e or ()))):
        events.delete(w, event_id)
    return {}


@router.post("/api/people/{person_id}/name-parts", dependencies=[Depends(_from_app)])
def save_name_parts(person_id: int, body: NamePartsBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    if catalog.person(c, person_id) is None:
        raise HTTPException(404, "No such person")
    with changes.tracking(w, v, "person", f"Split {catalog.person(c, person_id)['name']}'s name",
                          _family_spec(v, person_id)):
        events.set_name_parts(w, person_id, body.given, body.surname, body.type)
    return {"message": "Saved."}


@router.post("/api/people/{person_id}/names", dependencies=[Depends(_from_app)])
def add_name(person_id: int, body: OtherNameBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    if catalog.person(c, person_id) is None:
        raise HTTPException(404, "No such person")
    with changes.tracking(w, v, "person", f"Gave {catalog.person(c, person_id)['name']} another name, {body.name}",
                          _family_spec(v, person_id)):
        events.add_name(w, person_id, body.name, body.type)
    return {}


@router.post("/api/names/{name_id}/delete", dependencies=[Depends(_from_app)])
def delete_name(name_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    if v.limited and not c.execute("SELECT 1 FROM person_name WHERE id = ?", (name_id,)).fetchone():
        raise HTTPException(404, "No such name")
    owner = w.execute("SELECT person_id FROM person_name WHERE id = ?", (name_id,)).fetchone()
    with changes.tracking(w, v, "person", "Took off another name", _family_spec(v, owner[0] if owner else None)):
        w.execute("DELETE FROM person_name WHERE id = ?", (name_id,))
    return {}


@router.get("/family/names", response_class=HTMLResponse)
def names_page(request: Request, c=Depends(con)):
    """Name parts to check: split at the last word when they were first needed (GEDCOM keeps them apart)."""
    rows = c.execute("""SELECT id, name, given_name, surname, name_type FROM person WHERE NOT names_checked
                        ORDER BY name COLLATE NOCASE""").fetchall()
    done = c.execute("SELECT count(*) FROM person WHERE names_checked").fetchone()[0]
    return page(request, "family_names.html", nav="family", rows=rows, done=done, name_types=events.NAME_TYPES)


@router.post("/api/family/names/confirm", dependencies=[Depends(_from_app)])
def confirm_names(v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """Every name still to check (of those the viewer sees) is right as it was split."""
    ids = json.dumps([r[0] for r in c.execute("SELECT id FROM person WHERE NOT names_checked")])
    with changes.tracking(w, v, "person", "Said names are split right",
                          lambda new: [("person", "id IN (SELECT value FROM json_each(?))", (ids,))]):
        n = w.execute("UPDATE person SET names_checked = 1 WHERE id IN (SELECT value FROM json_each(?))",
                      (ids,)).rowcount
    return {"confirmed": n}


@router.post("/api/people/{person_id}/story", dependencies=[Depends(_from_app)])
def save_story(person_id: int, body: StoryBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """Their life story, as the family writes it, or their notes (the sources a reading added, notes from another
    tree): anyone who sees them may write either, and can undo it."""
    p = catalog.person(c, person_id)
    if p is None:
        raise HTTPException(404, "No such person")
    fields = {k: (val.replace("\r\n", "\n").strip() or None) for k, val in
              (("biography", body.biography), ("notes", body.notes)) if val is not None}
    if not fields:
        return {}
    what = "life story" if "biography" in fields else "notes"
    with changes.tracking(w, v, "person", f"Wrote {p['name']}'s {what}", _family_spec(v, person_id)) as kept:
        w.execute(f"UPDATE person SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                  (*fields.values(), person_id))
    return {"message": "Saved.", "undo": kept.id}


@router.post("/api/people/{person_id}/portrait", dependencies=[Depends(_from_app)])
def set_portrait(person_id: int, body: PortraitBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """The portrait shown for them on People, Family, the tree and in exports: one of the faces named as them, in a
    photo the viewer sees. None goes back to the clearest of them, picked anew as faces are named."""
    p = catalog.person(c, person_id)
    if p is None:
        raise HTTPException(404, "No such person")
    if body.face_id is not None and not c.execute("""SELECT 1 FROM face WHERE id = ? AND person_id = ?
                                                     AND assigned_by IN ('user', 'auto')""",
                                                  (body.face_id, person_id)).fetchone():
        raise HTTPException(404, f"That face isn't named {p['name']}.")
    with changes.tracking(w, v, "person", f"Chose {p['name']}'s portrait", _family_spec(v, person_id)) as kept:
        w.execute("UPDATE person SET cover_face_id = ? WHERE id = ?", (body.face_id, person_id))
    return {"undo": kept.id}


def _target_of(c, person_id: int, target: str) -> None:
    """A citation on someone's page is for them or one of their own (or their couples') life events."""
    kind, _, number = target.partition(":")
    ok = number.isdecimal() and (
        (kind == "p" and int(number) == person_id) or kind == "e" and c.execute(
            "SELECT 1 FROM event WHERE id = :e AND (person_id = :p OR :p IN (couple_a, couple_b))",
            {"e": int(number), "p": person_id}).fetchone() is not None)
    if not ok:
        raise HTTPException(404, "No such person or event")


@router.post("/api/people/{person_id}/sources", dependencies=[Depends(_from_app)])
def add_source(person_id: int, body: SourceBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """Cite a written source for someone or one of their life events: one already here (picked, or typed by the
    same title), or a new one."""
    p = catalog.person(c, person_id)
    if p is None:
        raise HTTPException(404, "No such person")
    _target_of(c, person_id, body.target)
    source_id = body.source_id
    if source_id is not None and not c.execute("SELECT 1 FROM source WHERE id = ?", (source_id,)).fetchone():
        raise HTTPException(404, "No such source")
    if source_id is None and body.title.strip():  # the same title typed again is the same source, if theirs to see
        source_id = documents.find_source(c, body.title, body.author)
    with changes.tracking(w, v, "person", f"Cited a source for {p['name']}", _family_spec(v, person_id)) as kept:
        if source_id is None:
            source_id = documents.save_source(w, None, body.title, body.author, body.publication, body.note, _by(v))
        documents.cite_source(w, source_id, body.target, body.page)
    return {"undo": kept.id}


@router.post("/api/sources/{source_id}", dependencies=[Depends(_from_app)])
def edit_source(source_id: int, body: SourceEditBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """Change a source's title, author, publication or note, wherever it is cited."""
    if not c.execute("SELECT 1 FROM source WHERE id = ?", (source_id,)).fetchone():
        raise HTTPException(404, "No such source")
    with changes.tracking(w, v, "person", f"Changed the source {body.title}",
                          lambda new: [("source", "id = ?", (source_id,))]) as kept:
        documents.save_source(w, source_id, body.title, body.author, body.publication, body.note)
    return {"message": "Saved.", "undo": kept.id}


@router.post("/api/source-citations/{citation_id}/delete", dependencies=[Depends(_from_app)])
def delete_source_citation(citation_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    row = c.execute("""SELECT coalesce(c.person_id, e.person_id, e.couple_a) AS pid, e.couple_b FROM source_citation c
                       LEFT JOIN event e ON e.id = c.event_id WHERE c.id = ?""", (citation_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "No such citation")
    with changes.tracking(w, v, "person", "Took a source off", _family_spec(v, row["pid"], row["couple_b"])) as kept:
        documents.uncite_source(w, citation_id)
    return {"undo": kept.id}
