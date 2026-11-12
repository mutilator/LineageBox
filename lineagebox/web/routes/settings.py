"""The Settings page: what it saves, network paths, addresses and HTTPS, watched folders, Ollama and Whisper, and
copies of the catalog (Back up now, the export)."""
import os
import tempfile
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse
from starlette.background import BackgroundTask

from lineagebox import backup, db, export, https, llm, passkeys, serving, settings, speech, tours
from lineagebox import faces, imports, sidecars, writeback
from lineagebox.llm import Ollama
from lineagebox.storage import fmt_size
from lineagebox.web.common import (ADMIN, PRIVATE, FolderBody, _attachment, _cert_name, _from_app, _host_allowed,
                                   _net_folders, _photos_per_watched)
from lineagebox.web.deps import con, page, site
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/settings": ADMIN,
    "/api/settings": ADMIN,
    "/api/settings/network-paths": ADMIN,
    "/api/settings/address": ADMIN,
    "/api/https": ADMIN,
    "/api/https/upload": ADMIN,
    "/api/https/off": ADMIN,
    "/api/folders/watch": ADMIN,
    "/api/folders/unwatch": ADMIN,
    "/api/ollama/check": ADMIN,
    "/api/ollama/pull": ADMIN,
    "/api/whisper/check": ADMIN,
    "/api/backup": ADMIN,
    "/download/export": ADMIN,
}


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, browse: str = "", c=Depends(con), s: Site = Depends(site)):
    now = s.live(c)
    browse = browse.replace("\\", "/").strip("/")
    if browse and (".." in browse.split("/") or ":" in browse.split("/")[0]  # a drive (C:) is outside the share
                   or not (now.share_root / browse).is_dir()):
        browse = ""
    cert = https.about(now)
    host = request.headers.get("host", "")
    return page(request, "settings.html", nav="settings", cfg=now, counts=_photos_per_watched(c, now.folders),
                browse=browse, crumbs=browse.split("/") if browse else [],
                subfolders=settings.subfolders(now, browse), data_dir=s.cfg.data_dir, tour_types=tours.types(c),
                sources=settings.sources(s.cfg), problems=settings.problems(c),
                net_folders=_net_folders(now, settings.sources(s.cfg)), net_paths=dict(now.network_paths),
                cert=cert, host=host, cert_covers=cert is not None and https.covers(cert.names, host),
                cert_name=_cert_name(cert, now.allowed_hosts, host), proxied="x-forwarded-for" in request.headers,
                passkey_problem=passkeys.why_not(now), passkey_domain=passkeys.domain(now),
                passkey_count=c.execute("SELECT count(*) FROM passkey WHERE rp_id = ?",
                                        (passkeys.domain(now),)).fetchone()[0],
                backups=backup.summary(c, now), default_backups=s.cfg.data_dir.joinpath(*backup.DEFAULT_FOLDER),
                sidecar_counts=sidecars.summary(c), other_apps=imports.summary(c),
                exiftool=writeback.exiftool() is not None, face_round=faces.round_progress(c))


@router.post("/api/settings", dependencies=[Depends(_from_app)])
def save_settings(body: dict, c=Depends(con), s: Site = Depends(site)):
    settings.save(c, s.cfg, body)
    s.shared["at"] = -1e9
    return {"message": "Saved."}


@router.post("/api/settings/network-paths", dependencies=[Depends(_from_app)])
def save_network_paths(body: dict, c=Depends(con), s: Site = Depends(site)):
    """The Settings page's network path for each source: fields named net:<folder> (net: for the share)."""
    paths = {k[4:]: v for k, v in body.items() if k.startswith("net:")}
    settings.save(c, s.cfg, {"network_paths": paths})
    s.shared["at"] = -1e9
    return {"message": "Saved."}


@router.post("/api/settings/address", dependencies=[Depends(_from_app)])
def save_address(request: Request, body: dict, c=Depends(con), s: Site = Depends(site)):
    """Host names and reverse proxies. A list without the name this page was opened by is refused: the next page
    would be."""
    def save():
        hosts = settings.host_names(body.get("allowed_hosts", ""))
        host = request.headers.get("host", "")
        if not _host_allowed(host, hosts):
            raise settings.Invalid(f"This page was opened as {host}: keep that name in the list, or open the page "
                                   "by this machine's address first.")
        settings.save(c, s.cfg, {"allowed_hosts": hosts, "trusted_proxies": body.get("trusted_proxies", "")})
    save()
    s.shared["at"] = -1e9
    return {"message": "Saved."}


def switch_https(s: Site, request: Request, c, on: bool, files) -> dict:
    """Serve with another certificate, or none. The web server (cli serve) starts again the new way a moment
    later, and the page follows to its new address."""
    def save():
        if on != bool(s.live(c).tls_cert) and "x-forwarded-for" in request.headers:
            raise settings.Invalid("This page came through a reverse proxy, which talks to LineageBox over "
                                   f"{'plain HTTP' if on else 'HTTPS'}: switching would cut it off. Open the page "
                                   "by this machine's address to switch, then change the proxy to match.")
        try:
            cert, key = files()
        except https.Problem as e:
            raise settings.Invalid(str(e)) from None
        settings.save(c, s.cfg, {"tls_cert": cert, "tls_key": key})
    save()
    serving.saved.set()
    if not on:
        message = "HTTPS is off. Moving to the plain http:// address…"
    elif request.url.scheme == "https":
        message = "Saved. The web server starts again with this certificate…"
    else:
        message = ("HTTPS is on. Moving to the https:// address (if the browser warns, the certificate is not for "
                   "this name)…")
    go = f"{'https' if on else 'http'}://{request.headers.get('host', '')}/settings#https"
    return {"message": message, "go": go, "wait": 3}


@router.post("/api/https", dependencies=[Depends(_from_app)])
def use_certificate(request: Request, body: dict, c=Depends(con), s: Site = Depends(site)):
    """Certificate and key files this machine has: fields tls_cert and tls_key, as paths."""
    cert, key = str(body.get("tls_cert", "")).strip(), str(body.get("tls_key", "")).strip()
    return switch_https(s, request, c, bool(cert or key), lambda: (cert, key))


@router.post("/api/https/upload", dependencies=[Depends(_from_app)])
def upload_certificate(request: Request, body: dict, c=Depends(con), s: Site = Depends(site)):
    """A certificate and key chosen in the browser: their text, as fields cert and key."""
    return switch_https(s, request, c, True, lambda: https.store(s.cfg, str(body.get("cert", "")),
                                                              str(body.get("key", ""))))


@router.post("/api/https/off", dependencies=[Depends(_from_app)])
def https_off(request: Request, c=Depends(con), s: Site = Depends(site)):
    return switch_https(s, request, c, False, lambda: ("", ""))


@router.post("/api/folders/watch", dependencies=[Depends(_from_app)])
def watch_folder(body: FolderBody, c=Depends(con), s: Site = Depends(site)):
    settings.watch(c, s.cfg, body.path)
    return {}


@router.post("/api/folders/unwatch", dependencies=[Depends(_from_app)])
def unwatch_folder(body: FolderBody, c=Depends(con), s: Site = Depends(site)):
    settings.unwatch(c, s.cfg, body.path)
    return {}


@router.post("/api/ollama/check", dependencies=[Depends(_from_app)])
def check_ollama(c=Depends(con), s: Site = Depends(site)):
    return {"message": Ollama(s.live(c)).check()}


@router.post("/api/ollama/pull", dependencies=[Depends(_from_app)])
def pull_model(model: str = "", c=Depends(con), s: Site = Depends(site)):
    """Have Ollama download the chosen model (model=meaning: the one for search by meaning); the Status page shows
    how far it is."""
    now = s.live(c)
    meaning = model == "meaning" and now.ollama_embed_model
    if not s.pulling.acquire(blocking=False):
        return {"message": "Ollama is already downloading a model for LineageBox: see the Status page."}

    def run():
        try:
            con = db.connect(s.cfg, migrate_schema=False, check_same_thread=False)
            llm.pull(now, con, now.ollama_embed_model) if meaning else llm.pull(now, con)
        finally:
            s.pulling.release()

    threading.Thread(target=run, daemon=True, name="ollama pull").start()
    return {"message": f"Ollama is downloading {now.ollama_embed_model if meaning else now.ollama_model}. The Status "
                       f"page shows how far it is."}


@router.post("/api/whisper/check", dependencies=[Depends(_from_app)])
def check_whisper(c=Depends(con), s: Site = Depends(site)):
    return {"message": speech.Whisper(s.live(c)).check()}


@router.post("/api/backup", dependencies=[Depends(_from_app)])
def back_up_now(c=Depends(con), s: Site = Depends(site)):
    """Back up now: a copy made beside the web app, so it doesn't wait for the worker to finish what it is doing (a
    backlog of faces can take hours). The answer waits for it a while; a big catalog on a slow disk carries on."""
    done = backup.soon(s.live(c))
    if done is None:
        return {"message": "Still copying: the result shows here in a minute or two."}
    if "problem" in done:
        raise backup.Failed(done["problem"])
    last = done["last"]
    return {"message": f"Backed up and checked: {last['file']}, {fmt_size(last['bytes'])}.", "reload": True}


EXPORTS_LEFT = 3600  # seconds after which an export left in the data folder (the web app stopped while sending) goes


@router.get("/download/export")
def download_export(s: Site = Depends(site)):
    """Everything the family gave the catalog, as one zip that outlives LineageBox (export.py). It is made in a file
    of its own in the data folder, which is on a disk with room, sent, then deleted."""
    for old in s.cfg.data_dir.glob(".export-*.zip"):
        try:
            if time.time() - old.stat().st_mtime > EXPORTS_LEFT:
                old.unlink()
        except OSError:
            continue
    handle, name = tempfile.mkstemp(prefix=".export-", suffix=".zip", dir=s.cfg.data_dir)
    os.close(handle)
    path = Path(name)
    try:
        with s.own_con() as c:
            export.write(c, s.cfg, path)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return FileResponse(path, media_type="application/zip", background=BackgroundTask(path.unlink, missing_ok=True),
                        headers={"Content-Disposition": _attachment(export.file_name()), **PRIVATE})
