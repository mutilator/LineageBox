"""Full-text search (SQLite FTS5) over descriptions, tags, people, folder context, dates and what is said in videos.

search_fts.rowid is the image id, so one image's row can be replaced without scanning the index.
"""
import json
import re
import sqlite3
from datetime import datetime

from lineagebox import catalog, dates, family
from lineagebox.folders import FolderIndex

_SPLIT = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])|[\\/_.\-]+")
# bm25 column weights: image_id, caption, tags, people, place_event, folder_text, date_text, speech
_WEIGHTS = "0.0, 3.0, 2.0, 5.0, 3.0, 1.5, 1.0, 1.5"
COMMIT_EVERY = 25  # photos re-indexed per write: each write is short, so the web app never waits long


def folder_words(path: str) -> str:
    """'DCIM/2015/TomsPirateParty' -> 'DCIM 2015 Toms Pirate Party'"""
    return " ".join(_SPLIT.sub(" ", path).split())


def index_image(con: sqlite3.Connection, image_id: int, folders: FolderIndex) -> None:
    img = con.execute("SELECT taken_at, taken_at_source, date_text, date_from, date_to, media FROM image WHERE id = ?",
                      (image_id,)).fetchone()
    a = catalog.analysis_for(con, image_id)
    raw = json.loads(a["raw_json"]) if a else {}
    ctx = folders.context(image_id)
    tags = [*raw.get("tags", []), *raw.get("objects", []), *raw.get("animals", []), *raw.get("activities", []),
            *raw.get("age_groups", []), raw.get("setting") or "", raw.get("photo_type") or ""]
    tags += [raw.get(k) for k in ("season", "time_of_day", "indoor_outdoor") if raw.get(k) not in (None, "unknown")]
    if img["media"] == "video":
        tags.append("video")
    by_hand = con.execute("""SELECT t.name, it.source FROM image_tag it JOIN tag t ON t.id = it.tag_id
                             WHERE it.image_id = ? AND it.source IN ('user', 'not')""", (image_id,)).fetchall()
    if off := {name for name, source in by_hand if source == "not"}:  # tags taken off the photo by hand
        tags = [t for t in tags if t and catalog.tag_name(t) not in off]
    tags += [name for name, source in by_hand if source == "user"]
    speech = con.execute("SELECT text FROM transcript WHERE image_id = ?", (image_id,)).fetchone()
    people = [r[0] for r in con.execute("""SELECT p.name FROM face f JOIN person p ON p.id = f.person_id
                                           WHERE f.image_id = :i AND f.assigned_by IN ('user', 'auto')
                                           UNION SELECT p.name FROM image_person l JOIN person p ON p.id = l.person_id
                                           WHERE l.image_id = :i""", {"i": image_id})]
    date_text = ""
    if img["taken_at_source"] == "user" and img["date_from"]:  # set by hand: every year it may be, and as it was said
        first, last = int(img["date_from"][:4]), int(img["date_to"][:4])
        date_text = " ".join([*map(str, range(first, min(last, first + 40) + 1)), img["date_text"]])
        if img["date_from"][:7] == img["date_to"][:7]:
            dt = datetime.fromisoformat(img["date_from"])
            date_text += f" {dt:%B} {dates.season_of(dt.month, dt.year, dates.south(con))[0]}"
    elif img["taken_at"] and img["taken_at_source"] != "mtime":  # file times say when a copy was made
        dt = datetime.fromisoformat(img["taken_at"])
        date_text = f"{dt.year} {dt:%B} {dates.season_of(dt.month, dt.year, dates.south(con))[0]}"
    con.execute("DELETE FROM search_fts WHERE rowid = ?", (image_id,))
    con.execute("""INSERT INTO search_fts(rowid, image_id, caption, tags, people, place_event, folder_text, date_text,
                                         speech)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (image_id, image_id, raw.get("caption", ""), " ".join(t for t in tags if t), " ".join(people),
                 " ".join([*ctx.places, *ctx.events, raw.get("event") or ""]),
                 " ".join(folder_words(f) for f in ctx.folders), date_text, speech[0] if speech else ""))


def index_group(con: sqlite3.Connection, image_id: int, folders: FolderIndex) -> None:
    """Re-index an image and every near-duplicate that shares its description."""
    for (member,) in con.execute("""SELECT id FROM image WHERE id = :id
                                    OR dup_group_id = (SELECT dup_group_id FROM image WHERE id = :id)""",
                                 {"id": image_id}).fetchall():
        index_image(con, member, folders)


def index_new(con: sqlite3.Connection) -> int:
    """Index images that are not in the index yet (new photos), committing as it goes so the web app never waits."""
    folders = FolderIndex(con)
    ids = [r[0] for r in con.execute(f"""SELECT i.id FROM image i WHERE {catalog.VISIBLE}
                                         AND i.id NOT IN (SELECT rowid FROM search_fts)""")]
    for n, image_id in enumerate(ids, 1):
        index_image(con, image_id, folders)
        if n % COMMIT_EVERY == 0:
            con.commit()
    con.commit()
    return len(ids)


def rebuild(con: sqlite3.Connection) -> int:
    folders = FolderIndex(con)
    con.execute("DELETE FROM search_fts")
    ids = [r[0] for r in con.execute(f"SELECT i.id FROM image i WHERE {catalog.VISIBLE}")]
    for image_id in ids:
        index_image(con, image_id, folders)
    con.commit()
    return len(ids)


def context_ids(con: sqlite3.Connection, ctx: str) -> list[int]:
    """Ordered image ids for a browsing context: 'folder:<id>', 'year:<yyyy>', 'person:<id>', 'tag:<name>',
    'photos:<category>' or 'search:<query>'."""
    kind, _, value = ctx.partition(":")
    if kind == "search":
        return [r["id"] for r in query(con, value)]
    if kind == "tag":
        return [r["id"] for r in catalog.tag_images(con, value)]
    if kind == "photos":
        return [r["id"] for r in catalog.category_images(con, value)]
    if not value.isdecimal():
        return []
    if kind == "person":
        return [r["id"] for r in catalog.person_images(con, int(value))]
    if kind == "folder":
        return [r["id"] for r in catalog.folder_images(con, int(value))]
    if kind == "year":
        return [r["id"] for r in catalog.year_images(con, int(value))]
    return []


def query(con: sqlite3.Connection, text: str, limit: int = 1000) -> list[sqlite3.Row]:
    """Every word must match somewhere (stemmed: 'scouts' finds 'scout'). One result per near-duplicate group.
    Asking for videos puts them first: photos of a video game or a video call say 'video' more often.

    Relationship words find the people they mean as well as the word itself: 'nephews' finds photos of any of the
    nephews of the person marked as "me", 'friend' photos of anyone's friend (see family.py)."""
    g = family.load(con)
    people = family.word_index(con, g)
    terms = family.search_terms(text, set(people))
    if not terms:
        return []
    parts = []
    for key, typed in terms:
        word = _phrase(typed)
        if people.get(key):
            names = " OR ".join(_phrase(g.name(p)) for p in sorted(people[key]))
            word = f"(people : ({names}) OR {word})"
        parts.append(word)
    match = " AND ".join(parts)
    videos_first = "i.media = 'video' DESC, " if {"video", "videos"} & {typed for _, typed in terms} else ""
    return con.execute(f"""
        SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.media, i.duration FROM search_fts s
        JOIN image i ON i.id = s.rowid
        WHERE search_fts MATCH ? AND (i.dup_group_id IS NULL OR i.dup_group_id = i.id) AND {catalog.VISIBLE}
        ORDER BY {videos_first}bm25(search_fts, {_WEIGHTS}), i.taken_at LIMIT ?""", (match, limit)).fetchall()


def people_matching(con: sqlite3.Connection, text: str, limit: int = 48) -> list[int]:
    """The people a search is about, for the top of the results: everyone whose name, other names or groups hold every
    word typed, or whom the words mean ('nephews', 'teachers', a maiden name)."""
    g = family.load(con)
    index = family.word_index(con, g)
    terms = family.search_terms(text, set(index))
    if not terms:
        return []
    texts: dict[int, list[str]] = {p: [r["name"]] for p, r in g.people.items()}
    for pid, name in con.execute("SELECT person_id, name FROM person_name UNION ALL SELECT person_id, name FROM person_group"):
        texts.setdefault(pid, []).append(name)
    out = []
    for pid, names in texts.items():
        hay = " ".join(names).lower()
        if all(typed in hay or pid in index.get(key, ()) for key, typed in terms):
            out.append(pid)
    return sorted(out, key=lambda p: g.name(p).lower())[:limit]


def _phrase(text: str) -> str:
    return '"' + text.lower().replace('"', '""') + '"'
