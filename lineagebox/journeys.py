"""A journey: an album read as a trip, stop by stop, to draw on the map.

An album already holds photos in the order its maker wants them (albums.py), and that is just what a trip is: the
photos of the first place, then those of the next, and so on. Walking an album in its own order and putting
consecutive photos taken near one another together gives the stops -- home, the mountains, the park, the monument,
the shopping trip, home again -- and the line from each to the next is the way the family travelled. Coming back at
the end to the place they set out from is a stop of its own, as it was a day of its own: only photos next to each
other in the album join up.

Where each photo was taken is worked out exactly as the map does it (places.py): a place put on it by hand wins, then
the camera's GPS, then the nearest folder put on the map by hand or named for a place that was found. A photo with no
place at all is counted and left out of the line, so the page can say how many there are and where to put them.

How near counts as the same place is the family's to pick (NEARNESS): 5 km keeps neighboring towns apart, 100 km
makes a day's drive one stop. Nothing is kept in the catalog -- a journey is read from the album every time it is
shown -- so putting the photos in another order, or on the map, changes it at once, with nothing to keep up to date.
"""
import json
import sqlite3
from datetime import date

from lineagebox import albums, places, towns
from lineagebox.catalog import ORIGIN, VISIBLE

NEAR_KM = 25.0                  # photos this close to the first of a stop belong to that stop
NEARNESS = (5.0, 25.0, 100.0)   # what the page offers instead
SAMPLES = 8                     # photos of a stop the map shows in its bubble
LEAST = 2                       # stops before an album draws a journey at all


def nearness(km) -> float:
    """The distance a page asked for, if it is one of those offered; else the usual one."""
    try:
        asked = float(km)
    except (TypeError, ValueError):
        return NEAR_KM
    return asked if asked in NEARNESS else NEAR_KM


def where(con: sqlite3.Connection, image_ids) -> dict[int, dict]:
    """Where each of these photos was taken, as the map works it out: {image id: {lat, lon, how, name}}. `how` is
    "photo" (put on the map by hand), "gps", "folder" (a folder put there by hand) or "name" (a place a folder's name
    gives). Photos with no place at all are left out. On a member's connection, only what they may see."""
    ids = list(dict.fromkeys(image_ids))
    if not ids:
        return {}
    hand = places.HandPlaces(con)
    coords = {r[0]: (r[1], r[2]) for r in con.execute("SELECT name, lat, lon FROM place WHERE lat IS NOT NULL")}
    found: dict[int, dict] = {}
    for r in con.execute(f"""SELECT i.id, i.gps_lat, i.gps_lon, min(f.folder_id) AS folder_id, min({ORIGIN}) AS origin
                             FROM image i JOIN file f ON f.image_id = i.id AND NOT f.missing
                             WHERE i.id IN (SELECT value FROM json_each(?)) AND {VISIBLE} GROUP BY i.id""",
                         (json.dumps(ids),)):
        if (own := hand.photos.get(r["id"])) is not None:
            found[r["id"]] = {"lat": own[0], "lon": own[1], "how": "photo", "name": None}
        elif r["gps_lat"] is not None and r["gps_lon"] is not None:
            found[r["id"]] = {"lat": r["gps_lat"], "lon": r["gps_lon"], "how": "gps", "name": None}
        elif (hit := hand.nearest(r["origin"], r["folder_id"], coords)) is not None:
            folder_id, name, by_hand = hit
            if by_hand is not None:
                found[r["id"]] = {"lat": by_hand[0], "lon": by_hand[1], "how": "folder",
                                  "name": hand.folder_name(folder_id)}
            else:
                found[r["id"]] = {"lat": coords[name][0], "lon": coords[name][1], "how": "name", "name": name}
    return found


def _middle(lats: list[float], lons: list[float]) -> tuple[float, float]:
    """The middle of a stop's photos. Longitudes are averaged against the first one, so a stop that straddles the
    date line doesn't land on the other side of the world."""
    first = lons[0]
    lon = first + sum(((x - first + 180) % 360) - 180 for x in lons) / len(lons)
    return sum(lats) / len(lats), ((lon + 180) % 360) - 180


def _days(first: str | None, last: str | None) -> int | None:
    """How many days a journey took, both ends counted; None when either end has no date."""
    if not first or not last:
        return None
    try:
        return (date.fromisoformat(last) - date.fromisoformat(first)).days + 1
    except ValueError:
        return None


def of_photos(photos: list[dict], found: dict[int, dict], near_km: float = NEAR_KM) -> dict:
    """The journey a list of photos in their order makes, given where each was taken (`where` above). Each photo
    needs an "id"; "sha256", "taken_at", "caption" and "own" are used where they are there."""
    made: list[dict] = []
    unplaced = 0
    for p in photos:
        spot = found.get(p["id"])
        if spot is None:
            unplaced += 1
            continue
        if not made or towns.km_between(made[-1]["lat"], made[-1]["lon"], spot["lat"], spot["lon"]) > near_km:
            made.append({"lat": spot["lat"], "lon": spot["lon"], "how": spot["how"], "name": spot["name"],
                         "lats": [], "lons": [], "photos": [], "count": 0, "from": None, "to": None, "words": ""})
        stop = made[-1]
        stop["count"] += 1
        stop["lats"].append(spot["lat"])
        stop["lons"].append(spot["lon"])
        if len(stop["photos"]) < SAMPLES:
            stop["photos"].append({"id": p["id"], "sha": p.get("sha256")})
        if when := (p.get("taken_at") or "")[:10]:
            stop["from"] = when if stop["from"] is None else min(stop["from"], when)
            stop["to"] = when if stop["to"] is None else max(stop["to"], when)
        if not stop["words"]:
            stop["words"] = " ".join(str(p.get("caption") or p.get("own") or "").split())

    stops, last, km = [], None, 0.0
    for n, stop in enumerate(made, 1):
        lat, lon = _middle(stop["lats"], stop["lons"])
        # a place a folder's name gave is already a place's own name ("Yellowstone National Park"); otherwise the
        # nearest town says where this is, and failing that the folder someone put on the map, or the bare position
        name = stop["name"] if stop["how"] == "name" else towns.label(lat, lon) or stop["name"]
        leg = towns.km_between(last[0], last[1], lat, lon) if last else 0.0
        km += leg
        stops.append({"n": n, "name": name or f"{lat:.3f}, {lon:.3f}", "lat": round(lat, 5), "lon": round(lon, 5),
                      "how": stop["how"], "count": stop["count"], "photos": stop["photos"], "words": stop["words"],
                      "from": stop["from"], "to": stop["to"], "km": round(leg)})
        last = (lat, lon)
    dated = [d for s in stops for d in (s["from"], s["to"]) if d]
    return {"stops": stops, "unplaced": unplaced, "placed": sum(s["count"] for s in stops), "km": round(km),
            "days": _days(min(dated), max(dated)) if dated else None, "near": near_km}


def of_album(con: sqlite3.Connection, album_id: int, near_km: float = NEAR_KM) -> dict:
    """The journey an album draws, in the album's own order, as the connection sees its photos."""
    photos = albums.photos(con, album_id)
    return of_photos(photos, where(con, [p["id"] for p in photos]), near_km)


def on_map(con: sqlite3.Connection, near_km: float = NEAR_KM) -> list[dict]:
    """Every journey the map can draw: each album the connection sees whose photos make LEAST stops or more, with its
    stops in the album's order and the years its photos were taken (the map shows one year at a time). Worked out for
    all the albums together, and kept for a minute by the page that asks (routes/places.py)."""
    names = {r["id"]: r["name"] for r in con.execute("SELECT id, name FROM album ORDER BY changed_at DESC, id DESC")}
    if not names:
        return []
    order = {album_id: albums.photo_ids(con, album_id) for album_id in names}
    every = list(dict.fromkeys(i for ids in order.values() for i in ids))
    found = where(con, every)
    taken = dict(con.execute("SELECT id, taken_at FROM image WHERE id IN (SELECT value FROM json_each(?))",
                             (json.dumps(every),)))
    out = []
    for album_id, ids in order.items():
        made = of_photos([{"id": i, "taken_at": taken.get(i)} for i in ids], found, near_km)
        if len(made["stops"]) < LEAST:
            continue
        years = sorted({int(d[:4]) for s in made["stops"] for d in (s["from"], s["to"]) if d})
        out.append({"album_id": album_id, "name": names[album_id], "km": made["km"], "days": made["days"],
                    "years": years,
                    "stops": [{"n": s["n"], "name": s["name"], "lat": s["lat"], "lon": s["lon"],
                               "count": s["count"]} for s in made["stops"]]})
    return out


def how_many(con: sqlite3.Connection, album_ids, near_km: float = NEAR_KM) -> dict[int, int]:
    """How many stops each of these albums makes, worked out together: which of them draw a journey (LEAST or more),
    for the album pages and the list of albums to offer it."""
    order = {album_id: albums.photo_ids(con, album_id) for album_id in dict.fromkeys(album_ids)}
    found = where(con, [i for ids in order.values() for i in ids])
    return {album_id: len(of_photos([{"id": i} for i in ids], found, near_km)["stops"])
            for album_id, ids in order.items()}
