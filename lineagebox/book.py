"""A family book: a person, or a person and everyone descended from them, as pages to print or save as a PDF.

Made from what is already there, on the viewer's own connection, so a member's book holds only their part of the
family and the photos they see: each person's portrait (the face chosen for them, else their clearest named face, cut
from its photo), the life story the family wrote, their life events, their family, the documents that are records of
them, and their best photos year by year (tours.photos, so favourites come first) with the family's captions. The
browser prints it: the page has print styles (book.css) with a page for each person, and Print → Save as PDF makes the
file. No PDF library is needed, and nothing is sent anywhere.
"""
import json
import sqlite3
from collections import defaultdict
from datetime import date

from lineagebox import comments, documents, events, family, tours
from lineagebox.catalog import VISIBLE

PER_YEAR = 4          # photos a year in one person's book...
MOST_PHOTOS = 120     # ...and at most this many
BRANCH_PER_YEAR = 2   # in a branch's book, for each person...
BRANCH_PHOTOS = 24    # ...at most this many
BRANCH_MOST = 80      # people in a branch's book: the generations nearest the first person, if there are more
PORTRAIT = 2.6        # a portrait's side, in heights of the face: the face with some hair and shoulders


def portrait(con: sqlite3.Connection, pid: int) -> dict | None:
    """The person's portrait: the face chosen for them (person.cover_face_id, while still theirs), else their clearest
    named face (by hand before recognised), cut square from its photo. Gives the photo's sha256 and the img's size and
    place in a square frame, in percent (CSS). None without a named face in a photo."""
    row = con.execute(f"""SELECT i.sha256, i.width, i.height, f.x1, f.y1, f.x2, f.y2 FROM face f
                          JOIN image i ON i.id = f.image_id
                          WHERE f.person_id = :p AND f.assigned_by IN ('user', 'auto') AND f.frame IS NULL
                            AND i.media = 'photo' AND i.width > 0 AND i.height > 0 AND {VISIBLE}
                          ORDER BY f.id IS (SELECT cover_face_id FROM person WHERE id = :p) DESC,
                                   f.assigned_by = 'user' DESC, f.quality DESC, (f.x2 - f.x1) * (f.y2 - f.y1) DESC
                          LIMIT 1""", {"p": pid}).fetchone()
    if row is None:
        return None
    w, h = row["width"], row["height"]
    cx, cy = (row["x1"] + row["x2"]) / 2 * w, (row["y1"] + row["y2"]) / 2 * h
    side = min(max(PORTRAIT * (row["y2"] - row["y1"]) * h, PORTRAIT * (row["x2"] - row["x1"]) * w), w, h)
    left = min(max(cx - side / 2, 0), w - side)
    top = min(max(cy - side / 2, 0), h - side)
    return {"sha": row["sha256"], "width": round(100 * w / side, 2), "left": round(-100 * left / side, 2),
            "top": round(-100 * top / side, 2)}


def years(con: sqlite3.Connection, pid: int) -> str:
    """Born and died, in words: "1931–2012", "born 1990", or ""."""
    born = died = None
    for e in con.execute("SELECT tag, date, sort_date FROM event WHERE person_id = ? AND tag IN ('BIRT', 'DEAT')",
                         (pid,)):
        y = (e["sort_date"] or "")[:4]
        if e["tag"] == "BIRT":
            born = y or None
        else:
            died = y or "?"
    if born and died:
        return f"{born}–{died}"
    if born:
        return f"born {born}"
    return f"died {died}" if died and died != "?" else ""


def life(con: sqlite3.Connection, pid: int) -> list[dict]:
    """Their life events, in date order, in words: what, when, where, and with whom (a marriage's partner)."""
    out = []
    for e in events.for_person(con, pid):
        names = events.COUPLE_EVENTS if e["person_id"] is None else events.PERSON_EVENTS
        what = e["type"] if e["tag"] == "EVEN" and e["type"] else names.get(e["tag"], e["tag"])
        if e["type"] and e["tag"] in events.ATTRIBUTES:  # its value is what it says: "Worked as Teacher"
            what = f"{what} {e['type']}"
        out.append({"what": what, "when": events.show_date(e["date"]), "where": e["place"] or "",
                    "with": e["partner"] or "", "note": e["note"] or ""})
    return out


def kin(g: family.Graph, con: sqlite3.Connection, pid: int) -> list[tuple[str, list[str]]]:
    """Their family as the book lists it: parents, partners, brothers and sisters, children, each with their years."""
    links = family.links(g, pid)
    named = lambda q: f"{g.name(q)}" + (f" ({y})" if (y := years(con, q)) else "")  # noqa: E731
    out = [("Parents", [named(p["id"]) for p in links["parents"]]),
           ("Partners", [named(q["id"]) + ("" if q["current"] else ", former") for q in links["partners"]]),
           ("Brothers and sisters", [named(s["id"]) for s in links["siblings"]]),
           ("Children", [named(c["id"]) for c in links["children"]])]
    return [(label, names) for label, names in out if names]


def photos(con: sqlite3.Connection, pid: int, per_year: int, most: int) -> list[tuple[str, list[dict]]]:
    """Their best photos, a few a year, oldest first, each with when it was taken and the family's caption:
    [(year, [photo])]. Photos with no date come last, under "Undated"."""
    spec = tours.clean({"title": "", "who": {"mode": "person", "person_ids": [pid]},
                        "pick": {"mode": "per_year", "count": per_year, "max": most}, "media": "photos"})
    chosen = tours.photos(con, spec)
    words = comments.captions(con, [p["id"] for p in chosen])
    dated = {r["id"]: r for r in con.execute("""SELECT id, taken_at, taken_at_source, date_text, date_from, date_to
                                                FROM image WHERE id IN (SELECT value FROM json_each(?))""",
                                             (_ids(chosen),))}
    by_year = defaultdict(list)
    for p in chosen:
        by_year[p["taken"][:4] if p["taken"] else "Undated"].append(
            {"id": p["id"], "sha": p["sha"], "when": tours.when_words(dated[p["id"]]), "caption": words.get(p["id"], "")})
    return sorted(by_year.items(), key=lambda kv: (kv[0] == "Undated", kv[0]))


def _ids(chosen) -> str:
    return json.dumps([p["id"] for p in chosen])


def chapter(con: sqlite3.Connection, g: family.Graph, pid: int, per_year: int, most: int) -> dict:
    """Everything the book says of one person."""
    p = dict(con.execute("SELECT * FROM person WHERE id = ?", (pid,)).fetchone())
    return {"id": pid, "name": p["name"], "years": years(con, pid), "portrait": portrait(con, pid),
            "story": p.get("biography") or "",  # the life story the family wrote, where there is one
            "other_names": [n["name"] for n in events.other_names(con, pid)], "life": life(con, pid),
            "family": kin(g, con, pid), "documents": [dict(d) for d in documents.for_person(con, pid)],
            "photos": photos(con, pid, per_year, most)}


def descendants(g: family.Graph, pid: int, most: int = BRANCH_MOST) -> list[tuple[int, int]]:
    """pid and everyone descended from them, as (person, generations below pid): a generation at a time, each by
    birth date, then name; at most `most` people (the nearest generations)."""
    out, seen, now = [], {pid}, [pid]
    depth = 0
    while now and len(out) < most:
        out += [(p, depth) for p in now][:most - len(out)]
        nxt = []
        for p in now:
            for c in g.effective_children.get(p, ()):
                if c > 0 and c in g.people and c not in seen:
                    seen.add(c)
                    nxt.append(c)
        now = sorted(nxt, key=lambda c: (g.people[c]["birth_date"] or "9999", g.name(c).lower()))
        depth += 1
    return out


def book(con: sqlite3.Connection, pid: int, branch: bool = False) -> dict:
    """A person's book, or with branch, theirs and their descendants': chapters in order, and a contents list."""
    g = family.load(con)
    people = descendants(g, pid) if branch else [(pid, 0)]
    per_year, most = (BRANCH_PER_YEAR, BRANCH_PHOTOS) if branch else (PER_YEAR, MOST_PHOTOS)
    chapters = [{**chapter(con, g, p, per_year, most), "depth": depth} for p, depth in people]
    more = sum(1 for _ in _all_descendants(g, pid)) - len(people) if branch else 0
    return {"title": chapters[0]["name"], "branch": branch, "chapters": chapters, "left_out": max(more, 0),
            "made": date.today()}


def _all_descendants(g: family.Graph, pid: int):
    seen, todo = {pid}, [pid]
    while todo:
        p = todo.pop()
        yield p
        for c in g.effective_children.get(p, ()):
            if c > 0 and c in g.people and c not in seen:
                seen.add(c)
                todo.append(c)
