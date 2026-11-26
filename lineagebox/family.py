"""Family and other relationships between people.

Only facts are stored: who is whose parent, who are siblings (when their parents are not in the catalog), who are or
were a couple (married, partners, or going out together: see BONDS), each person's sex, and other relationships such
as friend or coworker. Everything else is worked out
here, relative to one person (normally the one marked as "me"): niece, brother-in-law, stepson, former sister-in-law...
Those words also work in search ("nephews 2024") and choose the people of photo tours ("Brothers 2024").

A child belongs to each parent by birth, by adoption or as a foster child (see PEDI): an adopted son is a son, with
"adopted" before it where it is about the link itself ("adopted son", "adoptive mother", "birth mother"), and a foster
family is family through that link only ("foster mother", "foster brother").
"""
import json
import re
import sqlite3
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field

from lineagebox import errors, events

SEXES = ("male", "female")

# What someone can be added as on a person's page: word -> (link, the sex it implies)
FAMILY_WORDS = {
    "father": ("parent", "male"), "mother": ("parent", "female"), "parent": ("parent", None),
    "son": ("child", "male"), "daughter": ("child", "female"), "child": ("child", None),
    "brother": ("sibling", "male"), "sister": ("sibling", "female"), "sibling": ("sibling", None),
    "husband": ("partner", "male"), "wife": ("partner", "female"), "partner": ("partner", None),
    "boyfriend": ("partner", "male"), "girlfriend": ("partner", "female"),
    "ex-husband": ("ex", "male"), "ex-wife": ("ex", "female"), "ex-partner": ("ex", None),
    "ex-boyfriend": ("ex", "male"), "ex-girlfriend": ("ex", "female"),
    "adoptive father": ("parent", "male"), "adoptive mother": ("parent", "female"), "adoptive parent": ("parent", None),
    "adopted son": ("child", "male"), "adopted daughter": ("child", "female"), "adopted child": ("child", None),
    "foster father": ("parent", "male"), "foster mother": ("parent", "female"), "foster parent": ("parent", None),
    "foster son": ("child", "male"), "foster daughter": ("child", "female"), "foster child": ("child", None),
}
# What a couple are to each other, by the word entered (partner.married and partner.dating): married, partners
# sharing a life without marrying, or going out together. Each of the three can be current or former. Many in a family
# never married: some have children together, and the tree holds them as it holds any other couple.
BONDS = {"husband": "married", "wife": "married", "spouse": "married", "partner": "partner",
         "boyfriend": "dating", "girlfriend": "dating"}
BOND_WORDS = {"married": ("husband", "wife", "spouse"), "partner": ("partner", "partner", "partner"),
              "dating": ("boyfriend", "girlfriend", "partner")}

# How a child belongs to a parent (parent.pedi, GEDCOM's FAMC.PEDI): by birth, adopted, or fostered. Adoption makes
# them family as birth does, so an adoptive mother's mother is a grandmother; a foster family is family through the
# foster link only ("foster mother", "foster brother", "foster grandmother"). At most two birth parents and two
# adoptive parents each; a child can have both, and foster parents besides.
PEDI = ("birth", "adopted", "foster")
PEDI_OF_WORD = {"adoptive": "adopted", "adopted": "adopted", "foster": "foster"}  # by the word's first word
TWO_EACH = {"birth", "adopted"}
OTHER_WORDS = ("friend", "best friend", "family friend", "coworker", "neighbor", "classmate", "teammate", "teacher",
               "coach", "babysitter", "pet")

# Headings on the Family page, closest first
GROUPS = ("you", "partner", "parents", "siblings", "children", "grandchildren", "grandparents", "aunts and uncles",
          "nieces and nephews", "cousins", "in-laws", "step-family", "extended family", "former family")
_ORDINALS = {2: "second", 3: "third", 4: "fourth", 5: "fifth"}


@dataclass
class Kin:
    label: str                          # "nephew", "former sister-in-law", "wife's great-aunt"
    group: str                          # one of GROUPS
    words: set[str] = field(default_factory=set)  # what finds them in search
    distance: int = 0                   # for sorting: closer relatives first


@dataclass
class Graph:
    people: dict[int, sqlite3.Row]
    parents: dict[int, set[int]]                     # as entered
    children: dict[int, set[int]]
    siblings: dict[int, set[int]]                    # siblings by word
    partners: dict[int, dict[int, tuple[bool, str]]]  # person -> {partner: (current, one of BONDS' kinds)}
    relations: list[tuple[int, int, str]]            # (person, of whom, kind)
    ancestors: dict[int, dict[int, int]] = field(default_factory=dict)  # person -> {ancestor: generations up}
    pedi: dict[tuple[int, int], str] = field(default_factory=dict)      # (child, parent) -> adopted|foster; else birth

    def __post_init__(self):
        # Siblings by word share their parents: those entered for any of them, or else a stand-in (a negative id)
        effective = {p: set(self.parents.get(p, ())) for p in self.people}
        seen = set()
        for start in self.people:
            if start in seen or not self.siblings.get(start):
                continue
            group, todo = set(), [start]
            while todo:
                p = todo.pop()
                if p not in group:
                    group.add(p)
                    todo.extend(self.siblings.get(p, ()))
            seen |= group
            shared = set().union(*(self.parents.get(p, set()) for p in group)) or {-min(group)}
            for p in group:
                if not effective[p]:
                    effective[p] = set(shared)
        self.effective_parents = effective
        self.effective_children = defaultdict(set)
        for child, ps in effective.items():
            for parent in ps:
                self.effective_children[parent].add(child)
        # kin_ancestors: the same, up birth and adoptive links only. Someone shares an ancestor with ego only through a
        # foster link when blood() finds one and kin() doesn't: a foster mother, a foster brother.
        fostered = "foster" in self.pedi.values()
        self.kin_ancestors = {} if fostered else self.ancestors  # most families have no foster link: one walk
        for p in self.people:
            self.ancestors[p] = self._up(p, lambda q: effective.get(q, ()))
            if fostered:
                self.kin_ancestors[p] = self._up(p, lambda q: (r for r in effective.get(q, ())
                                                               if self.pedi.get((q, r)) != "foster"))

    @staticmethod
    def _up(p: int, parents) -> dict[int, int]:
        up, todo = {p: 0}, deque([p])
        while todo:
            q = todo.popleft()
            for parent in parents(q):
                if parent not in up:
                    up[parent] = up[q] + 1
                    todo.append(parent)
        return up

    def how(self, child: int, parent: int) -> str:
        """How a child belongs to a parent: 'birth', 'adopted' or 'foster'."""
        return self.pedi.get((child, parent), "birth")

    def birth_parents(self, pid: int) -> set[int]:
        return {p for p in self.parents.get(pid, ()) if self.how(pid, p) == "birth"}

    def sex(self, pid: int) -> str | None:
        return self.people[pid]["sex"] if pid in self.people else None

    def name(self, pid: int) -> str:
        return self.people[pid]["name"] if pid in self.people else "?"

    def blood(self, a: int, b: int, kin: bool = False) -> tuple[int, int] | None:
        """(generations from a up to the closest shared ancestor, generations from b up to it), or None. kin: up
        birth and adoptive links only, leaving out foster ones."""
        ancestors = self.kin_ancestors if kin else self.ancestors
        up_a, up_b = ancestors.get(a, {}), ancestors.get(b, {})
        shared = up_a.keys() & up_b.keys()
        if not shared:
            return None
        best = min(shared, key=lambda s: (up_a[s] + up_b[s], up_a[s]))
        return up_a[best], up_b[best]

    def half(self, a: int, b: int) -> bool:
        """Half brothers and sisters: two birth parents each, one of them the same."""
        pa, pb = self.birth_parents(a), self.birth_parents(b)
        return len(pa) == 2 and len(pb) == 2 and len(pa & pb) == 1

    def all_siblings(self, pid: int) -> set[int]:
        out = set(self.siblings.get(pid, ()))
        for parent in self.effective_parents.get(pid, ()):
            out |= {c for c, ps in self.effective_parents.items() if parent in ps}
        out.discard(pid)
        return out


def immediate(g: Graph, pid: int, formers: bool = True) -> list[int]:
    """Someone and their immediate family, in that order: parents, current partner, brothers and sisters (half ones
    too), children, and last the partners they once had, each by name. A member's Home shows their photos first, and
    (formers False, their own choice there) can leave the former partners out."""
    by_name = lambda ids: sorted(ids, key=lambda i: g.name(i).lower())  # noqa: E731
    partners = {q for q, (current, _) in g.partners.get(pid, {}).items() if current}
    past = {q for q, (current, _) in g.partners.get(pid, {}).items() if not current} if formers else set()
    ids = [pid, *by_name(g.effective_parents.get(pid, ())), *by_name(partners), *by_name(g.all_siblings(pid)),
           *by_name(g.effective_children.get(pid, ())), *by_name(past)]
    return [i for i in dict.fromkeys(ids) if i in g.people]  # not the stand-in parent of siblings with none entered


def load(con: sqlite3.Connection) -> Graph:
    people = {r["id"]: r for r in con.execute("SELECT id, name, sex, deceased, birth_date FROM person")}
    parents, children, siblings = defaultdict(set), defaultdict(set), defaultdict(set)
    partners, pedi = defaultdict(dict), {}
    for child, parent, how in con.execute("SELECT child_id, parent_id, pedi FROM parent"):
        parents[child].add(parent)
        children[parent].add(child)
        if how in ("adopted", "foster"):
            pedi[child, parent] = how
    for a, b in con.execute("SELECT a, b FROM sibling"):
        siblings[a].add(b)
        siblings[b].add(a)
    try:
        couples = con.execute("SELECT a, b, current, married, dating FROM partner").fetchall()
    except sqlite3.OperationalError:  # migration 009 works out kin words here, before 038 adds dating
        couples = [(*r, 0) for r in con.execute("SELECT a, b, current, married FROM partner")]
    for a, b, current, married, dating in couples:
        bond = "married" if married else "dating" if dating else "partner"
        partners[a][b] = partners[b][a] = (bool(current), bond)
    relations = [tuple(r) for r in con.execute("SELECT person_id, of_id, kind FROM relation")]
    return Graph(people, dict(parents), dict(children), dict(siblings), dict(partners), relations, pedi=pedi)


def linked(con: sqlite3.Connection) -> set[int]:
    """Everyone with a parent, a child, a brother or sister, a partner, or another relationship (a friend) entered.
    On a connection that sees everyone, the links a family member doesn't see count too (a stepchild's father)."""
    return {r[0] for r in con.execute("""SELECT child_id FROM parent UNION SELECT parent_id FROM parent
                                         UNION SELECT a FROM sibling UNION SELECT b FROM sibling
                                         UNION SELECT a FROM partner UNION SELECT b FROM partner
                                         UNION SELECT person_id FROM relation UNION SELECT of_id FROM relation""")}


def me(con: sqlite3.Connection) -> int | None:
    """The person things are worked out from ("my niece"): whoever is signed in, else the one marked as "me"."""
    if getattr(con, "me", None) is not None:
        return con.me if con.execute("SELECT 1 FROM person WHERE id = ?", (con.me,)).fetchone() else None
    row = con.execute("SELECT value FROM settings WHERE key = 'me'").fetchone()
    pid = int(row[0]) if row and row[0] and row[0].isdecimal() else None
    return pid if pid and con.execute("SELECT 1 FROM person WHERE id = ?", (pid,)).fetchone() else None


# ---- what someone is to someone else ------------------------------------------------------------------------------

def _w(sex, male, female, neutral):
    return male if sex == "male" else female if sex == "female" else neutral


def compact(label: str) -> str:
    """'sister-in-law' -> 'sisterinlaw': one search word."""
    return re.sub(r"[^a-z]", "", label.lower())


def _blood_label(a: int, b: int, sex, half=False) -> tuple[str, str]:
    if b == 0:
        if a == 1:
            return _w(sex, "father", "mother", "parent"), "parents"
        return "great-" * (a - 2) + _w(sex, "grandfather", "grandmother", "grandparent"), "grandparents"
    if a == 0:
        if b == 1:
            return _w(sex, "son", "daughter", "child"), "children"
        return "great-" * (b - 2) + _w(sex, "grandson", "granddaughter", "grandchild"), "grandchildren"
    if a == 1 and b == 1:
        return ("half-" if half else "") + _w(sex, "brother", "sister", "sibling"), "siblings"
    if a == 1:
        return "great-" * (b - 2) + _w(sex, "nephew", "niece", "niece or nephew"), "nieces and nephews"
    if b == 1:
        return "great-" * (a - 2) + _w(sex, "uncle", "aunt", "aunt or uncle"), "aunts and uncles"
    degree, removed = min(a, b) - 1, abs(a - b)
    label = ("" if degree == 1 else _ORDINALS.get(degree, f"{degree}th") + " ") + "cousin"
    if removed:
        label += " " + {1: "once", 2: "twice"}.get(removed, f"{removed} times") + " removed"
    return label, "cousins"


def _partners_relative(a: int, b: int, sex, half=False) -> tuple[str, str]:
    """X is my partner's (a, b) blood relative, and not mine."""
    if b == 0:
        if a == 1:
            return _w(sex, "father-in-law", "mother-in-law", "parent-in-law"), "in-laws"
        return "great-" * (a - 2) + _w(sex, "grandfather-in-law", "grandmother-in-law", "grandparent-in-law"), "in-laws"
    if a == 0:
        if b == 1:
            return "step" + _w(sex, "son", "daughter", "child"), "step-family"
        return "step-" + "great-" * (b - 2) + _w(sex, "grandson", "granddaughter", "grandchild"), "step-family"
    if a == 1 and b == 1:
        return _w(sex, "brother-in-law", "sister-in-law", "sibling-in-law"), "in-laws"
    if a == 1 or b == 1:  # a partner's niece or uncle is one's own, by marriage
        return _blood_label(a, b, sex)
    return "cousin-in-law", "in-laws"


def _relatives_partner(a: int, b: int, sex) -> tuple[str, str]:
    """X is the partner of my (a, b) blood relative."""
    if a == 0:
        if b == 1:
            return _w(sex, "son-in-law", "daughter-in-law", "child-in-law"), "in-laws"
        return "great-" * (b - 2) + _w(sex, "grandson-in-law", "granddaughter-in-law", "grandchild-in-law"), "in-laws"
    if b == 0:
        if a == 1:
            return "step" + _w(sex, "father", "mother", "parent"), "step-family"
        return "step-" + "great-" * (a - 2) + _w(sex, "grandfather", "grandmother", "grandparent"), "step-family"
    if a == 1 and b == 1:
        return _w(sex, "brother-in-law", "sister-in-law", "sibling-in-law"), "in-laws"
    if a == 1:
        return "great-" * (b - 2) + _w(sex, "nephew-in-law", "niece-in-law", "niece- or nephew-in-law"), "in-laws"
    if b == 1:  # an uncle's wife is an aunt
        return _blood_label(a, b, sex)
    return "cousin-in-law", "in-laws"


def _partner_word(sex, bond: str, current: bool) -> str:
    """'wife', 'ex-girlfriend', 'partner': what one of a couple is to the other, by what they are to each other
    (BONDS) and whether they still are."""
    word = _w(sex, *BOND_WORDS.get(bond, BOND_WORDS["partner"]))
    return word if current else "ex-" + word


def _kin(label: str, group: str, neutral: str, distance: int, also: set[str] = frozenset()) -> Kin:
    former = group == "former family"
    words = {compact(label), compact(neutral), *also}
    words |= {"former", "ex"} if former else {"family", "relative"}
    if group == "in-laws":
        words.add("inlaw")
    if group == "step-family":
        words.add("step")
    if group == "cousins":
        words.add("cousin")
    if group == "partner":
        words.add("partner")
    if group == "extended family":
        words.add("extended")
    return Kin(label, group, words, distance)


def relations_to(g: Graph, ego: int) -> dict[int, Kin]:
    """What everyone connected to ego is to them. Blood comes first (a child of a former marriage is a son, not a
    former stepson), then partners, then partners' relatives and relatives' partners, then step-siblings; anyone
    further away is described along the way ("wife's ex-husband")."""
    if ego not in g.people:
        return {}
    out = {ego: Kin("you", "you", set(), 0)}
    blood = {}  # by birth or adoption; foster family is labeled, and the rest is worked out from these
    for x in g.people:
        if x == ego:
            continue
        kin = g.blood(ego, x, kin=True)
        if not (ab := kin or g.blood(ego, x)):
            continue
        half = ab == (1, 1) and g.half(ego, x)
        label, group = _blood_label(*ab, g.sex(x), half)
        neutral = _blood_label(*ab, None, half)[0]
        if kin:
            blood[x] = ab
            prefix = _adoption_word(g, ego, x, ab)
            also = {compact(label), compact(neutral), "adopted" if "adopt" in prefix else ""} if prefix else set()
        else:
            prefix, also = "foster ", {"foster"}
        out[x] = _kin(prefix + label, group, prefix + neutral, sum(ab), also - {""})
    mine = sorted(g.partners.get(ego, {}).items(), key=lambda kv: not kv[1][0])  # current first
    for q, (current, bond) in mine:
        if q not in out:
            out[q] = _kin(_partner_word(g.sex(q), bond, current), "partner" if current else "former family",
                          _partner_word(None, bond, current), 1)
    for q, (current, bond) in mine:
        if bond == "dating":
            continue  # a girlfriend's family are not in-laws: they are described by the way to them below
        for x in g.people:
            if x not in out and (ab := g.blood(q, x, kin=True)):
                label, group = _partners_relative(*ab, g.sex(x))
                neutral = _partners_relative(*ab, None)[0]
                if not current:
                    label, group, neutral = "former " + label, "former family", "former " + neutral
                out[x] = _kin(label, group, neutral, sum(ab) + 1)
    for y, ab in sorted(blood.items(), key=lambda kv: sum(kv[1])):
        for r, (current, bond) in sorted(g.partners.get(y, {}).items(), key=lambda kv: not kv[1][0]):
            if r not in out and bond != "dating":  # a sister's boyfriend is no brother-in-law: described below
                label, group = _relatives_partner(*ab, g.sex(r))
                neutral = _relatives_partner(*ab, None)[0]
                if not current:
                    label, group, neutral = "former " + label, "former family", "former " + neutral
                out[r] = _kin(label, group, neutral, sum(ab) + 1)
    # A relative's current partner's children from other relationships are step-family: a parent's are stepsiblings,
    # a brother's are step-nieces and step-nephews. (The children of an ex, or of someone a relative is going out
    # with, are described by the way to them instead: "father's girlfriend's son".)
    for y, (a, b) in sorted(blood.items(), key=lambda kv: sum(kv[1])):
        for r, (current, bond) in g.partners.get(y, {}).items():
            for x in sorted(g.effective_children.get(r, ())) if current and bond != "dating" else ():
                if x > 0 and x not in out and g.how(x, r) != "foster":
                    out[x] = _kin(_step(_blood_label(a, b + 1, g.sex(x))[0]), "step-family",
                                  _step(_blood_label(a, b + 1, None)[0]), a + b + 2)
    _describe_the_rest(g, ego, out)
    return out


def _adoption_word(g: Graph, ego: int, x: int, ab: tuple[int, int]) -> str:
    """What goes before a parent's or a child's word: 'adoptive ' (mother), 'adopted ' (son), or 'birth ' (mother)
    for the parents by birth of someone who has adoptive ones too; '' otherwise."""
    if ab == (1, 0) and x in g.parents.get(ego, ()):
        if g.how(ego, x) == "adopted":
            return "adoptive "
        adopted = any(g.how(ego, p) == "adopted" for p in g.parents[ego])
        return "birth " if adopted and g.how(ego, x) == "birth" else ""
    if ab == (0, 1) and ego in g.parents.get(x, ()) and g.how(x, ego) == "adopted":
        return "adopted "
    return ""


def _step(label: str) -> str:
    """'brother' -> 'stepbrother', 'niece' -> 'step-niece'."""
    closed = ("brother", "sister", "sibling", "son", "daughter", "child", "father", "mother", "parent")
    return "step" + label if label in closed else "step-" + label


def _describe_the_rest(g: Graph, ego: int, out: dict[int, Kin]) -> None:
    """Label people further out by the way to them: "wife's ex-husband", "sister-in-law's brother"."""
    todo = deque([ego])
    seen = {ego}
    while todo:
        u = todo.popleft()
        steps = [(p, _w(g.sex(p), "father", "mother", "parent")) for p in g.parents.get(u, ())]
        steps += [(c, _w(g.sex(c), "son", "daughter", "child")) for c in g.children.get(u, ())]
        steps += [(s, _w(g.sex(s), "brother", "sister", "sibling")) for s in g.siblings.get(u, ())]
        steps += [(r, _partner_word(g.sex(r), bond, cur)) for r, (cur, bond) in g.partners.get(u, {}).items()]
        for v, word in steps:
            if v in seen:
                continue
            seen.add(v)
            todo.append(v)
            if v not in out and u in out and out[u].label.count("'s") < 2:
                base = out[u].label
                out[v] = Kin(f"{base}'s {word}", "extended family", {"extended", "relative"},
                             out[u].distance + 1)


def links(g: Graph, pid: int) -> dict:
    """One person's family as their page lists it. 'entered' says whether a link was entered for them (it can be
    removed there) or follows from another (a brother's parents). 'how' says how a child belongs to a parent (birth,
    adopted, foster); a child's 'with' is their other parent of the same kind."""
    entered_children = g.children.get(pid, set())
    return {
        "parents": [{"id": p, "entered": p in g.parents.get(pid, ()), "how": g.how(pid, p)}
                    for p in sorted(g.effective_parents.get(pid, ()), key=lambda p: (PEDI.index(g.how(pid, p)), p))
                    if p > 0],
        "partners": [{"id": q, "current": cur, "bond": bond, "word": _partner_word(g.sex(q), bond, True)}
                     for q, (cur, bond) in sorted(g.partners.get(pid, {}).items(),
                                                  key=lambda kv: (not kv[1][0], kv[0]))],
        "siblings": [{"id": s, "entered": s in g.siblings.get(pid, ()), "half": g.half(pid, s),
                      "foster": g.blood(pid, s, kin=True) is None}
                     for s in sorted(g.all_siblings(pid), key=g.name)],
        "children": [{"id": c, "entered": c in entered_children, "how": g.how(c, pid),
                      "with": sorted(p for p in g.effective_parents.get(c, ())
                                     if p > 0 and p != pid and g.how(c, p) == g.how(c, pid))}
                     for c in sorted(g.effective_children.get(pid, ()), key=lambda c: (
                         g.people[c]["birth_date"] or "9999", g.name(c))) if c > 0],
        "others": _grouped_others(g, pid),
    }


def _grouped_others(g: Graph, pid: int) -> list[dict]:
    """Other relationships, one entry per kind: 'Pets: Rex, Daisy, Biscuit', 'Friend of: Tom'."""
    groups: dict[tuple[str, bool], list[int]] = {}
    for kind, other, is_kind in other_relations(g, pid):
        groups.setdefault((kind, is_kind), []).append(other)
    return [{"kind": kind, "is_kind": is_kind, "label": (kind + " of" if is_kind else plural(kind)).capitalize(),
             "ids": sorted(ids, key=g.name)} for (kind, is_kind), ids in sorted(groups.items())]


def worked_out(g: Graph, pid: int) -> list[tuple[str, list[tuple[int, str]]]]:
    """Everyone else in pid's family from pid's own point of view (grandparents, cousins, in-laws, step-family...),
    by group: what their page shows besides the links entered for them."""
    entered = {pid, *(p for p in g.effective_parents.get(pid, ()) if p > 0), *g.partners.get(pid, {}),
               *g.all_siblings(pid), *(c for c in g.effective_children.get(pid, ()) if c > 0)}
    groups: dict[str, list] = {}
    for x, k in sorted(relations_to(g, pid).items(), key=lambda kv: (kv[1].distance, g.name(kv[0]))):
        if x not in entered:
            groups.setdefault(k.group, []).append((x, k.label))
    return [(name, groups[name]) for name in GROUPS if name in groups]


def plural(label: str) -> str:
    """'brother-in-law' -> 'brothers-in-law', 'pet' -> 'pets', 'child' -> 'children'."""
    label = label.replace("niece or nephew", "nieces and nephews").replace("aunt or uncle", "aunts and uncles")
    if " and " in label:
        return label
    m = re.match(r"^(.*?)((?:-in-law)|(?: (?:once|twice|\d+ times) removed))?$", label)
    head, tail = m[1], m[2] or ""
    for one, many in (("child", "children"), ("wife", "wives")):
        if head.endswith(one):
            return head[:-len(one)] + many + tail
    return head + ("es" if head.endswith(("ch", "sh", "s", "x")) else "s") + tail


def other_relations(g: Graph, pid: int) -> list[tuple[str, int, bool]]:
    """(kind, other person, whether pid is the one who is the kind): ('friend', Tom, True) = pid is Tom's friend."""
    out = [(kind, of, True) for p, of, kind in g.relations if p == pid]
    out += [(kind, p, False) for p, of, kind in g.relations if of == pid]
    return out


def kind_words(kind: str) -> set[str]:
    """'best friend' is found by 'bestfriend' and 'friend'."""
    return {compact(kind), compact(kind.split()[-1])}


def word_index(con: sqlite3.Connection, g: Graph | None = None) -> dict[str, set[int]]:
    """Search word -> the people it means: relatives of "me" by what they are to them, everyone with another
    relationship (anybody's friend, coworker...), and people by their other names."""
    g = g or load(con)
    index: dict[str, set[int]] = defaultdict(set)
    ego = me(con)
    if ego:
        for pid, kin in relations_to(g, ego).items():
            for word in kin.words:
                index[word].add(pid)
    for p, _, kind in g.relations:
        for word in kind_words(kind):
            index[word].add(p)
    # other names ("emma carter", "em" find Emma Miller's photos) and groups ("teachers")
    for p, name, is_group in con.execute("""SELECT person_id, name, 0 FROM person_name
                                            UNION ALL SELECT person_id, name, 1 FROM person_group"""):
        words = re.findall(r"\w+", name.lower())
        for word in words:
            index[word].add(p)
        if len(words) == 2:
            index["".join(words)].add(p)
        if is_group and len(words) == 1 and words[0].endswith("s"):
            index[words[0][:-1]].add(p)  # "teacher" as well as "teachers"
    return dict(index)


# ---- search words --------------------------------------------------------------------------------------------------

_BASE = {"father", "mother", "parent", "son", "daughter", "child", "brother", "sister", "sibling", "husband", "wife",
         "spouse", "partner", "boyfriend", "girlfriend",
         "grandfather", "grandmother", "grandparent", "grandson", "granddaughter", "grandchild",
         "nephew", "niece", "aunt", "uncle", "cousin", "family", "relative"}
_PREFIXES = ("ex", "step", "half", "great", "grand", "former", "adopted", "adoptive", "foster", "birth")
_SYNONYMS = {"mom": "mother", "mum": "mother", "mommy": "mother", "mama": "mother", "dad": "father", "daddy": "father",
             "grandma": "grandmother", "granny": "grandmother", "nana": "grandmother", "grandpa": "grandfather",
             "granddad": "grandfather", "kid": "child", "kids": "child", "children": "child", "grandkid": "grandchild",
             "grandkids": "grandchild", "grandchildren": "grandchild", "stepchildren": "stepchild", "wives": "wife",
             "neighbour": "neighbor", "relatives": "relative", "inlaws": "inlaw", "stepfamily": "step",
             "formerfamily": "former", "exfamily": "former"}


def search_terms(text: str, known: set[str] = frozenset()) -> list[tuple[str, str]]:
    """The words of a search as (relationship key, as typed): 'my brothers-in-law 2024' ->
    [('brotherinlaw', 'brothers in law'), ('2024', '2024')]. known: the relationship words in use (e.g. 'bestfriend')."""
    words = re.findall(r"\w+", re.sub(r"['’]s\b", "", text.lower()))

    def key(word: str) -> str:
        if word in _SYNONYMS:
            return _SYNONYMS[word]
        if word.endswith("s") and (word[:-1] in _BASE or word[:-1] in known or word[:-1] in _PREFIXES):
            return word[:-1]
        return word

    out, i = [], 0
    while i < len(words):
        j, prefix = i, ""
        while j < len(words) - 1 and words[j] in _PREFIXES:
            prefix += words[j]
            j += 1
        base = key(words[j])
        if prefix and base not in _BASE:  # "great view", "ex libris": just words
            j, prefix, base = i, "", key(words[i])
        k = j + 1
        if base in _BASE and words[k:k + 2] in (["in", "law"], ["in", "laws"]):
            base, k = base + "inlaw", k + 2
        elif base == "in" and words[k:k + 1] in (["law"], ["laws"]):
            base, k = "inlaw", k + 1
        elif k < len(words) and base + key(words[k]) in known:  # "best friend"
            base, k = base + key(words[k]), k + 1
        out.append((_SYNONYMS.get(prefix + base, prefix + base), " ".join(words[i:k])))
        i = k
    if any(k in known or k in _BASE or k.endswith("inlaw") or k.startswith(_PREFIXES) and k != typed
           for k, typed in out):
        out = [(k, typed) for k, typed in out if k not in ("my", "our", "me", "the")]
    return out


# ---- changes -------------------------------------------------------------------------------------------------------

class Problem(errors.Problem):
    """A change that cannot be made; the message is shown to the person who asked for it."""


def set_sex(con: sqlite3.Connection, pid: int, sex: str | None) -> None:
    if sex not in (*SEXES, None):
        raise Problem("Unknown sex.")
    con.execute("UPDATE person SET sex = ? WHERE id = ?", (sex, pid))


def link(con: sqlite3.Connection, pid: int, word: str, other: int, with_parent: int | None = None,
         shown: set[int] | None = None) -> None:
    """Record that `other` is pid's <word>: 'mother', 'son', 'ex-wife', or another relationship such as 'coworker'.
    The checks look at the whole family, but a refusal names only the people in `shown` (None: anyone), those the
    person making the change can see."""
    word = _as_offered(" ".join(word.lower().split()))
    if other == pid:
        raise Problem("Someone cannot be their own relative.")
    if word in FAMILY_WORDS:
        kind, sex = FAMILY_WORDS[word]
        if sex:
            con.execute("UPDATE person SET sex = ? WHERE id = ? AND sex IS NULL", (sex, other))
        how = PEDI_OF_WORD.get(word.split()[0])  # "adopted son": adopted; "son": by birth, or as entered before
        if kind == "parent":
            add_parent(con, child=pid, parent=other, shown=shown, pedi=how)
        elif kind == "child":
            add_parent(con, child=other, parent=pid, shown=shown, pedi=how)
            if with_parent and with_parent not in (pid, other):
                add_parent(con, child=other, parent=with_parent, shown=shown, pedi=how)
        elif kind == "sibling":
            add_sibling(con, pid, other)
        else:
            add_partner(con, pid, other, current=kind == "partner", bond=BONDS[word.removeprefix("ex-")],
                        shown=shown)
    elif word:
        if len(word) > 40:
            raise Problem("That relationship is too long; a word or two, like coworker.")
        _not_worked_out(word)
        con.execute("INSERT OR IGNORE INTO relation(person_id, of_id, kind) VALUES (?, ?, ?)", (other, pid, word))
    else:
        raise Problem("Say what they are to each other.")
    con.commit()


def _as_offered(word: str) -> str:
    """A word typed by hand as the page offers it: "ex wife" and "former girlfriend" are "ex-wife" and
    "ex-girlfriend", so they make a couple who were, rather than a label of their own."""
    for prefix in ("ex ", "ex-", "former "):
        if word.startswith(prefix) and "ex-" + word[len(prefix):] in FAMILY_WORDS:
            return "ex-" + word[len(prefix):]
    return word


def _not_worked_out(word: str) -> None:
    """Family words that are worked out cannot be entered as another relationship ("grandmother" would then be just
    a label, like "coworker", and the family would not know of it). Say what to enter instead."""
    w = compact(word)
    if w.startswith(("grand", "great")):
        raise Problem(f"{word.capitalize()} is worked out: add them as the parent of the mother or father instead.")
    if w.startswith("step"):
        raise Problem(f"{word.capitalize()} is worked out from a current partner's children: add the partner and "
                      f"the children's parents instead.")
    if w.endswith("inlaw") or w in ("aunt", "uncle", "niece", "nephew", "cousin") or w.startswith("half"):
        raise Problem(f"{word.capitalize()} is worked out from parents, siblings and partners: enter those instead.")


def _name(con, pid: int) -> str:
    return (con.execute("SELECT name FROM person WHERE id = ?", (pid,)).fetchone() or ("?",))[0]


def add_parent(con, child: int, parent: int, shown: set[int] | None = None, pedi: str | None = None) -> None:
    """parent is child's parent: by birth, adopted or foster (pedi). pedi None: by birth, or a link already entered
    stays as it is; given, it changes one already entered. Refused when it would make someone their own ancestor:
    when the child, or a brother or sister entered as such (who shares the child's parents, Graph), is the parent or
    the parent's ancestor already. Doesn't commit."""
    def name(pid):
        return _name(con, pid)

    if pedi not in (None, *PEDI):
        raise Problem("Unknown kind of parent.")
    if _is_ancestor(con, child, parent):
        raise Problem(f"{name(child)} is already {name(parent)}'s ancestor.")
    siblings = _siblings_of(con, child)
    if parent in siblings:
        raise Problem(f"{name(parent)} is entered as {name(child)}'s brother or sister: remove that first.")
    if any(_is_ancestor(con, s, parent) for s in siblings):
        raise Problem(f"A brother or sister of {name(child)} is already {name(parent)}'s ancestor.")
    kind = pedi or (con.execute("SELECT pedi FROM parent WHERE child_id = ? AND parent_id = ?", (child, parent))
                    .fetchone() or ("birth",))[0]
    _room_for(con, child, parent, kind, shown, name)
    con.execute("""INSERT INTO parent(child_id, parent_id, pedi) VALUES (?, ?, ?)
                   ON CONFLICT(child_id, parent_id) DO UPDATE SET pedi = excluded.pedi""", (child, parent, kind))


def _room_for(con, child: int, parent: int, kind: str, shown, name) -> None:
    """Two birth parents at most, and two adoptive ones: refused with what to do about it."""
    if kind not in TWO_EACH:
        return
    others = {r[0] for r in con.execute("""SELECT parent_id FROM parent WHERE child_id = ? AND parent_id != ?
                                            AND pedi = ?""", (child, parent, kind))}
    if len(others) >= 2:
        which = "parents" if kind == "birth" else "adoptive parents"
        if shown is not None and not others <= shown:
            raise Problem(f"{name(child)}'s two {which} are already entered. An admin can change that.")
        raise Problem(f"{name(child)} already has two {which}. Remove one first"
                      + (", or add this one as an adoptive or foster parent." if kind == "birth" else "."))


def set_pedi(con: sqlite3.Connection, child: int, parent: int, pedi: str, shown: set[int] | None = None) -> None:
    """Change how a child belongs to a parent already entered: by birth, adopted, or foster."""
    if pedi not in PEDI:
        raise Problem("Unknown kind of parent.")
    if not con.execute("SELECT 1 FROM parent WHERE child_id = ? AND parent_id = ?", (child, parent)).fetchone():
        raise Problem("They aren't entered as parent and child.")
    _room_for(con, child, parent, pedi, shown, lambda pid: _name(con, pid))
    con.execute("UPDATE parent SET pedi = ? WHERE child_id = ? AND parent_id = ?", (pedi, child, parent))
    con.commit()


def _is_ancestor(con, who: int, of: int) -> bool:
    """Whether `who` is `of` or one of their ancestors, going up the parents as Graph does (siblings with no parents
    entered have their siblings'). Only those ancestors are looked up, so a GEDCOM import's thousands of links each
    take a moment, not a load of the whole family."""
    seen, todo = {of}, [of]
    while todo:
        p = todo.pop()
        if p == who:
            return True
        parents = _parents_of(con, p)
        todo += parents - seen
        seen |= parents
    return False


def _parents_of(con, pid: int) -> set[int]:
    """pid's parents as Graph has them: those entered, or else their brothers' and sisters'."""
    parents = {r[0] for r in con.execute("SELECT parent_id FROM parent WHERE child_id = ?", (pid,))}
    if parents:
        return parents
    group = _siblings_of(con, pid) | {pid}
    return {r[0] for r in con.execute("SELECT parent_id FROM parent WHERE child_id IN (SELECT value FROM json_each(?))",
                                      (json.dumps(sorted(group)),))}


def _siblings_of(con, pid: int) -> set[int]:
    """Everyone linked to pid by sibling links, through each other."""
    group, todo = {pid}, [pid]
    while todo:
        p = todo.pop()
        for (q,) in con.execute("SELECT CASE a WHEN ? THEN b ELSE a END FROM sibling WHERE ? IN (a, b)", (p, p)):
            if q not in group:
                group.add(q)
                todo.append(q)
    return group - {pid}


def add_sibling(con, a: int, b: int) -> bool:
    """a and b are brothers or sisters, entered as such (for when their parents aren't: they share those of either,
    Graph). Refused when that would make someone their own ancestor: one of them, or a brother or sister of theirs,
    an ancestor of a parent they would share. Returns whether it is new. Doesn't commit."""
    if a == b:
        raise Problem("Someone cannot be their own relative.")
    for x, y in ((a, b), (b, a)):
        if _is_ancestor(con, x, y):
            raise Problem(f"{_name(con, x)} is already {_name(con, y)}'s ancestor.")
    group = {a, b} | _siblings_of(con, a) | _siblings_of(con, b)
    shared = {r[0] for r in con.execute("""SELECT parent_id FROM parent
                                           WHERE child_id IN (SELECT value FROM json_each(?))""",
                                        (json.dumps(sorted(group)),))}
    if any(_is_ancestor(con, x, q) for x in group for q in shared):
        raise Problem(f"{_name(con, a)} and {_name(con, b)} can't be brothers or sisters: one of their brothers or "
                      f"sisters would be an ancestor of a parent they share.")
    return bool(con.execute("INSERT OR IGNORE INTO sibling(a, b) VALUES (?, ?)", (min(a, b), max(a, b))).rowcount)


def add_partner(con, a: int, b: int, current: bool, bond: str, shown: set[int] | None = None, replace: bool = True,
                uid: str | None = None) -> None:
    """a and b are a couple: married, partners or going out together (bond, one of BONDS' kinds), now or before; a
    current couple only when neither has another current partner living. replace: a couple entered already becomes
    this one, else stays as it was. uid: the couple's GEDCOM UID, when they come with one. Doesn't commit."""
    if current:
        _no_other_current_partner(con, a, b, shown)
        _no_other_current_partner(con, b, a, shown)
    keep = ("DO UPDATE SET current = excluded.current, married = excluded.married, dating = excluded.dating"
            if replace else "DO NOTHING")
    con.execute(f"""INSERT INTO partner(a, b, current, married, dating, uid) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(a, b) {keep}""",
                (min(a, b), max(a, b), int(current), int(bond == "married"), int(bond == "dating"),
                 uid or str(uuid.uuid4())))


def change_current(con, a: int, b: int, current: bool, shown: set[int] | None = None) -> None:
    """A couple are together now, or were (divorced, separated, broken up). Doesn't commit."""
    if current:
        _no_other_current_partner(con, a, b, shown)
        _no_other_current_partner(con, b, a, shown)
    con.execute("UPDATE partner SET current = ? WHERE a = ? AND b = ?", (int(current), min(a, b), max(a, b)))


def _no_other_current_partner(con, pid: int, new: int, shown: set[int] | None = None) -> None:
    row = con.execute("""SELECT p.id, p.name FROM partner r
                         JOIN person p ON p.id = (CASE r.a WHEN ? THEN r.b ELSE r.a END)
                         WHERE ? IN (r.a, r.b) AND r.current AND p.id != ? AND NOT p.deceased""",
                      (pid, pid, new)).fetchone()
    if row:
        name = con.execute("SELECT name FROM person WHERE id = ?", (pid,)).fetchone()[0]
        if shown is not None and row[0] not in shown:
            raise Problem(f"{name} already has a current partner. Add this one as an ex, or ask an admin to mark "
                          f"that one as former.")
        raise Problem(f"{name}'s current partner is {row[1]}. Mark that one as former first (on {name}'s page), "
                      f"or add this one as an ex.")


def unlink(con: sqlite3.Connection, kind: str, a: int, b: int, what: str = "") -> None:
    """Remove one fact: kind parent (a is the child), sibling, partner or relation (a is the 'what' of b)."""
    if kind == "parent":
        con.execute("DELETE FROM parent WHERE child_id = ? AND parent_id = ?", (a, b))
    elif kind == "sibling":
        con.execute("DELETE FROM sibling WHERE a = ? AND b = ?", (min(a, b), max(a, b)))
    elif kind == "partner":
        con.execute("DELETE FROM partner WHERE a = ? AND b = ?", (min(a, b), max(a, b)))
    elif kind == "relation":
        con.execute("DELETE FROM relation WHERE person_id = ? AND of_id = ? AND kind = ?", (a, b, what))
    else:
        raise Problem("Unknown kind of link.")
    con.commit()


def set_current(con: sqlite3.Connection, a: int, b: int, current: bool, shown: set[int] | None = None) -> None:
    change_current(con, a, b, current, shown)
    con.commit()


def merge(con: sqlite3.Connection, old: int, new: int) -> None:
    """Move old's family links to new before old is deleted (two people renamed into one)."""
    con.execute("UPDATE OR IGNORE parent SET child_id = ? WHERE child_id = ?", (new, old))
    con.execute("UPDATE OR IGNORE parent SET parent_id = ? WHERE parent_id = ?", (new, old))
    con.execute("DELETE FROM parent WHERE child_id = parent_id")
    for table in ("sibling", "partner"):
        for row in con.execute(f"SELECT * FROM {table} WHERE ? IN (a, b)", (old,)).fetchall():
            other = row["b"] if row["a"] == old else row["a"]
            if other != new:
                values = {**dict(row), "a": min(new, other), "b": max(new, other)}
                con.execute(f"INSERT OR IGNORE INTO {table}({','.join(values)}) VALUES ({','.join('?' * len(values))})",
                            tuple(values.values()))
                if table == "partner":  # the couple's marriage and the like go with it
                    events.move_couple(con, (row["a"], row["b"]), (new, other))
    con.execute("UPDATE OR IGNORE relation SET person_id = ? WHERE person_id = ?", (new, old))
    con.execute("UPDATE OR IGNORE relation SET of_id = ? WHERE of_id = ?", (new, old))
    con.execute("DELETE FROM relation WHERE person_id = of_id")
    # the stored "This is me", not me(con), which is whoever is signed in
    con.execute("UPDATE settings SET value = ? WHERE key = 'me' AND value = ?", (str(new), str(old)))
    # Two people who are one: but not if that makes someone their own ancestor, or a parent their child's brother
    looped = any(_is_ancestor(con, new, q) for q in _parents_of(con, new))
    if looped or con.execute("""SELECT 1 FROM sibling s JOIN parent p ON p.child_id IN (s.a, s.b)
                                                                  AND p.parent_id IN (s.a, s.b)
                                WHERE :n IN (s.a, s.b)""", {"n": new}).fetchone():
        names = [r[0] for r in con.execute("SELECT name FROM person WHERE id IN (?, ?) ORDER BY id", (old, new))]
        raise Problem(f"{' and '.join(names)} can't be one person: one is the other's ancestor, or their brother or "
                      f"sister, in the family tree.")


def suggested_sex(con: sqlite3.Connection, pid: int) -> str | None:
    """What the face analysis says, when nearly all of this person's faces agree. It is unreliable for children,
    whose faces split (one boy's were 126 male to 209 female), so then it says nothing."""
    m, f = con.execute("""SELECT coalesce(sum(est_sex = 'M'), 0), coalesce(sum(est_sex = 'F'), 0) FROM face
                          WHERE person_id = ? AND assigned_by = 'user'""", (pid,)).fetchone()
    if m + f >= 10 and max(m, f) >= 0.9 * (m + f):
        return "male" if m > f else "female"
    return None


# ---- the family tree -----------------------------------------------------------------------------------------------

def tree(g: Graph, root: int, depth: int = 8, _seen=None) -> dict:
    """root and their descendants: {'id', 'unions': [{'partner', 'current', 'children': [tree...]}]}. Children are
    grouped by their other parent, so a child of a former marriage sits under that marriage. A negative root stands
    for the parents (not entered) of siblings."""
    seen = _seen if _seen is not None else set()
    seen.add(root)
    kids = set(g.effective_children.get(root, ())) - seen
    unions = []
    own = set(g.effective_children.get(root, ()))
    for partner, (current, _) in sorted(g.partners.get(root, {}).items(), key=lambda kv: (not kv[1][0], kv[0])):
        together = {k for k in kids if partner in g.effective_parents.get(k, ())}
        # a current partner's children from other relationships, shown as root's stepchildren
        step = {k for k in g.effective_children.get(partner, ()) if k > 0} - own - seen if current else set()
        unions.append({"partner": partner, "current": current, "children": together, "step": step})
        kids -= together
    by_other = defaultdict(set)  # children with another parent who is not a partner, or with only this parent
    for k in kids:
        others = g.effective_parents.get(k, set()) - {root}
        by_other[min(others) if others else None].add(k)
    for other, together in sorted(by_other.items(), key=lambda kv: (kv[0] is None, kv[0] or 0)):
        unions.append({"partner": other, "current": None, "children": together, "step": set()})
    birth = lambda k: (g.people[k]["birth_date"] or "9999", g.name(k))
    for u in unions:
        for key in ("children", "step"):
            u[key] = [{**(tree(g, k, depth - 1, seen) if depth > 1 else {"id": k, "unions": []}),
                       "how": g.how(k, root) if key == "children" else "birth"}  # adopted, foster: marked
                      for k in sorted(u[key], key=birth) if k not in seen]
    return {"id": root, "unions": unions}


def view(g: Graph, center: int, ego: int | None = None) -> dict:
    """The family around one person, for the Family page, instead of everyone at once: their grandparents and parents
    above, their own generation (partners, brothers and sisters), and below, their children and their brothers' and
    sisters' children. With how many generations go on above and below, to climb to by making someone else the
    center, and the way there from ego (ego, their mother, her father...) to climb back. Stand-in parents (siblings
    with none entered) are left out."""
    birth = lambda p: (g.people[p]["birth_date"] or "9999", g.name(p))
    real = lambda ids: sorted((p for p in ids if p > 0 and p in g.people), key=birth)
    parents = real(g.effective_parents.get(center, ()))
    siblings = real(g.all_siblings(center))
    ancestors = [up for a, up in g.ancestors.get(center, {}).items() if a > 0]
    grandparents = [(p, real(g.effective_parents.get(p, ()))) for p in parents]
    children = [(p, real(g.effective_children.get(p, ()))) for p in [center, *siblings]]
    return {
        "center": center,
        "grandparents": grandparents,
        "parents": parents,
        "partners": [(p, current) for p, (current, _) in
                     sorted(g.partners.get(center, {}).items(), key=lambda kv: (not kv[1][0], birth(kv[0])))],
        "older": [s for s in siblings if birth(s) < birth(center)],
        "younger": [s for s in siblings if birth(s) >= birth(center)],
        "children": children,
        "above": max(0, max(ancestors, default=0) - 2),  # generations beyond the grandparents
        "below": max(0, _generations_down(g, center) - 1),  # beyond the children
        # the edges of the view that go on: grandparents with parents, children with children
        "up_from": {gp for _, gps in grandparents for gp in gps if real(g.effective_parents.get(gp, ()))},
        "down_from": {c for _, kids in children for c in kids if real(g.effective_children.get(c, ()))},
        "path": _way(g, ego, center) if ego is not None else [],
    }


def _generations_down(g: Graph, pid: int) -> int:
    """How many generations of descendants someone has (0: no children)."""
    level, n, seen = {pid}, 0, {pid}
    while level := {c for p in level for c in g.effective_children.get(p, ()) if c > 0 and c not in seen}:
        seen |= level
        n += 1
    return n


def _way(g: Graph, start: int, end: int) -> list[int]:
    """The shortest way from one person to another through parents, children and partners, both ends included; [] if
    they are not related."""
    back, todo = {start: None}, deque([start])
    while todo and end not in back:
        p = todo.popleft()
        for q in (*g.effective_parents.get(p, ()), *g.effective_children.get(p, ()), *g.partners.get(p, {})):
            if q > 0 and q not in back:
                back[q] = p
                todo.append(q)
    if end not in back:
        return []
    out = [end]
    while out[-1] != start:
        out.append(back[out[-1]])
    return out[::-1]


def tops(g: Graph) -> list[tuple[int, int]]:
    """The tops of trees (no parents entered, or the stand-in parents of siblings) that have descendants, with how
    many: the choice of trees on the Family page."""
    out = []
    for p in {*g.people, *g.effective_children}:
        if not g.effective_parents.get(p) and g.effective_children.get(p):
            out.append((p, sum(1 for q, up in g.ancestors.items() if p in up and q != p)))
    keep = []  # a couple at the top is one tree, married or not
    for p, n in sorted(out, key=lambda t: (-t[1], t[0])):
        if not any(q in g.partners.get(p, {}) or q in _co_parents(g, p) for q, _ in keep):
            keep.append((p, n))
    return keep


def _co_parents(g: Graph, p: int) -> set[int]:
    return {q for c in g.effective_children.get(p, ()) for q in g.effective_parents.get(c, ()) if q != p}


def top_name(g: Graph, top: int) -> str:
    """How a tree is listed: 'Grandpa & Grandma', or 'Parents of Emma, Jake and Tom' for stand-in parents."""
    if top < 0:
        kids = sorted((g.name(k) for k in g.effective_children.get(top, ())), key=str.lower)
        return "Parents of " + (", ".join(kids[:-1]) + " and " + kids[-1] if len(kids) > 1 else "".join(kids))
    partners = [q for q, (current, _) in g.partners.get(top, {}).items() if current]
    partners = partners or sorted(q for q in _co_parents(g, top) if q > 0 and not g.effective_parents.get(q))
    return " & ".join([g.name(top), *(g.name(q) for q in partners[:1])])


def tree_root(g: Graph, pid: int, below: Counter | None = None) -> int:
    """The top of the tree pid is best seen in: their highest ancestor with the most descendants. below: how many
    people each one is an ancestor of (themselves included), counted once for many calls."""
    up = g.ancestors.get(pid, {pid: 0})
    candidates = [a for a in up if not g.effective_parents.get(a)]
    count = below.__getitem__ if below is not None else lambda a: sum(1 for ups in g.ancestors.values() if a in ups)
    return max(candidates, key=lambda a: (up[a], count(a), -a), default=pid)


def lines(g: Graph, t: dict) -> dict[int, int]:
    """Where a click on each person in a tree (as tree() makes it) leads on the whole line's page: the top of the line
    they come from. Its top and those descended from it stay in this tree; someone who married in, or a stepchild,
    leads to the top of the family they come from, unless that is in this tree already (none of their parents
    entered)."""
    shown, todo = set(), [t]
    while todo:
        n = todo.pop()
        shown.add(n["id"])
        for u in n["unions"]:
            shown.add(u["partner"])
            todo += [*u["children"], *u["step"]]
    root = t["id"]
    below = Counter(a for ups in g.ancestors.values() for a in ups)
    out = {}
    for p in shown:
        if p is None or p not in g.people:  # no other parent entered; a stand-in for siblings' parents
            continue
        top = root if root in g.ancestors.get(p, ()) else tree_root(g, p, below)
        out[p] = root if top in shown else top
    return out


# ---- everyone connected, on one chart (the whole line's page, "Everyone connected") ---------------------------------
# Every family linked to one person by parents, children and partners, each person once: a row for each generation,
# partners side by side, and lines from each couple down to their children. Laid out here, in px, for the page to
# place as it is (family_tree.html, app.css .tree-sheet.all): the cards have one size there.

CARD_W, CARD_H = 104, 140  # a card
PARTNER_GAP = 14           # between partners side by side
GAP = 30                   # between anyone else in a row
ROW = 220                  # from one generation's row to the next
COUPLE_Y = 40              # down a card, where the line between partners runs (across their faces)
LANE = 8                   # between the bars of two families over one row, where they would run into each other
EDGE = 24                  # around the chart
SWEEPS = 16                # passes ordering the rows (fewer lines crossing)
SETTLE = 40                # passes placing them (each under its parents, over its children)
BLOOD_WEIGHT = 3           # how much more a tie of focus's own blood counts in placing them than one married in


def connected(g: Graph, start: int) -> set[int]:
    """Everyone linked to start by parents, children and partners, the stand-in parents of siblings included."""
    seen, todo = {start}, [start]
    while todo:
        p = todo.pop()
        for q in (*g.effective_parents.get(p, ()), *g.effective_children.get(p, ()), *g.partners.get(p, {})):
            if q not in seen and (q in g.people or q < 0):
                seen.add(q)
                todo.append(q)
    return seen


def charts(g: Graph) -> list[tuple[int, str, int]]:
    """The families that aren't linked to one another, each a chart: (its top with the most below, how it is named,
    how many people), the largest first. The choice on the page."""
    out, seen = [], set()
    for top, _ in tops(g):
        if top in seen:
            continue
        people = connected(g, top)
        seen |= people
        out.append((top, top_name(g, top), sum(1 for p in people if p > 0)))
    return sorted(out, key=lambda c: -c[2])


def roots(g: Graph, focus: int) -> set[int]:
    """Who is on focus's Roots chart, a trimmed Everyone connected: everyone linked to focus on their generation and
    above (their ancestors and their partner's, and every branch of those families), the descendants of all of those,
    and below focus's generation the partners of those descendants, but not the partners' own parents and families.
    From Russell's roots, his daughter's husband is there; the husband's parents and their line aren't."""
    full = connected(g, focus)
    gen = _generations(g, full, focus)
    upper, todo = {focus}, [focus]
    while todo:  # linked through focus's generation and those above it only
        p = todo.pop()
        for q in (*g.effective_parents.get(p, ()), *g.effective_children.get(p, ()), *g.partners.get(p, {})):
            if q in full and q not in upper and gen[q] <= gen[focus]:
                upper.add(q)
                todo.append(q)
    keep, todo = set(upper), list(upper)
    while todo:  # and their descendants: children born to them, not a partner's from elsewhere
        p = todo.pop()
        for k in g.effective_children.get(p, ()):
            if k in full and k not in keep:
                keep.add(k)
                todo.append(k)
    for p in keep - upper:  # the partners of those below, on their own
        keep |= {q for q in g.partners.get(p, {}) if q in full}
    return keep


def chart(g: Graph, start: int, focus: int | None = None, among: set[int] | None = None) -> dict:
    """Everyone connected to start (or only those among, as roots() picks them), laid out: {"nodes": [{"id", "x", "y",
    "direct", "how"}], "lines": [{"x", "y", "w", "h", "dashed", "faint"}], "width", "height", "people"}. x and y are a
    card's top left corner, in px; a line is a box of one side, across (h 0) or down (w 0). direct: focus's own blood
    (someone sharing an ancestor with them, or descended from them), drawn bold as the line is on the one line's chart;
    focus is start when not given."""
    people = connected(g, start) if among is None else among
    gen = _generations(g, people, start)
    blood = _blood(g, people, focus if focus in people else start)
    birth = lambda p: (g.people[p]["birth_date"] or "9999", g.name(p)) if p in g.people else ("9999", "")
    parents = {p: sorted(q for q in g.effective_parents.get(p, ()) if q in people) for p in people}
    families = defaultdict(list)  # the parents in the chart -> their children, eldest first
    for p in sorted(people, key=birth):
        if parents[p]:
            families[tuple(parents[p])].append(p)

    # a row's units: people side by side, a person with their partners and with whoever they had children with
    beside = defaultdict(set)
    for p in people:
        beside[p] |= {q for q in g.partners.get(p, {}) if q in people and gen[q] == gen[p]}
    for ps in families:
        for a in ps:
            beside[a] |= {b for b in ps if b != a and gen[b] == gen[a]}
    units, unit_of = [], {}
    for p in sorted(people, key=lambda p: (gen[p], birth(p))):
        if p in unit_of:
            continue
        group, todo = set(), [p]
        while todo:
            q = todo.pop()
            if q not in group:
                group.add(q)
                todo += beside[q] - group
        row = _side_by_side(g, group, beside)
        for q in row:
            unit_of[q] = len(units)
        units.append(row)
    rows = defaultdict(list)
    for u, row in enumerate(units):
        rows[gen[row[0]]].append(u)
    width = [len(row) * CARD_W + (len(row) - 1) * PARTNER_GAP for row in units]
    offset = {p: i * (CARD_W + PARTNER_GAP) for row in units for i, p in enumerate(row)}  # within its unit
    above = {u: {q for p in units[u] for q in parents[p]} for u in range(len(units))}
    below = {u: {k for ps, kids in families.items() if set(ps) & set(units[u]) for k in kids} for u in range(len(units))}

    # the order in each row: families together, from the top down, then fewer crossings (barycenters, both ways)
    order, done = {r: [] for r in rows}, set()

    def visit(u: int) -> None:
        done.add(u)
        order[gen[units[u][0]]].append(u)
        for k in sorted(below[u], key=birth):
            if unit_of[k] not in done:
                visit(unit_of[k])

    for r in sorted(rows):
        for u in rows[r]:
            if u not in done:
                visit(u)
    x = {}

    def pack(r: int) -> None:
        at = 0
        for u in order[r]:
            x[u] = at
            at += width[u] + GAP

    center = lambda p: x[unit_of[p]] + offset[p] + CARD_W / 2
    for r in order:
        pack(r)
    # brothers and sisters stay together, eldest first, as a block under their parents; one who married into another
    # family on the chart stands at the end of the block nearer that family. A couple who both have parents on it goes
    # with focus's own blood: their family stays whole, and the line of the family the other married in from reaches
    # over to them. Going down, a row's blocks follow their parents; going up, whole blocks follow their children.
    home, pull = {}, {}
    for u, row in enumerate(units):
        theirs = [tuple(parents[p]) for p in sorted(row, key=lambda p: p not in blood) if parents[p]]
        home[u] = theirs[0] if theirs else ("alone", u)
        pull[u] = [q for f in theirs[1:] for q in f]
    mean = lambda xs: sum(xs) / len(xs)
    rank = {}  # each one's place along their row, 0 to 1: rows of any length compared alike

    def ranks(r: int) -> None:
        n = max(1, len(order[r]) - 1)
        for i, u in enumerate(order[r]):
            for j, p in enumerate(units[u]):
                rank[p] = (i + j / (len(units[u]) + 1)) / n

    for r in order:
        ranks(r)
    for sweep in range(SWEEPS):
        for r in sorted(order, reverse=sweep % 2 == 1):
            if sweep % 2 == 0:
                spot = {u: mean([rank[q] for q in home[u]]) if home[u][0] != "alone" else rank[units[u][0]]
                        for u in order[r]}
                lean = {u: mean([rank[q] for q in pull[u]]) - spot[u] if pull[u] else 0 for u in order[r]}
                order[r].sort(key=lambda u: (spot[u], str(home[u]), (lean[u] > 0) - (lean[u] < 0), lean[u],
                                             min(birth(p) for p in units[u])))
            else:
                blocks = defaultdict(list)
                for u in order[r]:
                    blocks[home[u]].append(u)
                kids = lambda us: [rank[k] for u in us for k in below[u]]
                order[r] = [u for us in sorted(blocks.values(), key=lambda us: mean(kids(us)) if kids(us)
                                               else mean([rank[p] for u in us for p in units[u]])) for u in us]
            ranks(r)
    for r in order:
        pack(r)

    # where each goes in its row: as near as the row allows to under its parents and over its children
    for sweep in range(SETTLE):
        for r in sorted(order, reverse=sweep % 2 == 1):
            wanted = []
            for u in order[r]:  # (where it wants to be, how much): the ties of focus's blood count most
                wants = [(sum(center(q) for q in parents[p]) / len(parents[p]) - offset[p] - CARD_W / 2,
                          BLOOD_WEIGHT if p in blood else 1) for p in units[u] if parents[p]]
                for ps, kids in families.items():
                    mine = [p for p in ps if unit_of[p] == u]
                    if mine:
                        middle = sum(offset[p] for p in mine) / len(mine) + CARD_W / 2
                        wants.append((sum(center(k) for k in kids) / len(kids) - middle,
                                      BLOOD_WEIGHT if any(k in blood for k in kids) else 1))
                wanted.append(sum(a * w for a, w in wants) / sum(w for _, w in wants) if wants else x[u])
            for u, left in zip(order[r], _in_order(wanted, [width[u] for u in order[r]])):
                x[u] = left
    least = min(x.values(), default=0)
    top = min(order, default=0)
    place = {p: (x[unit_of[p]] + offset[p] - least + EDGE, (gen[p] - top) * ROW + EDGE) for p in people}

    lines = []  # faint: the lines of families focus's blood isn't in (they reach across the chart to where their
    # children married in), drawn lighter so focus's own family stands out
    across = lambda x1, x2, y, dashed=False, faint=False: lines.append(
        {"x": round(min(x1, x2)), "y": round(y), "w": round(abs(x2 - x1)), "h": 0, "dashed": dashed, "faint": faint})
    down = lambda x0, y1, y2, faint=False: lines.append(
        {"x": round(x0), "y": round(min(y1, y2)), "w": 0, "h": round(abs(y2 - y1)), "dashed": False, "faint": faint})
    couples = {tuple(sorted((a, b))) for a in people for b in g.partners.get(a, {}) if b in people}
    couples |= {ps for ps in families if len(ps) == 2}
    for a, b in sorted(couples):
        (ax, ay), (bx, by) = place[a], place[b]
        if ay == by:  # side by side: from one card's edge to the other's, across their faces
            current = g.partners.get(a, {}).get(b, (False,))[0]
            across(min(ax, bx) + CARD_W, max(ax, bx), ay + COUPLE_Y, dashed=not current,
                   faint=a not in blood and b not in blood)
    bars = defaultdict(list)  # a row's families' bars, to keep apart: [(left, right)] in each lane
    for ps, kids in sorted(families.items(), key=lambda f: min(place[k][0] for k in f[1])):
        tops_ = [place[p] for p in ps]
        lowest = max(y for _, y in tops_)
        if len(ps) == 2 and tops_[0][1] == tops_[1][1]:  # from the middle of the line between them
            fx = (min(t[0] for t in tops_) + CARD_W + max(t[0] for t in tops_)) / 2
            fy = lowest + COUPLE_Y
        else:  # from under the one parent (or the lower of two)
            fx = sum(t[0] for t in tops_) / len(tops_) + CARD_W / 2
            fy = lowest + CARD_H
        faint = not any(k in blood for k in kids)
        for row_y in sorted({place[k][1] for k in kids}):
            these = [place[k][0] + CARD_W / 2 for k in kids if place[k][1] == row_y]
            left, right = min(these + [fx]), max(these + [fx])
            lanes = bars[row_y]
            lane = next((i for i, spans in enumerate(lanes) if all(right < a - 6 or left > b + 6 for a, b in spans)),
                        len(lanes))
            if lane == len(lanes):
                lanes.append([])
            lanes[lane].append((left, right))
            bar = row_y - 14 - LANE * min(lane, (ROW - CARD_H - 24) // LANE)
            down(fx, fy, bar, faint)
            if right > left:
                across(left, right, bar, faint=faint)
            for cx in these:
                down(cx, bar, row_y, faint)
    nodes = [{"id": p, "x": round(place[p][0]), "y": round(place[p][1]), "direct": p in blood,
              "how": next((g.how(p, q) for q in parents[p] if g.how(p, q) != "birth"), "birth")}
             for p in sorted(people, key=lambda p: place[p])]
    return {"nodes": nodes, "lines": lines, "people": sum(1 for p in people if p > 0),
            "width": round(max((place[p][0] for p in people), default=0) + CARD_W + EDGE),
            "height": round(max((place[p][1] for p in people), default=0) + CARD_H + EDGE)}


def _generations(g: Graph, people: set[int], start: int) -> dict[int, int]:
    """Each one's row, 0 the top: a parent's is above their child's, a partner's the same; reached from start, partners
    first (a step that doesn't change the row goes to the front), so a couple stays on one row where the family allows."""
    gen, todo = {start: 0}, deque([start])
    while todo:
        p = todo.popleft()
        steps = ([(q, 0) for q in g.partners.get(p, {})] + [(q, -1) for q in g.effective_parents.get(p, ())]
                 + [(q, 1) for q in g.effective_children.get(p, ())])
        for q, d in steps:
            if q in people and q not in gen:
                gen[q] = gen[p] + d
                (todo.appendleft if d == 0 else todo.append)(q)
    low = min(gen.values())
    return {p: v - low for p, v in gen.items()}


def _side_by_side(g: Graph, group: set[int], beside: dict[int, set[int]]) -> list[int]:
    """The order people side by side stand in, each next to their partner: along the line they make, or around the one
    with the most partners, the former ones on the left and the current one on the right."""
    if len(group) == 1:
        return list(group)
    near = {p: beside[p] & group for p in group}
    ends = sorted(p for p in group if len(near[p]) == 1)
    if ends and all(len(n) <= 2 for n in near.values()):  # a line: one partner after another
        out, prev, p = [], None, ends[0]
        while p is not None:
            out.append(p)
            nxt = sorted(near[p] - {prev} - set(out))
            prev, p = p, (nxt[0] if nxt else None)
        if len(out) == len(group):
            return out
    hub = max(group, key=lambda p: (len(near[p]), -p))
    others = sorted(group - {hub}, key=lambda q: (g.partners.get(hub, {}).get(q, (False,))[0], q))
    half = len(others) // 2
    return [*others[:half], hub, *others[half:]]


def _in_order(wanted: list[float], widths: list[int]) -> list[float]:
    """Left edges as near as can be to wanted (least squares), keeping the order and GAP between: pool adjacent
    violators, on the edges less the room the ones before take up."""
    room, at = [], 0.0
    for w in widths:
        room.append(at)
        at += w + GAP
    blocks: list[list[float]] = []  # [sum, count]
    for want, r in zip(wanted, room):
        blocks.append([want - r, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            s, n = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += n
    out = []
    for s, n in blocks:
        out += [s / n] * int(n)
    return [v + r for v, r in zip(out, room)]


def _blood(g: Graph, people: set[int], focus: int) -> set[int]:
    """focus's own blood among people: those sharing an ancestor with them (their descendants share focus)."""
    theirs = set(g.ancestors.get(focus, {focus: 0}))
    return {p for p in people if theirs & set(g.ancestors.get(p, {p: 0}))}
