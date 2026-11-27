"""Historical papers: letters, awards, certificates, property papers, clippings (PLAN.md §10.16).

A paper is a document row (documents.py: its kind and title, what it is a record of) on the image of its first page:
a scan or photo of it, or the file it is (image.media 'document': papers.py). Its pages are paper_page rows in reading
order, each with its words: the file's own, the AI's copy (paper_reading.py) or a person's. A paper scanned page by
page is one paper once its pages are put together: its later pages point at the first (image.page_of) and leave every
list (catalog.VISIBLE), as a Live Photo's clip does.

Papers are seen as photos are (access.py), with their pages. They stay out of Home, memories, the Timeline, year
pages, the map, photo frames and tours (catalog.NOT_PAPER) and have the Historical page instead
(web/routes/historical.py), whose lists are worked out here: by collection (the folder a paper came from, which
Reorganize folders leaves where it is), by kind, and by decade written.

What a person says wins: details and words given by hand (document.source, paper_page.text_source 'user') are never
written over by the worker, and a photo a person said is no paper (paper_check by 'user') is never asked about again.
"""
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass

from lineagebox import clock, dates, documents, errors, events, papers
from lineagebox.catalog import ORIGIN, VISIBLE

PAGES_AT_ONCE = 200  # scans put together as one paper at most at once (a long letter, a booklet)
BESIDE = 30          # scans after a paper in its folder offered as its next pages
KIND_FOR_FORMAT = {"eml": "Email"}
TITLE_LIMIT = 200
FIELD_LIMIT = 200
TEXT_LIMIT = 100_000  # characters a person may type for one page
SUMMARY_LIMIT = 1000
UNDATED = "undated"


class Problem(errors.Problem):
    """A change to a paper that can't be made; the message is for the person who asked."""


# ---- making papers --------------------------------------------------------------------------------------------------

def add_file_paper(con: sqlite3.Connection, image_id: int, read: papers.Read) -> None:
    """A paper that is a file of its own, as ingest read it: its document row and its pages. Part of ingest's write."""
    span = _span_of(con, read.written)
    con.execute("""INSERT OR IGNORE INTO document(image_id, kind, title, created_at, source, written, written_from,
                                                  written_to, sender, recipient, unreadable)
                   VALUES (?, ?, ?, ?, 'auto', ?, ?, ?, ?, ?, ?)""",
                (image_id, KIND_FOR_FORMAT.get(read.format, "Other"), (read.title or "Untitled")[:TITLE_LIMIT],
                 clock.now(), _shown_date(read.written), *span, _short(read.sender), _short(read.to),
                 read.unreadable))
    now = clock.now()
    pages = read.pages or [None]  # a paper whose words can't be read still has its one page, to download
    con.executemany("""INSERT OR IGNORE INTO paper_page(paper_id, n, text, text_source, read_at, blank)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    ((image_id, n, text, "file" if text is not None else None, now if text is not None else None,
                      int(text is not None and not text.strip() and read.format != "pdf"))
                     for n, text in enumerate(pages, 1)))


def make(con: sqlite3.Connection, image_id: int, kind: str, title: str, source: str = "auto",
         text: str | None = None, **reading) -> None:
    """A scan or photo becomes a paper of one page (more are put with it by add_pages). text: its words as the AI
    read them (reading: model, version, unsure, blank, language). Doesn't commit."""
    con.execute("""INSERT INTO document(image_id, kind, title, created_at, source, language) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(image_id) DO NOTHING""",
                (image_id, kind[:60] or "Other", (title or kind)[:TITLE_LIMIT], clock.now(), source,
                 reading.get("language")))
    con.execute("""INSERT INTO paper_page(paper_id, n, image_id, text, text_source, unsure, blank, model, version,
                                          read_at)
                   VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(paper_id, n) DO NOTHING""",
                (image_id, image_id, text, "ai" if text is not None else None,
                 json.dumps(reading["unsure"]) if reading.get("unsure") else None, int(bool(reading.get("blank"))),
                 reading.get("model"), reading.get("version"), clock.now() if text is not None else None))
    con.execute("UPDATE image SET kept_at = coalesce(kept_at, ?) WHERE id = ?", (clock.now(), image_id))


def mark(con: sqlite3.Connection, image_id: int, kind: str, title: str) -> None:
    """A person says a photo is a paper, of this kind (the image page's "Is this a document?"). Doesn't commit."""
    if con.execute("SELECT page_of FROM image WHERE id = ?", (image_id,)).fetchone()[0] is not None:
        raise Problem("This is a page of another document: change that document instead.")
    documents.save(con, image_id, kind, title)
    con.execute("UPDATE document SET source = 'user' WHERE image_id = ?", (image_id,))
    con.execute("""INSERT INTO paper_page(paper_id, n, image_id) SELECT ?, 1, ?
                   WHERE NOT EXISTS (SELECT 1 FROM paper_page WHERE paper_id = ?)""", (image_id, image_id, image_id))
    _checked(con, image_id, True)


def not_a_paper(con: sqlite3.Connection, image_id: int) -> None:
    """A person says a photo is no paper: it stops being one, its pages are photos again, and the AI never asks.
    A paper that is a file of its own (a PDF) is always one: it can be hidden or removed as any file. Doesn't commit."""
    row = con.execute("SELECT media, page_of FROM image WHERE id = ?", (image_id,)).fetchone()
    if row is None:
        raise Problem("No such photo.")
    if row["media"] == "document":
        raise Problem("A document file is always a document. Hide it or move it to Removed instead.")
    if row["page_of"] is not None:
        raise Problem("This is a page of another document: take it out of that document first.")
    con.execute("UPDATE image SET page_of = NULL WHERE page_of = ?", (image_id,))
    documents.remove(con, image_id)  # its pages go with it (paper_page refers to it)
    _checked(con, image_id, False)


def _checked(con: sqlite3.Connection, image_id: int, is_paper: bool) -> None:
    con.execute("""INSERT INTO paper_check(image_id, is_paper, by, at) VALUES (?, ?, 'user', ?)
                   ON CONFLICT(image_id) DO UPDATE SET is_paper = excluded.is_paper, by = 'user', what = NULL,
                                                       model = NULL, version = NULL, at = excluded.at""",
                (image_id, int(is_paper), clock.now()))


# ---- one paper ------------------------------------------------------------------------------------------------------

def get(con: sqlite3.Connection, paper_id: int) -> sqlite3.Row | None:
    return con.execute("""SELECT d.*, i.sha256, i.media, i.format, i.taken_at, i.taken_at_source, i.date_text,
                                 i.hidden, i.width, i.height
                          FROM document d JOIN image i ON i.id = d.image_id WHERE d.image_id = ?""",
                       (paper_id,)).fetchone()


def paper_of(con: sqlite3.Connection, image_id: int) -> int | None:
    """The paper an image is a page of (itself, when it is a paper's first page), or None."""
    row = con.execute("""SELECT coalesce(i.page_of, i.id) FROM image i
                         WHERE i.id = ? AND EXISTS (SELECT 1 FROM document d WHERE d.image_id = coalesce(i.page_of, i.id))""",
                      (image_id,)).fetchone()
    return row[0] if row else None


def pages(con: sqlite3.Connection, paper_id: int) -> list[dict]:
    """A paper's pages, in order: each with n, its words and whose they are, the lines the AI wasn't sure of, and its
    picture: a scanned page's own image (image_id, sha256), or the paper file's page n (sha256 None)."""
    rows = con.execute("""SELECT p.*, i.sha256 FROM paper_page p LEFT JOIN image i ON i.id = p.image_id
                          WHERE p.paper_id = ? ORDER BY p.n""", (paper_id,)).fetchall()
    return [{**dict(r), "unsure": json.loads(r["unsure"]) if r["unsure"] else []} for r in rows]


def collection(con: sqlite3.Connection, paper_id: int) -> sqlite3.Row | None:
    """The folder a paper came from (where its first file is, or was before a reorganization moved it)."""
    return con.execute(f"""SELECT d.id, d.path, d.name FROM file f JOIN folder d ON d.id = {ORIGIN}
                           WHERE f.image_id = ? AND NOT f.missing ORDER BY f.id LIMIT 1""", (paper_id,)).fetchone()


def beside(con: sqlite3.Connection, paper_id: int, most: int = BESIDE) -> list[sqlite3.Row]:
    """Scans after a paper in its folder, by name, to put with it as its next pages: photos that aren't a page of a
    paper already (a paper of one page may be: its words come along)."""
    first = con.execute("SELECT f.folder_id, f.path FROM file f WHERE f.image_id = ? AND NOT f.missing ORDER BY f.id "
                        "LIMIT 1", (paper_id,)).fetchone()
    if first is None:
        return []
    return con.execute(f"""
        SELECT i.id, i.sha256, min(f.path) AS path, (SELECT title FROM document d WHERE d.image_id = i.id) AS title
        FROM file f JOIN image i ON i.id = f.image_id
        WHERE f.folder_id = ? AND f.path > ? AND NOT f.missing AND i.media = 'photo' AND i.id != ? AND {VISIBLE}
          AND (SELECT count(*) FROM paper_page p WHERE p.paper_id = i.id) <= 1
        GROUP BY i.id ORDER BY path LIMIT ?""", (first["folder_id"], first["path"], paper_id, most)).fetchall()


def add_pages(con: sqlite3.Connection, paper_id: int, image_ids: list[int]) -> int:
    """Put scans after a paper's last page, in the order given: each one's own page (its words, if a paper of its own
    already read them; that paper goes, and what it was a record of passes to this one), or a new page to be read.
    Returns how many were put in. Doesn't commit."""
    paper = get(con, paper_id)
    if paper is None:
        raise Problem("No such document.")
    if paper["media"] != "photo":
        raise Problem("The pages of a document file are the file's own.")
    ids = [i for n, i in enumerate(image_ids) if i not in image_ids[:n] and i != paper_id][:PAGES_AT_ONCE]
    last = con.execute("SELECT coalesce(max(n), 0) FROM paper_page WHERE paper_id = ?", (paper_id,)).fetchone()[0]
    added = 0
    for image_id in ids:
        img = con.execute("SELECT media, page_of, live_of, error FROM image WHERE id = ?", (image_id,)).fetchone()
        if img is None or img["media"] != "photo" or img["live_of"] is not None or img["error"] is not None:
            raise Problem("Only scans and photos can be pages of a document.")
        if img["page_of"] is not None:
            raise Problem("One of them is a page of another document already.")
        own = con.execute("SELECT * FROM paper_page WHERE paper_id = ? ORDER BY n", (image_id,)).fetchall()
        if len(own) > 1:
            raise Problem("One of them is a document of several pages: add its pages one by one.")
        last += 1
        if own:  # a paper of one page: its words come along, and it is a paper no more
            r = own[0]
            con.execute("""INSERT INTO paper_page(paper_id, n, image_id, text, text_source, unsure, blank, model,
                                                  version, read_at, edited_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (paper_id, last, image_id, r["text"], r["text_source"], r["unsure"], r["blank"], r["model"],
                         r["version"], r["read_at"], r["edited_at"]))
            con.execute("""UPDATE OR IGNORE citation SET image_id = ? WHERE image_id = ?""", (paper_id, image_id))
            con.execute("DELETE FROM document WHERE image_id = ?", (image_id,))
        else:
            con.execute("INSERT INTO paper_page(paper_id, n, image_id) VALUES (?, ?, ?)", (paper_id, last, image_id))
        con.execute("UPDATE image SET page_of = ?, kept_at = coalesce(kept_at, ?) WHERE id = ?",
                    (paper_id, clock.now(), image_id))
        _checked(con, image_id, True)
        added += 1
    return added


def take_out(con: sqlite3.Connection, paper_id: int, n: int) -> int:
    """A later page leaves its paper and is a scan of its own again: a paper of one page, with its words, of the same
    kind. Returns its image's id. Doesn't commit."""
    page = con.execute("SELECT * FROM paper_page WHERE paper_id = ? AND n = ?", (paper_id, n)).fetchone()
    if page is None:
        raise Problem("No such page.")
    if page["image_id"] is None:
        raise Problem("The pages of a document file are the file's own.")
    if n == 1:
        raise Problem("The first page is the document itself: take out the others, or say it isn't a document.")
    paper = get(con, paper_id)
    image_id = page["image_id"]
    con.execute("DELETE FROM paper_page WHERE paper_id = ? AND n = ?", (paper_id, n))
    con.execute("UPDATE image SET page_of = NULL WHERE id = ?", (image_id,))
    con.execute("""INSERT INTO document(image_id, kind, title, created_at, source) VALUES (?, ?, ?, ?, ?)""",
                (image_id, paper["kind"], f"{paper['title']} (page {n})"[:TITLE_LIMIT], clock.now(), paper["source"]))
    con.execute("""INSERT INTO paper_page(paper_id, n, image_id, text, text_source, unsure, blank, model, version,
                                          read_at, edited_at)
                   VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (image_id, image_id, page["text"], page["text_source"], page["unsure"], page["blank"],
                 page["model"], page["version"], page["read_at"], page["edited_at"]))
    _renumber(con, paper_id)
    return image_id


def move_page(con: sqlite3.Connection, paper_id: int, n: int, to: int) -> None:
    """Move a later page to another place among the later pages (the first stays: it is the paper). Doesn't commit."""
    count = con.execute("SELECT count(*) FROM paper_page WHERE paper_id = ?", (paper_id,)).fetchone()[0]
    if not (2 <= n <= count and 2 <= to <= count):
        raise Problem("The first page is the document itself: only the pages after it can move.")
    if con.execute("SELECT image_id FROM paper_page WHERE paper_id = ? AND n = ?", (paper_id, n)).fetchone()[0] is None:
        raise Problem("The pages of a document file are the file's own.")
    order = [r[0] for r in con.execute("SELECT n FROM paper_page WHERE paper_id = ? ORDER BY n", (paper_id,))]
    order.insert(to - 1, order.pop(n - 1))
    _renumber(con, paper_id, order)


def _renumber(con: sqlite3.Connection, paper_id: int, order: list[int] | None = None) -> None:
    """Number a paper's pages 1, 2, ... in this order of their numbers now (by default, as they are)."""
    order = order or [r[0] for r in con.execute("SELECT n FROM paper_page WHERE paper_id = ? ORDER BY n", (paper_id,))]
    con.execute("UPDATE paper_page SET n = -n WHERE paper_id = ?", (paper_id,))  # out of each other's way first
    for new, old in enumerate(order, 1):
        con.execute("UPDATE paper_page SET n = ? WHERE paper_id = ? AND n = ?", (new, paper_id, -old))


def set_text(con: sqlite3.Connection, paper_id: int, n: int, text: str) -> None:
    """A person's words for a page (typed, or the AI's corrected): kept as theirs, never read again. Doesn't
    commit."""
    if con.execute("SELECT 1 FROM paper_page WHERE paper_id = ? AND n = ?", (paper_id, n)).fetchone() is None:
        raise Problem("No such page.")
    text = papers.tidy(str(text or ""))[:TEXT_LIMIT]
    con.execute("""UPDATE paper_page SET text = ?, text_source = 'user', unsure = NULL, blank = 0, edited_at = ?
                   WHERE paper_id = ? AND n = ?""", (text, clock.now(), paper_id, n))


def read_again(con: sqlite3.Connection, paper_id: int, n: int) -> None:
    """Forget the words of a page so that the worker reads it again (after a better model is chosen, say): never a
    person's words, nor a file's own. Doesn't commit."""
    row = con.execute("SELECT text_source FROM paper_page WHERE paper_id = ? AND n = ?", (paper_id, n)).fetchone()
    if row is None:
        raise Problem("No such page.")
    if row["text_source"] == "user":
        raise Problem("Its words were typed or corrected by someone: they are kept.")
    if row["text_source"] == "file":
        raise Problem("Its words are the file's own.")
    con.execute("""UPDATE paper_page SET text = NULL, text_source = NULL, unsure = NULL, blank = 0, model = NULL,
                   version = NULL, read_at = NULL WHERE paper_id = ? AND n = ?""", (paper_id, n))
    con.execute("DELETE FROM retry WHERE stage = 'papers' AND item_id = ?", (paper_id,))


@dataclass
class Details:
    kind: str
    title: str
    written: str = ""
    sender: str = ""
    recipient: str = ""
    summary: str | None = None  # None: as it is


def set_details(con: sqlite3.Connection, paper_id: int, d: Details) -> None:
    """What a person says a paper is: its kind, title, the date it was written, who wrote it and to whom. Kept as
    theirs. Raises Problem for a date that can't be read. Doesn't commit."""
    if get(con, paper_id) is None:
        raise Problem("No such document.")
    kind, title = " ".join(d.kind.split())[:60], " ".join(d.title.split())[:TITLE_LIMIT]
    if not kind:
        raise Problem("Say what kind of document it is.")
    written = " ".join(d.written.split())[:120]
    try:
        first, last = _span_of(con, written, strict=True)
    except dates.Problem as e:
        raise Problem(f"The date it was written: {e}") from None
    con.execute("""UPDATE document SET kind = ?, title = ?, written = ?, written_from = ?, written_to = ?, sender = ?,
                                       recipient = ?, source = 'user' WHERE image_id = ?""",
                (kind, title or kind, written or None, first, last, _short(d.sender), _short(d.recipient), paper_id))
    if d.summary is not None:
        con.execute("UPDATE document SET summary = ? WHERE image_id = ?",
                    (" ".join(d.summary.split())[:SUMMARY_LIMIT] or None, paper_id))


def _short(text: str | None) -> str | None:
    return " ".join(str(text or "").split())[:FIELD_LIMIT] or None


def _shown_date(iso: str | None) -> str | None:
    """'1931-06-03' -> 'June 3, 1931', as a person would write it."""
    if not iso:
        return None
    try:
        return dates.photo_date(iso).text
    except dates.Problem:
        return iso


_OLD_YEAR = re.compile(r"\b(1[0-7]\d\d)\b")


def _span_of(con: sqlite3.Connection, written: str | None, strict: bool = False) -> tuple[str | None, str | None]:
    """The first and last day (ISO) a written date means, or (None, None). Papers go back further than photos: a year
    before 1800 is that whole year. strict: raise dates.Problem for one that can't be read."""
    if not written:
        return None, None
    try:
        span = dates.photo_date(written, dates.south(con), events.day_first(con))
    except dates.Problem:
        if m := _OLD_YEAR.search(written):
            return f"{m[1]}-01-01", f"{m[1]}-12-31"
        if strict:
            raise
        return None, None
    return (span.first.isoformat(), span.last.isoformat()) if span else (None, None)


def written_span(con: sqlite3.Connection, written: str | None) -> tuple[str | None, str | None]:
    """_span_of for the AI's readings (paper_reading.py): what can't be read is no date."""
    return _span_of(con, written)


# ---- the Historical page's lists --------------------------------------------------------------------------------------

PAPERS = f"""SELECT d.image_id AS id, d.kind, d.title, d.written, d.written_from, d.written_to, d.sender, d.recipient,
                    d.unreadable, d.summary, i.sha256, i.media, i.format,
                    (SELECT count(*) FROM paper_page p WHERE p.paper_id = d.image_id) AS pages,
                    (SELECT count(*) FROM paper_page p WHERE p.paper_id = d.image_id AND p.text IS NULL
                     AND NOT p.blank) AS unread,
                    (SELECT min(p.n) FROM paper_page p WHERE p.paper_id = d.image_id AND p.unsure IS NOT NULL)
                      AS first_unsure,
                    (SELECT {ORIGIN} FROM file f WHERE f.image_id = i.id AND NOT f.missing ORDER BY f.id LIMIT 1)
                      AS folder_id,
                    coalesce(d.written_from, CASE WHEN i.taken_at_source = 'user' THEN coalesce(i.date_from, i.taken_at)
                                                  END) AS dated
             FROM document d JOIN image i ON i.id = d.image_id WHERE {VISIBLE}"""


def listing(con: sqlite3.Connection, kind: str = "", folder: int | None = None, decade: str = "",
            sort: str = "written", ids: list[int] | None = None, to_check: bool = False) -> list[dict]:
    """The papers the connection sees, narrowed to a kind, a collection (folder) or a decade ('1930', or UNDATED),
    or to those with lines the AI wasn't sure it read right (to_check: for someone to check against the page);
    oldest written first (sort 'written'), newest added first ('added') or by title ('title')."""
    where, args = [], []
    if to_check:
        where.append("first_unsure IS NOT NULL")
    if kind:
        where.append("kind = ?")
        args.append(kind)
    if folder is not None:
        where.append("folder_id = ?")
        args.append(folder)
    if decade == UNDATED:
        where.append("dated IS NULL")
    elif decade.isdecimal():
        where.append("dated >= ? AND dated < ?")
        args += [f"{int(decade):04d}", f"{int(decade) + 10:04d}"]
    if ids is not None:
        where.append("id IN (SELECT value FROM json_each(?))")
        args.append(json.dumps(ids))
    order = {"added": "id DESC", "title": "title COLLATE NOCASE, id"}.get(sort, "dated IS NULL, dated, title COLLATE NOCASE")
    sql = f"SELECT * FROM ({PAPERS})" + (f" WHERE {' AND '.join(where)}" if where else "") + f" ORDER BY {order}"
    return [dict(r) for r in con.execute(sql, args)]


def facets(con: sqlite3.Connection) -> dict:
    """What the papers the connection sees can be narrowed to, each with how many: kinds, collections (folders) and
    decades written, the most first (decades in order)."""
    rows = con.execute(f"SELECT kind, folder_id, dated, first_unsure FROM ({PAPERS})").fetchall()
    kinds = Counter(r["kind"] for r in rows)
    folders = Counter(r["folder_id"] for r in rows if r["folder_id"] is not None)
    decades = Counter(f"{int(r['dated'][:4]) // 10 * 10}" if r["dated"] else UNDATED for r in rows)
    names = {r["id"]: r for r in con.execute("SELECT id, path, name FROM folder WHERE id IN (SELECT value FROM "
                                             "json_each(?))", (json.dumps(sorted(folders)),))}
    return {
        "total": len(rows),
        "to_check": sum(r["first_unsure"] is not None for r in rows),
        "kinds": kinds.most_common(),
        "collections": [{"id": f, "name": names[f]["name"] or names[f]["path"] or "/", "path": names[f]["path"],
                         "count": n} for f, n in folders.most_common() if f in names],
        "decades": sorted(decades.items(), key=lambda kv: (kv[0] == UNDATED, kv[0])),
    }


def for_person(con: sqlite3.Connection, person_id: int) -> list[dict]:
    """The papers the connection sees that are about someone, by or to them, or name them (linked by the card or by
    hand: paper_person), that are a record of them or of one of their life events (citation), or that show them (a
    face named on one of its pages, or added by hand): oldest written first, each with how."""
    how: dict[int, str] = {}
    for paper_id, role in con.execute("""
            SELECT paper_id, role FROM paper_person WHERE person_id = :p AND state IN ('auto', 'user')
            UNION ALL SELECT c.image_id, 'record' FROM citation c LEFT JOIN event e ON e.id = c.event_id
              WHERE c.person_id = :p OR e.person_id = :p OR :p IN (e.couple_a, e.couple_b)
            UNION ALL SELECT coalesce(i.page_of, i.id), 'shown' FROM face f JOIN image i ON i.id = f.image_id
              WHERE f.person_id = :p AND f.assigned_by IN ('user', 'auto')
            UNION ALL SELECT image_id, 'shown' FROM image_person WHERE person_id = :p""", {"p": person_id}):
        if paper_id not in how or how[paper_id] in ("named", "shown"):
            how[paper_id] = role
    if not how:
        return []
    found = listing(con, ids=sorted(how))
    for p in found:
        p["how"] = how[p["id"]]
    return found


def related(con: sqlite3.Connection, paper_id: int) -> list[dict]:
    """Other papers between the same two people (a letter and the answers to it), oldest written first: by the same
    sender and recipient, either way round, as written on them."""
    paper = get(con, paper_id)
    if paper is None or not paper["sender"] or not paper["recipient"]:
        return []
    a, b = paper["sender"].lower(), paper["recipient"].lower()
    ids = [r[0] for r in con.execute("""SELECT image_id FROM document WHERE image_id != ?
                                        AND ((lower(sender) = ? AND lower(recipient) = ?)
                                             OR (lower(sender) = ? AND lower(recipient) = ?))""",
                                     (paper_id, a, b, b, a))]
    return listing(con, ids=ids) if ids else []


def counts(con: sqlite3.Connection) -> dict:
    """For Status: papers, their pages, and the pages waiting to be read (not blank, no words yet, readable)."""
    one = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    return {"papers": one(f"SELECT count(*) FROM document d JOIN image i ON i.id = d.image_id WHERE {VISIBLE}"),
            "pages": one("SELECT count(*) FROM paper_page"),
            "to_read": one("""SELECT count(*) FROM paper_page p JOIN document d ON d.image_id = p.paper_id
                              WHERE p.text IS NULL AND NOT p.blank AND d.unreadable IS NULL""")}
