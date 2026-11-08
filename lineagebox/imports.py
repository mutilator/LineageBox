"""Names, keywords, captions, dates and places other apps gave the photos, brought in so that a library already tagged
elsewhere starts out named, not from zero (Settings → Other programs; on for a new catalog, see migration 029).

Where they are read from, each photo once (metadata_read):
- the photo's own XMP and IPTC (xmp.embedded): faces drawn and named in digiKam, Lightroom, Picasa 3.9 or Windows
  Photo Gallery (MWG and Microsoft regions), keywords, the caption, and a date or GPS the camera didn't give;
- an XMP sidecar beside it made by another program (IMG_1234.JPG.xmp or IMG_1234.xmp), never one LineageBox wrote;
- Picasa's .picasa.ini in its folder: faces (rect64 boxes) named through the [Contacts2] list in the same file, or a
  contacts.xml in the folder or one above it, and keywords;
- Google Takeout's JSON beside it (IMG_1234.JPG.json, IMG_1234.JPG.supplemental-metadata.json and the shortened
  names Takeout gives long ones): when it was taken, where, its description and the people Google Photos named.

How each comes in, below anything set here, which is never overwritten:
- a name on a face waits until the photo's faces are found (faces.py), then names the face its box covers, as a name
  given by hand would: someone named here, or set aside, keeps their face (state 'kept'), and recognition's guess
  gives way. A name whose box covers no face found, or that has no box, adds the person to the photo. A name nobody
  in the catalog has adds them; a name two people have is left for someone to pick ('unsure');
- a keyword becomes a tag given by hand, unless it was taken off here; a keyword that is someone's name is that
  person in the photo;
- a caption becomes the photo's caption if it has none (cameras' own words, like "OLYMPUS DIGITAL CAMERA", are not
  captions);
- a date replaces only a date from the file's time or a folder's name (or, from XMP and IPTC, the file's name), as
  taken_at_source 'import'; a date set by hand stays;
- a place goes where the photo has no GPS, as GPS would.

Boxes: apps disagree on whether a box is drawn on the picture as stored or as shown (turned upright by its EXIF
orientation), so both are tried, and the one that covers more of the faces found is taken.

Everything brought in is a row of imported, which Settings → Other programs counts.
"""
import json
import os
import re
import sqlite3
import time
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from lineagebox import comments, people, retry, search, xmp
from lineagebox.catalog import ON_SHARE, tag_name
from lineagebox.config import Config
from lineagebox.faces import STAGE_FACES
from lineagebox.folders import FolderIndex

STAGE = "metadata"
MINUTES = 10           # each pass reads for this long at most: a first pass over a big library takes several
MATCH_IOU = 0.2        # a box another app drew and a face found here are one face when they overlap this much (or
                       # each one's centre is inside the other's box): apps draw boxes from the hairline to the chin,
                       # or just around the eyes and mouth, so the same face's boxes overlap by a third or more
TAKEOUT_MAX = 1 << 20  # bytes: a Takeout JSON is a few hundred; anything bigger is something else
TAKEOUT_NAME = 51      # Takeout shortens a JSON's whole name to this many characters
MOST_EACH = 200        # names, and keywords, taken from one source for one photo: a class photo names 40, and a
                       # packet of thousands is something else, which would fill the people and tags
NOT_NAMES = re.compile(r"^(?:unknown|unknown person|unnamed|no name|noname|person|face|\?+)$", re.IGNORECASE)
TURN = {6: 90, 3: 180, 8: 270}  # EXIF orientation -> the quarter turn clockwise that shows the picture upright
SOURCES = {"xmp": "the photos' own XMP", "iptc": "the photos' own IPTC", "sidecar": "other programs' sidecars",
           "picasa": "Picasa", "takeout": "Google Takeout"}


@dataclass
class Stats:
    read: int = 0
    found: int = 0      # photos something was found for
    named: int = 0      # faces named, and people added to photos
    failed: int = 0
    left: int = 0


# ---- reading ------------------------------------------------------------------------------------------------------

class Reader:
    """Reads what other apps left for photos, folder by folder: each folder's list of files, Picasa file and contacts
    are read once."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.listings: dict[Path, dict[str, str]] = {}
        self.picasas: dict[Path, dict[str, xmp.Found]] = {}
        self.contacts: dict[Path, dict[str, str]] = {}

    def listing(self, folder: Path) -> dict[str, str]:
        """The folder's file names, by their lowercase: a name is looked for whatever case another app gave it."""
        if folder not in self.listings:
            try:
                self.listings[folder] = {e.name.lower(): e.name for e in os.scandir(folder) if e.is_file()}
            except OSError:
                self.listings[folder] = {}
        return self.listings[folder]

    def read(self, rel: str, photo: bool) -> list[tuple[str, xmp.Found, int]]:
        """(source, what it holds, the file's orientation) for one file of a photo, from each place that has
        something."""
        path = self.cfg.source_path(rel)
        out = []
        orientation = 1
        if photo:
            found, orientation = xmp.embedded(path)
            if not found.ours and not found.empty():
                out.append(("xmp", found, orientation))
        names = self.listing(path.parent)
        stem = path.name.rsplit(".", 1)[0]
        for side in dict.fromkeys((f"{path.name}.xmp".lower(), f"{stem}.xmp".lower())):
            if side in names:
                with open(path.parent / names[side], "rb") as f:
                    found = xmp.parse(f.read(xmp.MAX_PACKET + 1))
                if not found.ours and not found.empty():
                    out.append(("sidecar", found, orientation))
        if (found := self.picasa(path.parent).get(path.name.lower())) and not found.empty():
            out.append(("picasa", found, orientation))
        if (found := self.takeout(path)) and not found.empty():
            out.append(("takeout", found, orientation))
        return out

    # Picasa ------------------------------------------------------------------------------------------------------

    def picasa(self, folder: Path) -> dict[str, xmp.Found]:
        if folder not in self.picasas:
            names = self.listing(folder)
            ini = next((names[n] for n in (".picasa.ini", "picasa.ini") if n in names), None)
            self.picasas[folder] = {}
            if ini:
                with open(folder / ini, "rb") as f:
                    self.picasas[folder] = self._picasa(f.read(xmp.MAX_PACKET).decode("utf-8", "replace"), folder)
        return self.picasas[folder]

    def _picasa(self, text: str, folder: Path) -> dict[str, xmp.Found]:
        sections: dict[str, dict[str, str]] = {}
        current = None
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                current = sections.setdefault(line[1:-1].strip().lower(), {})
            elif current is not None and "=" in line:
                key, _, value = line.partition("=")
                current[key.strip().lower()] = value.strip()
        names = {}
        for section in ("contacts", "contacts2"):
            for cid, value in sections.get(section, {}).items():
                if name := " ".join(value.split(";")[0].split()):
                    names[cid] = name
        out = {}
        for name, keys in sections.items():
            if name in ("contacts", "contacts2", "picasa", "encoding"):
                continue
            found = xmp.Found()
            for face in keys.get("faces", "").split(";"):
                m = re.match(r"\s*rect64\(([0-9a-fA-F]{1,16})\)\s*,\s*([0-9a-fA-F]+)", face)
                if not m:
                    continue
                cid = m[2].lower()
                who = names.get(cid) or self.contact_names(folder).get(cid)
                if not who:
                    continue  # ffffffffffffffff: a face Picasa found that nobody named
                v = int(m[1].zfill(16), 16)
                left, top, right, bottom = ((v >> s) & 0xFFFF for s in (48, 32, 16, 0))
                if box := _box(left / 65535, top / 65535, right / 65535, bottom / 65535):
                    found.regions.append((who, *box))
            found.keywords = [k.strip() for k in keys.get("keywords", "").split(",") if k.strip()]
            if (caption := keys.get("caption", "").strip()) and not xmp.CAMERA_WORDS.match(caption):
                found.caption = caption
            if not found.empty():
                out[name] = found
        return out

    def contact_names(self, folder: Path) -> dict[str, str]:
        """Picasa's contacts.xml (id -> name), the nearest one in this folder or a folder above it in the share."""
        if folder in self.contacts:
            return self.contacts[folder]
        root = self.cfg.share_root.resolve()
        found: dict[str, str] = {}
        here = folder
        while True:
            names = self.listing(here)
            if "contacts.xml" in names:
                found = _contacts(here / names["contacts.xml"])
                break
            try:
                if here.resolve() == root or root not in here.resolve().parents:
                    break
            except OSError:
                break
            here = here.parent
        self.contacts[folder] = found
        return found

    # Google Takeout ----------------------------------------------------------------------------------------------

    def takeout(self, path: Path) -> xmp.Found | None:
        names = self.listing(path.parent)
        hit = takeout_json(path.name, names)
        if hit is None:
            return None
        with open(path.parent / hit, "rb") as f:
            raw = f.read(TAKEOUT_MAX + 1)
        if len(raw) > TAKEOUT_MAX:
            return None
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):  # nested too deep to be Takeout's: something else
            return None
        if not isinstance(data, dict) or not ({"photoTakenTime", "creationTime", "geoData"} & data.keys()):
            return None
        found = xmp.Found()
        try:
            stamp = int(str((data.get("photoTakenTime") or {}).get("timestamp") or 0))
            if stamp > 0:  # Google keeps the moment in UTC: shown as this install's local time
                found.taken_at = datetime.fromtimestamp(stamp).isoformat(timespec="seconds")
        except (ValueError, OverflowError, OSError, AttributeError):
            pass
        for key in ("geoData", "geoDataExif"):
            geo = data.get(key) or {}
            try:
                lat, lon = float(geo.get("latitude") or 0), float(geo.get("longitude") or 0)
            except (TypeError, ValueError, AttributeError):
                continue
            if (lat, lon) != (0, 0) and -90 <= lat <= 90 and -180 <= lon <= 180:
                found.gps = (lat, lon)
                break
        if isinstance(data.get("description"), str) and (caption := data["description"].strip()) \
                and not xmp.CAMERA_WORDS.match(caption):
            found.caption = caption
        for p in data.get("people") if isinstance(data.get("people"), list) else []:
            if isinstance(p, dict) and isinstance(p.get("name"), str) and p["name"].strip():
                found.people.append(" ".join(p["name"].split()))
        return found


def takeout_json(name: str, names: dict[str, str]) -> str | None:
    """The Takeout JSON of a photo among a folder's files (lowercase -> as named), or None. Takeout names it after the
    photo (IMG_1234.JPG.json, or since 2024 IMG_1234.JPG.supplemental-metadata.json), shortens the whole name to 51
    characters, puts the number of a second photo of that name after the extension (IMG_1234(1).JPG ->
    IMG_1234.JPG(1).json), and gives an edited copy (IMG_1234-edited.JPG) the original's."""
    low = name.lower()
    stem, dot, ext = low.rpartition(".")
    tries = [low]
    if dot and (m := re.match(r"^(.*)(\(\d+\))$", stem)):  # IMG_1234(1).jpg -> IMG_1234.jpg + (1)
        tries.append((f"{m[1]}.{ext}", m[2]))
    if dot and "-" in stem:  # IMG_1234-edited.jpg (the suffix is in Google's language)
        tries.append(f"{stem.rsplit('-', 1)[0]}.{ext}")
    for t in tries:
        base, number = (t, "") if isinstance(t, str) else t
        for kind in (".supplemental-metadata", ""):
            full = f"{base}{kind}{number}.json"
            if full in names:
                return names[full]
            short = f"{base}{kind}"[:TAKEOUT_NAME - len(number) - 5] + f"{number}.json"
            if short in names:
                return names[short]
    for n in sorted(names):  # shortened some other way: IMG_1234.JPG.supplemental-metad.json
        if n.endswith(".json") and n.startswith(low + ".") and len(n) <= TAKEOUT_NAME:
            return names[n]
    return None


def _contacts(path: Path) -> dict[str, str]:
    try:
        with open(path, "rb") as f:
            raw = f.read(xmp.MAX_PACKET)
        if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
            return {}
        root = ET.fromstring(raw)
    except (OSError, ET.ParseError):
        return {}
    out = {}
    for c in root.iter("contact"):
        if (cid := c.get("id")) and (name := " ".join((c.get("name") or "").split())):
            out[cid.lower()] = name
    return out


def _box(x1, y1, x2, y2):
    x1, y1, x2, y2 = (min(1.0, max(0.0, v)) for v in (x1, y1, x2, y2))
    return (x1, y1, x2, y2) if x2 - x1 > 0.002 and y2 - y1 > 0.002 else None


# ---- bringing it in -----------------------------------------------------------------------------------------------

def _usable_name(name: str) -> str | None:
    # one form of each letter: a Mac writes "é" as e plus an accent (NFD), which would otherwise be someone new
    name = " ".join(unicodedata.normalize("NFC", name or "").split())
    if not name or "@" in name or NOT_NAMES.match(name) or not re.search(r"[^\W\d_]", name):
        return None
    return name[:200]


def bring_in(con: sqlite3.Connection, image_id: int, image, found: list[tuple[str, xmp.Found, int]]) -> list[str]:
    """Record what was found for one photo (see the module's docstring for how each comes in). Returns the sources
    that gave something. Doesn't commit."""
    now = datetime.now().isoformat(timespec="seconds")
    known = {r[0].casefold() for r in con.execute("SELECT name FROM person")}
    names, keywords, used = set(), set(), []
    caption_done = con.execute("SELECT 1 FROM comment WHERE image_id = ? AND kind = 'caption'",
                               (image_id,)).fetchone() is not None
    date_done = place_done = False

    def note(kind, value, source, **more):
        cols = {"image_id": image_id, "kind": kind, "value": value, "source": source, "at": now, **more}
        con.execute(f"INSERT INTO imported({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    tuple(cols.values()))
        if source not in used:
            used.append(source)

    for source, f, orientation in found:
        turn = TURN.get(orientation, 0)
        for name, x1, y1, x2, y2 in f.regions[:MOST_EACH]:
            if (name := _usable_name(name)) and name.casefold() not in names:
                names.add(name.casefold())
                note("name", name, source, state="waiting", x1=x1, y1=y1, x2=x2, y2=y2, turn=turn)
        people_too = [k for k in f.keywords if k.casefold() in known]  # a keyword that is someone's name
        for name in [*f.people, *people_too][:MOST_EACH]:
            if (name := _usable_name(name)) and name.casefold() not in names:
                names.add(name.casefold())
                note("name", name, source, state="waiting")
        for k in f.keywords[:MOST_EACH]:
            tag = tag_name(k)
            if not tag or k.casefold() in known or k.casefold() in names or tag in keywords:
                continue
            keywords.add(tag)
            con.execute("INSERT OR IGNORE INTO tag(name) VALUES (?)", (tag,))
            tag_id = con.execute("SELECT id FROM tag WHERE name = ?", (tag,)).fetchone()[0]
            given = con.execute("""INSERT INTO image_tag(image_id, tag_id, source) VALUES (?, ?, 'user')
                                   ON CONFLICT(image_id, tag_id) DO UPDATE SET source = 'user'
                                   WHERE source NOT IN ('user', 'not')""", (image_id, tag_id)).rowcount
            if given:
                note("tag", tag, source)
        if f.caption and not caption_done:
            # the start of a longer one: a caption here is a memory (comments.MOST), and set_caption refuses more
            comments.set_caption(con, image_id, f.caption[:comments.MOST], None)
            caption_done = True
            note("caption", f.caption[:300], source)
        replaces = ("mtime", "folder") if source == "takeout" else ("mtime", "folder", "filename")
        if f.taken_at and not date_done and image["taken_at_source"] in replaces:
            con.execute("UPDATE image SET taken_at = ?, taken_at_source = 'import' WHERE id = ?",
                        (f.taken_at, image_id))
            date_done = True
            note("date", f.taken_at, source)
        if f.gps and not place_done and image["gps_lat"] is None:
            con.execute("UPDATE image SET gps_lat = ?, gps_lon = ? WHERE id = ?", (f.gps[0], f.gps[1], image_id))
            place_done = True
            note("place", f"{f.gps[0]:.6f},{f.gps[1]:.6f}", source)
    return used


def read_new(con: sqlite3.Connection, cfg: Config, until: float, stop: Callable[[], bool] | None,
             stats: Stats) -> None:
    """Read the photos not read yet, newest first, until `until` (time.monotonic())."""
    reader = Reader(cfg)
    rows = con.execute(f"""SELECT i.id, i.media, i.taken_at_source, i.gps_lat FROM image i
                           WHERE i.error IS NULL AND {ON_SHARE}
                             AND NOT EXISTS (SELECT 1 FROM metadata_read m WHERE m.image_id = i.id)
                             AND {retry.waiting(STAGE)} ORDER BY i.id DESC""").fetchall()
    folders = None
    for n, image in enumerate(rows):
        if time.monotonic() > until or (stop is not None and stop()):
            stats.left = len(rows) - n
            return
        files = [r[0] for r in con.execute("SELECT path FROM file WHERE image_id = ? AND NOT missing ORDER BY path",
                                           (image["id"],))]
        found: list[tuple[str, xmp.Found, int]] = []
        try:
            for k, rel in enumerate(files):
                for item in reader.read(rel, photo=image["media"] == "photo" and k == 0):  # copies: the same bytes
                    found.append(item)
            found.sort(key=lambda item: list(SOURCES).index(item[0]) if item[0] in SOURCES else 99)
            used = bring_in(con, image["id"], image, found) if found else []
        except sqlite3.OperationalError:  # the catalog busy: the stage stops, and goes on from here next pass
            raise
        except Exception as e:  # a file that can't be read now, or one no reader expected: this photo waits
            con.rollback()  # what bring_in had written of it
            why = f"{type(e).__name__}: {e.strerror or e}" if isinstance(e, OSError) else f"{type(e).__name__}: {e}"
            retry.later(con, STAGE, image["id"], why)  # never the same photo stopping every pass, and all after it
            stats.failed += 1
            continue
        con.execute("INSERT OR REPLACE INTO metadata_read(image_id, read_at, found) VALUES (?, ?, ?)",
                    (image["id"], datetime.now().isoformat(timespec="seconds"), ",".join(used) or None))
        retry.done(con, STAGE, [image["id"]])
        if used:
            folders = folders or FolderIndex(con)
            search.index_image(con, image["id"], folders)
            stats.found += 1
        con.commit()
        stats.read += 1


def name_faces(con: sqlite3.Connection, cfg: Config) -> int:
    """Names waiting for their photos' faces to be found: on the faces they cover, or added to the photo. Returns how
    many faces were named and people added."""
    done_faces = "1" if not cfg.faces_enabled else f"(m.stage & {STAGE_FACES} OR m.media != 'photo')"
    ids = [r[0] for r in con.execute(f"""SELECT DISTINCT i.image_id FROM imported i JOIN image m ON m.id = i.image_id
                                         WHERE i.state = 'waiting' AND {done_faces}""")]
    total, folders = 0, None
    for image_id in ids:
        total += _name_photo(con, image_id)
        folders = folders or FolderIndex(con)
        search.index_image(con, image_id, folders)
        con.commit()
    return total


def _name_photo(con: sqlite3.Connection, image_id: int) -> int:
    waiting = con.execute("SELECT * FROM imported WHERE image_id = ? AND state = 'waiting' ORDER BY id",
                          (image_id,)).fetchall()
    faces = con.execute("""SELECT id, x1, y1, x2, y2, person_id, assigned_by FROM face
                           WHERE image_id = ? AND frame IS NULL""", (image_id,)).fetchall()
    boxed = [w for w in waiting if w["x1"] is not None]
    pairs = _match(boxed, faces)
    face_of = {w["id"]: f for w, f in pairs}
    now = datetime.now().isoformat(timespec="seconds")
    done = 0
    for w in waiting:
        pid = _person(con, w["value"], image_id, w["source"], now)
        if pid is None:
            con.execute("UPDATE imported SET state = 'unsure' WHERE id = ?", (w["id"],))
            continue
        face = face_of.get(w["id"])
        if face is not None:
            state = "face"
            if face["assigned_by"] in ("rejected", "not_a_face"):
                state = "kept"
            elif face["assigned_by"] == "user":
                state = "face" if face["person_id"] == pid else "kept"
            elif con.execute("SELECT 1 FROM face_not_person WHERE face_id = ? AND person_id = ?",
                             (face["id"], pid)).fetchone():
                state = "kept"
            elif face["person_id"] != pid or face["assigned_by"] != "user":
                people.name_as(con, [face["id"]], pid)
                done += 1
            con.execute("UPDATE imported SET state = ?, face_id = ?, person_id = ? WHERE id = ?",
                        (state, face["id"], pid, w["id"]))
            continue
        if con.execute("SELECT 1 FROM face WHERE image_id = ? AND person_id = ?", (image_id, pid)).fetchone():
            state = "face"  # already named on a face here
        else:
            done += con.execute("INSERT OR IGNORE INTO image_person(image_id, person_id, added_at) VALUES (?, ?, ?)",
                                (image_id, pid, now)).rowcount
            state = "photo"
        con.execute("UPDATE imported SET state = ?, person_id = ? WHERE id = ?", (state, pid, w["id"]))
    return done


def _person(con: sqlite3.Connection, name: str, image_id: int, source: str, now: str) -> int | None:
    """The one person with this name, or someone new; None when two people have it."""
    try:
        found = people.existing_person_id(con, name)
    except people.Problem:
        return None
    if found is not None:
        return found
    pid = people.person_id(con, name)
    con.execute("""INSERT INTO imported(image_id, kind, value, source, at, person_id)
                   VALUES (?, 'person', ?, ?, ?, ?)""", (image_id, name, source, now, pid))
    return pid


def _turned(box, turn: int):
    """A box on the picture as stored, on the picture turned upright (`turn` degrees clockwise)."""
    def point(x, y):
        return {0: (x, y), 90: (1 - y, x), 180: (1 - x, 1 - y), 270: (y, 1 - x)}[turn]
    (ax, ay), (bx, by) = point(box[0], box[1]), point(box[2], box[3])
    return min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)


def _overlap(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _inside(point, box) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def _match(regions, faces) -> list:
    """Pairs (region, face) that are one face, each face used once, the closest first; of the two ways a box may be
    meant (as drawn, or turned with the picture), the one that pairs more faces."""
    best: list = []
    best_score = (-1, -1.0)
    for turn in dict.fromkeys([0, *(r["turn"] for r in regions)]):
        scored = []
        for r in regions:
            box = _turned((r["x1"], r["y1"], r["x2"], r["y2"]), turn)
            centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            for f in faces:
                fb = (f["x1"], f["y1"], f["x2"], f["y2"])
                iou = _overlap(box, fb)
                if iou >= MATCH_IOU or (_inside(centre, fb) and _inside(((fb[0] + fb[2]) / 2, (fb[1] + fb[3]) / 2),
                                                                       box)):
                    scored.append((iou, r, f))
        scored.sort(key=lambda s: -s[0])
        used_r, used_f, pairs = set(), set(), []
        for iou, r, f in scored:
            if r["id"] in used_r or f["id"] in used_f:
                continue
            used_r.add(r["id"])
            used_f.add(f["id"])
            pairs.append((iou, r, f))
        score = (len(pairs), sum(p[0] for p in pairs))
        if score > best_score:
            best, best_score = [(r, f) for _, r, f in pairs], score
    return best


def run(con: sqlite3.Connection, cfg: Config, stop: Callable[[], bool] | None = None,
        minutes: float = MINUTES) -> Stats:
    """The worker's stage: read photos not read yet, then name the faces of those whose faces are found."""
    stats = Stats()
    if not cfg.import_enabled:
        return stats
    read_new(con, cfg, time.monotonic() + minutes * 60, stop, stats)
    stats.named = name_faces(con, cfg)
    return stats


def summary(con: sqlite3.Connection) -> dict:
    """For Settings → Other programs: what was read and brought in, by kind, and the people it added."""
    read, found = con.execute("SELECT count(*), coalesce(sum(found IS NOT NULL), 0) FROM metadata_read").fetchone()
    kinds = {f"{k}:{s or ''}": n for k, s, n in con.execute(
        "SELECT kind, state, count(*) FROM imported GROUP BY kind, state")}
    by_source = dict(con.execute("SELECT source, count(*) FROM imported WHERE kind != 'person' GROUP BY source"))
    added = con.execute("""SELECT p.id, p.name FROM imported i JOIN person p ON p.id = i.person_id
                           WHERE i.kind = 'person' ORDER BY p.name COLLATE NOCASE LIMIT 60""").fetchall()
    total_added = con.execute("SELECT count(*) FROM imported WHERE kind = 'person'").fetchone()[0]
    count = lambda kind, *states: sum(kinds.get(f"{kind}:{s}", 0) for s in states)  # noqa: E731
    return {"read": read, "found": found, "faces": count("name", "face"), "photos": count("name", "photo"),
            "waiting": count("name", "waiting"), "kept": count("name", "kept"), "unsure": count("name", "unsure"),
            "tags": count("tag", ""), "captions": count("caption", ""), "dates": count("date", ""),
            "places": count("place", ""), "people": total_added, "added": [dict(r) for r in added],
            "sources": {SOURCES.get(k, k): v for k, v in by_source.items()}}
