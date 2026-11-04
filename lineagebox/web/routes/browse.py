"""Pages to look around by: folders, years, search, a photo, tags, people, a person, the family."""
import json
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from lineagebox import access, catalog, copies, documents, events, family, people, search, tagging, tours, writeback
from lineagebox.folders import FolderIndex
from lineagebox.web.common import ADMIN, MEMBER, _covers, _kin_labels, _others_by_whose, _within
from lineagebox.web.deps import con, page, paging, site, viewer, writer
from lineagebox.web.routes.home import home_page
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/": MEMBER,
    "/folder/{folder_id}": MEMBER,
    "/timeline": MEMBER,
    "/timeline/{year}": MEMBER,
    "/search": MEMBER,
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
    "/family": MEMBER,
}


@router.get("/", response_class=HTMLResponse)
def home(request: Request, v=Depends(viewer), c=Depends(con)):
    if v.limited:  # members start on Home: their Folders hold only their uploads
        return home_page(request, v, c)
    root = catalog.root_folder_id(c)
    if root is None:
        return page(request, "empty.html")
    tree = catalog.folder_tree(c)
    folder = tree[root]
    while not folder.own and len(shown := [f for f in folder.children if f.total]) == 1:
        folder = shown[0]  # one watched folder: start in it rather than at the share
    return folder_page(request, folder.id, v=v, c=c)


@router.get("/folder/{folder_id}", response_class=HTMLResponse)
def folder_page(request: Request, folder_id: int, select: bool = False, v=Depends(viewer), c=Depends(con)):
    tree = catalog.folder_tree(c)
    folder = tree.get(folder_id)
    uploads = v.account["upload_folder"] if v.limited else None
    if folder is None or v.limited and not _within(folder.path, uploads):
        raise HTTPException(404, "No such folder")  # members browse only their own uploads
    crumbs = []
    node = folder
    while node is not None and (v.admin or _within(node.path, uploads)):
        crumbs.insert(0, node)
        node = tree.get(node.parent_id)
    return page(request, "folder.html", folder=folder, crumbs=crumbs,
                subfolders=[f for f in folder.children if f.total or f.ignored],
                images=catalog.folder_images(c, folder_id), ctx=f"folder:{folder_id}", select=select,
                folder_hidden=bool(c.execute("SELECT hidden FROM folder WHERE id = ?", (folder_id,)).fetchone()[0]))


@router.get("/timeline", response_class=HTMLResponse)
def timeline_page(request: Request, c=Depends(con)):
    years = catalog.timeline(c)
    peak = max((n for months in years.values() for n in months.values()), default=1)
    return page(request, "timeline.html", years=years, peak=peak)


@router.get("/timeline/{year}", response_class=HTMLResponse)
def year_page(request: Request, year: int, c=Depends(con)):
    months: dict[int, list] = {}
    for row in catalog.year_images(c, year):
        months.setdefault(int(row["taken_at"][5:7]), []).append(row)
    years = sorted(catalog.timeline(c))
    i = years.index(year) if year in years else -1
    return page(request, "year.html", year=year, months=months, ctx=f"year:{year}",
                prev_year=years[i - 1] if i > 0 else None,
                next_year=years[i + 1] if 0 <= i < len(years) - 1 else None)


@router.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = "", select: bool = False, pg: int = Query(1, alias="page", ge=1),
                per: str | None = None, c=Depends(con)):
    """The people a search is about, then its photos, a page at a time."""
    limit = 5000
    s = catalog.stats(c) if q else {"described": 0, "to_describe": 0}
    results = search.query(c, q, limit) if q else []
    p = paging(request, "search", pg, per, "100")
    p.total = len(results)
    found = search.people_matching(c, q) if q else []
    named = {r["id"]: r for r in catalog.people(c)}
    names = dict(c.execute("SELECT id, name FROM person"))
    cards = [{"id": pid, "name": names[pid], "cover_face": named[pid]["cover_face"] if pid in named else None,
              "photos": named[pid]["photos"] if pid in named else 0} for pid in found]
    return page(request, "search.html", q=q, results=results[p.offset:p.offset + p.size], total=len(results),
                limit=limit, ctx=f"search:{q}", described=s["described"], to_describe=s["to_describe"],
                select=select, paging=p, page_query={"q": q, **({"select": 1} if select else {})},
                people_found=cards, kin=_kin_labels(c))


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
        ids = [p["id"] for p in tours.photos(c, tour["spec"], s.live(c).blurry_below, seed=tour["id"])]
    else:
        ids = search.context_ids(c, ctx) if ctx else []
    pos = ids.index(image_id) if image_id in ids else -1
    back = "/"
    if tour:
        back = f"/tour/{tour['id']}"
    elif ctx.startswith("folder:"):
        back = f"/folder/{ctx[7:]}"
    elif ctx.startswith("year:"):
        back = f"/timeline/{ctx[5:]}#m{int(img['taken_at'][5:7]) if img['taken_at'] else ''}"
    elif ctx.startswith("search:"):
        back = "/search?" + urlencode({"q": ctx[7:]})
    elif ctx.startswith("tag:"):
        back = "/tag/" + quote(ctx[4:])
    elif ctx.startswith("photos:"):
        back = "/photos/" + quote(ctx[7:])
    elif files:
        back = f"/folder/{files[0]['folder_id']}"
    next_id = ids[pos + 1] if 0 <= pos < len(ids) - 1 else None
    analysis = catalog.analysis_for(c, image_id)
    is_video = img["media"] == "video"
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
    suggested = {f: (pid, names[pid]) for f, (pid, _) in people.suggest_for_photo(w, image_id).items()
                 if pid in names}
    return page(request, "image.html", img=img, files=files, ctx=ctx, back=back, is_video=is_video,
                frames=catalog.video_frames(c, image_id) if is_video else [],
                copy=copies.find_copy(c, s.cfg, image_id) if is_video and img["playable"] == "copy" else None,
                transcript=catalog.transcript(c, image_id) if is_video else None,
                position=(pos + 1, len(ids)) if pos >= 0 else None,
                prev_id=ids[pos - 1] if pos > 0 else None, next_id=next_id,
                next_sha=catalog.image(c, next_id)["sha256"] if next_id else None,
                ai=analysis, ai_raw=json.loads(analysis["raw_json"]) if analysis else {},
                folder_ctx=FolderIndex(c).context(image_id), similar=catalog.group_members(c, image_id),
                faces=faces, kin=_kin_labels(c), tags=tagging.for_image(c, image_id), linked=linked,
                ages=events.ages(c, img, [f["person_id"] for f in faces] + [p["id"] for p in linked]),
                doc=doc, doc_kinds=documents.KINDS, citations=documents.citations(c, image_id) if doc else [],
                cite_targets=documents.targets(c) if doc else [], suggested=suggested,
                turn_problem=writeback.why_not(img), mine=v.admin or any(not f["missing"] for f in files),
                download=download)


@router.get("/tags", response_class=HTMLResponse)
def tags_page(request: Request, q: str = "", sort: str = "count", c=Depends(con)):
    tags, total = catalog.tags(c, q.strip(), sort)
    s = catalog.stats(c)
    name = "_tag_list.html" if request.headers.get("x-requested-with") == "lineagebox" else "tags.html"
    return page(request, name, nav="tags", tags=tags, total=total, tag_q=q.strip(), sort=sort,
                described=s["described"], to_describe=s["to_describe"])


@router.get("/tag/{name:path}", response_class=HTMLResponse)
def tag_page(request: Request, name: str, select: bool = False, c=Depends(con)):
    name = catalog.tag_name(name)
    images = catalog.tag_images(c, name)
    if not images:
        raise HTTPException(404, "No photos have this tag")
    return page(request, "tag.html", nav="tags", tag=name, images=images, ctx=f"tag:{name}",
                related=catalog.related_tags(c, name), select=select)


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
def people_page(request: Request, c=Depends(con)):
    """Everyone named; their unnamed faces wait under Cleanup → Confirm faces."""
    return page(request, "people.html", nav="people", named=catalog.people(c), s=catalog.stats(c),
                kin=_kin_labels(c), circles=people.groups(c), also=people.also_known(c),
                total_groups=catalog.clusters(c, 0, 0)[1], beside=people.beside_named(c))


@router.get("/people/together")
def together_moved(request: Request):
    return RedirectResponse(f"/cleanup/faces?{urlencode({**request.query_params, 'show': 'together'})}", 301)


@router.get("/cleanup/faces", response_class=HTMLResponse)
def cleanup_faces_page(request: Request, show: str | None = None, pg: int = Query(1, alias="page", ge=1),
                       per: str | None = None, c=Depends(con), w=Depends(writer)):
    """Faces to confirm: who the unnamed faces beside named people are (Seen together), photos and videos with two
    or more people named and someone not yet, and the groups of faces nobody has named. Seen together comes first
    while it has faces: each takes one click. For a member, people named in a photo outside their part of the
    family are shown without a link, as on the photo's page."""
    beside = people.beside_named(c)
    finish = None if show == "finish" else people.to_finish(c, 0, 0)[1]  # on its own tab, counted with its page
    show = (show if show in ("together", "finish", "groups") else
            "together" if beside else "finish" if finish else "groups")
    context = {"show": show, "beside": beside, "finish": finish, "page_query": {"show": show},
               "s": catalog.stats(c)}
    if show == "finish":
        p = paging(request, "finish", pg, per, "25")
        photos, p.total = people.to_finish(c, p.offset, p.size)
        outside = access.names_of(w, {pid for ph in photos for pid, name in ph["named"] if name is None})
        context.update(photos=photos, outside=outside, finish=p.total, total_groups=catalog.clusters(c, 0, 0)[1])
    elif show == "together":
        groups = people.together(c)
        p = paging(request, "together", pg, per, "25")
        p.total = len(groups)
        context.update(groups=groups[p.offset:p.offset + p.size], faces=sum(len(g["faces"]) for g in groups),
                       names=dict(c.execute("SELECT id, name FROM person")),
                       covers={r["id"]: r["cover_face"] for r in catalog.people(c)},
                       total_groups=catalog.clusters(c, 0, 0)[1])
    else:
        p = paging(request, "unnamed", pg, per, "25")
        groups, p.total = catalog.clusters(c, p.offset, p.size)
        folders = FolderIndex(c)
        taken = {r["name"].lower() for r in catalog.people(c)}
        for g in groups:
            g["suggestion"] = people.suggest_name(folders, g["images"], taken)
        context.update(groups=groups, total_groups=p.total)
    return page(request, "cleanup_faces.html", nav="cleanup", paging=p, **context)


@router.get("/person/{person_id}", response_class=HTMLResponse)
def person_page(request: Request, person_id: int, c=Depends(con)):
    p = catalog.person(c, person_id)
    if p is None:
        raise HTTPException(404, "No such person")
    g = family.load(c)
    ego = family.me(c)
    return page(request, "person.html", nav="people", person=p, images=catalog.person_images(c, person_id),
                ctx=f"person:{person_id}", auto_count=len(catalog.person_faces(c, person_id, "auto")),
                confirmed_count=len(catalog.person_faces(c, person_id, "user")),
                g=g, fam=family.links(g, person_id), theirs=family.worked_out(g, person_id), ego=ego,
                tree_root=family.tree_root(g, person_id),
                kin=family.relations_to(g, ego).get(person_id) if ego else None,
                suggested_sex=None if p["sex"] else family.suggested_sex(c, person_id),
                others=c.execute("SELECT id, name FROM person WHERE id != ? ORDER BY name COLLATE NOCASE",
                                 (person_id,)).fetchall(),
                family_words=family.FAMILY_WORDS, other_words=family.OTHER_WORDS,
                life=events.for_person(c, person_id), other_names=events.other_names(c, person_id),
                person_events=events.PERSON_EVENTS, couple_events=events.COUPLE_EVENTS,
                name_types=events.NAME_TYPES, circles=people.person_groups(c, person_id),
                docs=documents.for_person(c, person_id),
                circle_names=sorted({*people.groups(c), *people.GROUP_SUGGESTIONS}, key=str.lower))


@router.get("/person/{person_id}/faces", response_class=HTMLResponse)
def person_faces_page(request: Request, person_id: int, c=Depends(con)):
    p = catalog.person(c, person_id)
    if p is None:
        raise HTTPException(404, "No such person")
    auto = catalog.person_faces(c, person_id, "auto")
    named = people.least_typical_first(c, person_id)
    return page(request, "person_faces.html", nav="people", person=p, auto_faces=auto[:240], auto_count=len(auto),
                named_faces=named[:240], confirmed_count=len(named))


@router.get("/family", response_class=HTMLResponse)
def family_page(request: Request, root: int | None = None, center: int | None = None, c=Depends(con)):
    g = family.load(c)
    ego = family.me(c)
    kin = family.relations_to(g, ego) if ego else {}
    tops = family.tops(g)
    if root not in g.people and root not in g.effective_children:
        root = family.tree_root(g, ego) if ego else tops[0][0] if tops else None
    groups: dict[str, list] = {}
    for pid, k in sorted(kin.items(), key=lambda kv: (kv[1].distance, g.name(kv[0]))):
        if pid != ego:
            groups.setdefault(k.group, []).append(pid)
    circles = people.groups(c)
    linked = set(kin) | {p for r in g.relations for p in r[:2]} | set(g.parents) | set(g.children)
    linked |= set(g.partners) | set(g.siblings) | {p for ids in circles.values() for p in ids}
    named = catalog.people(c)
    top_names = {p: family.top_name(g, p) for p in [*(t[0] for t in tops), *([root] if root is not None else [])]}
    if center not in g.people:  # the family around you; before "This is me", around whoever has the most photos
        in_family = set(g.parents) | set(g.children) | set(g.partners) | set(g.siblings)
        center = ego if ego is not None else next((p["id"] for p in named if p["id"] in in_family), None)
    return page(request, "family.html", nav="family", g=g, ego=ego, kin=kin, tops=tops, root=root,
                top_names=top_names, view=family.view(g, center, ego) if center is not None else None,
                whole_tree=request.query_params.get("root") is not None,
                tree=family.tree(g, root) if root is not None else None,
                groups=[(name, groups[name]) for name in family.GROUPS if name in groups],
                others=_others_by_whose(g),
                unplaced=[p for p in named if p["id"] not in linked], covers=_covers(named), circles=circles,
                names_to_check=c.execute("SELECT count(*) FROM person WHERE NOT names_checked").fetchone()[0])
