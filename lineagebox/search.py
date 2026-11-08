"""Full-text search (SQLite FTS5) over descriptions, tags, people, folder context, dates, what is said in videos, the
words the AI read off photos, the text of documents, and the captions and comments the family writes; with filters
(people together, years, a place, photos or videos, faces to name, no date) that work with words or without them.

search_fts.rowid is the image id, so one image's row can be replaced without scanning the index. Search shows one
photo of each group of near-duplicates, so a photo's row carries the family's words and the documents' text of every
copy in its group: a caption written on any of them finds the one shown.
"""
import difflib
import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime
from urllib.parse import parse_qs, urlencode

from lineagebox import catalog, dates, family
from lineagebox.faces import usable_faces_sql
from lineagebox.folders import NO_CONTEXT_KINDS, FolderIndex

_SPLIT = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])|[\\/_.\-]+")
# bm25 column weights: image_id, caption, tags, people, place_event, folder_text, date_text, speech, comments,
# seen_text, document. What the family wrote (comments) counts above the AI's caption: they were there.
_WEIGHTS = "0.0, 3.0, 2.0, 5.0, 3.0, 1.5, 1.0, 1.5, 4.0, 2.0, 1.0"
COMMIT_EVERY = 25  # photos re-indexed per write: each write is short, so the web app never waits long
RESULTS = 5000     # results a search shows at most; the photo page's ←/→ walk the same ones
PARTIAL = 3        # letters from which the last word typed also finds the words it starts ("birthd": birthday)
MOST_PEOPLE = 10   # people a search can ask for together

MEDIA = {"photos": "photo", "videos": "video", "recordings": "audio"}
SORTS = ("best", "newest", "oldest")


def folder_words(path: str) -> str:
    """'DCIM/2015/TomsPirateParty' -> 'DCIM 2015 Toms Pirate Party'"""
    return " ".join(_SPLIT.sub(" ", path).split())


# ---- the index -----------------------------------------------------------------------------------------------------

_GROUP = """SELECT id FROM image WHERE id = :i OR dup_group_id = (SELECT dup_group_id FROM image WHERE id = :i)"""


def index_image(con: sqlite3.Connection, image_id: int, folders: FolderIndex) -> None:
    img = con.execute("SELECT taken_at, taken_at_source, date_text, date_from, date_to, media FROM image WHERE id = ?",
                      (image_id,)).fetchone()
    con.execute("DELETE FROM search_stale WHERE image_id = ?", (image_id,))
    if img is None:  # gone from the catalog since it was asked for
        con.execute("DELETE FROM search_fts WHERE rowid = ?", (image_id,))
        return
    a = catalog.analysis_for(con, image_id)
    raw = json.loads(a["raw_json"]) if a else {}
    ctx = folders.context(image_id)
    tags = [*raw.get("tags", []), *raw.get("objects", []), *raw.get("animals", []), *raw.get("activities", []),
            *raw.get("age_groups", []), raw.get("setting") or "", raw.get("photo_type") or ""]
    tags += [raw.get(k) for k in ("season", "time_of_day", "indoor_outdoor") if raw.get(k) not in (None, "unknown")]
    if img["media"] == "video":
        tags.append("video")
    elif img["media"] == "audio":  # a sound recording (video.py): found as one, and by what is said in it
        tags += ["recording", "sound recording", "audio"]
    if renamed := _aliases(con):  # tags renamed, merged or taken off every photo, as they are now (tagging.rename)
        tags = [renamed.get(catalog.tag_name(t), t) if t and catalog.tag_name(t) in renamed else t for t in tags]
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
    # the caption first, then the comments as they were written, of every copy in the group
    comments = [r[0] for r in con.execute(f"""SELECT text FROM comment WHERE image_id IN ({_GROUP})
                                             ORDER BY kind = 'caption' DESC, at, id""", {"i": image_id})]
    # then the stories recorded about it (stories.py): their titles, and what is said in them
    comments += [r[0] for r in con.execute(f"""SELECT trim(coalesce(title, '') || ' ' || coalesce(words, heard, ''))
                                              FROM story WHERE image_id IN ({_GROUP}) AND removed_at IS NULL
                                              ORDER BY at, id""", {"i": image_id}) if r[0]]
    # a document's kind and title, and its text as the AI copied it out (or as typed) to read it into the tree
    document = [f"{r[0]} {r[1]}" for r in con.execute(f"SELECT kind, title FROM document WHERE image_id IN ({_GROUP})",
                                                      {"i": image_id})]
    document += [r[0] for r in con.execute(f"""SELECT CASE WHEN state = 'ready' THEN title || ' ' ELSE '' END || text
                                              FROM family_reading WHERE image_id IN ({_GROUP}) ORDER BY id""",
                                           {"i": image_id})]
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
                                         speech, comments, seen_text, document)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (image_id, image_id, raw.get("caption", ""), " ".join(t for t in tags if t), " ".join(people),
                 " ".join([*ctx.places, *ctx.events, raw.get("event") or ""]),
                 " ".join(folder_words(f) for f in ctx.folders), date_text, speech[0] if speech else "",
                 "\n".join(comments), raw.get("text_visible") or "", "\n".join(document)))


def _aliases(con: sqlite3.Connection) -> dict[str, str | None]:
    from lineagebox import tagging  # it needs this module first
    return tagging.aliases(con)


def index_group(con: sqlite3.Connection, image_id: int, folders: FolderIndex) -> None:
    """Re-index an image and every near-duplicate that shares its description (and its family's words)."""
    for (member,) in con.execute("""SELECT id FROM image WHERE id = :id
                                    OR dup_group_id = (SELECT dup_group_id FROM image WHERE id = :id)""",
                                 {"id": image_id}).fetchall():
        index_image(con, member, folders)
    if not con.execute("SELECT 1 FROM image WHERE id = ?", (image_id,)).fetchone():
        con.execute("DELETE FROM search_stale WHERE image_id = ?", (image_id,))


def index_new(con: sqlite3.Connection) -> int:
    """Index images that are not in the index yet (new photos, or all of them after the index is made again), and
    those whose captions, comments or documents changed (search_stale, which the catalog's triggers fill: migration
    023), committing as it goes so the web app never waits."""
    folders = FolderIndex(con)
    ids = [r[0] for r in con.execute(f"""SELECT i.id FROM image i WHERE {catalog.VISIBLE}
                                         AND i.id NOT IN (SELECT rowid FROM search_fts)""")]
    for n, image_id in enumerate(ids, 1):
        index_image(con, image_id, folders)
        if n % COMMIT_EVERY == 0:
            con.commit()
    stale = [r[0] for r in con.execute("SELECT image_id FROM search_stale ORDER BY image_id")]
    for n, image_id in enumerate(stale, 1):
        index_group(con, image_id, folders)
        if n % COMMIT_EVERY == 0:
            con.commit()
    con.commit()
    return len(ids) + len(stale)


def rebuild(con: sqlite3.Connection) -> int:
    folders = FolderIndex(con)
    con.execute("DELETE FROM search_fts")
    ids = [r[0] for r in con.execute(f"SELECT i.id FROM image i WHERE {catalog.VISIBLE}")]
    for image_id in ids:
        index_image(con, image_id, folders)
    con.execute("DELETE FROM search_stale")
    con.commit()
    return len(ids)


# ---- what a search asks for ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Filters:
    """What a search is narrowed to besides its words, each a parameter of the search page's address (params)."""
    people: tuple[int, ...] = ()  # people=12,34: all of them are in the photo
    year_from: int | None = None  # from=2010
    year_to: int | None = None    # to=2014
    place: str = ""               # place=Riverside Zoo: a place folder names give
    media: str = ""               # media=photos or media=videos
    unnamed: bool = False         # unnamed=1: with a face nobody has named yet
    undated: bool = False         # undated=1: without a date of its own (none, or only the file's time)
    sort: str = "best"            # sort=newest or sort=oldest; the best match first otherwise

    @property
    def narrowed(self) -> bool:
        """Whether any filter is on (the order is not one)."""
        return bool(self.people or self.year_from or self.year_to or self.place or self.media or self.unnamed
                    or self.undated)

    def params(self, text: str = "") -> dict[str, str]:
        """The search page's address parameters for these words and filters, in a fixed order."""
        out = {"q": text} if text else {}
        if self.people:
            out["people"] = ",".join(map(str, self.people))
        if self.year_from:
            out["from"] = str(self.year_from)
        if self.year_to:
            out["to"] = str(self.year_to)
        if self.place:
            out["place"] = self.place
        if self.media:
            out["media"] = self.media
        if self.unnamed:
            out["unnamed"] = "1"
        if self.undated:
            out["undated"] = "1"
        if self.sort != "best":
            out["sort"] = self.sort
        return out

    @classmethod
    def from_params(cls, params) -> "Filters":
        """From the address's parameters (anything with .get), leaving out what makes no sense."""
        def year(name):
            value = (params.get(name) or "").strip()
            return int(value) if value.isdecimal() and 1800 <= int(value) <= 2200 else None

        people = []
        for part in (params.get("people") or "").split(","):
            if part.strip().isdecimal() and int(part) not in people:
                people.append(int(part))
        first, last = year("from"), year("to")
        if first and last and first > last:
            first, last = last, first
        media = params.get("media") or ""
        sort = params.get("sort") or "best"
        return cls(people=tuple(people[:MOST_PEOPLE]), year_from=first, year_to=last,
                   place=" ".join((params.get("place") or "").split())[:200],
                   media=media if media in MEDIA else "", unnamed=_on(params.get("unnamed")),
                   undated=_on(params.get("undated")), sort=sort if sort in SORTS else "best")


def _on(value) -> bool:
    return (value or "").lower() in ("1", "true", "on", "yes")


def context(text: str, filters: "Filters | None" = None) -> str:
    """The browsing context of a search, for the photo page's ←/→ (context_ids): 'search:<words>' for words alone
    (as before filters, so older links still work), else 'search:?<the search page's parameters>'."""
    f = filters or Filters()
    if not f.narrowed and f.sort == "best" and not text.startswith("?"):
        return f"search:{text}"
    return "search:?" + urlencode(f.params(text))


def from_context(value: str) -> tuple[str, Filters]:
    """The words and filters of a search's browsing context (the part after 'search:')."""
    if not value.startswith("?"):
        return value, Filters()
    params = {k: v[-1] for k, v in parse_qs(value[1:]).items()}
    return params.get("q", ""), Filters.from_params(params)


def checked(con: sqlite3.Connection, f: Filters) -> Filters:
    """The filters with only the people this connection has: on a member's, those they see. Someone they can't
    see is left out, as if not asked for, so a search never tells which of their photos a stranger is in."""
    if not f.people:
        return f
    known = {r[0] for r in con.execute("SELECT id FROM person WHERE id IN (SELECT value FROM json_each(?))",
                                       (json.dumps(list(f.people)),))}
    return replace(f, people=tuple(p for p in f.people if p in known))


# ---- searching -----------------------------------------------------------------------------------------------------

def context_ids(con: sqlite3.Connection, ctx: str, meaning=None) -> list[int]:
    """Ordered image ids for a browsing context: 'folder:<id>', 'year:<yyyy>', 'person:<id>', 'tag:<name>',
    'photos:<category>' or 'search:<words>' / 'search:?<parameters>' (context()). meaning: as for query()."""
    kind, _, value = ctx.partition(":")
    if kind == "search":  # the same results, in the same order, as the search page shows
        text, filters = from_context(value)
        return [r["id"] for r in query(con, text, RESULTS, filters, meaning=meaning)]
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
    if kind == "album":  # in the album's own order (albums.py), of the photos the connection sees
        from lineagebox import albums
        return albums.photo_ids(con, int(value))
    return []


@dataclass
class _Words:
    """The family's words for people (family.word_index) on one connection, worked out once for several searches."""
    g: family.Graph
    index: dict[str, set[int]]

    @classmethod
    def of(cls, con: sqlite3.Connection) -> "_Words":
        g = family.load(con)
        return cls(g, family.word_index(con, g))


def terms(con: sqlite3.Connection, text: str, words: _Words | None = None) -> list[tuple[str, str]]:
    """The words of a search as family.search_terms reads them: (relationship key, as typed)."""
    words = words or _Words.of(con)
    return family.search_terms(text, set(words.index))


def _match(con: sqlite3.Connection, text: str, words: _Words | None = None) -> tuple[str | None, list]:
    """The FTS5 MATCH for the words (None for none), and the words as read. Every word must match somewhere; the
    last one, typed partly, also finds the words it starts ("birthd": birthday), but a whole word counts more."""
    words = words or _Words.of(con)
    found = terms(con, text, words)
    parts = []
    for n, (key, typed) in enumerate(found):
        word = _phrase(typed)
        if n == len(found) - 1 and len(typed) >= PARTIAL and re.search(r"[^\W\d_]", typed):
            word = f"({word} OR {word}*)"
        if words.index.get(key):
            names = " OR ".join(_phrase(words.g.name(p)) for p in sorted(words.index[key]))
            word = f"(people : ({names}) OR {word})"
        parts.append(word)
    return " AND ".join(parts) or None, found


# a photo one of the people is in: on any copy in its group (search shows one of each), by a face or added by hand
_WITH_PERSON = """i.id IN (SELECT coalesce(m.dup_group_id, m.id) FROM face f JOIN image m ON m.id = f.image_id
                           WHERE f.person_id = ? AND f.assigned_by IN ('user', 'auto')
                           UNION SELECT coalesce(m.dup_group_id, m.id) FROM image_person l
                           JOIN image m ON m.id = l.image_id WHERE l.person_id = ?)"""
# a face in the photo good enough to name, which nobody has named (or set aside)
_UNNAMED = f"""EXISTS (SELECT 1 FROM face f WHERE f.image_id = i.id AND f.person_id IS NULL AND f.assigned_by IS NULL
                       AND {usable_faces_sql()})"""
# no date of its own: none, or only the file's time (when a copy was made)
_UNDATED = "(i.taken_at IS NULL OR i.taken_at_source = 'mtime')"


def _select(con: sqlite3.Connection, text: str, f: Filters, words: _Words | None = None):
    """(FROM and WHERE, their arguments, ORDER BY) for a search, or None when it asks for nothing."""
    f = checked(con, f)
    match, found = _match(con, text, words)
    if f.place and re.search(r"\w", f.place):
        place = f"place_event : {_phrase(f.place)}"
        match = f"{match} AND {place}" if match else place
    if match is None and not f.narrowed:
        return None
    where, args = ["(i.dup_group_id IS NULL OR i.dup_group_id = i.id)", catalog.VISIBLE], []
    source = "image i"
    if match:
        source = "search_fts s JOIN image i ON i.id = s.rowid"
        where.insert(0, "search_fts MATCH ?")
        args.append(match)
    for pid in f.people:
        where.append(_WITH_PERSON)
        args += [pid, pid]
    if f.year_from or f.year_to:
        start, end = f"{f.year_from or 1:04d}", f"{(f.year_to or 9998) + 1:04d}"
        # a date set by hand as a span ("the 1950s") is in every year of it, as searching for one of them finds it
        where.append("""CASE WHEN i.taken_at_source = 'user' AND i.date_from IS NOT NULL
                             THEN i.date_to >= ? AND i.date_from < ?
                             ELSE i.taken_at >= ? AND i.taken_at < ? AND i.taken_at_source != 'mtime' END""")
        args += [start, end, start, end]
    if f.media:
        where.append("i.media = ?")
        args.append(MEDIA[f.media])
    if f.unnamed:
        where.append(_UNNAMED)
    if f.undated:
        where.append(_UNDATED)
    if f.sort == "newest":
        order = "i.taken_at IS NULL, i.taken_at DESC, i.id DESC"
    elif f.sort == "oldest" or not match:
        order = "i.taken_at IS NULL, i.taken_at, i.id"
    else:  # asking for videos puts them first: photos of a video game or a video call say 'video' more often
        videos_first = "i.media = 'video' DESC, " if {"video", "videos"} & {typed for _, typed in found} else ""
        order = f"{videos_first}bm25(search_fts, {_WEIGHTS}), i.taken_at"
    return f"FROM {source} WHERE {' AND '.join(where)}", args, order


def query(con: sqlite3.Connection, text: str, limit: int = RESULTS, filters: Filters | None = None,
          words: _Words | None = None, meaning=None) -> list[sqlite3.Row]:
    """Every word must match somewhere (stemmed: 'scouts' finds 'scout'; the last word may be the start of one), and
    every filter must hold. One result per near-duplicate group. Filters work without words: two people's photos
    together, a year's videos. Sorted by the best match (then the oldest first), or newest or oldest first.

    Relationship words find the people they mean as well as the word itself: 'nephews' finds photos of any of the
    nephews of the person marked as "me", 'friend' photos of anyone's friend (see family.py).

    meaning: what reads the words' meaning (embeddings.asker), to find also the photos whose descriptions mean what
    the words say ("kids sledding": children on a snowy hill), blended in (_with_meaning). None, or Ollama away: the
    words alone, as before."""
    f = filters or Filters()
    found = _select(con, text, f, words)
    rows = []
    if found is not None:
        sql, args, order = found
        rows = con.execute(f"""SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.media, i.duration {sql}
                               ORDER BY {order} LIMIT ?""", (*args, limit)).fetchall()
    if meaning is not None and text.strip():
        rows = _with_meaning(con, rows, text, f, words or _Words.of(con), meaning, limit)
    return rows


# ---- search by meaning ------------------------------------------------------------------------------------------------

BLEND = 10  # how far down the lists a rank still counts (reciprocal rank fusion: 1 / (BLEND + rank) from each list)


def _with_meaning(con: sqlite3.Connection, rows: list, text: str, f: Filters, words: _Words, meaning,
                  limit: int) -> list:
    """The word matches with the photos the words mean blended in. Words that name someone (a name, a relationship
    word), and years, are not about what a photo shows: a photo found by meaning must still match them, as the
    filters (and the viewer's own photos: con) must hold. The rest of the words is what is read for its meaning. Best
    match first: each photo counts by its place in both lists (reciprocal rank fusion), so one found both ways comes
    first, and the closest by meaning mix with the best word matches; or newest or oldest first, as asked."""
    from lineagebox import embeddings  # numpy and the Ollama client, only once someone searches by meaning

    names = set()
    for (name,) in con.execute("SELECT name FROM person UNION SELECT name FROM person_name"):
        names.update(fold(w) for w in re.findall(r"\w+", name))
    must, about = [], []
    for key, typed in terms(con, text, words):
        (must if words.index.get(key) or fold(typed) in names or typed.isdecimal() else about).append(typed)
    q = meaning(" ".join(about)) if about else None
    near = embeddings.nearest(con, q) if q is not None else []
    if not near:
        return rows
    found = _select(con, " ".join(must), f, words)
    if found is None:  # no names, no filters: any photo search shows
        sql, args = f"FROM image i WHERE (i.dup_group_id IS NULL OR i.dup_group_id = i.id) AND {catalog.VISIBLE}", []
    else:
        sql, args, _ = found
    meant = {r["id"]: r for r in con.execute(
        f"""SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.media, i.duration {sql}
            AND i.id IN (SELECT value FROM json_each(?))""", (*args, json.dumps([i for i, _ in near])))}
    if not meant:
        return rows
    by_words = {r["id"]: n for n, r in enumerate(rows)}
    by_meaning = {i: n for n, i in enumerate(i for i, _ in near if i in meant)}
    every = {r["id"]: r for r in rows} | meant
    if f.sort in ("newest", "oldest"):  # as _select orders them: by date, those without one last
        dated = sorted((r for r in every.values() if r["taken_at"]), key=lambda r: (r["taken_at"], r["id"]),
                       reverse=f.sort == "newest")
        return (dated + sorted((r for r in every.values() if not r["taken_at"]), key=lambda r: r["id"]))[:limit]
    score = {i: (1 / (BLEND + by_words[i]) if i in by_words else 0) + (1 / (BLEND + by_meaning[i]) if i in by_meaning
                                                                      else 0) for i in every}
    videos_first = bool({"video", "videos"} & {fold(t) for t in about})
    order = sorted(every, key=lambda i: (videos_first and every[i]["media"] != "video", -score[i],
                                         by_words.get(i, len(rows)), by_meaning.get(i, len(near))))
    return [every[i] for i in order[:limit]]


def count(con: sqlite3.Connection, text: str, filters: Filters | None = None, words: _Words | None = None) -> int:
    """How many results a search has (up to RESULTS, as the page shows)."""
    found = _select(con, text, filters or Filters(), words)
    if found is None:
        return 0
    sql, args, _ = found
    return con.execute(f"SELECT count(*) FROM (SELECT i.id {sql} LIMIT ?)", (*args, RESULTS)).fetchone()[0]


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


def ideas(con: sqlite3.Connection, text: str, filters: Filters, names, most: int = 6) -> list[dict]:
    """What to try when a search finds nothing: a word spelt like a name the catalog has (a tag, a person, a place:
    names, from what the viewer sees), or the search without one of its words. [{'label', 'q', 'count'}], each
    finding something with the same filters. (Leaving out a filter is the search page's: it names them.)"""
    words = _Words.of(con)
    typed = [t for _, t in terms(con, text, words)]
    vocabulary = sorted({w for n in names for w in re.findall(r"[^\W\d_]{3,}", n.lower())})
    out, asked = [], 0
    for i, word in enumerate(typed[:6]):
        if len(word) < 3 or " " in word or count(con, word, None, words):
            continue  # it finds something by itself: the words together are what find nothing
        for close in difflib.get_close_matches(word, vocabulary, n=2, cutoff=0.75):
            new = " ".join([*typed[:i], close, *typed[i + 1:]])
            asked += 1
            if n := count(con, new, filters, words):
                out.append({"label": f"“{close}” instead of “{word}”", "q": new, "count": n})
    if len(typed) > 1:
        for i, word in enumerate(typed[:6]):
            new = " ".join([*typed[:i], *typed[i + 1:]])
            if n := count(con, new, filters, words):
                out.append({"label": f"Without “{word}”", "q": new, "count": n})
    return out[:most]


def _phrase(text: str) -> str:
    return '"' + text.lower().replace('"', '""') + '"'


# ---- suggestions as a search is typed ------------------------------------------------------------------------------

def fold(text: str) -> str:
    """Lowercase, without accents: 'Zoë' is found by 'zoe' (as app.js folds names)."""
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c)).lower()


def family_words(con: sqlite3.Connection) -> list[dict]:
    """The words for people a search understands: what they are to the person marked as "me" ("cousins",
    "grandmother"), other relationships ("friends") and groups ("teachers"), each with how many people it means:
    [{'word', 'key', 'people'}]. Each is read back as search_terms reads it and kept only when it finds them all, so
    a label that is several words to the search ("wife's great-aunt") isn't offered."""
    words = _Words.of(con)
    known = set(words.index)
    labels: dict[tuple[str, bool], set[int]] = defaultdict(set)  # (label, whether it is a group's name) -> people
    ego = family.me(con)
    if ego is not None:
        for pid, kin in family.relations_to(words.g, ego).items():
            if pid != ego:
                labels[kin.label, False].add(pid)
    for p, _, kind in words.g.relations:
        labels[kind, False].add(p)
    for p, name in con.execute("SELECT person_id, name FROM person_group"):
        labels[name.lower(), True].add(p)
    out = []
    for (label, group), pids in labels.items():
        word = label if group or len(pids) == 1 else family.plural(label)  # "teachers" is a group's name already
        read = family.search_terms(word, known)
        if len(read) == 1 and pids <= words.index.get(read[0][0], set()):
            out.append({"word": word, "key": read[0][0], "people": len(pids)})
    return sorted(out, key=lambda w: (-w["people"], w["word"]))


def places(con: sqlite3.Connection) -> list[tuple[str, int]]:
    """The places folder names give (those the search's place filter finds), with how many folders say each, the
    most first. On a member's connection, those of the folders their photos are in."""
    kinds = ", ".join(f"'{k}'" for k in sorted(NO_CONTEXT_KINDS))
    return [tuple(r) for r in con.execute(f"""
        SELECT min(trim(place)), count(*) FROM folder WHERE place IS NOT NULL AND trim(place) != '' AND NOT ignored
          AND coalesce(kind, '') NOT IN ({kinds}) GROUP BY lower(trim(place)) ORDER BY 2 DESC, 1""")]


def years(con: sqlite3.Connection) -> dict[int, int]:
    """Photos per year, as the year filter finds them (one of each group, and not by a file's time), for the year
    filter and suggestions."""
    return dict(con.execute(f"""SELECT CAST(substr(i.taken_at, 1, 4) AS INTEGER), count(*) FROM image i
                                WHERE i.taken_at IS NOT NULL AND i.taken_at_source != 'mtime'
                                  AND (i.dup_group_id IS NULL OR i.dup_group_id = i.id) AND {catalog.VISIBLE}
                                GROUP BY 1"""))


SUGGEST = {"person": 5, "family": 3, "place": 3, "tag": 5, "year": 4}  # suggestions of each kind at most


def suggest(text: str, people: list[dict], words: list[dict], place_names: list[tuple[str, int]], tags,
            year_counts: dict[int, int], chosen=()) -> list[dict]:
    """What to offer as a search is typed, for the end of what is typed: people (by any part of their names or
    other names, as the name boxes do), family words, places, tags and years. Each says how many of the last words
    typed it takes the place of ('take'), what is left of the search before them ('keep'), and either the words it
    puts in their place ('words') or the filter it adds ('add'). The lists are the viewer's own (people:
    web.common._name_choices; tags: catalog.tags), so nothing is offered they can't see. chosen: people already
    asked for."""
    spans = list(re.finditer(r"\w+", text))
    typed = [fold(m.group()) for m in spans]
    if not typed:
        return []
    out = []

    def offer(kind: str, label: str, about: str, take: int, **what) -> None:
        out.append({"kind": kind, "label": label, "about": about, "take": take,
                    "keep": text[:spans[-take].start()].strip(), **what})

    photos = lambda n: f"{n:,} photo{'' if n == 1 else 's'}"  # noqa: E731
    for k in range(min(3, len(typed)), 0, -1):  # the most words at the end that name someone: "emma mil"
        if found := _people_for(typed[-k:], people, chosen):
            for p in found[:SUGGEST["person"]]:
                about = " · ".join(filter(None, [p.get("about"), photos(p["photos"]) if p["photos"] else
                                                 "no photos yet"]))
                offer("person", p["name"], about, k, face=p.get("face"), add={"people": str(p["id"])})
            break
    last = typed[-1]
    if len(last) >= 2 and not last.isdecimal():
        key = next(iter(family.search_terms(last)), (last,))[0]  # "grandma" means grandmother
        for w in [w for w in words if w["word"].startswith(last) or w["key"] == key][:SUGGEST["family"]]:
            offer("family", w["word"], f"{w['people']} {'person' if w['people'] == 1 else 'people'}", 1,
                  words=w["word"])
    for kind, names, longest in (("place", place_names, 3), ("tag", [(t["name"], t["photos"]) for t in tags], 2)):
        for k in range(min(longest, len(typed)), 0, -1):
            if found := _names_for(" ".join(typed[-k:]), names):
                for name, n in found[:SUGGEST[kind]]:
                    if kind == "place":
                        offer("place", name, "place", k, add={"place": name})
                    else:
                        offer("tag", name, photos(n), k, words=name)
                break
    if last.isdecimal() and 2 <= len(last) <= 4:
        for y in sorted((y for y in year_counts if str(y).startswith(last)), reverse=True)[:SUGGEST["year"]]:
            offer("year", str(y), photos(year_counts[y]), 1, add={"from": str(y), "to": str(y)})
    return out


def _people_for(tail: list[str], people: list[dict], chosen) -> list[dict]:
    """Those whose names or other names have a word starting with each of these: whose name starts with them all
    first, then whose name holds them, then by another name; the most photos first."""
    if len("".join(tail)) < 2:
        return []
    found = []
    for p in people:
        if p["id"] in chosen:
            continue
        own = fold(p["name"]).split()
        also = [w for w in re.split(r"[\s·]+", fold(p.get("also") or "")) if w]
        if all(any(w.startswith(t) for w in own + also) for t in tail):
            in_name = all(any(w.startswith(t) for w in own) for t in tail)
            found.append((0 if fold(p["name"]).startswith(" ".join(tail)) else 1 if in_name else 2,
                          -p["photos"], p["name"].lower(), p))
    return [f[-1] for f in sorted(found, key=lambda f: f[:3])]


def _names_for(start: str, names: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """The names (places, tags) with a word that starts as typed: those that start so first, then the most used."""
    if len(start) < 2 or start.isdecimal():
        return []
    pattern = re.compile(rf"(?:^|\W){re.escape(start)}")
    found = [(not fold(name).startswith(start), -n, name, n) for name, n in names if pattern.search(fold(name))]
    return [(name, n) for _, _, name, n in sorted(found)]
