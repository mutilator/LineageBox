"""Tours to suggest on the Tours page, worked out from what this catalog holds.

Nothing here knows whose photos these are: every suggestion comes from counts over the catalog, so each library gets
its own. Most are the kinds of tour (Settings → Photo tours) with their questions answered by what the photos show
most: the person in the most photos, the two most often together, the busiest year and season, a birthday or wedding
anniversary coming up. Occasions are tours of their own, since no kind of tour asks for one: tags that come back on
about the same days year after year (a holiday, a yearly fair or outing: whatever the catalog's photos show).
"""
import calendar
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import combinations

from lineagebox import dates, family, tours
from lineagebox.catalog import VISIBLE

LEAST = 12  # photos a suggested tour needs: fewer is hardly a slideshow
SOON = 45   # days ahead that a birthday, anniversary or occasion counts as coming up
# An occasion is a tag on at least OCCASION_PHOTOS dated photos from OCCASION_YEARS years or more, given on
# OCCASION_DAYS days a year or fewer on average, whose photos keep to one time of year: OCCASION_KEEP of 1 would be
# the same day every year, 0 any day of it. On the library this was measured on, holidays and yearly fairs and
# outings scored 0.94 to 1 (Christmas 0.95, Halloween 0.97, Easter 0.99), "snow" 0.77, and "summer", given on 25
# days a year, 0.90. Tags that name a month or a season keep to one time of year too, but say nothing about what
# happened, so they are left out unless they are about one day.
OCCASION_PHOTOS, OCCASION_YEARS, OCCASION_DAYS, OCCASION_KEEP = 30, 4, 7, 0.93
TIME_WORDS = {m.lower() for m in calendar.month_name[1:]} | set(dates.SEASONS) | {"autumn"}


@dataclass
class _Facts:
    """What the suggestions are worked out from, read once."""
    today: date
    least: int                       # photos a suggestion needs
    day: dict[int, str]              # image id -> the day it was taken (photos dated by their file's time left out)
    photos_of: dict[int, set[int]]   # person id -> their dated image ids
    south: bool = False              # the seasons are the southern hemisphere's

    def days(self, people: set[int] | None) -> Counter:
        """Photos a day: of any of these people, or anyone's."""
        ids = self.day.keys() if people is None else set().union(*(self.photos_of.get(p, set()) for p in people))
        return Counter(self.day[i] for i in ids)


def suggest(con, blurry_below: float | None = None, today: date | None = None, most: int = 9,
            least: int | None = None) -> list[dict]:
    """Up to `most` tours worth making, the timeliest and most popular first, each with at least `least` photos
    (LEAST) and none the same as a tour already made: name, why it is suggested, its photo count, a few photos to
    show, and the body that makes it (POST /api/tours)."""
    least = least or LEAST
    day = dict(con.execute(f"""SELECT i.id, substr(i.taken_at, 1, 10) FROM image i
                               WHERE i.taken_at IS NOT NULL AND i.taken_at_source != 'mtime' AND {VISIBLE}"""))
    photos_of = defaultdict(set)
    for pid, image_id in con.execute("""SELECT person_id, image_id FROM face
                                        WHERE person_id IS NOT NULL AND assigned_by IN ('user', 'auto')
                                        UNION SELECT person_id, image_id FROM image_person"""):
        if image_id in day:
            photos_of[pid].add(image_id)
    facts = _Facts(today or date.today(), least, day, dict(photos_of), dates.south(con))

    candidates = []
    kinds = tours.types(con)
    for order, t in enumerate(kinds):
        asked = tours.questions(t["spec"])
        if not asked:  # nothing to answer: it is one click away under "Start a new tour" already
            continue
        for answers, why, rank, people, year in _answers(con, facts, t["spec"], asked):
            try:
                spec = tours.answer(t["spec"], answers)
            except tours.Problem:
                continue
            candidates.append({"rank": rank, "order": order, "kind": t["id"], "people": people, "year": year,
                               "why": why, "spec": spec, "body": {"type": t["id"], **answers}})
    for i, (name, years, usual) in enumerate(_occasions(con, facts)[:8]):
        coming = _coming(_as_month_day(usual), facts.today)
        spec = tours.clean({"title": f"{name[:1].upper()}{name[1:]} through the years", "words": f'"{name}"',
                            "pick": {"mode": "per_year", "count": 5}})
        candidates.append({"rank": coming / SOON if coming <= SOON else 1.2 + i, "order": len(kinds),
                           "kind": "occasion", "people": set(), "year": None, "spec": spec, "body": {"spec": spec},
                           "why": ("coming up: " if coming <= SOON else "") +
                                  f"around {_month_day(_as_month_day(usual))}, in photos from {years} years"})
    candidates.sort(key=lambda c: (c["rank"], c["order"]))

    made = {_key(t["spec"]) for t in tours.tours(con)}
    chosen, seen_people, seen_years, tries = [], set(), set(), 0
    for strict in (True, False):  # first one of each kind, with someone or a year not suggested yet; then the rest
        for c in candidates:
            if len(chosen) >= most or tries >= most + 6:  # each try picks a tour's photos, which takes a while
                break
            key = _key(c["spec"])
            if key in made:
                continue
            if strict and (sum(s["kind"] == c["kind"] for s in chosen) >= (2 if c["kind"] == "occasion" else 1)
                           or c["people"] and c["people"] <= seen_people or c["year"] in seen_years):
                continue
            made.add(key)
            tries += 1
            found = tours.photos(con, c["spec"], blurry_below)
            if len(found) < least:
                continue
            seen_people |= c["people"]
            if c["year"]:
                seen_years.add(c["year"])
            chosen.append({**c, "name": tours.title(con, c["spec"]), "why": c["why"][:1].upper() + c["why"][1:],
                           "count": len(found), "covers": _covers(found)})
    return [{k: c[k] for k in ("name", "why", "count", "covers", "body", "spec")} for c in chosen]


def unmade(con, suggested: list[dict]) -> list[dict]:
    """The suggestions not made into tours yet: they are kept a while, and tours may be made from them meanwhile."""
    made = {_key(t["spec"]) for t in tours.tours(con)}
    return [s for s in suggested if _key(s["spec"]) not in made]


def _key(spec: dict) -> str:
    return json.dumps(spec, sort_keys=True)


def _covers(found: list[dict], n: int = 4) -> list[str]:
    """The best photo of each quarter of the tour, so the covers show its whole span."""
    parts = [found[i * len(found) // n:(i + 1) * len(found) // n] for i in range(n)]
    return [max(part, key=lambda p: p["score"])["sha"] for part in parts if part]


# ---- answering a kind of tour's questions --------------------------------------------------------------------------

def _answers(con, facts: _Facts, spec: dict, asked: list[str]):
    """(answers, why, rank, people, year) for a kind of tour, the likeliest first. A rank under 1 is something coming
    up soon; from 1 on, the most popular answer, and each next one a rank further on."""
    for who, who_why, who_rank, people in _who(con, facts, spec, asked)[:3]:
        chosen = json.loads(json.dumps(spec))  # whose photos, before the dates are chosen from them
        if "relation" in who:
            chosen["who"]["relation"] = who["relation"]
        elif who:
            chosen["who"]["person_ids"] = who.get("people") or [who["person"]]
        for when, when_why, when_rank, year in _when(facts, spec, asked, tours.target_people(con, chosen))[:3]:
            ranks = [r for r in (who_rank, when_rank) if r is not None]
            yield ({**who, **when}, ", ".join(w for w in (who_why, when_why) if w), sum(ranks) - len(ranks) + 1,
                   people, year)


def _who(con, facts: _Facts, spec: dict, asked: list[str]) -> list[tuple[dict, str, float | None, set[int]]]:
    """(answers, why, rank, people) for whose photos; one empty answer when it isn't asked."""
    if "people" in asked:
        return [({"people": [a, b]}, "the two people most often in photos together" if i == 0 else
                 "often in photos together", 1.1 + i, {a, b}) for i, (a, b) in enumerate(_pairs(facts))]
    if "relation" in asked:
        return _relations(con, facts, spec)
    if "person" not in asked:
        return [({}, "", None, set())]
    mode = spec["when"]["mode"]
    lots = {p for p, ids in facts.photos_of.items() if len(ids) >= facts.least}
    if mode in ("birthday", "anniversary"):
        what = "birthday" if mode == "birthday" else "wedding anniversary"
        out = []
        for i, (coming, pid, month_day) in enumerate(sorted(_yearly_days(con, facts, mode, lots))):
            when = ("today" if coming == 0 else "tomorrow" if coming == 1 else f"in {coming} days" if coming <= SOON
                    else f"on {_month_day(month_day)}")
            out.append(({"person": pid}, f"{what} {when}", coming / SOON if coming <= SOON else 3 + i, {pid}))
        return out
    if mode == "events":
        return _with_events(con, facts, lots)
    if spec["who"]["mode"] == "line":
        return _lines(con, facts, lots)
    ranked = sorted(lots, key=lambda p: (-len(facts.photos_of[p]), p))
    return [({"person": p}, "the person in the most photos" if i == 0 else "one of the people in the most photos",
             1.0 + i, {p}) for i, p in enumerate(ranked)]


def _pairs(facts: _Facts) -> list[tuple[int, int]]:
    """The two people most often in the same photos, the most first."""
    in_photo = defaultdict(list)
    for pid, ids in facts.photos_of.items():
        for image_id in ids:
            in_photo[image_id].append(pid)
    together = Counter()
    for pids in in_photo.values():
        if len(pids) <= 8:  # a crowd says little about who goes together
            together.update(combinations(sorted(pids), 2))
    return [pair for pair, n in together.most_common(3) if n >= facts.least]


def _relations(con, facts: _Facts, spec: dict) -> list[tuple[dict, str, float, set[int]]]:
    """Relationships (sons, cousins, friends...) by how many photos their people are in."""
    ranked = []
    for key, _, _ in tours.relation_choices(con):
        chosen = json.loads(json.dumps(spec))
        chosen["who"]["relation"] = key
        people = tours.target_people(con, chosen) or set()
        n = len(set().union(*(facts.photos_of.get(p, set()) for p in people)))
        if n >= facts.least:
            ranked.append((-n, key, people))
    return [({"relation": key}, "the relatives in the most photos" if i == 0 else "relatives in many photos", 1.5 + i,
             people) for i, (_, key, people) in enumerate(sorted(ranked, key=lambda r: r[:2]))]


def _yearly_days(con, facts: _Facts, mode: str, people: set[int]) -> list[tuple[int, int, tuple[int, int]]]:
    """(days until, person, (month, day)) for these people's birthdays, or wedding days."""
    if mode == "birthday":
        rows = con.execute("SELECT id, birth_date FROM person").fetchall()
    else:  # a couple's wedding goes by whichever of them is in more photos; their tour shows both
        rows = [(max((p for p in r[:3] if p in people), key=lambda p: len(facts.photos_of[p]), default=None), r[3])
                for r in con.execute("""SELECT person_id, couple_a, couple_b, sort_date FROM event
                                        WHERE tag = 'MARR' AND sort_date IS NOT NULL""")]
    out = {}
    for pid, text in rows:
        if pid in people and (month_day := tours._birthday(text)):
            out[pid] = (_coming(month_day, facts.today), pid, month_day)
    return list(out.values())


def _with_events(con, facts: _Facts, people: set[int]) -> list[tuple[dict, str, float, set[int]]]:
    """People by how many of their dated life events (their own, or as a couple) fall in the years with photos."""
    years = {d[:4] for d in facts.day.values()}
    count = Counter()
    for row in con.execute("SELECT person_id, couple_a, couple_b, sort_date FROM event WHERE sort_date IS NOT NULL"):
        if row[3][:4] in years:
            count.update({p for p in row[:3] if p in people})
    return [({"person": p}, f"{n} life events with dates" if n > 1 else "a life event with a date", 1.7 + i, {p})
            for i, (p, n) in enumerate(sorted(count.items(), key=lambda pn: (-pn[1], pn[0])))]


def _lines(con, facts: _Facts, people: set[int]) -> list[tuple[dict, str, float, set[int]]]:
    """People whose ancestors and they themselves are in photos over the most generations."""
    g = family.load(con)
    ranked = []
    for p in people:
        line = {a: up for a, up in g.ancestors.get(p, {}).items() if a in facts.photos_of}
        generations = len(set(line.values()))
        if generations >= 2 and len(line) >= 3:
            ranked.append((-generations, -len(line), p))
    return [({"person": p}, f"{-generations} generations in photos", 1.6 + i, {p})
            for i, (generations, _, p) in enumerate(sorted(ranked))]


def _when(facts: _Facts, spec: dict, asked: list[str], targets: set[int] | None):
    """(answers, why, rank, year) for when the photos are from, worked out from the chosen people's photos."""
    if not {"year", "season", "range"} & set(asked):
        return [({}, "", None, None)]
    days = facts.days(targets)
    of = "" if targets is None else " of them"
    if "range" in asked:
        return [({"from": a.isoformat(), "to": b.isoformat()}, f"{n:,} photos{of} in {(b - a).days + 1} days",
                 1.8 + i, None) for i, (n, a, b) in enumerate(_stretches(days))]
    if spec["when"]["mode"] == "season":
        seasons = Counter()
        for d, n in days.items():
            year, month = int(d[:4]), int(d[5:7])
            seasons[dates.season_of(month, year, facts.south)] += n  # winter 2023 is December 2023 into February 2024
        ranked = [(s, y) for (s, y), _ in seasons.most_common()
                  if ("season" in asked or s == spec["when"]["season"])
                  and ("year" in asked or y == spec["when"]["year"])]
        now = dates.season_of(facts.today.month, facts.today.year, facts.south)[0]
        first = next((o for o in ranked if o[0] == now), None) if "season" in asked else None
        if first:  # this time of year comes first
            ranked = [first] + [o for o in ranked if o != first]
        out = []
        for i, (s, y) in enumerate(ranked):
            why = (f"this time of year: the {s} with the most photos{of}" if (s, y) == first
                   else f"the {'season' if 'season' in asked else s} with the most photos{of}" if i == 0
                   else f"a busy {s}")
            out.append(({k: v for k, v in (("season", s), ("year", y)) if k in asked}, why, 1.3 + i, y))
        return out
    years = Counter()
    for d, n in days.items():
        years[int(d[:4])] += n
    ranked = [y for y, _ in years.most_common()]
    out = [({"year": y}, f"the year with the most photos{of}" if i == 0
            else "a busy year" + (" for them" if targets is not None else ""), 1.4 + 2 * i, y)
           for i, y in enumerate(ranked[:2])]
    last = facts.today.year - 1
    if years[last] >= facts.least and ranked[:1] != [last]:
        out.insert(1, ({"year": last}, "last year", 1.9, last))
    return out


def _stretches(days: Counter) -> list[tuple[int, date, date]]:
    """(photos, first day, last day) of runs of days with photos, a day without allowed, at most two weeks long:
    trips, holidays, big days. The most photos first."""
    runs, run = [], []
    for d in sorted(days):
        day = date.fromisoformat(d)
        if run and (day - run[-1]).days > 2:
            runs.append(run)
            run = []
        run.append(day)
    runs.append(run)
    out = [(sum(days[d.isoformat()] for d in r), r[0], r[-1]) for r in runs if len(r) >= 2 and (r[-1] - r[0]).days < 14]
    return sorted(out, key=lambda o: (-o[0], o[1]))[:3]


# ---- occasions ----------------------------------------------------------------------------------------------------

def _occasions(con, facts: _Facts) -> list[tuple[str, int, float]]:
    """(tag, years, usual day of the year counted from 0) for tags that come back on about the same days every year,
    those in the most years first. One for every three weeks of the year: "pumpkin patch" and "carved pumpkins" are
    the same outing, and the shortest tag ("christmas", not "christmas tree") is usually the occasion itself."""
    on = defaultdict(Counter)  # tag -> photos a day
    # Every photo's tags, read once (CROSS JOIN keeps the photos' tags first): with a "tags given OCCASION_PHOTOS times"
    # filter in the query, SQLite looked up each of a member's photos for each such tag, which took a minute. Too few
    # photos are left out below anyway, counting only dated ones.
    for name, image_id in con.execute("""SELECT t.name, it.image_id FROM image_tag it
                                         CROSS JOIN tag t ON t.id = it.tag_id WHERE it.source != 'not'"""):
        if d := facts.day.get(image_id):
            on[name][d] += 1
    found = []
    for name, days in on.items():
        n = sum(days.values())
        by_year = Counter(d[:4] for d in days)  # days a year with photos
        # "august" or "summer event" names a time, not an occasion, unless it is about one day a year ("july 4th")
        most_days = 1.5 if TIME_WORDS & set(name.split()) else OCCASION_DAYS
        if n < OCCASION_PHOTOS or len(by_year) < OCCASION_YEARS or len(days) > most_days * len(by_year):
            continue
        x = y = 0.0  # the days as points round a circle: the closer together, the longer their average
        for d, k in days.items():
            angle = 2 * math.pi * (date.fromisoformat(d).timetuple().tm_yday - 1) / 365.25
            x, y = x + k * math.cos(angle), y + k * math.sin(angle)
        if math.hypot(x, y) / n >= OCCASION_KEEP:
            found.append((name, len(by_year), n, math.atan2(y, x) % (2 * math.pi) * 365.25 / (2 * math.pi)))
    kept = []
    for name, years, n, usual in sorted(found, key=lambda f: (-f[1], len(f[0].split()), -f[2], f[0])):
        if all(min(abs(usual - k[2]), 365.25 - abs(usual - k[2])) > 21 for k in kept):
            kept.append((name, years, usual))
    return kept


def _coming(month_day: tuple[int, int], today: date) -> int:
    """Days from today to the next time it is this month and day; 0 is today."""
    day = tours._on(month_day, today.year)
    return ((day if day >= today else tours._on(month_day, today.year + 1)) - today).days


def _as_month_day(day_of_year: float) -> tuple[int, int]:
    d = date(2001, 1, 1) + timedelta(days=round(day_of_year) % 365)
    return d.month, d.day


def _month_day(month_day: tuple[int, int]) -> str:
    return f"{date(2000, *month_day):%B} {month_day[1]}"  # 2000 has a 29 February
