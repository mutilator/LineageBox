"""Scans dated by the day they were scanned (scan_dates.py, Cleanup → Suggested dates): which documents and photos are
listed, what can tell their real date (a date written on them, the AI's reading, the family tree, who is in them),
dating one from the list, the AI's reading on request and in the worker, and the page."""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from lineagebox import changes, documents, events, llm, people, scan_dates, settings
from lineagebox.web import app as webapp
from tests.conftest import LAN
from tests.test_face_checks import A, B, born, face, like, photo

APP = {"X-Requested-With": "lineagebox"}


def described(con, image_id, photo_type="document/screenshot", text=None, at="2026-11-07T10:00:00"):
    con.execute("""INSERT INTO analysis(image_id, model, prompt_version, created_at, photo_type, text_visible, raw_json)
                   VALUES (?, 'm', 1, ?, ?, ?, '{}')""", (image_id, at, photo_type, text))


def library(con):
    """A folder of scans (named by the scanning app), a scanner's photos, a phone's photos of papers, and others."""
    scans = "lib/ScannedPhotos/Image_20261107_"
    ids = {"letter": photo(con, 1, "2026-11-07T14:30:00", "filename", folder=5, path=scans + "143000.tif"),
           "print": photo(con, 2, "2026-11-07T14:31:00", "filename", folder=5, path=scans + "143100.tif"),
           "flatbed": photo(con, 3, "2009-03-01T10:00:00", "exif", folder=6, path="lib/Misc/x0003.jpg",
                            camera="HP ScanJet 3500"),
           "receipt": photo(con, 4, "2021-05-05T12:00:00", "exif", folder=7, path="lib/Phone/PXL_1.jpg",
                            camera="Google Pixel"),
           "certificate": photo(con, 5, "2021-05-06T12:00:00", "exif", folder=7, path="lib/Phone/PXL_2.jpg",
                                camera="Google Pixel"),
           "undated": photo(con, 6, "2015-01-01T00:00:00", "mtime", folder=8, path="lib/Papers/note.jpg"),
           "dated": photo(con, 7, "1950-06-01T00:00:00", "user", folder=5, path=scans + "143200.tif"),
           "beach": photo(con, 8, "2019-07-04T12:00:00", "exif", folder=7, path="lib/Phone/PXL_3.jpg",
                          camera="Google Pixel"),
           "ultrasound": photo(con, 9, "2014-02-01T00:00:00", "folder", folder=9, path="lib/2014/Feb/scan1.jpg")}
    described(con, ids["letter"], text="Dear Ruth, Saturday, June 3rd, 1952. We arrived safe.")
    described(con, ids["print"], "portrait")
    described(con, ids["flatbed"], "document/screenshot")
    described(con, ids["receipt"], "document/screenshot", "TOTAL $4.99 05/05/2021")
    described(con, ids["undated"], "document/screenshot")
    described(con, ids["dated"], "document/screenshot")
    described(con, ids["beach"], "candid")
    described(con, ids["ultrasound"], "document/screenshot")
    documents.save(con, ids["certificate"], "Marriage certificate", "")
    con.commit()
    return ids


def test_which_scans_are_listed(con):
    ids = library(con)
    found = scan_dates.scans(con)
    docs = {d["id"]: d["found"] for d in found["documents"]}
    assert docs == {ids["letter"]: "folder", ids["flatbed"]: "scanner", ids["certificate"]: "marked",
                    ids["undated"]: "no date", ids["ultrasound"]: "folder"}  # "scan" in its file name
    # a document dated by its folder, with nothing to say it was scanned: the folder's date is someone's word
    con.execute("UPDATE file SET path = 'lib/2014/Feb/x1.jpg' WHERE image_id = ?", (ids["ultrasound"],))
    assert ids["ultrasound"] not in {d["id"] for d in scan_dates.scans(con)["documents"]}
    # a phone's photo of a receipt is dated right; a date set by hand is the family's; a beach photo isn't a scan
    assert [p["id"] for p in found["photos"]] == [ids["print"]]
    assert scan_dates.counts(con) == {"documents": 4, "photos": 1, "young": 0}
    settings.put(con, scan_dates.OK_KEY, [ids["undated"]])  # its date is right, an admin said
    assert ids["undated"] not in {d["id"] for d in scan_dates.scans(con)["documents"]}


@pytest.mark.parametrize("text, want", [
    ("Saturday, June 3rd, 1952", ["Jun 3, 1952"]), ("the 5th of July 1955", ["Jul 5, 1955"]),
    ("Sept. 1944", ["September 1944"]), ("about 1950", ["about 1950"]), ("12-25-1961", ["Dec 25, 1961"]),
    ("Dear Ruth, it was June 3, 1952. Love, 1938", ["Jun 3, 1952"]), ("printed 1938", ["1938"]),
    ("born 1920 died 1999", []),  # two years alone: a life's span, not the document's date
    ("no date here", []), ("June 2099", []),  # to come
])
def test_dates_found_in_words(text, want):
    assert [s.text for s in scan_dates.found_dates(text)] == want


def test_day_first_where_the_family_writes_dates_so():
    assert [s.text for s in scan_dates.found_dates("3/6/1952", day_first=True)] == ["Jun 3, 1952"]
    assert [s.text for s in scan_dates.found_dates("3/6/1952")] == ["Mar 6, 1952"]


def test_what_tells_a_scans_date(con):
    ids = library(con)
    found = {d["id"]: d for d in scan_dates.scans(con)["documents"]}
    said = lambda key: scan_dates.suggestions(con, found[ids[key]])
    assert said("letter")[0] == {"text": "Jun 3, 1952", "label": "Jun 3, 1952",
                                 "why": "The AI's description saw it written on it."}
    # the AI's reading comes first; each date once
    con.execute("""INSERT INTO scan_reading(image_id, model, version, read_at, dates) VALUES (?, 'm', 1, '', ?)""",
                (ids["letter"], json.dumps([{"written": "June 1, 1952", "what": "the letter's date"},
                                            {"written": "June 3rd, 1952", "what": "arrived"}])))
    assert [(s["text"], s["why"]) for s in said("letter")] == [
        ("Jun 1, 1952", "It says “June 1, 1952” (the letter's date)."),
        ("Jun 3, 1952", "It says “June 3rd, 1952” (arrived).")]
    # the family tree: a marriage certificate of someone takes their marriage's date
    ruth, tom = people.person_id(con, "Ruth Baker"), people.person_id(con, "Tom Baker")
    con.execute("INSERT INTO partner(a, b) VALUES (?, ?)", (min(ruth, tom), max(ruth, tom)))
    events.save(con, None, tag="MARR", couple=(ruth, tom), date_text="1951-06-09")
    documents.cite(con, ids["certificate"], f"p:{ruth}")
    assert said("certificate") == [{"text": "Jun 9, 1951", "label": "Jun 9, 1951",
                                    "why": "It is Ruth Baker’s marriage certificate: the tree has the date of the "
                                           "marriage it records."}]
    # an event it is cited for
    birth = events.save(con, None, tag="BIRT", person_id=tom, date_text="May 1925")
    documents.save(con, ids["undated"], "Other", "")
    documents.cite(con, ids["undated"], f"e:{birth}")
    assert [(s["text"], s["why"]) for s in said("undated")] == [("May 1925", "It is cited for Tom Baker: born.")]
    # who is in a scanned print: between a birth and a death close together, else "not before"
    kid = people.person_id(con, "Owen Miller")
    born(con, kid, "1975-03-01")
    face(con, ids["print"], like(A), kid, "user")
    con.commit()
    print_item = scan_dates.scans(con)["photos"][0]
    assert scan_dates.suggestions(con, print_item) == [{"text": None, "label": "Not before 1975",
                                                        "why": "Owen Miller, in it, was born in 1975."}]
    con.execute("INSERT INTO event(person_id, tag, date, sort_date) VALUES (?, 'DEAT', '1977', '1977')", (kid,))
    assert scan_dates.suggestions(con, print_item)[0]["text"] == "between 1975 and 1977"


class FakeOllama:
    model = "vision-model"

    def __init__(self, answer=None, fail=None):
        self.answer, self.fail, self.asked = answer, fail, []

    def chat_json(self, system, user, schema, images=(), temperature=0.1, max_tokens=1024):
        self.asked.append(len(images))
        if self.fail:
            raise self.fail
        return self.answer, llm.Usage(1, 1, 1.0)


def previews(cfg, con, *image_ids):
    from PIL import Image
    for i in image_ids:
        sha = con.execute("SELECT sha256 FROM image WHERE id = ?", (i,)).fetchone()[0]
        path = cfg.preview_path(sha)
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (40, 30), "white").save(path, "JPEG")


def test_the_ai_reads_the_dates_written_on_it(con, cfg):
    ids = library(con)
    previews(cfg, con, ids["letter"], ids["flatbed"], ids["certificate"])
    ai = FakeOllama({"dates": [{"written": " May 2, 1940 ", "what": "the letter's date"},
                               {"written": "", "what": "x"}]})
    assert scan_dates.read(con, cfg, ids["letter"], ai) == [{"written": "May 2, 1940", "what": "the letter's date"}]
    assert ai.asked == [1]
    row = scan_dates.reading(con, ids["letter"])
    assert (row["model"], row["version"]) == ("vision-model", scan_dates.PROMPT_VERSION)
    with pytest.raises(scan_dates.Problem, match="preview"):
        scan_dates.read(con, cfg, ids["undated"], ai)  # no preview made yet
    with pytest.raises(scan_dates.Problem, match="didn't make sense"):
        scan_dates.read(con, cfg, ids["flatbed"], FakeOllama(fail=ValueError("not JSON")))
    # the worker: every document not read yet with this model, once; one it can't read waits for another try
    worker_ai = FakeOllama({"dates": []})
    # documents first, then scanned photos: the flatbed scan and the certificate; the others have no preview: wait
    assert scan_dates.read_waiting(con, cfg, worker_ai) == 2
    assert scan_dates.read_waiting(con, cfg, worker_ai) == 0  # nothing new: none read twice
    waiting = con.execute("SELECT item_id FROM retry WHERE stage = ?", (scan_dates.STAGE,)).fetchall()
    assert sorted(r[0] for r in waiting) == sorted([ids["undated"], ids["ultrasound"], ids["print"]])
    assert json.loads(scan_dates.reading(con, ids["flatbed"])["dates"]) == []
    # the AI not answering ends the pass at the first scan it is asked about, which isn't held back for it
    import httpx
    con.execute("DELETE FROM scan_reading")
    con.execute("DELETE FROM retry")
    away = FakeOllama(fail=httpx.ConnectError("away"))
    assert scan_dates.read_waiting(con, cfg, away) == 0 and away.asked == [1]
    held = {r[0] for r in con.execute("SELECT item_id FROM retry")}
    assert not held & {ids["letter"], ids["flatbed"], ids["certificate"]}  # those it could have read


def test_the_suggested_dates_page(con, cfg, monkeypatch):
    ids = library(con)
    previews(cfg, con, ids["letter"])
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get("/cleanup/dates").text
    assert 'id="sd1"' in page and 'id="sd3"' in page and 'id="sd5"' in page and 'id="sd6"' in page
    assert 'id="sd4"' not in page and 'id="sd7"' not in page and 'id="sd2"' not in page
    assert "Date it Jun 3, 1952" in page and "a scanner (HP ScanJet 3500): the day it was scanned" in page
    assert page.index('id="sd1"') < page.index('id="sd3"')  # with a date to use first
    assert 'id="sd2"' in client.get("/cleanup/dates", params={"show": "photos"}).text
    card = client.get("/cleanup/counts/dates").text  # the number is both tabs together
    assert "Suggested dates" in card and '<span class="num">6</span>' in card and "5 documents,\n1 photo,\n0 photos" in card
    # dated from the list: by hand, undoable, off the list
    r = client.post(f"/api/images/{ids['letter']}/date", json={"date": "Jun 3, 1952"}, headers=APP)
    assert r.status_code == 200
    taken = con.execute("SELECT taken_at, taken_at_source, date_text FROM image WHERE id = ?",
                        (ids["letter"],)).fetchone()
    assert tuple(taken) == ("1952-06-03T14:30:00", "user", "Jun 3, 1952")  # the day given, the scan's time kept
    assert 'id="sd1"' not in client.get("/cleanup/dates").text
    # its date is right: off the list
    assert client.post(f"/api/images/{ids['flatbed']}/date-ok").status_code == 403  # needs the app's header
    assert client.post(f"/api/images/{ids['flatbed']}/date-ok", headers=APP).status_code == 200
    assert 'id="sd3"' not in client.get("/cleanup/dates").text
    assert client.post("/api/images/999/date-ok", headers=APP).status_code == 404
    # the AI asked now
    monkeypatch.setattr(llm.Ollama, "chat_json", lambda self, *a, **k: (
        {"dates": [{"written": "March 1960", "what": "issued"}]}, llm.Usage(1, 1, 1.0)))
    previews(cfg, con, ids["certificate"])
    read = client.post(f"/api/images/{ids['certificate']}/read-date", headers=APP).json()
    assert read == {"message": "It says March 1960.", "reload": True}
    listed = client.get("/cleanup/dates").text
    assert "Date it March 1960" in listed and "Read it again" in listed
    bad = client.post(f"/api/images/{ids['undated']}/read-date", headers=APP)
    assert bad.status_code == 400 and "preview" in bad.json()["detail"]
    assert client.post("/api/images/999/read-date", headers=APP).status_code == 404
    assert 'id="sd2"' in client.get("/cleanup/dates", params={"show": "photos", "page": 9}).text  # the last page
    assert client.post(f"/api/images/{ids['print']}/date-ok", headers=APP).status_code == 200
    assert "No scanned photo is waiting" in client.get("/cleanup/dates", params={"show": "photos"}).text


def test_a_worker_pass_reads_document_dates(con, cfg, monkeypatch):
    """The worker reads the dates in the describing hours, when the model can look at pictures."""
    from lineagebox import worker
    library(con)
    calls = []
    monkeypatch.setattr(scan_dates, "read_waiting", lambda con, cfg, client, stop=None: calls.append(stop) or 3)
    monkeypatch.setattr(llm.Ollama, "reachable", lambda self: True)
    monkeypatch.setattr(llm.Ollama, "model_problem", lambda self: None)
    monkeypatch.setattr(worker, "describe", lambda *a, **k: None)
    worker.run_once(con, cfg)
    assert len(calls) == 1 and callable(calls[0])
    assert "document dates: the AI read the dates on 3 scanned documents" in json.dumps(
        [r[0] for r in con.execute("SELECT message FROM activity")])


def test_the_scans_beside_one_dated_by_hand(con):
    """An album is scanned in its order: the scans just before and after one, dated by hand, date it; their own dates
    (the day they were scanned) say nothing."""
    from lineagebox import tagging
    album = [photo(con, n, f"2026-11-07T10:0{n}:00", "filename", folder=5,
                   path=f"lib/ScannedPhotos/Image_20261107_10{n:02d}00.tif") for n in range(1, 8)]
    for n in album:
        described(con, n, "portrait")
    tagging.set_date(con, [album[0]], "summer 1987")
    tagging.set_date(con, [album[2]], "summer 1987")
    tagging.set_date(con, [album[4]], "1989")
    con.commit()
    listed = {p["id"]: p for p in scan_dates.scans(con)["photos"]}
    said = lambda n: [(s["text"], s["why"]) for s in scan_dates.suggestions(con, listed[n])]
    assert said(album[1]) == [("summer 1987", "The scans before and after it in its folder were both dated summer 1987 "
                                              "by hand.")]
    assert said(album[3]) == [("between 1987 and 1989", "The scans before and after it in its folder were dated "
                                                        "summer 1987 and 1989 by hand.")]
    assert said(album[5]) == [("1989", "The scan just before it in its folder was dated 1989 by hand.")]
    # against who is in it (born after that), it isn't suggested
    kid = people.person_id(con, "Owen Miller")
    born(con, kid, "1995-03-01")
    face(con, album[5], like(A), kid, "user")
    con.commit()
    assert [s["label"] for s in scan_dates.suggestions(con, listed[album[5]])] == ["Not before 1995"]


def test_a_document_with_no_date_takes_the_photos_beside_it(con):
    """Not a scan: a ticket photographed on a trip, with only its file's time, is from the trip its folder holds."""
    for n, day in enumerate(("2016-01-10", "2016-01-11", "2016-01-12", "2016-01-13"), 1):
        photo(con, n, f"{day}T10:00:00", folder=4, path=f"lib/Chicago/IMG_{n}.jpg", camera="Phone")
    ticket = photo(con, 9, "2020-05-05T00:00:00", "mtime", folder=4, path="lib/Chicago/IMG_2b.jpg")
    described(con, ticket, "document/screenshot")
    con.commit()
    item = scan_dates.scans(con)["documents"][0]
    assert item["id"] == ticket and item["found"] == "no date"
    assert scan_dates.suggestions(con, item)[0]["text"] == "January 2016"


def childhood(con):
    """Owen, born 1980: photos of him as a child (1984 to 1990) and grown up (2008 to 2015), by a camera's date. His
    face as a child is like A, grown up like B."""
    owen = people.person_id(con, "Owen Miller")
    born(con, owen, "1980-04-02")
    for n, year in enumerate((1984, 1985, 1986, 1987, 1988), start=20):
        face(con, photo(con, n, f"{year}-06-01T10:00:00", folder=2), like(A, 0.2), owen, "user", age=25)
    for n, year in enumerate((2008, 2010, 2012, 2015), start=30):
        face(con, photo(con, n, f"{year}-06-01T10:00:00", folder=2), like(B, 0.2), owen, "user", age=31)
    return owen


def test_someone_looks_too_young_for_the_date(con):
    """A face most like the person's own childhood photos, on a photo dated when they were grown up: an old print
    photographed with a phone, a scan. Dated about when those childhood photos were taken."""
    owen = childhood(con)
    phone = photo(con, 40, "2021-03-01T10:00:00", folder=3, camera="Google Pixel")  # a phone's photo of a print
    scan = photo(con, 41, "2009-03-01T10:00:00", "filename", folder=4, path="lib/Scans/Scan_0041.jpg")
    grown = photo(con, 42, "2016-03-01T10:00:00", folder=3)  # him grown up, rightly dated
    by_hand = photo(con, 43, "2003-01-01T00:00:00", "user", folder=3)  # dated by hand: the family's word
    looks = photo(con, 44, "2019-01-01T10:00:00", folder=3)  # a face that looks like a young child's
    for image_id in (phone, scan, by_hand):
        face(con, image_id, like(A, 0.2), owen, "auto", age=24)
    face(con, grown, like(B, 0.2), owen, "auto", age=33)
    face(con, looks, like(B, 0.2), owen, "user", age=6)
    con.commit()
    found = scan_dates.young(con)
    assert set(found) == {phone, scan, looks}
    assert [(f["why"], f["day"].year, f["age"]) for f in found[phone]] == [("like", 1986, 40)]
    assert found[looks][0]["why"] == "looks" and found[looks][0]["day"] is None
    assert scan_dates.young(con, {phone}).keys() == {scan, looks}
    # the scan is with the scanned photos, the rest on a list of their own
    lists = scan_dates.scans(con)
    assert [i["id"] for i in lists["photos"]] == [scan] and lists["photos"][0]["young"][0]["why"] == "like"
    assert [i["id"] for i in lists["young"]] == [phone, looks]
    suggested = [s["text"] for s in scan_dates.suggestions(con, lists["young"][0])]
    assert suggested[:2] == ["about 1986", "between 1980 and 1992"]
    why = scan_dates.suggestions(con, lists["young"][0])[0]["why"]
    assert why == "Most like Owen Miller's photos from about 1986, aged about 6; its date makes Owen Miller 40."
    assert "between 1980 and 1992" in [s["text"] for s in scan_dates.suggestions(con, lists["young"][1])]


def test_the_looks_too_young_list(con, cfg):
    childhood(con)
    owen = people.person_id(con, "Owen Miller")
    phone = photo(con, 40, "2021-03-01T10:00:00", folder=3, camera="Google Pixel")
    young_face = face(con, phone, like(A, 0.2), owen, "auto", age=24)
    con.commit()
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get("/cleanup/dates", params={"show": "young"}).text
    assert 'id="sd40"' in page and "Looks too young <small>1</small>" in page
    assert "Looks like Owen Miller's own photos as a child; its date makes Owen Miller 40." in page
    assert f'data-api="/api/faces/{young_face}/not"' in page and "Date it about 1986" in page
    card = client.get("/cleanup/counts/dates").text
    assert '<span class="num">1</span>' in card and "1 photo where someone looks too young for its date." in card
    # its date is right: off the list
    assert client.post(f"/api/images/{phone}/date-ok", headers=APP).status_code == 200
    assert 'id="sd40"' not in client.get("/cleanup/dates", params={"show": "young"}).text
