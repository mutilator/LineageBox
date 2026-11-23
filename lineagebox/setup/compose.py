"""The setup wizard's compose file: the answers given on its page (static/setup.js), checked, and made into one
compose.yaml that runs LineageBox as the repository's compose.yaml does, with every value written into it. One file
and no .env, because a NAS's own Docker app (Synology's Container Manager, Portainer, TrueNAS's Install via YAML) takes
a compose file pasted as it is, and most people never open a shell on their NAS.

read() turns the answers into a Plan and says what is wrong with them; render() writes the file; steps() says what to
do with it: the folders to make, how to start it, where to open it.

A network share's password never reaches the server while the page asks for the file. The file then has a stand-in
for it (secret()), which the page fills in itself, escaped as quote() does here (setup.js, quoteInner). Only Save
on this machine (server.py) sends the passwords, to write the file whole.
"""
import os
import posixpath
import re
from dataclasses import dataclass, field
from datetime import date

from lineagebox import __version__

OLLAMA_VERSION = "0.40.11"   # compose.ollama.yaml's (tests/test_setup.py keeps them the same)
WHISPER_MODELS = {   # docs/whisper.md: name → what the page says of it
    "ggml-large-v3-turbo-q5_0.bin": "the most accurate (550 MB); a few times faster than the video on a desktop "
                                    "processor, slower than it on a small NAS",
    "ggml-small.bin": "faster, more mistakes (470 MB)",
    "ggml-base.bin": "faster still, fine for clear speech (140 MB)",
    "ggml-small.en.bin": "English only, a little more accurate in English",
    "ggml-base.en.bin": "English only, the fastest",
}
SMB_VERSIONS = ("3.1.1", "3.0", "2.1", "2.0", "1.0")
SUPPORTED = ("amd64", "arm64")   # the platforms the image is published for
CATALOG_MB_PER_PHOTO = 0.5       # docs/install.md: database, thumbnails, previews
SOURCE_NAME = re.compile(r"[^\W_][\w '.\-]{0,39}")   # a folder's name: letters of any language, no / , = or quotes
TIME_ZONE = re.compile(r"UTC|[A-Za-z][A-Za-z0-9_+\-]*(/[A-Za-z0-9_+\-]+){1,2}")
LANGUAGE = re.compile(r"auto|[a-z]{2,3}")
VERSION = re.compile(r"\d+\.\d+(\.\d+)?([-.][A-Za-z0-9.]+)?")
SERVER = re.compile(r"[A-Za-z0-9_.\-]+|\[[0-9A-Fa-f:.%]+\]")   # a name, an IPv4 address or [an IPv6 one]
IPV4 = re.compile(r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})")
URL = re.compile(r"https?://[^\s/?#]+(:\d+)?(/[^\s]*)?")
NETWORK_FS = ("cifs", "smb3", "smbfs", "nfs", "nfs4", "fuse.sshfs", "9p", "davfs", "fuse.rclone")


def repository():
    """Where this LineageBox is published (owner/name on GitHub; a fork's CI sets LINEAGEBOX_UPDATES_REPOSITORY, as
    for updates)."""
    return os.environ.get("LINEAGEBOX_UPDATES_REPOSITORY") or "mutilator/LineageBox"


def image():
    """The image to run: this one's own repository's."""
    return "ghcr.io/" + repository().lower()


def quote_inner(text):
    """text inside a YAML double-quoted string that compose takes as it is: \\ and " escaped, characters YAML won't
    have raw written as \\u, and $ doubled (compose would take ${...} for one of its variables). setup.js's quoteInner
    does the same for passwords: change both together."""
    out = []
    for c in text:
        n = ord(c)
        if c == "\\":
            out.append("\\\\")
        elif c == '"':
            out.append('\\"')
        elif c == "$":
            out.append("$$")
        elif n < 0x20 or 0x7F <= n <= 0x9F or n in (0x2028, 0x2029, 0xFEFF):
            out.append(f"\\u{n:04x}")
        else:
            out.append(c)
    return "".join(out)


def quote(text):
    return f'"{quote_inner(str(text))}"'


def secret(index):
    """The stand-in for source index's password in a file the page fills in (setup.js, fillSecrets)."""
    return f"@@password:{index}@@"


@dataclass
class Problem:
    level: str   # "error": the file can't be made until it is put right; "warning": it can, but read this first
    field: str   # the answer it is about ("sources.0.path"), "" for the whole
    text: str


@dataclass
class Source:
    kind: str = "folder"   # "folder" of this machine, or "smb": a network share Docker mounts itself
    name: str = "local"    # its folder in /photos, which starts every path of its photos in the catalog
    path: str = ""         # folder: where it is on this machine (relative ones from compose.yaml's folder)
    writable: bool = True  # Cleanup's Removed folder, uploads and 720p copies; read-only for the worker either way
    new: bool = False      # a folder still to be made: a shared folder on the NAS, or one next to compose.yaml
    size_gb: int = 0       # a new one's size limit on the NAS (its quota), when the NAS has them
    server: str = ""       # smb: the server's name or address
    share: str = ""        # smb: the share, with folders inside it if wanted ("photos/family")
    username: str = ""     # smb: empty for a guest share
    version: str = "3.0"   # smb: the protocol version (2.1 or 1.0 for old NASes)
    has_password: bool = False
    anchor: str = ""       # the YAML anchor its mount is written under
    volume: str = ""       # smb: its Docker volume's name

    @property
    def target(self):
        return f"/photos/{self.name}"

    @property
    def device(self):
        return f"//{self.server}/{self.share}"


@dataclass
class Plan:
    system: dict | None = None   # the machine's entry in nas.toml
    model: dict | None = None    # and its model's, when said
    host_name: str = ""          # the machine's name on the network, for network paths and its address
    install_dir: str = ""        # compose.yaml's folder on this machine
    data: str = "./data"         # the catalog folder
    photos_count: int = 0
    puid: int = 1000
    pgid: int = 1000
    root: bool = False           # root with CHOWN, DAC_OVERRIDE and FOWNER, for photos that belong to several users
    sources: list = field(default_factory=list)
    ollama: str = "none"         # none, docker (next to LineageBox), host (this machine, outside Docker), remote
    ollama_gpu: str = "cpu"      # docker: cpu, nvidia or amd
    ollama_url: str = ""
    whisper: str = "none"        # none, docker, remote
    whisper_model: str = "ggml-large-v3-turbo-q5_0.bin"
    whisper_url: str = ""
    language: str = "auto"
    about: str = ""
    tz: str = "UTC"
    port: int = 8000
    bind: str = ""
    host_network: bool = False   # routers and machines whose bridge network can't reach DNS (docs/install.md)
    host_address: str = ""
    updater: bool = True
    socket: str = "/var/run/docker.sock"
    version: str = ""            # "" for the newest release
    network_paths: dict = field(default_factory=dict)
    # the wizard runs from the compose.yaml in install_dir that this one replaces (LineageBox's image started it):
    # "save" when the page saves it there, "replace" when people put the file there; "" otherwise
    in_place: str = ""

    problems: list = field(default_factory=list)

    @property
    def errors(self):
        return [p for p in self.problems if p.level == "error"]

    @property
    def windows(self):
        return bool(self.system and self.system.get("windows"))

    def full(self, path):
        """path as an absolute one, when compose.yaml's folder is known: for comparing folders."""
        if is_relative(path) and is_absolute(self.install_dir):
            return posixpath.normpath(posixpath.join(self.install_dir, path))
        return path

    def folder_sources(self):
        return [s for s in self.sources if s.kind == "folder"]

    def smb_sources(self):
        return [s for s in self.sources if s.kind == "smb"]

    def service_url(self, name, port):
        """How LineageBox's containers reach a service next to them in Docker: by its name on compose's network, or
        on this machine's own address with host networking, where the service is published on 127.0.0.1 alone."""
        return f"http://127.0.0.1:{port}" if self.host_network else f"http://{name}:{port}"

    @property
    def this_machine(self):
        """This machine's own address as the containers see it."""
        return "127.0.0.1" if self.host_network else "host.docker.internal"


def folder(text):
    """A folder of the machine as typed: trimmed, Windows' \\ turned into /, no / at the end, and a whole path's
    . and .. worked out (/a/b/../c is /a/c), so that one folder inside another is told by its path alone."""
    p = str(text or "").strip()[:1000]
    if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'":   # as Windows' Copy as path puts it
        p = p[1:-1].strip()
    if re.match(r"^[A-Za-z]:\\", p):
        p = p.replace("\\", "/")
    if p.startswith("/") or re.match(r"^[A-Za-z]:/", p):
        p = posixpath.normpath(p)
    if len(p) > 1:
        p = p.rstrip("/") or "/"
    if re.fullmatch(r"[A-Za-z]:", p):
        p += "/"
    return p


def is_relative(p):
    return p in (".", "..") or p.startswith(("./", "../"))


def is_absolute(p):
    return p.startswith(("/", "~/")) or bool(re.match(r"^[A-Za-z]:/", p))


def inside(child, parent):
    """Whether folder child is parent or one of its folders (Windows' drive paths without regard to case)."""
    if re.match(r"^[A-Za-z]:/", parent):
        child, parent = child.lower(), parent.lower()
    return child == parent or child.startswith(parent.rstrip("/") + "/")


def smb_parts(server, share):
    """The server and share from what people paste: \\\\nas\\photos, //nas/photos, smb://nas/photos, or the two
    fields as they are."""
    s = str(server or "").strip()
    sh = str(share or "").strip().replace("\\", "/").strip("/")
    m = re.fullmatch(r"(?:smb:|cifs:)?[\\/]{2}([^\\/]+)(?:[\\/](.*))?", s, re.IGNORECASE)
    if m:
        s = m.group(1)
        if m.group(2) and not sh:
            sh = m.group(2).replace("\\", "/").strip("/")
    return s, sh


def anchor_name(text, taken):
    """A YAML anchor or Docker volume name made from a source's name: letters, digits, - and _ only, and unique."""
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-").lower() or "photos"
    if not base[0].isalnum():
        base = "p" + base
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    taken.add(name)
    return name


def _int(value, default, low=0, high=2**31 - 1):
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None if value not in (None, "") else default
    return n if low <= n <= high else None


def _text(value, limit=500):
    """A line of text: control characters made spaces, trimmed to limit."""
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value or "")).strip()[:limit]


def read(answers, systems):
    """The answers (a dict, as the page sends them) as a Plan, with what is wrong in plan.problems. systems: nas.toml's
    entries by id (profiles.load)."""
    a = answers if isinstance(answers, dict) else {}
    p = Plan()
    problem = lambda level, where, text: p.problems.append(Problem(level, where, text))  # noqa: E731

    p.system = systems.get(str(a.get("system") or ""))
    if p.system and a.get("model"):
        p.model = next((m for m in p.system.get("models", []) if m["name"] == a.get("model")), None)
    if p.model:
        arch = p.model.get("arch", "")
        if arch and arch not in SUPPORTED:
            problem("error", "model", f"The {p.model['name']} has a 32-bit processor ({p.model.get('cpu') or arch}). "
                                      "LineageBox needs a 64-bit one (x86-64 or 64-bit ARM), so it can't run on it. "
                                      "Run it on another machine and give it these photos as a network share.")
        elif p.model.get("docker") is False:
            app = p.system.get("docker_app") or "Docker"
            problem("warning", "model", f"{p.system['name'].split(' (')[0]} doesn't offer {app} for the "
                                        f"{p.model['name']}. Without Docker on it, run LineageBox on another machine.")
        mem = p.model.get("memory_gb")
        if mem and mem < 2:
            problem("warning", "model", f"The {p.model['name']} comes with {mem:g} GB of memory. Recognizing faces "
                                        "needs more: add memory if it takes more, or run LineageBox elsewhere.")

    p.host_name = _text(a.get("host_name"), 100)
    if p.host_name and not SERVER.fullmatch(p.host_name):
        problem("error", "host_name", "The machine's name can only have letters, digits, dots and dashes.")
    p.install_dir = folder(a.get("install_dir"))
    if p.install_dir and not is_absolute(p.install_dir):
        problem("error", "install_dir", "Give the whole path of the folder, starting with / (for example "
                                        f"{(p.system or {}).get('install_dir') or '/srv/lineagebox'}).")

    # who LineageBox runs as
    p.root = bool(a.get("root"))
    puid, pgid = _int(a.get("puid"), 1000), _int(a.get("pgid"), 1000)
    if puid is None:
        problem("error", "puid", "The user id is a whole number, such as 1000.")
    if pgid is None:
        problem("error", "pgid", "The group id is a whole number, such as 100 or 1000.")
    p.puid, p.pgid = (0, 0) if p.root else (puid if puid is not None else 1000, pgid if pgid is not None else 1000)
    if puid == 0 and not p.root and not (p.system or {}).get("rootless"):
        problem("warning", "puid", "User 0 is root. That is right for rootless Docker or Podman, where the containers' "
                                   "root is you. Anywhere else, give the photos' owner, or choose \"Run as root\" "
                                   "below, which gives root the powers it needs.")

    # the catalog
    p.data = folder(a.get("data")) or "./data"
    p.photos_count = _int(a.get("photos_count"), 0, 0, 10**8) or 0
    if not (is_absolute(p.data) or is_relative(p.data)):
        problem("error", "data", "Give the whole path of the catalog folder, starting with /, or ./data for a folder "
                                 "next to compose.yaml.")

    # the photos
    raw = a.get("sources") if isinstance(a.get("sources"), list) else []
    names, anchors = {}, set()
    for i, r in enumerate(raw[:12]):
        r = r if isinstance(r, dict) else {}
        s = Source(kind="smb" if r.get("kind") == "smb" else "folder")
        where = f"sources.{i}"
        s.name = _text(r.get("name"), 60) or ("nas" if s.kind == "smb" else "local")
        if not SOURCE_NAME.fullmatch(s.name) or s.name.lower().endswith("-removed"):
            problem("error", f"{where}.name", "A source's name is up to 40 letters, digits, spaces, apostrophes, "
                                              "dots, dashes or underscores, starting with a letter or digit (and not "
                                              "ending in -Removed).")
        elif s.name.lower() in names:
            problem("error", f"{where}.name", f"Two sources are both named \"{s.name}\": give each its own name.")
        names[s.name.lower()] = i
        s.writable = r.get("writable", True) is not False
        s.anchor = anchor_name(s.name, anchors)
        if s.kind == "folder":
            s.path = folder(r.get("path"))
            s.new = bool(r.get("new"))
            s.size_gb = _int(r.get("size_gb"), 0, 0, 10**6) or 0
            if not s.path:
                problem("error", f"{where}.path", "Say where this folder is." if not s.new else
                        "Say where the new folder will be: its path once it is made.")
            elif re.search(r"[\x00-\x1f]", s.path):
                problem("error", f"{where}.path", "The path has a character that can't be in a folder's name.")
            elif not (is_absolute(s.path) or is_relative(s.path)):
                problem("error", f"{where}.path", "Give the whole path, starting with / (for example "
                        f"{(p.system or {}).get('photos_example') or '/srv/photos'}), or ./photos for a folder next "
                        "to compose.yaml.")
        else:
            s.server, s.share = smb_parts(r.get("server"), r.get("share"))
            s.username = _text(r.get("username"), 100)
            s.version = str(r.get("version") or "3.0")
            s.has_password = bool(r.get("has_password"))
            s.volume = anchor_name("smb-" + s.name, anchors)
            if not s.server:
                problem("error", f"{where}.server", "Give the server's name or address, such as 192.168.1.20.")
            elif not SERVER.fullmatch(s.server):
                problem("error", f"{where}.server", "A server's name can only have letters, digits, dots and dashes "
                                                    "(or give its address, such as 192.168.1.20).")
            if not s.share:
                problem("error", f"{where}.share", "Give the share's name, such as photos.")
            elif re.search(r'[\x00-\x1f",]', s.share):
                problem("error", f"{where}.share", "The share's name has a character Docker can't pass on "
                                                   "(a comma or a quote).")
            if "," in s.username:
                problem("error", f"{where}.username", "Docker can't pass a user name with a comma to the share.")
            if s.version not in SMB_VERSIONS:
                problem("error", f"{where}.version", "Pick one of the versions offered.")
        p.sources.append(s)
    if not p.sources:
        problem("error", "sources", "Say where the photos are: a folder of this machine or a network share.")

    # folders that would get in each other's way
    data = p.full(p.data)
    for i, s in enumerate(p.sources):
        if s.kind != "folder" or not s.path:
            continue
        here = p.full(s.path)
        if inside(data, here):
            problem("error", "data", f"The catalog folder is inside the photos folder \"{s.name}\". LineageBox would "
                                     "read its own thumbnails as photos: put the catalog somewhere else, such as "
                                     "next to compose.yaml.")
        elif inside(here, data):
            problem("error", f"sources.{i}.path", "This photos folder is inside the catalog folder: put them apart.")
        for j, t in enumerate(p.sources[:i]):
            if t.kind == "folder" and t.path and (inside(here, p.full(t.path)) or inside(p.full(t.path), here)):
                problem("error", f"sources.{i}.path", f"This folder and \"{t.name}\" are one inside the other: one "
                                                      "source that holds both is enough.")

    # Ollama and Whisper
    o = a.get("ollama") if isinstance(a.get("ollama"), dict) else {}
    p.ollama = o.get("where") if o.get("where") in ("none", "docker", "host", "remote") else "none"
    p.ollama_gpu = o.get("gpu") if o.get("gpu") in ("cpu", "nvidia", "amd") else "cpu"
    if p.ollama == "remote":
        p.ollama_url = service(o.get("url"), 11434)
        if not URL.fullmatch(p.ollama_url):
            problem("error", "ollama.url", "Give the address of the machine that runs Ollama, such as "
                                           "192.168.1.10 or http://192.168.1.10:11434.")
    if p.ollama == "docker" and p.model:
        if p.ollama_gpu == "amd" and p.model.get("arch") == "arm64":
            problem("error", "ollama.gpu", "Ollama runs on AMD graphics cards on x86-64 machines only.")
        mem = p.model.get("memory_gb") or 0
        if p.ollama_gpu == "cpu" and mem and mem < 8:
            problem("warning", "ollama.where", f"The {p.model['name']} comes with {mem:g} GB of memory. A vision "
                                               "model needs about 5 GB free on top of LineageBox: on less, Ollama is "
                                               "stopped as it loads. Ollama on another machine, or more memory, "
                                               "works better.")
    w = a.get("whisper") if isinstance(a.get("whisper"), dict) else {}
    p.whisper = w.get("where") if w.get("where") in ("none", "docker", "remote") else "none"
    p.whisper_model = w.get("model") if w.get("model") in WHISPER_MODELS else p.whisper_model
    p.language = str(w.get("language") or "auto").strip().lower()
    if not LANGUAGE.fullmatch(p.language):
        problem("error", "whisper.language", "The language is a code such as en or de, or auto.")
    if p.whisper == "remote":
        p.whisper_url = service(w.get("url"), 9300)
        if not URL.fullmatch(p.whisper_url):
            problem("error", "whisper.url", "Give the address of the machine that runs Whisper, such as "
                                            "192.168.1.10 or http://192.168.1.10:9300.")
    p.about = _text(a.get("about"), 200)

    # the rest
    p.tz = str(a.get("tz") or "UTC").strip()
    if not TIME_ZONE.fullmatch(p.tz):
        problem("error", "tz", "The time zone is a name such as America/Denver or Europe/Berlin.")
    port = _int(a.get("port"), 8000, 1, 65535)
    if port is None:
        problem("error", "port", "The port is a number from 1 to 65535, such as 8000.")
    p.port = port or 8000
    p.host_network = bool(a.get("host_network"))
    p.bind = str(a.get("bind") or "").strip()
    if p.bind and not ipv4(p.bind):
        problem("error", "bind", "Give one of this machine's IPv4 addresses, such as 192.168.1.5, or leave it empty.")
    if p.host_network:
        p.host_address = str(a.get("host_address") or "").strip()
        if not ipv4(p.host_address):
            problem("error", "host_address", "With host networking, give the address LineageBox answers on: this "
                                             "machine's address on the home network, such as 192.168.1.1.")
    p.updater = a.get("updater", True) is not False
    p.socket = folder(a.get("socket")) or (p.system or {}).get("docker_socket") or "/var/run/docker.sock"
    if p.updater and not p.socket.startswith("/"):
        problem("error", "socket", "Docker's socket is a path such as /var/run/docker.sock.")
    p.version = str(a.get("version") or "").strip().removeprefix("v")
    if p.version and not VERSION.fullmatch(p.version):
        problem("error", "version", "A version is written like 1.10.3, or left empty for the newest.")
    p.network_paths = network_paths(p, a.get("network_paths"))
    return p


def ipv4(text):
    m = IPV4.fullmatch(text or "")
    return bool(m) and all(int(g) <= 255 for g in m.groups())


def service(text, port):
    """A service's address as typed ("192.168.1.10", "pc:11434", "http://pc:11434/") as its URL, with its usual
    port if none is given."""
    t = str(text or "").strip().rstrip("/")
    if not t:
        return ""
    if not re.match(r"^https?://", t, re.IGNORECASE):
        t = "http://" + t
    m = re.match(r"^(https?://)([^/]+)(.*)$", t, re.IGNORECASE)
    if m and not re.search(r":\d+$", m.group(2)) and not m.group(2).endswith("]"):
        t = f"{m.group(1)}{m.group(2)}:{port}{m.group(3)}"
    return t


def network_paths(p, given):
    """Where people's PCs find each source, for Copy network path: as given on the page, or worked out: a share's own
    \\\\server\\share, and a folder of a NAS's shared folder as \\\\name\\share\\..., when the machine's name is said."""
    out = {}
    given = given if isinstance(given, dict) else {}
    template = (p.system or {}).get("network_path") or ""
    parent = (p.system or {}).get("share_parent") or ""
    for s in p.sources:
        typed = _text(given.get(s.name), 300)
        if typed:
            out[s.name] = typed
        elif s.kind == "smb" and s.server and s.share:
            out[s.name] = "\\\\" + s.server + "\\" + s.share.replace("/", "\\")
        elif s.kind == "folder" and p.host_name and template and parent and inside(s.path, parent):
            rest = s.path[len(parent.rstrip("/")):].strip("/")
            if rest:
                share, _, inner = rest.partition("/")
                path = template.replace("{host}", p.host_name).replace("{share}", share)
                out[s.name] = path + ("\\" + inner.replace("/", "\\") if inner else "")
    return {k: v for k, v in out.items() if "," not in v and "=" not in k}


def render(p, secrets=None):
    """The compose file for plan p. secrets: each smb source's password by its index in p.sources; None for the
    stand-ins the page fills in (secret())."""
    out = []
    add = out.append
    when = date.today().isoformat()
    machine = " ".join(x for x in ((p.system or {}).get("name", ""), (p.model or {}).get("name", "")) if x)
    add(f"# LineageBox, from its setup wizard (LineageBox {__version__}, {when})."
        + (f" The machine: {machine}." if machine else ""))
    add("# It runs the web UI, a worker that keeps the catalog up to date, a service that makes 720p copies of the")
    add("# videos browsers can't play" + (", and one that installs newer versions when an admin asks." if p.updater
                                          else "."))
    needed = folders_to_make(p)
    if needed:
        add("#")
        add(f"# Before the first start, these folders must be there, writable by user {p.puid} (group {p.pgid}):")
        for f in needed:
            add(f"#   {f}")
    add("#")
    add("# Start it with your NAS's Docker app (the wizard's page has the steps), or in the folder of this file:")
    add(f"#   {'sudo ' if (p.system or {}).get('sudo') else ''}docker compose up -d")
    add(f"# then open http://{p.host_name or '<this machine>'}:{p.port}. docs/install.md explains every part:")
    add("#   https://github.com/mutilator/LineageBox/blob/main/docs/install.md")
    add("name: lineagebox")
    add("")
    tag = p.version or "latest"
    add("x-lineagebox: &lineagebox")
    if p.version:
        add(f"  # LineageBox {p.version} and no other: Update now only installs it again. \"latest\" is the newest.")
    else:
        add("  # \"latest\" is always the newest release, which the Status page's Update now installs")
    add(f"  image: {image()}:{tag}")
    add("  labels:")
    add('    lineagebox.update: "true"   # the containers the updater service may update')
    add("  restart: unless-stopped")
    if p.root:
        add("  # root, with only the powers writing other users' files needs: the photos belong to several users.")
        add("  # What it writes next to a photo (720p copies, Removed folders) is given to the photo's owner.")
        add('  user: "0:0"')
    else:
        add("  # who LineageBox runs as: the owner of the photos and of the catalog folder. It has no root powers, and")
        add("  # no setuid program can give it any.")
        add(f'  user: "{p.puid}:{p.pgid}"')
    add("  security_opt:")
    add("    - no-new-privileges:true")
    add("  cap_drop: [ALL]")
    if p.root:
        add("  cap_add: [CHOWN, DAC_OVERRIDE, FOWNER]")
    if p.host_network:
        add("  network_mode: host   # this machine's own network: its bridge network can't reach the DNS server")
    else:
        add("  extra_hosts:")
        add('    - "host.docker.internal:host-gateway"   # Ollama or Whisper on this same machine')
    add("  environment:")
    add("    LINEAGEBOX_SETUP: done   # made by the setup wizard: the image starts LineageBox, not the wizard")
    add(f"    TZ: {quote(p.tz)}")
    if p.ollama == "none":
        add('    LINEAGEBOX_OLLAMA_ENABLED: "false"   # no AI descriptions yet: Settings → AI turns them on')
    else:
        url = {"docker": p.service_url("ollama", 11434), "host": f"http://{p.this_machine}:11434",
               "remote": p.ollama_url}[p.ollama]
        add(f"    LINEAGEBOX_OLLAMA_URL: {quote(url)}")
    if p.whisper != "none":
        url = p.service_url("whisper", 9300) if p.whisper == "docker" else p.whisper_url
        add(f"    LINEAGEBOX_WHISPER_URL: {quote(url)}")
        if p.language != "auto":
            add(f"    LINEAGEBOX_WHISPER_LANGUAGE: {quote(p.language)}")
    if p.about:
        add(f"    LINEAGEBOX_LIBRARY_ABOUT: {quote(p.about)}   # whose photos: helps the AI read folder names")
    if p.network_paths:
        joined = ",".join(f"{k}={v}" for k, v in p.network_paths.items())
        add(f"    LINEAGEBOX_WEB_NETWORK_PATHS: {quote(joined)}   # for Copy network path")
    add("")
    add("# The catalog (database, thumbnails, previews): on this machine's own disk, never a network share. It must be")
    add("# there already: Docker would make a missing folder itself, as root, which LineageBox couldn't write.")
    add(f"x-catalog: &catalog {{type: bind, source: {quote(p.data)}, target: /data, bind: {{create_host_path: false}}}}")
    add("# The photos. Each source is a folder of /photos, whose name starts every path in the catalog: keep it.")
    for s in p.sources:
        if s.kind == "folder":
            add(f"x-{s.anchor}: &{s.anchor} {{type: bind, source: {quote(s.path)}, target: {quote(s.target)}, "
                "bind: {create_host_path: false}}")
        else:
            add(f"x-{s.anchor}: &{s.anchor} {{type: volume, source: {s.volume}, target: {quote(s.target)}}}"
                f"   # the share {s.device}")
    add("")
    add("services:")
    add("  web:")
    add("    <<: *lineagebox")
    if p.host_network:
        add(f'    command: ["serve", "--host", {quote(p.host_address)}, "--port", "{p.port}"]')
    else:
        add('    command: ["serve", "--host", "0.0.0.0", "--port", "8000"]')
        add("    ports:")
        add("      # IPv4 only: Docker relays IPv6 connections from its own gateway address, which would look like the")
        add("      # home network to LineageBox.")
        add(f'      - "{p.bind or "0.0.0.0"}:{p.port}:8000"')
    _volumes(p, add, "web", "Cleanup's Removed folder and uploads are written here")
    add("  worker:")
    add("    <<: *lineagebox")
    add('    command: ["worker"]')
    _volumes(p, add, "worker", "")
    add("  copies:")
    add("    <<: *lineagebox")
    add('    command: ["copies"]')
    _volumes(p, add, "copies", "720p copies go next to their videos, XMP sidecars (when on) next to photos")
    if p.updater:
        _updater(p, add, tag)
    if p.ollama == "docker":
        _ollama(p, add)
    if p.whisper == "docker":
        _whisper(p, add, tag)
    named = p.smb_sources()
    if named or p.ollama == "docker" or p.whisper == "docker":
        add("")
        add("volumes:")
    for s in named:
        index = p.sources.index(s)
        add(f"  {s.volume}:")
        add(f"    # {s.device}, mounted by Docker. Its options are kept from the first start: after changing them, run")
        add(f"    #   docker compose down && docker volume rm lineagebox_{s.volume}")
        add("    # which only unmounts it; nothing on the share is touched.")
        add("    driver: local")
        add("    driver_opts:")
        add("      type: cifs")
        add(f"      device: {quote(s.device)}")
        if s.username:
            if secrets is None:
                password = secret(index) if s.has_password else ""
            else:
                password = quote_inner(str(secrets.get(index) or secrets.get(str(index)) or ""))
            login = f"username={quote_inner(s.username)},password={password}"
        else:
            login = "guest"
        add(f'      o: "{login},vers={s.version},uid={p.puid},gid={p.pgid},file_mode=0664,dir_mode=0775"')
    if p.ollama == "docker":
        add("  ollama:   # Ollama's models, several GB each")
    if p.whisper == "docker":
        add("  whisper:   # Whisper's models")
    return "\n".join(out) + "\n"


def _volumes(p, add, service, why):
    """A service's mounts: each source, read-only for the worker and wherever LineageBox may not write; why is said
    beside the first writable one."""
    add("    volumes:")
    for s in p.sources:
        if service == "worker" or not s.writable:
            note = ("the worker only reads photos and videos" if service == "worker" else
                    "LineageBox never writes here: no Cleanup, uploads or 720p copies for it")
            add(f"      - {{<<: *{s.anchor}, read_only: true}}   # {note}")
        else:
            add(f"      - *{s.anchor}" + (f"   # {why}" if why else ""))
            why = ""
    add("      - *catalog")


def _updater(p, add, tag):
    add("  updater:")
    add("    # Installs a newer LineageBox when an admin presses Update now on the Status page. It is the one container")
    add("    # with Docker access, which is root on this machine, so: it has no network, reads nothing but whether the")
    add("    # web app made the empty file update/requested in the catalog folder, and only updates this project's")
    add("    # containers labeled lineagebox.update. docs/install.md#updating-and-backups says how to do without it.")
    add(f"    image: {image()}:{tag}")
    add('    command: ["updater"]')
    add("    restart: unless-stopped")
    add('    user: "0:0"                  # the Docker socket is root\'s')
    add("    network_mode: none")
    add("    read_only: true")
    add("    security_opt:")
    add("      - no-new-privileges:true")
    add("    cap_drop: [ALL]")
    add("    cap_add: [CHOWN, DAC_OVERRIDE, FOWNER]   # the update folder belongs to LineageBox's user")
    add("    healthcheck:")
    add("      disable: true              # the image's check reads the catalog, which the updater never opens")
    add("    depends_on: [web]            # after web: a missing catalog folder stops it there first")
    add("    environment:")
    add(f'      PUID: "{p.puid}"')
    add(f'      PGID: "{p.pgid}"')
    add("    labels:")
    add('      lineagebox.update: "true"   # it updates itself, last')
    add("    volumes:")
    add(f"      - {quote(p.socket + ':/var/run/docker.sock')}")
    add(f"      - {quote(posixpath.join(p.data, 'update') + ':/update')}")


def _ollama(p, add):
    add("  ollama:")
    add("    # Ollama, for photo descriptions and reading folder names. Download a model on LineageBox's Settings page.")
    suffix = "-rocm" if p.ollama_gpu == "amd" else ""
    add(f"    image: ollama/ollama:{OLLAMA_VERSION}{suffix}")
    add("    restart: unless-stopped")
    add("    environment:")
    add('      OLLAMA_NUM_PARALLEL: "1"   # photos at once; each one more needs more memory')
    add("    volumes:")
    add("      - ollama:/root/.ollama")
    if p.host_network:
        add("    ports:")
        add('      - "127.0.0.1:11434:11434"   # for LineageBox on this machine\'s network; no one else')
    if p.ollama_gpu == "nvidia":
        add("    # the NVIDIA graphics card: needs its driver and the NVIDIA Container Toolkit on this machine")
        add("    deploy:")
        add("      resources:")
        add("        reservations:")
        add("          devices:")
        add("            - driver: nvidia")
        add("              count: all")
        add("              capabilities: [gpu]")
    elif p.ollama_gpu == "amd":
        add("    devices:   # the AMD graphics card (ROCm)")
        add("      - /dev/kfd")
        add("      - /dev/dri")


def _whisper(p, add, tag):
    add("  whisper:")
    add("    # Speech to text for video transcripts, on this machine's processor. It downloads its model at first start.")
    add(f"    image: {image()}-whisper:{tag}")
    add("    labels:")
    add('      lineagebox.update: "true"   # updated with LineageBox')
    add("    restart: unless-stopped")
    add("    security_opt:")
    add("      - no-new-privileges:true")
    add("    cap_drop: [ALL]")
    add(f'    command: ["--model", "{p.whisper_model}"]')
    add("    volumes:")
    add("      - whisper:/data")
    if p.host_network:
        add("    ports:")
        add('      - "127.0.0.1:9300:9300"')


def folders_to_make(p):
    """The folders that must be there before the first start: the catalog folder, and new photo folders."""
    out = [p.data]
    out += [s.path for s in p.folder_sources() if s.new and s.path]
    return out


def steps(p):
    """What to do with the file, in order, as the page shows it: [{title, lines, code}]."""
    sysinfo = p.system or {}
    sudo = "sudo " if sysinfo.get("sudo") else ""
    user = f"{p.puid}:{p.pgid}"
    out = []
    by_nas = set()   # new shared folders the NAS makes itself: mkdir would make a plain folder, not a share
    for s in p.folder_sources():
        if (s.new and sysinfo.get("share_how") and sysinfo.get("kind", "nas") == "nas" and not is_relative(s.path)
                and not (p.install_dir and inside(s.path, p.install_dir))):
            by_nas.add(s.path)
            name = posixpath.basename(s.path) or "photos"
            # a step about the size limit only when one was given
            how = [t for t in sysinfo["share_how"] if s.size_gb or "{size}" not in t]
            lines = [fill(t, size=s.size_gb, name=name, user=user) for t in how]
            lines += [fill(t, user=user, name=name) for t in sysinfo.get("permissions_how", [])]
            out.append({"title": f"Make the shared folder for the photos ({s.path})", "lines": lines, "code": ""})
    shown = [f if not is_relative(f) or not p.install_dir else posixpath.normpath(posixpath.join(p.install_dir, f))
             for f in folders_to_make(p) if f not in by_nas]
    if shown and not p.windows:
        code = "\n".join(f'{sudo}mkdir -p "{f}" && {sudo}chown {user} "{f}"' for f in shown)
        one = len(shown) == 1
        lines = [f"LineageBox needs {'this folder' if one else 'these folders'} to be there, and to be able to write "
                 f"{'it' if one else 'them'}. Docker would make a missing one itself, as root, which LineageBox "
                 "couldn't write. Over SSH:"]
        if sysinfo.get("permissions_how"):
            lines.append(f"Or make {'it' if one else 'them'} in the NAS's file manager, and check that the user can "
                         "write there.")
        out.append({"title": "Make the catalog folder" if one else "Make the folders LineageBox needs", "lines": lines,
                    "code": code})
    where = p.install_dir or "the folder of compose.yaml"
    code = (f'cd "{p.install_dir}"\n' if p.install_dir else "") + f"{sudo}docker compose up -d"
    if p.in_place:   # the wizard's own compose.yaml started it: the new file takes its place, in the same project
        start = ["Save it above: it takes the place of the compose.yaml that started this wizard, which is kept beside "
                 "it." if p.in_place == "save" else
                 f"Put the file in {where} as compose.yaml, in place of the one that started this wizard."]
        start.append("Then start it the way you started the wizard. LineageBox takes the wizard's place, and the "
                     "wizard stops.")
        if sysinfo.get("docker_app"):
            start.append(f"In {sysinfo['docker_app']}: open the project the wizard runs in (lineagebox), put the new "
                         "file in place of its YAML if it shows the wizard's, and start it again (stop it first if it "
                         "asks).")
        start.append("Or over SSH, in that folder:")
    else:
        start = [fill(t, install_dir=where) for t in sysinfo.get("compose_how", [])]
        start.append("Or over SSH, in the folder of compose.yaml:" if start else "In the folder of compose.yaml:")
    out.append({"title": "Start LineageBox", "lines": start, "code": code})
    lines = [f"Open http://{p.host_name or '<this machine’s address>'}:{p.port} in a browser. It opens on Getting "
             "started: watch your photos' folder first (Settings → Watched folders), and the worker starts within a "
             "minute. Status shows how far it has got."]
    if p.ollama == "docker":
        lines.append("Then download a model to Ollama on Settings → AI (ministral-3 with a graphics card, gemma3:4b "
                     "on a processor alone).")
    if p.ollama == "host":
        lines.append("Ollama on this machine must answer more than itself: set OLLAMA_HOST=0.0.0.0 for it and restart "
                     "it (docs/ollama.md).")
    if p.whisper == "docker":
        lines.append("Whisper downloads its model the first time it starts: the Status page shows how far.")
    out.append({"title": "Open it", "lines": lines, "code": ""})
    return out


def fill(text, **values):
    """A nas.toml step with its {size}, {name}, {user} and {install_dir} filled in."""
    for k, v in values.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def catalog_gb(photos):
    return round(photos * CATALOG_MB_PER_PHOTO / 1024, 1)
