"""Photo frames in the web app: what a device link (devices.py) may open, and the photos a frame cycles through.

A device link's address is /d/<token>. DevicePath takes the token off the front of every request under it
(/d/<token>/p/<sha> is /p/<sha> to the routes), and the gate (web/app.py) looks it up. So a frame needs no cookie,
and keeps working inside a Home Assistant dashboard's frame and on a browser too old for anything else. A request
under a link that is revoked, or made for the home network and asked for from outside, is refused outright.

A device link sees only the photos and videos of its one view (access.device), and opens only ROUTES: the frame
page, its list of photos and those pictures. Every other route answers it 404, as for something that isn't there
(app.route_gate; tests/test_access_web.py sweeps every route with one).

The photos are worked out as the account the link was made for sees them (memories), as a tour's maker does, or as an
album's maker may pass them on, then kept a while (PHOTOS_KEPT, and anew after any change made on the site), so that
the frame's pictures, asked for every few seconds, are checked against a list already made.
"""
import hashlib
import json
import threading
import time
from datetime import date, timedelta

from lineagebox import access, accounts, albums, comments, devices, memories, tours
from lineagebox.catalog import VISIBLE

PREFIX = "/d/"
TOKEN = "lineagebox.device_token"  # where DevicePath leaves the token, in the request's scope
# what a device link may open: its frame, the frame's list, and the pictures and videos on that list
ROUTES = frozenset({"/photo-frame", "/api/photo-frame", "/p/{sha}", "/play/{image_id}"})
LINK_KEPT = 10  # seconds a link's lookup is kept (and at once anew after a change on the site, a revoke included)
PHOTOS_KEPT = 600  # seconds a frame's list is kept
MOST = 400  # photos on one frame's list
FILL = 60  # memories with fewer photos than this are filled with the best of other years
REFRESH = 30 * 60  # seconds between the frame asking for its list again
OFF = ("This photo frame's link was turned off, or isn't right. Ask whoever set up the frame for a new one: Settings "
       "→ Photo frames.")
AWAY = "This photo frame's link works only on the home network."


class DevicePath:
    """ASGI middleware: /d/<token>/rest becomes /rest (the frame page for /d/<token> itself), with the token left in
    the scope for the gate. The path is changed in place, so the server's own log shows it without the token."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith(PREFIX):
            token, _, rest = scope["path"][len(PREFIX):].partition("/")
            path = f"/{rest}" if rest else "/photo-frame"
            scope["path"], scope["raw_path"], scope[TOKEN] = path, path.encode(), token
        await self.app(scope, receive, send)


_seen_written: dict[str, float] = {}  # link token hash -> when its last use was written (monotonic)
_seen_lock = threading.Lock()


def link_for(s, token: str, ip: str = "") -> dict | None:
    """The device link a token is, while it is on (devices.by_token): kept LINK_KEPT seconds, and looked up anew after
    any change made on the site, so revoking a link turns it off at once. Its last use is written every
    devices.SEEN_EVERY seconds."""
    if not token or len(token) > 100:
        return None
    key = ("device link", hashlib.sha256(token.encode()).hexdigest())
    found, generation = s.kept.peek(key, LINK_KEPT)
    if found is None:
        with s.own_con() as c:
            found = devices.by_token(c, token)
        if found is None:  # not kept: made-up tokens would only fill the memory
            return None
        s.kept.put(key, found, generation)
    with _seen_lock:
        due = time.monotonic() - _seen_written.get(found["token_hash"], -1e9) > devices.SEEN_EVERY
        if due:
            _seen_written[found["token_hash"]] = time.monotonic()
    if due:
        with s.own_con() as c:
            devices.seen(c, found["id"], ip)
    return dict(found)


def shown(s, link: dict) -> dict:
    """What a device link's frame shows: {"slides", "viewer"}, kept PHOTOS_KEPT seconds (anew after any change made
    on the site, and each day)."""
    today = date.today()

    def work():
        with s.own_con() as c:
            blurry = s.live(c).blurry_below
            # a photo an admin hid from the family is on no frame, whoever's view it is: a frame is seen by whoever
            # passes it (and an admin's album or the whole library would hold it), as no share passes one on
            not_shown, args = access.hidden(c)
            hidden = {r[0] for r in c.execute(not_shown, args)}
            chosen = [p for p in PICKERS.get(link["view"], _nothing)(s, c, link, today, blurry)
                      if p["id"] not in hidden][:MOST]
            slides = slideshow(c, chosen)
        ids = {p["id"] for p in chosen}
        return {"slides": slides, "viewer": access.device(link, ids, {p["sha"] for p in chosen})}
    return s.kept.get(("device photos", link["id"], today.isoformat()), PHOTOS_KEPT, work)


def viewer_for(s, link: dict) -> access.Viewer:
    """The device link as a Viewer: it sees the photos on its frame's list, and nothing else."""
    return shown(s, link)["viewer"]


# ---- the photos of each view ---------------------------------------------------------------------------------------

def _as_account(s, c, account_id: int | None) -> bool:
    """Make c see what this account sees (nothing of it when it is gone or switched off). True when it sees anything."""
    if account_id is None:
        return True
    account = accounts.get(c, account_id)
    if account is None or account["disabled_at"] is not None:
        return False
    if account["role"] != "admin":
        access.scope(c, s.viewers.get(c, dict(account)))
    return True


def memories_view(s, c, link: dict, today: date, blurry) -> list[dict]:
    """Memories, as the link's account sees them (None: the whole library)."""
    return memory_photos(c, today, blurry) if _as_account(s, c, link["account_id"]) else []


def tour_view(s, c, link: dict, today: date, blurry) -> list[dict]:
    """A tour, as its maker sees it (what sharing it would share): an admin's from everything."""
    t = tours.tour(c, link["tour_id"]) if link["tour_id"] is not None else None
    if t is None:
        return []
    maker = accounts.get(c, t["account_id"]) if t["account_id"] is not None else None
    if maker is not None and maker["disabled_at"] is not None:
        return []
    if maker is not None and maker["role"] != "admin":
        access.scope(c, s.viewers.get(c, dict(maker), shared=False))
    return tours.photos(c, t["spec"], blurry, seed=t["id"])


def album_view(s, c, link: dict, today: date, blurry) -> list[dict]:
    """An album, in its order, with the photos its maker may pass on (routes/albums.passed_on: an admin's all of them,
    a member's those of their own part of the family and uploads), and the album's caption for each where it has one."""
    album = albums.get(c, link["album_id"]) if link["album_id"] is not None else None
    if album is None or album["created_at"] > link["made_at"]:  # deleted, and its number given to a newer album
        return []
    maker = accounts.get(c, album["account_id"]) if album["account_id"] is not None else None
    if maker is not None and maker["disabled_at"] is not None:
        return []
    if maker is not None and maker["role"] != "admin":
        access.scope(c, s.viewers.get(c, dict(maker), shared=False))
    return [{"id": r["id"], "sha": r["sha256"], "taken": r["taken_at"], "media": r["media"],
             "duration": r["duration"], "playable": r["playable"], "faces": {}, "score": 0,
             "album_caption": r["caption"]} for r in albums.photos(c, album["id"])]


def _nothing(s, c, link, today, blurry) -> list[dict]:
    return []


# view -> its photos (tours.photos' kind of list), on a connection that sees everything until the picker scopes it;
# devices.VIEWS names each for the choice on Settings
PICKERS = {"memories": memories_view, "tour": tour_view, "album": album_view}


def memory_photos(c, today: date, blurry: float | None) -> list[dict]:
    """Memories for a frame, from what the connection holds: this week in every earlier year (memories.AROUND days
    either side of today's date), the latest year first; when that is too few, the best photos spread over all the
    years, in another order each day."""
    days = sorted({(today + timedelta(days=k)).strftime("%m-%d")
                   for k in range(-memories.AROUND, memories.AROUND + 1)})
    years = sorted({int(y) for (y,) in c.execute(f"""
        SELECT DISTINCT substr(i.taken_at, 1, 4) FROM image i
        WHERE i.taken_at IS NOT NULL AND {memories.PRECISE} AND {VISIBLE}
          AND substr(i.taken_at, 6, 5) IN (SELECT value FROM json_each(?))""", (json.dumps(days),))
                    if y and y.isdecimal()}, reverse=True)
    chosen: list[dict] = []
    for year in years:
        if not 1800 <= year < today.year or len(chosen) >= MOST:
            continue
        middle = memories._same_day(today, year)
        spec = tours.clean({"title": "", "media": "both", "pick": {"mode": "all", "max": 100},
                            "when": {"mode": "range", "from": (middle - timedelta(days=memories.AROUND)).isoformat(),
                                     "to": (middle + timedelta(days=memories.AROUND)).isoformat()}})
        chosen += tours.photos(c, spec, blurry)
    if len(chosen) < FILL:
        have = {p["id"] for p in chosen}
        spec = tours.clean({"title": "", "media": "photos", "order": "shuffle",
                            "pick": {"mode": "spread", "max": FILL * 2}})
        chosen += [p for p in tours.photos(c, spec, blurry, seed=today.toordinal()) if p["id"] not in have]
    return chosen[:MOST]


def slideshow(c, chosen: list[dict]) -> list[dict]:
    """What the frame shows of each photo: its picture (a video's, to play muted), the date and the caption (an
    album's own for it, else the family's). No names: a frame on a wall is seen by whoever passes. A photo of an album
    with no date shows none."""
    made = {x["id"]: x for x in tours.slides(c, [p for p in chosen if p.get("taken")])}
    undated = comments.captions(c, [p["id"] for p in chosen if not p.get("taken")])
    out = []
    for p in chosen:
        x = made.get(p["id"]) or {"src": f"/p/{p['sha']}", "poster": f"/p/{p['sha']}", "video": False, "when": "",
                                  "caption": undated.get(p["id"], "")}
        out.append({"src": x["src"], "poster": x["poster"], "video": x["video"], "when": x["when"],
                    "caption": p.get("album_caption") or x["caption"]})
    return out


def seconds_from(value) -> int:
    """How long each photo stays, as one of devices.SECONDS."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 20
    return n if n in devices.SECONDS else 20
