-- Writing read into the family tree (family_reader.py): an obituary, an announcement, a letter or notes, typed or
-- pasted, or photos of it. The AI's reading and who is who are kept, so the changes it suggests can be looked over,
-- and made, a few at a time.
CREATE TABLE family_reading (
  id         INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  account_id INTEGER REFERENCES account(id) ON DELETE SET NULL,  -- who asked for it
  title      TEXT NOT NULL,                                      -- "Obituary of Peggy Miller", once read
  source     TEXT,                                               -- where it is from, as typed: a link, a newspaper
  image_id   INTEGER REFERENCES image(id) ON DELETE SET NULL,    -- the library's photo it was read from
  text       TEXT NOT NULL,                                      -- as typed, or as the AI copied it from the photos
  state      TEXT NOT NULL,                                      -- reading | ready | failed
  error      TEXT,
  found      TEXT,             -- JSON: the people the text names and what it says of each (family_reader.tidy)
  choices    TEXT,             -- JSON: who each one is in the tree, as matched and as picked
  report     TEXT,             -- JSON: what the last "Make these changes" did
  seconds    REAL,             -- how long the AI took
  applied_at TEXT
);
