-- What is new for each account (New for you, and admins' alerts), and device links for photo frames and TVs.
--
-- outbox: each event is written once, by the code that knows of it: a share, a "Who is this?", new photos, a comment
-- on someone's photo; for admins a damaged file, a disk nearly full, a service down, an update (outbox.py). Who may
-- see an event is checked again each time it is shown, against what they see then, never only when it was written.
-- Email or Web Push, should they come, would read it the same way, each with a mark of its own for what it has sent.
-- share_id and comment_id are not foreign keys: those tables may be built again by later migrations, which would
-- otherwise take these rows with them. An event whose share or comment is gone is simply not shown.
CREATE TABLE outbox (
  id         INTEGER PRIMARY KEY,
  at         TEXT NOT NULL,
  kind       TEXT NOT NULL,                                       -- share, who_is_this, new_photos, comment, alerts...
  admins     INTEGER NOT NULL DEFAULT 0,                          -- an alert, for admins only
  to_account INTEGER REFERENCES account(id) ON DELETE CASCADE,     -- for this account only; NULL: whoever may see it
  by_account INTEGER REFERENCES account(id) ON DELETE SET NULL,    -- who did it, who is never told of it
  image_id   INTEGER REFERENCES image(id) ON DELETE CASCADE,
  face_id    INTEGER REFERENCES face(id) ON DELETE CASCADE,
  share_id   INTEGER,
  comment_id INTEGER,
  data       TEXT NOT NULL DEFAULT '{}',                           -- JSON: the rest of what its words need
  key        TEXT                                                  -- an alert's: the same one is told again only later
);
CREATE INDEX outbox_key ON outbox(key, at) WHERE key IS NOT NULL;
CREATE INDEX outbox_to ON outbox(to_account);
CREATE INDEX outbox_image ON outbox(image_id);
CREATE INDEX outbox_face ON outbox(face_id);
CREATE TABLE outbox_seen (
  account_id INTEGER PRIMARY KEY,                                 -- 0: the house's one list, without family accounts
  seen_id    INTEGER NOT NULL                                     -- the newest event they have seen
);

-- A device link: a photo frame, a TV or a Home Assistant dashboard that shows one view (memories as one account sees
-- them, or a tour) without anyone signing in on it. Only the token's SHA-256 is kept, as for sessions; revoking it
-- turns the link off at once. album_id is for an album, once there are albums (devices.VIEWS); not a foreign key, so
-- that this table doesn't depend on them.
CREATE TABLE device_link (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL,
  token_hash   TEXT NOT NULL UNIQUE,
  view         TEXT NOT NULL,                                     -- memories, tour (album: see devices.VIEWS)
  account_id   INTEGER REFERENCES account(id) ON DELETE CASCADE,   -- memories as this account sees them; NULL: all
  tour_id      INTEGER REFERENCES tour(id) ON DELETE CASCADE,
  album_id     INTEGER,
  home_only    INTEGER NOT NULL DEFAULT 1,                        -- answered on the home network only
  seconds      INTEGER NOT NULL DEFAULT 20,                       -- each photo is shown this long
  made_by      INTEGER REFERENCES account(id) ON DELETE SET NULL,
  made_at      TEXT NOT NULL,
  revoked_at   TEXT,
  last_seen_at TEXT,
  last_seen_ip TEXT
);

-- photos already in the catalog aren't news: only those added from now on are told of
INSERT OR REPLACE INTO settings(key, value) SELECT 'outbox_photos_after', CAST(coalesce(max(id), 0) AS TEXT) FROM image;
INSERT OR REPLACE INTO settings(key, value)
  SELECT 'outbox_damage_after', json_quote(coalesce(max(checked_at), '')) FROM file WHERE damage IS NOT NULL;
