"""What the system says about mounted file systems: where they are and what kind they are (Linux; elsewhere only what
os.path.ismount can tell).

In Docker every photo source is its own mount under the media root (a folder of this machine, a NAS share), and a
file can only be renamed within one mount: cleanup puts the Removed folder of a watched folder that is itself a mount
inside it. The catalog must not be on a network file system, where SQLite's locking and shared memory don't work.
"""
import os
import re
from pathlib import Path

MOUNTINFO = Path("/proc/self/mountinfo")
# network file systems, as /proc names them
NETWORK = {"cifs", "smb3", "smbfs", "nfs", "nfs4", "afs", "ceph", "glusterfs", "9p", "fuse.sshfs", "fuse.rclone",
           "davfs", "fuse.davfs2"}


def _unescape(field: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), field)  # spaces and such are written \040


def mounts() -> dict[str, str]:
    """Mount point -> file system type, for every mount this process sees. Empty where /proc isn't there."""
    try:
        lines = MOUNTINFO.read_text().splitlines()
    except OSError:
        return {}
    out = {}
    for line in lines:
        fields = line.split()
        try:
            sep = fields.index("-")
            out[_unescape(fields[4])] = fields[sep + 1]
        except (ValueError, IndexError):
            continue
    return out


def is_mount(path: Path) -> bool:
    """Whether path is where a file system (or a Docker bind mount, even of another folder of the same disk) starts."""
    try:
        if os.path.ismount(path):
            return True
    except OSError:
        return False
    return Path(path).resolve().as_posix() in mounts()


def fs_type(path: Path) -> str | None:
    """The type of the file system path is on (ext4, cifs, ...), or None if unknown."""
    real = Path(path).resolve().as_posix()
    best = None
    for point, kind in mounts().items():
        if (real == point or real.startswith(point.rstrip("/") + "/")) and (best is None or len(point) > len(best[0])):
            best = (point, kind)
    return best[1] if best else None


def is_network(path: Path) -> bool:
    return fs_type(path) in NETWORK
