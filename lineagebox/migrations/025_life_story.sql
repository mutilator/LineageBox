-- A person's life story and a deeper family tree (person page, family.py, documents.py, gedcom*.py).
--
-- The life story the family writes about someone. person.notes stays what it was: the sources the family reader
-- adds ("Obituary: <link>") and the notes other trees bring in. person.cover_face_id, there since the start and unused
-- until now, is the portrait someone chose from the faces named as them (catalog.people shows it while it still is).
ALTER TABLE person ADD COLUMN biography TEXT;

-- Written sources: a book, a website, a census page, a parish register (a photo or scan of a record is a document,
-- in the document table). Each is cited by the people and life events it bears out, with where in it: "page 12",
-- "line 4", the record's own address. GEDCOM's SOUR records and the citations that point at them.
CREATE TABLE source (
  id          INTEGER PRIMARY KEY,
  title       TEXT NOT NULL,
  author      TEXT,
  publication TEXT,                                               -- who published it, where and when
  note        TEXT,                                               -- what it says, copied out, or anything about it
  uid         TEXT UNIQUE,                                        -- GEDCOM UID: a tree brought in again finds it
  added_by    INTEGER REFERENCES account(id) ON DELETE SET NULL,  -- the member who added it (NULL: an admin, home)
  created_at  TEXT NOT NULL
);
CREATE TABLE source_citation (
  id        INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES source(id) ON DELETE CASCADE,
  person_id INTEGER REFERENCES person(id) ON DELETE CASCADE,
  event_id  INTEGER REFERENCES event(id) ON DELETE CASCADE,
  page      TEXT,
  CHECK ((person_id IS NULL) != (event_id IS NULL))
);
CREATE INDEX source_citation_source ON source_citation(source_id);
CREATE INDEX source_citation_person ON source_citation(person_id);
CREATE INDEX source_citation_event ON source_citation(event_id);

-- Photos and files a family tree brought in names for someone, or for a source, that aren't in the library (GEDCOM
-- OBJE): kept, so the person's page lists them and an export carries them on to the next program.
CREATE TABLE tree_media (
  id        INTEGER PRIMARY KEY,
  person_id INTEGER REFERENCES person(id) ON DELETE CASCADE,
  source_id INTEGER REFERENCES source(id) ON DELETE CASCADE,
  file      TEXT NOT NULL,                                        -- as the other program wrote it: a path or address
  form      TEXT,                                                 -- image/jpeg, jpg...
  title     TEXT,
  CHECK ((person_id IS NULL) != (source_id IS NULL))
);
CREATE UNIQUE INDEX tree_media_person ON tree_media(person_id, file) WHERE person_id IS NOT NULL;
CREATE UNIQUE INDEX tree_media_source ON tree_media(source_id, file) WHERE source_id IS NOT NULL;
