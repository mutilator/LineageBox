"""Merging a GEDCOM file into the family: a tree from Ancestry, FamilySearch, MyHeritage or Gramps, or a LineageBox
export coming back.

1. read: the file's people and families as plain data, kept with the import (JSON) until it is applied;
2. match: who in the file is who here. A UID settles it (a LineageBox export, or an earlier import of the same tree);
   otherwise names (all of them: maiden names, nicknames), birth years and sex, then the relatives already matched
   ("Jake" here and "Jake Thomas Miller" there, whose parents match too). Each match says why and how sure it is;
3. ask_ai: for the unsure ones, Ollama compares the two people (text only: names, dates, places, relatives) and says
   whether they are the same, and why;
4. the person looks over every match on the import page, and changes any;
5. apply: adds what the catalog doesn't have (people, names, events, families, relationships, groups). It never
   removes or overwrites what was entered here: where the two disagree (another birth date, another sex), what is
   here stays, and the report lists it.
"""
import io
import json
import re
import sqlite3
import uuid
import zipfile
import zlib
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher

import httpx

from lineagebox import errors, events, family, gedcom, people
from lineagebox.config import Config

MOST = 100 * 2**20  # the largest GEDCOM file taken, and the largest .ged unpacked from a zip
PERSON_TAGS = set(events.PERSON_EVENTS)
COUPLE_TAGS = set(events.COUPLE_EVENTS)
ROLE_WORDS = {"FRIEND": "friend", "NGHBR": "neighbor", "GODP": "godparent"}
SURE, LIKELY, UNSURE = 0.95, 0.8, 0.6  # match scores
# first names and their usual short forms, each group one name: Mike is Michael (a likely match, not a sure one)
NICKNAMES = [
    "michael mike mikey mick", "william bill billy will liam", "robert bob bobby rob robbie bert", "james jim jimmy jamie",
    "john jack johnny", "joseph joe joey", "thomas tom tommy", "richard rick ricky rich dick", "charles charlie chuck",
    "edward ed eddie ted ned", "elizabeth liz lizzie beth betty eliza", "margaret maggie peggy meg marge",
    "katherine catherine kathy kate katie cathy", "christina christine chris tina", "christopher chris",
    "daniel dan danny", "david dave", "steven stephen steve", "anthony tony", "andrew andy drew", "nicholas nick",
    "nicole nicky nikki", "jennifer jen jenny", "rebecca becky becca", "deborah debby debbie deb",
    "pamela pam", "patricia pat patty trish", "susan sue susie", "gregory greg", "douglas doug", "randall randy",
    "bradley brad", "angela angie", "alexander alex", "samuel sam", "benjamin ben", "matthew matt",
    "timothy tim", "kenneth ken kenny", "ronald ron ronnie", "donald don", "gerald jerry", "lawrence larry",
    "theodore ted teddy", "victoria vicky tori", "abigail abby", "zachary zach zack", "joshua josh",
    "jonathan jon", "cynthia cindy", "lucinda cindy"]
_SAME_NAME = {}
for _group in NICKNAMES:
    for _name in _group.split():
        _SAME_NAME.setdefault(_name, set()).update(_group.split())
AI_SCHEMA = {"type": "object", "properties": {"same": {"type": "boolean"}, "confidence": {"type": "number"},
                                              "reason": {"type": "string"}}, "required": ["same", "confidence", "reason"]}
AI_SYSTEM = """You help merge family trees. You are shown a person from a GEDCOM file and a person already in a family
photo catalog. Say whether they are the same person. Names can differ (nicknames, middle names, maiden and married
names, typos); dates can be missing. Weigh the relatives: the same parents, partners or children are strong evidence.
Different birth years more than a couple of years apart, or different sexes, mean different people. Answer with JSON:
same (true or false), confidence (0 to 1) and a short reason."""


class Problem(errors.Problem):
    """A file that cannot be read; the message is shown to the person who uploaded it."""


# ---- 1. reading ----------------------------------------------------------------------------------------------------

def _unzip(data: bytes) -> bytes:
    """The .ged inside a GEDZIP (.gdz) or a .zip, read no further than MOST: a zip can unpack to a thousand times
    its size."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".ged")]
            if not names:
                raise Problem("The zip has no .ged file in it.")
            with z.open("gedcom.ged" if "gedcom.ged" in names else names[0]) as f:
                ged = f.read(MOST + 1)
    except (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, NotImplementedError):  # damaged, locked, odd
        raise Problem("That zip file is damaged or can't be opened here: save the tree again from the program it "
                      "came from, as a .ged file.") from None
    if len(ged) > MOST:
        raise Problem(f"The family tree in that zip is over {MOST // 2**20} MB.")
    return ged


def read(data: bytes) -> dict:
    """{'people': {xref: person}, 'families': {xref: family}} from a .ged, or a .gdz/.zip holding one."""
    if data[:2] == b"PK":
        data = _unzip(data)
    records = gedcom.parse(data)
    if not records or records[0].tag != "HEAD":
        raise Problem("This isn't a GEDCOM file: it doesn't start with a HEAD record.")
    out = {"people": {}, "families": {}}
    for r in records:
        if r.tag == "INDI" and r.xref:
            out["people"][r.xref] = _person(r)
        elif r.tag == "FAM" and r.xref:
            out["families"][r.xref] = {
                "partners": [n.payload for n in r.children if n.tag in ("HUSB", "WIFE") and n.payload.startswith("@")],
                "children": [n.payload for n in r.all("CHIL") if n.payload.startswith("@")],
                "events": [_event(n) for n in r.children if n.tag in COUPLE_TAGS | {"ANUL", "MARB", "MARC", "SEP"}],
                "uid": r.value("UID") or r.value("_UID") or None}
    if not out["people"]:
        raise Problem("The file has no people in it.")
    return out


def _person(r: gedcom.Node) -> dict:
    names = []
    for n in r.all("NAME"):
        m = re.match(r"^(.*?)/(.*?)/(.*)$", n.payload)
        given = (n.value("GIVN") or (m[1] if m else n.payload)).strip()
        surname = (n.value("SURN") or (m[2] if m else "")).strip()
        kind = n.value("TYPE").lower()
        names.append({"given": given, "surname": surname or None, "type": kind if kind in ("birth", "married", "aka")
                      else "maiden" if kind == "maiden" else "", "full": " ".join(f"{given} {surname}".split())})
        for nick in n.all("NICK"):
            names.append({"given": nick.payload, "surname": None, "type": "nickname", "full": nick.payload})
    sex = {"M": "male", "F": "female"}.get(r.value("SEX").upper()[:1])
    asso = []
    for a in r.all("ASSO"):
        role = a.first("ROLE")
        word = (role.value("PHRASE") if role and role.payload == "OTHER" else ROLE_WORDS.get(role.payload if role else "")
                ) or a.value("RELA")
        if a.payload.startswith("@") and word:
            asso.append([a.payload, word.lower()])
    return {"names": names or [{"given": "?", "surname": None, "type": "", "full": "?"}], "sex": sex,
            "events": [_event(n) for n in r.children if n.tag in PERSON_TAGS],
            "famc": [[n.payload, n.value("PEDI").lower() or "birth"] for n in r.all("FAMC")],
            "fams": [n.payload for n in r.all("FAMS")], "uid": r.value("UID") or r.value("_UID") or None,
            "note": "\n".join(n.payload for n in r.all("NOTE") if not n.payload.startswith("@")),
            "pet": r.value("_PET") == "Y", "groups": [n.payload for n in r.all("_GROUP")], "asso": asso}


def _event(n: gedcom.Node) -> dict:
    tag, kind = n.tag, n.value("TYPE")
    if tag in ("ANUL", "MARB", "MARC", "SEP"):  # couple events with no tag of their own here
        tag, kind = "EVEN", {"ANUL": "Annulment", "MARB": "Marriage banns", "MARC": "Marriage contract",
                             "SEP": "Separation"}[n.tag]
    if tag == "OCCU" and n.payload and n.payload != "Y":
        kind = n.payload
    date, sort = _date(n.value("DATE"))
    return {"tag": tag, "type": kind or None, "date": date, "sort": sort, "place": n.value("PLAC") or None,
            "note": n.value("NOTE") or None}


def _date(text: str) -> tuple[str | None, str | None]:
    """A GEDCOM date as stored here; one this can't read (a phrase, another calendar) is kept as written."""
    if not text.strip():
        return None, None
    try:
        d = events.parse_date(text)
        return d.gedcom, d.sort
    except events.Problem:
        year = re.search(r"\b(\d{4})\b", text)
        return text.strip().upper()[:80], year[1] if year else None


# ---- 2. matching ---------------------------------------------------------------------------------------------------

def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def _birth_year(evts) -> int | None:
    for e in evts:
        if e.get("tag") == "BIRT" and e.get("sort"):
            return int(e["sort"][:4])
    return None


def _ours(con: sqlite3.Connection) -> dict[int, dict]:
    out = {}
    for r in con.execute("SELECT id, name, given_name, surname, sex, uid FROM person"):
        variants = {_norm(r["name"])}
        if r["given_name"]:
            variants.add(_norm(f"{r['given_name']} {r['surname'] or ''}"))
        given, surname = (r["given_name"], r["surname"]) if r["given_name"] else events.split_name(r["name"])
        out[r["id"]] = {"name": r["name"], "sex": r["sex"], "uid": r["uid"], "variants": variants, "born": None,
                        "parts": [(_norm(given or "").split()[:1], _norm(surname or ""))]}
    for pid, name, given, surname in con.execute("SELECT person_id, name, given_name, surname FROM person_name"):
        out[pid]["variants"].add(_norm(name))
        out[pid]["parts"].append((_norm(given or name).split()[:1], _norm(surname or "")))
    for pid, sort in con.execute("SELECT person_id, sort_date FROM event WHERE tag = 'BIRT' AND sort_date IS NOT NULL"):
        out[pid]["born"] = int(sort[:4])
    return out


def _name_score(theirs: dict, ours: dict) -> tuple[float, str]:
    """How alike two people's names are, 0 to 1: first names and surnames apart (a shared surname alone makes no
    match: Ruth and Walter Miller), over every name either has (maiden names, nicknames)."""
    if {_norm(n["full"]) for n in theirs["names"]} & ours["variants"]:
        return 1.0, f"same name ({ours['name']})"
    best, why = 0.0, ""
    for n in theirs["names"]:
        first, surname = _norm(n["given"] or "").split()[:1], _norm(n["surname"] or "")
        for our_first, our_surname in ours["parts"]:
            if not first or not our_first:
                continue
            if first == our_first:
                fs = 1.0
            elif our_first[0] in _SAME_NAME.get(first[0], ()):
                fs = 0.8  # Mike and Michael
            else:
                fs = SequenceMatcher(None, first[0], our_first[0]).ratio()
            if fs < 0.6:
                continue
            if surname and our_surname:
                ss = 1.0 if surname == our_surname else SequenceMatcher(None, surname, our_surname).ratio()
                score = min(0.95, fs * ss)  # only the very same name is certain
            elif not our_surname and len(ours["name"].split()) == 1:
                score = 0.75 * fs  # a one-word name here: Jake, Nana, Grandpa
            else:
                score = 0.6 * fs
            if score > best:
                best, why = score, (f"same first name and surname as {ours['name']}" if score >= 0.95
                                    else f"name {n['full']!r} ≈ {ours['name']!r}")
    return best, why


def match(con: sqlite3.Connection, data: dict) -> dict:
    """GEDCOM id -> {'choice': person id or None (a new person), 'how': uid|sure|likely|unsure|new, 'why',
    'candidates': [[person id, score, why]], 'ai': None}."""
    ours = _ours(con)
    by_uid = {o["uid"]: pid for pid, o in ours.items() if o["uid"]}
    by_uid.update(dict(con.execute("SELECT uid, person_id FROM external_id")))
    g = family.load(con)
    theirs = data["people"]
    rel = _their_relatives(data)
    scores: dict[str, dict[int, tuple[float, str]]] = {}
    out = {}
    for x, p in theirs.items():
        if p["uid"] and p["uid"] in by_uid:
            out[x] = {"choice": by_uid[p["uid"]], "how": "uid", "why": "the same person (same UID)",
                      "candidates": [[by_uid[p["uid"]], 1.0, "same UID"]], "ai": None}
            continue
        born = _birth_year(p["events"])
        cands = {}
        for pid, o in ours.items():
            if p["sex"] and o["sex"] and p["sex"] != o["sex"]:
                continue
            if born and o["born"] and abs(born - o["born"]) > 2:
                continue
            score, why = _name_score(p, o)
            if score >= 0.5:
                if born and o["born"]:
                    score, why = min(1.0, score + 0.05), why + f", born {o['born']}"
                cands[pid] = (score, why)
        scores[x] = cands
    names_only = {x: dict(c) for x, c in scores.items()}
    for _ in range(2):  # relatives that match each other make a match likelier, and the other way round
        chosen = {x: m["choice"] for x, m in out.items()}
        chosen.update({x: max(c, key=lambda pid: c[pid][0]) for x, c in scores.items() if c})
        for x, cands in names_only.items():
            for pid, (score, why) in cands.items():
                agree = sorted({kind for kind, other in rel[x] if chosen.get(other) in _our_relatives(g, pid, kind)})
                scores[x][pid] = (min(1.0, score + 0.1 * len(agree)),
                                  why + (f", and {' and '.join(agree)} match" if agree else ""))
    taken = {m["choice"] for m in out.values()}
    for x in sorted(scores, key=lambda x: -max((s for s, _ in scores[x].values()), default=0)):
        ranked = sorted(scores[x].items(), key=lambda kv: -kv[1][0])
        cands = [[pid, round(s, 3), why] for pid, (s, why) in ranked[:5]]
        free = [c for c in cands if c[0] not in taken]
        best = free[0] if free else None
        second = free[1][1] if len(free) > 1 else 0.0
        if best and best[1] >= SURE and best[1] - second >= 0.1:
            how = "sure"
        elif best and best[1] >= LIKELY:
            how = "likely"
        elif best and best[1] >= UNSURE:
            how = "unsure"
        else:
            how, best = "new", None
        if best and how in ("sure", "likely"):
            taken.add(best[0])
        out[x] = {"choice": best[0] if best and how in ("sure", "likely") else None, "how": how,
                  "why": best[2] if best else "nobody here like them", "candidates": cands, "ai": None}
    return out


def _their_relatives(data: dict) -> dict[str, list[tuple[str, str]]]:
    """GEDCOM person -> [(kind, other GEDCOM person)]: their parents, partners and children."""
    out = defaultdict(list)
    for fam in data["families"].values():
        for p in fam["partners"]:
            out[p] += [("partners", q) for q in fam["partners"] if q != p] + [("children", c) for c in fam["children"]]
        for c in fam["children"]:
            out[c] += [("parents", p) for p in fam["partners"]]
    return out


def _our_relatives(g: family.Graph, pid: int, kind: str) -> set[int]:
    if kind == "parents":
        return set(g.parents.get(pid, ()))
    if kind == "children":
        return set(g.children.get(pid, ()))
    return set(g.partners.get(pid, {}))


# ---- 3. asking the AI ----------------------------------------------------------------------------------------------

def ask_ai(con: sqlite3.Connection, cfg: Config, data: dict, matches: dict, limit: int = 20) -> int:
    """Ask Ollama about up to `limit` unsure matches not asked about yet. Returns how many it answered."""
    from lineagebox.llm import Ollama, OllamaError

    client, g, done = Ollama(cfg), family.load(con), 0
    for x, m in matches.items():
        if done >= limit:
            break
        if m["how"] != "unsure" or m["ai"] or not m["candidates"]:
            continue
        pid = m["candidates"][0][0]
        prompt = (f"In the GEDCOM file:\n{_describe_theirs(data, x)}\n\nIn the catalog:\n{_describe_ours(con, g, pid)}")
        try:
            answer, _ = client.chat_json(AI_SYSTEM, prompt, AI_SCHEMA, temperature=0.0, max_tokens=300)
        except (OllamaError, ValueError, httpx.HTTPError) as e:  # an error, or Ollama is away: ask again later
            m["ai"] = {"error": f"{type(e).__name__}: {e}"[:200]}
            break
        same, confidence = bool(answer.get("same")), float(answer.get("confidence") or 0)
        m["ai"] = {"same": same, "confidence": round(confidence, 2), "reason": str(answer.get("reason", ""))[:300],
                   "about": pid}
        if same and confidence >= 0.7:
            m["choice"], m["how"] = pid, "ai"
        done += 1
    return done


def _describe_theirs(data: dict, x: str) -> str:
    p, fams = data["people"][x], data["families"]
    name = lambda y: data["people"].get(y, {}).get("names", [{"full": "?"}])[0]["full"]
    lines = [f"Name: {p['names'][0]['full']}"]
    if len(p["names"]) > 1:
        lines.append("Other names: " + ", ".join(n["full"] for n in p["names"][1:]))
    lines.append(f"Sex: {p['sex'] or 'unknown'}")
    for e in p["events"]:
        lines.append(f"{events.PERSON_EVENTS.get(e['tag'], e['tag'])}: {e['date'] or ''} {e['place'] or ''}".strip())
    parents = [name(q) for f, _ in p["famc"] for q in fams.get(f, {}).get("partners", [])]
    partners = [name(q) for f in p["fams"] for q in fams.get(f, {}).get("partners", []) if q != x]
    children = [name(c) for f in p["fams"] for c in fams.get(f, {}).get("children", [])]
    for label, names in (("Parents", parents), ("Partners", partners), ("Children", children)):
        if names:
            lines.append(f"{label}: {', '.join(names)}")
    return "\n".join(lines)


def _describe_ours(con: sqlite3.Connection, g: family.Graph, pid: int) -> str:
    lines = [f"Name: {g.name(pid)}"]
    others = [r[0] for r in con.execute("SELECT name FROM person_name WHERE person_id = ?", (pid,))]
    if others:
        lines.append("Other names: " + ", ".join(others))
    lines.append(f"Sex: {g.sex(pid) or 'unknown'}")
    for e in events.for_person(con, pid):
        if e["person_id"] == pid:
            lines.append(f"{events.PERSON_EVENTS.get(e['tag'], e['tag'])}: {e['date'] or ''} {e['place'] or ''}".strip())
    for label, ids in (("Parents", g.parents.get(pid, ())), ("Partners", g.partners.get(pid, {})),
                       ("Children", g.children.get(pid, ()))):
        if ids:
            lines.append(f"{label}: {', '.join(g.name(q) for q in ids)}")
    return "\n".join(lines)


def review_rows(con: sqlite3.Connection, data: dict, matches: dict) -> list[dict]:
    """The import page's rows, the ones to look at first on top: unsure, then new, likely, the AI's, and the sure."""
    names = dict(con.execute("SELECT id, name FROM person"))
    born_here = dict(con.execute("""SELECT person_id, substr(min(sort_date), 1, 4) FROM event
                                    WHERE tag = 'BIRT' AND sort_date IS NOT NULL GROUP BY person_id"""))
    fams = data["families"]
    their = lambda y: data["people"].get(y, {}).get("names", [{"full": "?"}])[0]["full"]
    order = {"unsure": 0, "new": 1, "likely": 2, "ai": 3, "chosen": 4, "sure": 5, "uid": 6}
    rows = []
    for x, p in data["people"].items():
        m = matches[x]
        born = next((e["date"] for e in p["events"] if e["tag"] == "BIRT" and e["date"]), None)
        died = next((e["date"] for e in p["events"] if e["tag"] == "DEAT"), None)
        parents = [their(q) for f, _ in p["famc"] for q in fams.get(f, {}).get("partners", [])]
        cands = [{"id": pid, "name": names.get(pid, "?"), "born": born_here.get(pid), "score": score, "why": why}
                 for pid, score, why in m["candidates"] if pid in names]
        if m["choice"] and m["choice"] not in [c["id"] for c in cands] and m["choice"] in names:
            cands.insert(0, {"id": m["choice"], "name": names[m["choice"]], "born": born_here.get(m["choice"]),
                             "score": None, "why": "chosen"})
        rows.append({"xref": x, "name": p["names"][0]["full"], "other_names": [n["full"] for n in p["names"][1:]],
                     "born": events.show_date(born) if born else "", "died": died is not None,
                     "died_on": events.show_date(died) if died and died != "Y" else "", "parents": parents,
                     "pet": p["pet"], "how": m["how"], "why": m["why"], "choice": m["choice"], "candidates": cands,
                     "ai": m["ai"]})
    return sorted(rows, key=lambda r: (order.get(r["how"], 9), r["name"].lower()))


# ---- stored imports ------------------------------------------------------------------------------------------------

def start(con: sqlite3.Connection, file_name: str, raw: bytes) -> int:
    data = read(raw)
    matches = match(con, data)
    import_id = con.execute("""INSERT INTO gedcom_import(file_name, created_at, data, matches) VALUES (?, ?, ?, ?)""",
                            (file_name[:200], _now(), json.dumps(data), json.dumps(matches))).lastrowid
    con.commit()
    return import_id


def load(con: sqlite3.Connection, import_id: int) -> tuple[sqlite3.Row, dict, dict] | None:
    row = con.execute("SELECT * FROM gedcom_import WHERE id = ?", (import_id,)).fetchone()
    return (row, json.loads(row["data"]), json.loads(row["matches"])) if row else None


def save_matches(con: sqlite3.Connection, import_id: int, matches: dict) -> None:
    con.execute("UPDATE gedcom_import SET matches = ? WHERE id = ?", (json.dumps(matches), import_id))
    con.commit()


def set_choices(con: sqlite3.Connection, import_id: int, choices: dict[str, str]) -> None:
    """The person's choices from the import page: GEDCOM id -> 'new', a person id, or a person's name. A name must be
    one person's (namesakes are told apart by picking from the suggestions, which sends the id): raises Problem,
    saving nothing, for a name that is nobody's or several people's, or someone merged or removed since."""
    _, _, matches = load(con, import_id)
    for x, value in choices.items():
        if x not in matches or not value:
            continue
        value = str(value).strip()
        if value == "new":
            matches[x]["choice"], matches[x]["how"] = None, "new"
        elif value.isdecimal():
            if not con.execute("SELECT 1 FROM person WHERE id = ?", (int(value),)).fetchone():
                raise Problem("Someone picked was merged or removed since the page was opened: reload it and pick "
                              "again.")
            if matches[x]["choice"] != int(value):
                matches[x]["choice"], matches[x]["how"] = int(value), "chosen"
        else:
            found = [r[0] for r in con.execute("SELECT id FROM person WHERE lower(name) = lower(?)", (value,))]
            if len(found) != 1:
                raise Problem(f"{len(found)} people here are called {value}: pick one from the suggestions as you "
                              f"type." if found else f"Nobody here is called {value}.")
            matches[x]["choice"], matches[x]["how"] = found[0], "chosen"
    save_matches(con, import_id, matches)


# ---- 5. applying ---------------------------------------------------------------------------------------------------

def apply(con: sqlite3.Connection, import_id: int) -> dict:
    """Add what the file has that the catalog doesn't. Returns the report (also kept with the import)."""
    row, data, matches = load(con, import_id)
    if row["applied_at"]:
        return json.loads(row["report"])
    here = {r[0] for r in con.execute("SELECT id FROM person")}
    gone = sorted(data["people"][x]["names"][0]["full"] for x, m in matches.items()
                  if m.get("choice") and m["choice"] not in here)
    if gone:  # merged into someone else, or removed, while the import was being looked over
        raise Problem(f"{', '.join(gone[:5])}{' and others' if len(gone) > 5 else ''}: matched to someone merged or "
                      f"removed since. Pick again, save, then bring it in.")
    report = {"new": 0, "matched": 0, "names": 0, "events": 0, "links": 0, "conflicts": [], "skipped": []}
    pid_of: dict[str, int] = {}
    for x, p in data["people"].items():
        chosen = matches.get(x, {}).get("choice")
        pid_of[x] = chosen if chosen else _new_person(con, p)
        report["matched" if chosen else "new"] += 1
        _merge_person(con, pid_of[x], p, report, matched=bool(chosen))
    for x, p in data["people"].items():
        for other, word in p["asso"]:
            if other in pid_of and pid_of[other] != pid_of[x]:
                con.execute("INSERT OR IGNORE INTO relation(person_id, of_id, kind) VALUES (?, ?, ?)",
                            (pid_of[other], pid_of[x], word))
    for fam in data["families"].values():
        _merge_family(con, fam, pid_of, data, report)
    con.execute("UPDATE gedcom_import SET applied_at = ?, report = ? WHERE id = ?",
                (_now(), json.dumps(report), import_id))
    con.commit()
    return report


def _new_person(con, p: dict) -> int:
    first = p["names"][0]
    name = first["full"] or "?"
    born = _birth_year(p["events"])
    if con.execute("SELECT 1 FROM person WHERE lower(name) = lower(?)", (name,)).fetchone():
        name = f"{name} ({born or 'imported'})"  # someone else here has the name: told apart, renamable later
        n = 2
        while con.execute("SELECT 1 FROM person WHERE lower(name) = lower(?)", (name,)).fetchone():
            name, n = f"{first['full']} ({born or 'imported'} {n})", n + 1
    uid = p["uid"] if p["uid"] and not con.execute("SELECT 1 FROM person WHERE uid = ?", (p["uid"],)).fetchone() else None
    return con.execute("""INSERT INTO person(name, uid, given_name, surname, name_type, names_checked, sex)
                          VALUES (?, ?, ?, ?, ?, 1, ?)""",
                       (name, uid or str(uuid.uuid4()), first["given"] or None, first["surname"],
                        first["type"] if first["type"] in ("birth", "married", "aka") else "birth", p["sex"])).lastrowid


def _merge_person(con, pid: int, p: dict, report: dict, matched: bool) -> None:
    here = con.execute("SELECT * FROM person WHERE id = ?", (pid,)).fetchone()
    if matched and p["uid"] and p["uid"] != here["uid"]:
        con.execute("INSERT OR IGNORE INTO external_id(uid, person_id) VALUES (?, ?)", (p["uid"], pid))
    first = p["names"][0]
    if matched and not here["names_checked"] and first["given"]:
        con.execute("UPDATE person SET given_name = ?, surname = ? WHERE id = ?", (first["given"], first["surname"], pid))
    for n in p["names"] if matched else p["names"][1:]:
        if n["full"] and _norm(n["full"]) != _norm(here["name"]):
            kind = {"maiden": "birth", "": "aka"}.get(n["type"], n["type"])
            if con.execute("INSERT OR IGNORE INTO person_name(person_id, name, given_name, surname, type) "
                           "VALUES (?, ?, ?, ?, ?)", (pid, n["full"], n["given"], n["surname"], kind)).rowcount:
                report["names"] += 1
    if p["sex"] and here["sex"] and p["sex"] != here["sex"]:
        report["conflicts"].append(f"{here['name']}: the file says {p['sex']}, here {here['sex']} (kept)")
    elif p["sex"] and not here["sex"]:
        con.execute("UPDATE person SET sex = ? WHERE id = ?", (p["sex"], pid))
    for e in p["events"]:
        _merge_event(con, e, report, here["name"], person_id=pid)
    if p["note"] and not here["notes"]:
        con.execute("UPDATE person SET notes = ? WHERE id = ?", (p["note"], pid))
    for group in p["groups"]:
        people.add_to_group(con, pid, group)
    events._sync_person(con, pid)


def _merge_event(con, e: dict, report: dict, who: str, person_id: int | None = None,
                 couple: tuple[int, int] | None = None) -> None:
    where = "person_id = ?" if person_id is not None else "couple_a = ? AND couple_b = ?"
    key = (person_id,) if person_id is not None else (min(couple), max(couple))
    same = con.execute(f"SELECT * FROM event WHERE {where} AND tag = ? AND coalesce(type, '') = ?",
                       (*key, e["tag"], e["type"] or "")).fetchall()
    target = next((r for r in same if r["date"] == e["date"]), None)
    if target is None and same and (e["tag"] in events.ONE_EACH or e["tag"] in ("MARR", "DIV")):
        target = same[0]  # one of these per person or couple
        if e["date"] and target["date"]:
            label = (events.PERSON_EVENTS if person_id is not None else events.COUPLE_EVENTS).get(e["tag"], e["tag"])
            report["conflicts"].append(f"{who}: {label.lower()} {events.show_date(e['date'])} in the file, "
                                       f"{events.show_date(target['date'])} here (kept)")
            return
    if target is not None:  # already here: fill in what it lacks
        if (e["date"] and not target["date"]) or (e["place"] and not target["place"]):
            con.execute("UPDATE event SET date = coalesce(date, ?), sort_date = coalesce(sort_date, ?), "
                        "place = coalesce(place, ?) WHERE id = ?", (e["date"], e["sort"], e["place"], target["id"]))
            report["events"] += 1
        return
    a, b = (min(couple), max(couple)) if couple else (None, None)
    con.execute("""INSERT INTO event(person_id, couple_a, couple_b, tag, type, date, sort_date, place, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (person_id, a, b, e["tag"], e["type"], e["date"], e["sort"], e["place"], e["note"]))
    report["events"] += 1


def _merge_family(con, fam: dict, pid_of: dict, data: dict, report: dict) -> None:
    partners = [pid_of[x] for x in fam["partners"] if x in pid_of]
    kids = [x for x in fam["children"] if x in pid_of]
    names = lambda ids: " and ".join(con.execute("SELECT name FROM person WHERE id = ?", (i,)).fetchone()[0] for i in ids)
    if len(partners) == 2 and partners[0] != partners[1]:
        a, b = sorted(partners)
        tags = {e["tag"] for e in fam["events"]}
        row = con.execute("SELECT * FROM partner WHERE a = ? AND b = ?", (a, b)).fetchone()
        if row is None:
            current = "DIV" not in tags
            if current:
                try:
                    family._no_other_current_partner(con, a, b)
                    family._no_other_current_partner(con, b, a)
                except family.Problem:
                    current = False
                    report["conflicts"].append(f"{names((a, b))}: added as former partners, since one of them has "
                                               f"another current partner here")
            con.execute("INSERT INTO partner(a, b, current, married, uid) VALUES (?, ?, ?, ?, ?)",
                        (a, b, int(current), int(bool(tags & {"MARR", "DIV"})),
                         fam["uid"] if fam["uid"] and not con.execute("SELECT 1 FROM partner WHERE uid = ?",
                                                                      (fam["uid"],)).fetchone()
                         else str(uuid.uuid4())))
            report["links"] += 1
        for e in fam["events"]:
            if e["tag"] in ("MARR", "DIV") and not (e["date"] or e["place"] or e["note"]):
                continue  # "they married", "they divorced": what the couple's married and former already say
            _merge_event(con, e, report, names((a, b)), couple=(a, b))
    for x in kids:
        child = pid_of[x]
        pedi = next((how for f, how in data["people"][x]["famc"] if data["families"].get(f) is fam), "birth")
        for parent in partners:
            if parent == child or con.execute("SELECT 1 FROM parent WHERE child_id = ? AND parent_id = ?",
                                              (child, parent)).fetchone():
                continue
            try:
                family._add_parent(con, child, parent)
                con.execute("UPDATE parent SET pedi = ? WHERE child_id = ? AND parent_id = ?",
                            (pedi if pedi in ("birth", "adopted", "foster") else "birth", child, parent))
                report["links"] += 1
            except family.Problem as e:
                report["skipped"].append(str(e))
    if not partners and len(kids) > 1:  # siblings whose parents aren't known
        for i, x in enumerate(kids):
            for y in kids[i + 1:]:
                a, b = sorted((pid_of[x], pid_of[y]))
                if a != b and con.execute("INSERT OR IGNORE INTO sibling(a, b) VALUES (?, ?)", (a, b)).rowcount:
                    report["links"] += 1


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
