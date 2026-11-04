-- Dates set by hand, for photos whose automatic date (camera, file name, folder name or file time) is wrong: scans of
-- old prints, a camera with its clock wrong. taken_at holds the middle of the date's span and taken_at_source 'user';
-- date_text is the date as it is shown ("summer 1987", "between 1986 and 1988"), date_from and date_to its first and
-- last day. The automatic date is kept in auto_taken_at and auto_taken_at_source, to go back to.
ALTER TABLE image ADD COLUMN date_text TEXT;
ALTER TABLE image ADD COLUMN date_from TEXT;
ALTER TABLE image ADD COLUMN date_to TEXT;
ALTER TABLE image ADD COLUMN auto_taken_at TEXT;
ALTER TABLE image ADD COLUMN auto_taken_at_source TEXT;
