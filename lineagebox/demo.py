"""A public demo: a made-up family's photos that anyone may look around in and change, put back as they were every day.

It is turned on in config.toml ([demo] enabled = true) or with LINEAGEBOX_DEMO_ENABLED, never on the Settings page:
it is how a copy is run, not something its visitors could switch off. With it on:
- The sign-in page has two buttons. Sign in to demo makes a member account for whoever clicks it, as the person
  [demo] person names; Sign in as an admin makes an admin's. Google, passkeys and codes are off (CLOSED), and so are
  sharing and recording stories: what a stranger made would be shown to every other visitor.
- An admin here is locked down: Settings, Status, accounts and Cleanup are shown, but nothing that changes how this
  copy runs or reaches other machines can be done (LOCKED), and the addresses of other machines, proxies and networks
  are not shown (masked, redact). An admin sees everything, other visitors' uploads too (the user's choice,
  2026-11-08), so an admin may not upload; the limits members have on costly asks (zips, looking for faces again)
  hold for them too.
- Uploads are at most [demo] upload_max_mb each, and [demo] uploads from each address until the next reset. What a
  member uploads is seen by them alone among members (access.images_for, `alone`), however its faces are named.
- Link previews (chat apps, social sites) get the sign-in page's preview tags and [demo] preview, one photo of the
  library, at /demo-preview.jpg.
- The AI (Ollama), transcripts (Whisper), looking places up, imports from other programs' metadata, sidecars, backups
  and asking GitHub for updates are off, whatever the catalog's settings say (FORCED): the demo's photos are
  described and transcribed before it opens, and it reaches nothing on the network it runs in.
- `lineagebox demo save` keeps the catalog, the data folder and the watched folders as they are now (the seed, in
  <data>/demo-seed), and `lineagebox demo reset`, with the web app and the worker stopped, puts them back: every
  change, account and upload made since is gone (docs/demo.md).
"""
import dataclasses
import json
import os
import re
import secrets
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from lineagebox import accounts, errors
from lineagebox.config import Config

SEED = "demo-seed"  # in the data folder
UPLOADS_KEY = "demo_uploads"  # in the settings table: {address: uploads}, put back to the seed's (none) by a reset
MOST_VISITORS = 5000  # accounts made between two resets: more is someone filling the catalog
OFF = "That is switched off in this demo."
# whatever the catalog's settings say: what would reach other machines, or write outside the catalog
FORCED = {"accounts_enabled": True, "google_client_id": "", "google_client_secret": "",
          "describe_enabled": False, "ollama_embed_model": "", "transcribe_enabled": False, "whisper_url": "",
          "places_lookup": False, "import_enabled": False, "sidecars_enabled": False, "backup_enabled": False,
          "update_check": False}
# routes a visitor is told are off (app.route_gate): other ways in, sharing, and recordings of their own
CLOSED = frozenset({
    "/auth/google", "/auth/google/callback", "/auth/local",
    "/auth/passkey/start", "/auth/passkey/finish", "/auth/invite", "/auth/invite/check", "/auth/invite/start",
    "/auth/invite/finish", "/passkeys", "/api/passkeys/start", "/api/passkeys/finish",
    "/api/passkeys/{passkey_id}/rename", "/api/passkeys/{passkey_id}/remove",
    "/api/accounts/{account_id}/invite", "/api/accounts/{account_id}/invite/cancel",
    "/api/shares", "/api/stories", "/settings/codes", "/api/ai/models",
})
# what an admin is told is off here, but for looking (GET): changing how this copy runs (settings, accounts, HTTPS,
# updates, backups, photo frames, watched folders), asking the AI services, and reading papers with the AI
LOCKED = frozenset({
    "/api/settings", "/api/settings/accounts", "/api/settings/address", "/api/settings/network-paths",
    "/api/accounts", "/api/accounts/{account_id}", "/api/accounts/{account_id}/delete",
    "/api/accounts/{account_id}/local-code", "/api/accounts/{account_id}/local-code/check",
    "/api/accounts/{account_id}/local-code/remove", "/api/accounts/{account_id}/rules",
    "/api/accounts/{account_id}/rules/remove", "/api/accounts/{account_id}/signout", "/api/see-as",
    "/api/https", "/api/https/off", "/api/https/upload", "/api/update", "/api/update/check", "/api/backup",
    "/api/devices", "/api/devices/{device_id}/delete", "/api/devices/{device_id}/revoke",
    "/api/folders/watch", "/api/folders/unwatch", "/api/ollama/pull", "/api/ollama/check", "/api/whisper/check",
    "/api/family/read",
})
HIDDEN = "(hidden in the demo)"
_ADDRESS = re.compile(r"\bhttps?://[^\s;,)<>\"']+|\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")
# what a reset leaves alone in the data folder: the seed itself, the catalog (put back on its own), its own backups,
# the updater's folder, HTTPS certificates, the face models, work files and the data folder's own settings
KEEP_OUT = frozenset({SEED, "lineagebox.db", "lineagebox.db-wal", "lineagebox.db-shm", "backups", "update", "certs",
                      "insightface", "tmp", "config.toml"})


class Problem(errors.Problem):
    """The demo can't do what was asked; the message is shown."""


def forced(cfg: Config) -> dict:
    """The settings the demo holds to (settings.current applies them over the catalog's)."""
    return FORCED if cfg.demo_enabled else {}


def closed(cfg: Config, path: str, method: str) -> bool:
    """Whether a route is off for this request (app.route_gate): CLOSED always, LOCKED but for looking."""
    return cfg.demo_enabled and (path in CLOSED or (path in LOCKED and method not in ("GET", "HEAD")))


# ---- what an admin is shown ----------------------------------------------------------------------------------------

def masked(cfg: Config) -> Config:
    """The settings as the demo's admins see them: without the addresses of other machines (Ollama, Whisper), the
    proxy and networks, the network paths and the certificate's files."""
    return dataclasses.replace(cfg, ollama_url=HIDDEN, whisper_url=HIDDEN, trusted_proxies=(HIDDEN,),
                               allowed_networks=(HIDDEN,), network_paths=(), share_path="", tls_cert="", tls_key="",
                               google_client_id="", google_client_secret="", places_contact="")


def redact(value):
    """value with every web address and IP address in its words taken out, through lists, tuples and dicts (Status's
    activity and problems: "worker started ... Ollama http://10.1.2.3:11434")."""
    if isinstance(value, str):
        return _ADDRESS.sub(HIDDEN, value)
    if isinstance(value, sqlite3.Row):  # an activity line: read by name in the page, as a dict is
        return {k: redact(value[k]) for k in value.keys()}
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact(v) for v in value)
    return value


def preview_image(con: sqlite3.Connection, cfg: Config) -> Path | None:
    """The photo link previews show ([demo] preview, a file of the library), as its preview; None without one."""
    if not (cfg.demo_enabled and cfg.demo_preview):
        return None
    row = con.execute("SELECT i.sha256 FROM file f JOIN image i ON i.id = f.image_id WHERE f.path = ?",
                      (cfg.demo_preview.strip("/"),)).fetchone()
    path = cfg.preview_path(row[0]) if row else None
    return path if path is not None and path.is_file() else None


# ---- visitors ------------------------------------------------------------------------------------------------------

def person(con: sqlite3.Connection, cfg: Config) -> int:
    """Who visitors are in the family tree: [demo] person, by id or by name."""
    who = cfg.demo_person.strip()
    if not who:
        raise Problem("The demo isn't ready yet: [demo] person, who visitors are in the family tree, isn't set.")
    row = (con.execute("SELECT id FROM person WHERE id = ?", (int(who),)).fetchone() if who.isdecimal()
           else con.execute("SELECT id FROM person WHERE name = ? COLLATE NOCASE ORDER BY id", (who,)).fetchone())
    if row is None:
        raise Problem(f"The demo isn't ready yet: there is nobody called {who} in its family tree.")
    return row[0]


def visitor(con: sqlite3.Connection, cfg: Config, admin: bool = False) -> int:
    """An account of their own for someone who clicked Sign in to demo (a member's) or Sign in as an admin: its
    uploads, ticks and undo are theirs. Its email is made up (.invalid is never anyone's)."""
    pid = person(con, cfg)
    made = con.execute("SELECT count(*) FROM account WHERE email LIKE 'visitor-%@demo.invalid'").fetchone()[0]
    if made >= MOST_VISITORS:
        raise Problem("The demo has had too many visitors today. It starts again soon: try again later.")
    account_id = accounts.add(con, f"visitor-{secrets.token_hex(6)}@demo.invalid", pid,
                              "admin" if admin else "member")
    con.commit()
    return account_id


def is_visitor(email: str | None) -> bool:
    return bool(email) and email.startswith("visitor-") and email.endswith("@demo.invalid")


# ---- uploads from each address -------------------------------------------------------------------------------------

def _counts(con: sqlite3.Connection) -> dict:
    row = con.execute("SELECT value FROM settings WHERE key = ?", (UPLOADS_KEY,)).fetchone()
    return json.loads(row[0]) if row else {}


def uploads_left(con: sqlite3.Connection, ip: str, most: int) -> int:
    return max(0, most - _counts(con).get(ip, 0))


def take_upload(con: sqlite3.Connection, ip: str, most: int) -> bool:
    """Count one upload from this address, before it is received (several go up at once); False when it has had
    `most` since the last reset. In the catalog, so restarting the web app doesn't start the count again."""
    con.commit()
    con.execute("BEGIN IMMEDIATE")  # the write lock: two uploads at once can't both take the last one
    try:
        counts = _counts(con)
        if counts.get(ip, 0) >= most:
            con.rollback()
            return False
        counts[ip] = counts.get(ip, 0) + 1
        con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (UPLOADS_KEY, json.dumps(counts)))
        con.commit()
        return True
    except BaseException:
        con.rollback()
        raise


def give_back(con: sqlite3.Connection, ip: str) -> None:
    """An upload that wasn't taken (too big, not a photo, the connection dropped) doesn't count."""
    con.commit()
    con.execute("BEGIN IMMEDIATE")
    try:
        counts = _counts(con)
        if counts.get(ip, 0) > 0:
            counts[ip] -= 1
            con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
                        (UPLOADS_KEY, json.dumps(counts)))
        con.commit()
    except BaseException:
        con.rollback()
        raise


# ---- saving and putting back ---------------------------------------------------------------------------------------

def seed_dir(cfg: Config) -> Path:
    return cfg.data_dir / SEED


def save(con: sqlite3.Connection, cfg: Config) -> dict:
    """Keep everything as it is now as what each reset puts back: a checked copy of the catalog (SQLite's backup, so
    the web app and the worker may run meanwhile), the data folder but for KEEP_OUT, and each watched folder."""
    seed = seed_dir(cfg)
    seed.mkdir(parents=True, exist_ok=True)
    part = seed / ".lineagebox.db.part"
    part.unlink(missing_ok=True)
    copy = sqlite3.connect(part)
    try:
        con.commit()
        con.backup(copy)
        if (found := copy.execute("PRAGMA integrity_check").fetchone()[0]) != "ok":
            raise Problem(f"The copy of the catalog isn't whole ({found}): nothing was saved.")
        version = copy.execute("PRAGMA user_version").fetchone()[0]
        copy.execute("PRAGMA journal_mode = DELETE")  # one file, with no write-ahead log beside it
    finally:
        copy.close()
    os.replace(part, seed / "lineagebox.db")
    copied, removed = _mirror(cfg.data_dir, seed / "data", KEEP_OUT)
    folders = list(cfg.folders)
    for folder in folders:
        c, r = _mirror(cfg.share_root / folder, seed / "media" / folder)
        copied, removed = copied + c, removed + r
    about = {"saved_at": datetime.now().isoformat(timespec="seconds"), "catalog_version": version, "folders": folders}
    (seed / "seed.json").write_text(json.dumps(about, indent=1), encoding="utf-8")
    return {**about, "copied": copied, "removed": removed}


def reset(cfg: Config) -> dict:
    """Put back what save kept: the catalog, the data folder (files made since are deleted, those changed copied
    back) and the watched folders as they were saved (uploads deleted). Only with the demo on, as this deletes every
    photo added since, and with the web app and the worker stopped: they would go on with what they had in memory."""
    if not cfg.demo_enabled:
        raise Problem("This isn't a demo ([demo] enabled is off): a reset would delete every photo and change made "
                      "since `lineagebox demo save`.")
    seed = seed_dir(cfg)
    try:
        about = json.loads((seed / "seed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Problem(f"There is nothing to put back: no seed in {seed}. Make one with `lineagebox demo save`.") \
            from None
    # all of it or nothing: a catalog put back beside uploads that couldn't be deleted would list files it doesn't know
    for folder in [cfg.data_dir, *(cfg.share_root / f for f in about.get("folders", []))]:
        if not os.access(folder, os.W_OK):
            raise Problem(f"{folder} can't be written here, so nothing was put back. In Docker, run the reset in the "
                          "web service, which may write the photos (the worker only reads them).")
    kept = sqlite3.connect(seed / "lineagebox.db")
    live = sqlite3.connect(cfg.db_path)
    try:
        kept.backup(live)  # through SQLite, so the catalog's write-ahead log can't bring back what was in it
    finally:
        live.close()
        kept.close()
    copied, removed = _mirror(seed / "data", cfg.data_dir, KEEP_OUT)
    for folder in about.get("folders", []):
        c, r = _mirror(seed / "media" / folder, cfg.share_root / folder)
        copied, removed = copied + c, removed + r
    return {**about, "copied": copied, "removed": removed}


def _mirror(src: Path, dst: Path, keep_out=frozenset()) -> tuple[int, int]:
    """Make dst hold what src holds, file for file: (files copied, files and folders deleted). A file the same size
    with the same time is taken to be the same. keep_out: names at the top that are neither copied nor deleted. Links
    are never followed."""
    copied = removed = 0
    dst.mkdir(parents=True, exist_ok=True)
    names = set()
    if src.is_dir():
        for entry in os.scandir(src):
            if entry.name in keep_out:
                continue
            names.add(entry.name)
            target = dst / entry.name
            if entry.is_dir(follow_symlinks=False):
                if target.is_symlink() or target.is_file():
                    target.unlink()
                c, r = _mirror(Path(entry.path), target)
                copied, removed = copied + c, removed + r
            elif entry.is_file(follow_symlinks=False):
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                    removed += 1
                was = entry.stat(follow_symlinks=False)
                try:
                    now = target.stat(follow_symlinks=False)
                    same = now.st_size == was.st_size and int(now.st_mtime) == int(was.st_mtime)
                except OSError:
                    same = False
                if not same:
                    shutil.copy2(entry.path, target, follow_symlinks=False)
                    copied += 1
    for entry in os.scandir(dst):
        if entry.name in keep_out or entry.name in names:
            continue
        if entry.is_dir(follow_symlinks=False):
            shutil.rmtree(entry.path)
        else:
            os.unlink(entry.path)
        removed += 1
    return copied, removed
