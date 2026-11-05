import dataclasses
import sqlite3
import uuid
from datetime import datetime

import httpx
import pytest

from lineagebox import db, describe, settings, storage, worker
from lineagebox.faces import FaceStats
from lineagebox.llm import Ollama
from tests.conftest import rel
from tests.test_recognition import FakeOllama


def test_windows_paths_become_slashes(cfg):
    cfg.data_dir.mkdir(parents=True)
    con = sqlite3.connect(cfg.db_path)
    con.row_factory = sqlite3.Row
    for path in sorted(db.MIGRATIONS_DIR.glob("00[12]_*.sql")):  # a catalog made on Windows, before the move
        con.executescript(path.read_text(encoding="utf-8"))
    con.execute("PRAGMA user_version = 2")
    folder = con.execute("INSERT INTO folder(path, name) VALUES ('2012\\Bob''s Petting Zoo', 'x')").lastrowid
    con.execute("INSERT INTO file(path, folder_id, size, mtime, last_seen) VALUES ('2012\\Bob''s Petting Zoo\\a.jpg', ?, 1, 1, '')",
                (folder,))
    con.commit()
    db.migrate(con, cfg)
    # and then relative to the share, below the library folder, which is the first watched folder
    assert con.execute("SELECT path FROM folder WHERE id = ?", (folder,)).fetchone()[0] == "lib/2012/Bob's Petting Zoo"
    assert con.execute("SELECT path FROM file").fetchone()[0] == "lib/2012/Bob's Petting Zoo/a.jpg"
    con.close()


def test_web_and_worker_can_migrate_at_once(cfg):
    """Both containers start together and both run the migrations; neither may fail."""
    import threading
    start, errors = threading.Barrier(4), []

    def connect():
        start.wait()
        try:
            db.connect(cfg).close()
        except Exception as e:
            errors.append(e)

    cfg.data_dir.mkdir(parents=True)
    threads = [threading.Thread(target=connect) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    con = db.connect(cfg)
    assert con.execute("PRAGMA user_version").fetchone()[0] == max(
        int(p.name.split("_")[0]) for p in [*db.MIGRATIONS_DIR.glob("*.sql"), *db.MIGRATIONS_DIR.glob("*.py")])
    con.close()


class OnlineOllama(FakeOllama):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.cfg = cfg

    def reachable(self):
        return True

    def chat_json(self, system, user, schema, images=(), temperature=0.1, max_tokens=1024):
        if not images:  # folder names
            return {"folders": []}, None
        return super().chat_json(system, user, schema, images, temperature)


def test_one_pass_catalogs_new_photos(con, cfg, library, monkeypatch, capsys):
    monkeypatch.setattr(worker, "Ollama", OnlineOllama)
    monkeypatch.setattr(describe, "Ollama", OnlineOllama)
    monkeypatch.setattr(worker, "detect", lambda con, cfg, show_progress: FaceStats())  # no face models in tests
    worker.run_once(con, cfg)
    zoo = con.execute("SELECT image_id FROM file WHERE path = ?", (rel("2012", "Zoo", "DSC_0001.jpg"),)).fetchone()[0]
    assert con.execute("SELECT count(*) FROM analysis WHERE image_id = ?", (zoo,)).fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM search_fts WHERE rowid = ?", (zoo,)).fetchone()[0] == 1
    out = capsys.readouterr().out
    assert "ingest:" in out and "describe:" in out and "failed:" not in out
    measured = settings.get(con, storage.KEY)  # the room LineageBox's own files take, once a day
    assert measured["data_bytes"] > 0 and measured["measured_at"][:10] == datetime.now().date().isoformat()

    worker.run_once(con, cfg)  # nothing new: a quiet pass
    assert capsys.readouterr().out == ""
    assert settings.get(con, storage.KEY) == measured  # not again the same day


def test_pass_without_ollama_still_catalogs(con, cfg, library, monkeypatch, capsys):
    cfg = dataclasses.replace(cfg, ollama_url="http://127.0.0.1:9")  # nothing listens there
    monkeypatch.setattr(worker, "detect", lambda con, cfg, show_progress: FaceStats())
    worker.run_once(con, cfg)
    assert con.execute("SELECT count(*) FROM image").fetchone()[0] > 0
    assert con.execute("SELECT count(*) FROM analysis").fetchone()[0] == 0
    assert "descriptions wait for a later pass" in capsys.readouterr().out


def test_describe_gives_up_when_ollama_goes_away(con, cfg, library, monkeypatch):
    class Gone(FakeOllama):
        def chat_json(self, *args, **kwargs):
            raise httpx.ConnectError("PC asleep")

    from lineagebox.ingest import ingest
    from lineagebox.scan import scan
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    monkeypatch.setattr(describe, "Ollama", Gone)
    monkeypatch.setattr(describe, "GIVE_UP_AFTER", 2)
    s = describe.describe(con, dataclasses.replace(cfg, llm_workers=1), show_progress=False)
    assert s.pending > 2 and s.gave_up and (s.done, s.failed) == (0, 2)


def test_unreachable_ollama_fails_fast(cfg):
    assert not Ollama(dataclasses.replace(cfg, ollama_url="http://127.0.0.1:9")).reachable()


def _web_writes(cfg, writes):
    """The web app saving something (a name, a Keep) with no patience at all: fails if a stage holds the database."""
    web = sqlite3.connect(cfg.db_path, timeout=0)
    try:
        web.execute("INSERT INTO person(name) VALUES (?)", (f"P{uuid.uuid4()}",))
        web.commit()
        writes.append("ok")
    finally:
        web.close()


def test_scanning_and_reading_never_block_the_web_app(con, cfg, library, monkeypatch):
    import os
    from lineagebox import ingest as ingest_module, media, scan as scan_module
    writes, real_scandir, real_decode = [], os.scandir, media.decode

    def scandir(path):
        _web_writes(cfg, writes)
        return real_scandir(path)

    def decode(*args, **kwargs):
        _web_writes(cfg, writes)
        return real_decode(*args, **kwargs)

    monkeypatch.setattr(scan_module.os, "scandir", scandir)
    scan_module.scan(con, cfg)
    monkeypatch.setattr(scan_module.os, "scandir", real_scandir)
    assert len(writes) > 3 and con.execute("SELECT count(*) FROM file").fetchone()[0] == 6
    writes.clear()
    monkeypatch.setattr(media, "decode", decode)
    s = ingest_module.ingest(con, dataclasses.replace(cfg, ingest_threads=1), show_progress=False)
    assert s.new_images == 4 and len(writes) == 4  # every decode, while earlier files waited to be saved


def test_finding_faces_never_blocks_the_web_app(con, cfg, library, monkeypatch):
    import numpy as np
    from lineagebox import faces
    from lineagebox.ingest import ingest
    from lineagebox.scan import scan
    writes = []

    class Face:
        bbox = np.array([1.0, 2.0, 11.0, 12.0])
        kps = np.zeros((5, 2))
        det_score, embedding_norm, age, sex = 0.9, 20.0, 30, "F"
        normed_embedding = np.ones(512) / np.sqrt(512)

    class Model:
        class det_model:
            class session:
                get_providers = staticmethod(lambda: ["FakeProvider"])

        def get(self, bgr):
            _web_writes(cfg, writes)
            return [Face()]

    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    monkeypatch.setattr(faces, "load_model", lambda models_dir, gpu: Model())
    monkeypatch.setattr(faces, "ensure_models", lambda con, models_dir: None)  # no 280 MB download in a test
    s = faces.detect(con, cfg, show_progress=False)
    assert s.images >= 2 and writes == ["ok"] * s.images
    assert con.execute("SELECT count(*) FROM face").fetchone()[0] == s.faces == s.images
    from PIL import Image
    for x2, sha in con.execute("SELECT f.x2, i.sha256 FROM face f JOIN image i ON i.id = f.image_id"):
        assert x2 == pytest.approx(11 / Image.open(cfg.preview_path(sha)).width)  # boxes stored relative to the photo


def test_a_busy_database_gets_a_clear_answer(con, cfg, library):
    from fastapi.testclient import TestClient
    from lineagebox.web import app as webapp
    from tests.conftest import LAN
    client = TestClient(webapp.create_app(dataclasses.replace(cfg)), client=LAN)
    hold = sqlite3.connect(cfg.db_path)
    hold.execute("BEGIN IMMEDIATE")  # a stage holding the write lock
    import lineagebox.db as dbmodule
    real_connect = dbmodule.sqlite3.connect
    try:
        dbmodule.sqlite3.connect = lambda *a, **k: real_connect(*a, **{**k, "timeout": 0.1})
        r = client.post("/api/worker/run", headers={"X-Requested-With": "lineagebox"})
    finally:
        dbmodule.sqlite3.connect = real_connect
        hold.rollback()
        hold.close()
    assert r.status_code == 503 and "busy" in r.json()["detail"]


def test_a_catalog_folder_this_user_cant_write_is_said_plainly(con, cfg, monkeypatch, capsys):
    """A PUID that isn't the catalog's owner (a folder Docker made as root, say). Said at start, and on the page,
    instead of "attempt to write a readonly database"."""
    from fastapi.testclient import TestClient
    from lineagebox import cli, settings
    from lineagebox.web import app as webapp
    from tests.conftest import LAN
    assert db.unwritable(cfg) is None
    real = db.os.access
    monkeypatch.setattr(db.os, "access", lambda p, mode: real(p, mode) and p != cfg.db_path)
    assert f"{cfg.db_path} can't be written" in db.unwritable(cfg) and "PUID" in db.unwritable(cfg)
    monkeypatch.setenv("LINEAGEBOX_MEDIA_ROOT", str(cfg.share_root))
    monkeypatch.setenv("LINEAGEBOX_DATA_DIR", str(cfg.data_dir))
    with pytest.raises(SystemExit, match="can't be written by the user LineageBox runs as"):
        cli.main(["status"])
    monkeypatch.undo()

    def readonly(*args):
        raise sqlite3.OperationalError("attempt to write a readonly database")
    client = TestClient(webapp.create_app(cfg), client=LAN)
    monkeypatch.setattr(settings, "put", readonly)
    r = client.post("/api/worker/run", headers={"X-Requested-With": "lineagebox"})
    assert r.status_code == 503 and "belongs to another user" in r.json()["detail"]
