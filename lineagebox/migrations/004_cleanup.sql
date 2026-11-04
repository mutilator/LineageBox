-- Cleanup: files moved to the Removed folder next to the library (restorable), and a sharpness score per image.
ALTER TABLE file ADD COLUMN removed_at TEXT;        -- when cleanup moved it out of the library (it is also missing)
ALTER TABLE file ADD COLUMN removed_path TEXT;      -- where it is now, under the Removed folder ('/' separated)
ALTER TABLE file ADD COLUMN removed_reason TEXT;    -- shown on the Removed page
ALTER TABLE file ADD COLUMN removed_batch INTEGER;  -- one cleanup action; restored together
CREATE INDEX file_removed ON file(removed_batch) WHERE removed_at IS NOT NULL;
ALTER TABLE image ADD COLUMN sharpness REAL;        -- see quality.py; -1 when the preview could not be read
