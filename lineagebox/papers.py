"""Papers that are files of their own (image.media 'document'): a PDF, a Word or OpenDocument file, RTF, plain text,
an email, a saved web page; and old formats that can't be read here (an old Word file, WordPerfect, Outlook's .msg),
which are kept as papers to download. Read once, at ingest, for:
- their words: a PDF's own text page by page (a scanned PDF's pages have none: the worker reads those, as it reads
  scans: paper_reading.py), the others' paragraphs in pages of about PAGE_CHARS;
- what they say of themselves: a title, the date an email was sent, who it was from and to;
- a picture of the first page for tiles and previews: a PDF's drawn by PDFium (pypdfium2), the others drawn here as a
  page of their words.

Nothing in such a file is run or shown as it is: a web page's or email's markup is read for its words only, and the
site offers the file itself only to download (web/routes/historical.py), never as a page of its own. Each is read
with limits (MOST_BYTES, MOST_PAGES, and a zip's own sizes for Word and OpenDocument), since anyone may upload one.

A text file or web page with fewer than MIN_WORDS words isn't a paper (a camera's readme, a gallery's index page):
ingest leaves it out, as it leaves out songs (NOT_A_PAPER).
"""
import codecs
import io
import re
import textwrap
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image, ImageDraw, ImageFont

FORMATS = {".pdf": "pdf", ".docx": "docx", ".odt": "odt", ".rtf": "rtf", ".txt": "txt", ".eml": "eml",
           ".htm": "html", ".html": "html", ".doc": "doc", ".wpd": "wpd", ".wps": "wps", ".msg": "msg",
           ".pages": "pages"}
# formats whose words can't be read here, and what to say of them
CANT_READ = {"doc": "an old Word file (.doc): its words can't be read here. Save it as .docx in Word to search it.",
             "wpd": "a WordPerfect file: its words can't be read here.",
             "wps": "a Microsoft Works file: its words can't be read here.",
             "msg": "an Outlook message (.msg): its words can't be read here. Save it as .eml to search it.",
             "pages": "an Apple Pages file: its words can't be read here. Export it as PDF or Word to search it."}
NOT_A_PAPER = "not a paper: too few words for a letter or a page of notes"  # image.error, left out as songs are
MIN_WORDS = 30          # words a text file or web page needs to be a paper
# smaller than this, a text file or web page can't hold MIN_WORDS: the scan leaves it out, as before papers were read
MIN_TEXT_BYTES = 150
WORDS_NEEDED = {".txt", ".htm", ".html"}
PAGE_CHARS = 3000       # a text file's words are kept in pages about this long, so a search can say where it matched
MOST_BYTES = 200 << 20  # a paper read whole into memory (a PDF is read from its file, page by page)
MOST_XML = 50 << 20     # the most a Word or OpenDocument file's text may be once unzipped
MOST_TEXT = 2_000_000   # characters kept of one paper: a long book
MOST_PAGES = 2000       # pages of a PDF read: an abstract of title can have hundreds
TEXT_PAGE = 0.6         # a PDF page with fewer letters than this per square inch of page is read as a picture
PAGE_SIZE = (1275, 1650)  # a drawn page: a letter-size page at 150 dots an inch
_WORD = re.compile(r"[^\W\d_]{2,}")


class Unreadable(Exception):
    """A paper whose words can't be read here (locked with a password, too large): kept, to download. The message
    says why."""


class Damaged(Exception):
    """A file that can't be opened at all: recorded on it as a damaged photo is (image.error), never read again."""


@dataclass
class Read:
    format: str
    pages: list[str | None] = field(default_factory=list)  # each page's words; None: a picture to read (a scan's)
    picture: Image.Image | None = None                     # the first page
    title: str | None = None
    written: str | None = None      # the date it says it was written or sent, as an ISO date
    sender: str | None = None       # an email's From
    to: str | None = None           # an email's To
    unreadable: str | None = None   # why its words can't be read; then pages is empty
    not_a_paper: bool = False       # too few words to be one (a .txt or web page)
    page_count: int = 0             # all its pages (a PDF's), even past MOST_PAGES


def too_small(name: str, size: int) -> bool:
    """A text file or web page too small to be a paper (a camera's readme, a "do not delete" note)."""
    return size < MIN_TEXT_BYTES and Path(name).suffix.lower() in WORDS_NEEDED


def format_of(ext: str) -> str | None:
    return FORMATS.get(ext.lower())


def read_file(path: Path, ext: str) -> Read:
    """Read a paper's file. Its words that can't be read (locked, too large) say so in .unreadable. Raises Damaged for
    one that can't be opened, OSError when it can't be read just now."""
    fmt = format_of(ext)
    if fmt is None:
        raise Damaged(f"{ext} files aren't papers")
    if fmt in CANT_READ:
        out = Read(fmt, unreadable=CANT_READ[fmt], title=_name_title(path))
        out.picture = draw_page(out.title, "", note="This file can't be shown here. Download it to open it.")
        return out
    if fmt == "pdf":
        try:
            return _pdf(path)
        except Unreadable as e:
            return _kept(path, fmt, str(e))
    if path.stat().st_size > MOST_BYTES:
        return _kept(path, fmt, f"over {MOST_BYTES >> 20} MB: too large to be read here")
    data = path.read_bytes()
    try:
        return _read_data(data, fmt, path)
    except Unreadable as e:
        return _kept(path, fmt, str(e))


def _kept(path: Path, fmt: str, why: str) -> Read:
    """A paper kept to download, whose words can't be read here."""
    title = _name_title(path)
    return Read(fmt, title=title, unreadable=why, picture=draw_page(title, "", note="Download it to open it."))


def _read_data(data: bytes, fmt: str, path: Path) -> Read:
    if fmt == "docx":
        title, text, written = _docx(data)
    elif fmt == "odt":
        title, text, written = _odt(data)
    elif fmt == "rtf":
        title, text, written = None, rtf_text(decode_text(data)), None
    elif fmt == "eml":
        return _email(data, path)
    elif fmt == "html":
        title, text = html_text(decode_text(data))
        written = None
    else:
        title, text, written = None, decode_text(data), None
    text = tidy(text)
    out = Read(fmt, title=title or _name_title(path), written=written)
    if fmt in ("txt", "html") and len(_WORD.findall(text)) < MIN_WORDS:
        out.not_a_paper = True
        return out
    out.pages = split_pages(text)
    out.page_count = len(out.pages)
    out.picture = draw_page(out.title, text)
    return out


def _name_title(path: Path) -> str:
    """A title from a file's name: 'Grandma_letter-1931' -> 'Grandma letter 1931'."""
    return " ".join(re.sub(r"[_\-.]+", " ", path.stem).split())[:200] or path.name


# ---- the formats ---------------------------------------------------------------------------------------------------

def _pdf(path: Path) -> Read:
    try:
        import pypdfium2 as pdfium
    except ImportError:  # a copy of LineageBox installed without it: kept to download, read once it is there
        raise Unreadable("PDFs can't be read without pypdfium2 installed.")
    pdf = _open_pdf(pdfium, path)
    try:
        out = Read("pdf", page_count=len(pdf))
        if not out.page_count:
            raise Damaged("the PDF has no pages")
        try:
            meta = pdf.get_metadata_dict()
        except pdfium.PdfiumError:
            meta = {}
        title = " ".join(str(meta.get("Title") or "").split())
        # what programs put there for a title ("Microsoft Word - letter.docx", "Scan0001") says nothing
        out.title = (title if title and not re.match(r"(?i)(microsoft \w+ - |untitled|scan\s*\d|document\d|img_?\d)",
                                                    title) else _name_title(path))[:200]
        for n in range(min(out.page_count, MOST_PAGES)):
            page = pdf[n]
            try:
                text = page.get_textpage().get_text_bounded()
                width, height = page.get_size()  # in points, 72 an inch
                letters = sum(c.isalnum() for c in text)
                enough = letters >= TEXT_PAGE * (width / 72) * (height / 72)
                out.pages.append(tidy(text) if enough else None)
                if n == 0:
                    out.picture = render_page(page)
            finally:
                page.close()
        return out
    finally:
        pdf.close()


def _open_pdf(pdfium, path: Path):
    try:
        return pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as e:
        if "password" in str(e).lower():
            raise Unreadable("the PDF is locked with a password: its pages can't be shown or read here") from None
        raise Damaged(f"the PDF can't be opened: damaged, or not a PDF ({e})") from None


def pdf_page(path: Path, n: int, longest: int = 2048) -> Image.Image:
    """Page n (from 1) of a PDF, drawn with its longest side about `longest` pixels. Raises Unreadable or Damaged,
    and ImportError without pypdfium2."""
    import pypdfium2 as pdfium
    pdf = _open_pdf(pdfium, path)
    try:
        if not 1 <= n <= len(pdf):
            raise Damaged(f"the PDF has no page {n}")
        page = pdf[n - 1]
        try:
            return render_page(page, longest)
        finally:
            page.close()
    finally:
        pdf.close()


def render_page(page, longest: int = 2048) -> Image.Image:
    width, height = page.get_size()
    scale = min(longest / max(width, height, 1), 4.0)  # a tiny page isn't blown up past 288 dots an inch
    return page.render(scale=scale).to_pil().convert("RGB")


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
_DC = "{http://purl.org/dc/elements/1.1/}"
_DCTERMS = "{http://purl.org/dc/terms/}"


def _unzip(data: bytes, name: str) -> bytes | None:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise Damaged("damaged: it can't be opened") from None
    with z:
        try:
            info = z.getinfo(name)
        except KeyError:
            return None
        if info.file_size > MOST_XML:
            raise Unreadable(f"its text is over {MOST_XML >> 20} MB")
        return z.read(info)


def _xml(data: bytes | None):
    if data is None:
        return None
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError:
        raise Damaged("damaged: its text can't be read") from None


def _docx(data: bytes) -> tuple[str | None, str, str | None]:
    root = _xml(_unzip(data, "word/document.xml"))
    if root is None:
        raise Damaged("not a Word file")
    paragraphs: list[str] = []

    def walk(el, into: list[str]) -> None:
        for child in el:
            if child.tag == _FALLBACK:  # a text box is there twice: once for new Word and once for old
                continue
            if child.tag == _W + "t":
                into.append(child.text or "")
            elif child.tag == _W + "tab":
                into.append("\t")
            elif child.tag in (_W + "br", _W + "cr"):
                into.append("\n")
            elif child.tag == _W + "p":
                words: list[str] = []
                walk(child, words)
                paragraphs.append("".join(words))
                continue
            walk(child, into)

    walk(root, [])
    core = _xml(_unzip(data, "docProps/core.xml"))
    title = written = None
    if core is not None:
        title = " ".join((core.findtext(_DC + "title") or "").split()) or None
        written = _iso_day(core.findtext(_DCTERMS + "created"))
    return title, "\n".join(paragraphs), written


_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_META = "{urn:oasis:names:tc:opendocument:xmlns:meta:1.0}"


def _odt(data: bytes) -> tuple[str | None, str, str | None]:
    root = _xml(_unzip(data, "content.xml"))
    if root is None:
        raise Damaged("not an OpenDocument file")
    paragraphs: list[str] = []

    def words(el) -> str:
        out = [el.text or ""]
        for child in el:
            if child.tag == _TEXT + "s":
                out.append(" " * int(child.get(_TEXT + "c", "1") or 1))
            elif child.tag == _TEXT + "tab":
                out.append("\t")
            elif child.tag == _TEXT + "line-break":
                out.append("\n")
            elif child.tag not in (_TEXT + "note", _TEXT + "p", _TEXT + "h"):
                out.append(words(child))
            out.append(child.tail or "")
        return "".join(out)

    for el in root.iter():
        if el.tag in (_TEXT + "p", _TEXT + "h"):
            paragraphs.append(words(el))
    meta = _xml(_unzip(data, "meta.xml"))
    title = written = None
    if meta is not None:
        title = " ".join((meta.findtext(f".//{_DC}title") or "").split()) or None
        written = _iso_day(meta.findtext(f".//{_META}creation-date"))
    return title, "\n".join(paragraphs), written


def _iso_day(text: str | None) -> str | None:
    """'2004-06-03T10:00:00Z' -> '2004-06-03'; None for anything else."""
    m = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})", text or "")
    return f"{m[1]}-{m[2]}-{m[3]}" if m and 1800 <= int(m[1]) <= datetime.now().year else None


def _email(data: bytes, path: Path) -> Read:
    try:
        msg = BytesParser(policy=policy.default).parsebytes(data)
    except Exception:  # the email package raises many kinds for broken messages
        raise Damaged("damaged: the email can't be read") from None
    out = Read("eml")

    def header(name: str) -> str | None:
        try:
            value = msg[name]
        except Exception:  # a header the email package can't parse
            return None
        return " ".join(str(value).split())[:300] if value else None

    out.title = header("subject") or _name_title(path)
    out.sender, out.to = header("from"), header("to")
    try:
        sent = msg["date"].datetime if msg["date"] is not None else None
        out.written = sent.date().isoformat() if sent and 1800 <= sent.year <= datetime.now().year else None
    except Exception:
        out.written = None
    try:
        body = msg.get_body(preferencelist=("plain", "html"))
        text = body.get_content() if body is not None else ""
        if body is not None and body.get_content_type() == "text/html":
            text = html_text(text)[1]
    except Exception:
        text = ""
    heading = "\n".join(f"{label}: {value}" for label, value in (("From", out.sender), ("To", out.to),
                                                               ("Subject", header("subject"))) if value)
    text = tidy(f"{heading}\n\n{text}" if heading else text)
    out.pages = split_pages(text)
    out.page_count = len(out.pages)
    out.picture = draw_page(out.title, text)
    return out


def decode_text(data: bytes) -> str:
    """A text file's characters: by its byte-order mark, else UTF-8, else Windows' Western code page."""
    for bom, codec in ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"),
                       (codecs.BOM_UTF16_BE, "utf-16")):
        if data.startswith(bom):
            return data.decode(codec, "replace")
    if data[:200].count(0) > 40:  # UTF-16 without a mark: every other byte of English is 0
        return data.decode("utf-16-le", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


class _HtmlText(HTMLParser):
    """A web page's words, as they read: no scripts, styles or markup."""
    SKIP = {"script", "style", "head", "noscript", "template", "svg", "iframe", "object"}
    BLOCKS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table",
              "blockquote", "pre", "hr", "dd", "dt"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skipping = 0
        self.in_title = False
        self.title = ""

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        elif tag in self.SKIP:
            self.skipping += 1
        elif tag in self.BLOCKS:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        elif tag in self.SKIP:
            self.skipping = max(0, self.skipping - 1)
        elif tag in self.BLOCKS:
            self.out.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skipping:
            self.out.append(data)


def html_text(page: str) -> tuple[str | None, str]:
    """(its title, its words) of a web page."""
    parser = _HtmlText()
    try:
        parser.feed(page[:MOST_TEXT * 4])
        parser.close()
    except Exception:  # HTMLParser is lenient; anything left is a page too broken to read further
        pass
    title = " ".join(parser.title.split())[:200] or None
    return title, "".join(parser.out)


# RTF: control words, \'hh characters, \uN characters, control symbols, braces, and plain text
_RTF = re.compile(r"\\([a-z]{1,32})(-?\d{1,10})?[ ]?|\\'([0-9a-f]{2})|\\([^a-z])|([{}])|[\r\n]+|(.)", re.I | re.S)
_RTF_SKIP = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "header", "headerl", "headerr", "headerf",
             "footer", "footerl", "footerr", "footerf", "object", "themedata", "datastore", "xmlnstbl", "listtable",
             "listoverridetable", "rsidtbl", "generator", "latentstyles", "filetbl", "revtbl", "pgdsctbl", "fldinst",
             "colorschememapping", "mmathPr", "wgrffmtfilter", "background", "shp", "nonshppict", "bkmkstart",
             "bkmkend", "footnote", "annotation", "atnid", "atnauthor"}
_RTF_CHARS = {"par": "\n", "sect": "\n\n", "page": "\n\n", "line": "\n", "tab": "\t", "cell": "\t", "row": "\n",
              "emdash": "—", "endash": "–", "emspace": " ", "enspace": " ", "bullet": "•", "lquote": "‘",
              "rquote": "’", "ldblquote": "“", "rdblquote": "”"}


def rtf_text(rtf: str) -> str:
    """The words of an RTF file, without its formatting."""
    stack, skipping, uc, skip_chars, out = [], False, 1, 0, []
    for m in _RTF.finditer(rtf[:MOST_TEXT * 4]):
        word, arg, hexa, symbol, brace, char = m.groups()
        if brace:
            skip_chars = 0
            if brace == "{":
                stack.append((uc, skipping))
            elif stack:
                uc, skipping = stack.pop()
        elif symbol:
            skip_chars = 0
            if symbol == "*":
                skipping = True
            elif not skipping:
                out.append({"~": "\xa0", "-": "", "_": "-"}.get(symbol, symbol if symbol in "{}\\" else ""))
        elif word:
            skip_chars = 0
            if word in _RTF_SKIP:
                skipping = True
            elif skipping:
                pass
            elif word in _RTF_CHARS:
                out.append(_RTF_CHARS[word])
            elif word == "uc" and arg:
                uc = int(arg)
            elif word == "u" and arg:
                code = int(arg) + (0x10000 if int(arg) < 0 else 0)
                out.append(chr(code) if 0 < code < 0x110000 else "")
                skip_chars = uc
        elif hexa:
            if skip_chars > 0:
                skip_chars -= 1
            elif not skipping:
                out.append(bytes([int(hexa, 16)]).decode("cp1252", "replace"))
        elif char:
            if skip_chars > 0:
                skip_chars -= 1
            elif not skipping:
                out.append(char)
    return "".join(out)


# ---- words and pages -------------------------------------------------------------------------------------------------

def tidy(text: str) -> str:
    """Words as they are kept: each line's spaces tidied, no more than one empty line in a row, at most MOST_TEXT."""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    lines = [" ".join(line.replace("\t", "    ").split()) if line.strip() else "" for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()[:MOST_TEXT]


def split_pages(text: str, size: int = PAGE_CHARS) -> list[str]:
    """A text file's words in pages of about `size` characters, broken between paragraphs (or lines, or words, when
    one runs longer). At least one page, even when it has no words."""
    pages, page = [], ""
    for part in re.split(r"(?<=\n)", text):
        while len(part) > size:  # a paragraph longer than a page: broken at a space
            cut = part.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            pages.append((page + part[:cut]).strip())
            page, part = "", part[cut:]
        if len(page) + len(part) > size and page.strip():
            pages.append(page.strip())
            page = ""
        page += part
    if page.strip() or not pages:
        pages.append(page.strip())
    return pages[:MOST_PAGES]


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except (TypeError, OSError):  # a Pillow without FreeType: its small built-in font
        return ImageFont.load_default()


def draw_page(title: str | None, text: str, note: str | None = None) -> Image.Image:
    """A paper's first page as a picture (for its tile and preview): its title, then as much of its words as fit."""
    width, height = PAGE_SIZE
    im = Image.new("RGB", PAGE_SIZE, (252, 251, 247))
    draw = ImageDraw.Draw(im)
    margin, y = 110, 110
    big, small = _font(46), _font(30)
    for line in textwrap.wrap(title or "", 40)[:3]:
        draw.text((margin, y), line, fill=(30, 30, 30), font=big)
        y += 60
    if note:
        draw.text((margin, y + 30), note, fill=(110, 110, 110), font=small)
    y += 50
    for paragraph in (text or "").split("\n"):
        for line in textwrap.wrap(paragraph, 62) or [""]:
            if y > height - margin:
                return im
            draw.text((margin, y), line, fill=(60, 60, 60), font=small)
            y += 40
    return im


def words_in(text: str | None) -> int:
    return len(_WORD.findall(text or ""))
