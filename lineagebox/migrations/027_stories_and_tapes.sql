-- Recorded stories (stories.py): a voice note someone records in the browser, or a sound file from their phone, about
-- a photo, or about a person or one of their life events. Older relatives tell instead of typing. The sound itself is
-- a file in the data folder's stories/ (never on the photo share); Whisper, when it is on, writes down what is said.
-- Anyone who sees the photo or the person may record one; whoever recorded it, or an admin, may take it away, which
-- only hides it (removed_at) for a while, so Undo can bring it back; the worker deletes it for good later.
CREATE TABLE story (
  id            INTEGER PRIMARY KEY,
  image_id      INTEGER REFERENCES image(id) ON DELETE CASCADE,    -- a photo's story...
  person_id     INTEGER REFERENCES person(id) ON DELETE CASCADE,   -- ...or a person's,
  event_id      INTEGER REFERENCES event(id) ON DELETE SET NULL,   -- about one of their life events
  file          TEXT NOT NULL UNIQUE,                              -- in stories/: 'ab/ab12....webm'
  mime          TEXT NOT NULL,                                     -- what browsers are told it is
  bytes         INTEGER NOT NULL,
  seconds       REAL,                                              -- how long it is, when ffprobe could tell
  title         TEXT,                                              -- a few words typed with it ("How we met")
  account_id    INTEGER REFERENCES account(id) ON DELETE SET NULL, -- who recorded it; NULL without family accounts
  at            TEXT NOT NULL,
  removed_at    TEXT,                                              -- taken away: hidden, its file kept a while
  words         TEXT,                                              -- what is said, as someone wrote it or put it
                                                                   -- right; NULL: as Whisper heard it
  heard         TEXT,                                              -- what Whisper heard; NULL until it has, '' nothing
  segments      TEXT,                                              -- JSON [[start, end, text], ...] Whisper's lines
  raw           TEXT,                                              -- JSON: the service's whole answer
  model         TEXT,
  CHECK ((image_id IS NULL) != (person_id IS NULL)),
  CHECK (event_id IS NULL OR person_id IS NOT NULL)
);
CREATE INDEX story_image ON story(image_id) WHERE image_id IS NOT NULL;
CREATE INDEX story_person ON story(person_id) WHERE person_id IS NOT NULL;
CREATE INDEX story_unheard ON story(id) WHERE heard IS NULL AND removed_at IS NULL;
-- A photo's stories are found by search with its caption and comments (search.py): its index row goes out of date
-- whenever one is recorded, corrected or taken away, as for comments (023).
CREATE TRIGGER search_story_added AFTER INSERT ON story WHEN new.image_id IS NOT NULL BEGIN
  INSERT OR IGNORE INTO search_stale VALUES (new.image_id);
END;
CREATE TRIGGER search_story_changed AFTER UPDATE OF words, heard, title, removed_at, image_id ON story BEGIN
  INSERT OR IGNORE INTO search_stale SELECT new.image_id WHERE new.image_id IS NOT NULL;
  INSERT OR IGNORE INTO search_stale SELECT old.image_id WHERE old.image_id IS NOT NULL;
END;
CREATE TRIGGER search_story_gone AFTER DELETE ON story WHEN old.image_id IS NOT NULL BEGIN
  INSERT OR IGNORE INTO search_stale VALUES (old.image_id);
END;
-- Transcripts people corrected: edits holds each line as someone put it right, by the second it starts at
-- ({"12.4": "Happy birthday, Owen!"}; "" takes a line out). Filtering again (speech.refilter) or hearing it again
-- keeps them, and text holds the transcript with them.
ALTER TABLE transcript ADD COLUMN edits TEXT;
ALTER TABLE transcript ADD COLUMN edited_at TEXT;
-- Long videos' chapters (chapters.py): where a digitised tape's recordings start, found once per version of the rules.
-- chapters is JSON [[seconds, frame idx], ...] after the first chapter at 0 ([] when none were found); frames are
-- video_frame rows, taken at each chapter's start and to fill out long videos (video.frame_count).
CREATE TABLE video_chapters (
  image_id INTEGER PRIMARY KEY REFERENCES image(id) ON DELETE CASCADE,
  version  INTEGER NOT NULL,
  made_at  TEXT NOT NULL,
  chapters TEXT NOT NULL,
  error    TEXT
);
