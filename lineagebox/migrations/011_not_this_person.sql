-- Faces someone said are not a certain person (unticked when the face was suggested as them): it is not suggested or
-- recognised as them again, and stays free to be named as anyone else.
CREATE TABLE face_not_person (
  face_id   INTEGER NOT NULL REFERENCES face(id),
  person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  PRIMARY KEY (face_id, person_id)
) WITHOUT ROWID;
