"""Settings from config.toml, the data folder's config.toml and LINEAGEBOX_* environment variables; the catalog's disk;
the Docker health check."""
import os

import pytest

from lineagebox import config, db, health, mounts
from lineagebox.config import ConfigError


@pytest.fixture(autouse=True)
def no_settings_in_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith("LINEAGEBOX_"):
            monkeypatch.delenv(name)
    monkeypatch.setattr(config, "DEFAULT_PATH", config.DEFAULT_PATH.with_name("no-such-config.toml"))


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_each_place_overrides_the_one_before(tmp_path, monkeypatch):
    photos, data = tmp_path / "photos", tmp_path / "data"
    main = write(tmp_path / "main.toml", f"""media_root = '{photos}'
data_dir = '{data}'
[ollama]
url = "http://a:11434"
model = "m1"
workers = 3
[web]
network_paths = {{ local = '\\\\nas\\photo', "nas/" = "//pc/pics" }}
""")
    write(data / "config.toml", '[ollama]\nmodel = "m2"\nurl = "http://b:11434"\n')
    monkeypatch.setenv("LINEAGEBOX_OLLAMA_URL", "http://c:11434/")
    monkeypatch.setenv("LINEAGEBOX_WHISPER_URL", "")  # empty is unset: compose passes ${X:-} through
    monkeypatch.setenv("LINEAGEBOX_SCAN_FOLDERS", "local/A, nas/B")
    cfg = config.load(main)
    assert (cfg.share_root, cfg.data_dir) == (photos, data)
    assert (cfg.ollama_url, cfg.ollama_model, cfg.llm_workers) == ("http://c:11434", "m2", 3)
    assert cfg.whisper_url == "" and cfg.folders == ("local/A", "nas/B")
    assert cfg.network_paths == (("local", "\\\\nas\\photo"), ("nas", "//pc/pics"))


def test_environment_alone(tmp_path, monkeypatch, capsys):
    for name, value in {"LINEAGEBOX_MEDIA_ROOT": str(tmp_path), "LINEAGEBOX_DATA_DIR": str(tmp_path / "data"),
                        "LINEAGEBOX_WEB_NETWORK_PATHS": r"local=\\nas\photo, nas=//pc/pics",
                        "LINEAGEBOX_WEB_ALLOWED_HOSTS": "Photos.Example.com,*.home.example.org",
                        "LINEAGEBOX_INGEST_THREADS": "2", "LINEAGEBOX_LIBRARY_ABOUT": "a family in Oregon, USA",
                        "LINEAGEBOX_OLAMA_URL": "http://typo"}.items():
        monkeypatch.setenv(name, value)
    cfg = config.load()
    assert cfg.share_root == tmp_path and cfg.ingest_threads == 2 and cfg.library_about == "a family in Oregon, USA"
    assert cfg.network_paths == (("local", r"\\nas\photo"), ("nas", "//pc/pics"))
    assert cfg.allowed_hosts == ("photos.example.com", "*.home.example.org")
    assert "LINEAGEBOX_OLAMA_URL" in capsys.readouterr().err  # a typo is pointed out, not silently ignored
    assert cfg.network_roots()[-1] == ("", str(tmp_path))  # the share itself comes last


def test_settings_that_cannot_be_used(tmp_path, monkeypatch):
    with pytest.raises(ConfigError, match="media_root is not set"):
        config.load()
    monkeypatch.setenv("LINEAGEBOX_MEDIA_ROOT", str(tmp_path))
    monkeypatch.setenv("LINEAGEBOX_DATA_DIR", str(tmp_path / "data"))
    for name, value, message in [("LINEAGEBOX_INGEST_THREADS", "many", "LINEAGEBOX_INGEST_THREADS"),
                                 ("LINEAGEBOX_WEB_TRUSTED_PROXIES", "proxy.lan", "not an address"),
                                 ("LINEAGEBOX_WEB_TLS_CERT", "/certs/fullchain.pem", "both tls_cert and tls_key"),
                                 ("LINEAGEBOX_INGEST_THREADS", "0", "ingest_threads is 0: it can be 1 to 16")]:
        monkeypatch.setenv(name, value)
        with pytest.raises(ConfigError, match=message):
            config.load()
        monkeypatch.delenv(name)
    broken = write(tmp_path / "broken.toml", 'media_root = "/photos\n')  # a quote left open
    with pytest.raises(ConfigError, match="broken.toml isn't written right"):
        config.load(broken)
    with pytest.raises(ConfigError, match="can't be read"):
        config.load(tmp_path / "missing.toml")


def test_mount_table(tmp_path, monkeypatch):
    info = write(tmp_path / "mountinfo", "22 1 0:31 / / rw - overlay overlay rw\n"
                                         "30 22 0:50 / /photos/my\\040nas rw,relatime - cifs //nas/photos rw\n"
                                         "31 22 8:2 /srv/pics /photos/local rw shared:1 - ext4 /dev/sda2 rw\n")
    monkeypatch.setattr(mounts, "MOUNTINFO", info)
    assert mounts.mounts() == {"/": "overlay", "/photos/my nas": "cifs", "/photos/local": "ext4"}
    monkeypatch.setattr(mounts, "MOUNTINFO", tmp_path / "missing")  # not Linux
    assert mounts.mounts() == {} and mounts.fs_type(tmp_path) is None


def test_the_catalog_is_never_put_on_a_network_share(cfg, monkeypatch):
    disk = cfg.data_dir.parent.resolve().as_posix()
    monkeypatch.setattr(mounts, "mounts", lambda: {disk: "cifs"})
    assert mounts.fs_type(cfg.data_dir) == "cifs"
    with pytest.raises(ConfigError, match="network share"):
        db.connect(cfg)
    monkeypatch.setattr(mounts, "mounts", lambda: {disk: "ext4"})
    db.connect(cfg).close()


def test_a_catalog_folder_that_cannot_be_written(cfg, monkeypatch):
    monkeypatch.setattr(db.os, "access", lambda path, mode: False)  # owned by root, LineageBox runs as PUID
    with pytest.raises(ConfigError, match="cannot be written"):
        db.connect(cfg)


def test_health_check(cfg, monkeypatch, capsys):
    monkeypatch.setenv("LINEAGEBOX_CONFIG", str(write(cfg.data_dir.parent / "h.toml",
                                                  f"media_root = '{cfg.share_root}'\ndata_dir = '{cfg.data_dir}'\n")))
    assert health.main() == 1  # no catalog yet
    db.connect(cfg).close()
    assert health.main() == 0
    assert capsys.readouterr().out.splitlines()[-1] == "ok"
