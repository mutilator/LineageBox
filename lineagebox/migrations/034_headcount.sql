-- A careful count of the people in a photo (headcount.py): the AI asked on its own how many people it sees and how
-- many show their face, with how sure it was of each (0 to 1, from the chances Ollama gives each word of its answer;
-- NULL from an Ollama that gives none). Asked of the photos whose faces don't add up to their description's count,
-- for Cleanup's lists of faces missed; a new model or question (version) asks again.
CREATE TABLE headcount (
  image_id    INTEGER PRIMARY KEY REFERENCES image(id) ON DELETE CASCADE,
  model       TEXT NOT NULL,
  version     INTEGER NOT NULL,
  counted_at  TEXT NOT NULL,
  people      INTEGER,
  people_sure REAL,
  faces       INTEGER,
  faces_sure  REAL
);
