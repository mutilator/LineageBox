-- Damaged files (check.py): when each file was last read whole and checked, and what is wrong with it if anything.
ALTER TABLE file ADD COLUMN checked_at TEXT;
ALTER TABLE file ADD COLUMN damage TEXT;  -- NULL: fine (or not checked yet); else what is wrong, in words
CREATE INDEX file_damage ON file(damage) WHERE damage IS NOT NULL;
