"""What other programs read and wrote: XMP sidecars LineageBox writes beside the photos (sidecars.py), names, tags and
dates read from other apps' metadata (imports.py), and photos and folders placed on the map by hand (places.py).

An existing catalog keeps reading other apps' names off until an admin turns it on (Settings → Other programs): a
library named here already would otherwise gain the people and tags an old program left in its files, all at once. A
new catalog reads them from the start, so a library tagged elsewhere starts out named.
"""

def mark(image_id: str) -> str:
    """SQL putting a photo on sidecar_stale, in a trigger. Never a conflict, rather than INSERT OR IGNORE: a trigger
    takes the conflict rule of the statement that fired it, and an upsert's (tagging.add) is to fail."""
    return (f"INSERT INTO sidecar_stale SELECT {image_id} WHERE {image_id} IS NOT NULL "
            f"AND NOT EXISTS (SELECT 1 FROM sidecar_stale WHERE image_id = {image_id})")


STATEMENTS = [
    # XMP sidecars LineageBox wrote: one row per photo file it wrote one beside (photo.jpg.xmp), with the SHA-256 of
    # what it wrote, so it is written again only when that changes. digest 'other' marks a sidecar another program
    # made at that name, which is left as it is.
    """CREATE TABLE sidecar (
      file_id    INTEGER PRIMARY KEY REFERENCES file(id) ON DELETE CASCADE,
      image_id   INTEGER NOT NULL,
      digest     TEXT NOT NULL,
      written_at TEXT NOT NULL
    )""",
    "CREATE INDEX sidecar_image ON sidecar(image_id)",
    # Photos whose sidecars may be out of date, for the worker's sidecars stage: filled by the catalog itself, whatever
    # writes the rows (the site, an undo, the worker's own import), as search_stale is (023).
    "CREATE TABLE sidecar_stale (image_id INTEGER PRIMARY KEY)",
    f"""CREATE TRIGGER sidecar_face_named AFTER UPDATE OF person_id, assigned_by, x1, y1, x2, y2 ON face
       WHEN new.assigned_by = 'user' OR old.assigned_by = 'user' BEGIN
      {mark("new.image_id")};
    END""",
    f"""CREATE TRIGGER sidecar_face_gone AFTER DELETE ON face WHEN old.assigned_by = 'user' BEGIN
      {mark("old.image_id")};
    END""",
    f"""CREATE TRIGGER sidecar_face_added AFTER INSERT ON face WHEN new.assigned_by = 'user' BEGIN
      {mark("new.image_id")};
    END""",
    """CREATE TRIGGER sidecar_person_renamed AFTER UPDATE OF name ON person BEGIN
      INSERT INTO sidecar_stale SELECT image_id FROM (
        SELECT image_id FROM face WHERE person_id = new.id AND assigned_by = 'user'
        UNION SELECT image_id FROM image_person WHERE person_id = new.id)
        WHERE image_id NOT IN (SELECT image_id FROM sidecar_stale);
    END""",
    f"""CREATE TRIGGER sidecar_caption_added AFTER INSERT ON comment WHEN new.kind = 'caption' BEGIN
      {mark("new.image_id")};
    END""",
    f"""CREATE TRIGGER sidecar_caption_changed AFTER UPDATE OF text ON comment WHEN new.kind = 'caption' BEGIN
      {mark("new.image_id")};
    END""",
    f"""CREATE TRIGGER sidecar_caption_gone AFTER DELETE ON comment WHEN old.kind = 'caption' BEGIN
      {mark("old.image_id")};
    END""",
    f"""CREATE TRIGGER sidecar_tag_added AFTER INSERT ON image_tag WHEN new.source = 'user' BEGIN
      {mark("new.image_id")};
    END""",
    f"""CREATE TRIGGER sidecar_tag_changed AFTER UPDATE OF source ON image_tag
       WHEN new.source = 'user' OR old.source = 'user' BEGIN
      {mark("new.image_id")};
    END""",
    f"""CREATE TRIGGER sidecar_tag_gone AFTER DELETE ON image_tag WHEN old.source = 'user' BEGIN
      {mark("old.image_id")};
    END""",
    f"""CREATE TRIGGER sidecar_person_added AFTER INSERT ON image_person BEGIN
      {mark("new.image_id")};
    END""",
    f"""CREATE TRIGGER sidecar_person_gone AFTER DELETE ON image_person BEGIN
      {mark("old.image_id")};
    END""",
    f"""CREATE TRIGGER sidecar_image_changed AFTER UPDATE OF taken_at, taken_at_source, date_text, width, height,
       gps_lat, gps_lon ON image BEGIN
      {mark("new.id")};
    END""",
    # a copy found (or read again) gets its sidecar; one gone takes LineageBox's away with it
    f"""CREATE TRIGGER sidecar_file_changed AFTER UPDATE OF image_id, missing, path ON file BEGIN
      {mark("new.image_id")};
      {mark("old.image_id")};
    END""",

    # Photos and folders placed on the map by hand. A photo's own place wins over its GPS (a phone's can be wrong),
    # which wins over its folder's place by hand, which wins over the place a folder's name gives. Kept apart from the
    # GPS the file gave (image.gps_lat), which is put back by taking the place by hand away.
    """CREATE TABLE image_place (
      image_id   INTEGER PRIMARY KEY REFERENCES image(id) ON DELETE CASCADE,
      lat        REAL NOT NULL,
      lon        REAL NOT NULL,
      account_id INTEGER REFERENCES account(id) ON DELETE SET NULL,
      at         TEXT NOT NULL
    )""",
    """CREATE TABLE folder_place (
      folder_id  INTEGER PRIMARY KEY REFERENCES folder(id) ON DELETE CASCADE,
      lat        REAL NOT NULL,
      lon        REAL NOT NULL,
      account_id INTEGER REFERENCES account(id) ON DELETE SET NULL,
      at         TEXT NOT NULL
    )""",
    f"""CREATE TRIGGER sidecar_placed AFTER INSERT ON image_place BEGIN
      {mark("new.image_id")};
    END""",
    f"""CREATE TRIGGER sidecar_moved AFTER UPDATE ON image_place BEGIN
      {mark("new.image_id")};
    END""",
    f"""CREATE TRIGGER sidecar_unplaced AFTER DELETE ON image_place BEGIN
      {mark("old.image_id")};
    END""",
    # a folder's place is its photos', and those of the folders in it
    """CREATE TRIGGER sidecar_folder_placed AFTER INSERT ON folder_place BEGIN
      INSERT INTO sidecar_stale SELECT DISTINCT f.image_id FROM file f JOIN folder d ON d.id = f.folder_id
        WHERE f.image_id IS NOT NULL AND (d.id = new.folder_id
          OR d.path LIKE (SELECT replace(replace(replace(path, '!', '!!'), '%', '!%'), '_', '!_') || '/%'
                          FROM folder WHERE id = new.folder_id) ESCAPE '!')
          AND f.image_id NOT IN (SELECT image_id FROM sidecar_stale);
    END""",
    """CREATE TRIGGER sidecar_folder_moved AFTER UPDATE ON folder_place BEGIN
      INSERT INTO sidecar_stale SELECT DISTINCT f.image_id FROM file f JOIN folder d ON d.id = f.folder_id
        WHERE f.image_id IS NOT NULL AND (d.id = new.folder_id
          OR d.path LIKE (SELECT replace(replace(replace(path, '!', '!!'), '%', '!%'), '_', '!_') || '/%'
                          FROM folder WHERE id = new.folder_id) ESCAPE '!')
          AND f.image_id NOT IN (SELECT image_id FROM sidecar_stale);
    END""",
    """CREATE TRIGGER sidecar_folder_unplaced AFTER DELETE ON folder_place BEGIN
      INSERT INTO sidecar_stale SELECT DISTINCT f.image_id FROM file f JOIN folder d ON d.id = f.folder_id
        WHERE f.image_id IS NOT NULL AND (d.id = old.folder_id
          OR d.path LIKE (SELECT replace(replace(replace(path, '!', '!!'), '%', '!%'), '_', '!_') || '/%'
                          FROM folder WHERE id = old.folder_id) ESCAPE '!')
          AND f.image_id NOT IN (SELECT image_id FROM sidecar_stale);
    END""",

    # Other apps' metadata (imports.py): each photo is read once; metadata_read says which were, and where something
    # was found (xmp, iptc, sidecar, picasa, takeout), NULL for nothing.
    """CREATE TABLE metadata_read (
      image_id INTEGER PRIMARY KEY REFERENCES image(id) ON DELETE CASCADE,
      read_at  TEXT NOT NULL,
      found    TEXT
    )""",
    # Everything brought in, one row each, for Settings → Other programs. kind: name (a name on a face, or someone in
    # the photo), person (someone added to the catalog for a name), tag, caption, date, place. A name with a box
    # waits (state 'waiting') until the photo's faces are found, then names the face it covers ('face'), or adds them
    # to the photo when no face matches ('photo'); 'kept' when something set here won, 'unsure' when two people have
    # the name. The box is a fraction of the picture as the other app gave it; turn is the quarter turn (degrees) the
    # file's orientation makes, as apps differ on whether a box is drawn before or after it.
    """CREATE TABLE imported (
      id        INTEGER PRIMARY KEY,
      image_id  INTEGER NOT NULL REFERENCES image(id) ON DELETE CASCADE,
      kind      TEXT NOT NULL,
      value     TEXT,
      source    TEXT NOT NULL,
      at        TEXT NOT NULL,
      state     TEXT,
      x1 REAL, y1 REAL, x2 REAL, y2 REAL,
      turn      INTEGER NOT NULL DEFAULT 0,
      face_id   INTEGER REFERENCES face(id) ON DELETE SET NULL,
      person_id INTEGER REFERENCES person(id) ON DELETE SET NULL
    )""",
    "CREATE INDEX imported_image ON imported(image_id)",
    "CREATE INDEX imported_waiting ON imported(image_id) WHERE state = 'waiting'",
]


def upgrade(con, cfg):
    for statement in STATEMENTS:
        con.execute(statement)
    if con.execute("SELECT 1 FROM image LIMIT 1").fetchone():  # a library already here: see the docstring
        con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('import_enabled', 'false')")
