-- Library paths are stored with '/' on every system, so the catalog can move between Windows and Linux.
-- (The search index splits folder names on both separators, so it needs no rebuild.)
UPDATE folder SET path = replace(path, '\', '/') WHERE instr(path, '\') > 0;
UPDATE file SET path = replace(path, '\', '/') WHERE instr(path, '\') > 0;
