"""The Status page, updates, the copies of the catalog, and starting a worker pass."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from lineagebox import __version__, backup, catalog, progress, settings, storage, updates
from lineagebox.web.common import ADMIN, IN_DOCKER, _from_app
from lineagebox.web.deps import con, page, site, viewer
from lineagebox.web.site import SLOW_PAGE, Site

router = APIRouter()
ACCESS = {
    "/status": ADMIN,
    "/api/update": ADMIN,
    "/api/update/check": ADMIN,
    "/status/live": ADMIN,
    "/api/worker/run": ADMIN,
}


STATUS_COUNTS_FOR = 30  # seconds the counts of what is left are kept: the page asks every 10, and they take a while
STATS_FOR = 60  # seconds the catalog's counts are kept (Site.kept: any change made on the site counts them again)


def _snapshot(s: Site, v, c, now):
    """What the worker is doing (always now), with the counts of what is left kept a while, through changes: only
    the worker changes them."""
    kept = s.keep(v, ("status counts",), STATUS_COUNTS_FOR, lambda: progress.counts(c, now), through_changes=True)
    return progress.snapshot(c, now, kept)


@router.get("/status", response_class=HTMLResponse)
def status_page(request: Request, v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    now = s.live(c)
    return page(request, "status.html", s=s.keep(v, ("stats", now.folders), STATS_FOR,
                                                  lambda: catalog.stats(c, now.folders)),
                cfg=now, p=_snapshot(s, v, c, now), whisper=s.whisper_health(now), date_labels=catalog.DATE_SOURCES,
                date_help=catalog.DATE_HELP, release=updates.available(c), last_check=settings.get(c, updates.KEY),
                updater=updates.updater_state(s.cfg), in_docker=IN_DOCKER, slow=list(s.slow), slow_after=SLOW_PAGE,
                disks=s.keep(v, ("storage",), 60, lambda: storage.disks(c, now)), backups=backup.summary(c, now))


@router.get("/api/update")
def update_status(s: Site = Depends(site)):
    """Which version answers, and how an update is going: the Status page follows it while LineageBox restarts."""
    return {"version": __version__, "updater": updates.updater_state(s.cfg)}


@router.post("/api/update", dependencies=[Depends(_from_app)])
def start_update(s: Site = Depends(site)):
    state = updates.updater_state(s.cfg)
    if not state or not state["alive"]:
        raise HTTPException(409, "The updater isn't running here, so LineageBox can't update itself: see the steps "
                                 "under Version and updates.")
    if state["state"] == "updating" or state["requested"]:
        return {"message": "An update is already on its way."}
    updates.request_update(s.cfg)
    s.release.update(at=-1e9)  # told again once the new version answers
    return {"message": "Updating: LineageBox downloads the newest version and restarts with it."}


@router.post("/api/update/check", dependencies=[Depends(_from_app)])
def check_for_update(c=Depends(con), s: Site = Depends(site)):
    now = s.live(c)
    if not now.update_check:
        raise HTTPException(409, "Checking for updates is off: switch it on under Settings → Updates.")
    if not now.update_repository:
        raise HTTPException(409, "This copy of LineageBox doesn't know where its releases are published "
                                 "([updates] repository in the config).")
    found = updates.check(c, now, force=True) or {}
    s.release.update(at=-1e9)
    if found.get("problem"):
        return {"message": f"Couldn't check: {found['problem']}"}
    return {"message": f"LineageBox {found['version']} is out." if updates.newer(found.get("version", "")) else
            "This is the newest version.", "reload": True}


@router.get("/status/live", response_class=HTMLResponse)
def status_live(request: Request, v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    now = s.live(c)
    return page(request, "_progress.html", cfg=now, p=_snapshot(s, v, c, now), whisper=s.whisper_health(now))


@router.post("/api/worker/run", dependencies=[Depends(_from_app)])
def run_worker(c=Depends(con)):
    settings.put(c, "run_requested", datetime.now().isoformat(timespec="seconds"))
    return {"message": "The worker starts a pass within 15 seconds."}
