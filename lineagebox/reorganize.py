"""Reorganizing the photo folders (Settings → Watched folders → Reorganize folders, admins): every photo, video and
sound recording moved into a folder for its year and month (2016/2016-07), or, with by_type, the same under a folder
for its kind at the top (Photos, Scans, Videos, Recordings).

LineageBox moves the files itself, so the catalog follows each one at once: nothing is read again, no preview is lost
(moved by hand, every file would look deleted and then new to the scan), and faces, tags, dates and comments stay.
Each file keeps the folder it came from (file.from_folder_id, catalog.ORIGIN): what that folder's name said (a date,
an event, a place, who is in it), what an admin gave it by hand, and hiding or sharing it, still go for the file, and
the photos beside it there are still the ones beside it (date_hints, scan_dates). The new folders say nothing of
their own: they are named from the date the photo already had.

Where each one goes, in the watched folder it is in (a rename: a file never leaves its disk):
- <year>/<year>-<month>/ by the date the catalog has for it; <year>/ alone when only the year is known (a date set by
  hand to a span longer than a month, or a folder's name that gives only the year); Undated/ without one. A Live
  Photo's clip goes with its still.
- its name stays. One already taken in the new folder gets " (2)", and so on, with one number for the files that
  share its name there (a RAW and its JPEG, a still and its clip) and for its own files below.
- what belongs to it goes with it: its sidecars (IMG_1234.JPG.xmp, IMG_1234.xmp), Google Takeout's IMG_1234.JPG.json,
  the 720p copy LineageBox made of a video, and other files of its name beside it (IMG_1234.AAE).
- everything else in the folders (a PSD, another program's files, Thumbs.db, a photo or video that can't be read)
  goes to Other files/<the path it had>, so the old folders go away and nothing is lost or renamed.
- left as they are: the folders the scan ignores ([scan] ignore_dirs), the family's uploads folders, the Removed
  folders, LineageBox's own, Other files, a folder on another disk mounted inside, and a folder with photos or videos
  the worker hasn't read yet (until it has: run it again then).
- asked to, it deletes what other programs leave in photo folders that holds nothing of the family's (JUNK_FILES,
  JUNK_DIRS: thumbnail caches, folder settings, Dropbox's and macOS's marks) rather than move it to Other files, and
  takes away every empty folder when done, not only those it emptied (take_away_empty).

It runs in the web app, beside the request that starts it (start()), CHUNK files a write, each recorded in
reorganize_move as it goes; the latest reorganization can be put back (undo()). Meanwhile the scan leaves the folders
alone (busy(), and mark() for one already looking through them), and so do the copies service and the sidecars.
"""
import json
import os
import re
import shutil
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from lineagebox import clock, errors, scan, video
from lineagebox.config import Config

KINDS = {"photo": "Photos", "scan": "Scans", "video": "Videos", "audio": "Recordings"}  # the folders at the top
OTHER = "Other files"
UNDATED = "Undated"
CHUNK = 200       # files moved in one write: the web app waits for each
STALE = 600       # seconds without a sign of life after which a run has stopped (the web app restarted meanwhile)
BEAT = 60         # seconds between signs of life while it looks through the folders or tidies up after the moves
EXAMPLES = 12     # moves the preview shows
MOST_PROBLEMS = 50
MADE_KIND = {"top": "media_type", "date": "date", "other": "misc"}  # folder.kind of the folders made: never parsed
BUSY = "the photo folders are being reorganized: they are scanned again once that is done"
MOVED = "files were moved by a reorganization while the folders were looked through: they are scanned again next pass"
# Google Takeout's sidecar of a numbered copy: IMG_1234(1).jpg has IMG_1234.jpg(1).json
_NUMBERED = re.compile(r"^(.*)(\(\d+\))(\.[^.]+)$")
TAKEOUT_CUT = 40  # Takeout cuts a sidecar's name short (to 51 characters): a cut one is matched on this much at least
PART_FRESH = 24 * 3600  # seconds a half-written file (.name.part) holds its folder back; an older one was left behind

# What other programs leave in photo folders that holds nothing of the family's: deleted, asked to (remove_junk), rather
# than kept in Other files. Names in lower case. Picasa's .picasa.ini is not one: it holds stars and names on faces.
JUNK_FILES = frozenset({
    "thumbs.db", "ehthumbs.db", "ehthumbs_vista.db", "desktop.ini",   # Windows' thumbnail caches, folder settings
    ".ds_store", ".localized", "icon\r",                               # macOS's folder settings and icons
    ".dropbox", ".dropbox.attr",                                       # Dropbox's marks
    ".nomedia",                                                        # Android: don't show this folder's pictures
    "zbthumbnail.info", ".bridgesort", ".bridgelabelsandratings",      # ACDSee's and Adobe Bridge's caches
})
JUNK_PREFIXES = ("._", "~$")  # macOS's resource files (._IMG_1.JPG), Office's lock files (~$letter.docx)
JUNK_DIRS = frozenset({  # thumbnail caches and system folders, deleted whole when the catalog has nothing in them
    ".thumbnails", "@eadir", ".@__thumb", ".dropbox.cache", ".appledouble", ".spotlight-v100", ".trashes",
    ".fseventsd", ".temporaryitems",
})

_stop = threading.Event()  # set to stop the run in progress (stop())


class Problem(errors.Problem):
    """A reorganization that can't start or be undone now; the message says why, for the admin."""
    status = 409


@dataclass
class Move:
    old: str                     # paths from the share's root, '/' separated
    new: str
    file_id: int | None = None   # the catalog's file; None for one it doesn't list
    media: bool = False          # a photo, video or recording of the catalog (not a Live Photo's clip)
    of: str | None = None        # the old path of the photo it goes with: it stays if that can't be moved


@dataclass
class Plan:
    moves: list[Move] = field(default_factory=list)
    in_place: int = 0                                   # photos, videos and recordings already where they belong
    renamed: int = 0                                    # given " (2)" and so on in their new folder
    waiting: list[str] = field(default_factory=list)    # folders with photos or videos not read yet: left for now
    left: list[str] = field(default_factory=list)       # folders left alone: on another disk
    tops: Counter = field(default_factory=Counter)      # photos, videos and recordings by the folder at the top
    shared_folders: int = 0                             # shared folders that photos move out of
    made: dict[str, str] = field(default_factory=dict)  # folders to make -> MADE_KIND key
    junk: list[str] = field(default_factory=list)       # files to delete (remove_junk)
    junk_dirs: dict[str, int] = field(default_factory=dict)  # folders to delete whole -> how many files in them
    junk_names: Counter = field(default_factory=Counter)     # what the junk is: Thumbs.db, .thumbnails/

    @property
    def junk_count(self) -> int:
        """Files deleted as junk, those in junk folders too."""
        return len(self.junk) + sum(self.junk_dirs.values())

    @property
    def media(self) -> int:
        return sum(m.media for m in self.moves)

    @property
    def others(self) -> int:
        return sum(not m.media for m in self.moves)

    def examples(self, n: int = EXAMPLES) -> list[Move]:
        """A few of the photos it moves, from all over the library."""
        media = [m for m in self.moves if m.media]
        step = max(1, len(media) // n)
        return media[::step][:n]


# ---- what goes where -------------------------------------------------------------------------------------------------

def plan(con: sqlite3.Connection, cfg: Config, by_type: bool, remove_junk: bool = False, beat=None) -> Plan:
    """Where every file of the watched folders goes, without moving any: the preview, and what run() does.
    remove_junk: what is junk (JUNK_FILES, JUNK_DIRS) is to be deleted rather than moved to Other files. beat: called
    for each folder looked through (run() gives a sign of life with it: a big share takes minutes)."""
    beat = beat or (lambda: None)
    out = Plan()
    images = {r["id"]: r for r in con.execute(
        "SELECT id, media, taken_at, taken_at_source, date_from, date_to, live_of, error FROM image")}
    files = {r["path"]: r for r in con.execute(
        "SELECT id, path, folder_id, from_folder_id, image_id, error FROM file WHERE NOT missing")}
    taken = {r[0].lower() for r in con.execute("SELECT path FROM file")}  # removed and missing files keep theirs too
    scans = _scans(con) if by_type else set()
    year_only = _year_only(con, images)
    leave = _left_alone(con, cfg)
    wanted = {*cfg.image_extensions, *cfg.video_extensions, *cfg.audio_extensions}
    listed: dict[str, set[str]] = {}  # folder -> the lower-case names in it now, as they are looked for
    shared = [r[0] for r in con.execute("SELECT DISTINCT d.path FROM share s JOIN folder d ON d.id = s.folder_id")] \
        if _has_table(con, "share") else []
    moved_from_shared: set[str] = set()
    alive = _alive(con, read=True) if remove_junk else set()  # a junk folder with photos the worker read is kept

    def target_folder(watched: str, image_id: int) -> str:
        img = images[image_id]
        if img["live_of"] is not None and img["live_of"] in images:  # a Live Photo's clip: where its still goes
            return target_folder(watched, img["live_of"])
        kind = "scan" if img["id"] in scans else img["media"] if img["media"] in KINDS else "photo"
        parts = [watched, KINDS[kind]] if by_type else [watched]
        if by_type:
            out.made.setdefault("/".join(parts), "top")
        if not img["taken_at"]:
            parts.append(UNDATED)
        else:
            parts.append(img["taken_at"][:4])
            if image_id not in year_only:
                out.made.setdefault("/".join(parts), "date")
                parts.append(img["taken_at"][:7])
        out.made.setdefault("/".join(parts), "date")
        return "/".join(parts)

    def free(folder: str, names: list[str]) -> bool:
        """Whether none of these names is taken in folder: on the disk, in the catalog, or by a move planned."""
        if folder not in listed:
            try:
                listed[folder] = {n.lower() for n in os.listdir(cfg.share_root / folder)}
            except OSError:
                listed[folder] = set()
        return not any(n.lower() in listed[folder] or f"{folder}/{n}".lower() in taken for n in names)

    def take(folder: str, names: list[str]) -> None:
        listed.setdefault(folder, set()).update(n.lower() for n in names)
        taken.update(f"{folder}/{n}".lower() for n in names)

    for watched in cfg.folders:
        root = cfg.share_root / watched
        try:
            device = root.stat().st_dev
        except OSError:
            continue  # not there now: the scan says so on the Status page
        stack = [watched]
        while stack:
            rel = stack.pop()
            beat()
            try:
                with os.scandir(cfg.share_root / rel) as listing:
                    entries = sorted(listing, key=lambda e: e.name)
            except OSError:
                out.waiting.append(rel)
                continue
            names, media, waits, subs, junk = [], [], False, [], []
            for e in entries:
                try:
                    if e.is_dir(follow_symlinks=False):
                        sub = f"{rel}/{e.name}"
                        if remove_junk and e.name.lower() in JUNK_DIRS and sub not in leave:
                            if sub not in alive:  # a cache the catalog has nothing in: deleted whole
                                out.junk_dirs[sub] = sum(len(f) for _, _, f in os.walk(e.path))
                                out.junk_names[e.name + "/"] += 1
                            continue
                        if (sub in leave or e.name.lower().endswith("-removed")
                                or scan.is_ignored(e.name, cfg.ignore_dirs)):
                            continue
                        if os.stat(e.path, follow_symlinks=False).st_dev != device:  # a disk mounted inside
                            out.left.append(sub)
                            continue
                        subs.append(sub)
                        continue
                    if not e.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    waits = True
                    continue
                if e.name.startswith(".") and e.name.endswith(".part"):  # being written now (a 720p copy, an upload)
                    try:
                        if time.time() - e.stat().st_mtime < PART_FRESH:
                            waits = True
                            continue
                    except OSError:
                        continue  # gone meanwhile
                path = f"{rel}/{e.name}"
                ext = os.path.splitext(e.name)[1].lower()
                row = files.get(path)
                # junk the scan took for a photo (macOS's ._IMG_1.JPG) is junk too: never read as one
                if remove_junk and _is_junk(e.name) and (row is None or row["image_id"] is None
                                                         or (images.get(row["image_id"]) or {"error": 1})["error"]):
                    junk.append(path)
                    continue
                if ext in wanted and not video.is_copy(e.name):
                    img = images.get(row["image_id"]) if row is not None and row["image_id"] is not None else None
                    if img is not None and img["error"] is None:
                        media.append((e.name, row, img))
                        continue
                    if row is None or (row["image_id"] is None and row["error"] is None):
                        waits = True  # new, or not read yet: the worker reads it where it is first
                        continue
                names.append((e.name, row))  # can't be read: with the other files
            stack += reversed(subs)  # in the order of their names: of two photos of one name, the first keeps it
            if waits:
                out.waiting.append(rel)
                continue
            out.junk += junk
            out.junk_names.update(p.rpartition("/")[2] for p in junk)
            others = {name.lower(): (name, row) for name, row in names}
            if not media and not others:
                continue
            # what goes with each photo, by name: its whole name with more after it first (IMG_1234.JPG.xmp), then
            # its name without the extension (IMG_1234.xmp, a RAW's sidecar) for what is left
            belongs: dict[str, list[str]] = defaultdict(list)
            beside = _Beside(others)
            for name, _, _ in media:
                for found in beside.with_name(name.lower()):
                    belongs[name].append(others.pop(found)[0])
            for name, _, _ in media:
                for found in beside.with_stem(os.path.splitext(name.lower())[0]):
                    belongs[name].append(others.pop(found)[0])
            groups: dict[tuple[str, str], list] = defaultdict(list)  # (new folder, name without extension) -> files
            for name, row, img in media:
                to = target_folder(watched, img["id"])
                if to == rel:
                    out.in_place += img["live_of"] is None
                    out.tops[_top(watched, to)] += img["live_of"] is None
                    take(to, [name, *belongs[name]])
                    continue
                groups[(to, os.path.splitext(name)[0].lower())].append((name, row, img))
            for (to, _), group in groups.items():
                n = 1
                while True:
                    suffix = "" if n == 1 else f" ({n})"
                    renames = [(name, _named(name, name, suffix), [(c, _named(c, name, suffix))
                                                                   for c in belongs[name]]) for name, _, _ in group]
                    every = [new for _, new, side in renames for new in (new, *(s for _, s in side))]
                    if free(to, every):
                        break
                    n += 1
                take(to, every)
                out.renamed += len(group) if n > 1 else 0
                for (name, new, side), (_, row, img) in zip(renames, group):
                    old = f"{rel}/{name}"
                    out.moves.append(Move(old, f"{to}/{new}", row["id"], media=img["live_of"] is None))
                    out.moves += [Move(f"{rel}/{c}", f"{to}/{s}", of=old) for c, s in side]
                    out.tops[_top(watched, to)] += img["live_of"] is None
                    moved_from_shared.update(p for p in shared if old.startswith(p + "/") or not p)
            # the rest: under Other files, at the path it had
            for name, row in others.values():
                to = f"{watched}/{OTHER}{rel[len(watched):]}"
                new = name
                while not free(to, [new]):
                    new = _numbered(name, new)
                take(to, [new])
                parts = to.split("/")
                for depth in range(len(watched.split("/")) + 1, len(parts) + 1):
                    out.made.setdefault("/".join(parts[:depth]), "other")
                out.moves.append(Move(f"{rel}/{name}", f"{to}/{new}", row["id"] if row is not None else None))
        if remove_junk:  # Other files is left as it is, but for junk an earlier reorganization put there
            _junk_in(cfg, f"{watched}/{OTHER}", out, alive, files, images, beat)
    out.shared_folders = len(moved_from_shared)
    return out


def _junk_in(cfg: Config, top: str, out: Plan, alive: set[str], files: dict, images: dict, beat) -> None:
    """The junk in a folder and below it (Other files), for plan(): junk folders whole, junk files one by one."""
    for folder, dirs, names in os.walk(cfg.share_root / top):
        beat()
        rel = Path(folder).relative_to(cfg.share_root).as_posix()
        for d in list(dirs):
            if d.lower() in JUNK_DIRS:
                dirs.remove(d)
                if f"{rel}/{d}" not in alive:
                    out.junk_dirs[f"{rel}/{d}"] = sum(len(f) for _, _, f in os.walk(os.path.join(folder, d)))
                    out.junk_names[d + "/"] += 1
        for name in names:
            row = files.get(f"{rel}/{name}")
            if _is_junk(name) and (row is None or row["image_id"] is None
                                   or (images.get(row["image_id"]) or {"error": 1})["error"]):
                out.junk.append(f"{rel}/{name}")
                out.junk_names[name] += 1


def _is_junk(name: str) -> bool:
    low = name.lower()
    return low in JUNK_FILES or low.startswith(JUNK_PREFIXES)


def _alive(con: sqlite3.Connection, read: bool = False) -> set[str]:
    """Every folder that has, or has below it, a file of the catalog that is on the share; read: one the worker read
    (the scan also notes the files of ignored folders, a .thumbnails cache's, without reading them)."""
    alive = set()
    for (path,) in con.execute("SELECT path FROM file WHERE NOT missing"
                               + (" AND image_id IS NOT NULL" if read else "")):
        while (path := path.rpartition("/")[0]) and path not in alive:
            alive.add(path)
    return alive


def _top(watched: str, folder: str) -> str:
    """The folder at the top of the new layout that this one is in: Photos, or a year."""
    return folder[len(watched) + 1:].split("/")[0]


def _named(name: str, photo: str, suffix: str) -> str:
    """The new name of name, which is photo or one of its files, when photo gets suffix: IMG_1 (2).JPG and
    IMG_1 (2).JPG.xmp. A file whose name doesn't start with the photo's (Takeout's IMG_1.jpg(1).json for
    IMG_1(1).jpg) gets the same number before its own extension."""
    stem = os.path.splitext(photo)[0]
    if not suffix:
        return name
    if name.lower().startswith(stem.lower()):
        return stem + suffix + name[len(stem):]
    own, ext = os.path.splitext(name)
    return own + suffix + ext


def _numbered(name: str, current: str) -> str:
    """The next free-looking name for name after current: report.pdf, report (2).pdf, report (3).pdf."""
    stem, ext = os.path.splitext(name)
    m = re.fullmatch(re.escape(stem) + r" \((\d+)\)" + re.escape(ext), current)
    return f"{stem} ({int(m[1]) + 1 if m else 2}){ext}"


class _Beside:
    """The files of one folder that aren't photos or videos (others: lower-case name -> ...), looked up by the names
    of the photos beside them. A folder can hold thousands of each (a Google Takeout), so each is found by its name,
    never by going through them all for every photo. Only those still in others are given."""

    def __init__(self, others: dict):
        self.others = others
        self.prefixes: dict[str, list[str]] = defaultdict(list)  # 'img_1.jpg' -> ['img_1.jpg.xmp', ...]
        self.stems: dict[str, list[str]] = defaultdict(list)     # 'img_1' -> ['img_1.xmp', ...]
        self.cut: dict[str, list[str]] = defaultdict(list)       # Takeout sidecars cut short, by their start
        for n in others:
            for i, ch in enumerate(n):
                if ch == "." and i:
                    self.prefixes[n[:i]].append(n)
            self.stems[os.path.splitext(n)[0]].append(n)
            if n.endswith(".json") and len(n) - 5 >= TAKEOUT_CUT:
                self.cut[n[:TAKEOUT_CUT]].append(n)

    def with_name(self, name: str) -> list[str]:
        """Those that carry a photo's whole name (lower case): IMG_1234.JPG.xmp, IMG_1234.JPG.json, a video's 720p
        copy, Google Takeout's sidecars (cut short when long, IMG_1234.jpg(1).json for IMG_1234(1).jpg)."""
        found = list(self.prefixes.get(name, ()))
        if m := _NUMBERED.match(name):
            found += [f"{m[1]}{m[3]}{m[2]}.json", f"{m[1]}{m[3]}.supplemental-metadata{m[2]}.json"]
        whole = name + ".supplemental-metadata"
        found += [n for n in self.cut.get(whole[:TAKEOUT_CUT], ()) if whole.startswith(n[:-5])]
        return list(dict.fromkeys(n for n in found if n in self.others))

    def with_stem(self, stem: str) -> list[str]:
        """Those named as the photo without its extension: IMG_1234.xmp (a RAW's sidecar), IMG_1234.AAE, and a
        Pixel's motion clip, PXL_1.MP beside PXL_1.MP.jpg."""
        return [n for n in (*self.stems.get(stem, ()), stem) if n in self.others]


def _scans(con: sqlite3.Connection) -> set[int]:
    """The photos taken for scans: by a scanner, or named so, by file or folder (scan_dates.py)."""
    from lineagebox import scan_dates
    cameras = [c for (c,) in con.execute("SELECT DISTINCT camera FROM image WHERE camera IS NOT NULL")
               if scan_dates.SCANNER.search(c)]
    ids = {r[0] for r in con.execute(f"""SELECT DISTINCT f.image_id FROM file f WHERE NOT f.missing
                                         AND f.image_id IS NOT NULL AND {scan_dates.NAMED}""",
                                     {"scan": scan_dates.SCAN_PATH})}
    ids |= {r[0] for r in con.execute("SELECT id FROM image WHERE media = 'photo' AND camera IN "
                                      "(SELECT value FROM json_each(?))", (json.dumps(cameras),))}
    return ids


def _year_only(con: sqlite3.Connection, images: dict) -> set[int]:
    """Photos whose month isn't known: set by hand to a span longer than a month, or dated by a folder that names
    only the year."""
    from lineagebox.folders import FolderIndex
    out = {i for i, r in images.items() if r["taken_at_source"] == "user" and r["date_from"] and r["date_to"]
           and r["date_from"][:7] != r["date_to"][:7]}
    by_folder = [i for i, r in images.items() if r["taken_at_source"] == "folder"]
    if by_folder:
        index = FolderIndex(con)
        out |= {i for i in by_folder if index.context(i).month is None}
    return out


def _left_alone(con: sqlite3.Connection, cfg: Config) -> set[str]:
    """Folders a reorganization never goes into: the family's uploads, Other files, LineageBox's own, those set to be
    ignored by hand."""
    out = {f"{w}/{OTHER}" for w in cfg.folders}
    into = cfg.uploads_into or (cfg.folders[0] if cfg.folders else "")
    out.add(f"{into}/{cfg.uploads_folder}".strip("/"))
    if _has_table(con, "account"):
        out |= {r[0] for r in con.execute("SELECT upload_folder FROM account WHERE upload_folder IS NOT NULL")}
    out |= scan.own_folders(cfg)
    out |= {r[0] for r in con.execute("SELECT path FROM folder WHERE ignored AND source = 'user'")}
    return out


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


# ---- moving ----------------------------------------------------------------------------------------------------------

def latest(con: sqlite3.Connection) -> sqlite3.Row | None:
    """The latest reorganization; one that stopped showing signs of life (the web app restarted) is set as stopped."""
    row = con.execute("SELECT * FROM reorganize_run ORDER BY id DESC LIMIT 1").fetchone()
    if row is not None and row["state"] in ("moving", "undoing") and _stale(row["beat_at"]):
        con.execute("UPDATE reorganize_run SET state = 'stopped' WHERE id = ? AND state = ?", (row["id"], row["state"]))
        con.commit()
        row = con.execute("SELECT * FROM reorganize_run WHERE id = ?", (row["id"],)).fetchone()
    return row


def busy(con: sqlite3.Connection) -> bool:
    """Whether files are being moved now (or put back): the scan, the copies service and the sidecars wait."""
    row = con.execute("SELECT state, beat_at FROM reorganize_run ORDER BY id DESC LIMIT 1").fetchone()
    return row is not None and row["state"] in ("moving", "undoing") and not _stale(row["beat_at"])


def mark(con: sqlite3.Connection) -> tuple | None:
    """Changes whenever files start or stop being moved: a scan that looked through the folders meanwhile throws
    what it found away (scan.py)."""
    row = con.execute("SELECT id, state FROM reorganize_run ORDER BY id DESC LIMIT 1").fetchone()
    return tuple(row) if row is not None else None


def _stale(beat_at: str | None) -> bool:
    return beat_at is None or (datetime.now() - datetime.fromisoformat(beat_at)).total_seconds() > STALE


def start(con: sqlite3.Connection, cfg: Config, by_type: bool, account_id: int | None,
          connect=None, remove_empty: bool = False, remove_junk: bool = False) -> int:
    """Record a reorganization and start it beside the caller, on a connection of its own (connect()). Returns its
    id. connect None: run it here, to the end (the tests, the command line). remove_empty: take away every empty
    folder when done (take_away_empty), not only those it emptied; remove_junk: delete junk (JUNK_FILES)."""
    if not cfg.folders:
        raise Problem("No folders are watched yet.")
    con.commit()
    con.execute("BEGIN IMMEDIATE")
    try:
        if busy(con):
            raise Problem("Files are being moved already: wait until that is done.")
        now = clock.now()
        run_id = con.execute("""INSERT INTO reorganize_run(started_at, by_type, account_id, state, beat_at,
                                                           remove_empty, remove_junk)
                                VALUES (?, ?, ?, 'moving', ?, ?, ?)""",
                             (now, int(by_type), account_id, now, int(remove_empty), int(remove_junk))).lastrowid
        con.commit()
    except BaseException:
        con.rollback()
        raise
    _stop.clear()
    if connect is None:
        run(con, cfg, run_id)
    else:
        threading.Thread(target=_beside, args=(connect, run, cfg, run_id), daemon=True,
                         name=f"reorganize {run_id}").start()
    return run_id


def undo(con: sqlite3.Connection, cfg: Config, connect=None) -> int:
    """Put back what the latest reorganization moved, beside the caller as start() does. Returns its id."""
    latest(con)  # one that stopped showing signs of life is stopped first
    con.commit()
    con.execute("BEGIN IMMEDIATE")
    try:
        if busy(con):
            raise Problem("Files are being moved now: wait until that is done.")
        row = undoable(con)
        if row is None:
            raise Problem("There is no reorganization to undo.")
        con.execute("UPDATE reorganize_run SET state = 'undoing', moved = 0, problems = NULL, beat_at = ? WHERE id = ?",
                    (clock.now(), row["id"]))
        con.commit()
    except BaseException:
        con.rollback()
        raise
    _stop.clear()
    if connect is None:
        put_back(con, cfg, row["id"])
    else:
        threading.Thread(target=_beside, args=(connect, put_back, cfg, row["id"]), daemon=True,
                         name=f"reorganize {row['id']} undo").start()
    return row["id"]


def undoable(con: sqlite3.Connection) -> sqlite3.Row | None:
    """The reorganization that can be put back: the latest that moved files, when none after it did (one that only
    took away empty folders is passed over) and it isn't put back already. Its "moves" are the files it moved, by
    what it recorded: its own count is what was put back while it is put back, and none when that stopped at once."""
    for row in con.execute("""SELECT r.*, (SELECT count(*) FROM reorganize_move m WHERE m.run_id = r.id) AS moves
                              FROM reorganize_run r ORDER BY r.id DESC"""):
        if row["state"] in ("moving", "undoing", "undone"):
            return None
        if row["moves"]:
            return row
    return None


def stop() -> None:
    """Stop the run in progress after the file it is moving; what is moved stays moved, and can be put back."""
    _stop.set()


def _beside(connect, work, cfg: Config, run_id: int) -> None:
    con = connect()
    try:
        work(con, cfg, run_id)
    finally:
        con.close()


def _beater(con: sqlite3.Connection, run_id: int):
    """A sign of life for a run, given at most every BEAT seconds: while it looks through the folders before moving
    anything, and while it tidies up after, as the moves themselves do for each CHUNK. Without it a big share looks
    like a run that stopped (STALE), and the scan would start meanwhile."""
    last = time.monotonic()

    def beat() -> None:
        nonlocal last
        if time.monotonic() - last < BEAT:
            return
        last = time.monotonic()
        try:
            con.execute("UPDATE reorganize_run SET beat_at = ? WHERE id = ?", (clock.now(), run_id))
            con.commit()
        except sqlite3.OperationalError:  # the catalog busy just now: the next one
            con.rollback()
    return beat


def run(con: sqlite3.Connection, cfg: Config, run_id: int) -> None:
    """Move every file as plan() says, CHUNK a write, each recorded in reorganize_move before it is moved (a run cut
    short half way through a chunk, the web app stopped, leaves no file moved that put_back() can't find); then take
    away the folders it emptied."""
    from lineagebox import cleanup  # moves files as it does to the Removed folders (the scan imports this module)

    try:
        asked = con.execute("SELECT by_type, remove_empty, remove_junk FROM reorganize_run WHERE id = ?",
                            (run_id,)).fetchone()
        beat = _beater(con, run_id)
        p = plan(con, cfg, bool(asked["by_type"]), remove_junk=bool(asked["remove_junk"]), beat=beat)
        con.execute("UPDATE reorganize_run SET planned = ?, beat_at = ? WHERE id = ?",
                    (len(p.moves), clock.now(), run_id))
        con.commit()
        problems, seq, moved, emptied, stayed = [], 0, 0, set(), set()
        folder_ids = {r["path"]: r["id"] for r in con.execute("SELECT id, path FROM folder")}
        for start_at in range(0, len(p.moves), CHUNK):
            if _stop.is_set():
                break
            chunk = p.moves[start_at:start_at + CHUNK]
            # what is about to be moved, kept first: those it doesn't move after all are taken off again below
            con.execute("BEGIN IMMEDIATE")
            try:
                con.executemany("""INSERT INTO reorganize_move(run_id, seq, file_id, old_path, new_path, old_from)
                                   VALUES (?, ?, ?, ?, ?, (SELECT from_folder_id FROM file WHERE id = ?))""",
                                [(run_id, seq + n, m.file_id, m.old, m.new, m.file_id) for n, m in enumerate(chunk)])
                con.commit()
            except BaseException:
                con.rollback()
                raise
            con.execute("BEGIN IMMEDIATE")
            try:
                for n, m in enumerate(chunk):
                    if _stop.is_set():
                        con.execute("DELETE FROM reorganize_move WHERE run_id = ? AND seq >= ? AND seq < ?",
                                    (run_id, seq + n, seq + len(chunk)))
                        break
                    watched = cleanup.watched_folder(cfg, m.old)
                    problem = "the photo it goes with stayed" if m.of in stayed else None
                    # the catalog's row must still be at the path the plan saw
                    if problem is None and m.file_id is not None:
                        row = con.execute("SELECT path, folder_id, from_folder_id, missing FROM file WHERE id = ?",
                                          (m.file_id,)).fetchone()
                        if row is None or row["path"] != m.old or row["missing"]:
                            problem = "it changed while the folders were reorganized"
                        elif con.execute("SELECT 1 FROM file WHERE path = ? AND id != ?",
                                         (m.new, m.file_id)).fetchone():
                            problem = "the catalog has another file at its new path"
                    if problem is None:
                        try:
                            cleanup.move_file(cfg.share_root / m.old, cfg.share_root / m.new,
                                              like=cfg.share_root / watched)
                        except OSError as e:
                            problem = e.strerror or str(e)
                    if problem is not None:
                        stayed.add(m.old)
                        if len(problems) < MOST_PROBLEMS:
                            problems.append(f"{m.old}: {problem}")
                        con.execute("DELETE FROM reorganize_move WHERE run_id = ? AND seq = ?", (run_id, seq + n))
                        continue
                    if m.file_id is not None:
                        new_folder = _folder_id(con, folder_ids, m.new.rpartition("/")[0], p.made)
                        con.execute("""UPDATE file SET path = ?, folder_id = ?,
                                       from_folder_id = coalesce(from_folder_id, ?) WHERE id = ?""",
                                    (m.new, new_folder, row["folder_id"], m.file_id))
                    moved += 1
                    emptied.add(m.old.rpartition("/")[0])
                con.execute("UPDATE reorganize_run SET moved = ?, problems = ?, beat_at = ? WHERE id = ?",
                            (moved, json.dumps(problems) if problems else None, clock.now(), run_id))
            finally:
                con.commit()
            seq += len(chunk)
        deleted = 0 if _stop.is_set() else _delete_junk(cfg, p, problems, emptied, beat)
        # every folder it made, in the catalog's tree as it is on the disk, saying nothing of its own: else the scan
        # would add those under Other files (with no photos) for the AI to be asked what their names mean
        con.execute("BEGIN IMMEDIATE")
        try:
            for path in sorted(p.made, key=len):
                if path not in folder_ids and (cfg.share_root / path).is_dir():
                    _folder_id(con, folder_ids, path, p.made)
        finally:
            con.commit()
        gone = _take_away_empty(cfg, emptied, keep=set(p.made), beat=beat)
        if asked["remove_empty"] and not _stop.is_set():
            gone += take_away_empty(con, cfg, beat=beat)
        state = "stopped" if _stop.is_set() else "done"
        con.execute("UPDATE reorganize_run SET state = ?, moved = ?, problems = ?, finished_at = ?, beat_at = ?, "
                    "emptied = ?, junk = ? WHERE id = ?",
                    (state, moved, json.dumps(problems) if problems else None, clock.now(), clock.now(), gone,
                     deleted, run_id))
        con.commit()
        _after(con)
    except BaseException as e:
        con.rollback()
        _failed(con, run_id, e)
        raise


def _delete_junk(cfg: Config, p: Plan, problems: list[str], emptied: set[str], beat) -> int:
    """Delete what the plan found to be junk; returns how many files went (those in junk folders too). It can't be
    put back, and holds nothing of the family's."""
    deleted = 0
    for path in p.junk:
        beat()
        try:
            (cfg.share_root / path).unlink()
        except FileNotFoundError:
            continue
        except OSError as e:
            if len(problems) < MOST_PROBLEMS:
                problems.append(f"{path}: {e.strerror or e}")
            continue
        deleted += 1
        emptied.add(path.rpartition("/")[0])
    for folder, n in p.junk_dirs.items():
        beat()
        try:
            shutil.rmtree(cfg.share_root / folder)
        except OSError as e:
            if len(problems) < MOST_PROBLEMS:
                problems.append(f"{folder}: {e.strerror or e}")
            continue
        deleted += n
        emptied.add(folder.rpartition("/")[0])
    return deleted


def put_back(con: sqlite3.Connection, cfg: Config, run_id: int) -> None:
    """Move what a reorganization moved back where it was, last first. A file that changed since (moved or removed
    again) stays where it is now."""
    from lineagebox import cleanup  # moves files as it does to the Removed folders (the scan imports this module)

    try:
        moves = con.execute("SELECT * FROM reorganize_move WHERE run_id = ? ORDER BY seq DESC", (run_id,)).fetchall()
        folder_ids = {r["path"]: r["id"] for r in con.execute("SELECT id, path FROM folder")}
        problems, done, emptied = [], 0, set()
        for start_at in range(0, len(moves), CHUNK):
            if _stop.is_set():
                break
            con.execute("BEGIN IMMEDIATE")
            try:
                for m in moves[start_at:start_at + CHUNK]:
                    if _stop.is_set():
                        break
                    problem = None
                    src, dst = cfg.share_root / m["new_path"], cfg.share_root / m["old_path"]
                    like = cfg.share_root / cleanup.watched_folder(cfg, m["old_path"])
                    if m["file_id"] is not None:
                        row = con.execute("SELECT path, missing FROM file WHERE id = ?", (m["file_id"],)).fetchone()
                        if row is not None and row["path"] == m["old_path"]:
                            # back already (an undo stopped half way, then started again), or never written down as
                            # moved (the run was cut short between moving it and that): then only the file goes back
                            if src.exists() and not dst.exists():
                                try:
                                    cleanup.move_file(src, dst, like=like)
                                    done += 1
                                    emptied.add(m["new_path"].rpartition("/")[0])
                                except OSError as e:
                                    if len(problems) < MOST_PROBLEMS:
                                        problems.append(f"{m['new_path']}: {e.strerror or e}")
                            continue
                        if row is None or row["path"] != m["new_path"] or row["missing"]:
                            problem = "it was moved or removed since"
                        elif con.execute("SELECT 1 FROM file WHERE path = ? AND id != ?",
                                         (m["old_path"], m["file_id"])).fetchone():
                            problem = "the catalog has another file at its old path"
                    elif not src.exists() and dst.exists():
                        continue  # back already
                    if problem is None:
                        try:
                            cleanup.move_file(src, dst, like=like)
                        except OSError as e:
                            problem = e.strerror or str(e)
                    if problem is not None:
                        if len(problems) < MOST_PROBLEMS:
                            problems.append(f"{m['new_path']}: {problem}")
                        continue
                    if m["file_id"] is not None:
                        old_folder = _folder_id(con, folder_ids, m["old_path"].rpartition("/")[0], {})
                        con.execute("UPDATE file SET path = ?, folder_id = ?, from_folder_id = ? WHERE id = ?",
                                    (m["old_path"], old_folder, m["old_from"], m["file_id"]))
                    done += 1
                    emptied.add(m["new_path"].rpartition("/")[0])
                con.execute("UPDATE reorganize_run SET moved = ?, problems = ?, beat_at = ? WHERE id = ?",
                            (done, json.dumps(problems) if problems else None, clock.now(), run_id))
            finally:
                con.commit()
        _take_away_empty(cfg, emptied, keep=set())
        stopped = _stop.is_set()
        con.execute("UPDATE reorganize_run SET state = ?, undone_at = ?, beat_at = ? WHERE id = ?",
                    ("stopped" if stopped else "undone", None if stopped else clock.now(), clock.now(), run_id))
        con.commit()
        _after(con)
    except BaseException as e:
        con.rollback()
        _failed(con, run_id, e)
        raise


def _failed(con: sqlite3.Connection, run_id: int, e: BaseException) -> None:
    """Something unexpected stopped it: what was moved is recorded, and it can be put back or started again."""
    try:
        row = con.execute("SELECT problems FROM reorganize_run WHERE id = ?", (run_id,)).fetchone()
        problems = json.loads(row["problems"] or "[]") if row else []
        problems.append(f"stopped: {e}")
        con.execute("UPDATE reorganize_run SET state = 'stopped', problems = ?, beat_at = ? WHERE id = ?",
                    (json.dumps(problems), clock.now(), run_id))
        con.commit()
    except sqlite3.Error:
        con.rollback()


def _after(con: sqlite3.Connection) -> None:
    """The folders' photos changed: lists kept a while are worked out again (Cleanup's, the folder pages')."""
    from lineagebox import activity
    row = con.execute("SELECT * FROM reorganize_run ORDER BY id DESC LIMIT 1").fetchone()
    problems = len(json.loads(row["problems"] or "[]"))
    words = {"done": "moved", "undone": "put back", "stopped": "stopped after"}.get(row["state"], row["state"])
    done = row["state"] != "undone"
    activity.log(con, f"reorganize folders: {words} {row['moved']} files"
                 + (f"; deleted {row['junk']} junk files" if row["junk"] and done else "")
                 + (f"; took away {row['emptied']} empty folders" if row["emptied"] and done else "")
                 + (f"; {problems} could not be moved" if problems else ""))



def _folder_id(con: sqlite3.Connection, ids: dict[str, int], path: str, made: dict[str, str]) -> int:
    """The catalog's folder at path, recorded with those above it if new. One this makes says nothing of its own
    (folder.kind set, so the AI isn't asked what its name means): its photos mean what their old folders meant."""
    if path in ids:
        return ids[path]
    parent, _, name = path.rpartition("/")
    parent_id = _folder_id(con, ids, parent, made) if path else None
    kind = MADE_KIND.get(made.get(path, ""))
    ids[path] = con.execute("INSERT INTO folder(path, parent_id, name, kind) VALUES (?, ?, ?, ?)",
                            (path, parent_id, name, kind)).lastrowid
    return ids[path]


def _take_away_empty(cfg: Config, folders: set[str], keep: set[str], beat=None) -> int:
    """Remove the folders moving files out of left empty, and those above them left so, up to the watched folders.
    Returns how many."""
    stop_at = set(cfg.folders) | keep
    gone = 0
    for folder in sorted(folders, key=lambda f: -f.count("/")):
        if beat:
            beat()
        while folder and folder not in stop_at and any(folder.startswith(w + "/") for w in cfg.folders):
            path = cfg.share_root / folder
            try:
                if not path.is_dir() or os.listdir(path):
                    break
                path.rmdir()
            except OSError:
                break
            gone += 1
            folder = folder.rpartition("/")[0]
    return gone


def take_away_empty(con: sqlite3.Connection, cfg: Config, beat=None) -> int:
    """Remove every folder in the watched folders with nothing in it, or only empty folders (left so by cleanup's
    moves to Removed, or by hand), but for those a reorganization leaves alone (plan()) and one the catalog still has
    a file in (a disk mounted there and away just now looks empty too). Returns how many. beat: as for plan()."""
    alive = _alive(con)
    leave = _left_alone(con, cfg) - {f"{w}/{OTHER}" for w in cfg.folders}  # its folders left empty go too
    gone = 0

    def visit(folder: str, rel: str, device: int) -> bool:
        """Take away what is empty in folder, then folder itself if that leaves it empty; whether it went."""
        nonlocal gone
        if beat:
            beat()
        try:
            with os.scandir(folder) as listing:
                entries = list(listing)
        except OSError:
            return False
        kept = 0
        for e in entries:
            try:
                sub = f"{rel}/{e.name}"
                if not e.is_dir(follow_symlinks=False) or sub in leave or e.name.lower().endswith("-removed") \
                        or scan.is_ignored(e.name, cfg.ignore_dirs) \
                        or os.stat(e.path, follow_symlinks=False).st_dev != device or not visit(e.path, sub, device):
                    kept += 1
            except OSError:
                kept += 1
        if kept or rel in alive or rel in cfg.folders:
            return False
        try:
            os.rmdir(folder)
        except OSError:
            return False
        gone += 1
        return True

    for watched in cfg.folders:
        try:
            device = (cfg.share_root / watched).stat().st_dev
        except OSError:
            continue  # away just now
        visit(str(cfg.share_root / watched), watched, device)
    return gone
