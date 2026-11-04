-- A place someone said is where the map shows it (a folder's place, on the map: "Right place"), so the map can show
-- which of the places read from folder names are checked. Looking it up again as something else takes that back.
ALTER TABLE place ADD COLUMN confirmed_at TEXT;
