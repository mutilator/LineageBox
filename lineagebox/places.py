"""Places on the world map: where photos were taken (their GPS, else the place their folder names), and where family
events happened.

A place name (a folder's "Grand Canyon, Arizona", an event's "Portland, Oregon, USA") is found on the map by
OpenStreetMap's Nominatim, when places_lookup is on: that sends the names to nominatim.openstreetmap.org, so it is off
until switched on. Its usage policy (https://operations.osmfoundation.org/policies/nominatim/) is kept here:
- at most one lookup a second, and lookups that run on a schedule (the worker's) at most 4 a minute;
- a User-Agent that names the app, and the install's contact email when one is set (places_contact);
- every answer is kept, misses too, so each name is looked up once; a wrong one can be looked up again as something
  else, or taken off the map, on the map page;
- the server is a setting (places_url), so an install can move to another Nominatim without an update.

Photos with GPS are named after the nearest town without going online (towns.py, a list that comes with LineageBox).

A photo or a folder can also be put on the map by hand, on the map page: a photo by anyone who can see it (tracked, so
it can be undone), a folder by an admin. Where a photo is, the first of:
1. its own place by hand (image_place): a phone's GPS can be wrong, and a person's word wins;
2. its GPS;
3. the nearest folder above it (or its own) that is placed by hand (folder_place) or whose name gives a place on the
   map; at one folder, by hand wins.
A place by hand is "confirmed GPS": it goes into the photo's XMP sidecar (sidecars.py) when those are on, and into the
file only when an admin asks (writeback.py). A place from a folder's name is a town's or landmark's centre, so it stays
in the catalog.
"""
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import datetime

import httpx

from lineagebox import __version__, activity, errors, events, settings, towns
from lineagebox.catalog import VISIBLE
from lineagebox.config import Config

AGENT = f"LineageBox/{__version__} (self-hosted family archive)"
PER_PASS = 20        # lookups per worker pass: about 5 minutes at WORKER_GAP
WORKER_GAP = 15      # seconds between the worker's lookups: 4 a minute, as the policy asks of scheduled work
ASKED_GAP = 1.1      # seconds between lookups someone asked for on the map page: under one a second
SAMPLES = 12         # photos shown for a place on the map
_last = [0.0]        # when this process last asked, so lookups from different requests keep the gap too
_turn = threading.Lock()


class Problem(errors.Problem):
    """A change that cannot be made; the message is shown to the person who asked for it."""


def names_to_find(con: sqlite3.Connection) -> list[str]:
    return [r[0] for r in con.execute("""SELECT DISTINCT name FROM (
                                           SELECT trim(place) AS name FROM folder WHERE place IS NOT NULL AND NOT ignored
                                           UNION SELECT trim(place) FROM event WHERE place IS NOT NULL)
                                         WHERE name != '' AND name NOT IN (SELECT name FROM place) ORDER BY name""")]


def look_up(query: str, cfg: Config, http: httpx.Client | None = None,
            gap: float = ASKED_GAP) -> tuple[float, float, str] | None:
    """(latitude, longitude, what OpenStreetMap calls it), or None if it knows no such place. Waits until `gap`
    seconds have passed since this process last asked."""
    params = {"q": query, "format": "jsonv2", "limit": 1}
    if cfg.places_contact:
        params["email"] = cfg.places_contact
    with _turn:
        time.sleep(max(0.0, _last[0] + gap - time.monotonic()))
        _last[0] = time.monotonic()
    http = http or httpx.Client(timeout=20)
    r = http.get(cfg.places_url, params=params, headers={"User-Agent": AGENT, "Accept-Language": "en"})
    r.raise_for_status()
    found = r.json()
    if not found:
        return None
    return float(found[0]["lat"]), float(found[0]["lon"]), found[0].get("display_name", "")[:300]


REFUSED_WAIT = 24 * 3600  # after OpenStreetMap refuses (403, 429), unless it says how long in Retry-After
WAIT_KEY = "places_wait_until"  # settings: unix time before which no lookups are made


def find_new(con: sqlite3.Connection, cfg: Config, stop=None, limit: int = PER_PASS) -> int:
    """Look up place names not looked up yet, WORKER_GAP seconds apart. Returns how many were found on the map. When
    OpenStreetMap can't be reached or refuses, the rest wait for the next pass, and the Status page says why; after a
    refusal (the usage policy's rate limit, or a block) nothing is asked for a day."""
    if time.time() < (settings.get(con, WAIT_KEY) or 0):
        return 0
    found = 0
    http = httpx.Client(timeout=20)
    for name in names_to_find(con)[:limit]:
        if stop is not None and stop():
            break
        try:
            hit = look_up(name, cfg, http, gap=WORKER_GAP)
        except (httpx.HTTPError, ValueError, KeyError) as e:
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (403, 429):
                after = e.response.headers.get("retry-after", "")
                wait = int(after) if after.isdecimal() else REFUSED_WAIT
                settings.put(con, WAIT_KEY, time.time() + wait)
                why = (f"OpenStreetMap refused the lookups ({e.response.status_code}), so none are made for "
                       f"{'a day' if wait == REFUSED_WAIT else f'{wait // 60} minutes'}")
            elif isinstance(e, httpx.HTTPStatusError):
                why = f"OpenStreetMap answered {e.response.status_code}"
            elif isinstance(e, httpx.HTTPError):
                why = f"OpenStreetMap could not be reached ({type(e).__name__})"
            else:
                why = "OpenStreetMap's answer could not be read"
            activity.note_problem(con, "places", f"{why}. Place names wait to be put on the map.")
            break
        _store(con, name, hit, "osm")
        con.commit()
        activity.note_problem(con, "places", None)
        found += hit is not None
    return found


def fix(con: sqlite3.Connection, cfg: Config, name: str, query: str = "") -> bool:
    """A place in the wrong spot: look it up again as `query` ("Grand Canyon Village, Arizona"), or with no query take
    it off the map. True if it is on the map afterwards."""
    if not con.execute("SELECT 1 FROM place WHERE name = ?", (name,)).fetchone():
        raise Problem("No such place.")
    hit = None
    if query.strip():
        try:
            hit = look_up(query.strip(), cfg)
        except httpx.HTTPError as e:
            raise Problem(f"OpenStreetMap did not answer ({type(e).__name__}).") from None
        if hit is None:
            raise Problem(f"OpenStreetMap doesn't know {query.strip()!r}.")
    _store(con, name, hit, "user")
    return hit is not None


def confirm(con: sqlite3.Connection, name: str, right: bool = True) -> None:
    """A place is where the map shows it (or, right=False, that is taken back): it shows as confirmed."""
    if not con.execute("SELECT 1 FROM place WHERE name = ? AND lat IS NOT NULL", (name,)).fetchone():
        raise Problem("That place is not on the map.")
    con.execute("UPDATE place SET confirmed_at = ? WHERE name = ?",
                (datetime.now().isoformat(timespec="seconds") if right else None, name))


def _store(con, name: str, hit, source: str) -> None:
    """Where a place name is; stored again, it moves, so it is no longer confirmed."""
    lat, lon, found_as = hit if hit else (None, None, None)
    con.execute("""INSERT INTO place(name, lat, lon, found_as, source, looked_up) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET lat = excluded.lat, lon = excluded.lon, found_as = excluded.found_as,
                   source = excluded.source, looked_up = excluded.looked_up, confirmed_at = NULL""",
                (name, lat, lon, found_as, source, datetime.now().isoformat(timespec="seconds")))


def map_data(con: sqlite3.Connection, year: int | None = None) -> dict:
    """What the map shows for one year (or every year): photo places with how many photos and a few of them, family
    events with places, the photos per year (with a camera or folder date, or only a file time), and how many photos
    of the year have no place."""
    one_each = "(i.dup_group_id IS NULL OR i.dup_group_id = i.id)"
    years = defaultdict(lambda: {"dated": 0, "undated": 0})
    for y, dated, n in con.execute(f"""SELECT substr(i.taken_at, 1, 4), i.taken_at_source != 'mtime', count(*)
                                       FROM image i WHERE {VISIBLE} AND {one_each} AND i.taken_at IS NOT NULL
                                       GROUP BY 1, 2"""):
        years[int(y)]["dated" if dated else "undated"] += n
    in_year, args = "", []
    if year is not None:
        in_year, args = "AND substr(i.taken_at, 1, 4) = ?", [f"{year:04d}"]

    places: dict[tuple, dict] = {}

    # key[0] is how the place is known: "placed" by hand (a photo's or a folder's), "gps", or from a folder's name,
    # "confirmed" by someone or "folder" (not yet)
    def add(key, name, lat, lon, image, folder=None):
        p = places.setdefault(key, {"name": name, "lat": lat, "lon": lon, "count": 0, "photos": [], "folder": folder,
                                    "kind": key[0]})
        p["count"] += 1
        if len(p["photos"]) < SAMPLES:
            p["photos"].append({"id": image["id"], "sha": image["sha256"]})

    names: dict[tuple, str] = {}

    def town(lat, lon, otherwise):
        if (lat, lon) not in names:
            names[(lat, lon)] = towns.label(lat, lon) or otherwise
        return names[(lat, lon)]

    hand = HandPlaces(con)
    coords = {r["name"]: (r["lat"], r["lon"]) for r in con.execute("SELECT * FROM place WHERE lat IS NOT NULL")}
    confirmed = {r[0] for r in con.execute("SELECT name FROM place WHERE lat IS NOT NULL AND confirmed_at IS NOT NULL")}
    unplaced = 0
    for r in con.execute(f"""SELECT i.id, i.sha256, i.gps_lat, i.gps_lon, min(f.folder_id) AS folder_id FROM image i
                             JOIN file f ON f.image_id = i.id AND NOT f.missing
                             WHERE {VISIBLE} AND {one_each} {in_year} GROUP BY i.id ORDER BY i.taken_at DESC""", args):
        if (own := hand.photos.get(r["id"])) is not None:
            lat, lon = round(own[0], 4), round(own[1], 4)  # each spot someone picked: about 10 m
            add(("placed", lat, lon), town(round(own[0], 2), round(own[1], 2), "Placed by hand"), lat, lon, r)
            continue
        if r["gps_lat"] is not None:
            lat, lon = round(r["gps_lat"], 2), round(r["gps_lon"], 2)  # about a kilometre
            add(("gps", lat, lon), town(lat, lon, "Taken here (GPS)"), lat, lon, r)
            continue
        hit = hand.folder(r["folder_id"], coords)
        if hit is None:
            unplaced += 1
            continue
        fid, name, spot = hit
        if spot is not None:  # a folder placed by hand
            add(("placed", "folder", fid), hand.folder_name(fid), *spot, r, folder=fid)
        else:
            add(("confirmed" if name in confirmed else "folder", name), name, *coords[name], r, folder=fid)

    family_events = []
    for e in con.execute(f"""SELECT e.*, coalesce(p.name, a.name || ' and ' || b.name) AS who FROM event e
                             LEFT JOIN person p ON p.id = e.person_id LEFT JOIN person a ON a.id = e.couple_a
                             LEFT JOIN person b ON b.id = e.couple_b
                             WHERE e.place IS NOT NULL {"AND substr(e.sort_date, 1, 4) = ?" if year is not None else ""}""",
                         args):
        if (name := e["place"].strip()) in coords:
            kinds = events.PERSON_EVENTS if e["person_id"] is not None else events.COUPLE_EVENTS
            family_events.append({"place": name, "lat": coords[name][0], "lon": coords[name][1], "who": e["who"],
                                  "label": (e["type"] if e["tag"] == "EVEN" and e["type"] else
                                            kinds.get(e["tag"], e["tag"])).lower(),
                                  "date": events.show_date(e["date"]), "person": e["person_id"] or e["couple_a"]})
    return {"years": [{"year": y, **years[y]} for y in sorted(years)], "places": list(places.values()),
            "events": family_events, "unplaced": unplaced,
            "to_find": len(names_to_find(con)), "not_found": con.execute(
                "SELECT count(*) FROM place WHERE lat IS NULL AND source = 'osm'").fetchone()[0]}


# ---- placed by hand -----------------------------------------------------------------------------------------------

class HandPlaces:
    """Photos and folders placed on the map by hand, read once for many photos (the map, the sidecars, the export).
    On a member's connection, only theirs to see."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con
        self.photos = {r[0]: (r[1], r[2]) for r in con.execute("SELECT image_id, lat, lon FROM image_place")}
        self.folders = {r[0]: (r[1], r[2]) for r in con.execute("SELECT folder_id, lat, lon FROM folder_place")}
        self._tree = None
        self._named = None
        self._near: dict[tuple[int, bool], tuple | None] = {}

    @property
    def tree(self) -> dict:
        if self._tree is None:
            self._tree = {r[0]: (r[1], (r[2] or "").strip(), r[3]) for r in
                          self.con.execute("SELECT id, parent_id, place, name FROM folder")}
        return self._tree

    @property
    def named(self) -> dict:
        """Place names found on the map, as a folder's name gives one: (lat, lon) by name."""
        if self._named is None:
            self._named = {r[0]: (r[1], r[2]) for r in
                           self.con.execute("SELECT name, lat, lon FROM place WHERE lat IS NOT NULL")}
        return self._named

    def folder_name(self, folder_id: int) -> str:
        return (self.tree.get(folder_id) or (None, "", ""))[2] or "Placed by hand"

    def folder(self, folder_id: int | None, coords: dict | None = None):
        """The nearest folder, up from this one, placed by hand or (given the coordinates of place names) named for a
        place on the map: (folder id, the place's name, (lat, lon) when by hand), or None."""
        key = (folder_id, coords is not None)
        if key in self._near:
            return self._near[key]
        path, fid, found = [], folder_id, None
        while fid is not None and fid not in path:  # up the tree; a cycle would be a damaged catalog
            path.append(fid)
            if (fid, coords is not None) in self._near:
                found = self._near[(fid, coords is not None)]
                break
            if fid in self.folders:
                found = (fid, None, self.folders[fid])
                break
            parent, name, _ = self.tree.get(fid, (None, "", ""))
            if coords is not None and name and name in coords:
                found = (fid, name, None)
                break
            fid = parent
        for f in path:
            self._near[(f, coords is not None)] = found
        return found

    def of(self, image_id: int, folder_id: int | None = None) -> tuple[float, float, str] | None:
        """Where a photo was taken by someone's word: (lat, lon, "photo" or "folder"), or None. folder_id: its first
        file's folder, if known."""
        if image_id in self.photos:
            return (*self.photos[image_id], "photo")
        if not self.folders:
            return None
        if folder_id is None:
            row = self.con.execute("SELECT min(folder_id) FROM file WHERE image_id = ? AND NOT missing",
                                   (image_id,)).fetchone()
            folder_id = row[0] if row else None
        # the nearest folder with a place, as on the map: one named for a place, below one placed by hand, wins, and
        # its place (a town's centre) stays in the catalog
        hit = self.folder(folder_id, self.named)
        return (*hit[2], "folder") if hit and hit[2] else None


def _check_spot(lat, lon) -> tuple[float, float]:
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        raise Problem("A place is a latitude and a longitude.") from None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise Problem("That isn't a place on the map.")
    return round(lat, 6), round(lon, 6)  # about 10 cm: more than any map click means


def place_photo(con: sqlite3.Connection, image_id: int, lat, lon, account_id: int | None = None) -> None:
    """Put a photo on the map by hand (or move it there). Doesn't commit."""
    lat, lon = _check_spot(lat, lon)
    con.execute("""INSERT INTO image_place(image_id, lat, lon, account_id, at) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(image_id) DO UPDATE SET lat = excluded.lat, lon = excluded.lon,
                   account_id = excluded.account_id, at = excluded.at""",
                (image_id, lat, lon, account_id, datetime.now().isoformat(timespec="seconds")))


def place_folder(con: sqlite3.Connection, folder_id: int, lat, lon, account_id: int | None = None) -> None:
    """Put a folder on the map by hand: its photos, and those of the folders in it, without a place of their own."""
    lat, lon = _check_spot(lat, lon)
    con.execute("""INSERT INTO folder_place(folder_id, lat, lon, account_id, at) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(folder_id) DO UPDATE SET lat = excluded.lat, lon = excluded.lon,
                   account_id = excluded.account_id, at = excluded.at""",
                (folder_id, lat, lon, account_id, datetime.now().isoformat(timespec="seconds")))


def unplace_photo(con: sqlite3.Connection, image_id: int) -> None:
    con.execute("DELETE FROM image_place WHERE image_id = ?", (image_id,))


def unplace_folder(con: sqlite3.Connection, folder_id: int) -> None:
    con.execute("DELETE FROM folder_place WHERE folder_id = ?", (folder_id,))


def photo_place(con: sqlite3.Connection, image_id: int, image) -> dict | None:
    """Where the photo page says a photo was taken: {lat, lon, how, label, folder}. how: "photo" (placed by hand),
    "gps", "folder" (its folder placed by hand) or "name" (a place its folder's name gives, found on the map)."""
    hand = HandPlaces(con)
    folder_id = con.execute("SELECT min(folder_id) FROM file WHERE image_id = ? AND NOT missing",
                            (image_id,)).fetchone()[0]
    spot, how, folder, name = None, None, None, None
    if image_id in hand.photos:
        spot, how = hand.photos[image_id], "photo"
    elif image["gps_lat"] is not None and image["gps_lon"] is not None:
        spot, how = (image["gps_lat"], image["gps_lon"]), "gps"
    else:
        coords = {r[0]: (r[1], r[2]) for r in con.execute("SELECT name, lat, lon FROM place WHERE lat IS NOT NULL")}
        hit = hand.folder(folder_id, coords)
        if hit is None:
            return None
        folder, name, by_hand = hit
        spot, how = (by_hand, "folder") if by_hand else (coords[name], "name")
    label = name if how == "name" else towns.label(*spot)
    return {"lat": spot[0], "lon": spot[1], "how": how, "label": label, "folder": folder}
