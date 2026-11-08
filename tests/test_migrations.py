"""Upgrading a catalog that has been in use: the migrations that build a table again keep every row and id, and those
that set markers for what is already there (other apps' names, New for you) treat an old library as old."""
import dataclasses
import json

import pytest

from lineagebox import db, outbox, settings
from lineagebox.faces import STAGE_FACES


def older(cfg, tmp_path, upto: int):
    old = dataclasses.replace(cfg, data_dir=tmp_path / f"v{upto}")
    c = db.connect(old, migrate_schema=False)
    db.migrate(c, old, upto=upto)
    return old, c


def _photo(c, sha: str, folder_id: int | None = None) -> int:
    """A photo on the share, its faces looked for."""
    if folder_id is None:
        c.execute("INSERT OR IGNORE INTO folder(id, path, name) VALUES (1, 'lib', 'lib')")
        folder_id = 1
    image = c.execute("INSERT INTO image(sha256, format, stage) VALUES (?, 'jpeg', ?)", (sha, STAGE_FACES)).lastrowid
    c.execute("INSERT INTO file(path, folder_id, size, mtime, last_seen, image_id) VALUES (?, ?, 1, 0, 'x', ?)",
              (f"lib/{sha}.jpg", folder_id, image))
    return image


def test_shares_are_kept_whole_when_albums_can_be_shared_too(cfg, tmp_path):
    """028 builds share again (its CHECK gains album_id): every share, with its id, who made it and whom with."""
    old, c = older(cfg, tmp_path, 27)
    try:
        c.execute("INSERT INTO account(id, email, role, created_at) VALUES (1, 'sam@example.com', 'admin', 'x'), "
                  "(2, 'cora@example.com', 'member', 'x')")
        folder = c.execute("INSERT INTO folder(path, name) VALUES ('lib/Picnic', 'Picnic')").lastrowid
        photo = _photo(c, "a" * 64, folder)
        tour = c.execute("INSERT INTO tour(name, spec, created_at, account_id) "
                         "VALUES ('Summer', '{}', 'x', 2)").lastrowid
        rows = [(4, 1, folder, None, None, None, "2026-01-01"), (9, 2, None, photo, None, 1, "2026-01-02"),
                (17, 2, None, None, tour, None, "2026-01-03")]
        c.executemany("INSERT INTO share(id, account_id, folder_id, image_id, tour_id, with_id, created_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        c.commit()
        db.migrate(c, old, upto=28)
        assert [tuple(r) for r in c.execute("SELECT id, account_id, folder_id, image_id, tour_id, with_id, created_at "
                                            "FROM share ORDER BY id")] == rows
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        album = c.execute("INSERT INTO album(name, created_at, changed_at) VALUES ('Zoo', 'x', 'x')").lastrowid
        c.execute("INSERT INTO share(account_id, album_id, created_at) VALUES (1, ?, 'x')", (album,))
        with pytest.raises(Exception):  # still one thing a share is of
            c.execute("INSERT INTO share(account_id, album_id, tour_id, created_at) VALUES (1, ?, ?, 'x')",
                      (album, tour))
        with pytest.raises(Exception):  # and once
            c.execute("INSERT INTO share(account_id, album_id, created_at) VALUES (2, ?, 'y')", (album,))
        c.rollback()
        c.execute("DELETE FROM tour WHERE id = ?", (tour,))  # its share still goes with it
        assert not c.execute("SELECT 1 FROM share WHERE tour_id = ?", (tour,)).fetchone()
    finally:
        c.close()


def test_a_share_table_changed_since_is_never_built_again_without_it(cfg, tmp_path):
    """028 rebuilds share from the columns 019 made: a column added since would be lost, so it refuses, and the
    catalog stays as it was."""
    old, c = older(cfg, tmp_path, 27)
    try:
        c.execute("ALTER TABLE share ADD COLUMN note TEXT")
        c.commit()
        with pytest.raises(RuntimeError, match="share's columns"):
            db.migrate(c, old, upto=28)
        assert c.execute("PRAGMA user_version").fetchone()[0] == 27
        assert "note" in [r[1] for r in c.execute("PRAGMA table_info(share)")]
    finally:
        c.close()


def test_an_old_library_is_not_news_when_new_for_you_comes(cfg, tmp_path):
    """031: the photos and damaged files a catalog had before New for you aren't told of; what comes after is."""
    old, c = older(cfg, tmp_path, 30)
    try:
        for n in range(3):
            _photo(c, f"{n}" * 64)
        c.execute("UPDATE file SET checked_at = '2026-11-01T10:00:00', damage = 'cut short' WHERE id = 1")
        c.commit()
        db.migrate(c, old)
        cfg_now = settings.current(c, old)
        assert outbox.after_pass(c, cfg_now) == 0
        assert not c.execute("SELECT 1 FROM outbox").fetchone()
        new = _photo(c, "n" * 64)
        c.execute("UPDATE file SET checked_at = '2026-11-08T10:00:00', damage = 'cut short' WHERE image_id = ?", (new,))
        c.commit()
        assert outbox.after_pass(c, cfg_now) == 2
        told = {r["kind"]: json.loads(r["data"]) for r in c.execute("SELECT kind, data FROM outbox")}
        assert told == {"new_photos": {"ids": [new]}, "damaged": {"files": 1}}
    finally:
        c.close()


def test_a_new_catalog_tells_nothing_of_its_first_reading(cfg, tmp_path):
    """031 on an empty catalog: the whole library read the first time is all new, so none of it is news."""
    old, c = older(cfg, tmp_path, 30)
    try:
        db.migrate(c, old)
        assert settings.get(c, outbox.PHOTOS_AFTER) == 0 and settings.get(c, outbox.DAMAGE_AFTER) == ""
        for n in range(2):
            _photo(c, f"{n}" * 64)
        c.commit()
        assert outbox.new_photos(c, settings.current(c, old)) == 0
        assert settings.get(c, outbox.PHOTOS_AFTER) == 2
    finally:
        c.close()
