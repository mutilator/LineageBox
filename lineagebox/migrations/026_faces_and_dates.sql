-- Faces that find more, and dates for photos without one of their own.
--
-- image.faces_again: when faces were last looked for again in it (faces.look_again), or someone said all its faces
-- were found: Cleanup → Confirm faces → Faces missed leaves it out from then on.
ALTER TABLE image ADD COLUMN faces_again TEXT;

-- A camera whose clock was wrong for a while (camera_clock.py): its dates between first and last, as the camera wrote
-- them, are moved by months and seconds. Kept, so that a photo of that camera read again later (a file rewritten,
-- copied in again) is moved too. image.camera_taken_at keeps the date the camera wrote, before any move.
CREATE TABLE camera_clock (
  id         INTEGER PRIMARY KEY,
  camera     TEXT NOT NULL,
  first      TEXT NOT NULL,          -- ISO date and time, as the camera wrote them
  last       TEXT NOT NULL,
  months     INTEGER NOT NULL DEFAULT 0,
  seconds    INTEGER NOT NULL DEFAULT 0,
  made_at    TEXT NOT NULL,
  account_id INTEGER REFERENCES account(id) ON DELETE SET NULL
);
CREATE INDEX camera_clock_camera ON camera_clock(camera);
ALTER TABLE image ADD COLUMN camera_taken_at TEXT;
