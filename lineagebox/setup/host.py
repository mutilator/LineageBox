"""What the setup container sees of the machine it runs on, when started with the machine's root mounted read-only at
/host (docs/install.md#the-setup-wizard): which NAS it is, its users and groups, its folders and who owns them,
whether a folder is a network share, free space, graphics cards and Docker's socket; and where /install, the folder it
was started from and the one place it may write, is on the machine.

Every path given or shown is the machine's own (/volume1/photo), never the container's (/host/volume1/photo).
Symbolic links are followed as the machine would, inside /host: QNAP's /share/Photos is a link to
/share/CACHEDEV1_DATA/Photos, which the container would otherwise look for in its own root.
"""
import glob
import os
import platform
import posixpath
import re
import shutil
import stat
from pathlib import Path

from lineagebox.setup.compose import NETWORK_FS

# Folder names never offered: systems' own, NASes' thumbnails and recycle bins
HIDDEN = {"lost+found", "@eaDir", "#recycle", "#snapshot", "@Recycle", ".@__thumb", "$RECYCLE.BIN",
          "System Volume Information"}
# and at the top, the operating system's own folders
SYSTEM = {"proc", "sys", "dev", "run", "tmp", "boot", "bin", "sbin", "lib", "lib32", "lib64", "libx32", "usr", "etc",
          "var", "snap", "initrd", "selinux", "efi", "install", "host", "etc.defaults", "var.defaults"}
ARCH = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}


def unescape(text):
    """A path from /proc/self/mountinfo, whose spaces, tabs, newlines and backslashes are written in octal."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), text)


def mounts(text):
    """/proc/self/mountinfo's lines as {dev, root, point, fstype}."""
    out = []
    for line in text.splitlines():
        left, sep, right = line.partition(" - ")
        f, r = left.split(" "), right.split(" ")
        if sep and len(f) >= 5 and r:
            out.append({"dev": f[2], "root": unescape(f[3]), "point": unescape(f[4]), "fstype": r[0]})
    return out


class Host:
    def __init__(self, root="/host", install="/install", mountinfo="/proc/self/mountinfo"):
        self.root = Path(root)
        self.install = Path(install)
        self.mountinfo = Path(mountinfo)

    @property
    def available(self):
        """Whether the machine's root is there to look at."""
        return (self.root / "etc").is_dir()

    @property
    def can_save(self):
        return self.install.is_dir() and os.access(self.install, os.W_OK)

    def set_up_by_hand(self):
        """Whether the folder it was started in looks like LineageBox set up by hand, from compose.manual.yaml (which
        was compose.yaml, now the wizard's): a .env, or a catalog in data/."""
        return (self.install / ".env").is_file() or (self.install / "data" / "lineagebox.db").is_file()

    def inside(self, path):
        """The container's path for the machine's path, its links followed within /host; None for a path that isn't
        whole or a loop of links."""
        if not isinstance(path, str) or not path.startswith("/") or "\x00" in path:
            return None
        todo = [p for p in path.split("/") if p not in ("", ".")]
        done, hops = [], 0
        while todo:
            part = todo.pop(0)
            if part == "..":
                if done:
                    done.pop()
                continue
            here = self.root.joinpath(*done, part)
            if here.is_symlink():
                hops += 1
                if hops > 40:
                    return None
                target = os.readlink(here)
                if target.startswith("/"):
                    done = []
                todo = [p for p in target.split("/") if p not in ("", ".")] + todo
                continue
            done.append(part)
        return self.root.joinpath(*done)

    def read(self, path, limit=200_000):
        """A file of the machine as text, or "" if it can't be read."""
        here = self.inside(path)
        try:
            with open(here, "rb") as f:
                return f.read(limit).decode("utf-8", "replace")
        except (OSError, TypeError):
            return ""

    def exists(self, path):
        here = self.inside(path)
        return bool(here) and os.path.lexists(here)

    # ---- who's who
    def groups(self):
        """gid → {name, members}, from the machine's /etc/group."""
        out = {}
        for line in self.read("/etc/group").splitlines():
            f = line.split(":")
            if len(f) >= 4 and f[2].isdigit():
                out.setdefault(int(f[2]), {"name": f[0], "members": [m for m in f[3].split(",") if m]})
        return out

    def users(self, also=()):
        """The machine's people (uid 1000 and up, and the ids in also, such as Unraid's 99 or TrueNAS's 568), from its
        /etc/passwd: [{name, uid, gid, group, groups}]. A NAS's own users are there too (Synology, QNAP, Unraid)."""
        groups = self.groups()
        out = []
        for line in self.read("/etc/passwd").splitlines():
            f = line.split(":")
            if len(f) < 4 or not (f[2].isdigit() and f[3].isdigit()):
                continue
            uid, gid = int(f[2]), int(f[3])
            if not (1000 <= uid < 65534 or uid in also):
                continue
            member = sorted(g for g, info in groups.items() if f[0] in info["members"] and g != gid)
            out.append({"name": f[0], "uid": uid, "gid": gid, "group": groups.get(gid, {}).get("name", ""),
                        "groups": [{"gid": g, "name": groups[g]["name"]} for g in member]})
        return sorted(out, key=lambda u: u["uid"])

    # ---- which machine
    def matches(self, entry):
        """A nas.toml detect entry: "/path" is there, or "/path: words" has the words in it."""
        path, _, words = entry.partition(":")
        path, words = path.strip(), words.strip()
        return (words.lower() in self.read(path).lower()) if words else self.exists(path)

    def model(self, model_from):
        """The model, from a "/path: key" (a key="value" line in that file)."""
        path, _, key = model_from.partition(":")
        for line in self.read(path.strip()).splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() == key.strip():
                return v.strip().strip('"').strip("'")
        return ""

    def detect(self, systems):
        """What can be told of the machine: the nas.toml entry and model, its name, processor, memory, graphics cards,
        Docker's socket, time zone, users, and compose.yaml's folder."""
        found = next((s for s in systems.values() if s.get("detect") and any(self.matches(d) for d in s["detect"])),
                     None)
        model = self.model(found["model_from"]) if found and found.get("model_from") else ""
        # this machine's own app users below 1000 too (Unraid's nobody, TrueNAS's apps); root is a choice of its own
        ours = (found or {}).get("users", [])
        also = {u["uid"] for u in ours} - {0}
        name = (self.read("/etc/hostname").strip().splitlines() or [""])[0].strip()
        return {
            "system": found["id"] if found else "",
            "model": model,
            "host_name": name if re.fullmatch(r"[A-Za-z0-9_.\-]{1,63}", name) else "",
            "arch": ARCH.get(platform.machine().lower(), platform.machine().lower()),
            "memory_gb": memory_gb(),
            "cpus": os.cpu_count() or 0,
            "gpu": {"nvidia": self.exists("/dev/nvidiactl") or self.exists("/dev/nvidia0"),
                    "amd": self.exists("/dev/kfd"), "intel_or_amd": self.exists("/dev/dri/renderD128")},
            "socket": self.socket(),
            "tz": self.time_zone(),
            "users": self.users(also),
            "groups": [{"gid": g, "name": info["name"]} for g, info in sorted(self.groups().items())
                       if 100 <= g < 65534 or g in {u["gid"] for u in ours}],
            "install_dir": self.install_dir() if self.install.is_dir() else None,
            "install_owner": self.owner(self.install),
            "can_save": self.can_save,
        }

    def owner(self, folder):
        """Who owns a folder the container sees (the one the setup was started in: whoever made it is likely whom
        LineageBox should run as), or None for root's or one that isn't there."""
        try:
            st = os.stat(folder)
        except OSError:
            return None
        if st.st_uid == 0:
            return None
        users = {u["uid"]: u["name"] for u in self.users(range(0, 1000))}
        return {"uid": st.st_uid, "gid": st.st_gid, "user": users.get(st.st_uid, ""),
                "group": self.groups().get(st.st_gid, {}).get("name", "")}

    def socket(self):
        for path in ("/var/run/docker.sock", "/run/docker.sock"):
            here = self.inside(path)
            if here and here.is_socket():
                return path
        for here in sorted(glob.glob(str(self.root / "run" / "user" / "*" / "docker.sock"))):
            return "/" + Path(here).relative_to(self.root).as_posix()   # rootless Docker
        return ""

    def time_zone(self):
        named = self.read("/etc/timezone").strip()
        if re.fullmatch(r"[A-Za-z_]+(/[A-Za-z0-9_+\-]+)+", named):
            return named
        here = self.root / "etc" / "localtime"
        if here.is_symlink():
            m = re.search(r"zoneinfo/(.+)$", os.readlink(here))
            if m:
                return m.group(1)
        return ""

    # ---- folders
    def fs_type(self, path):
        """The file system the machine's folder is on (cifs, nfs4, ext4, btrfs, ...), from the mounts the container
        got with /host."""
        prefix = self.root.as_posix().rstrip("/")
        here = (self.inside(path) or Path("")).as_posix()
        best, kind = "", ""
        for m in mounts(self._mountinfo()):
            point = m["point"]
            if (point == prefix or point.startswith(prefix + "/")) and (here == point or here.startswith(
                    point.rstrip("/") + "/")) and len(point) > len(best):
                best, kind = point, m["fstype"]
        return kind

    def is_network(self, path):
        return self.fs_type(path) in NETWORK_FS

    def names(self):
        """uid → user name and gid → group name, everyone's (for owners shown)."""
        return ({u["uid"]: u["name"] for u in self.users(range(0, 1000))},
                {g: info["name"] for g, info in self.groups().items()})

    def look(self, path, uid=None, gid=None, names=None):
        """A folder of the machine: there or not, its owner, whether user uid in group gid could write it (by its
        permissions; ACLs, as a Synology's shared folders have, may let more in), on a network share or not."""
        here = self.inside(path)
        out = {"path": path, "exists": False}
        try:
            st = os.stat(here)
        except (OSError, TypeError):
            return out
        users, groups = names or self.names()
        out.update(exists=stat.S_ISDIR(st.st_mode), uid=st.st_uid, gid=st.st_gid,
                   user=users.get(st.st_uid, ""), group=groups.get(st.st_gid, ""),
                   network=self.fs_type(path) if self.is_network(path) else "")
        if uid is not None and gid is not None:
            out["writable"] = writable(st, uid, gid)
        return out

    def browse(self, path, uid=None, gid=None):
        """The folders in a folder of the machine, each as look() has it, and the free space where it is."""
        path = posixpath.normpath(path or "/")
        here = self.inside(path)
        if not here or not here.is_dir():
            return {"path": path, "error": "There is no such folder on this machine."}
        try:
            names = sorted(os.listdir(here), key=str.lower)
        except OSError as e:
            return {"path": path, "error": f"This folder can't be read ({e.strerror})."}
        folders = []
        who = self.names()
        for name in names[:2000]:
            if name.startswith((".", "@", "#")) or name in HIDDEN or (path == "/" and name in SYSTEM):
                continue
            inner = posixpath.join(path, name)
            look = self.look(inner, uid, gid, who)
            if look.get("exists"):
                folders.append({**look, "name": name})
            if len(folders) >= 500:
                break
        out = {**self.look(path, uid, gid, who), "parent": posixpath.dirname(path) if path != "/" else None,
               "folders": folders}
        try:
            room = shutil.disk_usage(here)
            out.update(free_gb=round(room.free / 1e9, 1), total_gb=round(room.total / 1e9, 1))
        except OSError:
            pass
        return out

    def install_dir(self):
        """/install's path on the machine, found by matching its mount with one under /host and checking it is the
        same folder; None when it can't be told (Docker Desktop, say): the page asks then."""
        all_ = mounts(self._mountinfo())
        mine = [m for m in all_ if m["point"] == self.install.as_posix()]
        if not mine:
            return None
        m = mine[-1]
        try:
            st = os.stat(self.install)
        except OSError:
            return None
        prefix = self.root.as_posix().rstrip("/")
        for h in all_:
            if h["dev"] != m["dev"] or not (h["point"] == prefix or h["point"].startswith(prefix + "/")):
                continue
            hr = h["root"].rstrip("/")
            if hr and not (m["root"] == hr or m["root"].startswith(hr + "/")):
                continue
            path = posixpath.normpath((h["point"][len(prefix):] or "/") + "/" + m["root"][len(hr):])
            try:
                there = os.stat(self.inside(path))
            except (OSError, TypeError):
                continue
            if (there.st_dev, there.st_ino) == (st.st_dev, st.st_ino):
                return path
        return None

    def _mountinfo(self):
        try:
            return self.mountinfo.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""


def writable(st, uid, gid):
    """Whether a container running as uid:gid could write a folder of this stat. Docker gives a numeric user: "uid:gid"
    no other groups, so only the owner's, that group's and everyone's permissions count."""
    if uid == 0:
        return True   # root: with the wizard's root choice, DAC_OVERRIDE lets it
    need = stat.S_IWUSR | stat.S_IXUSR
    if st.st_uid == uid:
        return st.st_mode & need == need
    if st.st_gid == gid:
        return st.st_mode & (need >> 3) == need >> 3
    return st.st_mode & (need >> 6) == need >> 6


def memory_gb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024 / 1024, 1)
    except (OSError, ValueError, IndexError):
        pass
    return 0


def gateway():
    """The container's default gateway: on Docker's bridge network, the machine itself (host.docker.internal)."""
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                f_ = line.split()
                if len(f_) > 2 and f_[1] == "00000000":
                    n = int(f_[2], 16)
                    return ".".join(str((n >> s) & 255) for s in (0, 8, 16, 24))
    except (OSError, ValueError):
        pass
    return ""
