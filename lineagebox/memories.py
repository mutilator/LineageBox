"""Home's row of memories: photos from this week in earlier years, tours worth making, and the viewer's own tours.

Each memory is a tour, saved or not. Its card opens it: an unsaved one at /tours/try, where it plays like any tour
and can be kept as one. Its photos are picked by tours.photos on the viewer's own connection, so a member's memories
are only of what they see. Nothing here knows whose photos these are: the years, people and occasions all come from
what the catalog holds, and the row changes from day to day (this week in other years, a different tour of the
viewer's own, another cover).
"""
import json
import random
from datetime import date, timedelta
from urllib.parse import urlencode

from lineagebox import tours
from lineagebox.catalog import VISIBLE

LEAST = 4       # photos a memory needs
AROUND = 3      # days either side of today's date that count as "this week", in earlier years
YEARS_AGO = 3   # "N years ago" memories at most: the years with the most photos this week
OWN_TOURS = 2   # of the viewer's own tours, a different few each day
MOST = 12       # memories in the row
# a date to the day: not a file's time, nor a year read from a folder's name, nor a date given by hand as a span
PRECISE = ("i.taken_at_source NOT IN ('mtime', 'folder') "
           "AND (i.taken_at_source != 'user' OR i.date_from = i.date_to)")


def try_href(body: dict) -> str:
    """Where a tour not saved yet plays: what POST /api/tours would make of the body."""
    return "/tours/try?" + urlencode({"body": json.dumps(body, separators=(",", ":"), sort_keys=True)})


def years_ago(con, today: date, blurry_below: float | None = None) -> list[dict]:
    """This week in earlier years: a memory for each of the YEARS_AGO years with the most photos taken within AROUND
    days of today's date, the latest year first."""
    days = dict(con.execute(f"""SELECT substr(i.taken_at, 1, 10) AS day, COUNT(*) FROM image i
                                WHERE i.taken_at IS NOT NULL AND {PRECISE} AND {VISIBLE}
                                  AND (i.dup_group_id IS NULL OR i.dup_group_id = i.id)
                                GROUP BY day"""))
    ranked = []
    for year in sorted({int(d[:4]) for d in days if d[:4].isdecimal()}):
        if not 1800 <= year < today.year:
            continue
        middle = _same_day(today, year)
        first, last = middle - timedelta(days=AROUND), middle + timedelta(days=AROUND)
        n = sum(days.get((first + timedelta(days=k)).isoformat(), 0) for k in range((last - first).days + 1))
        if n >= LEAST:
            ranked.append((n, year, first, last))
    out = []
    busiest = sorted(ranked, reverse=True)[:YEARS_AGO]
    for _, year, first, last in sorted(busiest, key=lambda r: -r[1]):
        spec = tours.clean({"title": "", "when": {"mode": "range", "from": first.isoformat(), "to": last.isoformat()},
                            "media": "both", "pick": {"mode": "all", "max": 300}})
        found = tours.photos(con, spec, blurry_below)
        if len(found) < LEAST:
            continue
        spec["title"] = span(found)
        ago = today.year - year
        out.append({"title": f"{ago} year{'' if ago == 1 else 's'} ago", "sub": spec["title"],
                    "cover": cover(found, today), "count": len(found), "href": try_href({"spec": spec})})
    return out


def _same_day(today: date, year: int) -> date:
    try:
        return today.replace(year=year)
    except ValueError:  # the 29th of February, in a year without one
        return date(year, 2, 28)


def span(found: list[dict]) -> str:
    """The days the photos are from, in words: 'November 5, 2021', 'November 3 to 6, 2021'."""
    days = sorted({p["taken"][:10] for p in found})
    a, b = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    if a == b:
        return f"{a:%B} {a.day}, {a.year}"
    if (a.year, a.month) == (b.year, b.month):
        return f"{a:%B} {a.day} to {b.day}, {a.year}"
    if a.year == b.year:
        return f"{a:%B} {a.day} to {b:%B} {b.day}, {a.year}"
    return f"{a:%B} {a.day}, {a.year} to {b:%B} {b.day}, {b.year}"


def cover(found: list[dict], today: date) -> str:
    """One of the best few photos, another each day."""
    best = sorted(found, key=lambda p: (-p["score"], p["id"]))[:6]
    return random.Random(today.toordinal()).choice(best)["sha"]


def own_tours(con, saved: list[dict], today: date, blurry_below: float | None = None) -> list[dict]:
    """A few of the viewer's own tours (saved: those, as tours.tours has them), a different few each day."""
    picked = random.Random(today.toordinal()).sample(saved, min(OWN_TOURS, len(saved)))
    out = []
    for t in picked:
        found = tours.photos(con, t["spec"], blurry_below, seed=t["id"])
        if found:
            out.append({"title": t["name"], "sub": f"Tour · {len(found):,} photo{'' if len(found) == 1 else 's'}",
                        "cover": cover(found, today), "count": len(found), "href": f"/tour/{t['id']}"})
    return out


def suggested(suggestions: list[dict], today: date) -> list[dict]:
    """The Tours page's suggestions (tour_suggestions.suggest), as memories: each opens before it is made."""
    return [{"title": s["name"], "sub": s["why"], "count": s["count"], "href": try_href(s["body"]),
             "cover": s["covers"][today.toordinal() % len(s["covers"])]} for s in suggestions if s["covers"]]


def row(years: list[dict], suggestions: list[dict], own: list[dict]) -> list[dict]:
    """The memories in the order Home shows them: this week in other years, the two timeliest suggestions (a
    birthday or an occasion coming up comes first), the viewer's own tours, then the other suggestions."""
    return (years + suggestions[:2] + own + suggestions[2:])[:MOST]
