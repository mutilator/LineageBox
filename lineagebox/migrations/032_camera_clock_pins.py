"""A camera's clock put right from its photos (camera_clock.py): a stretch of its photos, the first to the last it dated
wrong, and the photos in it someone said were really taken when (pins). One pin moves every photo of the stretch as
much as that one; two or more spread a clock that gained or lost time over the photos between them.

The moves of 026 (dates between two days moved by months and seconds, typed in) become stretches with one pin, at
their first moment: the same move to the second, by days rather than months.
"""
import json
from datetime import datetime, timedelta


def _moved(iso: str, months: int, seconds: int) -> str:
    """026's move: whole months (the same day of the month, or its last day), then seconds."""
    import calendar
    dt = datetime.fromisoformat(iso)
    if months:
        total = dt.year * 12 + dt.month - 1 + months
        y, m = divmod(total, 12)
        dt = dt.replace(year=y, month=m + 1, day=min(dt.day, calendar.monthrange(y, m + 1)[1]))
    return (dt + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def upgrade(con, cfg):
    old = con.execute("SELECT * FROM camera_clock ORDER BY id").fetchall()
    con.execute("DROP TABLE camera_clock")
    con.execute("""CREATE TABLE camera_clock (
      id         INTEGER PRIMARY KEY,
      camera     TEXT NOT NULL,
      first      TEXT NOT NULL,  -- its first and last photo, by the date the camera wrote (ISO, to the second); last
      last       TEXT NOT NULL,  -- 9999-12-31T23:59:59 runs on to every later photo, those read later too
      pins       TEXT NOT NULL,  -- JSON [{"at": the camera's date, "real": when it was really taken, "image": id}]
      made_at    TEXT NOT NULL,
      account_id INTEGER REFERENCES account(id) ON DELETE SET NULL
    )""")
    con.execute("CREATE INDEX camera_clock_camera ON camera_clock(camera)")
    for r in old:  # (id, camera, first, last, months, seconds, made_at, account_id), as 026 made it
        pin = [{"at": r[2], "real": _moved(r[2], r[4], r[5]), "image": None}]
        con.execute("""INSERT INTO camera_clock(id, camera, first, last, pins, made_at, account_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""", (r[0], r[1], r[2], r[3], json.dumps(pin), r[6], r[7]))
