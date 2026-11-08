-- Search finds more of what is already known about a photo: the captions and comments the family writes (comment,
-- 022), the words the AI read off it (signs, cakes, gravestones: analysis.text_visible) and a document's text as the AI
-- copied it out to read it into the family tree (family_reading.text). FTS5 tables cannot gain a column, so the
-- search index is made again with one for each. The worker's next pass fills it again, first thing (photos are added
-- to the search as they are found missing from it), as after 007.
DROP TABLE search_fts;
CREATE VIRTUAL TABLE search_fts USING fts5(
  image_id UNINDEXED, caption, tags, people, place_event, folder_text, date_text, speech, comments, seen_text, document,
  tokenize = 'porter unicode61'
);
-- Photos whose index row is out of date, for the worker's search stage to index again (search.index_new). Filled
-- here, by the catalog itself, whatever writes the rows: a comment saved or undone, an obituary read into the tree
-- in the web app's own thread, a document marked. Saving through the site also indexes the photo at once, which
-- takes its row out again.
CREATE TABLE search_stale (image_id INTEGER PRIMARY KEY);
CREATE TRIGGER search_comment_added AFTER INSERT ON comment BEGIN
  INSERT OR IGNORE INTO search_stale VALUES (new.image_id);
END;
CREATE TRIGGER search_comment_changed AFTER UPDATE OF text, image_id ON comment BEGIN
  INSERT OR IGNORE INTO search_stale VALUES (new.image_id);
  INSERT OR IGNORE INTO search_stale VALUES (old.image_id);
END;
CREATE TRIGGER search_comment_gone AFTER DELETE ON comment BEGIN
  INSERT OR IGNORE INTO search_stale VALUES (old.image_id);
END;
CREATE TRIGGER search_reading_added AFTER INSERT ON family_reading WHEN new.image_id IS NOT NULL BEGIN
  INSERT OR IGNORE INTO search_stale VALUES (new.image_id);
END;
CREATE TRIGGER search_reading_changed AFTER UPDATE OF text, title, state, image_id ON family_reading BEGIN
  INSERT OR IGNORE INTO search_stale SELECT new.image_id WHERE new.image_id IS NOT NULL;
  INSERT OR IGNORE INTO search_stale SELECT old.image_id WHERE old.image_id IS NOT NULL;
END;
CREATE TRIGGER search_reading_gone AFTER DELETE ON family_reading WHEN old.image_id IS NOT NULL BEGIN
  INSERT OR IGNORE INTO search_stale VALUES (old.image_id);
END;
CREATE TRIGGER search_document_added AFTER INSERT ON document BEGIN
  INSERT OR IGNORE INTO search_stale VALUES (new.image_id);
END;
CREATE TRIGGER search_document_changed AFTER UPDATE OF kind, title ON document BEGIN
  INSERT OR IGNORE INTO search_stale VALUES (new.image_id);
END;
CREATE TRIGGER search_document_gone AFTER DELETE ON document BEGIN
  INSERT OR IGNORE INTO search_stale VALUES (old.image_id);
END;
