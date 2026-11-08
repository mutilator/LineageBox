"""One file that outlives this install: the family tree, and what the family said about every photo, in formats other
programs read (Settings → Backup → Download everything, or `python -m lineagebox export <file>`).

A zip of:
- family-tree.ged: the family tree as GEDCOM 7.0 (gedcom.py), which genealogy programs import;
- photos.json: every photo and video, by its SHA-256 and its paths from the media root, with what people gave it: its
  date (as said, for a date set by hand), tags given and taken off by hand, names on faces with their boxes, people
  added to it, captions and comments, and what a document records; and the places confirmed on the map, the
  folders described by hand, people's life stories, chosen portraits and adoptions, and the written sources with
  what each is cited for; the albums the family made, in their order with their captions, and whose favourite each
  photo is. docs/export.md describes every field;
- README.txt: which file is what.

Only what can't be worked out again from the photos is in it: no AI descriptions, transcripts or face embeddings
(another program makes its own), and no pictures (the photos stay on the share, found again by their paths or their
SHA-256). The nightly copies (backup.py) are for putting LineageBox back as it was; this is for leaving it.

It is read in one transaction, so it is the catalog of one moment, and only read: the web app and the worker carry on
writing meanwhile (WAL). The rows of each photo come from queries in photo order, taken a photo at a time, so a big
library is never held in memory whole.
"""
import io
import json
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from lineagebox import __version__, gedcom
from lineagebox import sidecars
from lineagebox import stories as recorded  # stories the family recorded (stories.py)
from lineagebox.config import Config

FORMAT, FORMAT_VERSION = "lineagebox-export", 1
TREE, PHOTOS, README = "family-tree.ged", "photos.json", "README.txt"
NAMED_BY = {"user": "hand", "auto": "recognition"}  # face.assigned_by of the names exported
BOX_DIGITS = 4  # a face's box to a ten-thousandth of the photo

ABOUT = """LineageBox export, made {made} by LineageBox {version}

{tree}  The family tree, as GEDCOM 7.0: genealogy programs import it.
{photos}      Every photo and video: its paths from the photo folders' root, its SHA-256, its date, tags given
                 or taken off by hand, names on faces with their boxes, people added to it, captions, comments,
                 what a document records and whose favourite it is; and the family's albums, in their order with
                 their captions. People are given by "id": the "people" list has each one's name,
                 UID and record in {tree} (@I<id>@).

LineageBox's docs/export.md describes every field (format "{format}", version {format_version}).

xmp/             An XMP sidecar for each photo file the family gave names, a caption, tags, a date or a place, at
                 its path (xmp/Photos/2012/IMG_1234.JPG.xmp for Photos/2012/IMG_1234.JPG). Put beside the
                 photos, digiKam, darktable, Lightroom and ExifTool read them.
"""
XMP_FOLDER = "xmp"
ABOUT += """
stories/         The stories the family recorded, as they were recorded (WebM, M4A, MP3...): photos.json lists each
                 one, under its photo, or in "stories" for a person or a life event, with its file's name here.
"""
STORIES_FOLDER = "stories"


def file_name(at: datetime | None = None) -> str:
    return f"lineagebox-export-{(at or datetime.now()):%Y-%m-%d}.zip"


def write(con: sqlite3.Connection, cfg: Config, out: str | Path | BinaryIO) -> dict:
    """Write the export to a file (a path, or a file open for writing). Returns how much of each kind it holds."""
    made = datetime.now().isoformat(timespec="seconds")
    con.commit()
    con.execute("BEGIN")  # one moment's catalog: a read transaction, which holds up nobody's writes
    try:
        version = con.execute("PRAGMA user_version").fetchone()[0]
        tree, _, _ = gedcom.export_parts(con, cfg, "7.0")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.writestr(README, ABOUT.format(made=made, version=__version__, tree=TREE, photos=PHOTOS, format=FORMAT,
                                            format_version=FORMAT_VERSION))
            z.writestr(TREE, tree)
            with z.open(PHOTOS, "w", force_zip64=True) as raw, \
                    io.TextIOWrapper(raw, encoding="utf-8", newline="\n") as text:
                counts = _photos(con, text, {"format": FORMAT, "format_version": FORMAT_VERSION, "made_at": made,
                                             "made_by": f"LineageBox {__version__}", "catalog_version": version})
            # each photo file's XMP sidecar, as sidecars.py writes them beside the photos, under xmp/ by its path:
            # copied beside the photos, other programs read the names, caption and places from them
            counts["sidecars"] = 0
            for name, data in sidecars.everything(con):
                z.writestr(f"{XMP_FOLDER}/{name}", data)
                counts["sidecars"] += 1
            # the recorded stories' sound, stored as it is (it is compressed already)
            for st in recorded.for_export(con):
                if (path := recorded.folder(cfg) / st["file"]).is_file():
                    z.write(path, f"{STORIES_FOLDER}/{st['file']}", compress_type=zipfile.ZIP_STORED)
    finally:
        con.rollback()
    return counts


def _photos(con: sqlite3.Connection, out, head: dict) -> dict:
    """photos.json, written as it is read: the lists that are short first (people, places, folders), then the
    photos one by one."""
    counts = dict.fromkeys(("photos", "people", "named_faces", "tags", "captions", "comments", "documents"), 0)
    people = _people(con)  # with their life stories, portraits and adoptions (the life story's part, below)
    counts["people"] = len(people)
    sources = _sources(con)
    counts["sources"] = len(sources)
    places = [{"name": r["name"], "lat": r["lat"], "lon": r["lon"], "found_as": r["found_as"],
               "confirmed_at": r["confirmed_at"], "by_hand": r["source"] == "user"}
              for r in con.execute("""SELECT name, lat, lon, found_as, source, confirmed_at FROM place
                                      WHERE lat IS NOT NULL AND (confirmed_at IS NOT NULL OR source = 'user')
                                      ORDER BY name""")]
    folders = [_clean({"path": r["path"], "kind": r["kind"], "place": r["place"], "event": r["event"],
                       "year": r["year"], "month": r["month"], "day": r["day"], "hidden": bool(r["hidden"]) or None})
               for r in con.execute("""SELECT path, kind, place, event, year, month, day, hidden FROM folder
                                       WHERE source = 'user' ORDER BY path""")]
    place_of = _folder_places(con, {p["name"] for p in places})
    placed_folders = [dict(r) for r in con.execute("""SELECT d.path, p.lat, p.lon, p.at FROM folder_place p
                                                      JOIN folder d ON d.id = p.folder_id ORDER BY d.path""")]
    told, told_of_photos = [], {}  # recorded stories: a person's (and their life events'), and each photo's
    for st in recorded.for_export(con):
        item = _clean({k: v for k, v in st.items() if k not in ("id", "image_id")})
        if st["image_id"] is None:
            told.append(item)
        else:
            told_of_photos.setdefault(st["image_id"], []).append(item)
    counts["stories"] = len(told) + sum(map(len, told_of_photos.values()))
    corrected = {r[0]: json.loads(r[1]) for r in con.execute("SELECT image_id, edits FROM transcript "
                                                            "WHERE edits IS NOT NULL")}
    kept_albums = _albums(con)  # albums picked by hand (albums.py), and below each photo's hearts (favourites.py)
    counts.update(albums=len(kept_albums), favourites=0)

    out.write(json.dumps(head, ensure_ascii=False)[:-1])  # the head's fields, then the lists, in one object
    for key, items in (("people", people), ("places", places), ("folders", folders),
                       ("placed_folders", placed_folders), ("stories", told), ("sources", sources),
                       ("albums", kept_albums)):
        out.write(f',\n"{key}": ' + json.dumps(items, ensure_ascii=False))
    out.write(',\n"photos": [')
    rows = {name: _ByImage(con.execute(sql)) for name, sql in _QUERIES.items()}
    first = True
    for image in con.execute("""SELECT id, sha256, media, format, width, height, duration, taken_at, taken_at_source,
                                       date_text, date_from, date_to, auto_taken_at, auto_taken_at_source, camera,
                                       camera_taken_at, gps_lat, gps_lon, hidden, error FROM image ORDER BY id"""):
        mine = {name: r.take(image["id"]) for name, r in rows.items()}
        record = _photo(image, mine, place_of)
        if image["id"] in told_of_photos:
            record["stories"] = told_of_photos[image["id"]]
        if edits := corrected.get(image["id"]):  # lines of its transcript people put right ("" took one out)
            record["transcript_corrections"] = [{"at": float(at), "text": text} for at, text in sorted(
                edits.items(), key=lambda e: float(e[0]))]
        counts["photos"] += 1
        counts["named_faces"] += sum(1 for f in record.get("faces", ()) if "person" in f)
        counts["tags"] += len(record.get("tags", ()))
        counts["captions"] += "caption" in record
        counts["comments"] += len(record.get("comments", ()))
        counts["documents"] += "document" in record
        if loved := mine["favourites"]:  # whose favourite it is: each account's, or {"home": true} without accounts
            record["favourite_of"] = [_clean({"email": r["email"], "person": r["by_person"]}) or {"home": True}
                                      for r in loved]
            counts["favourites"] += len(loved)
        out.write(("\n" if first else ",\n") + json.dumps(record, ensure_ascii=False))
        first = False
    out.write("\n]}\n")
    return counts


_QUERIES = {  # each in image order, for _ByImage
    "files": """SELECT image_id, path, folder_id, missing, removed_at, removed_path FROM file
                WHERE image_id IS NOT NULL ORDER BY image_id, path""",
    "tags": """SELECT it.image_id, t.name, it.source FROM image_tag it JOIN tag t ON t.id = it.tag_id
               WHERE it.source IN ('user', 'not') ORDER BY it.image_id, t.name""",
    "faces": """SELECT f.image_id, f.x1, f.y1, f.x2, f.y2, f.frame, v.at AS frame_at, f.person_id, f.assigned_by,
                       (SELECT json_group_array(n.person_id) FROM face_not_person n
                        WHERE n.face_id = f.id) AS not_people
                FROM face f LEFT JOIN video_frame v ON v.image_id = f.image_id AND v.idx = f.frame
                WHERE (f.person_id IS NOT NULL AND f.assigned_by IN ('user', 'auto'))
                   OR EXISTS (SELECT 1 FROM face_not_person n WHERE n.face_id = f.id)
                ORDER BY f.image_id, f.frame, f.x1, f.id""",
    "people": "SELECT image_id, person_id FROM image_person ORDER BY image_id, person_id",
    "document": "SELECT image_id, kind, title FROM document ORDER BY image_id",
    "records": """SELECT c.image_id, c.person_id, e.person_id AS event_person, e.couple_a, e.couple_b, e.tag, e.type,
                         e.date, e.place
                  FROM citation c LEFT JOIN event e ON e.id = c.event_id ORDER BY c.image_id, c.id""",
    "comments": """SELECT c.image_id, c.kind, c.text, c.at, c.edited_at, a.email, a.person_id AS by_person
                   FROM comment c LEFT JOIN account a ON a.id = c.account_id ORDER BY c.image_id, c.at, c.id""",
    "placed": "SELECT image_id, lat, lon, at FROM image_place ORDER BY image_id",  # put on the map by hand
    "favourites": """SELECT f.image_id, a.email, a.person_id AS by_person FROM favourite f
                     LEFT JOIN account a ON a.id = f.account_id ORDER BY f.image_id, f.at, f.id""",
}


def _albums(con: sqlite3.Connection) -> list[dict]:
    """The albums (albums.py), each with who made it (none: the admins'), its photos by SHA-256 in its order with the
    album's caption for each, and its cover."""
    out = []
    for a in con.execute("""SELECT al.*, ac.email, ac.person_id AS by_person, c.sha256 AS cover FROM album al
                            LEFT JOIN account ac ON ac.id = al.account_id LEFT JOIN image c ON c.id = al.cover_id
                            ORDER BY al.id""").fetchall():
        photos = [_clean({"sha256": r["sha256"], "caption": r["caption"]})
                  for r in con.execute("""SELECT i.sha256, p.caption FROM album_photo p JOIN image i ON i.id = p.image_id
                                          WHERE p.album_id = ? ORDER BY p.position, p.added_at""", (a["id"],))]
        out.append(_clean({"name": a["name"], "about": a["about"],
                           "made_by": _clean({"email": a["email"], "person": a["by_person"]}) or None,
                           "created_at": a["created_at"], "photos": photos,
                           "cover": a["cover"] or (photos[0]["sha256"] if photos else None)}))
    return out


class _ByImage:
    """A query's rows in image order, handed out one photo at a time as the photos are gone through in that order."""

    def __init__(self, cursor):
        self.rows = iter(cursor)
        self.next = next(self.rows, None)

    def take(self, image_id: int) -> list:
        while self.next is not None and self.next["image_id"] < image_id:  # of no photo (none, by the foreign keys)
            self.next = next(self.rows, None)
        out = []
        while self.next is not None and self.next["image_id"] == image_id:
            out.append(self.next)
            self.next = next(self.rows, None)
        return out


def _photo(image, rows: dict, place_of: dict) -> dict:
    record = {"sha256": image["sha256"], "media": image["media"], "format": image["format"],
              "files": [f["path"] for f in rows["files"] if not f["missing"]]}
    gone = [_clean({"path": f["path"], "removed_to": f["removed_path"] if f["removed_at"] else None})
            for f in rows["files"] if f["missing"]]
    if gone:
        record["missing_files"] = gone
    if image["taken_at"]:
        date = {"at": image["taken_at"], "source": image["taken_at_source"]}
        if image["taken_at_source"] == "user":
            date.update(as_said=image["date_text"], **{"from": image["date_from"], "to": image["date_to"]})
            if image["auto_taken_at"]:
                date["automatic"] = {"at": image["auto_taken_at"], "source": image["auto_taken_at_source"]}
        if image["camera_taken_at"]:  # its camera's clock was put right (camera_clock.py): what the camera wrote
            date["camera_wrote"] = image["camera_taken_at"]
        record["date"] = _clean(date)
    record.update(_clean({"width": image["width"], "height": image["height"], "duration": image["duration"],
                          "camera": image["camera"]}))
    if image["gps_lat"] is not None and image["gps_lon"] is not None:
        record["gps"] = {"lat": image["gps_lat"], "lon": image["gps_lon"]}
    if rows.get("placed"):  # put on the map by hand, which the map takes over its GPS
        p = rows["placed"][0]
        record["placed"] = {"lat": p["lat"], "lon": p["lon"], "at": p["at"]}
    # the place of the first of its folders that names one (the map takes the first folder's, if it names one)
    folders = sorted(f["folder_id"] for f in rows["files"] if not f["missing"])
    if place := next((place_of[f] for f in folders if f in place_of), None):
        record["place"] = place
    tags = [r["name"] for r in rows["tags"] if r["source"] == "user"]
    taken_off = [r["name"] for r in rows["tags"] if r["source"] == "not"]
    if tags:
        record["tags"] = tags
    if taken_off:
        record["tags_taken_off"] = taken_off
    faces = []
    for f in rows["faces"]:
        face = {"box": [round(v, BOX_DIGITS) for v in (f["x1"], f["y1"], f["x2"], f["y2"])]}
        if f["frame"] is not None:
            face["frame_seconds"] = f["frame_at"]
        if f["person_id"] is not None and f["assigned_by"] in NAMED_BY:
            face.update(person=f["person_id"], named_by=NAMED_BY[f["assigned_by"]])
        if not_people := sorted(json.loads(f["not_people"] or "[]")):
            face["not_people"] = not_people
        faces.append(face)
    if faces:
        record["faces"] = faces
    if rows["people"]:
        record["people"] = [r["person_id"] for r in rows["people"]]
    if rows["document"]:
        d = rows["document"][0]
        record["document"] = {"kind": d["kind"], "title": d["title"],
                              "records": [_record_of(r) for r in rows["records"]]}
    for c in rows["comments"]:
        said = _clean({"text": c["text"], "at": c["at"], "edited_at": c["edited_at"],
                       "by": _clean({"email": c["email"], "person": c["by_person"]}) or None})
        if c["kind"] == "caption":
            record["caption"] = said
        else:
            record.setdefault("comments", []).append(said)
    if image["hidden"]:
        record["hidden"] = True
    if image["error"]:
        record["unreadable"] = True
    return record


def _record_of(r) -> dict:
    """What a document is a record of: a person, or one of a person's (or a couple's) life events."""
    if r["person_id"] is not None:
        return {"person": r["person_id"]}
    event = _clean({"tag": r["tag"], "type": r["type"], "date": r["date"], "place": r["place"]})
    if r["event_person"] is not None:
        event["person"] = r["event_person"]
    elif r["couple_a"] is not None:
        event["couple"] = [r["couple_a"], r["couple_b"]]
    return {"event": event}


def _folder_places(con: sqlite3.Connection, places: set[str]) -> dict[int, str]:
    """folder id -> the place exported (confirmed on the map, or put there by hand) that the folder, or the nearest
    folder above it, names: where its photos were taken, as the map shows them."""
    folders = {r[0]: (r[1], (r[2] or "").strip()) for r in con.execute("SELECT id, parent_id, place FROM folder")}
    found: dict[int, str | None] = {}

    def place(fid):
        path = []
        while fid is not None and fid not in found and fid not in path:  # up the tree: folders can be deep
            path.append(fid)
            parent, name = folders.get(fid, (None, ""))
            if name in places:
                found[fid] = name
                break
            fid = parent
        answer = found.get(fid) if fid is not None else None
        for f in path:
            found.setdefault(f, answer)
        return found.get(path[0]) if path else answer

    for fid in folders:
        place(fid)
    return {fid: name for fid, name in found.items() if name}


def _clean(d: dict) -> dict:
    """Without the fields that have no value, so each photo's record holds only what it has."""
    return {k: v for k, v in d.items() if v is not None and v != "" and v != [] and v != {}}


# ---- people's life stories, portraits and adoptions, and written sources (the person page's life story) -----------

def _people(con: sqlite3.Connection) -> list[dict]:
    """Everyone, as the "people" list: name, UID and GEDCOM record, with what the family wrote and chose for them
    that GEDCOM carries less plainly: their life story, the portrait chosen (its photo and box), the parents they
    were adopted or fostered by, and the files another tree linked to them that aren't in the library."""
    box = lambda r: [round(r[k], BOX_DIGITS) for k in ("x1", "y1", "x2", "y2")]  # noqa: E731
    portraits = {r["person_id"]: {"photo": r["sha256"], "box": box(r)}
                 for r in con.execute("""SELECT p.id AS person_id, i.sha256, f.x1, f.y1, f.x2, f.y2 FROM person p
                                         JOIN face f ON f.id = p.cover_face_id AND f.person_id = p.id
                                         JOIN image i ON i.id = f.image_id""")}
    raised: dict[int, list] = {}
    for child, parent, how in con.execute("SELECT child_id, parent_id, pedi FROM parent WHERE pedi != 'birth' "
                                          "ORDER BY child_id, parent_id"):
        raised.setdefault(child, []).append({"person": parent, "as": how})
    files: dict[int, list] = {}
    for m in con.execute("SELECT person_id, file, title FROM tree_media WHERE person_id IS NOT NULL ORDER BY id"):
        files.setdefault(m["person_id"], []).append(_clean({"file": m["file"], "title": m["title"]}))
    return [{"id": r["id"], "name": r["name"], "uid": r["uid"], "gedcom": f"@I{r['id']}@",
             **_clean({"life_story": r["biography"], "notes": r["notes"], "portrait": portraits.get(r["id"]),
                       "parents_not_by_birth": raised.get(r["id"]), "files_elsewhere": files.get(r["id"])})}
            for r in con.execute("SELECT id, name, uid, biography, notes FROM person ORDER BY id")]


def _sources(con: sqlite3.Connection) -> list[dict]:
    """Written sources (books, websites, records) and what each is cited for: a person, or one of their (or a
    couple's) life events, with where in the source."""
    cited: dict[int, list] = {}
    for r in con.execute("""SELECT c.source_id, c.page, c.person_id, e.person_id AS event_person, e.couple_a,
                                   e.couple_b, e.tag, e.type, e.date, e.place
                            FROM source_citation c LEFT JOIN event e ON e.id = c.event_id
                            ORDER BY c.source_id, c.id"""):
        cited.setdefault(r["source_id"], []).append(_clean({**_record_of(r), "page": r["page"]}))
    return [_clean({"title": r["title"], "author": r["author"], "publication": r["publication"], "note": r["note"],
                    "uid": r["uid"], "gedcom": f"@T{r['id']}@", "cited_for": cited.get(r["id"])})
            for r in con.execute("SELECT * FROM source ORDER BY id")]
