-- Scans dated by the day they were scanned (scan_dates.py, Cleanup → Suggested dates): the dates the AI found written
-- on each, as it copied them, with what each is the date of, the document's own first: JSON [{"written", "what"}],
-- empty when it found none. One reading a scan; a new model or prompt (version) reads it again.
CREATE TABLE scan_reading (
  image_id INTEGER PRIMARY KEY REFERENCES image(id) ON DELETE CASCADE,
  model    TEXT NOT NULL,
  version  INTEGER NOT NULL,
  read_at  TEXT NOT NULL,
  dates    TEXT NOT NULL
);
