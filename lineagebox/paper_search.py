"""Searching the words of historical papers (historical.py): an index of its own, a row for each page (paper_fts), so a
search can say on which page it matched and show the lines that did, with the words marked.

A paper's first page's row also holds what is said of the whole paper ("about": its title, kind, when it was
written, by whom and to whom, its collection) and who is in it ("people": named on a face or added by hand, or what it
is a record of), so a search for a name finds the papers about that person by any of their names, as photo search
does (search._match reads relationship words and names the same way here). The rowid is the paper's id times PAGE_ID
plus the page's number: a paper's rows are found, and made again, by a range of rowids.

The index is kept with the photos' (search.index_image calls index_paper), from the same queue of what changed
(search_stale, which paper_page's triggers fill). The photo search leaves papers out: they are found here, in a
section of their own on the search page and on the Historical page.
"""
import json
import sqlite3
from html import escape

from lineagebox import search
from lineagebox.catalog import ORIGIN, VISIBLE

PAGE_ID = 10_000      # rowid = paper id * PAGE_ID + page number (papers.MOST_PAGES is below it)
WEIGHTS = "4.0, 5.0, 1.0"  # bm25: what is said of the paper, who is in it, its words
MOST_ROWS = 5000      # pages looked at for one search, the best first
HITS = 3              # pages of one paper whose matching lines are shown
SNIPPET_WORDS = 16
_MARK, _END = "\x02", "\x03"  # around a matched word in a snippet, made <mark> once the rest is escaped


def index_paper(con: sqlite3.Connection, image_id: int) -> None:
    """Make a paper's rows again (none if it is no paper, or not in the library). Part of the caller's write."""
    con.execute("DELETE FROM paper_fts WHERE rowid >= ? AND rowid < ?",
                (image_id * PAGE_ID, (image_id + 1) * PAGE_ID))
    paper = con.execute(f"""SELECT d.* FROM document d JOIN image i ON i.id = d.image_id
                            WHERE d.image_id = ? AND {VISIBLE}""", (image_id,)).fetchone()
    if paper is None:
        return
    folder = con.execute(f"""SELECT d.name FROM file f JOIN folder d ON d.id = {ORIGIN}
                             WHERE f.image_id = ? AND NOT f.missing ORDER BY f.id LIMIT 1""", (image_id,)).fetchone()
    about = " ".join(filter(None, (paper["title"], paper["kind"], paper["written"], paper["sender"],
                                   paper["recipient"], search.folder_words(folder[0]) if folder else None)))
    people = [r[0] for r in con.execute("""
        SELECT p.name FROM face f JOIN person p ON p.id = f.person_id
        WHERE f.assigned_by IN ('user', 'auto') AND f.image_id IN (SELECT :i UNION SELECT image_id FROM paper_page
                                                                   WHERE paper_id = :i AND image_id IS NOT NULL)
        UNION SELECT p.name FROM image_person l JOIN person p ON p.id = l.person_id WHERE l.image_id = :i
        UNION SELECT p.name FROM citation c JOIN person p ON p.id = c.person_id WHERE c.image_id = :i
        UNION SELECT p.name FROM paper_person x JOIN person p ON p.id = x.person_id
        WHERE x.paper_id = :i AND x.state IN ('auto', 'user')""", {"i": image_id})]
    pages = con.execute("SELECT n, text FROM paper_page WHERE paper_id = ? ORDER BY n", (image_id,)).fetchall() \
        or [{"n": 1, "text": None}]
    con.executemany("INSERT INTO paper_fts(rowid, about, people, words) VALUES (?, ?, ?, ?)",
                    ((image_id * PAGE_ID + p["n"], about if p["n"] == 1 else "", " ".join(people) if p["n"] == 1
                      else "", p["text"] or "") for p in pages))


def rebuild(con: sqlite3.Connection) -> int:
    con.execute("DELETE FROM paper_fts")
    ids = [r[0] for r in con.execute(f"SELECT d.image_id FROM document d JOIN image i ON i.id = d.image_id "
                                     f"WHERE {VISIBLE}")]
    for image_id in ids:
        index_paper(con, image_id)
    return len(ids)


def _snippet(text: str) -> str:
    """A snippet as HTML: its words escaped, the matched ones marked."""
    return escape(text or "").replace(_MARK, "<mark>").replace(_END, "</mark>")


def query(con: sqlite3.Connection, text: str, limit: int = 500, people: tuple[int, ...] = (),
          year_from: int | None = None, year_to: int | None = None) -> list[dict]:
    """The papers the connection sees whose words (or title, or people) match every word typed, the best first:
    each with its details and its best pages' matching lines ("hits": [{n, snippet}], HTML with the words marked).
    people: papers each of these is in (a face named on one of its pages, added by hand, cited, or linked as named in
    it). Years: written (its date as said) within them."""
    match, _ = search._match(con, text)
    if match is None:
        return []
    rows = con.execute(f"""
        SELECT s.rowid AS rowid, bm25(paper_fts, {WEIGHTS}) AS score,
               snippet(paper_fts, 2, '{_MARK}', '{_END}', '…', {SNIPPET_WORDS}) AS snippet
        FROM paper_fts s WHERE paper_fts MATCH ? ORDER BY score LIMIT ?""", (match, MOST_ROWS)).fetchall()
    best: dict[int, dict] = {}
    for r in rows:
        paper_id, n = divmod(r["rowid"], PAGE_ID)
        found = best.setdefault(paper_id, {"score": r["score"], "hits": []})
        if len(found["hits"]) < HITS and n and _MARK in (r["snippet"] or ""):  # its words matched, not its title
            found["hits"].append({"n": n, "snippet": _snippet(r["snippet"])})
    if not best:
        return []
    where, args = [f"d.image_id IN (SELECT value FROM json_each(?))", VISIBLE], [json.dumps(sorted(best))]
    for pid in people:
        where.append("""(EXISTS (SELECT 1 FROM face f WHERE f.person_id = ? AND f.assigned_by IN ('user', 'auto')
                                 AND f.image_id IN (SELECT d.image_id UNION SELECT image_id FROM paper_page
                                                    WHERE paper_id = d.image_id))
                         OR EXISTS (SELECT 1 FROM image_person l WHERE l.person_id = ? AND l.image_id = d.image_id)
                         OR EXISTS (SELECT 1 FROM citation c WHERE c.person_id = ? AND c.image_id = d.image_id)
                         OR EXISTS (SELECT 1 FROM paper_person x WHERE x.person_id = ? AND x.paper_id = d.image_id
                                    AND x.state IN ('auto', 'user')))""")
        args += [pid] * 4
    if year_from:
        where.append("d.written_to >= ?")
        args.append(f"{year_from:04d}")
    if year_to:
        where.append("d.written_from < ?")
        args.append(f"{year_to + 1:04d}")
    papers = con.execute(f"""
        SELECT d.image_id AS id, d.kind, d.title, d.written, d.sender, d.recipient, i.sha256,
               (SELECT count(*) FROM paper_page p WHERE p.paper_id = d.image_id) AS pages
        FROM document d JOIN image i ON i.id = d.image_id WHERE {' AND '.join(where)}""", args).fetchall()
    out = [{**dict(p), **best[p["id"]]} for p in papers]
    return sorted(out, key=lambda p: (p["score"], p["id"]))[:limit]


def count(con: sqlite3.Connection, text: str) -> int:
    return len(query(con, text, limit=search.RESULTS))
