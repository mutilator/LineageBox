-- What an account shares with other accounts (sharing.py): a folder (everything in it and below, now and later), one
-- photo, or a tour (the photos it shows whoever made it). A member shares their own uploads and tours; an admin,
-- anything. tour.shared (migration 015, never set by the app) is no longer read: a tour is shared here instead.
CREATE TABLE share (
  id         INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,  -- who shared it
  folder_id  INTEGER REFERENCES folder(id) ON DELETE CASCADE,
  image_id   INTEGER REFERENCES image(id) ON DELETE CASCADE,
  tour_id    INTEGER REFERENCES tour(id) ON DELETE CASCADE,
  with_id    INTEGER REFERENCES account(id) ON DELETE CASCADE,          -- NULL: everyone with an account
  created_at TEXT NOT NULL,
  CHECK ((folder_id IS NOT NULL) + (image_id IS NOT NULL) + (tour_id IS NOT NULL) = 1)
);
CREATE UNIQUE INDEX share_once ON share(coalesce(folder_id, 0), coalesce(image_id, 0), coalesce(tour_id, 0),
                                        coalesce(with_id, 0));
CREATE INDEX share_with ON share(with_id);
CREATE INDEX share_by ON share(account_id);
-- The photos a shared tour showed whoever made it, the last time it was saved, shared or opened: what sharing it lets
-- the others see. A tour picks its photos again each time, so this follows it.
CREATE TABLE tour_photo (
  tour_id  INTEGER NOT NULL REFERENCES tour(id) ON DELETE CASCADE,
  image_id INTEGER NOT NULL REFERENCES image(id) ON DELETE CASCADE,
  PRIMARY KEY (tour_id, image_id)
) WITHOUT ROWID;
