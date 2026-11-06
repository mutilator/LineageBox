"""Home's row of memories: photos from this week in earlier years, tours worth making, and the viewer's own tours. A
member's row starts with their own family's: their biggest days of photos and their latest, and this week in earlier
years in their photos (family_occasions, years_ago with people).

Each memory is a tour, saved or not. Its card opens it: an unsaved one at /tours/try, where it plays like any tour
and can be kept as one. Its photos are picked by tours.photos on the viewer's own connection, so a member's memories
are only of what they see. Nothing here knows whose photos these are: the years, people and occasions all come from
what the catalog holds, and the row changes from day to day (this week in other years, a different tour of the
viewer's own, another cover).
"""
import json
import random
import statistics
from collections import Counter, defaultdict
from datetime import date, timedelta
from urllib.parse import urlencode

from lineagebox import tours
from lineagebox.catalog import VISIBLE

LEAST = 4       # photos a memory needs
AROUND = 3      # days either side of today's date that count as "this week", in earlier years
YEARS_AGO = 3   # "N years ago" memories at most: the years with the most photos this week
OWN_TOURS = 2   # of the viewer's own tours, a different few each day
MOST = 12       # memories in the row
FAMILY_OCCASIONS = 8  # a member's own family's days of photos in the row: their latest, and their biggest
YEAR_EVENTS = 12      # events of each year on the Timeline: its biggest runs of days with photos
DENSE = 30            # days with photos from which most days are everyday ones, and only the busiest are events...
BUSY = 2              # ...those with this many times the photos of the usual day (the median)
OCCASION_TAGS = 0.3   # of a day's photos with a tag that says what it was ("wedding"), for it to name the day
SHOWN = 0.25          # of its photos someone is named in, for their life event (or "their" wedding) to name it
# what photos of a life event are tagged with (the AI's descriptions, in English): a wedding's, a birth's...
EVIDENCE = {"MARR": {"wedding", "bride", "groom", "wedding party", "wedding dress", "wedding cake"},
            "ENGA": {"engagement", "engagement ring", "proposal"}, "BIRT": {"baby", "newborn", "infant"},
            "BAPM": {"baptism", "christening"}, "CHR": {"baptism", "christening"},
            "GRAD": {"graduation", "graduate", "diploma", "cap and gown"}, "RETI": {"retirement", "retirement party"},
            "DEAT": {"funeral", "memorial", "memorial service", "casket", "coffin", "cemetery", "graveside",
                     "grave", "burial", "urn"}}
# What a day of photos was, by the tags on them (the AI's descriptions give them, in English): with whose it was
# (the family member in the most of its photos), or the year. Generic words, whatever the catalog holds.
OCCASIONS = {"wedding": ("{whose} wedding", "A wedding, {year}"), "birthday": (None, "A birthday, {year}"),
             "graduation": ("{whose} graduation", "A graduation, {year}"),
             "baptism": ("{whose} baptism", "A baptism, {year}"), "funeral": (None, "A funeral, {year}"),
             "baby shower": ("{whose} baby shower", "A baby shower, {year}"),
             "bridal shower": ("{whose} bridal shower", "A bridal shower, {year}"),
             "christmas": (None, "Christmas {year}"), "thanksgiving": (None, "Thanksgiving {year}"),
             "halloween": (None, "Halloween {year}"), "easter": (None, "Easter {year}"),
             "fourth of july": (None, "Fourth of July {year}"), "new year's eve": (None, "New Year's Eve {year}"),
             "reunion": (None, "Family reunion, {year}"), "camping": (None, "Camping, {year}"),
             "vacation": (None, "Vacation, {year}")}
# life events of the family on those days, which name them first
EVENT_WORDS = {"MARR": "{a} and {b}'s wedding", "ENGA": "{a} and {b} engaged", "BIRT": "{a} is born",
               "BAPM": "{whose} baptism", "CHR": "{whose} christening", "GRAD": "{whose} graduation",
               "RETI": "{a} retires", "BURI": "Remembering {a}", "DEAT": "Remembering {a}"}
# a date to the day: not a file's time, nor a year read from a folder's name, nor a date given by hand as a span
PRECISE = ("i.taken_at_source NOT IN ('mtime', 'folder') "
           "AND (i.taken_at_source != 'user' OR i.date_from = i.date_to)")


def try_href(body: dict) -> str:
    """Where a tour not saved yet plays: what POST /api/tours would make of the body."""
    return "/tours/try?" + urlencode({"body": json.dumps(body, separators=(",", ":"), sort_keys=True)})


def of_people(column: str = "i.id") -> str:
    """SQL: the photo is one someone of :people (a JSON list) is named in, by a face or added to it."""
    return f"""{column} IN (SELECT image_id FROM face WHERE person_id IN (SELECT value FROM json_each(:people))
                         UNION SELECT image_id FROM image_person
                               WHERE person_id IN (SELECT value FROM json_each(:people)))"""


def years_ago(con, today: date, blurry_below: float | None = None, people: list[int] | None = None) -> list[dict]:
    """This week in earlier years: a memory for each of the YEARS_AGO years with the most photos taken within AROUND
    days of today's date, the latest year first. people: only photos of them (a member's own family)."""
    mine = f"AND {of_people()}" if people is not None else ""
    days = dict(con.execute(f"""SELECT substr(i.taken_at, 1, 10) AS day, COUNT(*) FROM image i
                                WHERE i.taken_at IS NOT NULL AND {PRECISE} AND {VISIBLE}
                                  AND (i.dup_group_id IS NULL OR i.dup_group_id = i.id) {mine}
                                GROUP BY day""", {"people": json.dumps(people or [])}))
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
                            "media": "both", "pick": {"mode": "all", "max": 300},
                            **({"who": {"mode": "people", "person_ids": people}} if people else {})})
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


def family_occasions(con, people: list[int], today: date, blurry_below: float | None = None) -> list[dict]:
    """A member's own family's days of photos, as memories: runs of days with photos of them (runs) with LEAST photos
    or more, however long ago. The latest of them and the biggest (those named by what they were count double),
    FAMILY_OCCASIONS in all, the newest first. Each is named as _occasion says, else by its days, with who is in it."""
    found = runs(con, today, people)
    if not found:
        return []
    names = dict(con.execute("SELECT id, name FROM person WHERE id IN (SELECT value FROM json_each(?))",
                             (json.dumps(people),)))
    for r in found:
        r["title"], r["who"], r["why"] = _occasion(con, r, people, names)
    biggest = sorted(range(len(found)), key=lambda k: -len(found[k]["ids"]) * (2 if found[k]["title"] else 1))
    picked = {len(found) - 1, *biggest[:FAMILY_OCCASIONS - 1]}
    return cards(con, [found[k] for k in sorted(picked, reverse=True)], today, blurry_below, people)


def year_events(con, year: int, today: date, blurry_below: float | None = None, most: int = YEAR_EVENTS) -> list[dict]:
    """A year's events, for the Timeline: its biggest runs of days with photos (those named by what they were count
    double), `most` of them, in the order they happened, each a tour of that run's photos."""
    found = runs(con, today, year=year)
    names = dict(con.execute("SELECT id, name FROM person"))
    candidates = sorted(found, key=lambda r: -len(r["ids"]))[:most * 2]  # naming takes a few queries: the big ones
    for r in candidates:
        r["title"], r["who"], r["why"] = _occasion(con, r, None, names)
    picked = sorted(candidates, key=lambda r: -len(r["ids"]) * (2 if r["title"] else 1))[:most]
    return cards(con, sorted(picked, key=lambda r: r["first"]), today, blurry_below)


def runs(con, today: date, people: list[int] | None = None, year: int | None = None) -> list[dict]:
    """Runs of days with photos (dated to the day, none after today), two weeks at most, with LEAST photos or more,
    in date order: [{'first', 'last', 'ids'}]. people: only photos of them; year: that year's. Where photos are
    taken on most days (DENSE days or more), only the busiest days make them (BUSY times a usual day's photos), next
    to each other; elsewhere any days with photos, a day without allowed."""
    mine = f"AND {of_people()}" if people is not None else ""
    rows = con.execute(f"""SELECT i.id, substr(i.taken_at, 1, 10) AS day FROM image i
                           WHERE i.taken_at IS NOT NULL AND i.taken_at < :tomorrow AND i.taken_at >= :start
                             AND i.taken_at < :end AND {PRECISE} AND {VISIBLE}
                             AND (i.dup_group_id IS NULL OR i.dup_group_id = i.id) {mine}""",
                       {"people": json.dumps(people or []), "tomorrow": (today + timedelta(days=1)).isoformat(),
                        "start": f"{year:04d}" if year else "", "end": f"{year + 1:04d}" if year else "~"}).fetchall()
    on_day = defaultdict(list)
    for r in rows:
        try:
            on_day[date.fromisoformat(r["day"])].append(r["id"])
        except ValueError:
            continue
    return grouped(on_day)


def grouped(on_day: dict[date, list[int]]) -> list[dict]:
    """runs, from the photos of each day."""
    if not on_day:
        return []
    dense = len(on_day) >= DENSE
    busy = max(LEAST, BUSY * statistics.median(len(ids) for ids in on_day.values())) if dense else 1
    gap = 1 if dense else 2  # days apart that are still one event
    out = []
    for day in sorted(d for d, ids in on_day.items() if len(ids) >= busy):
        if out and (day - out[-1]["last"]).days <= gap and (day - out[-1]["first"]).days < 14:
            out[-1]["last"] = day
            out[-1]["ids"] += on_day[day]
        else:
            out.append({"first": day, "last": day, "ids": list(on_day[day])})
    return [r for r in out if len(r["ids"]) >= LEAST]


def cards(con, found: list[dict], today: date, blurry_below: float | None = None,
          people: list[int] | None = None) -> list[dict]:
    """Runs of days (runs, named by _occasion) as memories, in the order given: each a tour of its photos (of
    people, if given), titled by what it was, else by its days."""
    out = []
    for r in found:
        spec = tours.clean({"title": "", "when": {"mode": "range", "from": r["first"].isoformat(),
                                                  "to": r["last"].isoformat()},
                            "media": "both", "pick": {"mode": "all", "max": 300},
                            **({"who": {"mode": "people", "person_ids": people}} if people else {})})
        photos = tours.photos(con, spec, blurry_below)
        if len(photos) < LEAST:
            continue
        when, what = span(photos), counted(photos)
        spec["title"] = r["title"] or when
        sub = f"{when} · {what}" if r["title"] else (f"With {r['who']} · {what}" if r["who"] else what)
        out.append({"title": spec["title"], "sub": sub, "when": when, "what": what, "who": r["who"],
                    "named": bool(r["title"]), "why": r.get("why", ""),
                    "cover": cover(photos, today), "count": len(photos), "href": try_href({"spec": spec})})
    return out


def counted(found: list[dict]) -> str:
    """'21 photos', '20 videos and 1 photo'."""
    videos = sum(p["media"] == "video" for p in found)
    parts = sorted([(len(found) - videos, "photo"), (videos, "video")], key=lambda x: -x[0])
    return " and ".join(f"{n} {word}{'' if n == 1 else 's'}" for n, word in parts if n)


def _occasion(con, run: dict, people: list[int] | None, names: dict) -> tuple[str | None, str, str]:
    """(what a run of days was, or None when nothing says; who of the family is in it, the most first; why it is
    called that, for its card). people: whose life events name it, and who counts as in it; None: anyone's.

    A name has to be borne out by the photos, not only by the date: a life event on those days names them when its
    person (or one of the couple) is in a good part of them (SHOWN), or they are tagged as that kind of day
    (EVIDENCE); a death only with a funeral's tags, as the one who died isn't in them. Then the event the folder is
    named for, on half of them; then a tag on a good part of them (OCCASION_TAGS), "whose" only of someone in them."""
    n = len(run["ids"])
    ids, kin = json.dumps(run["ids"]), json.dumps(people or [])
    seen = Counter(dict(con.execute("""SELECT person_id, COUNT(DISTINCT image_id) FROM (
                                           SELECT person_id, image_id FROM face
                                           WHERE image_id IN (SELECT value FROM json_each(:ids))
                                           UNION SELECT person_id, image_id FROM image_person
                                           WHERE image_id IN (SELECT value FROM json_each(:ids)))
                                       WHERE person_id IS NOT NULL GROUP BY person_id""", {"ids": ids})))
    tags = Counter(dict(con.execute("""SELECT lower(t.name), COUNT(DISTINCT it.image_id) FROM image_tag it
                                       JOIN tag t ON t.id = it.tag_id
                                       WHERE it.image_id IN (SELECT value FROM json_each(?)) AND it.source != 'not'
                                       GROUP BY lower(t.name)""", (ids,))))
    ours = [q for q, _ in seen.most_common() if q in names and (people is None or q in people)]
    who = _and([names[q].split()[0] for q in ours[:3]] + (["others"] if len(ours) > 3 else []))
    enough = max(2, SHOWN * n)

    def tagged(words) -> tuple[int, str] | None:
        """(photos, tag) of the most used of these tags, on a good part of the photos."""
        return max(((tags[w], w) for w in words if tags.get(w, 0) >= max(2, OCCASION_TAGS * n)), default=None)

    their_events = ("""AND (person_id IN (SELECT value FROM json_each(:kin))
                            OR couple_a IN (SELECT value FROM json_each(:kin))
                            OR couple_b IN (SELECT value FROM json_each(:kin)))""" if people is not None else "")
    first, last, year = run["first"] - timedelta(days=1), run["last"] + timedelta(days=1), run["first"].year
    for e in con.execute(f"""SELECT tag, type, person_id, couple_a, couple_b FROM event
                             WHERE length(sort_date) = 10 AND sort_date BETWEEN :a AND :b {their_events}
                             ORDER BY tag = 'MARR' DESC, id""",
                         {"a": first.isoformat(), "b": last.isoformat(), "kin": kin}):
        pids = [q for q in (e["person_id"], e["couple_a"], e["couple_b"]) if q]
        a, b = (names.get(q) or _name(con, q) for q in (pids + [None])[:2])
        if e["tag"] == "EVEN" and e["type"]:
            words = e["type"][:1].upper() + e["type"][1:]
        else:
            words = EVENT_WORDS.get(e["tag"])
        if not words or not a or ("{b}" in words and not b):
            continue
        title = words.format(a=a, b=b, whose=_whose(a))
        said = f"the family tree has {title} on these days"
        if e["tag"] in ("DEAT", "BURI"):  # who died isn't in the photos: they are of the funeral, or not of it
            if found := tagged(EVIDENCE["DEAT"]):
                return title, who, f"{a} died on these days, and {found[0]} of the {n} photos are tagged {found[1]}"
            continue
        if there := [q for q in pids if seen.get(q, 0) >= enough]:
            return title, who, f"{said}, and {names.get(there[0]) or a} is in {seen[there[0]]} of the {n} photos"
        if found := tagged(EVIDENCE.get(e["tag"], ())):
            return title, who, f"{said}, and {found[0]} of the {n} photos are tagged {found[1]}"
    folders = Counter(r[0] for r in con.execute("""SELECT fo.event FROM file f JOIN folder fo ON fo.id = f.folder_id
                                                   WHERE f.image_id IN (SELECT value FROM json_each(?))
                                                     AND coalesce(fo.event, '') != ''
                                                   GROUP BY f.image_id""", (ids,)))
    if folders and folders.most_common(1)[0][1] * 2 >= n:
        event, k = folders.most_common(1)[0]
        return event[:1].upper() + event[1:], who, f"{k} of the {n} photos are in a folder for {event}"
    for tag, k in tags.most_common():
        if tag not in OCCASIONS or k < max(2, OCCASION_TAGS * n):
            continue
        theirs, anyones = OCCASIONS[tag]
        why = f"{k} of the {n} photos are tagged {tag}"
        if tag == "birthday":  # whose: someone born on one of these days, and in the photos
            born = _born_on(con, run, [q for q in seen if seen[q] >= enough and q in names])
            return (f"{_whose(names[born])} birthday" if born else anyones.format(year=year)), who, why
        if theirs and ours and seen[ours[0]] >= enough:
            top = names[ours[0]]
            return theirs.format(whose=_whose(top), year=year), who, f"{why}, and {top} is in {seen[ours[0]]} of them"
        return anyones.format(year=year), who, why
    return None, who, ""


def _born_on(con, run: dict, candidates: list[int]) -> int | None:
    """Which of these people was born on one of the run's days of the year (a day either side), if one was."""
    days = {(run["first"] + timedelta(days=k)).strftime("%m-%d")
            for k in range(-1, (run["last"] - run["first"]).days + 2)}
    for pid, born in con.execute("SELECT id, birth_date FROM person WHERE id IN (SELECT value FROM json_each(?))",
                                 (json.dumps(candidates),)):
        if born and len(born) == 10 and born[5:] in days:
            return pid
    return None


def _name(con, pid) -> str | None:
    row = con.execute("SELECT name FROM person WHERE id = ?", (pid,)).fetchone() if pid else None
    return row[0] if row else None


def _whose(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def _and(words: list[str]) -> str:
    return ", ".join(words[:-1]) + (" and " if len(words) > 1 else "") + (words[-1] if words else "")


def suggested(suggestions: list[dict], today: date) -> list[dict]:
    """The Tours page's suggestions (tour_suggestions.suggest), as memories: each opens before it is made."""
    return [{"title": s["name"], "sub": s["why"], "count": s["count"], "href": try_href(s["body"]),
             "cover": s["covers"][today.toordinal() % len(s["covers"])]} for s in suggestions if s["covers"]]


def row(years: list[dict], suggestions: list[dict], own: list[dict], family: list[dict] = ()) -> list[dict]:
    """The memories in the order Home shows them: a member's own family's days first (family_occasions), then this
    week in other years, the two timeliest suggestions (a birthday or an occasion coming up comes first), the
    viewer's own tours, then the other suggestions."""
    return ([*family] + years + suggestions[:2] + own + suggestions[2:])[:MOST]
