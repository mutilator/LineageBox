"""What routes take with Depends(): the Site (site.py), who is looking, the catalog as they may see it and where their
changes go; and page() and answer(), which render through the Site's templates."""
import dataclasses

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from lineagebox import access, accounts, db, outbox
from lineagebox.web import frames, tiles
from lineagebox.web.common import PAGE_SIZES, SEE_AS, Paging
from lineagebox.web.site import Site

NEWS_KEPT = 30  # seconds the count of what is new for someone is kept (at once anew after a change on the site)


def site(request: Request) -> Site:
    return request.app.state.site


def viewer(request: Request) -> access.Viewer:
    """Who is looking. Everyone sees everything while accounts are off. With them on: nobody before signing in, an
    admin everything (or, seeing as a member, what that member sees), a member their own part of the family. A photo
    frame's device link (the gate found one in the address) sees only the photos on its frame, whatever else is on."""
    s = site(request)
    account = getattr(request.state, "account", None)
    if (link := getattr(request.state, "device", None)) is not None:
        v = frames.viewer_for(s, link)
    elif account is None:
        v = access.NOBODY if s.shared_cfg().accounts_enabled else access.EVERYONE
    elif account["role"] == "admin":
        v = access.admin(account)
        see = request.cookies.get(SEE_AS, "")
        if see.isdecimal():
            with s.own_con() as c:
                other = accounts.get(c, int(see))
                if other is not None and other["role"] == "member" and other["disabled_at"] is None:
                    v = dataclasses.replace(s.viewers.get(c, dict(other)), seeing_as=True)
    elif (v := s.viewers.fresh(account)) is None:
        with s.own_con() as c:
            v = s.viewers.get(c, account)
    request.state.viewer = v
    return v


def con(request: Request, v: access.Viewer = Depends(viewer)):
    """The catalog as the viewer may see it: a member's holds only their part of the family (access.scope)."""
    with site(request).own_con() as c:
        request.state.catalog = scoped = access.scope(c, v)  # also what the grids' alt text is read on (tiles.py)
        yield scoped


def writer(request: Request, c=Depends(con), v: access.Viewer = Depends(viewer)):
    """Where a change is written. A member's own connection is all views: their changes are first checked against
    what they see (by each route), then written on one that sees everything, so that what follows from them (the
    search index, names in videos) is worked out from everything, as for an admin."""
    if v.admin:
        yield c
        return
    w = db.connect(site(request).cfg, migrate_schema=False, check_same_thread=False)
    w.me = v.me
    try:
        yield w
    finally:
        w.close()


def page(request: Request, name: str, **context) -> HTMLResponse:
    """A page from templates/ (each has help, in help.PAGES), with what every page shows: who is signed in, and for
    admins whether a newer release is out."""
    s = site(request)
    context.setdefault("help_page", name.removesuffix(".html"))  # which page the help panel is about
    context.setdefault("viewer", getattr(request.state, "viewer", access.EVERYONE))
    context.setdefault("signed_in", getattr(request.state, "account", None))
    context.setdefault("signed_out", s.shared_cfg().accounts_enabled and context["signed_in"] is None)
    context.setdefault("hover_preview_ms", s.shared_cfg().hover_preview_ms)  # for app.js, on <body>
    # a demo (demo.py): the note at the top of every page says so, and when it all goes back as it was
    context.setdefault("demo_resets", (s.cfg.demo_resets.strip() or "every day") if s.cfg.demo_enabled else "")
    if context["viewer"].admin:
        context.setdefault("update_ready", s.update_ready())
    if not context["signed_out"] and context["viewer"].device is None:  # the count by New for you, at the top
        context.setdefault("news_count", lambda: news_count(s, context["viewer"]))
    with tiles.reading(getattr(request.state, "catalog", None)):  # who is in the grids' photos, as the viewer sees
        response = s.templates.TemplateResponse(request, name, context)
    if (paging := context.get("paging")) and paging.chosen:  # remembered for this list
        response.set_cookie(f"per_{paging.key}", paging.per, max_age=365 * 86400, samesite="lax",
                            secure=request.url.scheme == "https")
    return response


def _news_key(v: access.Viewer) -> tuple:
    return ("news count", v.account_id or 0, v.admin, v.seeing_as)


def news_count(s: Site, v: access.Viewer) -> int:
    """How many events are new to the viewer (outbox.unseen), for the count at the top of every page: kept NEWS_KEPT
    seconds, and only worked out by pages that show it (a part of a page doesn't)."""
    def work():
        with s.own_con() as c:
            return outbox.unseen(c, v)
    return s.kept.get(_news_key(v), NEWS_KEPT, work)


def forget_news_count(s: Site, v: access.Viewer) -> None:
    """They have just seen what is new: the count is worked out again on their next page."""
    with s.kept.lock:
        s.kept.kept.pop(_news_key(v), None)


def paging(request: Request, key: str, pg: int, per: str | None, default: str) -> Paging:
    """Which slice of a long list to show: `per` when it was just picked, else the size last picked for this list, else
    the default."""
    if per in PAGE_SIZES:
        return Paging(key, pg, per, chosen=True)
    remembered = request.cookies.get(f"per_{key}")
    return Paging(key, pg, remembered if remembered in PAGE_SIZES else default)


def answer(request: Request, status: int, message: str) -> Response:
    """An error: as JSON for the site's own scripts (app.js shows the message), and for someone who opened a page (a
    link to a photo that is gone, or outside their part of the family) a page that says it, with the way back."""
    if request.url.path.startswith("/api/") or request.headers.get("x-requested-with") or request.method != "GET":
        return JSONResponse({"detail": message}, status_code=status)
    try:
        who = getattr(request.state, "viewer", None) or (access.NOBODY if site(request).shared_cfg().accounts_enabled
                                                         else access.EVERYONE)
        response = page(request, "error.html", viewer=who, status=status, message=message)
        response.status_code = status
        return response
    except Exception:  # the page itself can't be made: the words are enough
        return PlainTextResponse(message, status_code=status)
