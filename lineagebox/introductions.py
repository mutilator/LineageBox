"""Introductions to the family's history (the Historical page's card; PLAN.md §10.16, the user's design): one paper
from the viewer's own family at a time, chosen for them, which they haven't opened. Once opened, from anywhere, it is
never offered to them again (paper_seen), as New for you marks news seen; being listed somewhere doesn't count. Show
me another puts it behind the rest (paper_offer.skipped_at). So a family with a few papers sees each once, and one
with hundreds gets one at a time, with a mark in the menu at most once a day or a week (Settings → Browsing,
introduce_every) while one is waiting.

Who is introduced to what (who_for):
- someone in the tree (a member's person; an admin's, or the person marked "me" for an admin who isn't anyone, and
  for a house without family accounts): papers about their own family, their ancestors first, the nearest
  generations first, then other blood relatives of older generations, then the rest of their family;
- someone who isn't in the tree: none, unless an admin showed them someone "with their family" on their account
  (access_rule.branch): then papers of that line, that person and their ancestors first, named rather than
  "your great-grandmother".

Among papers as close: letters, cards, diaries and clippings before deeds, tax and legal papers (KIND_FIRST), never
two in a row about the same person or from the same collection when there is a choice, and one written on today's
date in an earlier year first that day. Only papers with words to read, and none that look private (private.py).
"""
import json
import sqlite3
from datetime import date, datetime, timedelta

from lineagebox import clock, family, paper_cards, private
from lineagebox.catalog import ORIGIN, VISIBLE

EVERY = ("day", "week", "never")  # how often the menu marks a waiting introduction (Settings → Browsing)
# kinds of paper, the most worth meeting first; the rest after them, and those of the last list last
KIND_FIRST = ("Letter", "Card or note", "Diary or journal", "Newspaper clipping", "Obituary", "Award or certificate",
              "Diploma", "Photo record", "Family record", "Recipe", "Program or ticket", "Military record")
KIND_LAST = ("Census record", "Work record", "Property record", "Will or legal paper", "Money or tax paper",
             "Medical record", "Passport or ID", "Email")
ABOUT_MOST = 3  # people named on the card


def key(v) -> int:
    """Whose marks: the account, or 0 for the house (no family accounts)."""
    return v.account_id or 0


def mark_seen(w: sqlite3.Connection, v, paper_id: int) -> None:
    """They opened a paper (on a connection that may write). Never while an admin looks through someone's eyes."""
    if v.seeing_as or v.device is not None:
        return
    try:
        w.execute("""INSERT INTO main.paper_seen(account_id, paper_id, seen_at) VALUES (?, ?, ?)
                     ON CONFLICT(account_id, paper_id) DO NOTHING""", (key(v), paper_id, clock.now()))
        w.commit()
    except sqlite3.OperationalError:  # busy: marked the next time they open it
        w.rollback()


def in_a_family(con: sqlite3.Connection, pid: int) -> bool:
    """Whether someone has family in the tree: a parent, a child, a brother or sister, or a partner."""
    return con.execute("""SELECT EXISTS (SELECT 1 FROM main.parent WHERE :p IN (child_id, parent_id))
                                 OR EXISTS (SELECT 1 FROM main.sibling WHERE :p IN (a, b))
                                 OR EXISTS (SELECT 1 FROM main.partner WHERE :p IN (a, b))""", {"p": pid}).fetchone()[0]


def who_for(con: sqlite3.Connection, v) -> tuple[int | None, list[int]]:
    """(the person whose family it is, as "you"; or None) and (the people of lines shared with them, for someone who
    isn't in the tree, or is but with no family there: a friend's account). Neither: no introductions. con: one that
    sees the whole tree."""
    if v.me is not None and con.execute("SELECT 1 FROM main.person WHERE id = ?", (v.me,)).fetchone() \
            and in_a_family(con, v.me):
        return v.me, []
    if v.account is not None and not v.admin:
        shared = [r[0] for r in con.execute("""SELECT person_id FROM main.access_rule
                                               WHERE account_id = ? AND show AND branch ORDER BY person_id""",
                                            (v.account_id,))]
        return None, shared
    row = con.execute("SELECT value FROM main.settings WHERE key = 'me'").fetchone()
    pid = json.loads(row[0]) if row and row[0] else None
    pid = int(pid) if isinstance(pid, (int, str)) and str(pid).isdecimal() else None
    if pid is not None and con.execute("SELECT 1 FROM main.person WHERE id = ?", (pid,)).fetchone():
        return pid, []
    return None, []


def _closeness(g: family.Graph, ego: int | None, roots: list[int]) -> dict[int, tuple[int, int]]:
    """How near each person is, as (group, distance), the nearest first: 0 their ancestors (by generations), 1 other
    blood relatives of older generations, 2 the rest of their family. From roots (lines shared with someone not in
    the tree): 0 a root and their ancestors, 2 the rest of the line."""
    out: dict[int, tuple[int, int]] = {}
    if ego is not None and ego in g.people:
        for pid, up in g.ancestors.get(ego, {}).items():
            if up:
                out[pid] = (0, up)
        for pid, kin in family.relations_to(g, ego).items():
            if pid in out:
                continue
            blood = g.blood(ego, pid)
            older = blood is not None and blood[1] < blood[0]  # a great-aunt: nearer the shared ancestor than ego
            out[pid] = (1, blood[0] + blood[1]) if older else (2, kin.distance)
        return out
    for root in roots:
        for pid, up in g.ancestors.get(root, {}).items():
            out[pid] = min(out.get(pid, (9, 99)), (0, up))
    return out


def _kind_rank(kind: str) -> int:
    return 0 if kind in KIND_FIRST else 2 if kind in KIND_LAST else 1


def _people_of(con: sqlite3.Connection, ids: list[int]) -> dict[int, list[int]]:
    """Who each paper is about or names: linked by the card or by hand (paper_person), cited by it, or named on a
    face of one of its pages or added to it by hand. The most about it first."""
    out: dict[int, list[int]] = {}
    ids_json = json.dumps(ids)
    for paper_id, pid in con.execute("""
            SELECT paper_id, person_id FROM paper_person WHERE state IN ('auto', 'user')
              AND paper_id IN (SELECT value FROM json_each(:ids))
            UNION ALL SELECT c.image_id, coalesce(c.person_id, e.person_id) FROM citation c
              LEFT JOIN event e ON e.id = c.event_id WHERE c.image_id IN (SELECT value FROM json_each(:ids))
            UNION ALL SELECT coalesce(i.page_of, i.id), f.person_id FROM face f JOIN image i ON i.id = f.image_id
              WHERE f.assigned_by IN ('user', 'auto') AND coalesce(i.page_of, i.id) IN (SELECT value FROM json_each(:ids))
            UNION ALL SELECT image_id, person_id FROM image_person WHERE image_id IN (SELECT value FROM json_each(:ids))""",
                                  {"ids": ids_json}):
        if pid is not None and pid not in out.setdefault(paper_id, []):
            out[paper_id].append(pid)
    return out


def candidates(c: sqlite3.Connection, v, only: list[int] | None = None) -> list[dict]:
    """The papers the viewer may be introduced to (on their own connection: those they see), not opened by them,
    with words to read and not private: [{id, kind, title, written, written_from, written_to, summary, sha256,
    folder_id, skipped_at, offered_at}]. only: of these papers alone."""
    narrowed = "AND d.image_id IN (SELECT value FROM json_each(:only))" if only is not None else ""
    rows = c.execute(f"""
        SELECT d.image_id AS id, d.kind, d.title, d.written, d.written_from, d.written_to, d.summary, i.sha256,
               (SELECT {ORIGIN} FROM file f WHERE f.image_id = i.id AND NOT f.missing ORDER BY f.id LIMIT 1) AS folder_id,
               o.skipped_at, o.offered_at,
               (SELECT group_concat(p.text, char(10)) FROM paper_page p WHERE p.paper_id = d.image_id) AS words
        FROM document d JOIN image i ON i.id = d.image_id
        LEFT JOIN paper_offer o ON o.paper_id = d.image_id AND o.account_id = :me
        WHERE {VISIBLE} AND d.unreadable IS NULL
          AND d.image_id NOT IN (SELECT paper_id FROM paper_seen WHERE account_id = :me)
          AND EXISTS (SELECT 1 FROM paper_page p WHERE p.paper_id = d.image_id AND trim(coalesce(p.text, '')) != '')
          {narrowed}""", {"me": key(v), "only": json.dumps(only or [])}).fetchall()
    ok = {int(i) for i in private.ok_ids(c)}
    out = []
    for r in rows:
        if r["id"] not in ok and private.paper_private(r["kind"], r["words"], r["written_to"]):
            continue
        out.append({k: r[k] for k in r.keys() if k != "words"} | {"opening": _opening(r["words"])})
    return out


def _opening(words: str | None, most: int = 220) -> str:
    """The first words of a paper, for a card without a summary."""
    text = " ".join((words or "").split())
    return text if len(text) <= most else text[:most].rsplit(" ", 1)[0] + "…"


def ranked(c: sqlite3.Connection, everything: sqlite3.Connection, v, today: date | None = None,
           only: list[int] | None = None) -> list[dict]:
    """The viewer's papers to be introduced to, the first to offer first (see the module's docstring). Empty for
    someone who gets none. c: their own connection; everything: one that sees the whole tree (theirs, for an
    admin). only: of these papers alone."""
    ego, roots = who_for(everything, v)
    if ego is None and not roots:
        return []
    found = candidates(c, v, only)
    if not found:
        return []
    today = today or date.today()
    g = family.load(everything)
    near = _closeness(g, ego, roots)
    people = _people_of(c, [p["id"] for p in found])
    for p in found:
        them = people.get(p["id"], [])
        best = min((near[q] for q in them if q in near), default=(3 if them else 4, 0))
        p["people"] = sorted(them, key=lambda q: near.get(q, (9, 99)))
        p["near"] = best
        same_day = (p["written_from"] and p["written_from"] == p["written_to"]
                    and p["written_from"][5:10] == today.isoformat()[5:10] and p["written_from"][:4] < str(today.year))
        p["anniversary"] = bool(same_day)
    found.sort(key=lambda p: (not p["anniversary"], p["skipped_at"] is not None, p["skipped_at"] or "", p["near"],
                              _kind_rank(p["kind"]), p["written_from"] or "9999", p["id"]))
    return found


def _varied(found: list[dict], last: dict | None) -> dict:
    """The first of them, unless it is about the same person or from the same collection as the last offered: then
    the first one that isn't, if any is as near."""
    if not last or len(found) == 1 or found[0]["anniversary"]:
        return found[0]
    first = found[0]
    alike = []  # as near as the first: the ones it may give way to
    for p in found:
        if p["near"][0] != first["near"][0] or (p["skipped_at"] is not None) != (first["skipped_at"] is not None):
            break
        alike.append(p)
    other_person = [p for p in alike if not (p["people"][:1] and p["people"][:1] == last.get("people", [])[:1])]
    both = [p for p in other_person if p["folder_id"] != last.get("folder_id")]
    return (both or other_person or [first])[0]


def offer(c: sqlite3.Connection, everything: sqlite3.Connection, w: sqlite3.Connection, v,
          today: date | None = None) -> dict | None:
    """The paper to offer the viewer now: the one offered last, until they open it or ask for another, else the next
    (recorded as offered on w). None when there is none for them."""
    # the one offered last, while it is still theirs to meet: worked out alone, not with every other paper
    last = c.execute("""SELECT paper_id FROM paper_offer WHERE account_id = ? AND skipped_at IS NULL
                        ORDER BY offered_at DESC LIMIT 1""", (key(v),)).fetchone()
    current = ranked(c, everything, v, today, only=[last[0]]) if last else []
    if current:
        chosen = current[0]
    else:
        found = ranked(c, everything, v, today)
        if not found:
            return None
        last = _last_offered(w, v, found)
        chosen = _varied(found, last)
        _note_offer(w, v, chosen["id"])
    chosen["about"] = about_line(everything, v, chosen)
    return chosen


def _last_offered(w: sqlite3.Connection, v, found: list[dict]) -> dict | None:
    row = w.execute("""SELECT paper_id FROM main.paper_offer WHERE account_id = ? ORDER BY offered_at DESC LIMIT 1""",
                    (key(v),)).fetchone()
    if row is None:
        return None
    by_id = {p["id"]: p for p in found}
    if row[0] in by_id:
        return by_id[row[0]]
    folder = w.execute(f"""SELECT {ORIGIN} FROM main.file f WHERE f.image_id = ? AND NOT f.missing ORDER BY f.id
                           LIMIT 1""", (row[0],)).fetchone()
    people = _people_of(w, [row[0]]).get(row[0], [])
    return {"people": people, "folder_id": folder[0] if folder else None}


def _note_offer(w: sqlite3.Connection, v, paper_id: int) -> None:
    if v.seeing_as:
        return
    try:
        w.execute("""INSERT INTO main.paper_offer(account_id, paper_id, offered_at) VALUES (?, ?, ?)
                     ON CONFLICT(account_id, paper_id) DO UPDATE SET offered_at = excluded.offered_at,
                                                                     skipped_at = NULL""",
                  (key(v), paper_id, clock.now()))
        w.commit()
    except sqlite3.OperationalError:
        w.rollback()


def skip(w: sqlite3.Connection, v, paper_id: int) -> None:
    """Show me another: this one goes behind the rest. Doesn't commit."""
    w.execute("""INSERT INTO main.paper_offer(account_id, paper_id, offered_at, skipped_at) VALUES (?, ?, ?, ?)
                 ON CONFLICT(account_id, paper_id) DO UPDATE SET skipped_at = excluded.skipped_at""",
              (key(v), paper_id, clock.now(), clock.now()))


def about_line(everything: sqlite3.Connection, v, paper: dict) -> list[dict]:
    """Who the paper is about, for its card: [{person_id, name, kin}], kin as they are to the viewer ("your
    great-grandmother") for someone in the tree, None otherwise (named only)."""
    ego, _ = who_for(everything, v)
    them = paper.get("people", [])[:ABOUT_MOST]
    if not them:
        return []
    g = family.load(everything)
    kin = family.relations_to(g, ego) if ego is not None else {}
    lives = {r["id"]: r for r in everything.execute(
        f"""SELECT p.id, p.name, {paper_cards.LIFE.replace('event e', 'main.event e')} FROM main.person p
            WHERE p.id IN (SELECT value FROM json_each(?))""", (json.dumps(them),))}
    out = []
    for pid in them:
        if pid not in lives:
            continue
        label = kin[pid].label if pid in kin and pid != ego else ("you" if pid == ego else None)
        out.append({"person_id": pid, "name": lives[pid]["name"], "kin": label,
                    "years": paper_cards.life_years(lives[pid]["birth_date"], lives[pid]["death_date"])})
    return out


def _period_start(every: str, now: datetime) -> str:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if every == "week":
        start -= timedelta(days=start.weekday())
    return start.isoformat(timespec="seconds")


def waiting_mark(c: sqlite3.Connection, v, every: str, now: datetime | None = None) -> bool:
    """Whether the menu marks Historical for them: a paper they haven't opened has words to read, and nothing was
    offered to them since this day (or week) began. Never with introduce_every "never"; cheap: no ranking."""
    if every not in ("day", "week"):
        return False
    since = _period_start(every, now or clock.now_datetime())
    if c.execute("SELECT 1 FROM paper_offer WHERE account_id = ? AND offered_at >= ? LIMIT 1",
                 (key(v), since)).fetchone():
        return False
    return c.execute(f"""SELECT EXISTS (SELECT 1 FROM document d JOIN image i ON i.id = d.image_id
                                       WHERE {VISIBLE} AND d.unreadable IS NULL
                                         AND d.image_id NOT IN (SELECT paper_id FROM paper_seen WHERE account_id = ?)
                                         AND EXISTS (SELECT 1 FROM paper_page p WHERE p.paper_id = d.image_id
                                                     AND trim(coalesce(p.text, '')) != ''))""",
                     (key(v),)).fetchone()[0] == 1
