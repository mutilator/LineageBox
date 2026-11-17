-- Settings → Reorganize folders, two more things a reorganization can do when it is done: take away every empty
-- folder, also those that were empty before (reorganize.take_away_empty), and delete what other programs leave in
-- photo folders that holds nothing of the family's (Thumbs.db, .DS_Store: reorganize.JUNK_FILES), rather than move it
-- to Other files. Each says how many it did.
ALTER TABLE reorganize_run ADD COLUMN remove_empty INTEGER NOT NULL DEFAULT 0;
ALTER TABLE reorganize_run ADD COLUMN emptied INTEGER NOT NULL DEFAULT 0;      -- folders it took away
ALTER TABLE reorganize_run ADD COLUMN remove_junk INTEGER NOT NULL DEFAULT 0;
ALTER TABLE reorganize_run ADD COLUMN junk INTEGER NOT NULL DEFAULT 0;         -- files it deleted as junk
