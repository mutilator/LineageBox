"""The machines the setup wizard knows (nas.toml): for each NAS or kind of computer, the user and group ids to suggest,
where its folders are, how its own Docker app takes a compose file, and its models' processors and memory. Anyone can
correct it or add a machine by changing nas.toml alone; check() says what is wrong with it, and tests/test_setup.py
runs it on every change.
"""
import re
import tomllib
from functools import lru_cache
from pathlib import Path

FILE = Path(__file__).with_name("nas.toml")
TEXTS = ("docker_app", "install_dir", "photos_example", "share_parent", "network_path", "docker_socket", "ssh_how",
         "model_from", "summary")
LISTS = ("compose_how", "ids_how", "share_how", "permissions_how", "detect", "notes")
SWITCHES = ("sudo", "quota", "host_network", "rootless", "windows")
KINDS = ("nas", "computer", "other")
ARCHES = ("amd64", "arm64", "armv7", "armv5", "i686")
ID = re.compile(r"[a-z0-9][a-z0-9-]*")


@lru_cache(maxsize=4)
def load(path=FILE):
    """nas.toml's machines by id, in the file's order (the page lists them so; the first that matches a machine's files
    is the one detected)."""
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    return {s["id"]: s for s in raw.get("system", []) if isinstance(s, dict) and "id" in s}


def check(path=FILE):
    """What is wrong in nas.toml, as sentences; [] when nothing is."""
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        return [f"nas.toml can't be read: {e}"]
    out, seen = [], set()
    for n, s in enumerate(raw.get("system", []), 1):
        name = s.get("id", f"entry {n}")
        say = lambda text: out.append(f"{name}: {text}")  # noqa: E731
        if not isinstance(s.get("id"), str) or not ID.fullmatch(s["id"]):
            say("id must be lowercase letters, digits and dashes")
        elif s["id"] in seen:
            say("id is used twice")
        seen.add(s.get("id"))
        if not isinstance(s.get("name"), str) or not s["name"].strip():
            say("needs a name")
        if s.get("kind", "nas") not in KINDS:
            say(f"kind must be one of {', '.join(KINDS)}")
        for key, value in s.items():
            if key in TEXTS and not isinstance(value, str):
                say(f"{key} must be text")
            elif key in LISTS and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
                say(f"{key} must be a list of texts")
            elif key in SWITCHES and not isinstance(value, bool):
                say(f"{key} must be true or false")
            elif key not in (*TEXTS, *LISTS, *SWITCHES, "id", "name", "kind", "users", "models"):
                say(f"{key} isn't a key the wizard knows (a typo?)")
        for path_key in ("install_dir", "photos_example", "share_parent", "docker_socket"):
            value = s.get(path_key)
            if isinstance(value, str) and value and not (value.startswith(("/", "~/")) or re.match(r"[A-Za-z]:/",
                                                                                                  value)):
                say(f"{path_key} must be a whole path")
        if isinstance(s.get("network_path"), str) and "{share}" not in s["network_path"]:
            say("network_path needs {share} in it")
        for d in s.get("detect", []):
            if isinstance(d, str) and d.split(":", 1)[0].strip().startswith("/") is False:
                say(f"detect entries are paths from the root, starting with /: {d!r}")
        if isinstance(s.get("model_from"), str) and ":" not in s["model_from"]:
            say("model_from is written as \"/path/to/file: key\"")
        for u in s.get("users", []):
            if not (isinstance(u, dict) and isinstance(u.get("uid"), int) and isinstance(u.get("gid"), int)
                    and isinstance(u.get("who"), str) and u["uid"] >= 0 and u["gid"] >= 0):
                say(f"each user needs a uid, a gid (whole numbers) and who: {u!r}")
            elif set(u) - {"uid", "gid", "who", "guess"} or not isinstance(u.get("guess", False), bool):
                say(f"a user has uid, gid, who and guess (true or false) only: {u!r}")
        names = set()
        for m in s.get("models", []):
            if not (isinstance(m, dict) and isinstance(m.get("name"), str) and m["name"].strip()):
                say(f"each model needs a name: {m!r}")
                continue
            if m["name"] in names:
                say(f"the model {m['name']} is listed twice")
            names.add(m["name"])
            if m.get("arch") not in (None, *ARCHES):
                say(f"{m['name']}: arch must be one of {', '.join(ARCHES)}")
            if "memory_gb" in m and not (isinstance(m["memory_gb"], (int, float)) and m["memory_gb"] > 0):
                say(f"{m['name']}: memory_gb must be a number of GB")
            if "docker" in m and not isinstance(m["docker"], bool):
                say(f"{m['name']}: docker must be true or false")
            for key in m:
                if key not in ("name", "cpu", "arch", "memory_gb", "docker", "note"):
                    say(f"{m['name']}: {key} isn't a key the wizard knows (a typo?)")
    if not raw.get("system"):
        out.append("nas.toml has no [[system]] entries")
    return out
