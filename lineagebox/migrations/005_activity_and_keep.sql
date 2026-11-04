-- What the worker has been doing, for the Status page; photos and folder pairs the user chose to keep, which cleanup
-- never suggests again; and the index that finding photos to describe needs.
CREATE TABLE activity (
  id      INTEGER PRIMARY KEY,
  at      TEXT NOT NULL,                    -- local time, ISO-8601
  message TEXT NOT NULL
);
ALTER TABLE image ADD COLUMN kept_at TEXT;  -- "Keep" on a cleanup page: off the blurry, papers... and similar lists
CREATE TABLE kept_copies (                  -- "Keep both" on Duplicate copies: this pair of folders is not listed
  folder_a INTEGER NOT NULL REFERENCES folder(id),  -- the smaller id
  folder_b INTEGER NOT NULL REFERENCES folder(id),
  kept_at  TEXT NOT NULL,
  PRIMARY KEY (folder_a, folder_b)
);
CREATE INDEX image_group ON image(dup_group_id) WHERE dup_group_id IS NOT NULL;
