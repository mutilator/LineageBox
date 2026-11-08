-- The family's own words on a photo: one caption, which anyone who can see the photo may write or change, and the
-- comments under it ("I remember this day"). Members' writes go through changes.tracking, so an admin can undo them.
-- A recorded story (a voice note) may later be another kind.
CREATE TABLE comment (
  id         INTEGER PRIMARY KEY,
  image_id   INTEGER NOT NULL REFERENCES image(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL CHECK (kind IN ('caption', 'comment')),
  text       TEXT NOT NULL,
  account_id INTEGER REFERENCES account(id) ON DELETE SET NULL,   -- who wrote it; NULL without family accounts
  at         TEXT NOT NULL,                                       -- when it was written
  edited_at  TEXT                                                 -- when it was last changed
);
CREATE UNIQUE INDEX comment_caption ON comment(image_id) WHERE kind = 'caption';
CREATE INDEX comment_image ON comment(image_id, at);
