"""What to look at before a GEDCOM import is brought in, and what can be done about it: gedcom_import's step 4, the
person's check, made with the whole of both trees in view.

The matcher pairs people by names, birth years and relatives. Here each pair, and each person it would add, is looked
at again:

- doubts about a match (Context.doubts): only a one-word name agrees ("Edna", and Edna Baker); the parents say
  otherwise (here Henry is Clara's father, and the file gives Clara another father); the ages can't be (here they
  have a child born in 1933, and the file says they were born in 1959); photos named by hand show them after the file
  says they died, or before they were born; the import would make someone their own ancestor, or a couple of a brother
  and sister; one person here is chosen for two in the file; a match was found through the family, not by name. The
  matcher doesn't make a match with a doubt by itself (settle), and one made anyway is brought in only once it is
  confirmed (check's `stop`).
- notes worth a look, which stop nothing: two in the file share a name; the partners are other people here.
- people the names missed (Context.kin): someone who would be added as new, whom the family points to here. Clara's
  mother is Margaret Ellen Kerr in the file and Margaret Dalton here: the same first name, and a married name. Those
  are matched "through the family" and asked about; one whose first name differs too (the only father Clara has here)
  is only suggested.
- clashes, where the two disagree about someone matched (Context.clashes): a birth, death, burial or cremation; their
  sex; their name; a parent (a third one, or a second mother or father); a couple's wedding or divorce. Each has its
  choices: what is here is kept unless the person picks the file's (or, for a parent, another way).

What the person decides is kept with the import, in its matches: `confirmed` (the person here a doubtful match was said
to be right about), `not` (people here they said someone isn't) and `resolve` (each clash's choice), which
gedcom_import.apply follows.
"""
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

from lineagebox import events, family
from lineagebox import gedcom_import as gi

YOUNGEST_PARENT, OLDEST_PARENT = 12, 70  # the ages a parent can have had at a child's birth, give or take a year
FAMILY_MATCH = 0.85  # a match through the family, by the same first name: made, and asked about
ONLY_ONE = 0.6       # the only father (or wife) someone has here, by another name: suggested only
ONE_EACH = ("BIRT", "DEAT", "BURI", "CREM")
COUPLE_ONE = ("MARR", "DIV")
TRUSTED = ("exif", "user")  # photo dates good enough to say someone was alive then
CLASH_CHOICES = {"event": ("keep", "theirs"), "sex": ("keep", "theirs"), "name": ("keep", "theirs"),
                 "couple": ("keep", "theirs"), "parent": ("keep", "adopted", "foster")}  # and replace:<id>, same:<id>


@dataclass
class Item:
    """One thing on the import page's To check list."""
    kind: str               # doubt | suggest | pick | clash | note
    xref: str               # the person in the file it is about
    pid: int | None         # the person here
    their: str              # names, for the page
    ours: str
    lines: list = field(default_factory=list)  # what is wrong
    stop: bool = False      # bringing it in waits until the match is confirmed or changed
    confirmed: bool = False
    code: str = ""          # a clash: event, sex, name, parent or couple
    key: str = ""           # a clash: its choice, kept in matches[owner]["resolve"][key]
    owner: str = ""
    chosen: str = "keep"
    here: str = ""          # a clash: what is here, and what the file has
    there: str = ""
    word: str = ""          # a parent clash: father or mother
    same_pid: int | None = None  # a parent clash: the parent here the file's may be
    rivals: list = field(default_factory=list)  # a pick: [(GEDCOM id, name)] of those in the file who fit pid alike


def parent_word(sex: str | None) -> str:
    return {"male": "father", "female": "mother"}.get(sex, "parent")


def child_word(sex: str | None) -> str:
    return {"male": "son", "female": "daughter"}.get(sex, "child")


def partner_word(sex: str | None) -> str:
    return {"male": "husband", "female": "wife"}.get(sex, "partner")


def _kin_words(near: tuple[int, int]) -> str:
    up_a, up_b = near
    if 0 in near:
        return "parent and child" if max(near) == 1 else "grandparent and grandchild"
    return "brother and sister" if up_a == up_b == 1 else "close relatives"


def _words(text: str | None) -> set[str]:
    """A name's words as matched: lower case, no initials."""
    return {w for w in gi.norm(text or "").split() if len(w) > 1}


def same_given(a: set[str], b: set[str]) -> bool:
    """Whether two sets of given names share one (first or middle), or a short form of one (Ted and Edward)."""
    return bool(a & b) or any(gi.SAME_NAME.get(w, set()) & b for w in a)


def _show(date: str | None, place: str | None, _sort: str | None = None) -> str:
    return ", ".join(x for x in (events.show_date(date) if date else "", place or "") if x) or "?"


def _differ(e: dict, here: tuple) -> bool:
    """Whether the file's event says something else than the one here (date, place, sort date): not when it says
    the same less exactly ("1946" for June 15, 1946; "Pittsburgh" for "Pittsburgh, Pennsylvania"), which isn't worth
    asking about; yes when it says it more exactly, as the file's may then be worth taking."""
    def one(theirs: str, ours: str) -> bool:
        if not theirs or not ours or theirs == ours:
            return False
        if theirs.startswith(ours) or ours.startswith(theirs):
            return len(theirs) > len(ours)
        return True
    dates = e["date"] and here[0] and e["date"] != here[0] and (
        one(e.get("sort") or "", here[2] or "") if e.get("sort") and here[2] else True)
    return bool(dates or one(gi.norm(e["place"] or ""), gi.norm(here[1] or "")))


class Context:
    """Both trees as the checks need them: the file's people and families, the catalog's, and who is matched."""

    def __init__(self, con: sqlite3.Connection, data: dict, matches: dict):
        self.con, self.data, self.matches = con, data, matches
        self.people, self.fams = data["people"], data["families"]
        self.g = family.load(con)
        self.rel = gi.their_relatives(data)
        self.parents_of, self.children_of, self.partners_of = defaultdict(list), defaultdict(list), defaultdict(list)
        for fam in self.fams.values():
            for p in fam["partners"]:
                self.partners_of[p] += [q for q in fam["partners"] if q != p]
                self.children_of[p] += fam["children"]
            for c in fam["children"]:
                self.parents_of[c] += fam["partners"]
        self.their_born = {x: gi.birth_year(p["events"]) for x, p in self.people.items()}
        self.their_died = {x: next((int(e["sort"][:4]) for e in p["events"] if e["tag"] == "DEAT" and e.get("sort")),
                                   None) for x, p in self.people.items()}
        self.our_born, self.our_died, self.our_events = {}, {}, defaultdict(dict)
        for r in con.execute(f"""SELECT person_id, tag, date, sort_date, place FROM event WHERE person_id IS NOT NULL
                                 AND tag IN ({','.join('?' * len(ONE_EACH))}) ORDER BY id""", ONE_EACH):
            self.our_events[r[0]].setdefault(r[1], (r[2], r[4], r[3]))
            if r[3] and r[1] in ("BIRT", "DEAT"):
                (self.our_born if r[1] == "BIRT" else self.our_died).setdefault(r[0], int(r[3][:4]))
        self.couple_events = defaultdict(dict)
        for r in con.execute("SELECT couple_a, couple_b, tag, date, place, sort_date FROM event WHERE couple_a IS NOT "
                             "NULL AND tag IN ('MARR', 'DIV') ORDER BY id"):
            self.couple_events[(r[0], r[1])].setdefault(r[2], (r[3], r[4], r[5]))
        # when someone was in photos, named by hand, with dates that are known (the camera's, or set by hand)
        self.photos = {r[0]: (int(r[1][:4]), int(r[2][:4])) for r in con.execute(
            f"""SELECT f.person_id, min(i.taken_at), max(i.taken_at) FROM face f JOIN image i ON i.id = f.image_id
                WHERE f.person_id IS NOT NULL AND f.assigned_by = 'user' AND i.taken_at IS NOT NULL
                AND i.taken_at_source IN ({','.join('?' * len(TRUSTED))}) GROUP BY f.person_id""", TRUSTED)}
        self.our_given = defaultdict(set)
        for pid, name, given in con.execute("SELECT id, name, given_name FROM person"):
            self.our_given[pid] |= _words(given or events.split_name(name)[0] or name)
        for pid, name, given, kind in con.execute("SELECT person_id, name, given_name, type FROM person_name"):
            self.our_given[pid] |= _words(given or (name if kind == "nickname" else events.split_name(name)[0]))
        self.doubtful: set[str] = set()  # matched, but with a doubt that stops it: points nobody to anyone
        self.chosen, self.here_for = {}, defaultdict(list)
        for x, m in matches.items():
            if m.get("choice") and x in self.people:
                self.choose(x, m["choice"])
        self._spans = {}

    def choose(self, x: str, pid: int) -> None:
        self.chosen[x] = pid
        self.here_for[pid].append(x)
        self._spans = {}

    def unchoose(self, x: str) -> None:
        pid = self.chosen.pop(x, None)
        if pid is not None:
            self.here_for[pid].remove(x)
        self._spans = {}

    def their_name(self, x: str) -> str:
        return self.people[x]["names"][0]["full"] if x in self.people else "?"

    def their_sex(self, x: str) -> str | None:
        return self.people[x]["sex"] if x in self.people else None

    def their_given(self, x: str) -> set[str]:
        out = set()
        for n in self.people[x]["names"]:
            out |= _words(n["given"])
        return out

    def settled(self, x: str) -> bool:
        """Whether someone's match is more than the matcher's guess: the same UID, chosen, confirmed, the AI's."""
        m = self.matches.get(x, {})
        return m.get("how") in ("uid", "chosen", "ai") or (m.get("confirmed") is not None
                                                           and m.get("confirmed") == m.get("choice"))

    def born_here(self, pid: int) -> int | None:
        """Their birth year here, or in the file for someone matched for sure (a guess at one match mustn't make
        another look wrong: a Henry born 1959 matched by name would make his daughter here too old)."""
        return self.our_born.get(pid) or next((self.their_born[x] for x in self.here_for.get(pid, ())
                                               if self.their_born.get(x) and self.settled(x)), None)

    def born_there(self, x: str) -> int | None:
        pid = self.chosen.get(x)
        return self.their_born.get(x) or (self.our_born.get(pid) if pid and self.settled(x) else None)

    def span_here(self, pid: int) -> tuple:
        """(earliest, why, latest, why) they can have been born in, from their family and photos here."""
        if ("o", pid) in self._spans:
            return self._spans[("o", pid)]
        lo = hi = None
        lo_why = hi_why = ""
        for q in self.g.parents.get(pid, ()):
            if (b := self.born_here(q)) and (lo is None or b + YOUNGEST_PARENT > lo):
                lo, lo_why = b + YOUNGEST_PARENT, f"their {parent_word(self.g.sex(q))} {self.g.name(q)} was born in {b}"
        for c in self.g.children.get(pid, ()):
            if (b := self.born_here(c)) and (hi is None or b - YOUNGEST_PARENT < hi):
                hi, hi_why = b - YOUNGEST_PARENT, f"their {child_word(self.g.sex(c))} {self.g.name(c)} was born in {b}"
            if b and (lo is None or b - OLDEST_PARENT > lo):
                lo, lo_why = b - OLDEST_PARENT, f"their {child_word(self.g.sex(c))} {self.g.name(c)} was born in {b}"
        if pid in self.photos and (hi is None or self.photos[pid][0] < hi):
            hi, hi_why = self.photos[pid][0], f"they are in photos from {self.photos[pid][0]}"
        if (d := self.our_died.get(pid)) and (hi is None or d < hi):
            hi, hi_why = d, f"they died in {d}"
        self._spans[("o", pid)] = (lo, lo_why, hi, hi_why)
        return self._spans[("o", pid)]

    def span_there(self, x: str) -> tuple:
        """The same from the file's family."""
        if ("t", x) in self._spans:
            return self._spans[("t", x)]
        lo = hi = None
        lo_why = hi_why = ""
        for z in self.parents_of.get(x, ()):
            if (b := self.born_there(z)) and (lo is None or b + YOUNGEST_PARENT > lo):
                lo, lo_why = b + YOUNGEST_PARENT, f"their {parent_word(self.their_sex(z))} {self.their_name(z)} was born in {b}"
        for c in self.children_of.get(x, ()):
            if (b := self.born_there(c)) and (hi is None or b - YOUNGEST_PARENT < hi):
                hi, hi_why = b - YOUNGEST_PARENT, f"their {child_word(self.their_sex(c))} {self.their_name(c)} was born in {b}"
            if b and (lo is None or b - OLDEST_PARENT > lo):
                lo, lo_why = b - OLDEST_PARENT, f"their {child_word(self.their_sex(c))} {self.their_name(c)} was born in {b}"
        if (d := self.their_died.get(x)) and (hi is None or d < hi):
            hi, hi_why = d, f"they died in {d}"
        self._spans[("t", x)] = (lo, lo_why, hi, hi_why)
        return self._spans[("t", x)]

    def agree(self, x: str, pid: int) -> bool:
        """Whether a relative of theirs in the file is matched to the same kind of relative of pid here."""
        return any(self.chosen.get(y) in gi.our_relatives(self.g, pid, kind) for kind, y in self.rel.get(x, ()))

    # ---- doubts ---------------------------------------------------------------------------------------------------

    def doubts(self, x: str, pid: int, matching: bool = False) -> list[tuple[str, bool]]:
        """What says x and pid may not be the same person: [(text, whether it stops the match)]. matching: while the
        matcher works (leaves out the doubts about choices themselves: chosen twice, found through the family)."""
        out = []
        g, m = self.g, self.matches.get(x, {})
        tn, on = self.their_name(x), g.name(pid)
        # a one-word name with nothing else to go on
        if not self.agree(x, pid):
            fulls = [n["full"] for n in self.people[x]["names"] if n["type"] != "nickname"]
            if fulls and all(len(gi.norm(f).split()) <= 1 for f in fulls):
                out.append((f"Only the one name {tn} is known about them, and nothing in their family agrees.", True))
            elif len(gi.norm(on).split()) == 1:
                out.append((f"Here they are only {on}, and nothing in their family agrees.", True))
        # their parents in the file are other people than their parents here
        ours = g.parents.get(pid, set())
        for z in self.parents_of.get(x, ()):
            q = self.chosen.get(z)
            if not q or q in ours:
                continue
            sex = self.their_sex(z) or g.sex(q)
            same = sorted(p for p in ours if sex and g.sex(p) == sex)
            if same:
                out.append((f"In the file, {tn}'s {parent_word(sex)} is {self.their_name(z)} ({g.name(q)} here); "
                            f"here, {on}'s {parent_word(sex)} is {g.name(same[0])}.", True))
        # their children here are matched to people the file gives another parent of their sex
        sex = self.their_sex(x) or g.sex(pid)
        for c in sorted(g.children.get(pid, ())):
            for y in self.here_for.get(c, ()):
                if x in self.parents_of.get(y, ()):
                    continue
                others = [w for w in self.parents_of.get(y, ()) if sex and self.their_sex(w) == sex
                          and self.chosen.get(w) != pid]
                if others:
                    out.append((f"Here, {on} is {g.name(c)}'s {parent_word(sex)}; in the file, {self.their_name(y)}'s "
                                f"{parent_word(sex)} is {self.their_name(others[0])}.", True))
        # ages that can't be
        b, ob = self.their_born.get(x), self.our_born.get(pid)
        if b and ob and abs(b - ob) > 2:
            out.append((f"Born in {ob} here, and in {b} in the file.", True))
        elif b:
            lo, lo_why, hi, hi_why = self.span_here(pid)
            if lo is not None and b < lo - 1:
                out.append((f"The file says born in {b}, but here {lo_why}.", True))
            elif hi is not None and b > hi + 1:
                out.append((f"The file says born in {b}, but here {hi_why}.", True))
        elif ob:
            lo, lo_why, hi, hi_why = self.span_there(x)
            if lo is not None and ob < lo - 1:
                out.append((f"Born in {ob} here, but in the file {lo_why}.", True))
            elif hi is not None and ob > hi + 1:
                out.append((f"Born in {ob} here, but in the file {hi_why}.", True))
        # photos named by hand from before they were born, or after they died
        first, last = self.photos.get(pid, (None, None))
        if (d := self.their_died.get(x)) and last and last > d + 1:
            out.append((f"{on} is in photos from {last}, named by hand, after the file says they died ({d}).", True))
        if b and first and first < b - 1:
            out.append((f"{on} is in photos from {first}, named by hand, before the file says they were born ({b}).",
                        True))
        # someone their own ancestor
        for z in self.parents_of.get(x, ()):
            q = self.chosen.get(z)
            if q and (q == pid or pid in g.ancestors.get(q, {})):
                out.append((f"Here, {g.name(q)} is {on}'s descendant, and the file makes them {tn}'s parent.", True))
        for c in self.children_of.get(x, ()):
            q = self.chosen.get(c)
            if q and (q == pid or q in g.ancestors.get(pid, {})):
                out.append((f"Here, {g.name(q)} is {on}'s ancestor, and the file makes them {tn}'s child.", True))
        # a couple who are close relatives here
        for z in self.partners_of.get(x, ()):
            q = self.chosen.get(z)
            if q and q != pid and (near := g.blood(pid, q)) and sum(near) <= 2:
                out.append((f"Here, {on} and {g.name(q)} are {_kin_words(near)}, and the file makes them a couple.",
                            True))
        if not matching:
            others = [y for y in self.here_for.get(pid, ()) if y != x]
            if others:
                out.append((f"{on} is also chosen for {', '.join(self.their_name(y) for y in others)} in the file.",
                            True))
            if m.get("how") == "family":
                out.append((f"Found through the family, not by name: {m.get('why', '')}.", True))
        return out

    def notes(self, x: str, pid: int) -> list[str]:
        """What is worth a look about a match without making it doubtful."""
        out, g = [], self.g
        tn, on = self.their_name(x), g.name(pid)
        namesakes = [y for y in self.people if y != x and gi.norm(self.their_name(y)) == gi.norm(tn)]
        if namesakes:
            out.append(f"{len(namesakes) + 1} people in the file are called {tn}: check that this one is {on}.")
        theirs, ours = self.partners_of.get(x, []), set(g.partners.get(pid, {}))
        if theirs and ours and not {self.chosen.get(z) for z in theirs} & ours:
            out.append(f"Here, {on}'s partners are {', '.join(sorted(g.name(q) for q in ours))}; in the file, {tn}'s "
                       f"are {', '.join(self.their_name(z) for z in theirs)}.")
        return out

    # ---- people the names missed --------------------------------------------------------------------------------

    def kin(self, x: str) -> list[tuple[int, float, str]]:
        """People here the family points to for x: [(person id, score, why)], the likeliest first. Clara's mother in
        the file, Margaret Ellen Kerr, is Margaret Dalton, her mother here (the same first name); her father in the
        file is perhaps Henry Miller Sr., the only father she has here."""
        found, g = {}, self.g
        refused = set(self.matches.get(x, {}).get("not", ()))
        sex, born, mine = self.their_sex(x), self.their_born.get(x), self.their_given(x)
        for kind, y in self.rel.get(x, ()):
            r = self.chosen.get(y)
            if not r or y in self.doubtful:
                continue
            role = {"parents": "children", "children": "parents", "partners": "partners"}[kind]  # what x is to y
            pool = sorted(q for q in gi.our_relatives(g, r, role)
                          if q > 0 and not (sex and g.sex(q) and g.sex(q) != sex))
            word = {"parents": parent_word, "children": child_word, "partners": partner_word}[role](sex)
            for q in pool:
                b = self.born_here(q)
                if q in refused or (born and b and abs(born - b) > 2):
                    continue
                if same_given(mine, self.our_given.get(q, set())):
                    score, why = FAMILY_MATCH, (f"{g.name(r)}'s {word} here is {g.name(q)}, with the same first name "
                                                f"as {self.their_name(x)}")
                elif len(pool) == 1 and role in ("parents", "partners"):
                    score, why = ONLY_ONE, f"the only {word} {g.name(r)} has here is {g.name(q)}"
                else:
                    continue
                if q in found and why not in found[q][1]:  # pointed to from two sides: the likelier, both said
                    best, said = found[q]
                    why = f"{said}, and {why}" if best >= score else f"{why}, and {said}"
                    top = max(score, best)  # by role alone (another first name) it stays a suggestion
                    score = top if top >= FAMILY_MATCH else min(top + 0.05, FAMILY_MATCH - 0.05)
                found[q] = (round(score, 3), why)
        return sorted(((q, s, w) for q, (s, w) in found.items()), key=lambda c: (-c[1], c[0]))

    # ---- clashes --------------------------------------------------------------------------------------------------

    def clashes(self, x: str, pid: int) -> list[Item]:
        """Where the file and the catalog disagree about x, matched to pid: each with its choices."""
        out, g = [], self.g
        p = self.people[x]
        tn, on = self.their_name(x), g.name(pid)

        def clash(code, key, line, here, there, owner=x, **more):
            out.append(Item("clash", x, pid, tn, on, [line], code=code, key=key, owner=owner,
                            chosen=self.matches[owner].get("resolve", {}).get(key, "keep"), here=here, there=there,
                            **more))

        for e in p["events"]:
            here = self.our_events.get(pid, {}).get(e["tag"]) if e["tag"] in ONE_EACH else None
            if not here or (e["tag"] in [i.key[6:] for i in out if i.code == "event"]):
                continue
            if _differ(e, here):
                label = events.PERSON_EVENTS[e["tag"]]
                clash("event", f"event:{e['tag']}", f"{label}: {_show(*here)} here, {_show(e['date'], e['place'])} "
                      f"in the file.", _show(*here), _show(e["date"], e["place"]))
        if p["sex"] and g.sex(pid) and p["sex"] != g.sex(pid):
            clash("sex", "sex", f"{on} is {g.sex(pid)} here, and {p['sex']} in the file.", g.sex(pid), p["sex"])
        first = p["names"][0]
        if first["given"] and self.our_given.get(pid) and not same_given(self.their_given(x), self.our_given[pid]):
            clash("name", "name", f"Here they are called {on}, and in the file {first['full']}.", on, first["full"])
        ours = g.parents.get(pid, set())
        born_parents = {q for q in ours if g.how(pid, q) == "birth"}
        for z in self.parents_of.get(x, ()):
            q = self.chosen.get(z)
            if q and q in ours:
                continue
            sex = self.their_sex(z) or (g.sex(q) if q else None)
            same = sorted(o for o in born_parents if o != q and sex and g.sex(o) == sex)
            if not same and len(born_parents - {q}) < 2:
                continue
            here_p = same[0] if same else min(born_parents - {q})
            word = parent_word(sex)
            clash("parent", f"parent:{z}", f"{on}'s {word} here is {g.name(here_p)}; the file gives "
                  f"{self.their_name(z)}.", g.name(here_p), self.their_name(z), word=word, same_pid=here_p)
        for fx, fam in self.fams.items():
            if x not in fam["partners"] or fam["partners"][0] != x or len(fam["partners"]) != 2:
                continue
            q = self.chosen.get(fam["partners"][1])
            if not q:
                continue
            here_events = self.couple_events.get((min(pid, q), max(pid, q)), {})
            for e in fam["events"]:
                here = here_events.get(e["tag"]) if e["tag"] in COUPLE_ONE else None
                if here and _differ(e, here):
                    label = events.COUPLE_EVENTS[e["tag"]]
                    clash("couple", f"fam:{fx}:{e['tag']}", f"{label} ({on} and {g.name(q)}): {_show(*here)} here, "
                          f"{_show(e['date'], e['place'])} in the file.", _show(*here), _show(e["date"], e["place"]))
        return out

    def item(self, kind: str, x: str, pid: int | None, lines: list, **more) -> Item:
        return Item(kind, x, pid, self.their_name(x), self.g.name(pid) if pid else "", lines, **more)


def check(con: sqlite3.Connection, data: dict, matches: dict) -> list[Item]:
    """The import page's To check list, the ones that stop it first."""
    ctx, items = Context(con, data, matches), []
    for x, m in matches.items():
        pid = m.get("choice")
        if x not in ctx.people or not pid or pid not in ctx.g.people:
            continue
        lines = [t for t, _ in ctx.doubts(x, pid)]
        notes = ctx.notes(x, pid)
        if lines:
            confirmed = m.get("confirmed") == pid
            items.append(ctx.item("doubt", x, pid, lines + notes, confirmed=confirmed,
                                  stop=not confirmed and m.get("how") != "uid"))
            if items[-1].stop:
                ctx.doubtful.add(x)
        elif notes:
            items.append(ctx.item("note", x, pid, notes))
        items += ctx.clashes(x, pid)
    for x, m in matches.items():
        if x in ctx.people and not m.get("choice"):
            for q, _score, why in ctx.kin(x)[:1]:
                lines = [why[0].upper() + why[1:] + ("" if why.endswith(".") else ".")]
                if ctx.here_for.get(q):
                    lines.append(f"{ctx.g.name(q)} is chosen for {', '.join(ctx.their_name(y) for y in ctx.here_for[q])}"
                                 f" now.")
                items.append(ctx.item("suggest", x, q, lines))
    # two or more in the file who fit one person here as well as each other (Ted Steven, and Robert Edward known
    # as Ted, for a Ted here): which of them is it?
    seen = set()
    for x, m in matches.items():
        if x in seen or x not in ctx.people or m.get("choice") or not m.get("rivals") or not m.get("candidates"):
            continue
        pid = m["candidates"][0][0]
        group = [y for y in (x, *m["rivals"]) if y in matches and not matches[y].get("choice")
                 and pid not in matches[y].get("not", ())]
        seen |= set(group)
        if len(group) > 1 and pid in ctx.g.people and not ctx.here_for.get(pid):
            names = [(y, ctx.their_name(y)) for y in group]
            items.append(ctx.item("pick", x, pid, [f"{' and '.join(n for _, n in names)} in the file fit "
                                                   f"{ctx.g.name(pid)} here as well as each other."], rivals=names))
    order = {"doubt": 0, "suggest": 1, "pick": 1, "clash": 2, "note": 3}
    return sorted(items, key=lambda i: (i.confirmed, not i.stop, order[i.kind], i.their.lower(), i.key))


def settle(con: sqlite3.Connection, data: dict, matches: dict, rounds: int = 4) -> None:
    """The matcher's last step: a match it made that has a doubt stopping it is unmade (and says why), and someone left
    to be added as new whom the family points to here, with the same first name, is matched through the family. Over
    a few rounds, as one match leads to the next (Clara to her mother, her mother to her husband)."""
    for _ in range(rounds):
        changed = False
        ctx = Context(con, data, matches)
        for x, m in matches.items():
            pid = m.get("choice")
            if pid and x in ctx.people and m["how"] in ("sure", "likely", "family"):
                stops = [t for t, stop in ctx.doubts(x, pid, matching=True) if stop]
                if stops:
                    m["choice"], m["how"] = None, "unsure"
                    m["why"] = f"{m['why']}{'' if m['why'].endswith('.') else '.'} Not made by itself: {stops[0]}"
                    ctx.unchoose(x)
                    changed = True
        for x, m in matches.items():
            if m.get("choice") or x not in ctx.people or m["how"] == "uid":
                continue
            found = ctx.kin(x)
            by_name = max((c[1] for c in m["candidates"]), default=0)
            for q, score, why in reversed(found):
                if all(c[0] != q for c in m["candidates"]):
                    m["candidates"].insert(0, [q, round(score, 3), why])
            if found and (m["how"] == "new" or found[0][1] >= by_name):  # the family's reason, over a weaker likeness
                m["how"], m["why"] = "unsure", found[0][2]
            for q, score, why in found:
                if score < FAMILY_MATCH or ctx.here_for.get(q):
                    continue
                if not any(stop for _, stop in ctx.doubts(x, q, matching=True)):
                    m["choice"], m["how"], m["why"] = q, "family", why
                    ctx.choose(x, q)
                    changed = True
                break
        if not changed:
            break


def stops(con: sqlite3.Connection, data: dict, matches: dict) -> list[Item]:
    return [i for i in check(con, data, matches) if i.stop]
