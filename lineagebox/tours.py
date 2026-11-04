"""Photo tours: slideshows of photos picked by rules.

A tour type (set up on the Settings page) is a set of rules, some of them left open: "photos of {person} around their
birthday", "{season} {year}". The wizard asks for whatever a type leaves open, and a saved tour is the type's rules
with every answer filled in. The photos are picked again each time a tour is opened, so new photos join old tours.
"""
import functools
import json
import random
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta

from lineagebox import dates, errors, events, family
from lineagebox.catalog import VISIBLE

SEASONS = dates.SEASONS
WHO = {"anyone": "Anyone", "person": "One person", "people": "Several people", "relation": "People by relationship",
       "line": "One person and their ancestors"}
WHEN = {"any": "Any time", "year": "One year", "season": "One season", "birthday": "Around the person's birthday",
        "anniversary": "Around their wedding anniversary", "events": "Around their life events",
        "range": "Between two dates"}
AROUND = ("birthday", "anniversary", "events")  # dates of one person's: photos with a camera date near them
PICK = {"all": "Every photo", "per_year": "A few per year", "per_month": "A few per month",
        "spread": "A set number, spread over the time"}
MEDIA = {"photos": "Photos", "videos": "Videos", "both": "Photos and videos"}
GROUP_NAMES = {"family": "Family", "inlaw": "In-laws", "step": "Step-family", "former": "Former family",
               "relative": "Relatives", "extended": "Extended family", "sibling": "Siblings", "child": "Children",
               "parent": "Parents", "grandchild": "Grandchildren", "grandparent": "Grandparents", "cousin": "Cousins"}

DEFAULT_TYPES = [
    {"name": "Birthdays", "title": "{person}'s birthdays", "who": {"mode": "person"},
     "when": {"mode": "birthday", "days": 21}, "words": "birthday cake candles", "pick": {"mode": "all", "max": 300}},
    {"name": "A season", "title": "{season} {year}", "when": {"mode": "season"}, "pick": {"mode": "spread", "max": 40}},
    {"name": "Throughout the years", "title": "{person} through the years", "who": {"mode": "person"},
     "pick": {"mode": "per_year", "count": 2}},
    {"name": "Relatives in a year", "title": "{relation} {year}",
     "who": {"mode": "relation", "with_children": True}, "when": {"mode": "year"},
     "pick": {"mode": "spread", "max": 60}},
    {"name": "Together", "title": "{people} together", "who": {"mode": "people", "together": True},
     "pick": {"mode": "spread", "max": 100}},
    {"name": "Year in review", "title": "{year} in review", "when": {"mode": "year"},
     "pick": {"mode": "per_month", "count": 4}},
]
# added later, once, for catalogs made before them (see types)
EVENT_TYPES = [
    {"name": "Anniversaries", "title": "{person}'s anniversaries", "who": {"mode": "person", "with_partners": True},
     "when": {"mode": "anniversary", "days": 14}, "pick": {"mode": "all", "max": 300}},
    {"name": "Life events", "title": "{person}'s life events", "who": {"mode": "person"},
     "when": {"mode": "events", "days": 7}, "pick": {"mode": "spread", "max": 120}},
    {"name": "Family line", "title": "{person}'s family line", "who": {"mode": "line"},
     "pick": {"mode": "per_year", "count": 2}},
]


class Problem(errors.Problem):
    """Rules or answers that cannot be used; the message is shown to the person who gave them."""


def _sent(fn):
    """For what a page sent: something of the wrong kind (text where a list goes, a word where an id goes) is a
    Problem, said as such, not an error."""
    @functools.wraps(fn)
    def read(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Problem:
            raise
        except (TypeError, ValueError, AttributeError, KeyError) as e:
            raise Problem(f"Part of what was sent can't be read ({type(e).__name__}: {e}).") from None
    return read


@_sent
def clean(spec: dict) -> dict:
    """A spec with every field present and checked. Raises Problem."""
    who, when, pick = spec.get("who") or {}, spec.get("when") or {}, spec.get("pick") or {}
    out = {
        "title": " ".join(str(spec.get("title") or "").split())[:120],
        "who": {"mode": who.get("mode") or "anyone",
                "person_ids": [int(p) for p in who.get("person_ids") or []],
                "relation": str(who.get("relation") or "").strip().lower(),
                "together": bool(who.get("together")), "with_partners": bool(who.get("with_partners")),
                "with_children": bool(who.get("with_children"))},
        "when": {"mode": when.get("mode") or "any", "year": _int(when.get("year"), "The year", 1800, 2200),
                 "season": str(when.get("season") or "").lower(), "from": str(when.get("from") or ""),
                 "to": str(when.get("to") or ""), "days": _int(when.get("days"), "Days around the birthday", 0, 60)},
        "words": " ".join(str(spec.get("words") or "").split())[:200],
        "pick": {"mode": pick.get("mode") or "all", "count": _int(pick.get("count"), "Photos per year or month", 1,
                                                                    100) or 2,
                 "max": _int(pick.get("max"), "The most photos", 1, 2000) or 300},
        "media": spec.get("media") or "photos",
        "order": "shuffle" if spec.get("order") == "shuffle" else "date",
        "seconds": _int(spec.get("seconds"), "Seconds per photo", 1, 60) or 5,
    }
    if out["when"]["days"] is None:
        out["when"]["days"] = 21
    for value, allowed, what in ((out["who"]["mode"], WHO, "who"), (out["when"]["mode"], WHEN, "when"),
                                 (out["pick"]["mode"], PICK, "how many"), (out["media"], MEDIA, "photos or videos")):
        if value not in allowed:
            raise Problem(f"Unknown choice for {what}.")
    if out["when"]["season"] and out["when"]["season"] not in SEASONS:
        raise Problem("Unknown season.")
    for key in ("from", "to"):
        if out["when"][key]:
            try:
                date.fromisoformat(out["when"][key])
            except ValueError:
                raise Problem("Dates are written like 2023-12-25.") from None
    if out["when"]["mode"] in AROUND and out["who"]["mode"] != "person":
        raise Problem(f"{WHEN[out['when']['mode']]} needs one person to be chosen.")
    return out


def _int(value, what: str, low: int, high: int) -> int | None:
    if value in (None, ""):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise Problem(f"{what} must be a whole number.") from None
    if not low <= n <= high:
        raise Problem(f"{what} must be from {low} to {high}.")
    return n


def questions(spec: dict) -> list[str]:
    """What a type leaves open, for the wizard to ask: person, people, relation, year, season, range."""
    who, when = spec["who"], spec["when"]
    out = []
    if who["mode"] in ("person", "line") and not who["person_ids"]:
        out.append("person")
    if who["mode"] == "people" and len(who["person_ids"]) < 2:
        out.append("people")
    if who["mode"] == "relation" and not who["relation"]:
        out.append("relation")
    if when["mode"] == "season" and not when["season"]:
        out.append("season")
    if when["mode"] in ("year", "season") and not when["year"]:
        out.append("year")
    if when["mode"] == "range" and not (when["from"] and when["to"]):
        out.append("range")
    return out


@_sent
def answer(type_spec: dict, answers: dict) -> dict:
    """The type's spec with the wizard's answers filled in. Raises Problem if something is still open."""
    spec = json.loads(json.dumps(type_spec))
    asked = questions(spec)
    if "person" in asked and answers.get("person"):
        spec["who"]["person_ids"] = [int(answers["person"])]
    if "people" in asked:
        spec["who"]["person_ids"] = [int(p) for p in answers.get("people") or []]
    if "relation" in asked:
        spec["who"]["relation"] = str(answers.get("relation") or "")
    if "season" in asked:
        spec["when"]["season"] = str(answers.get("season") or "")
    if "year" in asked:
        spec["when"]["year"] = answers.get("year") or None
    if "range" in asked:
        spec["when"]["from"], spec["when"]["to"] = answers.get("from") or "", answers.get("to") or ""
    spec = clean(spec)
    missing = questions(spec)
    if missing:
        raise Problem({"person": "Pick a person.", "people": "Pick at least two people.",
                       "relation": "Pick a relationship.", "season": "Pick a season.", "year": "Pick a year.",
                       "range": "Give both dates."}[missing[0]])
    return spec


def title(con: sqlite3.Connection, spec: dict) -> str:
    """The type's name pattern with the answers: '{season} {year}' -> 'Winter 2023'."""
    names = {r[0]: r[1] for r in con.execute("SELECT id, name FROM person")}
    ids = spec["who"]["person_ids"]
    people = [names.get(p, "?") for p in ids]
    values = {
        "person": people[0] if people else "",
        "people": people[0] if len(people) == 1 else ", ".join(people[:-1]) + " and " + people[-1] if people else "",
        "relation": relation_name(con, spec["who"]["relation"]) if spec["who"]["relation"] else "",
        "year": str(spec["when"]["year"] or ""),
        "season": spec["when"]["season"].capitalize(),
    }
    text = re.sub(r"\{(\w+)\}", lambda m: values.get(m[1], m[0]), spec["title"] or "Photo tour")
    return " ".join(text.split()) or "Photo tour"


def season_dates(season: str, year: int, south: bool = False) -> tuple[date, date]:
    """From the first day to the day after the last (dates.season_span): winter 2023 is December 2023 to February
    2024."""
    first, last = dates.season_span(season, year, south)
    return first, last + timedelta(days=1)


def date_range(spec: dict, south: bool = False) -> tuple[date, date] | None:
    when = spec["when"]
    if when["mode"] == "year" and when["year"]:
        return date(when["year"], 1, 1), date(when["year"] + 1, 1, 1)
    if when["mode"] == "season" and when["year"] and when["season"]:
        return season_dates(when["season"], when["year"], south)
    if when["mode"] == "range" and when["from"] and when["to"]:
        a, b = sorted((date.fromisoformat(when["from"]), date.fromisoformat(when["to"])))
        return a, b + timedelta(days=1)
    return None


# ---- relationships to choose from ----------------------------------------------------------------------------------

def relation_choices(con: sqlite3.Connection) -> list[tuple[str, str, int]]:
    """(key, name, how many people) for every relationship someone has: 'brother' -> 'Brothers'."""
    g = family.load(con)
    index = family.word_index(con, g)
    labels = {}
    ego = family.me(con)
    for kin in (family.relations_to(g, ego).values() if ego else ()):
        if "'s" not in kin.label and kin.group != "you":
            labels.setdefault(family.compact(kin.label), kin.label)
    for _, _, kind in g.relations:
        labels.setdefault(family.compact(kind), kind)
        labels.setdefault(family.compact(kind.split()[-1]), kind.split()[-1])
    out = []
    for key, ids in index.items():
        name = GROUP_NAMES.get(key) or (family.plural(labels[key]).capitalize() if key in labels else None)
        if name and key not in ("ex",):
            out.append((key, name, len(ids)))
    return sorted(out, key=lambda t: t[1])


def relation_name(con: sqlite3.Connection, key: str) -> str:
    return next((name for k, name, _ in relation_choices(con) if k == key), key.capitalize())


def target_people(con: sqlite3.Connection, spec: dict) -> set[int] | None:
    """Whose photos the tour is of, or None for anyone's."""
    who = spec["who"]
    if who["mode"] == "line":  # the person and everyone above them
        g = family.load(con)
        return {a for p in who["person_ids"] for a in g.ancestors.get(p, {p: 0}) if a > 0}
    if who["mode"] in ("person", "people"):
        people = set(who["person_ids"])
        if who["with_partners"]:  # a couple's anniversaries: either of them
            g = family.load(con)
            people |= {q for p in who["person_ids"] for q, (_, married) in g.partners.get(p, {}).items() if married}
        return people
    if who["mode"] != "relation":
        return None
    g = family.load(con)
    people = set(family.word_index(con, g).get(who["relation"], ()))
    if who["with_partners"]:
        people |= {q for p in people for q, (current, _) in g.partners.get(p, {}).items() if current}
    if who["with_children"]:
        people |= {c for p in people for c in g.effective_children.get(p, ()) if c > 0}
    return people


# ---- picking the photos ------------------------------------------------------------------------------------------

def photos(con: sqlite3.Connection, spec: dict, blurry_below: float | None = None, seed: int = 0) -> list[dict]:
    """The tour's photos, in the order they are shown: one per group of near-duplicates, never blank or blurry."""
    targets = target_people(con, spec)
    if targets is not None and not targets:
        return []
    where, args = [VISIBLE, "i.taken_at IS NOT NULL"], []
    if spec["media"] != "both":
        where.append("i.media = ?")
        args.append("photo" if spec["media"] == "photos" else "video")
    if (span := date_range(spec, dates.south(con))) or spec["when"]["mode"] in AROUND:
        where.append("i.taken_at_source != 'mtime'")  # file times say when a copy was made
    if spec["when"]["mode"] in AROUND:  # around a day: not a photo dated by hand only to a season or a year
        where.append("(i.taken_at_source != 'user' OR i.date_from = i.date_to)")
    if span:
        where.append("i.taken_at >= ? AND i.taken_at < ?")
        args += [span[0].isoformat(), span[1].isoformat()]
    if blurry_below:
        where.append("(i.sharpness IS NULL OR i.sharpness < 0 OR i.sharpness >= ?)")
        args.append(blurry_below)
    if targets is not None:
        # their photos, found once: as EXISTS per image, SQLite went through all of a person's faces for every image
        marks = ','.join('?' * len(targets))
        where.append(f"""i.id IN (SELECT image_id FROM face WHERE assigned_by IN ('user', 'auto') AND person_id IN ({marks})
                                  UNION SELECT image_id FROM image_person WHERE person_id IN ({marks}))""")
        args += sorted(targets) * 2
    if terms := word_list(spec["words"]):
        where.append("i.id IN (SELECT rowid FROM search_fts WHERE search_fts MATCH ?)")
        args.append(" OR ".join(f'"{w}"' for w in terms))
    rows = con.execute(f"""
        SELECT i.id, i.sha256, i.taken_at, i.media, i.duration, i.playable, i.sharpness, i.dup_group_id,
               f.person_id, f.quality
        FROM image i LEFT JOIN face f ON f.image_id = i.id AND f.person_id IS NOT NULL
                                        AND f.assigned_by IN ('user', 'auto')
        WHERE {' AND '.join(where)}""", args).fetchall()
    found: dict[int, dict] = {}
    for r in rows:
        p = found.setdefault(r["id"], {"id": r["id"], "sha": r["sha256"], "taken": r["taken_at"], "media": r["media"],
                                       "duration": r["duration"], "playable": r["playable"],
                                       "sharpness": r["sharpness"] or 0, "group": r["dup_group_id"] or r["id"],
                                       "faces": {}})
        if r["person_id"]:
            p["faces"][r["person_id"]] = max(p["faces"].get(r["person_id"], 0), r["quality"] or 0)
    if spec["who"]["together"] and targets:
        found = {k: p for k, p in found.items() if targets <= p["faces"].keys()}
    if spec["when"]["mode"] == "birthday":
        found = _around_birthday(con, found, spec)
    elif spec["when"]["mode"] in ("anniversary", "events"):
        found = _around_events(con, found, spec)
    for p in found.values():
        faces = p["faces"] if targets is None else {k: q for k, q in p["faces"].items() if k in targets}
        p["score"] = sum(sorted(faces.values())[-3:]) + min(max(p["sharpness"], 0), 300) / 30
    best: dict[int, dict] = {}
    for p in found.values():  # near-duplicates: the best of each group
        if p["group"] not in best or p["score"] > best[p["group"]]["score"]:
            best[p["group"]] = p
    chosen = _pick(sorted(best.values(), key=lambda p: (p["taken"], p["id"])), spec["pick"])
    if spec["order"] == "shuffle":
        random.Random(seed).shuffle(chosen)
    return chosen


def word_list(words: str) -> list[str]:
    """What a tour's photos may be about: each word, or "a phrase in quotes", is one thing."""
    found = (phrase or word for phrase, word in re.findall(r'"([^"]*)"|(\S+)', words or ""))
    return [w for w in (" ".join(w.replace('"', "").split()) for w in found) if w]


def _around_birthday(con, found: dict[int, dict], spec: dict) -> dict[int, dict]:
    row = con.execute("SELECT birth_date FROM person WHERE id = ?", (spec["who"]["person_ids"][0],)).fetchone()
    born = _birthday(row[0] if row else None)
    if born is None:
        return found  # no birth date: the birthday words alone decide
    days = spec["when"]["days"]
    out = {}
    for k, p in found.items():
        taken = datetime.fromisoformat(p["taken"]).date()
        nearest = min(abs((taken - _on(born, taken.year + dy)).days) for dy in (-1, 0, 1))
        if nearest <= days:
            out[k] = p
    return out


def _around_events(con, found: dict[int, dict], spec: dict) -> dict[int, dict]:
    """Photos near the person's wedding days, every year (anniversary), or near each of their dated life events,
    their couples' included (events: a month-only date counts as the whole month)."""
    pid, days = spec["who"]["person_ids"][0], spec["when"]["days"]
    rows = con.execute("""SELECT tag, sort_date FROM event WHERE sort_date IS NOT NULL
                          AND (person_id = :p OR :p IN (couple_a, couple_b))""", {"p": pid}).fetchall()
    if spec["when"]["mode"] == "anniversary":
        weddings = [_birthday(r["sort_date"]) for r in rows if r["tag"] == "MARR"]
        weddings = [w for w in weddings if w]
        if not weddings:
            return {}
        return {k: p for k, p in found.items()
                if min(abs((datetime.fromisoformat(p["taken"]).date() - _on(w, datetime.fromisoformat(p["taken"]).year
                                                                            + dy)).days)
                       for w in weddings for dy in (-1, 0, 1)) <= days}
    spans = []
    for r in rows:
        d = r["sort_date"]
        if len(d) == 10:
            day = date.fromisoformat(d)
            spans.append((day - timedelta(days=days), day + timedelta(days=days)))
        elif len(d) == 7:
            first = date.fromisoformat(d + "-01")
            last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            spans.append((first - timedelta(days=days), last + timedelta(days=days)))
    return {k: p for k, p in found.items()
            if any(a <= datetime.fromisoformat(p["taken"]).date() <= b for a, b in spans)}


def _birthday(text: str | None) -> tuple[int, int] | None:
    m = re.match(r"^(?:\d{4}-)?(\d{1,2})-(\d{1,2})$", text or "")
    return (int(m[1]), int(m[2])) if m else None


def _on(month_day: tuple[int, int], year: int) -> date:
    month, day = month_day
    try:
        return date(year, month, day)
    except ValueError:  # 29 February in other years
        return date(year, month, 28)


def _pick(found: list[dict], pick: dict) -> list[dict]:
    """found in date order -> the chosen ones, still in date order."""
    mode, count, most = pick["mode"], pick["count"], pick["max"]
    if mode in ("per_year", "per_month"):
        slots = defaultdict(list)
        for p in found:
            slots[p["taken"][:4] if mode == "per_year" else p["taken"][:7]].append(p)
        chosen = [q for ps in slots.values() for q in _best(ps, count)]
    elif mode == "spread" or len(found) > most:
        chosen = found if len(found) <= most else [
            max(found[i * len(found) // most:(i + 1) * len(found) // most], key=lambda p: p["score"])
            for i in range(most)]
    else:
        chosen = found
    chosen = sorted(chosen, key=lambda p: (p["taken"], p["id"]))
    return chosen if len(chosen) <= most else sorted(
        sorted(chosen, key=lambda p: -p["score"])[:most], key=lambda p: (p["taken"], p["id"]))


def _best(found: list[dict], n: int) -> list[dict]:
    """The n best, from different days where there are enough days."""
    ranked = sorted(found, key=lambda p: -p["score"])
    chosen, days = [], set()
    for p in ranked:
        if len(chosen) < n and p["taken"][:10] not in days:
            chosen.append(p)
            days.add(p["taken"][:10])
    for p in ranked:
        if len(chosen) < n and p not in chosen:
            chosen.append(p)
    return chosen


def slides(con: sqlite3.Connection, chosen: list[dict]) -> list[dict]:
    """What the player shows for each photo: the picture, its date, and who is in it (with their age then)."""
    people = dict(con.execute("SELECT id, name FROM person"))
    births = dict(con.execute("SELECT person_id, sort_date FROM event WHERE tag = 'BIRT' AND sort_date IS NOT NULL"))
    dated = {r["id"]: r for r in con.execute("""SELECT id, taken_at, taken_at_source, date_from, date_to FROM image
                                                WHERE id IN (SELECT value FROM json_each(?))""",
                                             (json.dumps([p["id"] for p in chosen]),))}
    out = []
    for p in chosen:
        taken = datetime.fromisoformat(p["taken"])
        order = [pid for pid in sorted(p["faces"], key=lambda k: -p["faces"][k]) if pid in people]
        ages = events.ages(con, dated[p["id"]], order, births) if p["id"] in dated else {}
        names = [f"{people[pid]} ({ages[pid]})" if pid in ages else people[pid] for pid in order]
        video = p["media"] == "video" and p["playable"] in ("original", "copy")
        out.append({"id": p["id"], "src": f"/play/{p['id']}" if video else f"/p/{p['sha']}",
                    "poster": f"/p/{p['sha']}", "video": video, "when": f"{taken:%B} {taken.day}, {taken.year}",
                    "people": ", ".join(names)})
    return out


# ---- stored types and tours ----------------------------------------------------------------------------------------

def types(con: sqlite3.Connection) -> list[dict]:
    """The tour types, with the built-in ones added the first time (deleted ones stay deleted)."""
    for key, added in (("tour_types_added", DEFAULT_TYPES), ("tour_types_events_added", EVENT_TYPES)):
        if not con.execute("SELECT 1 FROM settings WHERE key = ?", (key,)).fetchone():
            for t in added:
                con.execute("INSERT INTO tour_type(name, spec) VALUES (?, ?)", (t["name"], json.dumps(clean(t))))
            con.execute("INSERT INTO settings(key, value) VALUES (?, 'true')", (key,))
            con.commit()
    return [{"id": r["id"], "name": r["name"], "spec": json.loads(r["spec"])}
            for r in con.execute("SELECT * FROM tour_type ORDER BY id")]


def tour_type(con: sqlite3.Connection, type_id: int) -> dict | None:
    return next((t for t in types(con) if t["id"] == type_id), None)


@_sent
def save_type(con: sqlite3.Connection, type_id: int | None, form: dict) -> int:
    """From the Settings page's form. Returns the type's id."""
    name = " ".join(str(form.get("name") or "").split())
    if not name:
        raise Problem("Give the tour type a name.")
    flags = set(form.get("flags") or [])
    people = []  # the form has every field; only the chosen kind of "whose photos" counts
    if form.get("who") == "person" and str(form.get("person") or "").isdecimal():
        people = [int(form["person"])]
    elif form.get("who") == "people":
        people = [int(p) for p in (form.get("people") or []) if str(p).isdecimal()]
    spec = clean({
        "title": form.get("title") or name,
        "who": {"mode": form.get("who"), "person_ids": people,
                "relation": form.get("relation") if form.get("who") == "relation" else "",
                "together": "together" in flags, "with_partners": "with_partners" in flags,
                "with_children": "with_children" in flags},
        "when": {"mode": form.get("when"), "year": form.get("year"), "season": form.get("season"),
                 "from": form.get("from"), "to": form.get("to"), "days": form.get("days")},
        "words": form.get("words"),
        "pick": {"mode": form.get("pick"), "count": form.get("count"), "max": form.get("max")},
        "media": form.get("media"), "order": form.get("order"), "seconds": form.get("seconds"),
    })
    if type_id is None:
        type_id = con.execute("INSERT INTO tour_type(name, spec) VALUES (?, ?)", (name, json.dumps(spec))).lastrowid
    else:
        con.execute("UPDATE tour_type SET name = ?, spec = ? WHERE id = ?", (name, json.dumps(spec), type_id))
    con.commit()
    return type_id


def tours(con: sqlite3.Connection) -> list[dict]:
    return [{**dict(r), "spec": json.loads(r["spec"])}
            for r in con.execute("SELECT * FROM tour ORDER BY created_at DESC, id DESC")]


def tour(con: sqlite3.Connection, tour_id: int) -> dict | None:
    r = con.execute("SELECT * FROM tour WHERE id = ?", (tour_id,)).fetchone()
    return {**dict(r), "spec": json.loads(r["spec"])} if r else None


def save_tour(con: sqlite3.Connection, tour_id: int | None, type_id: int | None, spec: dict, name: str,
              account_id: int | None = None) -> int:
    """Save a tour; a new one is the account's (None: the admins', which members see when shared)."""
    name = " ".join(name.split())[:120] or title(con, spec)
    if tour_id is None:
        tour_id = con.execute("INSERT INTO tour(type_id, name, spec, created_at, account_id) VALUES (?, ?, ?, ?, ?)",
                              (type_id, name, json.dumps(spec), datetime.now().isoformat(timespec="seconds"),
                               account_id)).lastrowid
    else:
        con.execute("UPDATE tour SET name = ?, spec = ? WHERE id = ?", (name, json.dumps(spec), tour_id))
    con.commit()
    return tour_id


def merge(con: sqlite3.Connection, old: int, new: int) -> None:
    """Point tours and tour types that pick old at new instead, before old is deleted (two people merged into one)."""
    for table in ("tour", "tour_type"):
        for r in con.execute(f"SELECT id, spec FROM {table}").fetchall():
            spec = json.loads(r["spec"])
            ids = spec["who"]["person_ids"]
            if old in ids:
                spec["who"]["person_ids"] = list(dict.fromkeys(new if p == old else p for p in ids))
                con.execute(f"UPDATE {table} SET spec = ? WHERE id = ?", (json.dumps(spec), r["id"]))


def describe(con: sqlite3.Connection, spec: dict) -> list[str]:
    """The rules in words, for the tour's page."""
    who, when, pick = spec["who"], spec["when"], spec["pick"]
    names = {r[0]: r[1] for r in con.execute("SELECT id, name FROM person")}
    out = []
    if who["mode"] in ("person", "people") and who["person_ids"]:
        people = ", ".join(names.get(p, "?") for p in who["person_ids"])
        out.append(("Photos with all of " if who["together"] and len(who["person_ids"]) > 1 else "Photos of ") + people)
    elif who["mode"] == "relation" and who["relation"]:
        extra = " and ".join(w for w, on in (("partners", who["with_partners"]), ("children", who["with_children"]))
                             if on)
        out.append(f"{relation_name(con, who['relation'])}" + (f", with their {extra}" if extra else ""))
    if span := date_range(spec, dates.south(con)):
        out.append(f"{span[0]:%b} {span[0].day}, {span[0].year} to {(span[1] - timedelta(days=1)):%b} "
                   f"{(span[1] - timedelta(days=1)).day}, {(span[1] - timedelta(days=1)).year}")
    if when["mode"] == "birthday":
        out.append(f"within {when['days']} days of their birthday (when their birth date is set)")
    if when["mode"] == "anniversary":
        out.append(f"within {when['days']} days of their wedding anniversary (their marriage's date, on Life events)")
    if when["mode"] == "events":
        out.append(f"within {when['days']} days of their dated life events")
    if spec["words"]:
        out.append("about: " + ", ".join(word_list(spec["words"])))
    out.append({"all": f"every photo (at most {pick['max']})", "per_year": f"the best {pick['count']} a year",
                "per_month": f"the best {pick['count']} a month",
                "spread": f"{pick['max']} spread over the time"}[pick["mode"]])
    return out
