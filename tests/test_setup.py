"""The setup wizard (lineagebox/setup/): nas.toml, the compose file it makes from people's answers, what it sees of a
machine through /host, and its server, hosted and on a machine. Where Docker is installed, every file the wizard makes
is read by `docker compose config` too, the same parser people's NAS apps use; where node is, the page's password
escaping is checked against the server's."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from lineagebox import cli, health, setup
from lineagebox.setup import compose, host as hostlib, profiles, server

ROOT = Path(__file__).resolve().parent.parent
SYSTEMS = profiles.load()
DOCKER = shutil.which("docker")
NODE = shutil.which("node")


def answers(**changes):
    """A Synology with one folder of photos, as most people answer; changes on top."""
    a = {"system": "synology", "install_dir": "/volume1/docker/lineagebox", "data": "/volume1/docker/lineagebox/data",
         "puid": 1026, "pgid": 100, "tz": "Europe/Berlin", "port": 8000,
         "sources": [{"kind": "folder", "name": "local", "path": "/volume1/photo"}]}
    a.update(changes)
    return a


def plan(**changes):
    return compose.read(answers(**changes), SYSTEMS)


def config(text, tmp_path):
    """The file as docker compose reads it (`config`, which checks it and needs no daemon), or skip without Docker.
    compose writes a literal $ back as $$; that is undone here."""
    if not DOCKER:
        pytest.skip("Docker isn't installed")
    f = tmp_path / "compose.yaml"
    f.write_text(text, encoding="utf-8", newline="\n")
    r = subprocess.run([DOCKER, "compose", "-f", str(f), "config", "--format", "json"], capture_output=True,
                       text=True, encoding="utf-8", timeout=120)
    if r.returncode and ("is not a docker command" in r.stderr or "unknown command" in r.stderr):
        pytest.skip("Docker has no compose")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.replace("$$", "$"))


def errors(p):
    return {x.field: x.text for x in p.problems if x.level == "error"}


def warnings(p):
    return {x.field: x.text for x in p.problems if x.level == "warning"}


# ---- nas.toml
def test_nas_toml_has_nothing_wrong():
    assert profiles.check() == []
    assert {"synology", "qnap", "unraid", "truenas", "linux", "rootless"} <= set(SYSTEMS)
    for s in SYSTEMS.values():
        assert s.get("users"), f"{s['id']} suggests no user"


def test_check_says_what_is_wrong(tmp_path):
    bad = tmp_path / "nas.toml"
    bad.write_text('[[system]]\nid = "Bad Id"\nname = "x"\nsudoo = true\ninstall_dir = "volume1"\n'
                   '[[system.users]]\nuid = "1026"\ngid = 100\nwho = "x"\n'
                   '[[system.models]]\nname = "X1"\narch = "sparc"\n', encoding="utf-8")
    said = " ".join(profiles.check(bad))
    for words in ("lowercase", "sudoo", "whole path", "uid", "arch"):
        assert words in said


def test_ollama_is_the_version_compose_ollama_yaml_runs():
    text = (ROOT / "compose.ollama.yaml").read_text(encoding="utf-8")
    assert f"ollama/ollama:${{OLLAMA_VERSION:-{compose.OLLAMA_VERSION}}}" in text


def test_the_repositorys_compose_yaml_starts_the_wizard(tmp_path):
    """compose.yaml is the wizard's: the image without LINEAGEBOX_SETUP, the machine read-only and the folder to save
    in, as the service web, so that the file the wizard saves takes its place in the same project. compose.manual.yaml,
    for a setup by hand with .env, says LINEAGEBOX_SETUP: done, as the wizard's own files do."""
    c = config((ROOT / "compose.yaml").read_text(encoding="utf-8"), tmp_path)
    assert c["name"] == "lineagebox" and set(c["services"]) == {"web"}
    web = c["services"]["web"]
    assert "LINEAGEBOX_SETUP" not in (web.get("environment") or {}) and not web.get("command")
    mounts = {m["target"]: m for m in web["volumes"]}
    assert mounts["/host"]["source"] in ("/", "\\") and mounts["/host"]["read_only"]
    assert Path(mounts["/install"]["source"]) == tmp_path and not mounts["/install"].get("read_only")
    assert web["user"] == "0:0" and web["cap_drop"] == ["ALL"] and set(web["cap_add"]) == {"CHOWN", "DAC_OVERRIDE",
                                                                                          "FOWNER"}
    assert web["read_only"] and web["restart"] == "no" and web["ports"][0]["published"] == "8000"
    (tmp_path / "manual").mkdir()
    manual = config((ROOT / "compose.manual.yaml").read_text(encoding="utf-8"), tmp_path / "manual")
    for name in ("web", "worker", "copies"):
        assert manual["services"][name]["environment"]["LINEAGEBOX_SETUP"] == "done"
    assert "COMPOSE_FILE=compose.manual.yaml\n" in (ROOT / ".env.example").read_text(encoding="utf-8")


def test_the_image_starts_the_wizard_until_it_is_set_up(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'media_root = "{(tmp_path / "photos").as_posix()}"\ndata_dir = "{(tmp_path / "data").as_posix()}"\n',
                   encoding="utf-8")
    monkeypatch.setenv("LINEAGEBOX_CONFIG", str(cfg))
    monkeypatch.delenv("LINEAGEBOX_DATA_DIR", raising=False)
    monkeypatch.delenv("LINEAGEBOX_SETUP", raising=False)
    assert not setup.first_start()                    # from source: never
    monkeypatch.setenv("LINEAGEBOX_SETUP", "wizard")   # the image's (its Dockerfile)
    assert setup.first_start()
    assert health.main() == 0                         # healthy while the wizard is all there is
    called = {}
    monkeypatch.setattr(server, "serve", lambda *a, **k: called.update(args=a, kw=k))
    cli.main(["serve", "--host", "0.0.0.0", "--port", "8000"])
    assert called["args"] == (False, "0.0.0.0", 8000) and called["kw"] == {"first": True}
    monkeypatch.setenv("LINEAGEBOX_SETUP", "done")     # the wizard's file, and compose.manual.yaml
    assert not setup.first_start()
    monkeypatch.setenv("LINEAGEBOX_SETUP", "wizard")
    (tmp_path / "data").mkdir()                       # a compose file from before the wizard mounts its catalog folder
    assert not setup.first_start()


# ---- the file
def test_the_usual_answers_make_the_repository_compose_files_services(tmp_path):
    p = plan()
    assert errors(p) == {}
    c = config(compose.render(p), tmp_path)
    assert set(c["services"]) == {"web", "worker", "copies", "updater"}
    web, worker, copies = c["services"]["web"], c["services"]["worker"], c["services"]["copies"]
    for s in (web, worker, copies):
        assert s["image"] == "ghcr.io/mutilator/lineagebox:latest"
        assert s["environment"]["LINEAGEBOX_SETUP"] == "done"   # the image starts LineageBox, not its wizard
        assert s["user"] == "1026:100"
        assert s["cap_drop"] == ["ALL"] and "no-new-privileges:true" in s["security_opt"]
        assert s["environment"]["TZ"] == "Europe/Berlin"
        assert s["environment"]["LINEAGEBOX_OLLAMA_ENABLED"] == "false"
        mounts = {m["target"]: m for m in s["volumes"]}
        assert mounts["/data"]["source"] == "/volume1/docker/lineagebox/data"
        assert mounts["/photos/local"]["source"] == "/volume1/photo"
        assert mounts["/photos/local"]["bind"]["create_host_path"] is False
    assert {m["target"]: m.get("read_only", False) for m in worker["volumes"]}["/photos/local"] is True
    assert not {m["target"]: m.get("read_only", False) for m in web["volumes"]}["/photos/local"]
    assert web["ports"][0]["published"] == "8000" and web["ports"][0]["host_ip"] == "0.0.0.0"
    assert web["command"] == ["serve", "--host", "0.0.0.0", "--port", "8000"]
    updater = c["services"]["updater"]
    assert updater["network_mode"] == "none" and updater["environment"] == {"PUID": "1026", "PGID": "100"}
    assert {m["target"]: m["source"] for m in updater["volumes"]} == {
        "/var/run/docker.sock": "/var/run/docker.sock", "/update": "/volume1/docker/lineagebox/data/update"}


def test_values_reach_docker_exactly_as_typed(tmp_path):
    odd = 'Großmutter\'s "box" $HOME ${X} \\ end'
    p = plan(about=odd, sources=[{"kind": "folder", "name": "Oma's Fotos", "path": "/volume1/Fotos von Oma/$1 \"a\""}])
    assert errors(p) == {}
    c = config(compose.render(p), tmp_path)
    web = c["services"]["web"]
    assert web["environment"]["LINEAGEBOX_LIBRARY_ABOUT"] == odd
    assert {m["target"]: m["source"] for m in web["volumes"]}["/photos/Oma's Fotos"] == '/volume1/Fotos von Oma/$1 "a"'


def test_quote_inner_escapes_what_yaml_and_compose_would_read():
    assert compose.quote_inner('a"b\\c$d\ne\u2028') == 'a\\"b\\\\c$$d\\u000ae\\u2028'
    assert compose.quote("plain") == '"plain"'


@pytest.mark.skipif(not NODE, reason="node isn't installed")
def test_the_page_escapes_passwords_as_the_server_does():
    """setup.js's quoteInner, run in node, against compose.quote_inner, for the passwords people have."""
    js = (ROOT / "lineagebox/setup/static/setup.js").read_text(encoding="utf-8")
    body = re.search(r"function quoteInner\(text\) \{.*?\n\}", js, re.S).group(0)
    samples = ['pa$$w"rd', "back\\slash", "tab\tnew\nline", "ünïcødé €", "\x7f\x85\u2028\ufeff", "${HOME}", "😀"]
    out = subprocess.run([NODE, "-e", body + "\nprocess.stdout.write(JSON.stringify(JSON.parse(process.argv[1])"
                          ".map(quoteInner)))", json.dumps(samples)], capture_output=True, text=True, encoding="utf-8",
                         timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [compose.quote_inner(s) for s in samples]


def test_a_share_is_a_docker_volume_and_its_password_is_filled_in_later(tmp_path):
    share = {"kind": "smb", "name": "Grandma's PC", "server": r"\\192.168.1.30\Pictures", "username": "anna",
             "has_password": True, "writable": False}
    p = plan(sources=[answers()["sources"][0], share])
    assert errors(p) == {}
    s = p.sources[1]
    assert (s.server, s.share, s.volume, s.target) == ("192.168.1.30", "Pictures", "smb-grandma-s-pc",
                                                       "/photos/Grandma's PC")
    page = compose.render(p)
    assert compose.secret(1) in page and "password=@@password:1@@" in page
    saved = compose.render(p, {1: 'se"cr$t'})
    assert compose.secret(1) not in saved
    c = config(saved, tmp_path)
    opts = c["volumes"]["smb-grandma-s-pc"]["driver_opts"]
    assert opts["device"] == "//192.168.1.30/Pictures" and opts["type"] == "cifs"
    assert opts["o"].startswith('username=anna,password=se"cr$t,vers=3.0,uid=1026,gid=100')
    for name in ("web", "worker", "copies"):   # read-only everywhere, as asked
        mounts = {m["target"]: m for m in c["services"][name]["volumes"]}
        assert mounts["/photos/Grandma's PC"]["read_only"] is True
    assert c["services"]["web"]["environment"]["LINEAGEBOX_WEB_NETWORK_PATHS"] == \
        "Grandma's PC=\\\\192.168.1.30\\Pictures"


@pytest.mark.parametrize("server, share, expected", [
    ("//nas/photos", "", ("nas", "photos")), ("smb://nas/photos/family", "", ("nas", "photos/family")),
    ("nas", "\\photos\\", ("nas", "photos")), (r"\\nas", "photos", ("nas", "photos"))])
def test_shares_are_understood_however_they_are_pasted(server, share, expected):
    assert compose.smb_parts(server, share) == expected


def test_a_guest_share_needs_no_login():
    p = plan(sources=[{"kind": "smb", "name": "nas", "server": "nas", "share": "public"}])
    assert 'o: "guest,vers=3.0' in compose.render(p)


def test_ai_helpers_in_docker_and_elsewhere(tmp_path):
    p = plan(ollama={"where": "docker", "gpu": "nvidia"}, whisper={"where": "docker", "model": "ggml-small.bin",
                                                                   "language": "de"})
    c = config(compose.render(p), tmp_path)
    env = c["services"]["web"]["environment"]
    assert env["LINEAGEBOX_OLLAMA_URL"] == "http://ollama:11434" and env["LINEAGEBOX_WHISPER_URL"] == "http://whisper:9300"
    assert env["LINEAGEBOX_WHISPER_LANGUAGE"] == "de"
    assert c["services"]["ollama"]["deploy"]["resources"]["reservations"]["devices"][0]["driver"] == "nvidia"
    assert c["services"]["whisper"]["command"] == ["--model", "ggml-small.bin"]
    assert c["services"]["whisper"]["image"] == "ghcr.io/mutilator/lineagebox-whisper:latest"
    p = plan(ollama={"where": "remote", "url": "192.168.1.10"}, whisper={"where": "remote", "url": "pc:9300/"})
    assert (p.ollama_url, p.whisper_url) == ("http://192.168.1.10:11434", "http://pc:9300")
    p = plan(ollama={"where": "docker", "gpu": "amd"})
    text = compose.render(p)
    assert f"ollama/ollama:{compose.OLLAMA_VERSION}-rocm" in text and "/dev/kfd" in text
    assert "LINEAGEBOX_OLLAMA_URL" not in compose.render(plan()) and "ollama:" not in compose.render(plan())


def test_host_networking_for_routers(tmp_path):
    p = plan(system="openwrt", host_network=True, host_address="192.168.1.1", port=8080,
             ollama={"where": "docker"}, whisper={"where": "docker"})
    assert errors(p) == {}
    c = config(compose.render(p), tmp_path)
    web = c["services"]["web"]
    assert web["network_mode"] == "host" and "ports" not in web and "extra_hosts" not in web
    assert web["command"] == ["serve", "--host", "192.168.1.1", "--port", "8080"]
    assert web["environment"]["LINEAGEBOX_OLLAMA_URL"] == "http://127.0.0.1:11434"
    assert c["services"]["ollama"]["ports"][0]["host_ip"] == "127.0.0.1"
    assert "host_address" in errors(plan(host_network=True, host_address="router"))


def test_photos_of_several_owners_run_as_root_with_three_powers(tmp_path):
    c = config(compose.render(plan(root=True, puid=1026)), tmp_path)
    for name in ("web", "worker", "copies"):
        s = c["services"][name]
        assert s["user"] == "0:0" and sorted(s["cap_add"]) == ["CHOWN", "DAC_OVERRIDE", "FOWNER"]


def test_one_version_and_no_updater():
    text = compose.render(plan(version="v1.10.3", updater=False))
    assert "ghcr.io/mutilator/lineagebox:1.10.3" in text and "updater" not in text.split("services:")[1]
    assert "version" in errors(plan(version="newest"))


def test_network_paths_from_the_nas_name():
    p = plan(host_name="mynas", sources=[{"kind": "folder", "name": "local", "path": "/volume1/photo/Family"}])
    assert p.network_paths == {"local": "\\\\mynas\\photo\\Family"}
    assert plan().network_paths == {}   # no name, no guess
    assert plan(host_name="mynas", network_paths={"local": r"\\other\x"}).network_paths == {"local": r"\\other\x"}


# ---- what is wrong
def test_what_people_get_wrong_is_said_beside_it():
    assert "data" in errors(plan(data="/volume1/photo/lineagebox"))   # the catalog inside the photos
    assert "sources.1.name" in errors(plan(sources=[{"kind": "folder", "name": "local", "path": "/a"},
                                                    {"kind": "folder", "name": "Local", "path": "/b"}]))
    assert "sources.0.name" in errors(plan(sources=[{"kind": "folder", "name": "a/b", "path": "/a"}]))
    assert "sources.0.name" in errors(plan(sources=[{"kind": "folder", "name": "Photos-Removed", "path": "/a"}]))
    assert "sources.1.path" in errors(plan(sources=[{"kind": "folder", "name": "a", "path": "/v/photo"},
                                                    {"kind": "folder", "name": "b", "path": "/v/photo/2012"}]))
    assert "sources.0.path" in errors(plan(sources=[{"kind": "folder", "name": "a", "path": "photos"}]))
    assert "sources.0.username" in errors(plan(sources=[{"kind": "smb", "name": "n", "server": "s", "share": "x",
                                                         "username": "a,b"}]))
    assert "sources" in errors(plan(sources=[]))
    assert "tz" in errors(plan(tz="Berlin time"))
    assert "port" in errors(plan(port=70000))
    assert "puid" in errors(plan(puid="anna"))
    assert errors(plan(sources=[{"kind": "folder", "name": "local", "path": "./photos"}], data="./data")) == {}
    assert plan(data="/volume1/photo/../docker/x/").data == "/volume1/docker/x"   # worked out before it is compared
    assert "data" in errors(plan(data="/volume1/docker/lineagebox/../../photo/catalog"))
    assert plan(data='"D:\\LineageBox\\data"').data == "D:/LineageBox/data"


def test_machines_that_cant_run_it_are_said_so():
    assert "32-bit" in errors(plan(model="DS218j"))["model"]
    assert "Container Manager" in warnings(plan(model="DS218"))["model"]
    assert "memory" in warnings(plan(model="DS220j"))["model"]
    assert "ollama.where" in warnings(plan(model="DS224+", ollama={"where": "docker"}))
    assert "model" not in errors(plan(model="DS920+")) | warnings(plan(model="DS920+"))


def test_root_without_the_root_choice_is_questioned_but_not_when_rootless():
    assert "puid" in warnings(plan(puid=0, pgid=0))
    assert "puid" not in warnings(plan(system="rootless", puid=0, pgid=0))


def test_the_steps_say_how_to_start_it_on_that_machine():
    p = plan(host_name="mynas", sources=[{"kind": "folder", "name": "local", "path": "/volume1/photos", "new": True,
                                          "size_gb": 750}])
    steps = compose.steps(p)
    titles = [s["title"] for s in steps]
    assert titles[0].startswith("Make the shared folder") and "750 GB" in " ".join(steps[0]["lines"])
    folders = next(s for s in steps if s["code"].startswith("sudo mkdir"))
    assert "/volume1/photos" not in folders["code"]   # the NAS makes that one, as a shared folder
    assert 'sudo chown 1026:100 "/volume1/docker/lineagebox/data"' in folders["code"]
    start = next(s for s in steps if s["title"] == "Start LineageBox")
    assert any("Container Manager" in line for line in start["lines"])
    assert "http://mynas:8000" in steps[-1]["lines"][0]
    no_size = compose.steps(plan(sources=[{"kind": "folder", "name": "l", "path": "/volume1/photos", "new": True}]))
    assert not any("{size}" in line or "quota" in line for line in no_size[0]["lines"])


# ---- the machine, through /host
@pytest.fixture
def nas(tmp_path):
    """A Synology's root as the setup container sees it at /host."""
    root = tmp_path / "host"
    (root / "etc.defaults").mkdir(parents=True)
    (root / "etc").mkdir()
    (root / "etc.defaults/synoinfo.conf").write_text('company_title="Synology"\nupnpmodelname="DS920+"\n')
    (root / "etc/hostname").write_text("mynas\n")
    (root / "etc/passwd").write_text("root:x:0:0::/root:/bin/sh\nadmin:x:1024:100::/var/services/homes/admin:/sbin/"
                                     "nologin\nanna:x:1026:100::/var/services/homes/anna:/bin/sh\n")
    (root / "etc/group").write_text("root:x:0:\nusers:x:100:\nadministrators:x:101:admin,anna\n")
    for d in ("volume1/photo/2012", "volume1/photo/@eaDir", "volume1/docker/lineagebox", "proc", "sys"):
        (root / d).mkdir(parents=True)
    return root


def test_the_nas_and_its_people_are_recognized(nas):
    seen = hostlib.Host(nas, nas / "nothing").detect(SYSTEMS)
    assert (seen["system"], seen["model"], seen["host_name"]) == ("synology", "DS920+", "mynas")
    anna = next(u for u in seen["users"] if u["name"] == "anna")
    assert (anna["uid"], anna["gid"], anna["group"]) == (1026, 100, "users")
    assert anna["groups"] == [{"gid": 101, "name": "administrators"}]
    assert [u["name"] for u in seen["users"]] == ["admin", "anna"]   # not root
    assert seen["install_dir"] is None and seen["can_save"] is False


def test_folders_are_browsed_without_the_systems_own(nas):
    h = hostlib.Host(nas, nas / "nothing")
    top = h.browse("/")
    assert [f["name"] for f in top["folders"]] == ["volume1"]   # not proc or sys
    photo = h.browse("/volume1/photo")
    assert [f["name"] for f in photo["folders"]] == ["2012"] and photo["parent"] == "/volume1"
    assert photo["exists"] and "free_gb" in photo
    assert "error" in h.browse("/volume1/nothing")
    assert h.browse("/volume1/photo/2012")["folders"][0:] == []


def test_links_are_followed_inside_the_machines_root(nas):
    try:
        (nas / "share").mkdir()
        os.symlink("/volume1/photo", nas / "share" / "Photos")   # QNAP's /share/Photos, an absolute link
        os.symlink("../../etc", nas / "volume1" / "up")
    except OSError:
        pytest.skip("this system won't make symbolic links")
    h = hostlib.Host(nas, nas / "nothing")
    assert h.inside("/share/Photos/2012") == nas / "volume1" / "photo" / "2012"
    assert h.inside("/volume1/up/hostname") == nas / "etc" / "hostname"
    assert h.inside("/../../../etc") == nas / "etc"   # never above the root
    assert h.inside("relative") is None


def test_where_the_setup_was_started_is_found_from_the_mounts(nas, tmp_path):
    install = nas / "volume1" / "docker" / "lineagebox"   # the same folder, seen at /install and under /host
    info = tmp_path / "mountinfo"
    info.write_text(f"20 1 0:50 / {nas.as_posix()} ro - ext4 /dev/md0 ro\n"
                    f"21 20 0:51 / {nas.as_posix()}/volume1 ro - btrfs /dev/md2 ro\n"
                    f"22 20 0:60 / {nas.as_posix()}/volume1/old\\040share ro - cifs //old/share ro\n"
                    f"30 1 0:51 /docker/lineagebox {install.as_posix()} rw - btrfs /dev/md2 rw\n")
    h = hostlib.Host(nas, install, info)
    assert h.install_dir() == "/volume1/docker/lineagebox"
    assert h.fs_type("/volume1/photo") == "btrfs" and h.fs_type("/etc") == "ext4"
    assert h.fs_type("/volume1/old share/x") == "cifs" and h.is_network("/volume1/old share")
    assert hostlib.Host(nas, nas / "elsewhere", info).install_dir() is None


def test_who_can_write_by_the_permissions():
    st = SimpleNamespace(st_uid=1026, st_gid=100, st_mode=0o40750)
    assert hostlib.writable(st, 1026, 1) and not hostlib.writable(st, 1027, 100)
    assert hostlib.writable(SimpleNamespace(st_uid=0, st_gid=100, st_mode=0o40770), 1027, 100)
    assert hostlib.writable(st, 0, 0)


def test_mountinfo_paths_with_spaces():
    m = hostlib.mounts("36 35 98:0 / /host/My\\040Photos rw - cifs //nas/p rw\nnonsense\n")
    assert m == [{"dev": "98:0", "root": "/", "point": "/host/My Photos", "fstype": "cifs"}]


# ---- the server
def test_hosted_it_only_makes_files():
    c = TestClient(server.create_app(hosted=True))
    page = c.get("/")
    assert page.status_code == 200 and "script-src 'self'" in page.headers["content-security-policy"]
    assert c.get("/static/setup.js").status_code == 200
    info = c.get("/api/info").json()
    assert info["mode"] == "hosted" and not info["can_browse"] and not info["can_save"] and info["detected"] is None
    assert not info["locked"] and not info["first"]
    assert any(s["id"] == "synology" for s in info["systems"])
    made = c.post("/api/compose", json={"answers": answers()})
    assert made.status_code == 200 and made.headers["cache-control"] == "no-store"
    body = made.json()
    assert body["compose"].startswith("# LineageBox") and body["problems"] == [] and body["steps"]
    assert c.get("/api/browse").status_code == 404
    assert c.post("/api/save", json={"answers": answers()}).status_code == 404
    assert c.post("/api/check", json={"kind": "ollama", "url": "localhost"}).status_code == 404
    assert c.post("/api/compose", content="x" * 70000, headers={"content-type": "application/json"}).status_code == 413
    assert c.post("/api/compose", content="[1]", headers={"content-type": "application/json"}).status_code == 400


def test_on_a_machine_it_wants_its_key_to_look(nas):
    c = TestClient(server.create_app(host=hostlib.Host(nas, nas / "nothing"), key="k3y"))
    assert c.get("/").status_code == 200   # the page itself is nothing
    missing = answers(sources=[{"kind": "folder", "name": "local", "path": "/volume1/nothing"}])
    for given in ({}, {"x-setup-key": "wrong"}):   # without its key: files from answers, as the hosted one makes
        info = c.get("/api/info", headers=given).json()
        assert info["locked"] and info["wrong_key"] == bool(given)
        assert info["detected"] is None and not info["can_browse"] and not info["can_save"]
        assert c.get("/api/browse?path=/", headers=given).status_code == 401
        assert c.post("/api/check", headers=given, json={"kind": "ollama", "url": "localhost"}).status_code == 401
        made = c.post("/api/compose", headers=given, json={"answers": missing}).json()
        assert made["compose"].startswith("# LineageBox") and not made["problems"]   # nothing of the machine
    k = {"x-setup-key": "k3y"}
    info = c.get("/api/info", headers=k).json()
    assert info["mode"] == "local" and info["can_browse"] and info["detected"]["system"] == "synology"
    assert not info["locked"]
    assert [f["name"] for f in c.get("/api/browse?path=/volume1", headers=k).json()["folders"]] == ["docker", "photo"]
    said = c.post("/api/compose", headers=k, json={"answers": answers(sources=[{"kind": "folder", "name": "local",
                                                                                "path": "/volume1/nothing"}])}).json()
    assert any(p["field"] == "sources.0.path" and "no folder" in p["text"] for p in said["problems"])


def test_save_writes_the_file_and_the_catalog_folder(nas, tmp_path):
    install = nas / "volume1" / "docker" / "lineagebox"
    c = TestClient(server.create_app(host=hostlib.Host(nas, install, tmp_path / "no-mountinfo"), key="k"))
    k = {"x-setup-key": "k"}
    share = {"kind": "smb", "name": "nas", "server": "pc", "share": "p", "username": "anna", "has_password": True}
    a = answers(sources=[answers()["sources"][0], share])
    done = c.post("/api/save", headers=k, json={"answers": a, "secrets": {"1": "s3cret$"}}).json()
    assert done["saved"] == "/volume1/docker/lineagebox/compose.yaml" and done["kept"] is None
    assert done["made"] == [{"path": "/volume1/docker/lineagebox/data", "new": True}] and done["todo"] == []
    assert done["not_given"] == []
    text = (install / "compose.yaml").read_text(encoding="utf-8")
    assert "password=s3cret$$," in text and "@@password" not in text
    assert (install / "data").is_dir()
    if sys.platform != "win32":
        assert (install / "compose.yaml").stat().st_mode & 0o777 == 0o600   # it holds a password
    if hasattr(os, "geteuid") and os.geteuid() == 0:   # as in its container: given to LineageBox's user
        for made in (install / "compose.yaml", install / "data"):
            assert (made.stat().st_uid, made.stat().st_gid) == (1026, 100)
    again = c.post("/api/save", headers=k, json={"answers": a, "secrets": {}})
    assert again.status_code == 409 and again.json()["exists"]
    replaced = c.post("/api/save", headers=k, json={"answers": a, "secrets": {}, "replace": True}).json()
    assert replaced["kept"].startswith("compose.yaml.before-") and (install / replaced["kept"]).exists()
    assert replaced["made"] == [{"path": "/volume1/docker/lineagebox/data", "new": False}]
    bad = c.post("/api/save", headers=k, json={"answers": answers(port="x")})
    assert bad.status_code == 400 and bad.json()["saved"] is None
    elsewhere = c.post("/api/save", headers=k, json={"answers": answers(data="/volume2/catalog"), "replace": True})
    assert elsewhere.json().get("todo") == ["/volume2/catalog"], elsewhere.json()
    out = answers(data="/volume1/docker/lineagebox/../../../../../outside")
    sneaky = c.post("/api/save", headers=k, json={"answers": out, "replace": True}).json()
    assert sneaky["todo"] == ["/outside"] and sneaky["made"] == []   # never made outside the folder it was started in
    assert not (tmp_path / "outside").exists() and not (nas / "outside").exists()


def test_started_by_the_image_its_file_takes_the_wizards_place(nas, tmp_path):
    install = nas / "volume1" / "docker" / "lineagebox"
    c = TestClient(server.create_app(host=hostlib.Host(nas, install, tmp_path / "no-mountinfo"), key="k", first=True))
    k = {"x-setup-key": "k"}
    info = c.get("/api/info", headers=k).json()
    assert info["first"] and not info["by_hand"]

    def start(made):
        return next(s for s in made.json()["steps"] if s["title"] == "Start LineageBox")

    saved = start(c.post("/api/compose", headers=k, json={"answers": answers()}))
    assert saved["lines"][0].startswith("Save it above") and "Container Manager" in " ".join(saved["lines"])
    assert saved["code"].endswith("sudo docker compose up -d")
    by_hand = start(c.post("/api/compose", json={"answers": answers()}))   # without the key there is no Save
    assert by_hand["lines"][0] == ("Put the file in /volume1/docker/lineagebox as compose.yaml, in place of the one "
                                   "that started this wizard.")
    (install / "compose.yaml").write_text("# the wizard's\n", encoding="utf-8")
    done = c.post("/api/save", headers=k, json={"answers": answers(), "replace": True}).json()
    assert done["kept"].startswith("compose.yaml.before-")
    assert "LINEAGEBOX_SETUP: done" in (install / "compose.yaml").read_text(encoding="utf-8")
    (install / ".env").write_text("PHOTOS=/volume1/photo\n", encoding="utf-8")
    assert c.get("/api/info").json()["by_hand"]   # set up here by hand before the wizard: the page says what to do
    elsewhere = start(TestClient(server.create_app(hosted=True)).post("/api/compose", json={"answers": answers()}))
    assert "Container Manager → Project → Create" in " ".join(elsewhere["lines"])   # a project of its own
    bare = TestClient(server.create_app(host=hostlib.Host(tmp_path / "no", tmp_path / "no"), key="k", first=True))
    alone = start(bare.post("/api/compose", json={"answers": answers()}))   # `docker run <image>`: no compose.yaml
    assert "Container Manager → Project → Create" in " ".join(alone["lines"])


def test_setup_needs_no_config_or_catalog(monkeypatch):
    called = {}
    monkeypatch.setattr(server, "serve", lambda *a: called.setdefault("args", a))
    monkeypatch.setenv("LINEAGEBOX_CONFIG", "/nowhere/config.toml")
    cli.main(["setup", "--hosted", "--port", "9999"])
    assert called["args"][:3] == (True, "0.0.0.0", 9999) and called["args"][-1] == 0
