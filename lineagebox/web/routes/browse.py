"""Pages to look around by: folders, years, search, a photo, tags, people, a person, the family."""
import dataclasses
import json
from calendar import month_name
from datetime import date
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from lineagebox import (access, catalog, comments, copies, documents, events, family, family_reader, life, memories,
                        people, search, sharing, tagging, tours, writeback)
from lineagebox import places as map_places, sidecars  # places: a list's name in search_page
from lineagebox import chapters, speech, stories  # long videos' chapters, transcripts, recorded stories
from lineagebox import embeddings  # search by meaning
from lineagebox import albums, favourites  # the albums a photo is in, the viewer's heart on it
from lineagebox import headcount  # how many people a photo shows, and how sure the AI is of it
from lineagebox.folders import FolderIndex
from lineagebox.web.common import (ADMIN, MEMBER, _covers, _kin_labels, _name_choices, _others_by_whose, _share_panel,
                                   _upload_labels, _within, years_of)
from lineagebox.web.deps import con, page, paging, site, viewer, writer
from lineagebox.web import pieces  # big pages a piece at a time, with a scrubber
from lineagebox import date_hints, face_ages  # date help for scans; a birth year from the faces
from lineagebox import first_run  # the first steps, on the start page of a new LineageBox
from lineagebox.web.routes import face_checks  # Confirm faces' Ages to check and Faces missed, groups' look-alikes
from lineagebox.web.routes.home import home_page
from lineagebox.web.routes.reading import readings as reading_list
from lineagebox.web.routes.tours import shown as tour_shown
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/": MEMBER,
    "/folder/{folder_id}": MEMBER,
    "/timeline": MEMBER,
    "/timeline/{year}": MEMBER,
    "/timeline/{year}/events": MEMBER,
    "/search": MEMBER,
    "/api/search/suggest": MEMBER,
    "/image/{image_id}": MEMBER,
    "/tags": MEMBER,
    "/tag/{name:path}": MEMBER,
    "/photos/undecodable": ADMIN,
    "/photos/{value:path}": ADMIN,
    "/people": MEMBER,
    "/people/together": MEMBER,
    "/cleanup/faces": MEMBER,
    "/person/{person_id}": MEMBER,
    "/person/{person_id}/faces": MEMBER,
    "/person/{person_id}/life/{key}": MEMBER,
    "/person/{person_id}/portraits": MEMBER,
    "/person/{person_id}/years": MEMBER,
    "/family": MEMBER,
    "/family/tree": MEMBER,
}


@router.get("/", response_class=HTMLResponse)
def home(request: Request, v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    if v.limited:  # members start on Home: their Folders hold only their uploads and what is shared with them
        return home_page(request, v, c)
    root = catalog.root_folder_id(c)
    if root is None:  # a new LineageBox: the first steps, from watching a folder on
        return page(request, "empty.html", first_run=first_run.checklist(c, s.live(c)))
    tree = catalog.folder_tree(c)
    folder = tree[root]
    while not folder.own and len(shown := [f for f in folder.children if f.total]) == 1:
        folder = shown[0]  # one watched folder: start in it rather than at the share
    return folder_page(request, folder.id, v=v, c=c, w=c, s=s)  # an admin's connection is also their writer


@router.get("/folder/{folder_id}", response_class=HTMLResponse)
def folder_page(request: Request, folder_id: int, select: bool = False, v=Depends(viewer), c=Depends(con),
                w=Depends(writer), s: Site = Depends(site)):
    """A folder's photos and the folders in it. A member browses their own uploads, and the folders others shared
    with them, each from its top; theirs they can also share and move to Removed (an admin: any folder)."""
    tree = catalog.folder_tree(c)
    folder = tree.get(folder_id)
    uploads = v.account["upload_folder"] if v.limited else None
    top = None  # where a member's browsing starts: their uploads, or a folder shared with them (the outermost)
    if folder is not None and v.limited:
        top = next((p for p in sorted(filter(None, (uploads, *v.shared_folders)), key=len)
                    if _within(folder.path, p)), None)
    if folder is None or v.limited and top is None:
        raise HTTPException(404, "No such folder")
    images = catalog.folder_images(c, folder_id)
    photos = pieces.part(request, images, f"folder:{folder_id}", select)  # a piece at a time (pieces.py)
    if photos["w"].piece:
        return page(request, "_piece.html", p=photos)
    crumbs = []
    node = folder
    while node is not None and (v.admin or _within(node.path, top)):
        crumbs.insert(0, node)
        node = tree.get(node.parent_id)
    subfolders = [f for f in folder.children if f.total or f.ignored]
    shown = {f.path for f in (*crumbs, *subfolders)}
    labels = {path: name for path, name in _upload_labels(w, v).items() if path in shown}
    own = v.admin or _within(folder.path, uploads)
    watched = s.live(c).folders
    from_whom = None
    if not own:  # who shared the folder they are in
        from_whom = next((x["by_name"] for x in sharing.with_me(w, v.account_id)
                          if x["kind"] == "folder" and x["item_id"] == crumbs[0].id), None)
    return page(request, "folder.html", folder=folder, crumbs=crumbs, labels=labels, subfolders=subfolders,
                images=images, photos=photos, everything=[r["id"] for r in images] if select else None,
                ctx=f"folder:{folder_id}", select=select, own=own,
                from_whom=from_whom, removable=own and folder.path != "" and not any(_within(p, folder.path)
                                                                                    for p in watched),
                sharing=_share_panel(w, v, "folder", folder_id) if own and folder.path and getattr(
                    request.state, "account", None) else None,
                folder_hidden=bool(c.execute("SELECT hidden FROM folder WHERE id = ?", (folder_id,)).fetchone()[0]),
                meaning=_meaning(c, folder_id) if v.admin and folder.path else None)


def _words(w, v, image_id: int) -> dict:
    """The photo's caption and comments. A member sees who wrote each only if that is someone in their part of the
    family (or themselves); anyone else is "a family member"."""
    words = comments.for_image(w, image_id)
    if v.limited:
        for item in [words["caption"], *words["comments"]]:
            if item and item["account_id"] != v.account_id and item["person_id"] not in v.people:
                item["by"] = "a family member"
    return words


def _meaning(c, folder_id: int) -> dict:
    """What the folder's name was read as (or set to by hand), for the folder page's panel that corrects it."""
    r = c.execute("SELECT * FROM folder WHERE id = ?", (folder_id,)).fetchone()
    when = ""
    if r["year"]:
        when = (f"{r['year']:04d}-{r['month']:02d}-{r['day']:02d}" if r["month"] and r["day"]
                else f"{month_name[r['month']]} {r['year']}" if r["month"] else str(r["year"]))
    return {"place": r["place"] or "", "event": r["event"] or "", "when": when, "kind": r["kind"],
            "people": ", ".join(json.loads(r["people_hint"] or "[]")), "source": r["source"],
            "read": r["kind"] is not None}


@router.get("/timeline", response_class=HTMLResponse)
def timeline_page(request: Request, c=Depends(con)):
    years = catalog.timeline(c)
    peak = max((n for months in years.values() for n in months.values()), default=1)
    return page(request, "timeline.html", years=years, peak=peak)


@router.get("/timeline/{year}/events", response_class=HTMLResponse)
def year_events(request: Request, year: int, v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    """A year's events (memories.year_events), loaded after the page: picking them takes a moment in a busy year, so
    each year's are kept 10 minutes for each viewer (and worked out again after a change)."""
    today = date.today()
    found = s.keep(v, ("year-events", year, today.isoformat()), 600,
                   lambda: memories.year_events(c, year, today, s.live(c).blurry_below))
    return page(request, "_year_events.html", events=found, year=year, least=memories.LEAST)


@router.get("/timeline/{year}", response_class=HTMLResponse)
def year_page(request: Request, year: int, select: bool = False, c=Depends(con)):
    """A year's photos month by month, a piece at a time (pieces.py), with its months at the side."""
    images = catalog.year_images(c, year)
    photos = pieces.part(request, images, f"year:{year}", select, by_month=True)
    if photos["w"].piece:
        return page(request, "_piece.html", p=photos)
    years = sorted(catalog.timeline(c))
    i = years.index(year) if year in years else -1
    return page(request, "year.html", year=year, images=images, photos=photos, ctx=f"year:{year}", select=select,
                everything=[r["id"] for r in images] if select else None,
                prev_year=years[i - 1] if i > 0 else None,
                next_year=years[i + 1] if 0 <= i < len(years) - 1 else None)


def _search_href(text: str, f: search.Filters) -> str:
    params = f.params(text)
    return "/search?" + urlencode(params) if params else "/search"


def _search_chips(text: str, f: search.Filters, names: dict[int, str]) -> list[dict]:
    """The filters a search has, each a chip that takes it off: [{'label', 'off' (the search without it), 'without'
    (its Filters)}]."""
    chips = []

    def chip(label, without):
        chips.append({"label": label, "without": without, "off": _search_href(text, without)})

    for pid in f.people:
        chip(names.get(pid, "Someone"), dataclasses.replace(f, people=tuple(p for p in f.people if p != pid)))
    if f.year_from or f.year_to:
        a, b = f.year_from, f.year_to
        chip(str(a) if a == b else f"{a} to {b}" if a and b else f"From {a}" if a else f"Up to {b}",
             dataclasses.replace(f, year_from=None, year_to=None))
    if f.place:
        chip(f.place, dataclasses.replace(f, place=""))
    if f.media:
        chip({"photos": "Photos only", "recordings": "Recordings only"}.get(f.media, "Videos only"),
             dataclasses.replace(f, media=""))
    if f.unnamed:
        chip("Faces to name", dataclasses.replace(f, unnamed=False))
    if f.undated:
        chip("No date", dataclasses.replace(f, undated=False))
    return chips


@router.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = "", select: bool = False, pg: int = Query(1, alias="page", ge=1),
                per: str | None = None, v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    """The people a search is about, then its photos, a page at a time; the filters it has as chips that take them
    off, and the ones to add. The address holds the words and filters (search.Filters.params), so a search can be
    kept, shared, and walked through with ←/→ on the photos' pages (search.context). Nothing found: what to try."""
    if q.startswith("?") and (back := search.from_context(q)) and (back[0] or back[1].narrowed):
        # the photo page's way back from a search with filters (its context, as words): the search itself
        return RedirectResponse(_search_href(back[0], back[1]), 303)
    q = q.strip()[:300]
    f = search.checked(c, search.Filters.from_params(request.query_params))
    asked = bool(q) or f.narrowed
    results = search.query(c, q, search.RESULTS, f, meaning=embeddings.asker(s.live(c))) if asked else []
    p = paging(request, "search", pg, per, "100")
    p.total = len(results)
    st = _stats(s, v, c) if q else {"described": 0, "to_describe": 0}
    names = dict(c.execute("SELECT id, name FROM person"))
    found = search.people_matching(c, q) if q else []
    named = {r["id"]: r for r in s.keep(v, ("people",), COUNTS_FOR, lambda: catalog.people(c))} if found else {}
    cards = [{"id": pid, "name": names[pid], "cover_face": named[pid]["cover_face"] if pid in named else None,
              "photos": named[pid]["photos"] if pid in named else 0} for pid in found]
    chips = _search_chips(q, f, names)
    href = lambda **change: _search_href(q, dataclasses.replace(f, **change))  # noqa: E731
    places = s.keep(v, ("search places",), COUNTS_FOR, lambda: search.places(c))
    ideas = []
    if asked and not results:  # what would find something: a word spelt as a name here, a word or a filter less
        tags = s.keep(v, ("tags", "count"), COUNTS_FOR, lambda: catalog.tags(c, "", "count", limit=None)[0])
        vocabulary = [t["name"] for t in tags] + list(names.values()) + [name for name, _ in places]
        ideas = [{**i, "href": _search_href(i["q"], f)} for i in search.ideas(c, q, f, vocabulary)]
        for chip in chips:
            if n := search.count(c, q, chip["without"]):
                ideas.append({"label": f"Without “{chip['label']}”", "href": chip["off"], "count": n})
    return page(request, "search.html", q=q, f=f, results=results[p.offset:p.offset + p.size], total=len(results),
                limit=search.RESULTS, ctx=search.context(q, f), described=st["described"],
                to_describe=st["to_describe"], select=select, paging=p,
                page_query={**f.params(q), **({"select": 1} if select else {})}, here_query=f.params(q),
                people_found=cards,
                kin=_kin_labels(c), asked=asked, chips=chips, kept=f.params(), ideas=ideas[:8],
                heading=f"“{q}”" if q else ", ".join(chip["label"] for chip in chips) or "Search",
                media=[("Photos and videos", href(media=""), not f.media), ("Photos", href(media="photos"),
                        f.media == "photos"), ("Videos", href(media="videos"), f.media == "videos"),
                       *([("Recordings", href(media="recordings"), True)] if f.media == "recordings" else [])],
                toggles=[("Faces to name", href(unnamed=not f.unnamed), f.unnamed,
                          "Photos with a face nobody has named yet"),
                         ("No date", href(undated=not f.undated), f.undated,
                          "Photos without a date of their own: the camera didn't record one, and nobody has set it")],
                sorts=[(label, href(sort=key), f.sort == key or (key == "oldest" and f.sort == "best" and not q))
                       for key, label in (("best", "Best match"), ("newest", "Newest first"),
                                          ("oldest", "Oldest first")) if q or key != "best"],
                years=sorted(s.keep(v, ("search years",), COUNTS_FOR, lambda: search.years(c)), reverse=True),
                place_names=[name for name, _ in places[:400]])


@router.get("/api/search/suggest")
def search_suggest(q: str = "", people: str = "", v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    """What to offer as a search is typed (app.js, data-search-suggest): people, family words, places, tags and
    years for the end of what is typed, from what the viewer sees (search.suggest). Each list is kept a minute for
    the viewer: this is asked again with each letter. people: those already asked for, not offered again."""
    keep = lambda name, work: s.keep(v, name, COUNTS_FOR, work)  # noqa: E731
    chosen = {int(x) for x in people.split(",") if x.strip().isdecimal()}
    items = search.suggest(q[:200], keep(("people names", v.me), lambda: _name_choices(c)),
                           keep(("family words", v.me), lambda: search.family_words(c)),
                           keep(("search places",), lambda: search.places(c)),
                           keep(("tags", "count"), lambda: catalog.tags(c, "", "count", limit=None)[0]),
                           keep(("search years",), lambda: search.years(c)), chosen)
    return {"items": items}


@router.get("/image/{image_id}", response_class=HTMLResponse)
def image_page(request: Request, image_id: int, ctx: str = "", v=Depends(viewer), c=Depends(con),
               w=Depends(writer), s: Site = Depends(site)):
    img = catalog.image(c, image_id)
    if img is None:
        raise HTTPException(404, "No such image")
    files = catalog.image_files(c, image_id)
    download = next((f["id"] for f in files if not f["missing"]), None)
    if v.limited:  # a member's own copies; the folders of the others are the admins'
        files = [f for f in files if _within(f["path"], v.account["upload_folder"])]
    tour = tours.tour(c, int(ctx[5:])) if ctx.startswith("tour:") and ctx[5:].isdecimal() else None
    if tour:
        ids = [p["id"] for p in tour_shown(s, v, c, w, tour)[0]]
    else:
        ids = search.context_ids(c, ctx, embeddings.asker(s.live(c))) if ctx else []
    pos = ids.index(image_id) if image_id in ids else -1
    back = "/"
    if tour:
        back = f"/tour/{tour['id']}"
    elif ctx.startswith("folder:"):
        back = f"/folder/{ctx[7:]}"
    elif ctx.startswith("year:"):
        back = f"/timeline/{ctx[5:]}#m{int(img['taken_at'][5:7]) if img['taken_at'] else ''}"
    elif ctx.startswith("search:?"):  # words with filters or a sort (search.context)
        back = "/search" + ctx[7:]
    elif ctx.startswith("search:"):
        back = "/search?" + urlencode({"q": ctx[7:]})
    elif ctx.startswith("album:") and ctx[6:].isdecimal():  # albums.py: back to the album, ← → in its order
        back = f"/album/{ctx[6:]}"
    elif ctx.startswith("tag:"):
        back = "/tag/" + quote(ctx[4:])
    elif ctx.startswith("photos:"):
        back = "/photos/" + quote(ctx[7:])
    elif ctx.startswith("person:") and ctx[7:].isdecimal() and (v.admin or v.sees_person(int(ctx[7:]))):
        back = f"/person/{ctx[7:]}"
    elif files:
        back = f"/folder/{files[0]['folder_id']}"
    if pos >= pieces.PIECE and ctx.startswith(("tour:", "folder:", "year:", "tag:")):  # pages loaded in pieces:
        back = f"{back.split('#')[0]}?skip={pos - pos % pieces.PIECE}#t{image_id}"   # from the piece with this photo
    next_id = ids[pos + 1] if 0 <= pos < len(ids) - 1 else None
    analysis = catalog.analysis_for(c, image_id)
    is_video = img["media"] == "video"
    is_audio = img["media"] == "audio"  # a sound recording: played, transcribed, with no picture but its wave
    transcript = speech.for_page(c, image_id) if is_video or is_audio else None
    faces = [dict(f) for f in catalog.image_faces(c, image_id)]
    linked = [dict(p) for p in tagging.linked_people(c, image_id)]
    if v.limited:  # people from outside their part of the family are named, without a link
        on_faces = {f["person_id"] for f in faces}
        added = {r[0] for r in c.execute("SELECT person_id FROM image_person WHERE image_id = ?", (image_id,))}
        outside = access.names_of(w, {p for p in on_faces | added if p is not None} - v.people)
        for f in faces:
            if f["person_id"] in outside:
                f.update(name=outside[f["person_id"]], outside=True)
        linked += [{"id": p, "name": n, "outside": True} for p, n in outside.items() if p not in on_faces]
    doc = documents.get(c, image_id)
    names = dict(c.execute("SELECT id, name FROM person"))
    # worked out against everyone; shown only for someone the viewer sees (names holds only them)
    suggested = {f: (pid, names[pid]) for f, (pid, *_) in people.suggest_for_photo(w, image_id).items()
                 if pid in names}
    return page(request, "image.html", img=img, files=files, ctx=ctx, back=back, is_video=is_video,
                frames=catalog.video_frames(c, image_id) if is_video else [],
                copy=copies.find_copy(c, s.cfg, image_id) if (is_video or is_audio) and img["playable"] == "copy"
                else None, transcript=transcript, is_audio=is_audio,
                chapters=chapters.for_page(c, image_id, transcript) if is_video else [],
                stories=stories.named(stories.for_image(c, image_id), v), about={"kind": "image", "id": image_id},
                most_minutes=stories.MOST_SECONDS // 60,
                position=(pos + 1, len(ids)) if pos >= 0 else None,
                prev_id=ids[pos - 1] if pos > 0 else None, next_id=next_id,
                next_sha=catalog.image(c, next_id)["sha256"] if next_id else None,
                ai=analysis, ai_raw=json.loads(analysis["raw_json"]) if analysis else {},
                people_seen=headcount.shown(c, analysis),
                folder_ctx=FolderIndex(c).context(image_id), similar=catalog.group_members(c, image_id),
                date_hint=date_hints.suggest(c, image_id),  # a photo dated only by file time or folder: a year
                faces=faces, kin=_kin_labels(c), tags=tagging.for_image(c, image_id), linked=linked,
                ages=events.ages(c, img, [f["person_id"] for f in faces] + [p["id"] for p in linked]),
                doc=doc, doc_kinds=documents.KINDS, citations=documents.citations(c, image_id) if doc else [],
                cite_targets=documents.targets(c) if doc else [], suggested=suggested,
                turn_problem=writeback.why_not(img), mine=v.admin or any(not f["missing"] for f in files),
                download=download, words=_words(w, v, image_id),
                # the face each person in it is shown by (People, Family, the tree), so this one can be made it
                portraits={pid: life.portrait(c, pid) for pid in {f["person_id"] for f in faces if f["person_id"]}},
                place=map_places.photo_place(c, image_id, img), xmp=sidecars.packet(c, image_id) is not None,
                live=catalog.live_clip(c, image_id) if not is_video else None,
                favourite=favourites.has(w, v, image_id), in_albums=albums.containing(c, image_id),
                sharing=_share_panel(w, v, "image", image_id) if getattr(request.state, "account", None) and (
                    v.admin or any(not f["missing"] for f in files)) else None)


COUNTS_FOR = 60  # seconds tag and people counts are kept (Site.kept: a change made on the site counts them again)
FACES_FOR = 300  # seconds Cleanup → Faces keeps who the unnamed faces may be (see _faces_to_confirm)


def _stats(s: Site, v, c) -> dict:
    return s.keep(v, ("stats", ()), COUNTS_FOR, lambda: catalog.stats(c))


@router.get("/tags", response_class=HTMLResponse)
def tags_page(request: Request, q: str = "", sort: str = "count", v=Depends(viewer), c=Depends(con),
              s: Site = Depends(site)):
    """Every tag with its photos, kept a minute: the filter box asks again with each letter typed, and picks from
    the kept list (catalog.tags matches anywhere in the name, as this does)."""
    sort = "name" if sort == "name" else "count"
    every = s.keep(v, ("tags", sort), COUNTS_FOR, lambda: catalog.tags(c, "", sort, limit=None)[0])
    wanted = q.strip().lower()
    matching = [t for t in every if wanted in t["name"]] if wanted else every
    st = _stats(s, v, c)
    name = "_tag_list.html" if request.headers.get("x-requested-with") == "lineagebox" else "tags.html"
    return page(request, name, nav="tags", tags=matching[:400], total=len(matching), tag_q=q.strip(), sort=sort,
                described=st["described"], to_describe=st["to_describe"])


@router.get("/tag/{name:path}", response_class=HTMLResponse)
def tag_page(request: Request, name: str, select: bool = False, c=Depends(con)):
    name = catalog.tag_name(name)
    images = catalog.tag_images(c, name)
    if not images:
        raise HTTPException(404, "No photos have this tag")
    photos = pieces.part(request, images, f"tag:{name}", select)  # a piece at a time (pieces.py)
    if photos["w"].piece:
        return page(request, "_piece.html", p=photos)
    return page(request, "tag.html", nav="tags", tag=name, images=images, photos=photos, ctx=f"tag:{name}",
                related=catalog.related_tags(c, name), select=select,
                everything=[r["id"] for r in images] if select else None)


@router.get("/photos/undecodable", response_class=HTMLResponse)
def undecodable_page(request: Request, pg: int = Query(1, alias="page", ge=1), per: str | None = None,
                     c=Depends(con)):
    broken = catalog.undecodable(c)
    p = paging(request, "photos", pg, per, "100")
    p.total = len(broken)
    return page(request, "photos.html", nav="status", title="Could not decode",
                broken=broken[p.offset:p.offset + p.size], paging=p, page_query={})


@router.get("/photos/{value:path}", response_class=HTMLResponse)
def category_page(request: Request, value: str, select: bool = False, pg: int = Query(1, alias="page", ge=1),
                  per: str | None = None, c=Depends(con)):
    """The photos a line of the Status page counts: with GPS, videos, one format, dated one way."""
    found = catalog.category(value)
    if found is None:
        raise HTTPException(404, "No such list of photos")
    images = catalog.category_images(c, value)
    p = paging(request, "photos", pg, per, "100")
    p.total = len(images)
    return page(request, "photos.html", nav="status", title=found[0], broken=None,
                images=images[p.offset:p.offset + p.size], ctx=f"photos:{value}", paging=p,
                page_query={"select": 1} if select else {}, select=select, here=f"/photos/{quote(value)}")


@router.get("/people", response_class=HTMLResponse)
def people_page(request: Request, v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    """Everyone named; their unnamed faces wait under Cleanup → Confirm faces. The counts are kept a minute."""
    named = s.keep(v, ("people",), COUNTS_FOR, lambda: catalog.people(c))
    return page(request, "people.html", nav="people", named=named, s=_stats(s, v, c), kin=_kin_labels(c),
                circles=people.groups(c), also=people.also_known(c),
                total_groups=s.keep(v, ("face groups",), COUNTS_FOR, lambda: catalog.clusters(c, 0, 0)[1]),
                beside=s.keep(v, ("beside named",), COUNTS_FOR, lambda: people.beside_named(c)))


def _faces_to_confirm(s: Site, v, c) -> tuple[list[dict], int]:
    """Who the unnamed faces beside named people may be (people.together) and how many such faces there are. Working
    it out takes seconds on a big library, and the page reloads after each confirm, so it is kept FACES_FOR seconds
    even through changes, and what changed since is taken out of it each time: faces named, set aside or turned down
    since (here or by the worker), and faces now said not to be the person they are suggested as."""
    groups, beside = s.keep(v, ("faces to confirm",), FACES_FOR,
                            lambda: (people.together(c), people.beside_named(c)), through_changes=True)
    ids = json.dumps([f["id"] for g in groups for f in g["faces"]])
    still = {r[0] for r in c.execute("""SELECT id FROM face WHERE id IN (SELECT value FROM json_each(?))
                                        AND person_id IS NULL AND assigned_by IS NULL""", (ids,))}
    not_them = set(map(tuple, c.execute("""SELECT face_id, person_id FROM face_not_person
                                           WHERE face_id IN (SELECT value FROM json_each(?))""", (ids,))))
    out, gone = [], 0
    for g in groups:
        faces = [f for f in g["faces"] if f["id"] in still and (f["id"], g["person_id"]) not in not_them]
        gone += sum(f["id"] not in still for f in g["faces"])
        if faces:
            out.append({**g, "faces": faces})
    return out, max(0, beside - gone)


@router.get("/people/together")
def together_moved(request: Request):
    return RedirectResponse(f"/cleanup/faces?{urlencode({**request.query_params, 'show': 'together'})}", 301)


@router.get("/cleanup/faces", response_class=HTMLResponse)
def cleanup_faces_page(request: Request, show: str | None = None, pg: int = Query(1, alias="page", ge=1),
                       per: str | None = None, v=Depends(viewer), c=Depends(con), w=Depends(writer),
                       s: Site = Depends(site)):
    """Faces to confirm: who the unnamed faces beside named people are (Seen together), photos and videos with two
    or more people named and someone not yet, and the groups of faces nobody has named. Seen together comes first
    while it has faces: each takes one click. For a member, people named in a photo outside their part of the
    family are shown without a link, as on the photo's page. The page reloads after each click, so what takes a while
    is kept, through those changes: Seen together for FACES_FOR seconds (_faces_to_confirm), the counts a minute."""
    groups, beside = _faces_to_confirm(s, v, c)
    kept = lambda name, work: s.keep(v, name, COUNTS_FOR, work, through_changes=True)
    total_groups = lambda: kept(("face groups",), lambda: catalog.clusters(c, 0, 0)[1])
    finish = None if show == "finish" else kept(("to finish",), lambda: people.to_finish(c, 0, 0)[1])
    show = (show if show in ("together", "finish", "groups", *face_checks.TABS) else
            "together" if beside else "finish" if finish else "groups")
    context = {"show": show, "beside": beside, "finish": finish, "page_query": {"show": show},
               "s": kept(("stats", ()), lambda: catalog.stats(c)),
               "check_tabs": face_checks.TABS, "check_counts": face_checks.counts(s, v, c)}
    if show in face_checks.TABS:
        context.update(face_checks.tab(request, show, pg, per, c), total_groups=total_groups())
        p = context.pop("paging")
    elif show == "finish":
        p = paging(request, "finish", pg, per, "25")
        photos, p.total = people.to_finish(c, p.offset, p.size)
        outside = access.names_of(w, {pid for ph in photos for pid, name in ph["named"] if name is None})
        context.update(photos=photos, outside=outside, finish=p.total, total_groups=total_groups())
    elif show == "together":
        p = paging(request, "together", pg, per, "25")
        p.total = len(groups)
        context.update(groups=groups[p.offset:p.offset + p.size], faces=sum(len(g["faces"]) for g in groups),
                       names=dict(c.execute("SELECT id, name FROM person")),
                       covers={r["id"]: r["cover_face"] for r in kept(("people",), lambda: catalog.people(c))},
                       total_groups=total_groups())
    else:
        p = paging(request, "unnamed", pg, per, "25")
        groups, p.total = catalog.clusters(c, p.offset, p.size)
        folders = FolderIndex(c)
        taken = {r["name"].lower() for r in catalog.people(c)}
        alike = face_checks.looks_like(v, c, w, [g["id"] for g in groups])
        for g in groups:
            g["suggestion"] = people.suggest_name(folders, g["images"], taken)
            g["looks_like"] = alike.get(g["id"])
        context.update(groups=groups, total_groups=p.total)
    return page(request, "cleanup_faces.html", nav="cleanup", paging=p, **context)


@router.get("/person/{person_id}", response_class=HTMLResponse)
def person_page(request: Request, person_id: int, select: bool = False, v=Depends(viewer), c=Depends(con)):
    """Someone's page: who they are and their family, their life story, and their life by age (life.py), whose
    photos load a group at a time (person_photos). select: their photos can be ticked, to work on together."""
    p = catalog.person(c, person_id)
    if p is None:
        raise HTTPException(404, "No such person")
    g = family.load(c)
    ego = family.me(c)
    timeline = life.life(c, person_id)
    found = events.for_person(c, person_id)
    return page(request, "person.html", nav="people", person=p, timeline=timeline, select=select,
                everything=[r["id"] for r in catalog.person_images(c, person_id)] if select else None,
                photo_count=sum(x.photos for x in timeline.periods), portrait=life.portrait(c, person_id),
                kept=people.why_kept(c, person_id) if v.admin else None,
                ctx=f"person:{person_id}", auto_count=len(catalog.person_faces(c, person_id, "auto")),
                confirmed_count=len(catalog.person_faces(c, person_id, "user")),
                g=g, fam=family.links(g, person_id), theirs=family.worked_out(g, person_id), ego=ego,
                tree_root=family.tree_root(g, person_id),
                kin=family.relations_to(g, ego).get(person_id) if ego else None,
                suggested_sex=None if p["sex"] else family.suggested_sex(c, person_id),
                born_hint=face_ages.birth_hint(c, person_id),  # no birth date: when the faces say they were born
                others=c.execute("SELECT id, name FROM person WHERE id != ? ORDER BY name COLLATE NOCASE",
                                 (person_id,)).fetchall(),
                family_words=family.FAMILY_WORDS, other_words=family.OTHER_WORDS,
                life=found, other_names=events.other_names(c, person_id),
                person_events=events.PERSON_EVENTS, couple_events=events.COUPLE_EVENTS,
                attributes=events.ATTRIBUTES, name_types=events.NAME_TYPES, circles=people.person_groups(c, person_id),
                docs=documents.for_person(c, person_id), sources=documents.sources_for(c, person_id),
                source_choices=documents.source_choices(c),
                tree_media=c.execute("SELECT * FROM tree_media WHERE person_id = ? ORDER BY title, file",
                                     (person_id,)).fetchall(),
                circle_names=sorted({*people.groups(c), *people.GROUP_SUGGESTIONS}, key=str.lower))


@router.get("/person/{person_id}/life/{key}", response_class=HTMLResponse)
def person_photos(request: Request, person_id: int, key: str, skip: int = Query(0, ge=0), select: bool = False,
                  c=Depends(con)):
    """One group of someone's life by age (life.periods): its photos, PER_LOAD at a time, loaded when it is opened."""
    if catalog.person(c, person_id) is None:
        raise HTTPException(404, "No such person")
    images, total = life.photos(c, person_id, key, skip)
    return page(request, "_life_photos.html", images=images, total=total, skip=skip, key=key, person_id=person_id,
                ctx=f"person:{person_id}", per=life.PER_LOAD, select=select)


@router.get("/person/{person_id}/portraits", response_class=HTMLResponse)
def portrait_choices(request: Request, person_id: int, year: str = Query("", max_length=4), c=Depends(con)):
    """The faces their portrait can be chosen from: named as them, by hand first, clearest first; a year's only, with
    the years to pick from."""
    p = catalog.person(c, person_id)
    if p is None:
        raise HTTPException(404, "No such person")
    return page(request, "_portraits.html", person=p, faces=life.portrait_choices(c, person_id, year=year),
                years=life.portrait_years(c, person_id), year=year, portrait=life.portrait(c, person_id))


@router.get("/person/{person_id}/years", response_class=HTMLResponse)
def person_years(request: Request, person_id: int, mode: str = Query("all", pattern="^(all|portraits)$"),
                 v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    """Through the years: a face of them for each year, lined up on the eyes, to play one after another
    (face_years.py). mode: from every photo, or from portraits only; the other's number of years is shown beside it."""
    p = catalog.person(c, person_id)
    if p is None:
        raise HTTPException(404, "No such person")
    portraits = mode == "portraits"
    found = years_of(s, v, c, person_id, portraits)
    other = len(years_of(s, v, c, person_id, not portraits))
    return page(request, "person_years.html", nav="people", person=p, mode=mode, years=found,
                counts={"all": other if portraits else len(found), "portraits": len(found) if portraits else other},
                ctx=f"person:{person_id}")


@router.get("/person/{person_id}/faces", response_class=HTMLResponse)
def person_faces_page(request: Request, person_id: int, c=Depends(con)):
    p = catalog.person(c, person_id)
    if p is None:
        raise HTTPException(404, "No such person")
    auto = people.least_sure_first(catalog.person_faces(c, person_id, "auto"))
    named = people.least_typical_first(c, person_id)
    return page(request, "person_faces.html", nav="people", person=p, auto_faces=auto[:240], auto_count=len(auto),
                named_faces=named[:240], confirmed_count=len(named))


@router.get("/family", response_class=HTMLResponse)
def family_page(request: Request, root: int | None = None, center: int | None = None, v=Depends(viewer),
                c=Depends(con), s: Site = Depends(site)):
    if root is not None:  # the whole line down from one ancestor was on this page once: links to it still work
        return RedirectResponse(f"/family/tree?root={root}", status_code=303)
    g = family.load(c)
    ego = family.me(c)
    kin = family.relations_to(g, ego) if ego else {}
    groups: dict[str, list] = {}
    for pid, k in sorted(kin.items(), key=lambda kv: (kv[1].distance, g.name(kv[0]))):
        if pid != ego:
            groups.setdefault(k.group, []).append(pid)
    circles = people.groups(c)
    linked = set(kin) | {p for r in g.relations for p in r[:2]} | set(g.parents) | set(g.children)
    linked |= set(g.partners) | set(g.siblings) | {p for ids in circles.values() for p in ids}
    named = catalog.people(c)
    if center not in g.people:  # the family around you; before "This is me", around whoever has the most photos
        in_family = set(g.parents) | set(g.children) | set(g.partners) | set(g.siblings)
        center = ego if ego is not None else next((p["id"] for p in named if p["id"] in in_family), None)
    tops = family.tops(g)
    # the whole line's page starts at the top of the family of whoever is in the middle, and shows them
    line_root = family.tree_root(g, center) if center is not None else tops[0][0] if tops else None
    reading = {}
    if v.admin:  # reading into the family tree, in its panel at the top
        reading = {"readings": reading_list(c), "ai_on": s.live(c).describe_enabled,
                   "most_photos": family_reader.MOST_PHOTOS}
    return page(request, "family.html", nav="family", g=g, ego=ego, kin=kin,
                view=family.view(g, center, ego) if center is not None else None, line_root=line_root,
                groups=[(name, groups[name]) for name in family.GROUPS if name in groups],
                others=_others_by_whose(g),
                unplaced=[p for p in named if p["id"] not in linked], covers=_covers(named), circles=circles,
                names_to_check=c.execute("SELECT count(*) FROM person WHERE NOT names_checked").fetchone()[0],
                **reading)


@router.get("/family/tree", response_class=HTMLResponse)
def family_tree_page(request: Request, root: int | None = None, person: int | None = None, c=Depends(con)):
    """The whole line down from one ancestor, on a page of its own: to move around and zoom in a large family, and to
    save as one picture. person: shown first (from the Family page, the one in the middle; from a person's page,
    them)."""
    g = family.load(c)
    ego = family.me(c)
    tops = family.tops(g)
    if root not in g.people and root not in g.effective_children:
        start = person if person in g.people else ego
        root = family.tree_root(g, start) if start is not None else tops[0][0] if tops else None
    top_names = {p: family.top_name(g, p) for p in [*(t[0] for t in tops), *([root] if root is not None else [])]}
    return page(request, "family_tree.html", nav="family", g=g, ego=ego,
                kin=family.relations_to(g, ego) if ego else {}, tops=tops, root=root, top_names=top_names,
                tree=family.tree(g, root) if root is not None else None,
                person=person if person in g.people else None, covers=_covers(catalog.people(c)))
