"""The Historical page (historical.py): the family's papers by collection, kind and decade written, and each paper's
own page to read, its pages beside their words; and the changes people make to a paper: what it is, its words, its
pages. Papers are seen as photos are: a member's connection holds those they may see, with their pages."""
import json
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from lineagebox import (catalog, changes, documents, historical, introductions, paper_cards, paper_reading,
                        paper_search, papers, search)
from lineagebox.web.common import MEMBER, derivative, from_app, images_or_404
from lineagebox.web.deps import con, page, site, viewer, writer
from lineagebox.web.routes.files import shown
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/historical": MEMBER,
    "/historical/{paper_id}": MEMBER,
    "/pp/{sha}/{n}": MEMBER,
    "/api/historical/{paper_id}/details": MEMBER,
    "/api/historical/{paper_id}/pages": MEMBER,
    "/api/historical/{paper_id}/pages/{n}/text": MEMBER,
    "/api/historical/{paper_id}/pages/{n}/read-again": MEMBER,
    "/api/historical/{paper_id}/pages/{n}/take-out": MEMBER,
    "/api/historical/{paper_id}/pages/{n}/move": MEMBER,
    "/search/papers": MEMBER,                                  # the search page's Historical part
    "/person/{person_id}/papers": MEMBER,                      # a person's page's Papers part
    "/api/historical/introduction/{paper_id}/skip": MEMBER,    # Show me another
    "/api/historical/{paper_id}/people/{person_id}": MEMBER,   # is it them? (the card's suggestions)
}
PER_PAGE = 60  # papers listed a page at a time
SEARCH_SHOWN = 8  # papers the search page shows, the best first; the rest on the Historical page


class DetailsBody(BaseModel):
    kind: str = Field(min_length=1, max_length=60)
    title: str = Field("", max_length=200)
    written: str = Field("", max_length=120)
    sender: str = Field("", max_length=200)
    recipient: str = Field("", max_length=200)
    summary: str | None = Field(None, max_length=historical.SUMMARY_LIMIT)


class TextBody(BaseModel):
    text: str = Field("", max_length=historical.TEXT_LIMIT)


class PagesBody(BaseModel):
    images: list[int] = Field(min_length=1, max_length=historical.PAGES_AT_ONCE)


class MoveBody(BaseModel):
    to: int


class YesBody(BaseModel):
    yes: bool


def paper_spec(paper_id: int, image_ids=()):
    """What a change to a paper may touch, for Undo: the papers and their pages, the photos' page marks and the
    citations that pass from a paper put into another."""
    ids = json.dumps(sorted({paper_id, *image_ids}))
    every = "SELECT value FROM json_each(?)"
    return lambda new: [("document", f"image_id IN ({every})", (ids,)),
                        ("paper_page", f"paper_id IN ({every}) OR image_id IN ({every})", (ids, ids)),
                        ("image", f"id IN ({every}) OR page_of = ?", (ids, paper_id)),
                        ("paper_check", f"image_id IN ({every})", (ids,)),
                        ("citation", f"image_id IN ({every})", (ids,))]


def _paper_or_404(c, paper_id: int):
    """A paper the viewer sees (on their connection), still in the library."""
    paper = historical.get(c, paper_id)
    if paper is None or not c.execute(f"SELECT 1 FROM image i WHERE i.id = ? AND {catalog.VISIBLE}",
                                      (paper_id,)).fetchone():
        raise HTTPException(404, "No such document")
    return paper


# ---- pages ---------------------------------------------------------------------------------------------------------

@router.get("/historical", response_class=HTMLResponse)
def historical_page(request: Request, kind: str = Query("", max_length=60), folder: int | None = None,
                    decade: str = Query("", max_length=8), sort: str = Query("written", max_length=10),
                    q: str = Query("", max_length=300), check: bool = False,
                    pg: int = Query(1, alias="page", ge=1), v=Depends(viewer),
                    c=Depends(con), w=Depends(writer), s: Site = Depends(site)):
    """The papers the viewer sees: one to be introduced to (introductions.py), what they can be narrowed to (kinds,
    collections, decades), and a page of them; or those a search of their words finds, with the lines that match."""
    facets = s.keep(v, ("historical facets",), 60, lambda: historical.facets(c))
    q = q.strip()
    found = historical.listing(c, kind, folder, decade, sort, to_check=check)
    if q:  # the best match first, each with the lines that matched, within the filters
        allowed = {p["id"] for p in found}
        found = [p for p in paper_search.query(c, q) if p["id"] in allowed]
    intro = None
    if not q and not (kind or folder is not None or decade or check) and pg == 1:
        intro = introductions.offer(c, c if v.admin else w, w, v)
        forget_waiting_mark(s, v)
    shown = found[(pg - 1) * PER_PAGE:pg * PER_PAGE]
    names = {f["id"]: f["name"] for f in facets["collections"]}
    here = {k: val for k, val in (("kind", kind), ("folder", folder), ("decade", decade), ("q", q),
                                  ("check", 1 if check else None)) if val}

    def href(**change) -> str:
        """This list's address with some of its filters changed (to "" or None: taken off)."""
        query = {**here, **({"sort": sort} if sort != "written" else {}), **change}
        defaults = {"sort": "written", "page": 1}  # left out of the address, as are filters taken off
        return "?" + urlencode({k: val for k, val in query.items() if val not in ("", None) and defaults.get(k) != val})

    to_read = historical.counts(c)["to_read"] if v.admin else 0
    return page(request, "historical.html", nav="historical", facets=facets, papers=shown, total=len(found),
                pg=pg, pages=max(1, -(-len(found) // PER_PAGE)), kind=kind, folder=folder, decade=decade,
                sort=sort, here=here, href=href, folder_name=names.get(folder), collection_names=names,
                to_read=to_read, undated=historical.UNDATED, q=q, intro=intro, check=check)


@router.get("/historical/{paper_id}", response_class=HTMLResponse)
def paper_page(request: Request, paper_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer),
               s: Site = Depends(site)):
    """One paper to read: its pages, each beside its words, what it is and where it came from, and what can be done to
    it. A later page's own address leads to its paper, at that page."""
    first = historical.paper_of(c, paper_id)
    if first is not None and first != paper_id:
        n = c.execute("SELECT n FROM paper_page WHERE paper_id = ? AND image_id = ?", (first, paper_id)).fetchone()
        return RedirectResponse(f"/historical/{first}" + (f"#page-{n[0]}" if n else ""), 303)
    paper = _paper_or_404(c, paper_id)
    introductions.mark_seen(w, v, paper_id)  # opened: never offered to them again
    forget_waiting_mark(s, v)
    pages = historical.pages(c, paper_id)
    for p in pages:
        p["lines"] = (p["text"] or "").split("\n") if p["text"] else []
        if p["sha256"]:
            p["picture"] = f"/p/{p['sha256']}"
        elif p["n"] == 1 or paper["format"] == "pdf" and not paper["unreadable"]:
            p["picture"] = f"/p/{paper['sha256']}" if p["n"] == 1 else f"/pp/{paper['sha256']}/{p['n']}"
        else:
            p["picture"] = None
    files = [f for f in catalog.image_files(c, paper_id) if not f["missing"]]
    collection = historical.collection(c, paper_id)
    beside = historical.beside(c, paper_id) if paper["media"] == "photo" else []
    named = paper_cards.people(c, paper_id)
    in_it = [p for p in named if p["state"] in ("auto", "user")]
    linked = {p["person_id"] for p in in_it}
    added = [dict(r) for r in c.execute("""SELECT p.id AS person_id, p.name FROM image_person l JOIN person p
                                           ON p.id = l.person_id WHERE l.image_id = ? ORDER BY p.name""", (paper_id,))
             if r["person_id"] not in linked]
    suggested = [p | {"years": paper_cards.life_years(p["birth_date"], p["death_date"])}
                 for p in named if p["state"] == "suggested"]
    return page(request, "paper.html", nav="historical", paper=paper, pages=pages, kinds=documents.KINDS,
                download=files[0]["id"] if files else None, collection=collection, beside=beside,
                citations=documents.citations(c, paper_id), can_regroup=paper["media"] == "photo",
                file_paper=paper["media"] == "document", in_it=in_it, added=added, suggested=suggested,
                places=json.loads(paper["places"]) if paper["places"] else [],
                related=historical.related(c, paper_id))


@router.get("/pp/{sha}/{n}")
def paper_file_page(sha: str, n: int, v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    """A later page of a PDF the viewer sees, drawn the first time it is asked for and kept (paper_reading)."""
    if not shown(v, sha) or not 2 <= n <= papers.MOST_PAGES:
        raise HTTPException(404)
    row = c.execute(f"""SELECT i.id FROM image i JOIN paper_page p ON p.paper_id = i.id AND p.n = ?
                        WHERE i.sha256 = ? AND i.media = 'document' AND i.format = 'pdf' AND {catalog.VISIBLE}""",
                    (n, sha)).fetchone()
    if row is None:
        raise HTTPException(404)
    if not s.cfg.page_path(sha, n).is_file():
        try:
            paper_reading.drawn_page(c, s.cfg, row[0], sha, n)
        except (OSError, papers.Unreadable, papers.Damaged):
            raise HTTPException(404) from None
    return derivative(s.cfg.page_path(sha, n), "image/jpeg")


def forget_waiting_mark(s: Site, v) -> None:
    """Something was offered or opened: the menu's mark is worked out again on their next page."""
    with s.kept.lock:
        s.kept.kept.pop(("papers waiting", introductions.key(v)), None)


@router.get("/search/papers", response_class=HTMLResponse)
def search_papers(request: Request, q: str = Query("", max_length=300), v=Depends(viewer), c=Depends(con)):
    """The search page's Historical part: the papers whose words match, the best first, with the lines that do.
    Its filters of people and years hold for papers too."""
    f = search.checked(c, search.Filters.from_params(request.query_params))
    q = q.strip()
    found = paper_search.query(c, q, people=f.people, year_from=f.year_from, year_to=f.year_to) if q else []
    return page(request, "_search_papers.html", papers=found[:SEARCH_SHOWN], total=len(found), q=q)


@router.get("/person/{person_id}/papers", response_class=HTMLResponse)
def person_papers(request: Request, person_id: int, v=Depends(viewer), c=Depends(con)):
    """A person's page's Papers part: the papers about them, by them, to them, or naming them."""
    if c.execute("SELECT 1 FROM person WHERE id = ?", (person_id,)).fetchone() is None:
        raise HTTPException(404, "No such person")
    return page(request, "_person_papers.html", papers=historical.for_person(c, person_id))


# ---- changes ---------------------------------------------------------------------------------------------------------

@router.post("/api/historical/introduction/{paper_id}/skip", dependencies=[Depends(from_app)])
def skip_introduction(paper_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """Show me another: this one goes behind the rest."""
    _paper_or_404(c, paper_id)
    introductions.skip(w, v, paper_id)
    w.commit()
    return {}


@router.post("/api/historical/{paper_id}/people/{person_id}", dependencies=[Depends(from_app)])
def confirm_person(paper_id: int, person_id: int, body: YesBody, v=Depends(viewer), c=Depends(con),
                   w=Depends(writer)):
    """Whether someone the AI took a name in the paper for is them."""
    _paper_or_404(c, paper_id)
    if c.execute("SELECT 1 FROM person WHERE id = ?", (person_id,)).fetchone() is None:
        raise HTTPException(404, "No such person")
    row = c.execute("SELECT role FROM paper_person WHERE paper_id = ? AND person_id = ?",
                    (paper_id, person_id)).fetchone()
    with changes.tracking(w, v, "documents", "Said who is named in a document", paper_people_spec(paper_id)) as kept:
        paper_cards.confirm(w, paper_id, person_id, body.yes, row["role"] if row else "named")
    return {"undo": kept.id}


def paper_people_spec(paper_id: int):
    return lambda new: [("paper_person", "paper_id = ?", (paper_id,))]


@router.post("/api/historical/{paper_id}/details", dependencies=[Depends(from_app)])
def save_details(paper_id: int, body: DetailsBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    _paper_or_404(c, paper_id)
    with changes.tracking(w, v, "documents", f"Said what a document is ({body.kind})", paper_spec(paper_id)) as kept:
        historical.set_details(w, paper_id, historical.Details(body.kind, body.title, body.written, body.sender,
                                                               body.recipient, body.summary))
    return {"message": "Saved.", "undo": kept.id}


@router.post("/api/historical/{paper_id}/pages/{n}/text", dependencies=[Depends(from_app)])
def save_text(paper_id: int, n: int, body: TextBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """A person's words for a page: typed, or the AI's corrected."""
    _paper_or_404(c, paper_id)
    with changes.tracking(w, v, "documents", f"Corrected the words of page {n} of a document",
                          paper_spec(paper_id)) as kept:
        historical.set_text(w, paper_id, n, body.text)
    return {"message": "Saved.", "undo": kept.id}


@router.post("/api/historical/{paper_id}/pages/{n}/read-again", dependencies=[Depends(from_app)])
def read_again(paper_id: int, n: int, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    _paper_or_404(c, paper_id)
    historical.read_again(w, paper_id, n)
    w.commit()
    return {"message": "The AI reads it again on the worker's next pass."}


@router.post("/api/historical/{paper_id}/pages", dependencies=[Depends(from_app)])
def add_pages(paper_id: int, body: PagesBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """Scans put after a paper's last page, in the order given: each one the viewer sees."""
    _paper_or_404(c, paper_id)
    images_or_404(c, v, body.images)
    if any(catalog.image(c, i) is None for i in body.images):
        raise HTTPException(404, "No such photo")
    with changes.tracking(w, v, "documents", f"Put {len(body.images)} page{'' if len(body.images) == 1 else 's'} "
                                             f"with a document", paper_spec(paper_id, body.images)) as kept:
        added = historical.add_pages(w, paper_id, body.images)
    return {"message": f"Added {added} page{'' if added == 1 else 's'}.", "undo": kept.id}


@router.post("/api/historical/{paper_id}/pages/{n}/take-out", dependencies=[Depends(from_app)])
def take_out(paper_id: int, n: int, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    _paper_or_404(c, paper_id)
    page_row = c.execute("SELECT image_id FROM paper_page WHERE paper_id = ? AND n = ?", (paper_id, n)).fetchone()
    if page_row is None:
        raise HTTPException(404, "No such page")
    with changes.tracking(w, v, "documents", f"Took page {n} out of a document",
                          paper_spec(paper_id, [page_row[0]] if page_row[0] else [])) as kept:
        historical.take_out(w, paper_id, n)
    return {"message": "It is a document of its own now.", "undo": kept.id}


@router.post("/api/historical/{paper_id}/pages/{n}/move", dependencies=[Depends(from_app)])
def move_page(paper_id: int, n: int, body: MoveBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    _paper_or_404(c, paper_id)
    with changes.tracking(w, v, "documents", f"Moved page {n} of a document", paper_spec(paper_id)) as kept:
        historical.move_page(w, paper_id, n, body.to)
    return {"undo": kept.id}
