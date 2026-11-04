"""What looks wrong or unfinished in the family tree, for Cleanup → Family tree: someone who is their own ancestor,
someone with one parent entered, brothers and sisters with none, dates that can't be right, one person perhaps entered
twice, parents with no sex set (family tree programs file children under a father and a mother), and a child's parents
not entered as partners.

Each problem has a key that stays the same while it lasts, so one that is right after all (a parent nobody knows)
can be marked fine and is not listed again."""
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

from lineagebox import family, settings

OK_KEY = "family_checks_ok"  # settings: the keys of problems marked fine as they are

# kind -> (heading, what it is and how to put it right)
KINDS = {
    "loop": ("Their own ancestor", "Going up from them through their parents comes back to them, which can't be: one "
             "of the parent links is wrong (two people merged who were a parent and a child, say). Remove it on their "
             "page."),
    "one_parent": ("Only one parent", "One parent is entered. Link the other if they are in the catalog (one of the "
                   "parent's partners, or anyone by name); mark it fine if they are unknown."),
    "no_parents": ("Brothers and sisters with no parents", "Entered as siblings, with no parent for any of them. A "
                   "parent added to one of them is everyone's."),
    "dates": ("Dates that can't be right", "Born before a parent, to a parent too young or too old, after a parent "
              "died, or an event after their own death. One of the dates is probably wrong."),
    "twice": ("Maybe one person entered twice", "Names that could be the same person's. If they are, open the one to "
              "go and use Merge into on its page."),
    "no_sex": ("Parents with no sex set", "Family tree programs file each child under a father and a mother."),
    "not_partners": ("Parents not entered as partners", "A child's two parents, never entered as partners (married, "
                     "together or former). Add them as such on either parent's page."),
}
YOUNGEST_PARENT, OLDEST_PARENT = 12, 70
_AFTER_DEATH_OK = {"BURI", "CREM", "PROB", "WILL"}


@dataclass
class Issue:
    kind: str
    key: str                 # the same while the problem lasts: "one_parent:12"
    people: list[int]        # whom it is about, the one to fix first
    text: str
    choices: list[int] = field(default_factory=list)  # one-click fixes: the other parent it may be


def check(con: sqlite3.Connection) -> list[Issue]:
    """Every problem not marked fine, by kind in the order of KINDS."""
    g = family.load(con)
    ok = set(settings.get(con, OK_KEY, []))
    found = [*_loops(g), *_parents(g), *_dates(con, g), *_twice(con, g), *_no_sex(g)]
    order = list(KINDS)
    return sorted((i for i in found if i.key not in ok), key=lambda i: (order.index(i.kind), g.name(i.people[0])))


def mark_ok(con: sqlite3.Connection, key: str) -> None:
    settings.put(con, OK_KEY, sorted({*settings.get(con, OK_KEY, []), key}))


def _loops(g: family.Graph) -> list[Issue]:
    """People among the ancestors of their own parents, one issue for each such circle of people."""
    out, seen = [], set()
    for p, parents in g.effective_parents.items():
        if p in seen or not any(p in g.ancestors.get(q, {}) for q in parents):
            continue
        circle = sorted((q for q in g.ancestors[p] if p in g.ancestors.get(q, {})), key=g.name)
        seen |= set(circle)
        out.append(Issue("loop", f"loop:{min(circle)}", circle, _and([g.name(q) for q in circle])))
    return out


def _parents(g: family.Graph) -> list[Issue]:
    out, seen, unpartnered = [], set(), defaultdict(list)
    for child, entered in g.parents.items():
        if len(entered) == 1:
            (parent,) = entered
            others = sorted((q for q in g.partners.get(parent, {})
                             if q != child and child not in g.ancestors.get(q, {})),
                            key=lambda q: (not g.partners[parent][q][0], g.name(q)))  # current partners first
            out.append(Issue("one_parent", f"one_parent:{child}", [child, parent],
                             f"{g.name(child)}: only {g.name(parent)} is entered", others))
        elif len(entered) == 2:
            a, b = sorted(entered)
            if b not in g.partners.get(a, {}):
                unpartnered[a, b].append(child)
    for (a, b), children in unpartnered.items():
        out.append(Issue("not_partners", f"not_partners:{a}:{b}", [a, b], f"{g.name(a)} and {g.name(b)}, parents of "
                         + _and(sorted(map(g.name, children)))))
    for p, ps in g.effective_parents.items():  # stand-in parents: siblings with none entered
        if p in seen or not any(q < 0 for q in ps):
            continue
        group = sorted((c for c, cs in g.effective_parents.items() if cs == ps), key=g.name)
        seen |= set(group)
        out.append(Issue("no_parents", f"no_parents:{min(group)}", group, _and([g.name(c) for c in group])))
    return out


def _dates(con: sqlite3.Connection, g: family.Graph) -> list[Issue]:
    years = defaultdict(dict)  # person -> {tag: first year}
    events = defaultdict(list)  # person -> [(tag, year)] of their other events
    for pid, tag, sort in con.execute("""SELECT person_id, tag, sort_date FROM event
                                         WHERE person_id IS NOT NULL AND sort_date IS NOT NULL"""):
        years[pid].setdefault(tag, int(sort[:4]))
        events[pid].append((tag, int(sort[:4])))
    for a, b, tag, sort in con.execute("""SELECT couple_a, couple_b, tag, sort_date FROM event
                                          WHERE couple_a IS NOT NULL AND sort_date IS NOT NULL"""):
        events[a].append((tag, int(sort[:4])))
        events[b].append((tag, int(sort[:4])))
    words = {"MARR": "married", "DIV": "divorced", "ENGA": "engaged"}
    out = []
    for pid in g.people:
        born, died = years[pid].get("BIRT"), years[pid].get("DEAT")
        said = []
        if born and died and died < born:
            said.append(f"died in {died}, before being born in {born}")
        for parent in sorted(g.parents.get(pid, ())):
            p_born, p_died = years[parent].get("BIRT"), years[parent].get("DEAT")
            if born and p_born and born - p_born < YOUNGEST_PARENT:
                said.append(f"born in {born}, when {g.name(parent)} (born {p_born}) was "
                            + (f"{born - p_born}" if born >= p_born else "not yet born"))
            elif born and p_born and born - p_born > OLDEST_PARENT:
                said.append(f"born in {born}, when {g.name(parent)} was {born - p_born}")
            if born and p_died and born > p_died + 1:
                said.append(f"born in {born}, after {g.name(parent)} died in {p_died}")
        for tag, year in events[pid]:
            if died and year > died and tag not in _AFTER_DEATH_OK:
                said.append(f"{words.get(tag, tag.lower())} in {year}, after dying in {died}")
            elif born and year < born + YOUNGEST_PARENT and tag in words:
                said.append(f"{words[tag]} in {year}, at {year - born}")
        for text in dict.fromkeys(said):
            out.append(Issue("dates", f"dates:{pid}:{_slug(text)}", [pid], f"{g.name(pid)}: {text}"))
    return out


def _twice(con: sqlite3.Connection, g: family.Graph) -> list[Issue]:
    """Pairs whose names could be one person's: "Emma" and "Emma Carter" (a first name alone), "Tom Miller" and "Tom
    James Miller" (the same first and last names), or someone's other name (a maiden name) as another's name. Never
    a parent and child, siblings or partners (a Jr. and Sr.)."""
    names = {p: _words(r["name"]) for p, r in g.people.items()}
    others = defaultdict(set)
    for pid, name in con.execute("SELECT person_id, name FROM person_name"):
        others[pid].add(tuple(_words(name)))
    close = lambda a, b: (b in g.parents.get(a, ()) or a in g.parents.get(b, ()) or b in g.all_siblings(a)
                          or b in g.partners.get(a, {}))
    out = []
    ids = sorted(names)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            x, y = names[a], names[b]
            if not x or not y or x[0] != y[0] and tuple(x) not in others[b] and tuple(y) not in others[a]:
                continue
            alike = (len(x) == 1 or len(y) == 1 or (len(x) > 1 and len(y) > 1 and x[-1] == y[-1] and x != y)
                     or tuple(x) in others[b] or tuple(y) in others[a] or x == y)
            if alike and not close(a, b):
                out.append(Issue("twice", f"twice:{a}:{b}", [a, b], f"{g.name(a)} and {g.name(b)}"))
    return out


def _no_sex(g: family.Graph) -> list[Issue]:
    return [Issue("no_sex", f"no_sex:{p}", [p], f"{g.name(p)}, parent of "
                  + _and([g.name(c) for c in sorted(g.effective_children[p], key=g.name) if c > 0]))
            for p in g.people if not g.sex(p) and any(c > 0 for c in g.effective_children.get(p, ()))]


def _words(name: str) -> list[str]:
    return re.sub(r"[^\w\s-]", "", name.lower()).split()


def _slug(text: str) -> str:
    return re.sub(r"\W+", "-", text.lower()).strip("-")[:60]


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
