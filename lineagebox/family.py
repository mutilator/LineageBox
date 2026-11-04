"""Family and other relationships between people.

Only facts are stored: who is whose parent, who are siblings (when their parents are not in the catalog), who are or
were partners, each person's sex, and other relationships such as friend or coworker. Everything else is worked out
here, relative to one person (normally the one marked as "me"): niece, brother-in-law, stepson, former sister-in-law...
Those words also work in search ("nephews 2024") and choose the people of photo tours ("Brothers 2024").
"""
import json
import re
import sqlite3
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field

from lineagebox import errors, events

SEXES = ("male", "female")

# What someone can be added as on a person's page: word -> (link, the sex it implies)
FAMILY_WORDS = {
    "father": ("parent", "male"), "mother": ("parent", "female"), "parent": ("parent", None),
    "son": ("child", "male"), "daughter": ("child", "female"), "child": ("child", None),
    "brother": ("sibling", "male"), "sister": ("sibling", "female"), "sibling": ("sibling", None),
    "husband": ("partner", "male"), "wife": ("partner", "female"), "partner": ("partner", None),
    "ex-husband": ("ex", "male"), "ex-wife": ("ex", "female"), "ex-partner": ("ex", None),
}
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
    partners: dict[int, dict[int, tuple[bool, bool]]]  # person -> {partner: (current, married)}
    relations: list[tuple[int, int, str]]            # (person, of whom, kind)
    ancestors: dict[int, dict[int, int]] = field(default_factory=dict)  # person -> {ancestor: generations up}

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
        for p in self.people:
            up, todo = {p: 0}, deque([p])
            while todo:
                q = todo.popleft()
                for parent in effective.get(q, ()):
                    if parent not in up:
                        up[parent] = up[q] + 1
                        todo.append(parent)
            self.ancestors[p] = up

    def sex(self, pid: int) -> str | None:
        return self.people[pid]["sex"] if pid in self.people else None

    def name(self, pid: int) -> str:
        return self.people[pid]["name"] if pid in self.people else "?"

    def blood(self, a: int, b: int) -> tuple[int, int] | None:
        """(generations from a up to the closest shared ancestor, generations from b up to it), or None."""
        up_a, up_b = self.ancestors.get(a, {}), self.ancestors.get(b, {})
        shared = up_a.keys() & up_b.keys()
        if not shared:
            return None
        best = min(shared, key=lambda s: (up_a[s] + up_b[s], up_a[s]))
        return up_a[best], up_b[best]

    def half(self, a: int, b: int) -> bool:
        pa, pb = self.parents.get(a, set()), self.parents.get(b, set())
        return len(pa) == 2 and len(pb) == 2 and len(pa & pb) == 1

    def all_siblings(self, pid: int) -> set[int]:
        out = set(self.siblings.get(pid, ()))
        for parent in self.effective_parents.get(pid, ()):
            out |= {c for c, ps in self.effective_parents.items() if parent in ps}
        out.discard(pid)
        return out


def load(con: sqlite3.Connection) -> Graph:
    people = {r["id"]: r for r in con.execute("SELECT id, name, sex, deceased, birth_date FROM person")}
    parents, children, siblings = defaultdict(set), defaultdict(set), defaultdict(set)
    partners = defaultdict(dict)
    for child, parent in con.execute("SELECT child_id, parent_id FROM parent"):
        parents[child].add(parent)
        children[parent].add(child)
    for a, b in con.execute("SELECT a, b FROM sibling"):
        siblings[a].add(b)
        siblings[b].add(a)
    for a, b, current, married in con.execute("SELECT a, b, current, married FROM partner"):
        partners[a][b] = partners[b][a] = (bool(current), bool(married))
    relations = [tuple(r) for r in con.execute("SELECT person_id, of_id, kind FROM relation")]
    return Graph(people, dict(parents), dict(children), dict(siblings), dict(partners), relations)


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


def _partner_word(sex, married: bool, current: bool) -> str:
    word = _w(sex, "husband", "wife", "spouse") if married else "partner"
    return word if current else "ex-" + word


def _kin(label: str, group: str, neutral: str, distance: int) -> Kin:
    former = group == "former family"
    words = {compact(label), compact(neutral)}
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
    blood = {}
    for x in g.people:
        if x != ego and (ab := g.blood(ego, x)):
            blood[x] = ab
            half = ab == (1, 1) and g.half(ego, x)
            label, group = _blood_label(*ab, g.sex(x), half)
            out[x] = _kin(label, group, _blood_label(*ab, None, half)[0], sum(ab))
    mine = sorted(g.partners.get(ego, {}).items(), key=lambda kv: not kv[1][0])  # current first
    for q, (current, married) in mine:
        if q not in out:
            out[q] = _kin(_partner_word(g.sex(q), married, current), "partner" if current else "former family",
                          _partner_word(None, married, current), 1)
    for q, (current, _) in mine:
        for x in g.people:
            if x not in out and (ab := g.blood(q, x)):
                label, group = _partners_relative(*ab, g.sex(x))
                neutral = _partners_relative(*ab, None)[0]
                if not current:
                    label, group, neutral = "former " + label, "former family", "former " + neutral
                out[x] = _kin(label, group, neutral, sum(ab) + 1)
    for y, ab in sorted(blood.items(), key=lambda kv: sum(kv[1])):
        for r, (current, _) in sorted(g.partners.get(y, {}).items(), key=lambda kv: not kv[1][0]):
            if r not in out:
                label, group = _relatives_partner(*ab, g.sex(r))
                neutral = _relatives_partner(*ab, None)[0]
                if not current:
                    label, group, neutral = "former " + label, "former family", "former " + neutral
                out[r] = _kin(label, group, neutral, sum(ab) + 1)
    # A relative's current partner's children from other relationships are step-family: a parent's are stepsiblings,
    # a brother's are step-nieces and step-nephews. (An ex's children are described by the way to them instead.)
    for y, (a, b) in sorted(blood.items(), key=lambda kv: sum(kv[1])):
        for r, (current, _) in g.partners.get(y, {}).items():
            for x in sorted(g.effective_children.get(r, ())) if current else ():
                if x > 0 and x not in out:
                    out[x] = _kin(_step(_blood_label(a, b + 1, g.sex(x))[0]), "step-family",
                                  _step(_blood_label(a, b + 1, None)[0]), a + b + 2)
    _describe_the_rest(g, ego, out)
    return out


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
        steps += [(r, _partner_word(g.sex(r), m, cur)) for r, (cur, m) in g.partners.get(u, {}).items()]
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
    removed there) or follows from another (a brother's parents)."""
    entered_children = g.children.get(pid, set())
    return {
        "parents": [{"id": p, "entered": p in g.parents.get(pid, ())}
                    for p in sorted(g.effective_parents.get(pid, ())) if p > 0],
        "partners": [{"id": q, "current": cur, "married": m}
                     for q, (cur, m) in sorted(g.partners.get(pid, {}).items(), key=lambda kv: (not kv[1][0], kv[0]))],
        "siblings": [{"id": s, "entered": s in g.siblings.get(pid, ()), "half": g.half(pid, s)}
                     for s in sorted(g.all_siblings(pid), key=g.name)],
        "children": [{"id": c, "entered": c in entered_children,
                      "with": sorted(p for p in g.effective_parents.get(c, ()) if p > 0 and p != pid)}
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
         "spouse", "partner", "grandfather", "grandmother", "grandparent", "grandson", "granddaughter", "grandchild",
         "nephew", "niece", "aunt", "uncle", "cousin", "family", "relative"}
_PREFIXES = ("ex", "step", "half", "great", "grand", "former")
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
    word = " ".join(word.lower().split())
    if other == pid:
        raise Problem("Someone cannot be their own relative.")
    if word in FAMILY_WORDS:
        kind, sex = FAMILY_WORDS[word]
        if sex:
            con.execute("UPDATE person SET sex = ? WHERE id = ? AND sex IS NULL", (sex, other))
        if kind == "parent":
            _add_parent(con, child=pid, parent=other, shown=shown)
        elif kind == "child":
            _add_parent(con, child=other, parent=pid, shown=shown)
            if with_parent and with_parent not in (pid, other):
                _add_parent(con, child=other, parent=with_parent, shown=shown)
        elif kind == "sibling":
            con.execute("INSERT OR IGNORE INTO sibling(a, b) VALUES (?, ?)", (min(pid, other), max(pid, other)))
        else:
            current = kind == "partner"
            married = not word.endswith("partner")
            if current:
                _no_other_current_partner(con, pid, other, shown)
                _no_other_current_partner(con, other, pid, shown)
            con.execute("""INSERT INTO partner(a, b, current, married, uid) VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT(a, b) DO UPDATE SET current = excluded.current, married = excluded.married""",
                        (min(pid, other), max(pid, other), int(current), int(married), str(uuid.uuid4())))
    elif word:
        if len(word) > 40:
            raise Problem("That relationship is too long; a word or two, like coworker.")
        _not_worked_out(word)
        con.execute("INSERT OR IGNORE INTO relation(person_id, of_id, kind) VALUES (?, ?, ?)", (other, pid, word))
    else:
        raise Problem("Say what they are to each other.")
    con.commit()


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


def _add_parent(con, child: int, parent: int, shown: set[int] | None = None) -> None:
    def name(pid):
        return (con.execute("SELECT name FROM person WHERE id = ?", (pid,)).fetchone() or ("?",))[0]

    if _is_ancestor(con, child, parent):
        raise Problem(f"{name(child)} is already {name(parent)}'s ancestor.")
    others = {r[0] for r in con.execute("SELECT parent_id FROM parent WHERE child_id = ? AND parent_id != ?",
                                        (child, parent))}
    if len(others) >= 2:
        if shown is not None and not others <= shown:
            raise Problem(f"{name(child)}'s two parents are already entered. An admin can change that.")
        raise Problem(f"{name(child)} already has two parents. Remove one first.")
    con.execute("INSERT OR IGNORE INTO parent(child_id, parent_id) VALUES (?, ?)", (child, parent))


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
    if current:
        _no_other_current_partner(con, a, b, shown)
        _no_other_current_partner(con, b, a, shown)
    con.execute("UPDATE partner SET current = ? WHERE a = ? AND b = ?", (int(current), min(a, b), max(a, b)))
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
            u[key] = [tree(g, k, depth - 1, seen) if depth > 1 else {"id": k, "unions": []}
                      for k in sorted(u[key], key=birth) if k not in seen]
    return {"id": root, "unions": unions}


def view(g: Graph, center: int, ego: int | None = None) -> dict:
    """The family around one person, for the Family page, instead of everyone at once: their grandparents and parents
    above, their own generation (partners, brothers and sisters), and below, their children and their brothers' and
    sisters' children. With how many generations go on above and below, to climb to by making someone else the
    centre, and the way there from ego (ego, their mother, her father...) to climb back. Stand-in parents (siblings
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


def tree_root(g: Graph, pid: int) -> int:
    """The top of the tree pid is best seen in: their highest ancestor with the most descendants."""
    up = g.ancestors.get(pid, {pid: 0})
    candidates = [a for a in up if not g.effective_parents.get(a)]
    count = lambda a: sum(1 for ancestors in g.ancestors.values() if a in ancestors)
    return max(candidates, key=lambda a: (up[a], count(a), -a), default=pid)
