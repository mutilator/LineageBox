-- Live Photos (live.py): an iPhone's still and the short clip saved beside it with the same name (IMG_1234.HEIC and
-- IMG_1234.MOV) are one item. The clip keeps its row, its files and their checks, and points at its still: it is left
-- out wherever photos are listed or worked on (catalog.VISIBLE), and the still's page plays it (catalog.live_clip).
ALTER TABLE image ADD COLUMN live_of INTEGER REFERENCES image(id) ON DELETE SET NULL;
CREATE INDEX image_live_of ON image(live_of) WHERE live_of IS NOT NULL;
