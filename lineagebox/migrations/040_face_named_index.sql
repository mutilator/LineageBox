-- Faces by how they were named (people.py). Those named by hand are what recognition, suggestions and Check faces
-- compare every face with, and finding them read the whole face table, every face's embedding with it, each time a
-- photo's page was opened. With the person in the index too, how many there are and whose they are (what tells
-- people._hand_named_faces that its copy is still current) are read from the index alone.
CREATE INDEX face_assigned ON face(assigned_by, person_id);
