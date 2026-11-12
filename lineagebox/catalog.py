"""Read-side queries shared by the web UI and the CLI."""
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

# An image is browsable once decoded and while at least one of its files is still on the share, unless it is the clip
# of a Live Photo (live.py): that is shown, played and worked on with its still. ON_SHARE counts clips too, for what
# is done to every video (720p copies) and file.
_PRESENT = "EXISTS (SELECT 1 FROM file v WHERE v.image_id = i.id AND NOT v.missing)"
ON_SHARE = f"i.error IS NULL AND {_PRESENT}"
VISIBLE = f"i.error IS NULL AND i.live_of IS NULL AND {_PRESENT}"


def tag_name(value) -> str:
    """A tag as stored: lowercase, single spaces, no hyphens ("Close-up" -> "close up")."""
    return " ".join(str(value).lower().replace("-", " ").split())[:40]


def escape_like(text: str) -> str:
    """text as it is, inside a LIKE pattern written with ESCAPE '!'."""
    return text.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def under(folder: str) -> str:
    """A LIKE pattern (with ESCAPE '!') for every path below a folder: 'a_b' finds a_b/x.jpg, not axb/x.jpg."""
    return escape_like(folder) + "/%"


@dataclass
class Folder:
    id: int
    path: str
    name: str
    parent_id: int | None
    ignored: bool
    own: int = 0              # distinct images directly in this folder
    total: int = 0            # distinct images in this folder and everything below it
    cover: str | None = None  # sha256 of the earliest image in the subtree
    children: list["Folder"] = field(default_factory=list)


def folder_tree(con: sqlite3.Connection) -> dict[int, Folder]:
    folders = {r["id"]: Folder(r["id"], r["path"], r["name"], r["parent_id"], bool(r["ignored"]))
               for r in con.execute("SELECT id, path, name, parent_id, ignored FROM folder")}
    images: dict[int, set[int]] = defaultdict(set)
    cover: dict[int, str] = {}
    for folder_id, image_id, sha in con.execute("""
            SELECT f.folder_id, i.id, i.sha256 FROM file f JOIN image i ON i.id = f.image_id
            WHERE NOT f.missing AND i.error IS NULL AND i.live_of IS NULL ORDER BY i.taken_at"""):
        images[folder_id].add(image_id)
        cover.setdefault(folder_id, sha)
    for f in folders.values():
        if f.parent_id in folders:
            folders[f.parent_id].children.append(f)
    for f in folders.values():
        f.children.sort(key=lambda c: c.name.lower())

    def fill(f: Folder) -> set[int]:
        subtree = set(images[f.id])
        f.own, f.cover = len(subtree), cover.get(f.id)
        for c in f.children:
            subtree |= fill(c)
            f.cover = f.cover or c.cover
        f.total = len(subtree)
        return subtree

    for f in folders.values():
        if f.parent_id is None:
            fill(f)
    return folders


def root_folder_id(con: sqlite3.Connection) -> int | None:
    row = con.execute("SELECT id FROM folder WHERE parent_id IS NULL").fetchone()
    return row[0] if row else None


def folder_images(con: sqlite3.Connection, folder_id: int) -> list[sqlite3.Row]:
    return con.execute("""
        SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.media, i.duration, min(f.path) AS path
        FROM file f JOIN image i ON i.id = f.image_id
        WHERE f.folder_id = ? AND NOT f.missing AND i.error IS NULL AND i.live_of IS NULL
        GROUP BY i.id ORDER BY i.taken_at, path""", (folder_id,)).fetchall()


def timeline(con: sqlite3.Connection) -> dict[int, dict[int, int]]:
    """{year: {month: image count}}"""
    out: dict[int, dict[int, int]] = defaultdict(dict)
    for y, m, n in con.execute(f"""
            SELECT CAST(substr(i.taken_at, 1, 4) AS INTEGER), CAST(substr(i.taken_at, 6, 2) AS INTEGER), count(*)
            FROM image i WHERE i.taken_at IS NOT NULL AND {VISIBLE} GROUP BY 1, 2 ORDER BY 1, 2"""):
        out[y][m] = n
    return dict(out)


def year_images(con: sqlite3.Connection, year: int) -> list[sqlite3.Row]:
    return con.execute(f"""
        SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.media, i.duration FROM image i
        WHERE i.taken_at >= ? AND i.taken_at < ? AND {VISIBLE}
        ORDER BY i.taken_at, i.id""", (f"{year:04d}", f"{year + 1:04d}")).fetchall()


def image(con: sqlite3.Connection, image_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM image WHERE id = ?", (image_id,)).fetchone()


def image_files(con: sqlite3.Connection, image_id: int) -> list[sqlite3.Row]:
    return con.execute("""SELECT f.id, f.path, f.folder_id, f.size, f.missing, f.removed_at, f.removed_reason FROM file f
                          WHERE f.image_id = ? ORDER BY f.missing, f.path""", (image_id,)).fetchall()


def live_clip(con: sqlite3.Connection, image_id: int) -> dict | None:
    """The clip of a Live Photo (live.py), for its still's page: its image row's id, sha256, duration, width, height
    and playable, and url, where a <video> plays it (the /play route, as for every video). url is None while browsers
    need a 720p copy of it that isn't made yet (a phone's HEVC clip). None when the photo has no clip, or its clip's
    file isn't on the share. On a member's connection, the clip of a photo they see is there with it (access.py)."""
    row = con.execute("""SELECT c.id, c.sha256, c.duration, c.width, c.height, c.playable FROM image c
                         WHERE c.live_of = ? AND c.error IS NULL
                         AND EXISTS (SELECT 1 FROM file v WHERE v.image_id = c.id AND NOT v.missing)
                         ORDER BY c.id LIMIT 1""", (image_id,)).fetchone()
    if row is None:
        return None
    return {**dict(row), "url": f"/play/{row['id']}" if row["playable"] in ("original", "copy") else None}


def video_frames(con: sqlite3.Connection, image_id: int) -> list[sqlite3.Row]:
    return con.execute("SELECT idx, at FROM video_frame WHERE image_id = ? ORDER BY at", (image_id,)).fetchall()


def transcript(con: sqlite3.Connection, image_id: int) -> dict | None:
    row = con.execute("SELECT * FROM transcript WHERE image_id = ?", (image_id,)).fetchone()
    if row is None:
        return None
    return {**dict(row), "segments": json.loads(row["segments"]), "error": json.loads(row["raw"]).get("error")}


def analysis_for(con: sqlite3.Connection, image_id: int) -> sqlite3.Row | None:
    """The newest description of this image, or of any near-duplicate in its group (they share one)."""
    return con.execute("""
        SELECT a.* FROM analysis a JOIN image m ON m.id = a.image_id
        WHERE m.id = :id OR m.dup_group_id = (SELECT dup_group_id FROM image WHERE id = :id)
        ORDER BY m.id = :id DESC, a.created_at DESC LIMIT 1""", {"id": image_id}).fetchone()


def group_members(con: sqlite3.Connection, image_id: int) -> list[sqlite3.Row]:
    """Other near-duplicates of this image (edited copies, burst shots)."""
    return con.execute(f"""
        SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.width, i.height FROM image i
        WHERE i.dup_group_id = (SELECT dup_group_id FROM image WHERE id = :id) AND i.id != :id AND {VISIBLE}
        ORDER BY i.taken_at, i.id""", {"id": image_id}).fetchall()


def people(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Named people with photo counts (photos still in the library: by face, or added by hand) and, as a cover, the
    portrait chosen for them (person.cover_face_id) while that face is still theirs, else their clearest hand-named
    face. Someone only added to photos by hand, with no face named, has the newest of those
    photos instead (cover_photo, its sha256), and cover_face None."""
    return con.execute(f"""
        WITH shown AS (
            SELECT f.person_id, f.image_id, f.assigned_by FROM face f JOIN image i ON i.id = f.image_id
            WHERE f.person_id IS NOT NULL AND {VISIBLE}
            UNION ALL
            SELECT l.person_id, l.image_id, 'hand' FROM image_person l JOIN image i ON i.id = l.image_id WHERE {VISIBLE})
        SELECT p.id, p.name, count(DISTINCT s.image_id) AS photos,
               sum(s.assigned_by = 'user') AS confirmed, sum(s.assigned_by = 'auto') AS recognised,
               (SELECT c.id FROM face c JOIN image i ON i.id = c.image_id WHERE c.person_id = p.id AND {VISIBLE}
                ORDER BY c.id IS (SELECT o.cover_face_id FROM person o WHERE o.id = c.person_id) DESC,
                         c.assigned_by = 'user' DESC, c.quality DESC LIMIT 1) AS cover_face,
               CASE WHEN max(s.assigned_by IS NOT 'hand') = 0 THEN  -- no face: only photos they were added to
                   (SELECT i.sha256 FROM image_person l JOIN image i ON i.id = l.image_id
                    WHERE l.person_id = p.id AND {VISIBLE} ORDER BY i.taken_at DESC, i.id DESC LIMIT 1)
               END AS cover_photo
        FROM person p JOIN shown s ON s.person_id = p.id
        GROUP BY p.id ORDER BY photos DESC""").fetchall()


def person(con: sqlite3.Connection, person_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM person WHERE id = ?", (person_id,)).fetchone()


def person_images(con: sqlite3.Connection, person_id: int) -> list[sqlite3.Row]:
    return con.execute(f"""
        SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.media, i.duration FROM image i
        WHERE i.id IN (SELECT image_id FROM face WHERE person_id = :p UNION SELECT image_id FROM image_person
                       WHERE person_id = :p) AND {VISIBLE} ORDER BY i.taken_at, i.id""", {"p": person_id}).fetchall()


def person_faces(con: sqlite3.Connection, person_id: int, assigned_by: str) -> list[sqlite3.Row]:
    return con.execute(f"""SELECT f.id, f.image_id, f.assign_score, f.assign_lead FROM face f
                           JOIN image i ON i.id = f.image_id
                           WHERE f.person_id = ? AND f.assigned_by = ? AND {VISIBLE}
                           ORDER BY f.assign_score, i.taken_at""", (person_id, assigned_by)).fetchall()


def clusters(con: sqlite3.Connection, offset: int = 0, limit: int = 30) -> tuple[list[dict], int]:
    """Groups of unnamed faces, largest first, each with its clearest faces and then the rest (to check them all)."""
    total = con.execute("""SELECT count(DISTINCT cluster_id) FROM face
                           WHERE cluster_id IS NOT NULL AND person_id IS NULL""").fetchone()[0]
    page = con.execute("""SELECT cluster_id, count(*) FROM face WHERE cluster_id IS NOT NULL AND person_id IS NULL
                          GROUP BY cluster_id ORDER BY count(*) DESC, cluster_id LIMIT ? OFFSET ?""",
                       (limit, offset)).fetchall()
    faces = defaultdict(list)  # in one pass: face.cluster_id has no index, so a query per group reads every face
    for r in con.execute(f"""SELECT cluster_id, id, image_id FROM face WHERE person_id IS NULL
                             AND cluster_id IN ({','.join('?' * len(page))}) ORDER BY quality DESC, id""",
                         [cid for cid, _ in page]):
        faces[r["cluster_id"]].append(r)
    out = []
    for cid, n in page:
        images = list(dict.fromkeys(f["image_id"] for f in faces[cid]))
        out.append({"id": cid, "size": n, "faces": faces[cid][:14], "more": faces[cid][14:], "images": images})
    return out, total


def image_faces(con: sqlite3.Connection, image_id: int) -> list[sqlite3.Row]:
    return con.execute("""SELECT f.id, f.person_id, f.assigned_by, f.assign_score, f.cluster_id, f.det_score,
                                 f.quality, f.frame, f.x1, f.y1, f.x2, f.y2, p.name
                          FROM face f LEFT JOIN person p ON p.id = f.person_id
                          WHERE f.image_id = ? AND (f.assigned_by IS NULL OR f.assigned_by != 'not_a_face'
                                                    OR f.person_id IS NOT NULL)
                          ORDER BY f.x1""", (image_id,)).fetchall()


def face(con: sqlite3.Connection, face_id: int) -> sqlite3.Row | None:
    return con.execute("""SELECT f.*, i.sha256 FROM face f JOIN image i ON i.id = f.image_id
                          WHERE f.id = ?""", (face_id,)).fetchone()


def file(con: sqlite3.Connection, file_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM file WHERE id = ?", (file_id,)).fetchone()


def tags(con: sqlite3.Connection, q: str = "", sort: str = "count", limit: int = 400) -> tuple[list[sqlite3.Row], int]:
    """Tags the AI or someone gave photos still in the library, with how many photos each (a group of similar shots counts once),
    those containing q only. Returns (the first `limit`, how many there are)."""
    pattern = "%" + escape_like(q.lower()) + "%"
    rows = con.execute(f"""
        SELECT t.name, count(DISTINCT it.image_id) AS photos FROM tag t
        JOIN image_tag it ON it.tag_id = t.id AND it.source != 'not' JOIN image i ON i.id = it.image_id
        WHERE t.name LIKE ? ESCAPE '!' AND {VISIBLE}
        GROUP BY t.id ORDER BY {"t.name" if sort == "name" else "photos DESC, t.name"}""", (pattern,)).fetchall()
    return rows[:limit], len(rows)


def tag_images(con: sqlite3.Connection, name: str) -> list[sqlite3.Row]:
    return con.execute(f"""
        SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.media, i.duration FROM image i
        JOIN image_tag it ON it.image_id = i.id AND it.source != 'not' JOIN tag t ON t.id = it.tag_id
        WHERE t.name = ? AND {VISIBLE} GROUP BY i.id ORDER BY i.taken_at, i.id""", (name,)).fetchall()


def related_tags(con: sqlite3.Connection, name: str, limit: int = 30) -> list[sqlite3.Row]:
    """The tags most often given together with this one."""
    return con.execute(f"""
        SELECT t2.name, count(*) AS photos FROM tag t
        JOIN image_tag a ON a.tag_id = t.id AND a.source != 'not' JOIN image i ON i.id = a.image_id
        JOIN image_tag b ON b.image_id = a.image_id AND b.tag_id != a.tag_id AND b.source != 'not'
        JOIN tag t2 ON t2.id = b.tag_id
        WHERE t.name = ? AND {VISIBLE} GROUP BY t2.id ORDER BY photos DESC, t2.name LIMIT ?""",
                       (name, limit)).fetchall()


# Photos the status page counts and links to: category -> (title, SQL condition). "format/<f>" and "dates/<source>"
# take their value from the address.
CATEGORIES = {
    "gps": ("Photos with GPS", "i.gps_lat IS NOT NULL"),
    "videos": ("Videos", "i.media = 'video'"),
    "recordings": ("Sound recordings", "i.media = 'audio'"),
    "format": ("{} files", "i.format = ?"),
    "dates": ("Dated by {}", "i.taken_at_source = ?"),
}
DATE_SOURCES = {"exif": "Camera (EXIF)", "camera": "Video's own date", "filename": "File name", "folder": "Folder name",
                "mtime": "File time", "user": "Set by you", "copy": "A copy's camera date"}
DATE_HELP = {
    "copy": "The camera's date of a near-identical copy, for an edited, resized or scanned copy that lost its own",
    "exif": "The date and time the camera wrote into the photo: the most reliable",
    "camera": "The date the camera or phone wrote into the video",
    "filename": "A date in the file name, like IMG_20140705_101233.jpg",
    "folder": "A date in a folder name, like 2008-08-19, for photos whose own date was missing or a file time",
    "mtime": "The file's own time, often when it was copied rather than taken: these dates may well be wrong",
    "user": "Dates you set on a photo's page, or for ticked photos",
}
# a date another app gave a photo with none of its own (imports.py): digiKam, Picasa, Google Takeout
DATE_SOURCES["import"] = "Another app"
DATE_HELP["import"] = ("A date another photo app gave it (in its XMP or IPTC, or Google Takeout's file beside it), for "
                       "a photo whose own date was missing or a file time")


def category(value: str) -> tuple[str, str, tuple] | None:
    """'gps', 'videos', 'format/nef' or 'dates/mtime' -> (title, SQL condition, its arguments); None if unknown."""
    kind, _, arg = value.partition("/")
    if kind not in CATEGORIES or bool(arg) != ("?" in CATEGORIES[kind][1]):
        return None
    title, where = CATEGORIES[kind]
    shown = arg.upper() if kind == "format" else DATE_SOURCES.get(arg, arg).lower() if kind == "dates" else arg
    return title.format(shown), where, (arg,) if arg else ()


def category_images(con: sqlite3.Connection, value: str) -> list[sqlite3.Row]:
    found = category(value)
    if found is None:
        return []
    _, where, args = found
    return con.execute(f"""
        SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.media, i.duration FROM image i
        WHERE {where} AND {VISIBLE} ORDER BY i.taken_at, i.id""", args).fetchall()


def undecodable(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Images on the share that could not be read, with a file of each and why. Songs, left out on purpose
    (ingest.SONG), are not among them."""
    return con.execute("""
        SELECT i.id, i.format, i.error, min(f.path) AS path FROM image i JOIN file f ON f.image_id = i.id AND NOT f.missing
        WHERE i.error IS NOT NULL AND i.error NOT LIKE 'song:%' GROUP BY i.id ORDER BY path""").fetchall()


def stats(con: sqlite3.Connection, watched: tuple[str, ...] = ()) -> dict:
    """Counts for the status page. Images are those in the library (a file of theirs still on the share), so each
    count matches the photos its link shows. Missing files are split by why, when the watched folders are given."""
    one = lambda sql, *args: con.execute(sql, args).fetchone()[0]
    shown = con.execute(f"""SELECT i.format, i.media, i.taken_at_source, i.gps_lat IS NOT NULL, count(*),
                                   coalesce(sum(i.duration), 0) FROM image i WHERE {VISIBLE} GROUP BY 1, 2, 3, 4""").fetchall()
    total = lambda pick: sum(r[4] for r in shown if pick(r))
    by = lambda col: dict(sorted(((k, total(lambda r: r[col] == k)) for k in {r[col] for r in shown}),
                                 key=lambda kv: -kv[1]))
    in_watched = " OR ".join("path LIKE ? ESCAPE '!'" for _ in watched) or "1"
    like = [under(w) for w in watched]
    return {
        "folders": one("SELECT count(*) FROM folder"),
        "folders_ignored": one("SELECT count(*) FROM folder WHERE ignored"),
        "files": one("SELECT count(*) FROM file"),
        "files_ingested": one("SELECT count(*) FROM file WHERE image_id IS NOT NULL"),
        "files_pending": one("""SELECT count(*) FROM file f JOIN folder d ON d.id = f.folder_id
                                WHERE f.image_id IS NULL AND NOT f.missing AND NOT d.ignored"""),
        "files_ignored": one("SELECT count(*) FROM file f JOIN folder d ON d.id = f.folder_id WHERE d.ignored"),
        "files_missing": one("SELECT count(*) FROM file WHERE missing"),
        "files_removed": one("SELECT count(*) FROM file WHERE missing AND removed_at IS NOT NULL"),
        "files_gone": one(f"SELECT count(*) FROM file WHERE missing AND removed_at IS NULL AND ({in_watched})", *like),
        "files_unwatched": one(f"SELECT count(*) FROM file WHERE missing AND removed_at IS NULL AND NOT ({in_watched})",
                               *like) if watched else 0,
        "files_read_errors": one("SELECT count(*) FROM file WHERE error IS NOT NULL"),
        "images": total(lambda r: True),
        "videos": total(lambda r: r[1] == "video"),
        "video_hours": sum(r[5] for r in shown if r[1] == "video") / 3600,
        "recordings": total(lambda r: r[1] == "audio"),
        "recording_hours": sum(r[5] for r in shown if r[1] == "audio") / 3600,
        "images_undecodable": one("""SELECT count(*) FROM image i WHERE i.error IS NOT NULL AND i.error NOT LIKE 'song:%'
                                     AND EXISTS (SELECT 1 FROM file v WHERE v.image_id = i.id AND NOT v.missing)"""),
        "images_with_gps": total(lambda r: r[3]),
        "folders_parsed": one("SELECT count(*) FROM folder WHERE kind IS NOT NULL"),
        "folders_to_parse": one("SELECT count(*) FROM folder WHERE NOT ignored AND path != ''"),
        "dup_groups": one("SELECT count(DISTINCT dup_group_id) FROM image WHERE dup_group_id IS NOT NULL"),
        "to_describe": one(f"""SELECT count(*) FROM image i WHERE (i.dup_group_id IS NULL OR i.dup_group_id = i.id)
                               AND {VISIBLE}"""),
        "described": one(f"""SELECT count(*) FROM image i WHERE (i.dup_group_id IS NULL OR i.dup_group_id = i.id)
                             AND {VISIBLE} AND (EXISTS (SELECT 1 FROM analysis a WHERE a.image_id = i.id)
                                  OR i.dup_group_id IS NOT NULL AND EXISTS (  -- the group shares one description
                                     SELECT 1 FROM image m JOIN analysis a ON a.image_id = m.id
                                     WHERE m.dup_group_id = i.dup_group_id))"""),
        "faces": one("SELECT count(*) FROM face"),
        "people": one("SELECT count(*) FROM person"),
        "formats": by(0),
        "date_sources": by(2),
    }
