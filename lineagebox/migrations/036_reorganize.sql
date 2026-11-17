-- Settings → Watched folders → Reorganize folders (reorganize.py): LineageBox moves the photo folders' files into
-- year and month folders itself, so the catalog follows each file at once.
--
-- The folder a file was in before it was moved there: what that folder's name said (a date, an event, a place, who is
-- in it), what an admin gave it by hand, and hiding or sharing it, still go for the file (catalog.ORIGIN). Set once,
-- by the first reorganization that moves the file, and kept by later ones.
ALTER TABLE file ADD COLUMN from_folder_id INTEGER REFERENCES folder(id);
CREATE INDEX file_from_folder ON file(from_folder_id);

-- Each reorganization, and every file it moved, so that it can be put back as it was.
CREATE TABLE reorganize_run (
  id          INTEGER PRIMARY KEY,
  started_at  TEXT NOT NULL,
  by_type     INTEGER NOT NULL,                                  -- Photos, Scans, Videos, Recordings at the top
  account_id  INTEGER REFERENCES account(id) ON DELETE SET NULL,  -- who started it; NULL without family accounts
  state       TEXT NOT NULL,                                     -- moving|done|stopped|undoing|undone
  planned     INTEGER NOT NULL DEFAULT 0,                        -- files it set out to move
  moved       INTEGER NOT NULL DEFAULT 0,                        -- files moved so far (put back, while undoing)
  problems    TEXT,                                              -- JSON: the first files it couldn't move, and why
  beat_at     TEXT,                                              -- last sign of life while moving or undoing
  finished_at TEXT,
  undone_at   TEXT
);
CREATE TABLE reorganize_move (
  run_id    INTEGER NOT NULL REFERENCES reorganize_run(id) ON DELETE CASCADE,
  seq       INTEGER NOT NULL,          -- the order they were moved in; put back in the opposite order
  file_id   INTEGER,                   -- the catalog's file, or NULL for one it doesn't list (a sidecar, other files)
  old_path  TEXT NOT NULL,
  new_path  TEXT NOT NULL,
  old_from  INTEGER,                   -- the file's from_folder_id before
  PRIMARY KEY (run_id, seq)
) WITHOUT ROWID;
