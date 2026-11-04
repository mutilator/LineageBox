"""SQLite connection and schema migrations (tracked with PRAGMA user_version)."""
import importlib.util
import os
import sqlite3
import threading
import time
from pathlib import Path

from lineagebox import mounts
from lineagebox.config import Config, ConfigError

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
BACKUPS = "backups"  # in the data folder: the catalog as it was before each upgrade
KEEP = 3


class Connection(sqlite3.Connection):
    """A catalog connection. me: the person the signed-in account is in the family tree, when someone is signed in
    (family.me, kin words); access.scope sets it, with a member's views of only what they may see."""
    me: int | None = None


def unwritable(cfg: Config) -> str | None:
    """Why the catalog folder can't be written by the user LineageBox runs as, if it can't: PUID isn't its owner (a
    folder Docker made as root, an install that ran as root before, rootless Docker). None when it can."""
    places = [cfg.data_dir]
    if cfg.data_dir.is_dir():
        places += sorted(cfg.data_dir.iterdir())
    for place in places:
        if place.exists() and not os.access(place, os.W_OK | (os.X_OK if place.is_dir() else 0)):
            st = place.stat()
            me = f"{os.geteuid()}:{os.getegid()}" if hasattr(os, "geteuid") else "this user"
            # rootless Docker and Podman show the person's own files as root's: there, PUID=0 is the person
            rootless = " With rootless Docker or Podman, that is PUID=0 and PGID=0." if st.st_uid == 0 else ""
            return (f"{place} can't be written by the user LineageBox runs as ({me}); it belongs to "
                    f"{st.st_uid}:{st.st_gid}. Run LineageBox as its owner (PUID and PGID in .env), or give the "
                    f"catalog folder to {me} (sudo chown -R {me} on it).{rootless} See Users and permissions in "
                    "docs/install.md.")
    return None


def connect(cfg: Config, *, migrate_schema: bool = True, check_same_thread: bool = True) -> sqlite3.Connection:
    if migrate_schema:  # once per process: it starts with the migrations
        _check_data_dir(cfg.data_dir)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(cfg.db_path, timeout=30, check_same_thread=check_same_thread, factory=Connection)
    con.row_factory = sqlite3.Row
    for attempt in range(50):
        try:
            if con.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                con.execute("PRAGMA journal_mode = WAL")  # needs the database to itself and does not wait for it
            break
        except sqlite3.OperationalError:
            if attempt == 49:
                raise
            time.sleep(0.2)  # another process is opening a new catalog at the same moment
    con.execute("PRAGMA synchronous = NORMAL")
    con.execute("PRAGMA foreign_keys = ON")
    if migrate_schema:
        migrate(con, cfg)
    return con


def _check_data_dir(data_dir: Path) -> None:
    """Say plainly what is wrong with the catalog folder, instead of SQLite's "unable to open database file"."""
    if mounts.is_network(data_dir):
        # SQLite's locks and WAL shared memory don't work over the network: the catalog would get corrupted
        raise ConfigError(f"The catalog folder {data_dir} is on a network share ({mounts.fs_type(data_dir)}). "
                          "Put data_dir on a disk of the machine LineageBox runs on.")
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    if not os.access(data_dir, os.W_OK):
        who = f"user {os.getuid()}, group {os.getgid()}" if hasattr(os, "getuid") else "this user"
        raise ConfigError(f"The catalog folder {data_dir} cannot be written by {who}. Make that user its owner "
                          "(chown), or run LineageBox as its owner (PUID and PGID).")


def migrate(con: sqlite3.Connection, cfg: Config, upto: int | None = None) -> None:
    """Apply every migrations/NNN_*.sql (or NNN_*.py, whose upgrade(con, cfg) can use the settings) newer than the
    database, each in its own transaction.

    The web and worker containers start together and both get here: each migration runs under the write lock and
    re-checks the version, so the second process finds it done instead of applying it twice.

    A .py migration that rebuilds a table others refer to sets FOREIGN_KEYS_OFF: dropping the old table would
    otherwise delete every row that refers to it (ON DELETE CASCADE). Foreign keys can only be switched off outside a
    transaction, so it is done around this one, and the references are checked before it is committed (SQLite's own
    recipe for changing a table: https://sqlite.org/lang_altertable.html#otheralter).
    """
    con.commit()
    paths = sorted([*MIGRATIONS_DIR.glob("*.sql"), *MIGRATIONS_DIR.glob("*.py")])
    newest = max(int(p.name.split("_", 1)[0]) for p in paths)
    have = con.execute("PRAGMA user_version").fetchone()[0]
    if have > newest:  # an older LineageBox on a catalog a newer one changed: it would misread it, or damage it
        raise ConfigError(f"This catalog was upgraded by a newer version of LineageBox (catalog version {have}; this "
                          f"version knows up to {newest}). Install the newer LineageBox again, or put back the "
                          f"copy of the catalog made before that upgrade, in {cfg.data_dir / BACKUPS}.")
    if 0 < have < newest and upto is None:
        _back_up(con, cfg, have)
    for path in paths:
        version = int(path.name.split("_", 1)[0])
        if version <= con.execute("PRAGMA user_version").fetchone()[0] or (upto is not None and version > upto):
            continue  # done already, or (tests) not wanted yet
        module = _module(path) if path.suffix == ".py" else None
        keys_off = getattr(module, "FOREIGN_KEYS_OFF", False)
        keys_were = con.execute("PRAGMA foreign_keys").fetchone()[0]
        if keys_off:
            con.execute("PRAGMA foreign_keys = OFF")
        con.execute("BEGIN IMMEDIATE")
        try:
            if con.execute("PRAGMA user_version").fetchone()[0] < version:
                if module:
                    module.upgrade(con, cfg)
                else:
                    for statement in _statements(path.read_text(encoding="utf-8")):
                        con.execute(statement)
                if keys_off and (broken := con.execute("PRAGMA foreign_key_check").fetchall()):
                    raise RuntimeError(f"migration {path.name} left {len(broken)} broken references, e.g. "
                                       f"{tuple(broken[0])}")
                con.execute(f"PRAGMA user_version = {version}")
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            if keys_off:
                con.execute(f"PRAGMA foreign_keys = {'ON' if keys_were else 'OFF'}")


def _back_up(con: sqlite3.Connection, cfg: Config, version: int) -> None:
    """A copy of the catalog as it is, before an update's migrations change it: <data>/backups/catalog-vN-<date>.db,
    the last KEEP of them kept. Web and worker start together: each copies, and keeps its copy only if it is still the
    catalog before the upgrade (one made while the other process was already upgrading is thrown away)."""
    folder = cfg.data_dir / BACKUPS
    if any(folder.glob(f"catalog-v{version}-*.db")):
        return
    target = folder / f"catalog-v{version}-{time.strftime('%Y%m%d-%H%M%S')}.db"
    part = folder / f".catalog-v{version}-{os.getpid()}-{threading.get_ident()}.part"  # each its own
    try:
        folder.mkdir(exist_ok=True)
        dest = sqlite3.connect(part)
        try:
            con.backup(dest)
            before = dest.execute("PRAGMA user_version").fetchone()[0] == version
        finally:
            dest.close()
        if before and not any(folder.glob(f"catalog-v{version}-*.db")):
            os.replace(part, target)
        part.unlink(missing_ok=True)
    except (OSError, sqlite3.Error) as e:
        part.unlink(missing_ok=True)
        raise ConfigError(f"Before upgrading the catalog, LineageBox makes a copy of it in {folder}, and that failed "
                          f"({e}). Make room there (the copy is as big as lineagebox.db), then start LineageBox "
                          f"again.") from None

    def age(path):
        try:
            return path.stat().st_mtime
        except OSError:  # the other process pruned it just now
            return 0

    for old in sorted(folder.glob("catalog-v*.db"), key=age)[:-KEEP]:
        old.unlink(missing_ok=True)


def _statements(sql: str):
    """Split a script into statements (executescript would commit first, giving up the lock)."""
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            yield buf
            buf = ""


def _module(path: Path):
    spec = importlib.util.spec_from_file_location(f"lineagebox.migrations.m{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
