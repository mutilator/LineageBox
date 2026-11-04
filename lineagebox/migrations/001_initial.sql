-- Initial schema. Paths are relative to library_root and use the OS separator; the root folder is ''.

CREATE TABLE folder (
  id          INTEGER PRIMARY KEY,
  path        TEXT NOT NULL UNIQUE,
  parent_id   INTEGER REFERENCES folder(id),
  name        TEXT NOT NULL,
  kind        TEXT,                          -- date|event|place|person|group_of_people|time_period|camera_or_device_dump|junk_or_generated|misc
  place       TEXT,
  place_type  TEXT,
  event       TEXT,
  people_hint TEXT,                          -- JSON array; hints only, never treated as identity
  year        INTEGER,
  month       INTEGER,
  day         INTEGER,
  ignored     INTEGER NOT NULL DEFAULT 0,    -- files are recorded but not ingested
  source      TEXT NOT NULL DEFAULT 'auto'   -- auto|user (user edits are never overwritten)
);

CREATE TABLE image (                         -- one row per unique content
  id              INTEGER PRIMARY KEY,
  sha256          TEXT NOT NULL UNIQUE,
  phash           INTEGER,                   -- 64-bit perceptual hash, stored signed
  dup_group_id    INTEGER,                   -- near-duplicate group
  format          TEXT NOT NULL,             -- jpeg|png|nef
  width           INTEGER,                   -- after EXIF rotation
  height          INTEGER,
  taken_at        TEXT,                      -- ISO-8601 local time, no zone
  taken_at_source TEXT,                      -- exif|folder|filename|mtime
  camera          TEXT,
  gps_lat         REAL,
  gps_lon         REAL,
  stage           INTEGER NOT NULL DEFAULT 0,-- bit flags: 1 ingest, 2 faces, 4 llm, 8 indexed
  error           TEXT                       -- decode failure; stage stays 1 so it is not retried
);

CREATE TABLE file (                          -- one row per path on the share
  id        INTEGER PRIMARY KEY,
  path      TEXT NOT NULL UNIQUE,
  folder_id INTEGER NOT NULL REFERENCES folder(id),
  image_id  INTEGER REFERENCES image(id),    -- NULL until ingested (or after the file changed)
  size      INTEGER NOT NULL,
  mtime     REAL NOT NULL,
  last_seen TEXT NOT NULL,
  missing   INTEGER NOT NULL DEFAULT 0,
  error     TEXT                             -- read failure; retried on the next ingest
);
CREATE INDEX file_image ON file(image_id);
CREATE INDEX file_folder ON file(folder_id);

CREATE TABLE person (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL UNIQUE,
  birth_date    TEXT,
  cover_face_id INTEGER,
  notes         TEXT
);

CREATE TABLE face (
  id           INTEGER PRIMARY KEY,
  image_id     INTEGER NOT NULL REFERENCES image(id),
  x1 REAL, y1 REAL, x2 REAL, y2 REAL,        -- normalized 0..1, in the EXIF-rotated image
  landmarks    BLOB,                         -- 5x2 float32
  det_score    REAL,
  quality      REAL,                         -- ArcFace embedding norm
  est_age      INTEGER,
  est_sex      TEXT,
  embedding    BLOB NOT NULL,                -- 512 x float32, L2-normalized
  cluster_id   INTEGER,
  person_id    INTEGER REFERENCES person(id),
  assigned_by  TEXT,                         -- user|auto|suggested|rejected|not_a_face
  assign_score REAL
);
CREATE INDEX face_image ON face(image_id);
CREATE INDEX face_person ON face(person_id);

CREATE TABLE analysis (
  image_id       INTEGER NOT NULL REFERENCES image(id),
  model          TEXT NOT NULL,
  prompt_version INTEGER NOT NULL,
  created_at     TEXT NOT NULL,
  caption        TEXT,
  setting        TEXT,
  indoor_outdoor TEXT,
  event          TEXT,
  photo_type     TEXT,
  time_of_day    TEXT,
  season         TEXT,
  people_count   INTEGER,
  text_visible   TEXT,
  quality_issues TEXT,                       -- JSON array
  raw_json       TEXT NOT NULL,
  PRIMARY KEY (image_id, model, prompt_version)
);

CREATE TABLE tag (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE image_tag (
  image_id INTEGER NOT NULL REFERENCES image(id),
  tag_id   INTEGER NOT NULL REFERENCES tag(id),
  source   TEXT NOT NULL,                    -- ai|folder|user
  PRIMARY KEY (image_id, tag_id)
);

CREATE VIRTUAL TABLE search_fts USING fts5(
  image_id UNINDEXED, caption, tags, people, place_event, folder_text, date_text,
  tokenize = 'porter unicode61'
);

CREATE TABLE settings (
  key   TEXT PRIMARY KEY,
  value TEXT
);
