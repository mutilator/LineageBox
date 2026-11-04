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
"""
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import datetime

import httpx

from lineagebox import __version__, activity, errors, events, settings
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

    # key[0] is how the place is known: "gps", or from a folder's name, "confirmed" by someone or "folder" (not yet)
    def add(key, name, lat, lon, image, folder=None):
        p = places.setdefault(key, {"name": name, "lat": lat, "lon": lon, "count": 0, "photos": [], "folder": folder,
                                    "kind": key[0]})
        p["count"] += 1
        if len(p["photos"]) < SAMPLES:
            p["photos"].append({"id": image["id"], "sha": image["sha256"]})

    for r in con.execute(f"""SELECT i.id, i.sha256, i.gps_lat, i.gps_lon FROM image i
                             WHERE {VISIBLE} AND {one_each} AND i.gps_lat IS NOT NULL {in_year}
                             ORDER BY i.taken_at DESC""", args):
        lat, lon = round(r["gps_lat"], 2), round(r["gps_lon"], 2)  # about a kilometre
        add(("gps", lat, lon), "Taken here (GPS)", lat, lon, r)

    coords = {r["name"]: (r["lat"], r["lon"]) for r in con.execute("SELECT * FROM place WHERE lat IS NOT NULL")}
    confirmed = {r[0] for r in con.execute("SELECT name FROM place WHERE lat IS NOT NULL AND confirmed_at IS NOT NULL")}
    folders = {r["id"]: r for r in con.execute("SELECT id, parent_id, place, name FROM folder")}
    where = {}  # folder -> the nearest folder above it (or itself) with a place on the map

    def placed(fid):
        if fid not in where:
            f = folders.get(fid)
            name = (f["place"] or "").strip() if f else ""
            where[fid] = (fid, name) if name in coords else placed(f["parent_id"]) if f and f["parent_id"] else None
        return where[fid]

    unplaced = 0
    for r in con.execute(f"""SELECT i.id, i.sha256, i.gps_lat, min(f.folder_id) AS folder_id FROM image i
                             JOIN file f ON f.image_id = i.id AND NOT f.missing
                             WHERE {VISIBLE} AND {one_each} {in_year} GROUP BY i.id ORDER BY i.taken_at DESC""", args):
        if r["gps_lat"] is not None:
            continue
        hit = placed(r["folder_id"])
        if hit is None:
            unplaced += 1
            continue
        fid, name = hit
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
