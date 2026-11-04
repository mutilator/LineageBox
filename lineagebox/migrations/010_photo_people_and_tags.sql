-- People added to photos by hand, for photos where their face was not found or cannot be seen (the back of a band on
-- stage, a photo of their car). Faces stay the usual way; this is for the rest.
-- Tags given by hand use image_tag.source 'user'; a tag taken off a photo stays as source 'not', so describing the
-- photo again does not bring it back.
CREATE TABLE image_person (
  image_id  INTEGER NOT NULL REFERENCES image(id),
  person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  added_at  TEXT NOT NULL,
  PRIMARY KEY (image_id, person_id)
);
CREATE INDEX image_person_person ON image_person(person_id);
CREATE INDEX image_tag_tag ON image_tag(tag_id);
