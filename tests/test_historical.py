"""Historical papers (papers.py, historical.py, paper_reading.py, web/routes/historical.py): files that are papers read
for their words, scans put together page by page, the AI copying out pages and finding papers among photos, the
Historical page and a paper's own, and papers kept out of the photo places."""
import io
import json
import math
import zipfile
from datetime import date
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from lineagebox import (accounts, catalog, changes, dedup, historical, llm, memories, paper_reading, paper_search,
                        papers, private, reorganize, search, settings)
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests.conftest import LAN, rel
from tests.test_face_checks import photo

APP = {"X-Requested-With": "lineagebox"}
LETTER = ("Dear Ada,", "The farm on Mill Road was sold on Tuesday to the Bakers for four hundred dollars.",
          "Mother sends her love, and the apple trees are in bloom again this spring.")


def docx(paragraphs, title="", created="1998-04-02T10:00:00Z") -> bytes:
    """A Word file as Word writes one, with just what LineageBox reads of it (and a text box Word keeps twice)."""
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    mc = 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    body += ("<w:p><mc:AlternateContent><mc:Choice><w:r><w:t>In the box</w:t></w:r></mc:Choice>"
             "<mc:Fallback><w:r><w:t>In the box</w:t></w:r></mc:Fallback></mc:AlternateContent></w:p>")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("word/document.xml", f'<w:document {w} {mc}><w:body>{body}</w:body></w:document>')
        z.writestr("docProps/core.xml",
                   '<cp:coreProperties xmlns:cp="x" xmlns:dc="http://purl.org/dc/elements/1.1/" '
                   f'xmlns:dcterms="http://purl.org/dc/terms/"><dc:title>{title}</dc:title>'
                   f'<dcterms:created>{created}</dcterms:created></cp:coreProperties>')
    return out.getvalue()


def odt(paragraphs) -> bytes:
    t = 'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
    body = "".join(f"<text:p>{p.replace('  ', '<text:s/> ')}</text:p>" for p in paragraphs)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("content.xml", f"<office:document-content xmlns:office='o' {t}><office:body>{body}"
                                  "</office:body></office:document-content>")
    return out.getvalue()


def email_file() -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "Ada Miller <ada@example.com>", "Ben Baker <ben@example.com>", "The reunion"
    m["Date"] = "Thu, 03 Jun 2004 10:00:00 -0400"
    m.set_content("We will meet at the lake house in July. Bring the old photo albums and the family Bible.")
    return bytes(m)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


# ---- reading files ----------------------------------------------------------------------------------------------------

def test_each_kind_of_file_is_read_for_its_words(tmp_path):
    r = papers.read_file(write(tmp_path / "Letter_to-Ada.docx", docx(LETTER, "To Ada")), ".docx")
    assert r.title == "To Ada" and r.written == "1998-04-02" and r.unreadable is None
    assert r.pages[0].startswith("Dear Ada,\nThe farm on Mill Road") and r.pages[0].count("In the box") == 1
    assert r.picture.size == papers.PAGE_SIZE

    r = papers.read_file(write(tmp_path / "Recipes.odt", odt(["Apple pie", "Two  cups of flour"])), ".odt")
    assert r.title == "Recipes" and r.pages == ["Apple pie\nTwo cups of flour"]

    # written with ~ for each backslash of the RTF, swapped below
    rtf = ("{~rtf1~ansi{~fonttbl{~f0 Times;}}{~*~generator Word;}~f0 Dear Ada,~par The caf~'e9 is "
           "~u8220?open~u8221?.~par}").replace("~", chr(92))
    r = papers.read_file(write(tmp_path / "note.rtf", rtf), ".rtf")
    assert r.pages == ["Dear Ada,\nThe café is “open”."]

    r = papers.read_file(write(tmp_path / "reunion.eml", email_file()), ".eml")
    assert (r.title, r.written, r.format) == ("The reunion", "2004-06-03", "eml")
    assert r.sender.startswith("Ada Miller") and r.to.startswith("Ben Baker")
    assert "From: Ada Miller" in r.pages[0] and "lake house in July" in r.pages[0]

    html = ("<html><head><title>Obituary of Ada Miller</title><script>alert(1)</script></head><body><h1>Ada</h1>"
            f"<p>{' '.join(['Ada was born on the farm and loved her family.'] * 6)}</p><style>p {{}}</style></body>")
    r = papers.read_file(write(tmp_path / "obit.html", html), ".html")
    assert r.title == "Obituary of Ada Miller" and "alert" not in r.pages[0] and "Ada was born" in r.pages[0]

    long = " ".join(f"Line {n} of the family history, as Grandpa told it." for n in range(600))
    r = papers.read_file(write(tmp_path / "history.txt", long), ".txt")
    assert len(r.pages) > 3 and all(len(p) <= papers.PAGE_CHARS for p in r.pages) and r.page_count == len(r.pages)
    assert " ".join(r.pages).split() == long.split()  # nothing lost between pages

    # too few words for a paper: a camera's readme, a gallery's index page
    assert papers.read_file(write(tmp_path / "readme.txt", "Do not delete"), ".txt").not_a_paper
    assert papers.read_file(write(tmp_path / "index.html", "<img src=a.jpg><img src=b.jpg>"), ".html").not_a_paper
    # kept to download: an old Word file; damaged ones are recorded as damaged
    r = papers.read_file(write(tmp_path / "Will.doc", b"\xd0\xcf\x11\xe0 old word"), ".doc")
    assert r.unreadable and r.pages == [] and r.title == "Will" and r.picture is not None
    with pytest.raises(papers.Damaged):
        papers.read_file(write(tmp_path / "broken.docx", b"not a zip"), ".docx")
    assert papers.decode_text("Grüße".encode("utf-16")) == "Grüße" and papers.decode_text(b"caf\xe9") == "café"


def test_a_pdf_is_read_page_by_page(tmp_path):
    pdfium = pytest.importorskip("pypdfium2")
    pdf = pdfium.PdfDocument.new()
    for _ in range(2):
        pdf.new_page(612, 792)
    pdf.save(str(tmp_path / "deed.pdf"))
    r = papers.read_file(tmp_path / "deed.pdf", ".pdf")
    assert r.page_count == 2 and r.pages == [None, None]  # no words of their own: pictures for the AI to read
    assert r.picture is not None and r.title == "deed"
    assert papers.pdf_page(tmp_path / "deed.pdf", 2, 100).size[1] == 100
    with pytest.raises(papers.Damaged):
        papers.read_file(write(tmp_path / "bad.pdf", b"%PDF-1.4 nonsense"), ".pdf")


# ---- papers in the catalog ------------------------------------------------------------------------------------------

def test_paper_files_become_papers(con, cfg, library):
    write(library / "Letters" / "To Ada.docx", docx(LETTER))
    write(library / "Letters" / "reunion.eml", email_file())
    write(library / "Letters" / "Will.wpd", b"WPC old")
    write(library / "Letters" / "log.txt", "\n".join(f"{n:08d} 00:00:{n:02d}" for n in range(20)))
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    rows = {r["format"]: r for r in con.execute("""SELECT i.*, d.kind, d.title, d.source, d.unreadable, d.sender
                                                   FROM image i JOIN document d ON d.image_id = i.id""")}
    assert set(rows) == {"docx", "eml", "wpd"} and all(r["media"] == "document" for r in rows.values())
    assert rows["eml"]["kind"] == "Email" and rows["eml"]["sender"].startswith("Ada Miller")
    assert rows["wpd"]["unreadable"] and rows["docx"]["source"] == "auto" and rows["docx"]["phash"] is None
    assert cfg.thumb_path(rows["docx"]["sha256"]).is_file()  # its first page drawn, for its tile
    pages = historical.pages(con, rows["docx"]["id"])
    assert len(pages) == 1 and pages[0]["text_source"] == "file" and "Mill Road" in pages[0]["text"]
    # the library's notes.txt ("not a photo") is too small to be one: not even scanned; a text of numbers is read,
    # found to be no paper, and left out as a song is, not called damaged
    assert con.execute("SELECT 1 FROM file WHERE path = ?", (rel("notes.txt"),)).fetchone() is None
    numbers = con.execute("SELECT i.error FROM file f JOIN image i ON i.id = f.image_id WHERE f.path = ?",
                          (rel("Letters", "log.txt"),)).fetchone()
    assert numbers["error"] == papers.NOT_A_PAPER and not any(r["path"].endswith("log.txt")
                                                               for r in catalog.undecodable(con))
    # nothing for the faces or describing stages; found by their words
    assert con.execute("SELECT count(*) FROM image WHERE media = 'document' AND error IS NULL AND stage != 7"
                       ).fetchone()[0] == 0
    search.index_new(con)
    assert [r["id"] for r in paper_search.query(con, "apple trees")] == [rows["docx"]["id"]]
    assert [r["id"] for r in paper_search.query(con, "lake house")] == [rows["eml"]["id"]]
    assert search.query(con, "apple trees") == []  # the photo search leaves papers out
    facets = historical.facets(con)
    assert facets["total"] == 3 and [c["name"] for c in facets["collections"]] == ["Letters"]
    assert dict(facets["kinds"]) == {"Other": 2, "Email": 1} and dict(facets["decades"]) == {"1990": 1, "2000": 1,
                                                                                             "undated": 1}
    assert [p["title"] for p in historical.listing(con, decade="2000")] == ["The reunion"]


def scans(con, n=3, folder=5):
    return [photo(con, 100 + k, "2026-11-25T10:00:00", "filename", folder=folder, path=f"f{folder}/Scan_{k:03d}.jpg")
            for k in range(n)]


def test_scans_put_together_as_one_paper(con):
    first, second, third = scans(con)
    historical.make(con, first, "Letter", "Letter to Ada", text="Dear Ada,")
    historical.make(con, third, "Letter", "Its last page", text="Your loving sister")  # read as a paper of its own
    citation = con.execute("INSERT INTO citation(image_id, person_id) VALUES (?, ?)",
                           (third, con.execute("INSERT INTO person(name) VALUES ('Ada Miller')").lastrowid)).lastrowid
    assert [b["id"] for b in historical.beside(con, first)] == [second, third]
    assert historical.add_pages(con, first, [second, third]) == 2
    pages = historical.pages(con, first)
    assert [(p["n"], p["image_id"], p["text"]) for p in pages] == [(1, first, "Dear Ada,"), (2, second, None),
                                                                    (3, third, "Your loving sister")]
    assert historical.get(con, third) is None  # a paper no more: its words came along, and what it was a record of
    assert con.execute("SELECT image_id FROM citation WHERE id = ?", (citation,)).fetchone()[0] == first
    # its later pages leave every list, and lead to it
    shown = {r[0] for r in con.execute(f"SELECT i.id FROM image i WHERE {catalog.VISIBLE}")}
    assert first in shown and not {second, third} & shown
    assert [r["id"] for r in catalog.folder_images(con, 5)] == [first]
    assert historical.paper_of(con, third) == first and historical.paper_of(con, first) == first
    historical.move_page(con, first, 3, 2)
    assert [p["image_id"] for p in historical.pages(con, first)] == [first, third, second]
    with pytest.raises(historical.Problem):
        historical.move_page(con, first, 2, 1)  # the first page is the paper itself
    assert historical.take_out(con, first, 2) == third
    assert [p["image_id"] for p in historical.pages(con, first)] == [first, second]
    alone = historical.pages(con, third)
    assert len(alone) == 1 and alone[0]["text"] == "Your loving sister"
    assert historical.get(con, third)["title"] == "Letter to Ada (page 2)"
    with pytest.raises(historical.Problem):
        historical.not_a_paper(con, second)  # a page of another: taken out first
    historical.not_a_paper(con, first)
    assert historical.get(con, first) is None and catalog.image(con, second)["page_of"] is None
    assert tuple(con.execute("SELECT is_paper, by FROM paper_check WHERE image_id = ?", (first,)).fetchone()) ==            (0, "user")


def test_what_a_person_says_of_a_paper(con):
    (first,) = scans(con, 1)
    historical.mark(con, first, "Letter", "To Ada")
    historical.set_details(con, first, historical.Details("Letter", "To Ada", "June 3, 1931", "Ben", "Ada"))
    paper = historical.get(con, first)
    assert (paper["written_from"], paper["written_to"], paper["sender"], paper["source"]) == \
           ("1931-06-03", "1931-06-03", "Ben", "user")
    historical.set_details(con, first, historical.Details("Letter", "Old", "1776"))  # papers go back before photos
    assert historical.get(con, first)["written_from"] == "1776-01-01"
    with pytest.raises(historical.Problem):
        historical.set_details(con, first, historical.Details("Letter", "x", "the day after the storm"))
    historical.set_text(con, first, 1, "  Dear   Ada  ")
    page = historical.pages(con, first)[0]
    assert (page["text"], page["text_source"]) == ("Dear Ada", "user")
    with pytest.raises(historical.Problem):
        historical.read_again(con, first, 1)  # a person's words are kept


# ---- the AI reading ---------------------------------------------------------------------------------------------------

def tokens_of(content: str, unsure_word: str = "") -> list:
    """An answer's tokens, a word at a time, each sure but the unsure one."""
    out, word = [], ""
    for ch in content:
        word += ch
        if ch in ' ",\n:{}':
            out.append(word)
            word = ""
    out.append(word)
    return [{"token": t, "logprob": math.log(0.05 if unsure_word and unsure_word in t else 0.97)} for t in out if t]


class Reader:
    """The AI reading pages: answers by the preview it is sent (each a solid color: the image's id), with tokens."""
    model = "reader"

    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def chat_json(self, system, user, schema, images=(), temperature=0.1, max_tokens=1024, logprobs=0):
        with Image.open(io.BytesIO(images[0])) as im:
            key = im.convert("RGB").getpixel((0, 0))[0]
        self.asked.append((key, "is_document" in schema["properties"]))
        found = self.answers[key]
        if isinstance(found, Exception):
            raise found
        content = json.dumps(found)
        return found, llm.Usage(0, 0, 0.0, tokens_of(content, found.get("unsure_word", "")))

    def model_problem(self):
        return None


def preview(cfg, con, image_id, shade):
    sha = catalog.image(con, image_id)["sha256"]
    path = cfg.preview_path(sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (30, 40), (shade, 0, 0)).save(path, "JPEG", quality=100)


def described(con, image_id, photo_type="document/screenshot"):
    con.execute("""INSERT INTO analysis(image_id, model, prompt_version, created_at, photo_type, raw_json)
                   VALUES (?, 'm', 1, '2026-11-25', ?, '{}')""", (image_id, photo_type))


def test_the_ai_reads_pages_and_finds_papers(con, cfg, monkeypatch):
    letter, shot, phone, grouped_first, grouped_next, portrait = (
        photo(con, n, "2026-11-25T10:00:00", "filename", path=f"f1/{name}")
        for n, name in ((10, "scan10.jpg"), (20, "Screenshot_2024.png"), (30, "IMG_30.png"), (40, "a.jpg"),
                        (50, "b.jpg"), (60, "p.jpg")))
    con.execute("UPDATE image SET format = 'png', width = 1080, height = 2400 WHERE id = ?", (phone,))  # a phone's screen
    for image_id in (letter, shot, phone, portrait):  # all called a document by the describing AI
        described(con, image_id)
    historical.mark(con, grouped_first, "Letter", "Two pages")  # by hand: its words read, the kind kept
    historical.add_pages(con, grouped_first, [grouped_next])
    con.commit()
    for image_id, shade in ((letter, 10), (grouped_first, 40), (grouped_next, 50), (portrait, 60)):
        preview(cfg, con, image_id, shade)
    assert paper_reading.candidates(con) == [portrait, letter]  # not the screenshot by name, nor the phone's screen
    reader = Reader({
        10: {"is_document": True, "kind": "Letter", "title": "Letter to Ada", "written": "June 3, 1931",
             "language": "English", "handwritten": True, "text": "Dear Ada,\nThe farm is sold.\nLove, Ben",
             "unsure_word": "farm"},
        40: {"blank": False, "text": "Dear Ada,\nIt rained.\nIt rained.\nIt rained.\nIt rained.\nIt rained."},
        50: ValueError("not JSON"),
        60: {"is_document": False, "kind": "Other", "title": "", "written": "", "language": "", "handwritten": False,
             "text": ""}})
    monkeypatch.setattr(paper_reading, "client_for", lambda cfg_: reader)
    assert paper_reading.read_waiting(con, cfg, stop=lambda: True) == (0, 0) and reader.asked == []
    assert paper_reading.read_waiting(con, cfg) == (1, 2)
    assert (40, False) in reader.asked and (50, False) in reader.asked and (10, True) in reader.asked
    page = historical.pages(con, grouped_first)[0]
    assert page["text"] == "Dear Ada,\nIt rained." and page["unsure"] == [1]  # cut where it began repeating itself
    assert historical.get(con, grouped_first)["kind"] == "Letter"  # said by hand: the AI reads its words only
    assert historical.pages(con, grouped_first)[1]["text"] is None  # couldn't be read: tried again later
    paper = historical.get(con, letter)
    assert (paper["kind"], paper["title"], paper["written_from"], paper["source"]) == \
           ("Letter", "Letter to Ada", "1931-06-03", "auto")
    read = historical.pages(con, letter)[0]
    assert read["text"] == "Dear Ada,\nThe farm is sold.\nLove, Ben" and read["unsure"] == [1]
    assert historical.get(con, portrait) is None
    assert tuple(con.execute("SELECT is_paper, by, model FROM paper_check WHERE image_id = ?",
                             (portrait,)).fetchone()) == (0, "ai", "reader")
    assert paper_reading.candidates(con) == []  # each asked once
    assert [(r["paper_id"], r["n"]) for r in paper_reading.pages_waiting(con)] == []  # the failed one waits a while
    # a person's word wins: no paper is made of a photo they said is none
    historical.not_a_paper(con, letter)
    con.commit()
    assert paper_reading.candidates(con) == []


def test_how_sure_each_line_was():
    content = json.dumps({"blank": False, "text": "Dear Ada,\nThe farm is sold.\n\nLove"})
    sure = paper_reading.line_sureness(tokens_of(content, "farm"), "Dear Ada,\nThe farm is sold.\n\nLove")
    assert len(sure) == 4 and sure[0] > 0.9 and sure[1] < 0.1 and sure[3] > 0.9
    assert paper_reading.line_sureness([], "x") is None
    text, unsure = paper_reading.tidy_reading("Dear Ada,\nThe farm is sold.\n\nLove", sure)
    assert unsure == [1] and text == "Dear Ada,\nThe farm is sold.\n\nLove"
    # a handwritten date read as another alphabet's digits (as ministral-3 read one, 2026-11-26): unsure, whatever
    # the odds; a page written in that alphabet is not
    persian = "".join(chr(0x06F0 + d) for d in (3, 1, 0, 2, 0, 0, 4))
    assert paper_reading.tidy_reading(f"Name: John Baker\nDied: {persian}\nin: Bay City", None)[1] == [1]
    cyrillic = "".join(chr(0x0410 + k) for k in range(8))
    assert paper_reading.tidy_reading(f"{cyrillic}\n{cyrillic} 1931", None)[1] == []


# ---- the pages people see ---------------------------------------------------------------------------------------------

def test_the_historical_pages(con, cfg, library):
    write(library / "Letters" / "To Ada.docx", docx(LETTER, "To Ada"))
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    first, second = scans(con, 2, folder=1)
    historical.make(con, first, "Letter", "Two pages", text="Dear Ada,\nThe farm is sold.", unsure=[1])
    con.commit()
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get("/historical").text
    assert "To Ada" in page and "Two pages" in page and 'href="/historical"' in page
    assert "Lines the AI wasn't sure of" in page  # the paper with an unsure line, to check
    to_check = client.get("/historical?check=1").text
    assert "Two pages" in to_check and "To Ada" not in to_check and f"/historical/{first}#page-1" in to_check
    assert "Two pages" in client.get("/historical?kind=Letter").text
    assert "To Ada" not in client.get("/historical?kind=Letter").text
    paper = client.get(f"/historical/{first}").text
    assert '<p class="unsure">The farm is sold.</p>' in paper and "Copied out by the AI" in paper
    assert f'value="{second}"' in paper  # the scan after it, to add as its next page
    r = client.post(f"/api/historical/{first}/pages", json={"images": [second]}, headers=APP)
    assert r.status_code == 200 and r.json()["undo"]
    assert client.get(f"/historical/{second}", follow_redirects=False).headers["location"] == \
           f"/historical/{first}#page-2"
    r = client.post(f"/api/historical/{first}/pages/1/text", json={"text": "Dear Ada,\nThe farm is sold!"},
                    headers=APP)
    assert historical.pages(con, first)[0]["text_source"] == "user"
    changes.undo(con, r.json()["undo"])
    assert historical.pages(con, first)[0]["text"] == "Dear Ada,\nThe farm is sold."
    r = client.post(f"/api/historical/{first}/details", json={"kind": "Letter", "title": "From Ben",
                                                              "written": "spring 1931"}, headers=APP)
    assert r.status_code == 200 and historical.get(con, first)["written_from"] == "1931-03-01"
    assert client.post(f"/api/historical/{first}/details", json={"kind": "Letter", "written": "someday"},
                       headers=APP).status_code == 400
    assert client.post(f"/api/historical/{first}/pages/2/take-out", headers=APP).status_code == 200
    assert historical.paper_of(con, second) == second
    # the photo page's own "Is this a document?" still works, through the same papers
    assert client.post(f"/api/images/{second}/document/remove", headers=APP).status_code == 200
    assert historical.get(con, second) is None
    assert 'href="/historical"' in client.get("/people").text  # in the menu


def test_members_see_papers_as_photos(con, cfg):
    emma = con.execute("INSERT INTO person(name) VALUES ('Emma Miller')").lastrowid
    zed = con.execute("INSERT INTO person(name) VALUES ('Zed Stranger')").lastrowid
    hers, theirs, page2 = scans(con, 3, folder=1)
    historical.make(con, hers, "Letter", "Letter to Emma", text="Dear Emma")
    historical.add_pages(con, hers, [page2])
    historical.make(con, theirs, "Letter", "Letter to Zed", text="Dear Zed")
    con.execute("INSERT INTO image_person(image_id, person_id, added_at) VALUES (?, ?, '2026-01-01')", (hers, emma))
    con.execute("INSERT INTO image_person(image_id, person_id, added_at) VALUES (?, ?, '2026-01-01')", (theirs, zed))
    con.commit()
    accounts.add(con, "admin@example.com", None, "admin")
    member = accounts.add(con, "emma@example.com", emma)
    settings.save(con, cfg, {"accounts_enabled": True})
    c = TestClient(webapp.create_app(cfg), client=LAN, follow_redirects=False)
    c.cookies.set(accounts.COOKIE, accounts.new_session(con, member, 30))
    page = c.get("/historical").text
    assert "Letter to Emma" in page and "Letter to Zed" not in page
    assert c.get(f"/historical/{hers}").status_code == 200 and c.get(f"/historical/{theirs}").status_code == 404
    sha = catalog.image(con, page2)["sha256"]
    assert f"/p/{sha}" in c.get(f"/historical/{hers}").text  # her paper's later page, which no list shows
    assert 'href="/historical"' in c.get("/people").text
    assert c.post(f"/api/historical/{theirs}/pages/1/text", json={"text": "x"}, headers=APP).status_code == 404


def test_papers_stay_out_of_the_photo_places(con, cfg):
    ids = [photo(con, n, f"2015-06-0{n}T10:00:00", "exif", path=f"f1/{n}.jpg") for n in range(1, 6)]
    con.execute("UPDATE image SET phash = 12345")  # all alike to a fingerprint, as printed forms are
    for n, paper in enumerate(ids[:4]):
        historical.make(con, paper, "Letter", f"Letter {n}")
    con.commit()
    dedup.group(con)
    assert con.execute("SELECT count(*) FROM image WHERE dup_group_id IS NOT NULL").fetchone()[0] == 0
    assert [r["id"] for r in catalog.year_images(con, 2015)] == [ids[4]]
    assert catalog.timeline(con) == {2015: {6: 1}}
    # five photos that week, four of them papers: too few photos for a memory (memories.LEAST)
    assert memories.years_ago(con, date(2026, 6, 3)) == []


def test_reorganizing_leaves_papers_where_they_are(con, cfg, library):
    write(library / "Letters" / "To Ada.docx", docx(LETTER))
    scan(con, cfg)
    ingest(con, cfg, show_progress=False)
    plan = reorganize.plan(con, cfg, by_type=False)
    assert not any(m.old.endswith("To Ada.docx") for m in plan.moves)
    assert not any(m.new.endswith("To Ada.docx") for m in plan.moves)


def test_private_papers_by_their_words(con):
    ssn, old, deed = scans(con, 3)
    historical.make(con, ssn, "Work record", "Pay stub", text="Employee 123-45-6789")
    historical.make(con, old, "Birth certificate", "A birth, 1890")
    con.execute("UPDATE document SET written_to = '1890-01-01' WHERE image_id = ?", (old,))
    historical.make(con, deed, "Property record", "Deed", text="The farm on Mill Road")
    con.commit()
    found = {f["id"]: f["kind"] for f in private.suggestions(con)}
    assert found == {ssn: "id"}
