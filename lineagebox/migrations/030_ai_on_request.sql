-- Search by meaning, describing again on request, and tags changed across the library.
--
-- embedding: what each photo's words mean, as Ollama's embedding model reads them (embeddings.py): its AI description
-- and the family's caption, of the photo search shows for its group of similar shots. One row a photo: a new model
-- makes them again. text_sha is the text embedded, hashed, so a change that leaves the words as they were costs
-- nothing. vector is float32, of length 1.
CREATE TABLE embedding (
  image_id INTEGER PRIMARY KEY REFERENCES image(id) ON DELETE CASCADE,
  model    TEXT NOT NULL,
  text_sha TEXT NOT NULL,
  vector   BLOB NOT NULL,
  made_at  TEXT NOT NULL
);
-- what the web app asks every few seconds (how many, and the newest), answered from the index without the vectors
CREATE INDEX embedding_made ON embedding(model, made_at);
-- Photos whose words changed since they were embedded, filled by the catalog itself whatever writes them (a
-- description, a caption written, changed or undone), for the worker's next pass.
CREATE TABLE embed_stale (image_id INTEGER PRIMARY KEY);
-- (WHERE NOT EXISTS rather than INSERT OR IGNORE: a statement with an ON CONFLICT clause of its own, such as an upsert,
-- would make the trigger's OR IGNORE fail on a photo already listed)
CREATE TRIGGER embed_described AFTER INSERT ON analysis BEGIN
  INSERT INTO embed_stale SELECT new.image_id WHERE NOT EXISTS (SELECT 1 FROM embed_stale WHERE image_id = new.image_id);
END;
CREATE TRIGGER embed_caption_added AFTER INSERT ON comment WHEN new.kind = 'caption' BEGIN
  INSERT INTO embed_stale SELECT new.image_id WHERE NOT EXISTS (SELECT 1 FROM embed_stale WHERE image_id = new.image_id);
END;
CREATE TRIGGER embed_caption_changed AFTER UPDATE OF text, image_id ON comment WHEN new.kind = 'caption' BEGIN
  INSERT INTO embed_stale SELECT new.image_id WHERE NOT EXISTS (SELECT 1 FROM embed_stale WHERE image_id = new.image_id);
  INSERT INTO embed_stale SELECT old.image_id WHERE NOT EXISTS (SELECT 1 FROM embed_stale WHERE image_id = old.image_id);
END;
CREATE TRIGGER embed_caption_gone AFTER DELETE ON comment WHEN old.kind = 'caption' BEGIN
  INSERT INTO embed_stale SELECT old.image_id WHERE NOT EXISTS (SELECT 1 FROM embed_stale WHERE image_id = old.image_id);
END;

-- Photos someone asked to have described again (describe.ask_again), for the worker: model NULL is the one on the
-- Settings page. One a photo (of the photo its group's description is on); asking again replaces the last ask.
CREATE TABLE describe_request (
  image_id   INTEGER PRIMARY KEY REFERENCES image(id) ON DELETE CASCADE,
  model      TEXT,
  account_id INTEGER REFERENCES account(id) ON DELETE SET NULL,
  asked_at   TEXT NOT NULL
);
-- A description made because someone asked for it: kept when the Settings page's model changes, which otherwise
-- describes every photo again (someone picked a better model for this one).
ALTER TABLE analysis ADD COLUMN asked INTEGER NOT NULL DEFAULT 0;

-- Tags renamed, merged into another, or taken off every photo (tagging.rename, tagging.remove_everywhere): the word
-- the AI gives, and the tag it now means (NULL: none, the AI's word is left out).
CREATE TABLE tag_alias (
  name   TEXT PRIMARY KEY,
  tag_id INTEGER REFERENCES tag(id) ON DELETE CASCADE
);
