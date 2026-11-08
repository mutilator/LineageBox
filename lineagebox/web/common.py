"""What the web app's parts share (routes/, site.py, deps.py, app.py): the rules' names, formatting, the checks
of addresses and hosts, ids checked against what the viewer sees, the specs of what a member's change
touches (changes.py), and the bodies routes take."""
import ipaddress
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from lineagebox import access, albums, catalog, cleanup, errors, faces, family, https, sharing, tours
from lineagebox.config import Config

HERE = Path(__file__).resolve().parent
SHA256 = re.compile(r"^[0-9a-f]{64}$")
# derivatives are named by content hash; private: no proxy keeps one for someone who may not see it
IMMUTABLE = {"Cache-Control": "private, max-age=31536000, immutable"}
PRIVATE = {"Cache-Control": "private"}  # originals, videos and downloads: kept by browsers, never by a shared cache
PAGE_SIZES = ("25", "50", "100", "200", "all")
ZIP_MOST, ZIP_BYTES = 2000, 10 * 2**30  # the most one download holds: files, and their size
IN_DOCKER = Path("/.dockerenv").exists()  # for the Status page's update steps
EVERYTHING = 1_000_000_000  # "all" as a page size: more than any list holds
LAN_ONLY = (".lan", ".local", ".home", ".home.arpa", ".internal", ".localdomain", ".localhost")  # never public names
DATE_SOURCE_LABEL = {"exif": None, "camera": None, "filename": "date from file name", "folder": "date from folder",
                     "mtime": "date from file time", "user": None, "copy": "camera date of a copy",
                     "import": "date from another app"}  # imports.py


def fmt_date(iso: str | None, with_time: bool = True) -> str:
    if not iso:
        return "unknown"
    dt = datetime.fromisoformat(iso)
    out = f"{dt:%b} {dt.day}, {dt.year}"
    return f"{out} {dt:%H:%M}" if with_time and (dt.hour or dt.minute) else out


def fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return ""
    s = round(seconds)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


@dataclass
class Paging:
    """A page of a long list. per is one of PAGE_SIZES; chosen means it was picked just now (remember it)."""
    key: str
    pg: int
    per: str
    chosen: bool = False
    total: int = 0

    @property
    def size(self) -> int:
        return EVERYTHING if self.per == "all" else int(self.per)

    @property
    def offset(self) -> int:
        return (self.pg - 1) * self.size

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.size))

    @property
    def shown(self) -> tuple[int, int]:
        """The first and last item on this page, counting from 1."""
        return min(self.offset + 1, self.total), min(self.offset + self.size, self.total)

    def page_for(self, per: str) -> int:
        """The page, at another size, that starts with this page's first item."""
        return 1 if per == "all" else self.offset // int(per) + 1


SEE_AS = "lineagebox_see_as"      # cookie: the member an admin is seeing the site as
SIGNING_IN = "lineagebox_signin"  # cookie: the Google sign-in this browser started
PASSKEY_STARTED = "lineagebox_passkey"  # cookie: the passkey this browser was asked to make or use
PUBLIC, MEMBER, ADMIN = "public", "member", "admin"

_NOT_PAGES = ("/api/", "/t/", "/t2/", "/p/", "/frame/", "/face/", "/play/", "/original/", "/download/",
              "/status/live", "/tours/suggested")


def _public_path(path: str) -> bool:
    """What anyone may reach before signing in: the sign-in page and Google's way back, the stylesheet and script,
    and the health check; and what a phone installing the site asks for on its own, without its cookie (the web
    manifest and the service worker, routes/phone.py)."""
    return (path in ("/signin", "/healthz", "/manifest.webmanifest", "/sw.js")
            or path.startswith(("/auth/", "/static/")))


def _is_page(path: str) -> bool:
    """A page to send someone who isn't signed in to the sign-in page from (the rest just answer 401)."""
    return not path.startswith(_NOT_PAGES)


def _local_path(where: str) -> str:
    """Where to go after signing in: a page of this site, never another site. Browsers drop tabs and line breaks from
    an address, so "/\\t/evil.example" would become "//evil.example": no spaces or control characters either."""
    local = where.startswith("/") and not where.startswith("//") and "\\" not in where
    return where if local and not any(ch <= " " or ch == "\x7f" for ch in where) else "/"


SMALL_BODY = 64 * 1024  # the most a request may send before signing in


def _small_body(request: Request) -> bool:
    """A body small enough to read before anyone has signed in: its length is given, and is under SMALL_BODY."""
    length = request.headers.get("content-length", "")
    return length.isdecimal() and int(length) <= SMALL_BODY and "transfer-encoding" not in request.headers


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _redirect_uri(request: Request, now: Config) -> str:
    """Where Google sends people back to: the address people use from outside (public_url), else this one."""
    base = now.public_url or f"{request.url.scheme}://{request.headers.get('host', '')}"
    return base.rstrip("/") + "/auth/google/callback"


def _content_policy(tiles: str, framed: bool = False) -> str:
    """Scripts only from this site's own files (pages have no inline scripts), pictures from it and the map's tile
    server, no plugins, frames or forms elsewhere: a name or caption that ever slipped through escaping could not run
    as script with an admin's session. The service worker (routes/phone.py) and the web manifest are this site's own
    files too. framed: a photo frame's device link (web/frames.py), which another page (a Home Assistant dashboard) may
    show inside it: it changes nothing, and has no buttons that could be clicked through a page laid over it."""
    ancestors = "*" if framed else "'none'"
    return ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            f"img-src 'self' data: {_tile_source(tiles)}; connect-src 'self'; object-src 'none'; base-uri 'none'; "
            f"form-action 'self'; frame-ancestors {ancestors}")


def _tile_source(tiles: str) -> str:
    """The [map] tiles address as a CSP source: its scheme and host, a {s} subdomain as *."""
    m = re.match(r"(https?)://([^/]+)", tiles)
    if not m:
        return ""
    host = re.sub(r"^\{s\}\.", "*.", m[2])
    return f"{m[1]}:" if "{" in host else f"{m[1]}://{host}"


def _uploads_root(now: Config) -> str:
    """The folder members' uploads folders are in: <watched folder>/<uploads folder>."""
    into = now.uploads_into or (now.folders[0] if now.folders else "")
    return f"{into}/{now.uploads_folder}".strip("/")


def _within(path: str, folder: str | None) -> bool:
    return bool(folder) and (path == folder or path.startswith(folder + "/"))


def _among(v: access.Viewer) -> set[int] | None:
    """The people a name typed by the viewer may mean (None: anyone)."""
    return set(v.people) if v.limited else None


def _by(v: access.Viewer) -> int | None:
    """The member's account recorded on what they add (people, groups, tours, faces they name)."""
    return v.account_id if v.limited else None


def _people_or_404(c, *person_ids) -> None:
    """The people a change is about are there (not merged or removed since the page was made) and, for a member, theirs
    to see: c, the viewer's own connection, holds only those. Otherwise it is refused, as for anyone not there."""
    ids = sorted({p for p in person_ids if p is not None})
    if ids and c.execute("SELECT count(*) FROM person WHERE id IN (SELECT value FROM json_each(?))",
                         (json.dumps(ids),)).fetchone()[0] != len(ids):
        raise HTTPException(404, "No such person")


def _images_or_404(c, v: access.Viewer, image_ids) -> None:
    """A member's change to photos is refused as a whole when any of them is not theirs to see."""
    if v.limited and any(not v.sees_image(i) for i in image_ids):
        raise HTTPException(404, "No such photo")


def _face_to_change(c, v: access.Viewer, face_id: int) -> None:
    """A member names faces in photos they see, but not one named as someone outside their part of the family."""
    if not v.limited:
        return
    f = catalog.face(c, face_id)
    if f is None:
        raise HTTPException(404, "No such face")
    if f["person_id"] is not None and not v.sees_person(f["person_id"]):
        raise HTTPException(403, "Someone from outside your part of the family is named here: only an admin can "
                                 "change that.")


def _own_files(c, v: access.Viewer, image_ids) -> list[int]:
    """The files a member uploaded of these photos; refused when any photo has none (it isn't theirs to remove)."""
    out = []
    for image_id in image_ids:
        mine = [f["id"] for f in catalog.image_files(c, image_id)
                if not f["missing"] and _within(f["path"], v.account["upload_folder"])]
        if not mine:
            raise HTTPException(404, "Only photos you uploaded yourself can be removed.")
        out += mine
    return out


def _own_tour(c, v: access.Viewer, tour_id: int) -> None:
    """A member changes only their own tours (those others shared with them are to watch)."""
    if v.limited and ((t := tours.tour(c, tour_id)) is None or t["account_id"] != v.account_id):
        raise HTTPException(404, "No such tour")


def _own_folder(c, v: access.Viewer, folder_id: int):
    """A folder the viewer may change (move to Removed, share): any, for an admin; for a member, their uploads folder
    or one in it. Others are refused as not there."""
    row = c.execute("SELECT id, path, name, parent_id FROM folder WHERE id = ?", (folder_id,)).fetchone()
    if row is None or v.limited and not _within(row["path"], v.account["upload_folder"]):
        raise HTTPException(404, "No such folder among your uploads" if v.limited else "No such folder")
    return row


def _upload_labels(w, v: access.Viewer) -> dict[str, str]:
    """The folder of each account's uploads (named by their person's number on disk) -> how it is shown: "Your
    uploads", "Cora's uploads". On a connection that sees everyone; a member is shown only folders they may see."""
    out = {}
    for folder, name, account_id in w.execute("""SELECT a.upload_folder, p.name, a.id FROM main.account a
                                                 LEFT JOIN main.person p ON p.id = a.person_id
                                                 WHERE a.upload_folder IS NOT NULL"""):
        out[folder] = "Your uploads" if account_id == v.account_id else f"{name or 'Someone'}'s uploads"
    return out


def _share_panel(w, v: access.Viewer, kind: str, item_id: int) -> dict:
    """What the sharing panel (_macros.share_panel) shows of one folder, photo or tour: whom it is shared with (a
    member sees only what they shared themselves), and whom else it may be shared with. On a connection that sees
    everyone."""
    shares = []
    for s in sharing.of(w, kind, item_id):
        if v.admin or s["by_id"] == v.account_id:
            shares.append({**s, "with": sharing.EVERYONE if s["with_id"] is None else
                           sharing.who(v, s["with_person"], s["with_name"], s["with_email"]),
                           "by": None if s["by_id"] == v.account_id else
                           sharing.who(v, s["by_person"], s["by_name"], s["by_email"])})
    return {"kind": kind, "item_id": item_id, "shares": shares, "recipients": sharing.recipients(w, v),
            "admin": v.admin}


def _shared_with_me(w, c, v: access.Viewer) -> dict:
    """What others share with a member, newest first, as they may see it: folders, tours and albums (each with its
    count and cover, a tour's from the photos it shares) and single photos, each with who shared it. Nothing for an
    admin, who sees everything anyway."""
    out = {"folders": [], "tours": [], "photos": [], "albums": []}
    if not v.limited:
        return out
    tree, seen = None, set()
    for s in sharing.with_me(w, v.account_id):
        if (s["kind"], s["item_id"]) in seen:
            continue
        seen.add((s["kind"], s["item_id"]))
        if s["kind"] == "folder":
            tree = catalog.folder_tree(c) if tree is None else tree
            f = tree.get(s["item_id"])
            if f is not None and f.total:
                label = f"{s['by_name']}'s uploads" if f.path == s["by_uploads"] else f.name
                out["folders"].append({"folder": f, "label": label, "by": s["by_name"]})
        elif s["kind"] == "tour":
            if t := tours.tour(c, s["item_id"]):
                shown = [(i, sha) for i, sha in w.execute("""SELECT i.id, i.sha256 FROM main.tour_photo p
                                                             JOIN main.image i ON i.id = p.image_id
                                                             WHERE p.tour_id = ? ORDER BY i.taken_at""", (t["id"],))
                         if v.sees_image(i)]
                out["tours"].append({"tour": t, "by": s["by_name"], "photos": len(shown),
                                     "cover": shown[0][1] if shown else None})
        elif s["kind"] == "album":  # albums.py: of its photos, those they see, in its order
            if a := albums.get(c, s["item_id"]):
                out["albums"].append({"album": a, "by": s["by_name"], "photos": len(albums.photo_ids(c, a["id"])),
                                      "cover": albums.cover(c, a)})
        elif row := c.execute(f"""SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.media, i.duration FROM image i
                                   WHERE i.id = ? AND {catalog.VISIBLE}""", (s["item_id"],)).fetchone():
            out["photos"].append(row)
    return out


def _person_named(c, name: str, pid: int | None) -> int:
    """Someone already in the family tree, by the name typed (or picked) in a box: for accounts and exceptions,
    which never add anyone."""
    name = " ".join(name.split())
    if pid is not None and (row := catalog.person(c, pid)) and row["name"].lower() == name.lower():
        return pid
    found = [r[0] for r in c.execute("SELECT id FROM person WHERE lower(name) = lower(?)", (name,))]
    if len(found) == 1:
        return found[0]
    if found:
        raise HTTPException(400, f"{len(found)} people are called {name}: pick the one you mean from the suggestions.")
    raise HTTPException(400, f"Nobody in the family tree is called {name}. Add them on the Family page first.")


def _ids(ids) -> str:
    return json.dumps(sorted({i for i in ids if i is not None}))


def _faces_spec(v: access.Viewer, face_ids):
    """Faces named or unnamed, with every face of a video among them (naming one names their others in it too,
    people.name_in_videos), and anyone the member added by naming them."""
    ids = _ids(face_ids)
    in_video = """image_id IN (SELECT image_id FROM main.face
                               WHERE id IN (SELECT value FROM json_each(?)) AND frame IS NOT NULL)"""
    return lambda new: [("face", f"id IN (SELECT value FROM json_each(?)) OR {in_video}", (ids, ids)),
                        ("face_not_person", "face_id IN (SELECT value FROM json_each(?))", (ids,)),
                        ("person", "id > ? AND added_by IS ?", (new["person"], _by(v)))]


def _tags_spec(image_ids, tag: str):
    ids, name = _ids(image_ids), catalog.tag_name(tag)
    return lambda new: [("image_tag", "image_id IN (SELECT value FROM json_each(?)) AND tag_id IN "
                                      "(SELECT id FROM main.tag WHERE name = ?)", (ids, name)),
                        ("tag", "id > ? AND name = ?", (new["tag"], name))]


def _dates_spec(image_ids):
    ids = _ids(image_ids)
    return lambda new: [("image", "id IN (SELECT value FROM json_each(?))", (ids,))]


def _photo_people_spec(v: access.Viewer, image_ids):
    ids = _ids(image_ids)
    return lambda new: [("image_person", "image_id IN (SELECT value FROM json_each(?))", (ids,)),
                        ("person", "id > ? AND added_by IS ?", (new["person"], _by(v)))]


def _family_spec(v: access.Viewer, *person_ids):
    """Everything about these people a family change can touch, and anyone the member added with it: with what
    their life events' removal takes along (documents' and written sources' citations of them), and the written
    sources cited for them (one nothing cites any more goes)."""
    ids = _ids(person_ids)
    among = "IN (SELECT value FROM json_each(?))"
    cites = f"""person_id {among} OR event_id IN (SELECT id FROM main.event
                                                 WHERE person_id {among} OR couple_a {among} OR couple_b {among})"""
    return lambda new: [
        ("person", f"id {among} OR (id > ? AND added_by IS ?)", (ids, new["person"], _by(v))),
        ("parent", f"child_id {among} OR parent_id {among}", (ids, ids)),
        ("sibling", f"a {among} OR b {among}", (ids, ids)), ("partner", f"a {among} OR b {among}", (ids, ids)),
        ("relation", f"person_id {among} OR of_id {among}", (ids, ids)),
        ("event", f"person_id {among} OR couple_a {among} OR couple_b {among}", (ids, ids, ids)),
        ("person_name", f"person_id {among}", (ids,)), ("person_group", f"person_id {among}", (ids,)),
        ("citation", cites, (ids,) * 4), ("source_citation", cites, (ids,) * 4),
        ("source", f"id > ? OR id IN (SELECT source_id FROM main.source_citation WHERE {cites})",
         (new["source"], *(ids,) * 4)),
        ("tree_media", f"person_id {among}", (ids,))]


def _merge_spec(w, v: access.Viewer, *person_ids):
    """Everything merging or removing these people can touch, for its undo: their family (as _family_spec), every
    face and photo named as them, their "not them" marks and citations, other trees' ids for them, accounts and access
    exceptions that point at them, the tours that pick them and "This is me". The faces and tours are picked now,
    before the change: a removal takes the name off the faces, which then could no longer be found by it."""
    ids = _ids(person_ids)
    among = "IN (SELECT value FROM json_each(?))"
    face_ids = _ids(r[0] for r in w.execute(f"SELECT id FROM main.face WHERE person_id {among}", (ids,)))
    tours = {t: _ids(r[0] for r in w.execute(f"""SELECT id FROM main.{t} WHERE EXISTS
                                                 (SELECT 1 FROM json_each(spec, '$.who.person_ids') WHERE value {among})""",
                                             (ids,))) for t in ("tour", "tour_type")}
    family = _family_spec(v, *person_ids)
    events = f"SELECT id FROM main.event WHERE person_id {among} OR couple_a {among} OR couple_b {among}"
    return lambda new: [*family(new), ("face", f"id {among}", (face_ids,)),
                        ("image_person", f"person_id {among}", (ids,)),
                        ("face_not_person", f"person_id {among}", (ids,)),
                        ("citation", f"person_id {among} OR event_id IN ({events})", (ids, ids, ids, ids)),
                        ("external_id", f"person_id {among}", (ids,)), ("access_rule", f"person_id {among}", (ids,)),
                        ("account", f"person_id {among}", (ids,)), ("settings", "key = 'me'", ()),
                        ("tour", f"id {among}", (tours["tour"],)), ("tour_type", f"id {among}", (tours["tour_type"],)),
                        ("story", f"person_id {among}", (ids,))]  # recorded stories about them (stories.py)


def _document_spec(image_id: int):
    return lambda new: [("document", "image_id = ?", (image_id,)), ("citation", "image_id = ?", (image_id,))]


def _cert_name(cert, allowed_hosts, host: str) -> str:
    """A name to open the site by that its certificate is for (with this request's port), to suggest instead of an
    address the browser warns on: a listed host name it covers, else its own name, unless it is for every name under a
    domain (that domain itself is most likely the family's website, elsewhere)."""
    if cert is None:
        return ""
    names = [h for h in allowed_hosts if "*" not in h and https.covers(cert.names, h)]
    if not any(n.startswith("*.") for n in cert.names):
        names += [n for n in cert.names if not _is_address(n)]
    if not names:
        return ""
    port = host.rpartition(":")[2] if host.count(":") == 1 or "]:" in host else ""
    return f"{names[0]}:{port}" if port else names[0]


def _is_address(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


class LiveProxyHeaders:
    """uvicorn's ProxyHeadersMiddleware, for the reverse proxies set now: the Settings page can change them."""

    def __init__(self, app, proxies):
        self.app, self.proxies, self.made = app, proxies, {}

    async def __call__(self, scope, receive, send):
        trusted = self.proxies() if scope["type"] in ("http", "websocket") else ()
        if not trusted:
            return await self.app(scope, receive, send)
        if trusted not in self.made:
            self.made = {trusted: ProxyHeadersMiddleware(self.app, trusted_hosts=list(trusted))}
        return await self.made[trusted](scope, receive, send)


def _allowed(host: str, networks: list) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in n for n in networks)


def _host_allowed(host_header: str, allowed: tuple[str, ...]) -> bool:
    """Whether a request's Host is an address, a name only a home network can have (one word, or ending in .lan,
    .local, .home.arpa...), or one of allowed_hosts ("*.example.com" for every name under it, "*" for any)."""
    host = host_header.strip().lower()
    if host.startswith("["):  # [IPv6]:port
        host = host[1:host.find("]")]
    elif host.count(":") == 1:
        host = host.rsplit(":", 1)[0]
    host = host.rstrip(".")
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if "." not in host or host.endswith(LAN_ONLY):
        return True
    return any(a == "*" or host == a or (a.startswith("*.") and host.endswith(a[1:])) for a in allowed)


def locate(cfg: Config, rel: str) -> str:
    """Where people's PCs find a folder or file of the share ('' for the share itself): under the network path of
    the source it is in (network_paths), else under share_path."""
    for folder, share in cfg.network_roots():
        if not folder or rel == folder or rel.startswith(folder + "/"):
            return network_path(share, rel[len(folder):].lstrip("/"))
    raise AssertionError("network_roots always ends with the share itself")


def _net_folders(now: Config, sources) -> list[str]:
    """The folders a network path can be set for on the Settings page: the share itself, each source (in Docker),
    and the top folders of what is watched."""
    folders = {name for name, _ in sources} | {w.split("/")[0] for w in now.folders} | {f for f, _ in now.network_paths}
    return ["", *sorted(folders - {""}, key=str.lower)]


def media_path(cfg: Config, rel: str = "") -> str:
    """Where a folder of the share is as LineageBox reads it: in Docker, the mounted folder (/photos/local/Photos)."""
    return (cfg.share_root / rel).as_posix() if rel else cfg.share_root.as_posix()


def on_network(cfg: Config, rel: str) -> str:
    """Where people's PCs find a folder of the share, when that is somewhere else than where LineageBox reads it (a
    network path set in network_paths or share_path); else ''."""
    found = locate(cfg, rel)
    return "" if found.replace("\\", "/").rstrip("/") == media_path(cfg, rel).rstrip("/") else found


def network_path(share: str, rel: str) -> str:
    """Where a file is for people browsing the share: '\\\\nas\\photos\\2012\\x.jpg' for a Windows share."""
    if not rel:
        return share
    sep = "\\" if share.startswith("\\\\") or re.match(r"^[A-Za-z]:", share) else "/"
    return share.rstrip("\\/") + sep + rel.replace("/", sep)


def _photos_per_watched(c, folders) -> dict[str, int]:
    out = {}
    for w in folders:
        out[w] = c.execute("""SELECT count(DISTINCT f.image_id) FROM file f WHERE NOT f.missing
                              AND f.image_id IS NOT NULL AND f.path LIKE ? ESCAPE '!'""",
                           (catalog.under(w),)).fetchone()[0]
    return out


def _photos(n: int) -> str:
    return f"{n:,} photo" + ("" if n == 1 else "s")


def _name_choices(c) -> list[dict]:
    """Everyone with a name, for the boxes names are typed in (app.js suggests from it): with their face, photos,
    years, relationship and other names, so that two people of the same name can be told apart."""
    shown = {p["id"]: p for p in catalog.people(c)}
    years: dict[int, dict[str, str]] = {}
    for pid, tag, when in c.execute("""SELECT person_id, tag, sort_date FROM event WHERE person_id IS NOT NULL
                                       AND tag IN ('BIRT', 'DEAT') AND sort_date IS NOT NULL"""):
        years.setdefault(pid, {})[tag] = when[:4]
    kin, also = _kin_labels(c), dict(c.execute(
        "SELECT person_id, group_concat(name, ' · ') FROM person_name GROUP BY person_id"))
    out = []
    for pid, name in c.execute("SELECT id, name FROM person ORDER BY name COLLATE NOCASE"):
        y = years.get(pid, {})
        life = f"{y.get('BIRT', '')}–{y['DEAT']}" if "DEAT" in y else f"born {y['BIRT']}" if "BIRT" in y else ""
        p = shown.get(pid)
        out.append({"id": pid, "name": name, "face": p["cover_face"] if p else None, "photos": p["photos"] if p else 0,
                    "about": " · ".join(filter(None, (kin.get(pid), life))), "also": also.get(pid) or ""})
    return out


def _image_or_404(c, image_id: int) -> None:
    if catalog.image(c, image_id) is None:
        raise HTTPException(404, "No such image")


def _kin_labels(c) -> dict[int, str]:
    """person id -> what they are to the person marked as "me" (nothing for "me" themselves)."""
    ego = family.me(c)
    if ego is None:
        return {}
    return {pid: k.label for pid, k in family.relations_to(family.load(c), ego).items() if pid != ego}


def _others_by_whose(g) -> list[tuple[str, list[int]]]:
    """The Family page's friends and others, one heading each: "Emma's pets" over Rex, Daisy and Biscuit."""
    groups: dict[tuple[str, int], list[int]] = {}
    for p, of, kind in g.relations:
        groups.setdefault((kind, of), []).append(p)
    return [(f"{g.name(of)}'s {family.plural(kind)}", sorted(ids, key=g.name))
            for (kind, of), ids in sorted(groups.items(), key=lambda kv: (kv[0][0], g.name(kv[0][1])))]


def _covers(named) -> dict[int, int]:
    """person id -> the face shown for them (people with no faces have none)."""
    return {p["id"]: p["cover_face"] for p in named}


def _same_group(c, cluster_id: int, faces: list[int]) -> None:
    """Groups are renumbered whenever faces are regrouped (new photos, "Find more photos"). The People page sends
    the clearest faces it showed for the group, so a page opened before a regroup cannot name whichever group now has
    the old number. One of them still in the group is enough: faces that don't belong can be taken out one by one."""
    row = c.execute(f"SELECT 1 FROM face WHERE cluster_id = ? AND id IN ({','.join('?' * len(faces))}) LIMIT 1",
                    (cluster_id, *faces)).fetchone()
    if row is None:
        raise HTTPException(409, "The face groups were updated since this page was opened. Reload it and try again.")


class NameBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    name_id: int | None = None  # the person picked from the name box's suggestions (two may have the same name)


class RenameBody(NameBody):
    merge: bool = False  # yes, make the two one person (asked first: rename_person)


class MergeBody(BaseModel):
    into: int


class FacesBody(BaseModel):
    """Faces suggested as someone: yes are them, no are not."""
    yes: list[int] = Field(default=[], max_length=1000)
    no: list[int] = Field(default=[], max_length=1000)


class LinkBody(BaseModel):
    word: str = Field(min_length=1, max_length=40)   # mother, son, ex-wife, coworker...
    name: str = Field(min_length=1, max_length=80)   # someone new is added to the catalog
    name_id: int | None = None                      # who was picked from the suggestions, if anyone
    with_parent: str = ""                           # a child's other parent


class SexBody(BaseModel):
    sex: str = ""


class DetailsBody(BaseModel):
    birth_date: str = ""
    deceased: list[str] = []


class EventBody(BaseModel):
    id: int | None = None
    tag: str = Field(min_length=3, max_length=4)
    date: str = Field("", max_length=80)
    place: str = Field("", max_length=200)
    type: str = Field("", max_length=80)
    note: str = Field("", max_length=2000)
    partner: str | int = ""  # a couple's event: the partner's id


class NamePartsBody(BaseModel):
    given: str = Field("", max_length=120)
    surname: str = Field("", max_length=120)
    type: str = "birth"


class PlaceBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    query: str = Field("", max_length=200)


class PlaceConfirmBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    right: bool = True


class DocumentBody(BaseModel):
    kind: str = Field(min_length=1, max_length=60)
    title: str = Field("", max_length=200)


class CitationBody(BaseModel):
    target: str = Field(min_length=3, max_length=20)


class GroupBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class OtherNameBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: str


class StoryBody(BaseModel):
    """A person's life story, or their notes: only what is sent changes."""
    biography: str | None = Field(None, max_length=20000)
    notes: str | None = Field(None, max_length=1_000_000)  # an imported tree's notes can be long: editing must work


class PortraitBody(BaseModel):
    face_id: int | None = None  # None: picked as before, the clearest named face


class SourceBody(BaseModel):
    """A written source cited for someone or one of their events: one already here (source_id), or a new one."""
    source_id: int | None = None
    title: str = Field("", max_length=200)
    author: str = Field("", max_length=120)
    publication: str = Field("", max_length=200)
    note: str = Field("", max_length=4000)
    target: str = Field(min_length=3, max_length=20)  # p:<person id> or e:<event id>
    page: str = Field("", max_length=300)


class SourceEditBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    author: str = Field("", max_length=120)
    publication: str = Field("", max_length=200)
    note: str = Field("", max_length=4000)


class PediBody(BaseModel):
    """How a child belongs to a parent: birth, adopted or foster."""
    child: int
    parent: int
    kind: str


class UnlinkBody(BaseModel):
    kind: str = ""
    a: int
    b: int
    what: str = ""


class MeBody(BaseModel):
    person_id: int


class LocalBody(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    code: str = Field(min_length=6, max_length=12)
    next: str = Field("/", max_length=500)


class DemoBody(BaseModel):
    next: str = Field("/", max_length=500)


class CodeBody(BaseModel):
    code: str = Field(min_length=1, max_length=12)


class PasskeyStart(BaseModel):
    next: str = Field("/", max_length=500)


class PasskeyBody(BaseModel):
    """What the browser answered (navigator.credentials.create or get), as WebAuthn's JSON; passkeys.py reads it."""
    credential: dict


class InviteBody(BaseModel):
    """The token of an admin's link for making a passkey, from after the link's #."""
    token: str = Field(min_length=10, max_length=100)


class PasskeyName(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class AccountBody(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    name: str = Field("", max_length=80)       # who they are in the family tree
    name_id: int | None = None
    role: str = "member"


class AccountChange(BaseModel):
    name: str = Field("", max_length=80)
    name_id: int | None = None
    role: str = ""
    disabled: bool | None = None


class RuleBody(BaseModel):
    """An exception on an account: someone also shown (show) or not, alone or with their family (branch)."""
    name: str = Field(min_length=1, max_length=80)
    name_id: int | None = None
    show: bool = True
    branch: bool = False


class HiddenBody(BaseModel):
    hidden: bool = True


class ShareBody(BaseModel):
    """One folder, photo, tour or album (only one of the ids), and whom with: an account's id, or "everyone"."""
    folder_id: int | None = None
    image_id: int | None = None
    tour_id: int | None = None
    album_id: int | None = None
    to: str = Field(min_length=1, max_length=20)


class UndoSinceBody(BaseModel):
    account_id: int
    since: int  # the first change to undo; every later one of theirs goes too


class SameBody(BaseModel):
    added: int
    other: int
    keep_seeing: bool = True


class SeeAsBody(BaseModel):
    account_id: int


class TurnBody(BaseModel):
    degrees: int  # clockwise: 90, 180 or 270


class ImageIds(BaseModel):
    image_ids: list[int] = Field(min_length=1, max_length=5000)
    reason: str = Field("selected on a cleanup page", min_length=1, max_length=200)


class Selection(ImageIds):
    """Photos ticked on a browsing page, and what was typed in its bar."""
    add_tag: str = Field("", max_length=80)
    person: str = Field("", max_length=80)
    person_id: int | None = None  # who was picked from the suggestions for person
    date: str = Field("", max_length=80)


class DateBody(BaseModel):
    """A photo's date as typed (see dates.photo_date); empty for its automatic date again."""
    date: str = Field("", max_length=80)


class TagBody(BaseModel):
    tag: str = Field(min_length=1, max_length=80)


class CheckBody(BaseModel):
    key: str = Field(min_length=1, max_length=120)  # a problem on Cleanup → Family tree


class FolderBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


def _moved(result: cleanup.Moved) -> dict:
    return {"moved": result.moved, "errors": result.errors[:50], "more_errors": max(0, len(result.errors) - 50)}


def _attachment(name: str) -> str:
    """Content-Disposition for a download, whatever letters its name has (a tour named after Łukasz): the name in
    UTF-8, with a plain one for programs that don't read it. Headers themselves can only hold Latin-1."""
    plain = re.sub(r"[^A-Za-z0-9 ._()-]+", "", name).strip(" .") or "download"
    return f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(name)}"


def _plain_error(errors) -> str:
    """The first thing FastAPI found wrong with a request, as a sentence for the person who sent it."""
    if not errors:
        return "Something sent with that didn't fit. Reload the page and try again."
    e = errors[0]
    field = next((p for p in reversed(e.get("loc") or ()) if isinstance(p, str) and p not in ("body", "query",
                                                                                              "path", "header")), "")
    what = field.replace("_", " ").capitalize() or "What was sent"
    limits, kind = e.get("ctx") or {}, e.get("type", "")
    if kind == "string_too_long":
        return f"{what} is too long: at most {limits.get('max_length')} characters."
    if kind == "string_too_short":
        return f"{what} is too short: at least {limits.get('min_length')} characters."
    if kind == "too_long":
        return f"That's too many at once: at most {limits.get('max_length'):,}. Do it in parts."
    if kind == "missing":
        return f"{what} is missing."
    if kind.startswith(("int_", "float_")):
        return f"{what} should be a number."
    return f"{what}: {e.get('msg', 'not right')}."


def _from_app(x_requested_with: str = Header("")) -> None:
    """Changes only come from this app's own scripts: browsers cannot add this header to a cross-site request
    without a CORS preflight, which this app never grants."""
    if x_requested_with != "lineagebox":
        raise HTTPException(403)


def _derivative(path: Path | None, media_type: str) -> FileResponse:
    if path is None or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type=media_type, headers=IMMUTABLE)
