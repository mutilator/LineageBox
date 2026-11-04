"""Paths become relative to the share instead of the one library folder, so more folders of the share can be watched.

'2012/x.jpg' becomes 'Photos/2012/x.jpg', and a removed file's 'Photos-Removed/2012/x.jpg'. The library's root
folder becomes the folder 'Photos', under a new root for the share. The library folder is the first watched folder in
the config. A new catalog has nothing to move.
"""
import json


def upgrade(con, cfg):
    if not cfg.folders or con.execute("SELECT 1 FROM folder LIMIT 1").fetchone() is None:
        return  # a new catalog: nothing to move
    library = cfg.folders[0]
    con.execute("UPDATE folder SET path = CASE path WHEN '' THEN ? ELSE ? || '/' || path END", (library, library))
    con.execute("UPDATE folder SET name = ? WHERE path = ?", (library.rpartition("/")[2], library))
    con.execute("UPDATE file SET path = ? || '/' || path", (library,))
    con.execute("UPDATE file SET removed_path = ? || '-Removed/' || removed_path WHERE removed_path IS NOT NULL",
                (library,))
    parent, parts = None, library.split("/")
    for depth in range(len(parts)):  # the share itself (''), then any folders between it and the library
        parent = con.execute("INSERT INTO folder(path, parent_id, name) VALUES (?, ?, ?)",
                             ("/".join(parts[:depth]), parent, parts[depth - 1] if depth else "")).lastrowid
    con.execute("UPDATE folder SET parent_id = ? WHERE path = ?", (parent, library))
    con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('folders', ?)", (json.dumps([library]),))
