"""Albums picked by hand, favourites, and tours and albums saved as one video file.

- album: an ordered set of photos with a cover and a few words about it, made by an account (NULL: the admins', or
  anyone's at home without family accounts). album_photo holds its photos in their order, each with the album's own
  caption for it (where it has none, the photo's family caption shows).
- album_passed: of a shared album's photos, those its maker may pass on (their own part of the family and uploads, not
  what others shared with them), kept whenever it is changed, shared or opened: what the others may see. As tour_photo.
- share gains album_id: an album is shared like a tour. The table's CHECK says which one thing a share is of, and
  SQLite can't change a CHECK, so the table is built again with every row and id kept. Nothing refers to share, so its
  rows can go with the old table without foreign keys being switched off.
- favourite: a heart on a photo, one per account (NULL: given at home, without family accounts). Tours and memories
  prefer the photos with one.
- slideshow_video: a tour or an album someone asked to have as one video file to send. The worker makes it, in the
  data folder, from the slides as that person saw them (slideshow.py).
"""

TABLES = """
CREATE TABLE album (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  about      TEXT NOT NULL DEFAULT '',                                  -- a few words under its name
  cover_id   INTEGER REFERENCES image(id) ON DELETE SET NULL,           -- NULL: its first photo
  account_id INTEGER REFERENCES account(id) ON DELETE CASCADE,          -- who made it; NULL: the admins'
  created_at TEXT NOT NULL,
  changed_at TEXT NOT NULL
);
CREATE INDEX album_account ON album(account_id);
CREATE TABLE album_photo (
  album_id INTEGER NOT NULL REFERENCES album(id) ON DELETE CASCADE,
  image_id INTEGER NOT NULL REFERENCES image(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,                                            -- its place in the album, from 0
  caption  TEXT NOT NULL DEFAULT '',                                    -- the album's words for it
  added_at TEXT NOT NULL,
  PRIMARY KEY (album_id, image_id)
) WITHOUT ROWID;
CREATE INDEX album_photo_image ON album_photo(image_id);
CREATE TABLE album_passed (
  album_id INTEGER NOT NULL REFERENCES album(id) ON DELETE CASCADE,
  image_id INTEGER NOT NULL REFERENCES image(id) ON DELETE CASCADE,
  PRIMARY KEY (album_id, image_id)
) WITHOUT ROWID;
CREATE TABLE favourite (
  id         INTEGER PRIMARY KEY,
  account_id INTEGER REFERENCES account(id) ON DELETE CASCADE,          -- NULL: given at home, without accounts
  image_id   INTEGER NOT NULL REFERENCES image(id) ON DELETE CASCADE,
  at         TEXT NOT NULL
);
CREATE UNIQUE INDEX favourite_once ON favourite(image_id, coalesce(account_id, 0));
CREATE INDEX favourite_account ON favourite(account_id);
CREATE TABLE slideshow_video (
  id          INTEGER PRIMARY KEY,
  account_id  INTEGER REFERENCES account(id) ON DELETE CASCADE,         -- who asked; NULL without family accounts
  tour_id     INTEGER REFERENCES tour(id) ON DELETE CASCADE,
  album_id    INTEGER REFERENCES album(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  slides      TEXT NOT NULL,                                            -- JSON: what to show, as they saw it
  seconds     INTEGER NOT NULL,                                         -- each photo's
  state       TEXT NOT NULL DEFAULT 'waiting' CHECK (state IN ('waiting', 'making', 'done', 'failed')),
  done        INTEGER NOT NULL DEFAULT 0,                               -- slides made so far
  total       INTEGER NOT NULL,
  message     TEXT,                                                     -- why it failed
  file        TEXT,                                                     -- its name in the data folder's videos/
  bytes       INTEGER,
  asked_at    TEXT NOT NULL,
  started_at  TEXT,
  finished_at TEXT,
  CHECK ((tour_id IS NOT NULL) + (album_id IS NOT NULL) = 1)
);
CREATE INDEX slideshow_video_state ON slideshow_video(state, asked_at);
"""

SHARE = """
CREATE TABLE share_new (
  id         INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,  -- who shared it
  folder_id  INTEGER REFERENCES folder(id) ON DELETE CASCADE,
  image_id   INTEGER REFERENCES image(id) ON DELETE CASCADE,
  tour_id    INTEGER REFERENCES tour(id) ON DELETE CASCADE,
  album_id   INTEGER REFERENCES album(id) ON DELETE CASCADE,
  with_id    INTEGER REFERENCES account(id) ON DELETE CASCADE,          -- NULL: everyone with an account
  created_at TEXT NOT NULL,
  CHECK ((folder_id IS NOT NULL) + (image_id IS NOT NULL) + (tour_id IS NOT NULL) + (album_id IS NOT NULL) = 1)
);
INSERT INTO share_new(id, account_id, folder_id, image_id, tour_id, with_id, created_at)
  SELECT id, account_id, folder_id, image_id, tour_id, with_id, created_at FROM share;
DROP TABLE share;
ALTER TABLE share_new RENAME TO share;
CREATE UNIQUE INDEX share_once ON share(coalesce(folder_id, 0), coalesce(image_id, 0), coalesce(tour_id, 0),
                                        coalesce(album_id, 0), coalesce(with_id, 0));
CREATE INDEX share_with ON share(with_id);
CREATE INDEX share_by ON share(account_id);
"""

WAS = ["id", "account_id", "folder_id", "image_id", "tour_id", "with_id", "created_at"]  # share, from migration 019


def upgrade(con, cfg):
    columns = [r[1] for r in con.execute("PRAGMA table_info(share)")]
    if columns != WAS:  # another change to share since 019 would be lost by building it again as below
        raise RuntimeError(f"share's columns are not as expected: {columns}")
    for statement in _statements(TABLES + SHARE):
        con.execute(statement)


def _statements(sql: str) -> list[str]:
    """The script's statements (none of them has a ';' inside it), without the comments."""
    lines = [line.split("--", 1)[0] for line in sql.splitlines()]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]
