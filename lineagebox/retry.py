"""Photos, videos and folders a stage couldn't do for a reason that may pass: a service answering an error for this one
item, a file that couldn't be read just now, a tool that took too long, an answer that wasn't JSON. Each is tried again
later, longer after each failure, instead of first on every pass: stages take the newest items first, so a few that
always fail would otherwise come first every time and, counted as the service failing, hold up everything behind them.

A stage leaves out what is waiting (waiting()), records a failure (later()) and clears one once it works (done()). The
Status page shows what is waiting and why (summary()).
"""
import sqlite3
import time
from datetime import datetime

WAITS = (3600, 6 * 3600, 24 * 3600, 7 * 24 * 3600)  # after the first, second, third and every later failure
STAGES = {"describe": "photos and videos to describe", "transcribe": "videos and recordings to transcribe",
          "copies": "videos to make 720p copies of", "folders": "folders to read the names of",
          "check": "files to check for damage", "stories": "recorded stories to transcribe",
          "chapters": "long videos to find chapters in"}
STAGES["meaning"] = "descriptions to read for search by meaning"  # embeddings.py
STAGES.update(sidecars="photos to write XMP sidecars beside", metadata="photos to read other apps' names from")
STAGES["ingest"] = "videos and recordings to read (ffmpeg took too long)"  # by file id: they have no image yet


def waiting(stage: str, item: str = "i.id") -> str:
    """SQL: the item (a column of the stage's query) is not waiting to be tried again."""
    assert stage in STAGES
    return (f"NOT EXISTS (SELECT 1 FROM retry r WHERE r.stage = '{stage}' AND r.item_id = {item} "
            f"AND r.next_at > CAST(strftime('%s', 'now') AS INTEGER))")


def later(con: sqlite3.Connection, stage: str, item_id: int, error: str) -> None:
    """Try this item again after a while. Saved at once, in a write of its own: stages hold no transaction open."""
    try:
        note(con, stage, item_id, error)
        con.commit()
    except sqlite3.OperationalError:  # the catalog is busy: the item just comes up again on the next pass
        con.rollback()


def note(con: sqlite3.Connection, stage: str, item_id: int, error: str) -> None:
    """later(), as part of the caller's own write (ingest saves what it read in batches)."""
    assert stage in STAGES
    row = con.execute("SELECT attempts FROM retry WHERE stage = ? AND item_id = ?", (stage, item_id)).fetchone()
    n = (row[0] if row else 0) + 1
    con.execute("""INSERT OR REPLACE INTO retry(stage, item_id, attempts, error, failed_at, next_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (stage, item_id, n, str(error)[:300], datetime.now().isoformat(timespec="seconds"),
                 int(time.time()) + WAITS[min(n, len(WAITS)) - 1]))


def done(con: sqlite3.Connection, stage: str, item_ids) -> None:
    """These worked (or were settled for good): forget their failures. Part of the caller's own write."""
    con.executemany("DELETE FROM retry WHERE stage = ? AND item_id = ?", ((stage, i) for i in item_ids))


def summary(con: sqlite3.Connection) -> list[dict]:
    """For the Status page: per stage, how many items are waiting to be tried again, and the latest error."""
    out = []
    for stage, count, error in con.execute("""
            SELECT stage, count(*),
                   (SELECT error FROM retry r2 WHERE r2.stage = r.stage ORDER BY failed_at DESC LIMIT 1)
            FROM retry r WHERE next_at > CAST(strftime('%s', 'now') AS INTEGER) GROUP BY stage ORDER BY stage"""):
        if stage in STAGES:
            out.append({"stage": stage, "what": STAGES[stage], "count": count, "error": error})
    return out
