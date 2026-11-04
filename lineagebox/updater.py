"""The updater service (compose.yaml): newer images for LineageBox's own containers, when an admin presses Update now.

It is the one part of LineageBox with Docker access, which is the same as root on that machine, so it does only this,
and nothing it reads can change what it does:
- It has no network (network_mode: none) and reads no setting, catalog or config file. An update is asked for by the
  file /update/requested being there; the file is never opened.
- Which containers and images come from Docker itself, as compose made them: the containers labelled lineagebox.update
  in the updater's own compose project, each updated to the newest copy of the image name it already runs. Ollama and
  anything else on the machine are never touched.
- It writes one file, /update/status.json, from which the web app shows how an update went.

An update pulls each container's image again. A container whose image changed is made again with the same settings,
as `docker compose up -d` would, and put back as it was if the new one doesn't start. The updater itself goes last.
"""
import json
import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

SOCKET = "/var/run/docker.sock"
FOLDER = Path("/update")
LABEL = "lineagebox.update"
PROJECT = "com.docker.compose.project"
SERVICE = "com.docker.compose.service"
COMPOSE_IMAGE = "com.docker.compose.image"  # compose's note of the image a container was made from
OLD = "-old"            # a container being replaced, kept until its new one runs
LOOK_EVERY = 5          # seconds between looks for a request
SPACED = 60             # seconds an update must wait after the last one, however often it is asked for
STARTING = 20           # seconds a new container must keep running before its old one is removed


class Failed(Exception):
    """Something Docker refused or that went wrong; the message is shown on the Status page."""


class Missing(Failed):
    """Docker has no such thing: an image whose last name moved on to a newer one, say."""


class Docker:
    """The few Docker Engine API calls an update needs, over the socket, in the daemon's own API version."""

    def __init__(self, http: httpx.Client):
        self.http = http

    def call(self, method: str, path: str, body=None, **params):
        try:
            r = self.http.request(method, path, json=body, params=params or None)
        except httpx.HTTPError as e:
            raise Failed(f"Docker could not be reached ({type(e).__name__})") from None
        if r.status_code == 304:  # already so: stopped, or started
            return None
        if r.status_code >= 400:
            try:
                why = r.json().get("message", r.text)
            except ValueError:
                why = r.text
            raise (Missing if r.status_code == 404 else Failed)(f"Docker: {why.strip()[:300]}")
        return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else None

    def pull(self, image: str) -> None:
        try:
            with self.http.stream("POST", "/images/create", params={"fromImage": image}, timeout=None) as r:
                if r.status_code >= 400:
                    r.read()
                    raise Failed(f"{image} could not be downloaded: {r.text.strip()[:300]}")
                for line in r.iter_lines():
                    if line.strip() and "error" in (msg := json.loads(line)):
                        raise Failed(f"{image} could not be downloaded: {msg['error'][:300]}")
        except httpx.HTTPError as e:
            raise Failed(f"Docker could not be reached ({type(e).__name__})") from None


def main(folder: Path = FOLDER, docker: Docker | None = None, rounds: int | None = None) -> None:
    """Look for a request every few seconds, and say every minute that the updater is there. rounds: tests stop it."""
    docker = docker or Docker(httpx.Client(transport=httpx.HTTPTransport(uds=SOCKET), base_url="http://docker",
                                           timeout=120))
    _prepare(folder)
    status = _read_status(folder)
    status.pop("restarting_myself", None)  # the updater before this one said it was handing over: this is it
    if status.get("state") == "updating":  # stopped half way: _tidy puts back what it had taken down
        status = {"state": "failed", "message": "The last update stopped half way; what it had stopped was started "
                                                "again. Try again."}
    try:
        me = docker.call("GET", f"/containers/{socket.gethostname()}/json")
        _tidy(docker, me)
    except Failed as e:
        status = {"state": "failed", "message": f"The updater can't work: {e}"}
        me = None
    last_run, last_write = -SPACED, 0.0
    while rounds is None or rounds > 0:
        if me is not None and (folder / "requested").exists() and time.monotonic() - last_run >= SPACED:
            (folder / "requested").unlink(missing_ok=True)  # one asked for while this one runs is the next one
            last_run = time.monotonic()
            _write_status(folder, status := {"state": "updating", "message": "Downloading the newest version…"})
            status = update(docker, me, lambda message: _write_status(folder, {"state": "updating",
                                                                              "message": message}))
            last_write = 0.0
        if time.monotonic() - last_write >= 60:
            _write_status(folder, status)
            last_write = time.monotonic()
        if status.get("restarting_myself"):
            return  # the new updater carries on
        time.sleep(LOOK_EVERY)
        if rounds is not None:
            rounds -= 1


def update(docker: Docker, me: dict, say=lambda message: None) -> dict:
    """Pull every labelled container's image; make again those whose image changed, the updater last. Returns the
    status to show."""
    project = me["Config"]["Labels"].get(PROJECT)
    filters = {"label": [LABEL] + ([f"{PROJECT}={project}"] if project else [])}
    found = docker.call("GET", "/containers/json", all="true", filters=json.dumps(filters)) or []
    mine = [docker.call("GET", f"/containers/{c['Id']}/json") for c in found if not _old(c["Names"][0])]
    mine.sort(key=lambda c: (c["Id"] == me["Id"], c["Config"]["Labels"].get(SERVICE) == "web"))
    done, myself, pulled = [], None, {}
    # Each container's image as it is now, read before anything is pulled: in Docker's containerd image store (Docker
    # Desktop's) an image whose name moves on to the newer one is gone at once, though its containers still run on it.
    olds = {}
    for c in mine:
        if c["Image"] not in olds:
            try:
                olds[c["Image"]] = docker.call("GET", f"/images/{c['Image']}/json")
            except Missing:  # gone already (pulled by hand, or by an update that stopped half way)
                olds[c["Image"]] = None
    try:
        for c in mine:
            image = c["Config"]["Image"]
            if image.startswith("sha256:") or "@" in image:
                continue  # run from one exact image: nothing newer to get
            if image not in pulled:  # web, worker, copies and the updater share one
                say(f"Downloading {image}…")
                docker.pull(image)
                pulled[image] = docker.call("GET", f"/images/{image}/json", manifests="1")
            new = pulled[image]["Id"]
            if new == c["Image"]:
                continue
            if c["Id"] == me["Id"]:
                myself = pulled[image]
                continue
            say(f"Restarting {_name(c)} with the new version…")
            _replace(docker, c, pulled[image], olds[c["Image"]])
            done.append(_service(c))
        if myself:
            _replace(docker, me, myself, olds[me["Image"]], myself_too=True)
            done.append(_service(me))
    except Failed as e:
        what = f"Updated {', '.join(done)}; then: {e}" if done else str(e)
        return {"state": "failed", "message": what, "updated": done}
    if not done:
        return {"state": "done", "message": "LineageBox is up to date: there was no newer version to install.",
                "updated": []}
    return {"state": "done", "message": f"Updated {', '.join(done)}.", "updated": done,
            "restarting_myself": bool(myself)}


def _replace(docker: Docker, c: dict, new: dict, old_image: dict | None, myself_too: bool = False) -> None:
    """Make container c again from its image's new version (new: as Docker inspects it), with the same settings; put
    it back if that fails. old_image: the image it runs on, as Docker had it before the pull (None: gone already)."""
    name = _name(c)
    body, more_networks = create_body(c, old_image or _as_before(c, new), new["Id"], compose_id(new))
    if myself_too:  # this process ends once its successor runs: it must not be started again by Docker
        docker.call("POST", f"/containers/{c['Id']}/update", {"RestartPolicy": {"Name": "no"}})
    else:
        docker.call("POST", f"/containers/{c['Id']}/stop", t=60)
    docker.call("POST", f"/containers/{c['Id']}/rename", name=name + OLD)
    new = None
    try:
        new = docker.call("POST", "/containers/create", body, name=name)["Id"]
        for network, endpoint in more_networks.items():
            docker.call("POST", f"/networks/{network}/connect", {"Container": new, "EndpointConfig": endpoint})
        docker.call("POST", f"/containers/{new}/start")
        if not myself_too:
            _keeps_running(docker, new, name)
    except Failed:
        if new:
            docker.call("DELETE", f"/containers/{new}", force="true")
        docker.call("POST", f"/containers/{c['Id']}/rename", name=name)
        if myself_too:
            docker.call("POST", f"/containers/{c['Id']}/update", {"RestartPolicy": c["HostConfig"]["RestartPolicy"]})
        else:
            docker.call("POST", f"/containers/{c['Id']}/start")
        raise
    if not myself_too:  # the old updater is removed by its successor, when it starts (_tidy)
        docker.call("DELETE", f"/containers/{c['Id']}", force="true")


def _as_before(c: dict, new_image: dict) -> dict:
    """What c's old image is taken to have set, when Docker no longer has it: whatever the new image sets, by name. So
    c's values of the environment variables and labels the new image has give way to the new image's, and the same
    goes for its entrypoint, working folder, user and health check where they are the new image's already."""
    image = new_image.get("Config") or {}
    names = {e.split("=", 1)[0] for e in image.get("Env") or []}
    labels = image.get("Labels") or {}
    cfg = c["Config"]
    return {"Config": {**image, "Env": [e for e in cfg.get("Env") or [] if e.split("=", 1)[0] in names],
                       "Labels": {k: v for k, v in (cfg.get("Labels") or {}).items() if k in labels}}}


def compose_id(image: dict) -> str:
    """The image as compose notes it on the containers it makes (COMPOSE_IMAGE), so that `docker compose up` finds
    nothing to change after an update: its id, or with Docker's containerd image store (where the id is the image
    index's) the digest of its manifest for the platform it runs on. image: as inspected with manifests=1."""
    for m in image.get("Manifests") or []:
        platform = (m.get("ImageData") or {}).get("Platform") or {}
        if m.get("Kind") == "image" and m.get("Available", True) and (platform.get("os"), platform.get(
                "architecture"), platform.get("variant")) == (image.get("Os"), image.get("Architecture"),
                                                               image.get("Variant")):
            return m["ID"]
    return image["Id"]


def create_body(c: dict, old_image: dict, new_image: str, noted: str | None = None) -> tuple[dict, dict]:
    """What creating c again from the new image needs: c's settings, less those it only had from its old image, so the
    new image's own apply (its command, environment, labels...). Also the networks to connect it to after the first.
    noted: the new image as compose notes it (compose_id), when it isn't its id."""
    cfg = dict(c["Config"])
    image = old_image.get("Config") or {}
    for key in ("WorkingDir", "User", "Healthcheck"):
        if cfg.get(key) == image.get(key):
            cfg.pop(key, None)
    if cfg.get("Entrypoint") == image.get("Entrypoint"):
        cfg.pop("Entrypoint", None)
        if cfg.get("Cmd") == image.get("Cmd"):
            cfg.pop("Cmd", None)
    from_image = set(image.get("Env") or [])
    cfg["Env"] = [e for e in cfg.get("Env") or [] if e not in from_image]
    image_labels = image.get("Labels") or {}
    cfg["Labels"] = {k: v for k, v in (cfg.get("Labels") or {}).items() if image_labels.get(k) != v}
    if COMPOSE_IMAGE in cfg["Labels"]:
        cfg["Labels"][COMPOSE_IMAGE] = noted or new_image  # so `docker compose up` sees nothing to change
    cfg["Volumes"] = {k: v for k, v in (cfg.get("Volumes") or {}).items() if k not in (image.get("Volumes") or {})}
    cfg["ExposedPorts"] = {k: v for k, v in (cfg.get("ExposedPorts") or {}).items()
                           if k not in (image.get("ExposedPorts") or {})}
    for port in c["HostConfig"].get("PortBindings") or {}:
        cfg["ExposedPorts"][port] = {}
    if cfg.get("Hostname") == c["Id"][:12]:
        cfg.pop("Hostname", None)  # Docker's default, the container's id: the new one gets its own
    body = {**cfg, "HostConfig": c["HostConfig"]}
    mode = c["HostConfig"].get("NetworkMode") or ""
    networks = (c.get("NetworkSettings") or {}).get("Networks") or {}
    if mode in ("none", "host") or mode.startswith("container:") or not networks:
        return body, {}
    endpoints = {name: _endpoint(n, c["Id"]) for name, n in networks.items()}
    first = mode if mode in endpoints else next(iter(endpoints))
    body["NetworkingConfig"] = {"EndpointsConfig": {first: endpoints.pop(first)}}
    return body, endpoints


def _endpoint(network: dict, container_id: str) -> dict:
    aliases = [a for a in network.get("Aliases") or [] if a != container_id[:12]]
    return {"Aliases": aliases or None, "IPAMConfig": network.get("IPAMConfig"), "Links": network.get("Links"),
            "DriverOpts": network.get("DriverOpts")}


def _keeps_running(docker: Docker, container_id: str, name: str) -> None:
    for _ in range(STARTING // 2):
        time.sleep(2)
        state = docker.call("GET", f"/containers/{container_id}/json")["State"]
        if not state.get("Running") or state.get("Restarting"):
            raise Failed(f"The new version of {name} stopped straight away (exit code {state.get('ExitCode')}), so "
                         "the one before was put back. `docker compose logs` says why.")


def _tidy(docker: Docker, me: dict) -> None:
    """After an update stopped half way, or the updater's own: an old container whose new one runs is removed; one
    with no new one is put back and started."""
    project = me["Config"]["Labels"].get(PROJECT)
    filters = {"label": [LABEL] + ([f"{PROJECT}={project}"] if project else [])}
    found = docker.call("GET", "/containers/json", all="true", filters=json.dumps(filters)) or []
    names = {c["Names"][0].lstrip("/"): c for c in found}
    for name, c in names.items():
        if not _old(name):
            continue
        current = names.get(name[:-len(OLD)])
        if current is not None and current["State"] == "running":
            docker.call("DELETE", f"/containers/{c['Id']}", force="true")
        elif current is None:
            docker.call("POST", f"/containers/{c['Id']}/rename", name=name[:-len(OLD)])
            docker.call("POST", f"/containers/{c['Id']}/start")


def _old(name: str) -> bool:
    return name.lstrip("/").endswith(OLD)


def _name(c: dict) -> str:
    return c["Name"].lstrip("/")


def _service(c: dict) -> str:
    return c["Config"]["Labels"].get(SERVICE) or _name(c)


def _prepare(folder: Path) -> None:
    """The folder the web app writes its request in: it may have been made by Docker (as root) for the mount."""
    folder.mkdir(parents=True, exist_ok=True)
    uid, gid = (int(os.environ.get(k) or 1000) for k in ("PUID", "PGID"))  # compose.yaml's default
    try:
        os.chown(folder, uid, gid)  # Linux, where the updater runs
        os.chmod(folder, 0o775)
    except (OSError, AttributeError):
        pass


def _read_status(folder: Path) -> dict:
    """Its own last status (the file only it writes), to go on showing how the last update went."""
    try:
        state = json.loads((folder / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = None
    return state if isinstance(state, dict) and state.get("state") else {"state": "idle"}


def _write_status(folder: Path, status: dict) -> None:
    """status.json, replaced in one step, with when the updater was last there (seen) and when this happened (at)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = {**status, "seen": now, "at": status.get("at") or now}
    status["at"] = out["at"]
    tmp = folder / "status.json.part"
    tmp.write_text(json.dumps(out), encoding="utf-8")
    try:
        os.chmod(tmp, 0o644)
    except OSError:
        pass
    os.replace(tmp, folder / "status.json")
