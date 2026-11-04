"""Cleanup: what could go (copies, similar shots, blurry, papers), damaged files, members' changes and people
added twice, Removed, Kept."""
import json
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from lineagebox import (accounts, catalog, changes, check, cleanup, family, family_check, orientation, people,
                        settings, writeback)
from lineagebox.web.common import ADMIN, MEMBER, ImageIds, SameBody, UndoSinceBody, _from_app, _moved
from lineagebox.web.deps import con, page, paging, site, viewer
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/cleanup": MEMBER,
    "/cleanup/turned": ADMIN,
    "/cleanup/family": MEMBER,
    "/cleanup/damaged": ADMIN,
    "/api/damaged/remove": ADMIN,
    "/api/damaged/ok": ADMIN,
    "/cleanup/changes": ADMIN,
    "/api/changes/{change_id}/undo": ADMIN,
    "/api/changes/undo-since": ADMIN,
    "/cleanup/same": ADMIN,
    "/api/same/merge": ADMIN,
    "/api/same/different": ADMIN,
    "/cleanup/copies": ADMIN,
    "/cleanup/similar": ADMIN,
    "/api/similar/remove-all": ADMIN,
    "/cleanup/blurry": ADMIN,
    "/cleanup/reference/{kind}": ADMIN,
    "/cleanup/removed": ADMIN,
    "/cleanup/kept": ADMIN,
    "/api/cleanup/copies": ADMIN,
}


@router.get("/cleanup", response_class=HTMLResponse)
def cleanup_page(request: Request, v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    now = s.live(c)
    if v.limited:  # members: faces to name and their part of the family tree; the rest moves files
        return page(request, "cleanup.html", s=None, stats=catalog.stats(c), family_issues=len(family_check.check(c)),
                    face_groups=catalog.clusters(c, 0, 0)[1], beside=people.beside_named(c))
    return page(request, "cleanup.html", s=cleanup.summary(c, now.blurry_below),
                recent_changes=c.execute("SELECT count(*) FROM change_log WHERE undone_at IS NULL").fetchone()[0],
                widened=c.execute("SELECT count(*) FROM change_log WHERE widened IS NOT NULL AND undone_at IS NULL")
                .fetchone()[0],
                same=len(people.maybe_same(c, set(settings.get(c, people.SAME_OK_KEY, [])))),
                damage=check.progress(c),
                labels=cleanup.REFERENCE_LABELS, stats=catalog.stats(c), watched=now.folders,
                family_issues=len(family_check.check(c)), turned=len(orientation.suggestions(c, now.turn_suggest)),
                turn_suggest=now.turn_suggest, face_groups=catalog.clusters(c, 0, 0)[1],
                beside=people.beside_named(c))


@router.get("/cleanup/turned", response_class=HTMLResponse)
def cleanup_turned_page(request: Request, c=Depends(con), s: Site = Depends(site)):
    now = s.live(c)
    found = orientation.suggestions(c, now.turn_suggest)
    rows = {r["id"]: r for r in c.execute(f"""SELECT id, sha256, taken_at, format, media FROM image
                                              WHERE id IN ({','.join('?' * len(found))})""", [s["id"] for s in found])}
    for s in found:
        s["image"] = rows[s["id"]]
        s["problem"] = writeback.why_not(rows[s["id"]])
    return page(request, "cleanup_turned.html", nav="cleanup", sure=[s for s in found if not s["doubt"]],
                unsure=[s for s in found if s["doubt"]], level=now.turn_suggest,
                ok_count=len(settings.get(c, orientation.OK_KEY, [])))


@router.get("/cleanup/family", response_class=HTMLResponse)
def cleanup_family_page(request: Request, c=Depends(con)):
    issues = defaultdict(list)
    for issue in family_check.check(c):
        issues[issue.kind].append(issue)
    return page(request, "cleanup_family.html", nav="cleanup", issues=issues, kinds=family_check.KINDS,
                g=family.load(c), ok_count=len(settings.get(c, family_check.OK_KEY, [])))


@router.get("/cleanup/damaged", response_class=HTMLResponse)
def damaged_page(request: Request, c=Depends(con)):
    """Files a disk spoilt (check.py), to remove or to keep; how far the checking is."""
    return page(request, "cleanup_damaged.html", nav="cleanup", files=check.damaged(c), p=check.progress(c))


@router.post("/api/damaged/remove", dependencies=[Depends(_from_app)])
def remove_damaged(body: ImageIds, c=Depends(con), s: Site = Depends(site)):
    """The ticked damaged files (their file ids, sent as image_ids by the tick-photos form) to the Removed folder,
    the only copy of a photo too: a damaged copy is no copy."""
    ids = [r[0] for r in c.execute("""SELECT id FROM file WHERE damage IS NOT NULL AND NOT missing
                                       AND id IN (SELECT value FROM json_each(?))""", (json.dumps(body.image_ids),))]
    return _moved(cleanup.remove_files(c, s.live(c), ids, body.reason, keep_last_copy=False))


@router.post("/api/damaged/ok", dependencies=[Depends(_from_app)])
def damaged_ok(body: ImageIds, c=Depends(con)):
    """Looked at and fine as they are: not listed, nor checked again."""
    check.mark_fine(c, body.image_ids)
    return {}


@router.get("/cleanup/changes", response_class=HTMLResponse)
def recent_changes_page(request: Request, c=Depends(con)):
    """What family members changed, newest first, with undo; those that let someone see more are marked."""
    rows = changes.recent(c)
    return page(request, "cleanup_changes.html", nav="cleanup", rows=rows,
                widened=sum(1 for r in rows if r["widened"] and not r["undone_at"]),
                emails={a["id"]: a["email"] for a in accounts.everyone(c)})


@router.post("/api/changes/{change_id}/undo", dependencies=[Depends(_from_app)])
def undo_change(change_id: int, c=Depends(con)):
    try:
        summary = changes.undo(c, change_id)
    except changes.Conflict as e:
        raise HTTPException(409, str(e)) from None
    return {"message": f"Undone: {summary}", "reload": True}


@router.post("/api/changes/undo-since", dependencies=[Depends(_from_app)])
def undo_changes_since(body: UndoSinceBody, c=Depends(con)):
    done, skipped = changes.undo_since(c, body.account_id, body.since)
    return {"message": f"Undid {done} change{'' if done == 1 else 's'}."
                       + (f" {len(skipped)} were changed again since and left as they are." if skipped else ""),
            "reload": True}


@router.get("/cleanup/same", response_class=HTMLResponse)
def same_person_page(request: Request, c=Depends(con)):
    """People family members added who may already be in the catalog: merge them, link them, or leave them."""
    found = people.maybe_same(c, set(settings.get(c, people.SAME_OK_KEY, [])))
    return page(request, "cleanup_same.html", nav="cleanup", found=found,
                emails={a["id"]: (a["person_name"] or a["email"]) for a in accounts.everyone(c)})


@router.post("/api/same/merge", dependencies=[Depends(_from_app)])
def merge_same(body: SameBody, c=Depends(con)):
    """The same person: the one a member added goes into the other. keep_seeing: the member who added them goes
    on seeing them (an exception on their account), whether they are family or a friend from another tree."""
    added = catalog.person(c, body.added)
    if added is None or catalog.person(c, body.other) is None or body.added == body.other:
        raise HTTPException(404, "No such person")
    if body.keep_seeing and added["added_by"]:
        c.execute("INSERT OR IGNORE INTO access_rule(account_id, person_id, show, branch) VALUES (?, ?, 1, 0)",
                  (added["added_by"], body.other))
    people.merge(c, body.added, body.other)
    c.commit()
    return {"reload": True}


@router.post("/api/same/different", dependencies=[Depends(_from_app)])
def not_the_same(body: SameBody, c=Depends(con)):
    pairs = settings.get(c, people.SAME_OK_KEY, [])
    pair = f"{min(body.added, body.other)}-{max(body.added, body.other)}"
    settings.put(c, people.SAME_OK_KEY, sorted({*pairs, pair}))
    return {}


@router.get("/cleanup/copies", response_class=HTMLResponse)
def copies_page(request: Request, pg: int = Query(1, alias="page", ge=1), per: str | None = None,
                c=Depends(con)):
    p = paging(request, "copies", pg, per, "25")
    pairs, p.total = cleanup.copy_pairs(c, p.offset, p.size)
    return page(request, "cleanup_copies.html", pairs=pairs, total=p.total, paging=p)


@router.get("/cleanup/similar", response_class=HTMLResponse)
def similar_page(request: Request, kind: str = "all", keep: str = "raw", pg: int = Query(1, alias="page", ge=1),
                 per: str | None = None, c=Depends(con)):
    if kind not in cleanup.SIMILAR_KINDS:
        raise HTTPException(404)
    p = paging(request, "similar", pg, per, "25")
    groups, p.total = cleanup.similar_groups(c, kind, keep != "jpeg", p.offset, p.size)
    return page(request, "cleanup_similar.html", groups=groups, total=p.total, paging=p,
                kind=kind, keep=keep, kinds=cleanup.SIMILAR_KINDS,
                hint=cleanup.SIMILAR_HINTS[kind], counts=cleanup.similar_counts(c),
                bulk=kind in cleanup._COPY_KINDS,
                ticked=len(cleanup.similar_ticked(c, kind, keep != "jpeg")) if kind in cleanup._COPY_KINDS else 0,
                page_query={"kind": kind, "keep": keep} if kind == "raw" else {"kind": kind})


@router.post("/api/similar/remove-all", dependencies=[Depends(_from_app)])
def remove_all_similar(kind: str = Query(), keep: str = "raw", c=Depends(con), s: Site = Depends(site)):
    if kind not in cleanup._COPY_KINDS:
        raise HTTPException(400, "Only copies of one picture can be removed from every group at once.")
    return _moved(cleanup.remove_similar(c, s.live(c), kind, keep != "jpeg"))


@router.get("/cleanup/blurry", response_class=HTMLResponse)
def blurry_page(request: Request, pg: int = Query(1, alias="page", ge=1), per: str | None = None,
                c=Depends(con), s: Site = Depends(site)):
    p = paging(request, "blurry", pg, per, "100")
    below = s.live(c).blurry_below
    images, p.total = cleanup.blurry(c, below, p.offset, p.size)
    return page(request, "cleanup_list.html", title="Blank and blurry", images=images, total=p.total, paging=p,
                reason="blank or blurry", kind="blurry",
                unmeasured=cleanup.summary(c, below)["unmeasured"])


@router.get("/cleanup/reference/{kind}", response_class=HTMLResponse)
def reference_page(request: Request, kind: str, pg: int = Query(1, alias="page", ge=1), per: str | None = None,
                   c=Depends(con)):
    if kind not in cleanup.REFERENCE:
        raise HTTPException(404)
    p = paging(request, "reference", pg, per, "100")
    images = cleanup.reference_photos(c)[kind]
    p.total = len(images)
    s = catalog.stats(c)
    return page(request, "cleanup_list.html", title=cleanup.REFERENCE_LABELS[kind],
                images=images[p.offset:p.offset + p.size], total=p.total, paging=p,
                reason=cleanup.REFERENCE_LABELS[kind].lower(), kind=kind,
                phrases=cleanup.REFERENCE[kind], described=s["described"], to_describe=s["to_describe"])


@router.get("/cleanup/removed", response_class=HTMLResponse)
def removed_page(request: Request, c=Depends(con), s: Site = Depends(site)):
    batches = cleanup.removed_batches(c)
    return page(request, "cleanup_removed.html", batches=batches, total=sum(b["files"] for b in batches),
                watched=s.live(c).folders)


@router.get("/cleanup/kept", response_class=HTMLResponse)
def kept_page(request: Request, c=Depends(con)):
    images, pairs = cleanup.kept(c)
    return page(request, "cleanup_kept.html", images=images, pairs=pairs)


@router.post("/api/cleanup/copies", dependencies=[Depends(_from_app)])
def remove_copies(remove_from: int = Query(), keep_in: int = Query(), c=Depends(con), s: Site = Depends(site)):
    return _moved(cleanup.remove_copies(c, s.live(c), remove_from, keep_in))
