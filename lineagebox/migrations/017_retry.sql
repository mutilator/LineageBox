-- What a stage couldn't do for a reason that may pass, and when to try it again (retry.py): a photo whose description
-- failed, a video Whisper answered 500 for, a folder batch whose answer wasn't JSON. item_id is the stage's own row:
-- an image's id, a folder's for "folders", a file's for "check".
CREATE TABLE retry (
    stage TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    attempts INTEGER NOT NULL,
    error TEXT,
    failed_at TEXT NOT NULL,
    next_at INTEGER NOT NULL,  -- unix time: left alone until then
    PRIMARY KEY (stage, item_id)
);
