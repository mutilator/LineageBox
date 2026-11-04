"""Versions, the daily look for a newer release, the catalog's copy before an upgrade, the Status page's Update now,
and the updater service, against a Docker that is faked here."""
import dataclasses
import hashlib
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from lineagebox import __version__, db, settings, updater, updates
from lineagebox.config import ConfigError
from lineagebox.web import app as webapp
from tests.conftest import LAN

APP = {"X-Requested-With": "lineagebox"}
IMAGE = "ghcr.io/example/lineagebox:latest"


def test_versions_in_order():
    assert updates.newer("0.3.0", "0.2.9") and updates.newer("v1.10.0", "1.9.9")
    assert updates.newer("1.2.0", "1.2.0-rc.1") and updates.newer("1.2.0", "1.2.0.dev0")
    assert not updates.newer("1.2.0", "1.2.0") and not updates.newer("1.1.9", "1.2.0")
    assert not updates.newer("nightly", "1.0.0") and not updates.newer("2.0.0", "main")


def github(answer, status=200, seen=None):
    def handle(request):
        if seen is not None:
            seen.append(str(request.url))
        return httpx.Response(status, json=answer)
    return httpx.Client(transport=httpx.MockTransport(handle))


def test_the_daily_look_for_a_newer_release(con, cfg):
    cfg = dataclasses.replace(cfg, update_repository="example/lineagebox")
    asked = []
    release = {"tag_name": "v99.0.0", "html_url": "https://github.com/example/lineagebox/releases/tag/v99.0.0",
               "published_at": "2030-01-02T03:04:05Z"}
    found = updates.check(con, cfg, github(release, seen=asked))
    assert found["version"] == "99.0.0" and asked == ["https://api.github.com/repos/example/lineagebox/releases/latest"]
    assert updates.available(con)["url"].startswith("https://github.com/") and found["published"] == "2030-01-02"
    updates.check(con, cfg, github(release, seen=asked))
    assert len(asked) == 1  # once a day
    # a link that isn't GitHub's is not shown; nor anything when the repository has no releases yet
    bad = updates.check(con, cfg, github({**release, "html_url": "javascript:alert(1)"}), force=True)
    assert bad["url"] == ""
    assert "no releases" in updates.check(con, cfg, github({}, 404), force=True)["problem"]
    assert updates.available(con) is None
    # this version, or switched off: nothing to say
    updates.check(con, cfg, github({**release, "tag_name": f"v{__version__}"}), force=True)
    assert updates.available(con) is None
    assert updates.check(con, dataclasses.replace(cfg, update_check=False), github(release), force=True) is None


def test_a_copy_of_the_catalog_before_an_upgrade_and_none_for_a_downgrade(cfg):
    con = db.connect(cfg, migrate_schema=False)
    db.migrate(con, cfg, upto=10)  # a catalog of an older version
    db.migrate(con, cfg)
    copies = list((cfg.data_dir / db.BACKUPS).glob("catalog-v10-*.db"))
    assert len(copies) == 1
    with sqlite3.connect(copies[0]) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 10
    newest = con.execute("PRAGMA user_version").fetchone()[0]
    con.execute(f"PRAGMA user_version = {newest + 1}")  # as a newer LineageBox would leave it
    con.commit()
    con.close()
    with pytest.raises(ConfigError, match="upgraded by a newer version"):
        db.connect(cfg)


# ---- the Status page ----------------------------------------------------------------------------------------------

def updater_says(cfg, **status):
    folder = updates.folder(cfg)
    folder.mkdir(parents=True, exist_ok=True)
    updater._write_status(folder, {"state": "idle", **status})


def test_update_now_on_the_status_page(con, cfg):
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get("/status").text
    assert f"This is LineageBox {__version__}" in page
    assert "git pull" in page or "docker compose pull" in page  # the steps for this install, from source or Docker
    assert 'data-api="/api/update"' not in page  # no updater here: the steps to update by hand instead
    assert client.post("/api/update", headers=APP).status_code == 409
    updater_says(cfg)
    assert 'data-api="/api/update"' in client.get("/status").text
    assert "Updating" in client.post("/api/update", headers=APP).json()["message"]
    assert (updates.folder(cfg) / "requested").exists()
    assert client.get("/api/update").json()["updater"]["requested"]
    assert "already on its way" in client.post("/api/update", headers=APP).json()["message"]


def test_admins_are_told_of_a_newer_release(con, cfg):
    settings.put(con, updates.KEY, {"checked_at": "2030-01-01T00:00:00", "version": "99.0.0",
                                    "url": "https://github.com/example/lineagebox/releases/tag/v99.0.0"})
    client = TestClient(webapp.create_app(cfg), client=LAN)
    assert "Update available" in client.get("/timeline").text
    assert "LineageBox 99.0.0 is out" in client.get("/status").text
    settings.save(con, cfg, {"update_check": False})
    assert "Update available" not in TestClient(webapp.create_app(cfg), client=LAN).get("/timeline").text


# ---- the updater, against a fake Docker -----------------------------------------------------------------------------

class FakeDocker:
    """Enough of the Docker Engine API for updater.py: containers by id and name, images by name and id, a registry
    of the newest image for each name."""

    def __init__(self):
        self.registry, self.local, self.images, self.containers, self.calls = {}, {}, {}, {}, []
        self.exits = set()  # image ids whose containers stop straight away

    def image(self, name, image_id, cmd=("serve",)):
        self.images[image_id] = {"Id": image_id, "Config": {
            "Env": ["PATH=/venv/bin", "LINEAGEBOX_CONFIG=/app"], "Entrypoint": ["python", "-m", "lineagebox"],
            "Labels": {"org.opencontainers.image.title": "LineageBox"}, "Cmd": list(cmd), "WorkingDir": "/app",
            "ExposedPorts": {"8000/tcp": {}}}}
        self.local[name] = image_id

    def container(self, name, image, service, labelled=True, project="lineagebox", cmd=("serve",)):
        cid = hashlib.sha256(f"{name} {len(self.containers)}".encode()).hexdigest()
        image_id = self.local[image]
        labels = {"com.docker.compose.project": project, "com.docker.compose.service": service,
                  "com.docker.compose.image": image_id, "org.opencontainers.image.title": "LineageBox"}
        if labelled:
            labels["lineagebox.update"] = "true"
        self.containers[cid] = {
            "Id": cid, "Name": f"/{name}", "Image": image_id, "State": {"Running": True, "Status": "running"},
            "Config": {"Image": image, "Hostname": cid[:12],
                       "Env": ["PATH=/venv/bin", "LINEAGEBOX_CONFIG=/app", "TZ=UTC"], "Labels": labels,
                       "Entrypoint": ["python", "-m", "lineagebox"], "Cmd": list(cmd), "WorkingDir": "/app",
                       "ExposedPorts": {"8000/tcp": {}}},
            "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}, "NetworkMode": "lineagebox_default",
                           "Binds": ["/srv/photos:/photos/local"]},
            "NetworkSettings": {"Networks": {"lineagebox_default": {"Aliases": [service, cid[:12]]}}}}
        return cid

    def find(self, key):
        return self.containers.get(key) or next((c for c in self.containers.values()
                                                 if c["Name"] == f"/{key}" or c["Id"].startswith(key)), None)

    def handle(self, request: httpx.Request) -> httpx.Response:
        method, path, q = request.method, request.url.path, dict(request.url.params)
        self.calls.append(f"{method} {path}" + (f"?name={q['name']}" if "name" in q else ""))
        body = json.loads(request.content) if request.content else None
        parts = path.strip("/").split("/")
        if path == "/containers/json":
            wanted = json.loads(q["filters"])["label"]
            out = [{"Id": c["Id"], "Names": [c["Name"]], "State": c["State"]["Status"]}
                   for c in self.containers.values()
                   if all(("=" in w and c["Config"]["Labels"].get(w.split("=")[0]) == w.split("=", 1)[1]) or
                          ("=" not in w and w in c["Config"]["Labels"]) for w in wanted)]
            return httpx.Response(200, json=out)
        if path == "/images/create":
            name = q["fromImage"]
            if name not in self.registry:
                return httpx.Response(200, text=json.dumps({"error": "pull access denied"}) + "\n")
            before = self.local.get(name)
            self.image(name, self.registry[name])
            if before != self.registry[name] and before not in self.local.values():  # as Docker's containerd store:
                self.images.pop(before, None)  # an image whose last name moved on is gone, though containers run on it
            return httpx.Response(200, text=json.dumps({"status": "Downloaded newer image"}) + "\n")
        if parts[0] == "images":
            ref = "/".join(parts[1:-1])
            image_id = self.local.get(ref, ref)
            return httpx.Response(200, json=self.images[image_id]) if image_id in self.images else \
                httpx.Response(404, json={"message": "No such image"})
        if path == "/containers/create":
            if self.find(q["name"]):
                return httpx.Response(409, json={"message": "name in use"})
            image_id = self.local[body["Image"]]
            cid = hashlib.sha256(f"{q['name']} new {len(self.containers)}".encode()).hexdigest()
            self.containers[cid] = {"Id": cid, "Name": f"/{q['name']}", "Image": image_id, "created": body,
                                    "State": {"Running": False, "Status": "created"},
                                    "Config": {**body, "Labels": body.get("Labels") or {}},
                                    "HostConfig": body["HostConfig"], "NetworkSettings": {"Networks": {}}}
            return httpx.Response(201, json={"Id": cid})
        if parts[0] == "networks":
            return httpx.Response(200)
        c = self.find(parts[1])
        if c is None:
            return httpx.Response(404, json={"message": "No such container"})
        if method == "GET":
            return httpx.Response(200, json=c)
        if method == "DELETE":
            del self.containers[c["Id"]]
            return httpx.Response(204)
        action = parts[2]
        if action == "stop":
            c["State"] = {"Running": False, "Status": "exited"}
        elif action == "start":
            running = c["Image"] not in self.exits
            c["State"] = {"Running": running, "Status": "running" if running else "exited",
                          "ExitCode": 0 if running else 1}
        elif action == "rename":
            c["Name"] = f"/{q['name']}"
        elif action == "update":
            c["HostConfig"]["RestartPolicy"] = body["RestartPolicy"]
        return httpx.Response(204)


@pytest.fixture
def fake(monkeypatch):
    d = FakeDocker()
    d.image(IMAGE, "sha256:old")
    d.image("ollama/ollama:0.40.11", "sha256:ollama")
    ids = {"web": d.container("lineagebox-web-1", IMAGE, "web"),
           "worker": d.container("lineagebox-worker-1", IMAGE, "worker", cmd=("worker",)),
           "updater": d.container("lineagebox-updater-1", IMAGE, "updater", cmd=("updater",)),
           "ollama": d.container("lineagebox-ollama-1", "ollama/ollama:0.40.11", "ollama", labelled=False),
           "other": d.container("someone-else-1", IMAGE, "web", project="another")}
    monkeypatch.setattr(updater.socket, "gethostname", lambda: ids["updater"][:12])
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)
    d.ids = ids
    return d


def run(d):
    docker = updater.Docker(httpx.Client(transport=httpx.MockTransport(d.handle), base_url="http://docker"))
    return updater.update(docker, docker.call("GET", f"/containers/{d.ids['updater'][:12]}/json"))


def by_name(d, name):
    return next(c for c in d.containers.values() if c["Name"] == f"/{name}")


def test_nothing_newer_changes_nothing(fake):
    fake.registry[IMAGE] = "sha256:old"
    before = {cid: c["Image"] for cid, c in fake.containers.items()}
    status = run(fake)
    assert status["state"] == "done" and "up to date" in status["message"]
    assert {cid: c["Image"] for cid, c in fake.containers.items()} == before


def test_a_newer_image_is_installed_and_the_updater_goes_last(fake):
    fake.registry[IMAGE] = "sha256:new"
    status = run(fake)
    assert status["state"] == "done" and status["updated"] == ["worker", "web", "updater"]
    assert status["restarting_myself"]
    web = by_name(fake, "lineagebox-web-1")
    assert web["Image"] == "sha256:new" and web["State"]["Running"]
    made = web["created"]
    assert made["Env"] == ["TZ=UTC"]  # what came from the old image is left to the new one
    assert "Hostname" not in made and "Cmd" not in made and "Entrypoint" not in made
    assert made["Labels"]["com.docker.compose.image"] == "sha256:new"
    assert made["Labels"]["lineagebox.update"] == "true" and "org.opencontainers.image.title" not in made["Labels"]
    assert made["HostConfig"]["Binds"] == ["/srv/photos:/photos/local"]
    assert made["NetworkingConfig"]["EndpointsConfig"]["lineagebox_default"]["Aliases"] == ["web"]
    assert by_name(fake, "lineagebox-worker-1")["created"]["Cmd"] == ["worker"]  # its own command, not the image's
    # the others are left alone: Ollama (not labelled) and another project's container
    assert fake.containers[fake.ids["ollama"]]["Image"] == "sha256:ollama"
    assert fake.containers[fake.ids["other"]]["Image"] == "sha256:old"
    # the old web and worker are gone; the old updater waits for its successor, and won't be restarted by Docker
    assert not any(c["Name"] in ("/lineagebox-web-1-old", "/lineagebox-worker-1-old") for c in fake.containers.values())
    old_updater = fake.containers[fake.ids["updater"]]
    assert old_updater["Name"] == "/lineagebox-updater-1-old"
    assert old_updater["HostConfig"]["RestartPolicy"] == {"Name": "no"}
    assert by_name(fake, "lineagebox-updater-1")["State"]["Running"]
    # the new updater, starting, removes the old one
    docker = updater.Docker(httpx.Client(transport=httpx.MockTransport(fake.handle), base_url="http://docker"))
    updater._tidy(docker, by_name(fake, "lineagebox-updater-1"))
    assert fake.ids["updater"] not in fake.containers


def test_an_image_docker_has_forgotten_is_taken_from_the_new_one(fake):
    """Pulled by hand before the update (or by one that stopped half way): Docker's containerd image store no longer has
    the image the containers run on. What the new image sets is then taken as what came from an image."""
    fake.registry[IMAGE] = "sha256:new"
    fake.image(IMAGE, "sha256:new")
    fake.images.pop("sha256:old")
    status = run(fake)
    assert status["state"] == "done" and status["updated"] == ["worker", "web", "updater"]
    made = by_name(fake, "lineagebox-web-1")["created"]
    assert made["Env"] == ["TZ=UTC"] and "org.opencontainers.image.title" not in made["Labels"]
    assert by_name(fake, "lineagebox-worker-1")["created"]["Cmd"] == ["worker"]


def test_the_image_as_compose_notes_it():
    """So that `docker compose up` after an update finds nothing to change. Docker's classic image store: the id; its
    containerd store (Docker Desktop's): the id is the image index's, and compose notes the platform's manifest."""
    assert updater.compose_id({"Id": "sha256:config", "Os": "linux", "Architecture": "amd64"}) == "sha256:config"
    board = {"Id": "sha256:index", "Os": "linux", "Architecture": "arm64", "Variant": "v8", "Manifests": [
        {"Kind": "attestation", "ID": "sha256:attestation", "Available": True},
        {"Kind": "image", "ID": "sha256:amd64", "Available": True,
         "ImageData": {"Platform": {"os": "linux", "architecture": "amd64"}}},
        {"Kind": "image", "ID": "sha256:arm64", "Available": True,
         "ImageData": {"Platform": {"os": "linux", "architecture": "arm64", "variant": "v8"}}}]}
    assert updater.compose_id(board) == "sha256:arm64"


def test_a_new_version_that_wont_start_is_put_back(fake):
    fake.registry[IMAGE] = "sha256:new"
    fake.exits.add("sha256:new")
    status = run(fake)
    assert status["state"] == "failed" and "put back" in status["message"]
    worker = fake.containers[fake.ids["worker"]]  # the first one tried: back as it was, running
    assert worker["Name"] == "/lineagebox-worker-1" and worker["State"]["Running"] and worker["Image"] == "sha256:old"
    assert not any(c["Name"].endswith("-old") for c in fake.containers.values())
    assert fake.containers[fake.ids["web"]]["Image"] == "sha256:old"  # nothing after it was touched


def test_an_image_built_here_cannot_be_downloaded(fake):
    status = run(fake)  # nothing in the registry
    assert status["state"] == "failed" and "could not be downloaded" in status["message"]
    assert all(c["State"]["Running"] for c in fake.containers.values())


def test_an_update_stopped_half_way_is_put_right(fake):
    docker = updater.Docker(httpx.Client(transport=httpx.MockTransport(fake.handle), base_url="http://docker"))
    fake.containers[fake.ids["worker"]]["Name"] = "/lineagebox-worker-1-old"  # renamed, the new one never made
    fake.containers[fake.ids["worker"]]["State"] = {"Running": False, "Status": "exited"}
    updater._tidy(docker, fake.containers[fake.ids["updater"]])
    assert fake.containers[fake.ids["worker"]]["Name"] == "/lineagebox-worker-1"
    assert fake.containers[fake.ids["worker"]]["State"]["Running"]


def test_the_updater_only_looks_for_the_request_file(fake, tmp_path):
    """It acts when the file is there and never reads it; it says it is there every minute."""
    fake.registry[IMAGE] = "sha256:old"
    docker = updater.Docker(httpx.Client(transport=httpx.MockTransport(fake.handle), base_url="http://docker"))
    folder = tmp_path / "update"
    updater.main(folder, docker, rounds=1)
    assert json.loads((folder / "status.json").read_text())["state"] == "idle"
    assert not any("images/create" in call for call in fake.calls)
    (folder / "requested").write_text("docker run --privileged evil")  # whatever it holds, it is never read
    updater.main(folder, docker, rounds=1)
    assert not (folder / "requested").exists()
    status = json.loads((folder / "status.json").read_text())
    assert status["state"] == "done" and "up to date" in status["message"]
    assert [c for c in fake.calls if c.startswith("POST /images/create")] == ["POST /images/create"]
