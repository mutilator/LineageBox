"""The worker's stage "read papers": the AI copies out the words of historical papers (historical.py), page by page,
and finds the papers among the photos.

1. Pages to read: every page of a paper with no words yet (a scan, a scanned PDF's page, a page put with a paper by
   hand), newest papers first. A paper's first page, when nobody has said what the paper is, is read with the
   question that also names its kind, title, date and language (FIRST); later pages only for their words (PAGE).
2. Photos the describing AI called a document ("document/screenshot"), but not screenshots by their names or shape,
   are each asked once (paper_check) whether they are a paper, with the same FIRST question: a letter or a certificate
   becomes a paper of one page; a screenshot or a sign is left as the photo it is.

The model is Settings' model for reading documents, else the one that describes photos: it must see pictures. Each
line it copies out comes with how likely its words were to the model (Ollama's token odds, as headcount.py reads
them): lines below UNSURE are kept as unsure (paper_page.unsure), shown on the paper's page for someone to check, so
a misread word is never quietly taken as right. An answer that repeats itself to its end (small models do, on dense
pages) is cut where it started repeating, and those lines are unsure too.

A page the AI can't read just now (Ollama's error, an answer that isn't JSON) waits to be tried again (retry.py); the
AI not answering ends the pass. Words a person typed or corrected are never read again.

Measured on 2026-11-26 on six real scanned pages (typed family group sheets, obituaries, an email, a scrapbook cover),
against the same pages read by eye: ministral-3 took 4 to 16 s a page on a graphics card and read the typed words
right; the one handwritten date (3/10/2004) it read as Arabic-Indic digits, and that was its least sure line (0.04),
while no typed line it read right fell below UNSURE once line breaks were left out of it (they were the least likely
tokens: where a line ends is a guess at the layout). qwen3.8:27b read the date right but took 43 to 294 s a page:
hence a model of its own for reading (Settings → AI). A line in a script the rest of the page doesn't use (those
digits in an English page) is unsure too, whatever the odds, so an Ollama that gives none still marks it.
"""
import io
import json
import math
import re
import sqlite3
import unicodedata

from PIL import Image

from lineagebox import clock, documents, historical, papers, retry
from lineagebox.catalog import VISIBLE

VERSION = 1          # of the questions: a new one asks the photos that weren't papers again (papers keep their words)
STAGE = "papers"     # retry.py
retry.STAGES[STAGE] = "pages of documents to read"
READ_SIZE = 1600     # px, the longest side the AI sees: small print and handwriting need more than a description
ANSWER_TOKENS = 4096  # a dense typed page is about 3,000 tokens of answer
MORE_TOKENS = 8192   # asked again with this much room when the first answer was cut off
UNSURE = 0.35        # a line with a word whose tokens were less likely than this is unsure
REPEATS = 4          # the same line this many times in a row: the model is repeating itself
GIVE_UP_AFTER = 5    # pages in a row the AI couldn't read: the rest wait for the next pass
DOCUMENT = "document/screenshot"  # the describing AI's photo_type for a picture of a document
# file names screenshots and screen captures have (lower case, as LIKE patterns)
SCREENSHOT_NAMES = ("%screenshot%", "%screen shot%", "%screen_shot%", "%screencap%", "%screen recording%",
                    "%bildschirmfoto%", "%capture d%cran%", "%schermafbeelding%", "%captura de pantalla%")
PAPER_SHAPE = (1.15, 1.75)  # height to width (either way up) of paper, and of photos of it; a screen is longer

_FIRST_FIELDS = """- is_document: true for a paper document or a page of one, held, laid down or scanned: a letter, a card, a
  certificate, an award, a diploma, a form, a deed, a newspaper clipping, a page of a book, diary or family Bible, a
  program, a recipe. False for anything else: a screenshot of a phone or computer screen, a photo of people or a
  place, a sign, a label, a photo of a photo.
- kind: what kind of document it is.
- title: a short title a family would give it ("Letter to her sister", "High school diploma", "Deed to the farm"),
  with names only as they are written on it.
- written: the date it was written, printed or issued, exactly as written on it; "" if it has none.
- language: the language it is written in, in English ("English", "French", "German").
- handwritten: true when most of its words are written by hand.
"""
_TEXT_FIELD = """- text: every word on the page, copied exactly as written, in reading order (column by column in a
  newspaper), one line of the page to a line. Keep every name, date and number exactly as written, even where they
  look wrong or are spelled the old way. Write [?] for a word you can't read. Leave out what isn't part of it (other
  articles cut off at the edge, the table it lies on). "" when it has no words."""
FIRST = ("You catalog a family's historical papers. Look at the photo or scan and fill in the JSON fields.\n"
         + _FIRST_FIELDS + _TEXT_FIELD)
PAGE = ("You copy out a page of a family's historical paper: a letter, a certificate, a form, a clipping.\n"
        "- blank: true when the page has nothing written or printed on it (the back of a page).\n" + _TEXT_FIELD)
FIRST_SCHEMA = {"type": "object",
                "required": ["is_document", "kind", "title", "written", "language", "handwritten", "text"],
                "properties": {"is_document": {"type": "boolean"},
                               "kind": {"type": "string", "enum": list(documents.KINDS)},
                               "title": {"type": "string"}, "written": {"type": "string"},
                               "language": {"type": "string"}, "handwritten": {"type": "boolean"},
                               "text": {"type": "string"}}}
PAGE_SCHEMA = {"type": "object", "required": ["blank", "text"],
               "properties": {"blank": {"type": "boolean"}, "text": {"type": "string"}}}


class Problem(Exception):
    """A page the AI couldn't read just now: tried again later (retry.py)."""


class Away(Exception):
    """The AI isn't answering: the pass ends."""


# ---- what to read ---------------------------------------------------------------------------------------------------

def pages_waiting(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Pages with no words yet, of papers whose words can be read, newest papers first, each in page order."""
    return con.execute(f"""
        SELECT p.paper_id, p.n FROM paper_page p JOIN document d ON d.image_id = p.paper_id
        JOIN image i ON i.id = p.paper_id
        WHERE p.text IS NULL AND NOT p.blank AND d.unreadable IS NULL AND {VISIBLE} AND {retry.waiting(STAGE, "p.paper_id")}
        ORDER BY p.paper_id DESC, p.n""").fetchall()


def _screenshot_names() -> str:
    return " OR ".join(f"lower(f.path) LIKE '{p}'" for p in SCREENSHOT_NAMES)


def candidates(con: sqlite3.Connection) -> list[int]:
    """Photos the describing AI called a document (it, or the near-duplicate whose description it shares), not asked
    about yet with this VERSION, nor said by a person to be or not be a paper, that aren't screenshots by their file's
    name, nor by their shape (a picture with no camera that is longer than paper is: a phone's or computer's screen).
    The newest first."""
    low, high = PAPER_SHAPE
    described = [r[0] for r in con.execute("SELECT DISTINCT image_id FROM analysis WHERE photo_type = ?", (DOCUMENT,))]
    if not described:
        return []
    rows = con.execute(f"""
        WITH said(id) AS (SELECT value FROM json_each(?)),
             groups(g) AS (SELECT DISTINCT dup_group_id FROM image WHERE id IN said AND dup_group_id IS NOT NULL)
        SELECT i.id, i.width, i.height, i.camera, i.format FROM image i
        WHERE (i.id IN said OR i.dup_group_id IN groups) AND {VISIBLE} AND i.media = 'photo'
          AND NOT EXISTS (SELECT 1 FROM document d WHERE d.image_id = i.id)
          AND NOT EXISTS (SELECT 1 FROM paper_check c WHERE c.image_id = i.id AND (c.by = 'user' OR c.version >= ?))
          AND NOT EXISTS (SELECT 1 FROM file f WHERE f.image_id = i.id AND ({_screenshot_names()}))
          AND {retry.waiting(STAGE, "i.id")}
        ORDER BY i.id DESC""", (json.dumps(described), VERSION)).fetchall()
    out = []
    for r in rows:
        if r["camera"] is None and r["width"] and r["height"]:
            shape = max(r["width"], r["height"]) / max(1, min(r["width"], r["height"]))
            if not low <= shape <= high and r["format"] == "png":  # a screen, not a page
                continue
        out.append(r["id"])
    return out


def count_waiting(con: sqlite3.Connection) -> int:
    """For Status's Work in progress: pages to read and photos to ask about."""
    return len(pages_waiting(con)) + len(candidates(con))


# ---- the pictures the AI sees ------------------------------------------------------------------------------------------

def page_picture(con: sqlite3.Connection, cfg, paper_id: int, n: int, longest: int = READ_SIZE) -> Image.Image | None:
    """A page as a picture: a scanned page's preview, or the paper file's page n drawn (kept in cfg.page_path), or
    None for a page that has none (a Word file's later pages are words only). Raises OSError when the preview or the
    file can't be read just now, papers.Unreadable or papers.Damaged for a file that can't be drawn."""
    page = con.execute("""SELECT p.image_id, i.sha256, i.media FROM paper_page p
                          JOIN image i ON i.id = coalesce(p.image_id, p.paper_id)
                          WHERE p.paper_id = ? AND p.n = ?""", (paper_id, n)).fetchone()
    if page is None:
        return None
    if page["image_id"] is not None or n == 1:
        with Image.open(cfg.preview_path(page["sha256"])) as im:
            out = im.convert("RGB")
    else:
        fmt = con.execute("SELECT format FROM image WHERE id = ?", (paper_id,)).fetchone()[0]
        if fmt != "pdf":
            return None
        out = drawn_page(con, cfg, paper_id, page["sha256"], n)
    out.thumbnail((longest, longest), Image.Resampling.LANCZOS)
    return out


def drawn_page(con: sqlite3.Connection, cfg, paper_id: int, sha256: str, n: int) -> Image.Image:
    """Page n of a PDF, drawn once at the preview's size and kept (cfg.page_path)."""
    from lineagebox import media

    kept = cfg.page_path(sha256, n)
    if kept.exists():
        with Image.open(kept) as im:
            return im.convert("RGB")
    path = con.execute("SELECT path FROM file WHERE image_id = ? AND NOT missing ORDER BY id LIMIT 1",
                       (paper_id,)).fetchone()
    if path is None:
        raise OSError("its file isn't on the share")
    try:
        im = papers.pdf_page(cfg.source_path(path[0]), n, cfg.preview_size)
    except ImportError:
        raise papers.Unreadable("PDFs can't be drawn without pypdfium2 installed") from None
    media.atomic_save(im, kept, "JPEG", quality=85)
    return im


def _jpeg(im: Image.Image) -> bytes:
    out = io.BytesIO()
    im.save(out, "JPEG", quality=90)
    return out.getvalue()


# ---- asking ---------------------------------------------------------------------------------------------------------

def _ask(client, system: str, schema: dict, picture: bytes) -> tuple[dict, list]:
    """One question about one picture: (the answer, its tokens). Asked again with more room when it was cut off.
    Raises Problem for an answer that can't be used, Away when the AI doesn't answer."""
    import httpx

    from lineagebox.llm import CutOff, OllamaError

    for room in (ANSWER_TOKENS, MORE_TOKENS):
        try:
            answer, usage = client.chat_json(system, "Read this.", schema, images=[picture], temperature=0.0,
                                             max_tokens=room, logprobs=1)
            return answer, usage.tokens
        except CutOff:
            continue  # a long page, or one the model repeats itself on: once more with more room
        except ValueError:
            raise Problem("The AI's answer wasn't JSON.") from None
        except OllamaError as e:
            raise Problem(f"The AI couldn't read it: {e}") from None
        except httpx.HTTPError as e:
            raise Away(f"The AI isn't answering ({type(e).__name__}).") from None
    raise Problem("The AI's answer ran past its room twice: the page may be too dense, or it repeated itself.")


def line_sureness(tokens: list, text: str) -> list[float] | None:
    """How likely each line of the answer's "text" was to the model: of the tokens of that line that hold a letter
    or a digit, the least likely one's chance. None without the tokens (an Ollama too old to give them), or when they
    don't spell the answer."""
    if not tokens:
        return None
    content, starts = "", []
    for t in tokens:
        starts.append(len(content))
        content += t.get("token", "")
    found = re.search(r'"text"\s*:\s*"', content)
    if not found:
        return None
    # where each character of the JSON string's value is, as a line number of the decoded text
    line_at, line, i = {}, 0, found.end()
    while i < len(content):
        c = content[i]
        if c == '"':
            break
        if c == "\\" and i + 1 < len(content):
            step = 6 if content[i + 1] == "u" else 2
            for k in range(i, min(i + step, len(content))):
                line_at[k] = line
            if content[i + 1] == "n":
                line += 1
            i += step
            continue
        line_at[i] = line
        i += 1
    lines = text.split("\n")
    if line + 1 != len(lines):
        return None
    least = [1.0] * len(lines)
    for t, at in zip(tokens, starts):
        word = t.get("token", "")
        # only a word's tokens: where a line breaks is the model's guess at the layout, not its reading (measured on
        # real pages, 2026-11-26: the least likely tokens of typed pages were line breaks, \n in the JSON)
        if not re.search(r"[^\W_]", re.sub(r'\\(?:u[0-9a-fA-F]{4}|.)', "", word)):
            continue
        spots = [line_at[k] for k in range(at, at + len(word)) if k in line_at]
        if not spots or t.get("logprob") is None:
            continue
        chance = math.exp(t["logprob"])
        for n in set(spots):
            least[n] = min(least[n], chance)
    return least


def _script(c: str) -> str | None:
    """The script a letter or digit is written in: 'latin', else the first word of its Unicode name (ARABIC,
    CYRILLIC, CJK, EXTENDED...); None for an ASCII digit, which any script's pages use."""
    if c.isascii():
        return None if c.isdigit() else "latin"
    name = unicodedata.name(c, "")
    return "latin" if name.startswith("LATIN") else (name.split(" ")[0] or "?")


def strange_lines(lines: list[str], share: float = 0.35) -> set[int]:
    """Lines with a letter or digit in a script that makes up less than this share of the page's, other than the
    page's own: a misreading (handwritten digits read as another alphabet's), never a page written in it."""
    counts: dict[str, int] = {}
    for line in lines:
        for c in line:
            if c.isalnum() and (s := _script(c)):
                counts[s] = counts.get(s, 0) + 1
    total = sum(counts.values())
    main = max(counts, key=counts.get) if counts else None
    rare = {s for s, n in counts.items() if s != main and n / total < share}
    return {k for k, line in enumerate(lines) if any(c.isalnum() and _script(c) in rare for c in line)}


def tidy_reading(text: str, sureness: list[float] | None) -> tuple[str, list[int]]:
    """The AI's words as they are kept, with the lines (from 0) it wasn't sure of: below UNSURE, in a script the rest
    of the page doesn't use (strange_lines), or where the answer began repeating itself (kept once, and marked)."""
    lines = str(text or "").replace("\r\n", "\n").split("\n")
    unsure = {n for n, s in enumerate(sureness or []) if s < UNSURE and n < len(lines) and lines[n].strip()}
    unsure |= strange_lines(lines)
    out, kept_unsure, run = [], [], 0
    for n, line in enumerate(lines):
        same = bool(out) and line.strip() and line.strip() == out[-1].strip()
        run = run + 1 if same else 0
        if run >= REPEATS - 1:  # the model repeating itself: the line kept once, the rest of the answer is no reading
            del out[len(out) - run + 1:]
            kept_unsure = [k for k in kept_unsure if k < len(out)] + [len(out) - 1]
            break
        if n in unsure:
            kept_unsure.append(len(out))
        out.append(line.rstrip())
    while out and not out[-1].strip():
        out.pop()
    kept = papers.tidy("\n".join(out))
    # papers.tidy only joins spaces and empty lines: line numbers past an empty run shift, so they are found again
    kept_lines = kept.split("\n")
    marked = sorted({_same_line(kept_lines, out[n]) for n in kept_unsure if n < len(out)} - {None})
    return kept, marked


def _same_line(lines: list[str], line: str) -> int | None:
    wanted = " ".join(line.split())
    for n, kept in enumerate(lines):
        if kept == wanted and wanted:
            return n
    return None


# ---- reading --------------------------------------------------------------------------------------------------------

def read_page(con: sqlite3.Connection, cfg, client, paper_id: int, n: int) -> None:
    """Read one page of a paper and keep its words. The first page of a paper nobody has said anything of is read with
    FIRST, and what it says the paper is (kind, title, date, language) is kept too. Raises Problem, Away."""
    paper = historical.get(con, paper_id)
    if paper is None:
        return
    try:
        picture = page_picture(con, cfg, paper_id, n)
    except OSError as e:
        raise Problem(f"Its picture can't be read just now: {e}") from None
    except (papers.Unreadable, papers.Damaged) as e:
        con.execute("UPDATE document SET unreadable = ? WHERE image_id = ?", (str(e), paper_id))
        con.commit()
        return
    if picture is None:  # words only, and none (a Word file's empty page): nothing to read
        con.execute("UPDATE paper_page SET blank = 1 WHERE paper_id = ? AND n = ? AND text IS NULL", (paper_id, n))
        con.commit()
        return
    first = n == 1 and paper["source"] == "auto" and paper["kind"] == "Other"
    answer, tokens = _ask(client, FIRST if first else PAGE, FIRST_SCHEMA if first else PAGE_SCHEMA, _jpeg(picture))
    text, unsure = tidy_reading(str(answer.get("text") or ""), line_sureness(tokens, str(answer.get("text") or "")))
    blank = (not text.strip()) if first else bool(answer.get("blank")) and not text.strip()
    # written only if nobody wrote its words meanwhile (the page shown with a box to type them)
    con.execute("""UPDATE paper_page SET text = ?, text_source = 'ai', unsure = ?, blank = ?, model = ?, version = ?,
                   read_at = ? WHERE paper_id = ? AND n = ? AND text IS NULL""",
                (text, json.dumps(unsure) if unsure else None, int(blank), client.model, VERSION, clock.now(),
                 paper_id, n))
    if first and answer.get("is_document") is not False:
        _keep_card(con, paper_id, answer)
    retry.done(con, STAGE, [paper_id])
    con.commit()


def _keep_card(con: sqlite3.Connection, paper_id: int, answer: dict) -> None:
    """What the first page says the paper is, unless a person said so meanwhile."""
    kind = answer.get("kind") if answer.get("kind") in documents.KINDS else "Other"
    title = " ".join(str(answer.get("title") or "").split())[:historical.TITLE_LIMIT] or kind
    written = " ".join(str(answer.get("written") or "").split())[:120] or None
    first, last = historical.written_span(con, written)
    language = " ".join(str(answer.get("language") or "").split())[:40] or None
    con.execute("""UPDATE document SET kind = ?, title = ?, written = coalesce(written, ?),
                   written_from = coalesce(written_from, ?), written_to = coalesce(written_to, ?), language = ?
                   WHERE image_id = ? AND source = 'auto'""",
                (kind, title, written, first, last, language, paper_id))


def check_photo(con: sqlite3.Connection, cfg, client, image_id: int) -> bool:
    """Ask whether a photo the describer called a document is a paper; one that is becomes a paper of one page with
    its words. Returns whether it is. Raises Problem, Away."""
    row = con.execute("SELECT sha256 FROM image WHERE id = ?", (image_id,)).fetchone()
    if row is None:
        return False
    try:
        with Image.open(cfg.preview_path(row["sha256"])) as im:
            picture = im.convert("RGB")
    except OSError as e:
        raise Problem(f"Its preview can't be read just now: {e}") from None
    picture.thumbnail((READ_SIZE, READ_SIZE), Image.Resampling.LANCZOS)
    answer, tokens = _ask(client, FIRST, FIRST_SCHEMA, _jpeg(picture))
    is_paper = answer.get("is_document") is True
    now = clock.now()
    if con.execute("SELECT 1 FROM paper_check WHERE image_id = ? AND by = 'user'", (image_id,)).fetchone():
        return False  # a person said meanwhile
    con.execute("""INSERT INTO paper_check(image_id, is_paper, by, what, model, version, at)
                   VALUES (?, ?, 'ai', ?, ?, ?, ?)
                   ON CONFLICT(image_id) DO UPDATE SET is_paper = excluded.is_paper, by = 'ai', what = excluded.what,
                                                       model = excluded.model, version = excluded.version,
                                                       at = excluded.at""",
                (image_id, int(is_paper), None if is_paper else str(answer.get("kind") or "")[:60] or None,
                 client.model, VERSION, now))
    if is_paper and not con.execute("SELECT 1 FROM image WHERE id = ? AND page_of IS NOT NULL", (image_id,)).fetchone():
        raw = str(answer.get("text") or "")
        text, unsure = tidy_reading(raw, line_sureness(tokens, raw))
        kind = answer.get("kind") if answer.get("kind") in documents.KINDS else "Other"
        historical.make(con, image_id, kind, " ".join(str(answer.get("title") or "").split()) or kind, "auto", text,
                        unsure=unsure, blank=not text.strip(), model=client.model, version=VERSION,
                        language=" ".join(str(answer.get("language") or "").split())[:40] or None)
        _keep_card(con, image_id, answer)
    retry.done(con, STAGE, [image_id])
    con.commit()
    return is_paper


def client_for(cfg):
    """The Ollama client that reads papers: Settings' model for reading documents, else the describing one."""
    from lineagebox.llm import Ollama

    client = Ollama(cfg)
    client.model = cfg.ollama_read_model or cfg.ollama_model  # before its first question: thinks() asks of this one
    return client


def read_waiting(con: sqlite3.Connection, cfg, stop=None) -> tuple[int, int]:
    """The worker's pass: pages to read first, then photos to ask about, until stop() says so. Returns (pages read,
    photos asked about). The model missing or unable to see, or the AI not answering, ends it."""
    pages, photos = pages_waiting(con), candidates(con)
    if not pages and not photos:  # nothing to read: Ollama isn't asked anything
        return 0, 0
    client = client_for(cfg)
    if client.model_problem() is not None:
        return 0, 0
    read = asked = failed = 0
    held = set()  # papers a page of couldn't be read: their later pages wait with it, to be read in order
    try:
        for r in pages:
            if stop is not None and stop():
                return read, asked
            if r["paper_id"] in held:
                continue
            try:
                read_page(con, cfg, client, r["paper_id"], r["n"])
                read += 1
                failed = 0
            except Problem as e:
                retry.later(con, STAGE, r["paper_id"], str(e))
                held.add(r["paper_id"])
                failed += 1
                if failed >= GIVE_UP_AFTER:
                    return read, asked
        for image_id in photos:
            if stop is not None and stop():
                break
            try:
                check_photo(con, cfg, client, image_id)
                asked += 1
                failed = 0
            except Problem as e:
                retry.later(con, STAGE, image_id, str(e))
                failed += 1
                if failed >= GIVE_UP_AFTER:
                    break
    except Away:
        pass
    return read, asked
