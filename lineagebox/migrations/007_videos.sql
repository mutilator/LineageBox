-- Videos: one image row per unique video (media = 'video'), the frames sampled from it (faces and descriptions use
-- them), what was said in it, and whether browsers can play it as it is or need the 720p copy next to it.
ALTER TABLE image ADD COLUMN media TEXT NOT NULL DEFAULT 'photo';  -- photo|video
ALTER TABLE image ADD COLUMN duration REAL;                        -- videos: seconds
ALTER TABLE image ADD COLUMN codecs TEXT;                          -- videos: 'h264/aac', 'h264/no sound'
ALTER TABLE image ADD COLUMN playable TEXT;                        -- videos: original|copy|needed|failed: <why>
CREATE INDEX image_playable ON image(playable) WHERE playable IS NOT NULL;
CREATE TABLE video_frame (
  image_id INTEGER NOT NULL REFERENCES image(id),
  idx      INTEGER NOT NULL,                 -- frames/<sha>_<idx>.jpg
  at       REAL NOT NULL,                    -- seconds from the start
  PRIMARY KEY (image_id, idx)
);
ALTER TABLE face ADD COLUMN frame INTEGER;   -- videos: the video_frame idx the face is on; NULL on photos
CREATE TABLE transcript (                    -- what was said in a video, from the Whisper service
  image_id   INTEGER PRIMARY KEY REFERENCES image(id),
  model      TEXT NOT NULL,
  version    INTEGER NOT NULL,               -- speech.VERSION: the filter rules applied to the segments
  created_at TEXT NOT NULL,
  text       TEXT NOT NULL,                  -- the kept segments, joined; '' when nothing was said
  segments   TEXT NOT NULL,                  -- JSON [[start, end, text], ...]: the kept segments
  raw        TEXT NOT NULL                   -- JSON: every segment the service returned, to filter again later
);
-- FTS5 tables cannot gain a column, so the search index is made again with one for speech. The worker's next pass
-- fills it again (photos are added to the search as they are found missing from it).
DROP TABLE search_fts;
CREATE VIRTUAL TABLE search_fts USING fts5(
  image_id UNINDEXED, caption, tags, people, place_event, folder_text, date_text, speech,
  tokenize = 'porter unicode61'
);
