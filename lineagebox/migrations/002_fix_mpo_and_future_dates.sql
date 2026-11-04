-- Data written before the 002 code fixes.

-- Pillow reports JPEGs with an embedded second image (some Panasonic and Fuji cameras) as "MPO".
UPDATE image SET format = 'jpeg' WHERE format = 'mpo';

-- EXIF dates in the future come from a wrong camera clock; fall back to the earliest file time.
UPDATE image
SET taken_at = (SELECT replace(datetime(min(f.mtime), 'unixepoch', 'localtime'), ' ', 'T')
                FROM file f WHERE f.image_id = image.id),
    taken_at_source = 'mtime'
WHERE taken_at > strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime', '+1 day');
