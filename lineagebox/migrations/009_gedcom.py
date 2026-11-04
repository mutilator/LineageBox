"""The family as GEDCOM keeps it (see events.py and gedcom.py).

New: a lasting id for each person and couple (GEDCOM's UID: what a re-import matches on), name parts (given name,
surname, what kind of name it is) and other names, how a child belongs to a parent (birth, adopted, foster), and
events with GEDCOM dates and places (births, deaths, marriages, divorces...). Also: groups of people who aren't family
(Teachers), other trees' UIDs for people merged from them, documents and what they are records of, places found on the
map, and GEDCOM imports being checked.

What was entered before is carried over as it was meant, by these rules:
- a birth date becomes a birth event, "passed away" a death with no date;
- every parent link is by birth;
- a sibling entered by word, whose sibling has parents, gets those parents (both are by birth); the sibling link is
  then not needed;
- a family word entered as a plain relationship ("grandmother" before that was refused) gives the sex it says, and
  goes when the family links say the same; pets stay as they are;
- the sex husband, wife, son or daughter implies: the opposite of a known spouse's; a couple of whom neither is known
  takes the face analysis's word for one of them;
- names are split at the last word (with particles: Van Dyke) for the user to check; the surname is a married name
  for someone with a spouse, else a birth name.
"""
import uuid
from collections import defaultdict

SCHEMA = [
    "ALTER TABLE person ADD COLUMN uid TEXT",
    "ALTER TABLE person ADD COLUMN given_name TEXT",
    "ALTER TABLE person ADD COLUMN surname TEXT",
    "ALTER TABLE person ADD COLUMN name_type TEXT",  # birth|married|aka: the main name's kind (GEDCOM NAME.TYPE)
    "ALTER TABLE person ADD COLUMN names_checked INTEGER NOT NULL DEFAULT 0",  # 1: someone confirmed the split
    "CREATE UNIQUE INDEX person_uid ON person(uid)",
    """CREATE TABLE person_name (                  -- other names: birth (maiden), married, also known as, nickname
         id         INTEGER PRIMARY KEY,
         person_id  INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
         name       TEXT NOT NULL,
         given_name TEXT,
         surname    TEXT,
         type       TEXT NOT NULL,
         UNIQUE (person_id, name, type))""",
    "ALTER TABLE parent ADD COLUMN pedi TEXT NOT NULL DEFAULT 'birth'",  # birth|adopted|foster (GEDCOM FAMC.PEDI)
    "ALTER TABLE partner ADD COLUMN uid TEXT",
    """CREATE TABLE event (                        -- a person's (BIRT, DEAT...) or a couple's (MARR, DIV...)
         id        INTEGER PRIMARY KEY,
         person_id INTEGER REFERENCES person(id) ON DELETE CASCADE,
         couple_a  INTEGER,
         couple_b  INTEGER,
         tag       TEXT NOT NULL,                  -- GEDCOM tag
         type      TEXT,                           -- what an EVEN was ("moved to Oregon")
         date      TEXT,                           -- GEDCOM date value: "12 JUN 1984", "ABT 1950"; NULL unknown
         sort_date TEXT,                           -- ISO, as much as is known: "1984-06-12", "1950"
         place     TEXT,
         note      TEXT,
         FOREIGN KEY (couple_a, couple_b) REFERENCES partner(a, b) ON DELETE CASCADE,
         CHECK ((person_id IS NULL) != (couple_a IS NULL)))""",
    "CREATE INDEX event_person ON event(person_id)",
    "CREATE INDEX event_couple ON event(couple_a, couple_b)",
    """CREATE TABLE person_group (                 -- people who aren't family, by what they are: Teachers, Foo Fighters
         person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
         name      TEXT NOT NULL,
         PRIMARY KEY (person_id, name))""",
    """CREATE TABLE external_id (                  -- another tree's UID for one of ours, from an import it was merged in
         uid       TEXT PRIMARY KEY,
         person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE)""",
    """CREATE TABLE document (                     -- a photo or scan that is a record: a certificate, an obituary...
         image_id   INTEGER PRIMARY KEY REFERENCES image(id) ON DELETE CASCADE,
         kind       TEXT NOT NULL,
         title      TEXT NOT NULL,
         created_at TEXT NOT NULL)""",
    """CREATE TABLE citation (                     -- what a document is a record of: a person, or one of their events
         id        INTEGER PRIMARY KEY,
         image_id  INTEGER NOT NULL REFERENCES document(image_id) ON DELETE CASCADE,
         person_id INTEGER REFERENCES person(id) ON DELETE CASCADE,
         event_id  INTEGER REFERENCES event(id) ON DELETE CASCADE,
         CHECK ((person_id IS NULL) != (event_id IS NULL)))""",
    "CREATE INDEX citation_image ON citation(image_id)",
    """CREATE TABLE place (                        -- where a place name is on the map (folder places, event places)
         name        TEXT PRIMARY KEY,             -- as written: "Grand Canyon, Arizona"
         lat         REAL,
         lon         REAL,
         found_as    TEXT,                         -- what the map service called it
         source      TEXT NOT NULL,                -- osm (looked up) | user (moved or removed by hand)
         looked_up   TEXT NOT NULL)""",
    """CREATE TABLE gedcom_import (                -- a GEDCOM file being merged in: what it holds and what goes where
         id         INTEGER PRIMARY KEY,
         file_name  TEXT NOT NULL,
         created_at TEXT NOT NULL,
         data       TEXT NOT NULL,                 -- JSON: its people and families (gedcom_import.py)
         matches    TEXT NOT NULL,                 -- JSON: GEDCOM id -> what it is matched with, and why
         applied_at TEXT,
         report     TEXT)""",
]
FEMALE = ("mother", "sister", "daughter", "wife", "aunt", "niece")
MALE = ("father", "brother", "son", "husband", "uncle", "nephew")


def upgrade(con, cfg):
    from lineagebox import events, family

    for statement in SCHEMA:
        con.execute(statement)
    for (pid,) in con.execute("SELECT id FROM person").fetchall():
        con.execute("UPDATE person SET uid = ? WHERE id = ?", (str(uuid.uuid4()), pid))
    for a, b in con.execute("SELECT a, b FROM partner").fetchall():
        con.execute("UPDATE partner SET uid = ? WHERE a = ? AND b = ?", (str(uuid.uuid4()), a, b))

    for pid, born, deceased in con.execute("SELECT id, birth_date, deceased FROM person").fetchall():
        if born:
            d = events.parse_date(born)
            con.execute("INSERT INTO event(person_id, tag, date, sort_date) VALUES (?, 'BIRT', ?, ?)",
                        (pid, d.gedcom, d.sort))
        if deceased:
            con.execute("INSERT INTO event(person_id, tag) VALUES (?, 'DEAT')", (pid,))

    _siblings_get_parents(con)
    _family_words(con, family)
    _sex_from_spouses(con, family)

    for pid, name in con.execute("SELECT id, name FROM person").fetchall():
        given, surname = events.split_name(name)
        married = con.execute("SELECT 1 FROM partner WHERE ? IN (a, b) AND married", (pid,)).fetchone()
        con.execute("UPDATE person SET given_name = ?, surname = ?, name_type = ? WHERE id = ?",
                    (given, surname, "married" if married and surname else "birth", pid))


def _siblings_get_parents(con) -> None:
    parents = defaultdict(set)
    for child, parent in con.execute("SELECT child_id, parent_id FROM parent"):
        parents[child].add(parent)
    linked = defaultdict(set)
    for a, b in con.execute("SELECT a, b FROM sibling"):
        linked[a].add(b)
        linked[b].add(a)
    seen = set()
    for start in list(linked):
        if start in seen:
            continue
        group, todo = set(), [start]
        while todo:
            p = todo.pop()
            if p not in group:
                group.add(p)
                todo.extend(linked[p])
        seen |= group
        known = {frozenset(parents[p]) for p in group if parents[p]}
        if len(known) != 1:
            continue  # nobody's parents are entered, or they differ (half-siblings): left as it is
        shared = next(iter(known))
        for p in group:
            if not parents[p]:
                con.executemany("INSERT OR IGNORE INTO parent(child_id, parent_id, pedi) VALUES (?, ?, 'birth')",
                                [(p, q) for q in shared])
                parents[p] = set(shared)
        con.executemany("DELETE FROM sibling WHERE a = ? AND b = ?",
                        [(min(a, b), max(a, b)) for a in group for b in linked[a] if a < b])


def _family_words(con, family) -> None:
    rows = con.execute("SELECT person_id, of_id, kind FROM relation").fetchall()
    family_rows = []
    for pid, of, kind in rows:
        word = family.compact(kind)
        if not (word.startswith(("grand", "great", "step", "half")) or word.endswith("inlaw")
                or word in ("aunt", "uncle", "niece", "nephew", "cousin")):
            continue  # friend, pet...: not family
        family_rows.append((pid, of, kind))
        sex = "female" if word.endswith(FEMALE) else "male" if word.endswith(MALE) else None
        if sex:
            con.execute("UPDATE person SET sex = ? WHERE id = ? AND sex IS NULL", (sex, pid))
    g = family.load(con)
    for pid, of, kind in family_rows:
        kin = family.relations_to(g, of).get(pid)
        if kin and family.compact(kind) in kin.words:
            con.execute("DELETE FROM relation WHERE person_id = ? AND of_id = ? AND kind = ?", (pid, of, kind))


def _sex_from_spouses(con, family) -> None:
    couples = con.execute("SELECT a, b FROM partner WHERE married").fetchall()
    sex = dict(con.execute("SELECT id, sex FROM person"))
    opposite = {"male": "female", "female": "male"}
    changed = True
    while changed:
        changed = False
        for a, b in couples:
            for x, y in ((a, b), (b, a)):
                if sex.get(x) is None and sex.get(y) in opposite:
                    sex[x] = opposite[sex[y]]
                    changed = True
        if not changed:  # couples of whom neither is known: the faces decide one, if they agree
            for a, b in couples:
                if sex.get(a) is None and sex.get(b) is None:
                    for x, y in ((a, b), (b, a)):
                        if guess := family.suggested_sex(con, x):
                            sex[x], sex[y] = guess, opposite[guess]
                            changed = True
                            break
    for pid, value in sex.items():
        if value:
            con.execute("UPDATE person SET sex = ? WHERE id = ? AND sex IS NULL", (value, pid))
