"""Docker's health check: python -m lineagebox.health. Healthy when the catalog can be read and the media root is there.

Kept to the config and sqlite3, so it starts fast; it runs every minute in each container.
"""
import contextlib
import sqlite3
import sys

from lineagebox import config, setup


def main() -> int:
    if setup.first_start():   # the image's setup wizard, before there is a catalog: running is all there is to it
        print("ok: the setup wizard")
        return 0
    try:
        cfg = config.load()
        with contextlib.closing(sqlite3.connect(f"file:{cfg.db_path.as_posix()}?mode=ro", uri=True, timeout=10)) as con:
            con.execute("SELECT 1 FROM settings LIMIT 1").fetchone()
    except (OSError, ValueError, sqlite3.Error) as e:
        print(f"unhealthy: {e}")
        return 1
    if not cfg.share_root.is_dir():
        print(f"unhealthy: the media root {cfg.share_root} is not there")
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
