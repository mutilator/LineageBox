"""Names for places without going online: the nearest town to a photo's GPS, and towns found by name for placing a
photo on the map by hand, from a list that comes with LineageBox.

The list (data/towns.tsv.gz) is GeoNames' towns and cities of over 5,000 people, about 70,000 of them, trimmed to a
name, a position to about 100 m, the country and region, and how many people live there
(https://www.geonames.org/, CC BY 4.0: the map page and THIRD_PARTY_NOTICES.md say so). 5,000 people is the size that
keeps the list under a megabyte while a photo taken in the country is still near a town it can be named after; GeoNames'
list of towns over 1,000 is two and a half times the size. A photo is named after the nearest town, not the biggest one
near it: a village a kilometre away says more than a city twenty kilometres off.

It is read the first time a name is wanted (a fifth of a second), and kept in memory: about 10 MB.
"""
import gzip
import math
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

DATA = Path(__file__).resolve().parent / "data" / "towns.tsv.gz"
ATTRIBUTION = "Town names: GeoNames (geonames.org), CC BY 4.0"
NEAR_KM = 10      # closer than this, a photo was taken in the town: "Springfield"
AROUND_KM = 50    # closer than this, near it: "near Springfield"; farther, how far: "60 km from Springfield"
EARTH_KM = 6371.0
CELL = 1.0        # degrees: towns are kept by the square of the map they are in, to look at a few squares only
_lock = threading.Lock()
_loaded: "_Towns | None" = None
_missing = False  # the list couldn't be read


@dataclass(frozen=True)
class Town:
    name: str
    lat: float
    lon: float
    region: str     # "Illinois"; "" when GeoNames gives none
    country: str    # "United States"
    people: int     # in thousands

    @property
    def full(self) -> str:
        """"Springfield, Illinois, United States": the region left out when it is the town's own name (Berlin)."""
        return ", ".join(p for p in dict.fromkeys((self.name, self.region, self.country)) if p)


class _Towns:
    def __init__(self, path: Path):
        countries, regions, rows = {}, {}, []
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                if line.startswith("#"):
                    continue
                part = line.rstrip("\n").split("\t")
                if part[0] == "C" and len(part) == 3:
                    countries[part[1]] = part[2]
                elif part[0] == "R" and len(part) == 3:
                    regions[part[1]] = part[2]
                elif len(part) == 6:
                    rows.append(part)
        self.towns = [Town(r[0], float(r[1]), float(r[2]), regions.get(f"{r[3]}.{r[4]}", ""),
                           countries.get(r[3], r[3]), int(r[5] or 0)) for r in rows]
        self.lat = np.radians(np.array([t.lat for t in self.towns]))
        self.lon = np.radians(np.array([t.lon for t in self.towns]))
        self.cells: dict[tuple[int, int], np.ndarray] = {}
        cells: dict[tuple[int, int], list[int]] = {}
        for i, t in enumerate(self.towns):
            cells.setdefault(_cell(t.lat, t.lon), []).append(i)
        self.cells = {k: np.array(v) for k, v in cells.items()}
        self.names = [t.name.casefold() for t in self.towns]

    def nearest(self, lat: float, lon: float) -> tuple[Town, float] | None:
        """The nearest town and how far it is (km): the squares around the point, wider until one has a town."""
        ci, cj = _cell(lat, lon)
        for reach in (1, 2, 4, 8, 16, 32, 64, 180):
            found = [self.cells[(i, ((j + 180) % 360) - 180)] for i in range(ci - reach, ci + reach + 1)
                     for j in range(cj - reach, cj + reach + 1) if (i, ((j + 180) % 360) - 180) in self.cells]
            if found:
                idx = np.concatenate(found)
                km = _distance(math.radians(lat), math.radians(lon), self.lat[idx], self.lon[idx])
                best = int(np.argmin(km))
                # a town in a square further out can still be nearer than this one: only sure within `reach` squares
                if km[best] <= reach * CELL * 111.0 * max(0.2, math.cos(math.radians(min(abs(lat), 80)))) \
                        or reach == 180:
                    return self.towns[int(idx[best])], float(km[best])
        return None

    def named(self, text: str, limit: int) -> list[Town]:
        """Towns whose name starts as typed, the most people first."""
        text = " ".join(text.split()).casefold()
        if not text:
            return []
        hits = [i for i, n in enumerate(self.names) if n.startswith(text)]
        hits.sort(key=lambda i: (self.names[i] != text, -self.towns[i].people))
        return [self.towns[i] for i in hits[:limit]]


def _cell(lat: float, lon: float) -> tuple[int, int]:
    return int(math.floor(lat / CELL)), int(math.floor(lon / CELL))


def _distance(lat: float, lon: float, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Great-circle distances in km (haversine)."""
    a = np.sin((lats - lat) / 2) ** 2 + math.cos(lat) * np.cos(lats) * np.sin((lons - lon) / 2) ** 2
    return 2 * EARTH_KM * np.arcsin(np.sqrt(np.minimum(a, 1.0)))


def _towns() -> _Towns | None:
    global _loaded, _missing
    if _loaded is None and not _missing:
        with _lock:
            if _loaded is None and not _missing:
                try:
                    _loaded = _Towns(DATA)
                # a copy of LineageBox without the list, or with it cut short: places just aren't named, and the
                # list isn't read again for every photo shown
                except (OSError, EOFError, ValueError, IndexError):
                    _missing = True
    return _loaded


def nearest(lat: float | None, lon: float | None) -> tuple[Town, float] | None:
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat, lon) == (0, 0):
        return None  # 0, 0 is a camera that wrote GPS without a fix: out at sea off Africa
    towns = _towns()
    return towns.nearest(lat, lon) if towns else None


def label(lat: float | None, lon: float | None) -> str | None:
    """Where a GPS position is, in words: "Springfield, Illinois, United States", "near Springfield, ..." or
    "60 km from Springfield, ..."; None when it can't be said."""
    found = nearest(lat, lon)
    if found is None:
        return None
    town, km = found
    if km < NEAR_KM:
        return town.full
    if km < AROUND_KM:
        return f"near {town.full}"
    return f"{km:,.0f} km from {town.full}"


def search(text: str, limit: int = 8) -> list[dict]:
    """Towns to jump to on the map while placing a photo by hand."""
    towns = _towns()
    if towns is None:
        return []
    return [{"name": t.full, "lat": t.lat, "lon": t.lon} for t in towns.named(text, limit)]
