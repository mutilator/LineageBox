-- Family and other relationships between people, and photo tours.
-- Only facts are stored: who is whose parent, sibling (for siblings whose parents are not in the catalog) or partner.
-- family.py works out the rest (niece, brother-in-law, stepson, former sister-in-law) relative to the person marked
-- as "me" (settings key 'me').
ALTER TABLE person ADD COLUMN sex TEXT;                           -- male|female; NULL until someone says
ALTER TABLE person ADD COLUMN deceased INTEGER NOT NULL DEFAULT 0;
CREATE TABLE parent (
  child_id  INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  parent_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  PRIMARY KEY (child_id, parent_id)
);
CREATE INDEX parent_parent ON parent(parent_id);
CREATE TABLE sibling (                     -- siblings by word; sharing a parent makes siblings too
  a INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,  -- the smaller id
  b INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  PRIMARY KEY (a, b)
);
CREATE TABLE partner (
  a       INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,  -- the smaller id
  b       INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  current INTEGER NOT NULL DEFAULT 1,      -- 0: former (divorced, separated); one current partner per living person
  married INTEGER NOT NULL DEFAULT 1,      -- husband and wife, rather than partners
  PRIMARY KEY (a, b)
);
CREATE TABLE relation (                    -- not family: "Jake is Tom's friend"
  person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  of_id     INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  kind      TEXT NOT NULL,                 -- friend, coworker, neighbor... (lower case)
  PRIMARY KEY (person_id, of_id, kind)
);
CREATE INDEX relation_of ON relation(of_id);
CREATE TABLE tour_type (                   -- set up on the Settings page; the wizard asks for what a type leaves open
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  spec TEXT NOT NULL                       -- JSON, see tours.py
);
CREATE TABLE tour (
  id         INTEGER PRIMARY KEY,
  type_id    INTEGER REFERENCES tour_type(id) ON DELETE SET NULL,
  name       TEXT NOT NULL,
  spec       TEXT NOT NULL,                -- JSON: the type's spec with the wizard's answers filled in
  created_at TEXT NOT NULL
);
