"""Historical papers, phases 2 and 3: searching their words (paper_search.py), the AI's card and the people it names
(paper_cards.py), introductions to the family's history (introductions.py), and New for you's new documents."""
from datetime import date, datetime

from fastapi.testclient import TestClient

from lineagebox import (access, accounts, family, historical, introductions, llm, outbox, paper_cards, paper_search,
                        search, settings)
from lineagebox.web import app as webapp
from tests.conftest import LAN
from tests.test_face_checks import photo

APP = {"X-Requested-With": "lineagebox"}


def paper(con, n, title, text, kind="Letter", written=None, folder=1):
    image_id = photo(con, n, "2026-11-25T10:00:00", "filename", folder=folder, path=f"f{folder}/scan{n:03d}.jpg")
    historical.make(con, image_id, kind, title, text=text)
    if written:
        historical.set_details(con, image_id, historical.Details(kind, title, written))
    return image_id


def person(con, name, born=None, died=None):
    pid = con.execute("INSERT INTO person(name, birth_date) VALUES (?, ?)", (name, born)).lastrowid
    if died:
        con.execute("INSERT INTO event(person_id, tag, date, sort_date) VALUES (?, 'DEAT', ?, ?)", (pid, died, died))
    return pid


def indexed(con):
    search.index_new(con)
    con.commit()


# ---- search ----------------------------------------------------------------------------------------------------------

def test_searching_the_words_of_papers(con):
    deed = paper(con, 1, "Deed to the farm", "The farm on Mill Road\nsold to Ben <Baker>")
    letter = paper(con, 2, "Letter to Ada", "Dear Ada,\nthe apple trees bloom.")
    historical.add_pages(con, letter, [photo(con, 3, None, path="f1/scan003.jpg")])
    historical.set_text(con, letter, 2, "Ben sold the farm last week.")
    plain = photo(con, 4, "2015-01-01T10:00:00", path="f1/farm.jpg")  # a photo of a farm, described as one
    con.execute("""INSERT INTO analysis(image_id, model, prompt_version, created_at, caption, raw_json)
                   VALUES (?, 'm', 1, '2026-01-01', 'A farm in winter', '{"caption": "A farm in winter"}')""",
                (plain,))
    con.commit()
    indexed(con)
    found = paper_search.query(con, "farm")
    assert [p["id"] for p in found] == [deed, letter]  # the title says farm too: it comes first
    assert found[1]["hits"][0]["n"] == 2 and "<mark>farm</mark>" in found[1]["hits"][0]["snippet"]
    assert "&lt;Baker&gt;" in found[0]["hits"][0]["snippet"]  # a paper's words are escaped
    assert [r["id"] for r in search.query(con, "farm")] == [plain]  # the photo search leaves papers out
    assert [p["id"] for p in paper_search.query(con, "apple")] == [letter]
    assert paper_search.query(con, "apple", year_from=1990) == []
    ada = person(con, "Ada Miller")
    paper_cards.confirm(con, letter, ada, True)
    indexed(con)
    assert [p["id"] for p in paper_search.query(con, "miller")] == [letter]  # by who is in it
    assert [p["id"] for p in paper_search.query(con, "trees", people=(ada,))] == [letter]
    assert paper_search.query(con, "farm", people=(ada,))[0]["id"] == letter
    historical.not_a_paper(con, deed)
    con.commit()
    indexed(con)
    assert [p["id"] for p in paper_search.query(con, "farm")] == [letter]


# ---- the AI's card ---------------------------------------------------------------------------------------------------

class Summer:
    """The AI summing up a paper: its answer, and what it was asked."""
    model = "summer"

    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def chat_json(self, system, user, schema, images=(), temperature=0.1, max_tokens=1024, logprobs=0):
        self.asked.append(user)
        return self.answer, llm.Usage(0, 0, 0.0)


def test_the_card_and_the_people_it_names(con):
    ada = person(con, "Ada Mae Miller", born="1900", died="1980")
    con.execute("INSERT INTO person_name(person_id, name, type) VALUES (?, 'Ada Baker', 'married')", (ada,))
    ben1, ben2 = person(con, "Ben Baker"), person(con, "Ben Baker")  # two of the same name
    young = person(con, "Cora Miller", born="1950")
    letter = paper(con, 1, "scan001", "Dear Ben, I am well. Ada")
    con.commit()
    assert paper_cards.waiting(con) == [letter]
    ai = Summer({"kind": "Letter", "title": "Ada's letter to Ben", "written": "June 3, 1931", "sender": "Ada Baker",
                 "recipient": "Ben Baker", "summary": "Ada writes that she is well.",
                 "people": [{"name": "Ada Baker", "role": "wrote"}, {"name": "Ben Baker", "role": "received"},
                            {"name": "Cora Miller", "role": "named"}, {"name": "Ada", "role": "named"}],
                 "places": ["Mill Road", "Mill Road"], "language": "English"})
    assert paper_cards.make_waiting(con, ai) == 1 and "Dear Ben" in ai.asked[0]
    p = historical.get(con, letter)
    assert (p["kind"], p["title"], p["written_from"], p["sender"], p["summary"]) == \
           ("Letter", "Ada's letter to Ben", "1931-06-03", "Ada Baker", "Ada writes that she is well.")
    assert p["places"] == '["Mill Road"]' and p["card_version"] == paper_cards.VERSION
    named = {x["person_id"]: (x["role"], x["state"]) for x in paper_cards.people(con, letter)}
    # Ada by her married name, at once; a Ben Baker of two only suggested; Cora not born yet; "Ada" alone: anyone
    assert named == {ada: ("wrote", "auto"), ben1: ("received", "suggested"), ben2: ("received", "suggested")}
    assert young not in named
    assert paper_cards.waiting(con) == []
    # a person's word stays when the card is made again; words they correct make it again
    paper_cards.confirm(con, letter, ben1, True, "received")
    paper_cards.confirm(con, letter, ada, False)
    historical.set_text(con, letter, 1, "Dear Ben, I am well. Love, Ada")
    con.commit()
    assert paper_cards.waiting(con) == [letter]
    paper_cards.make_waiting(con, ai)
    named = {x["person_id"]: x["state"] for x in paper_cards.people(con, letter)}
    assert named == {ben1: "user"}  # Ada said it isn't her; the other Ben isn't asked about once one is confirmed
    # a person's own details are never written over
    historical.set_details(con, letter, historical.Details("Card or note", "From Ada", "1931", summary="A note"))
    con.execute("UPDATE document SET card_at = NULL WHERE image_id = ?", (letter,))
    paper_cards.make_waiting(con, ai)
    p = historical.get(con, letter)
    assert (p["kind"], p["title"], p["summary"]) == ("Card or note", "From Ada", "A note")


def test_matching_names():
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE person(id INTEGER PRIMARY KEY, name TEXT, birth_date TEXT)")
    con.execute("CREATE TABLE person_name(person_id INTEGER, name TEXT)")
    con.execute("CREATE TABLE event(person_id INTEGER, tag TEXT, sort_date TEXT)")
    con.execute("INSERT INTO event VALUES (2, 'DEAT', '1920')")
    con.executemany("INSERT INTO person VALUES (?, ?, ?)", [(1, "Ada Mae Miller", "1900"), (2, "Zoë Baker", None)])
    assert paper_cards.match_name(con, "Mrs. Ada Miller") == [(1, 0.85)]
    assert paper_cards.match_name(con, "ada mae miller") == [(1, 0.95)]
    assert paper_cards.match_name(con, "A. Miller") == [(1, 0.6)]
    assert paper_cards.match_name(con, "Zoe Baker")[0][0] == 2
    assert paper_cards.match_name(con, "Zoe Baker", 1950)[0][1] < 0.9  # dead thirty years by then
    assert paper_cards.match_name(con, "Ada Miller", 1890) == []  # not born yet
    assert paper_cards.match_name(con, "Ada") == []


# ---- introductions ---------------------------------------------------------------------------------------------------

def family_tree(con):
    """Emma, her mother Ruth, her grandfather Ben, Ruth's sister Joan (Emma's aunt), Joan's son Max (her cousin)."""
    ids = {n: person(con, n) for n in ("Emma Miller", "Ruth Miller", "Ben Baker", "Joan Baker", "Max Baker",
                                        "Ollie Stranger")}
    for name, sex in (("Emma Miller", "female"), ("Ruth Miller", "female"), ("Ben Baker", "male"),
                      ("Joan Baker", "female"), ("Max Baker", "male")):
        con.execute("UPDATE person SET sex = ? WHERE id = ?", (sex, ids[name]))
    family.add_parent(con, ids["Emma Miller"], ids["Ruth Miller"])
    family.add_parent(con, ids["Ruth Miller"], ids["Ben Baker"])
    family.add_parent(con, ids["Joan Baker"], ids["Ben Baker"])
    family.add_parent(con, ids["Max Baker"], ids["Joan Baker"])
    return ids


def about(con, paper_id, pid):
    paper_cards.confirm(con, paper_id, pid, True, "about")


def test_introductions_closest_family_first(con):
    ids = family_tree(con)
    papers = {
        "cousin": paper(con, 1, "Max's report card", "Max did well", "School record"),
        "deed": paper(con, 2, "Ben's deed", "The farm", "Property record"),
        "grandpa": paper(con, 3, "Ben's letter", "Dear Ruth", "Letter"),
        "mother": paper(con, 4, "Ruth's diploma", "Ruth Miller", "Diploma"),
        "nobody": paper(con, 5, "A recipe", "Two cups of flour", "Recipe"),
        "stranger": paper(con, 6, "Ollie's letter", "Hello", "Letter"),
        "private": paper(con, 7, "Ben's pay stub", "Employee 123-45-6789", "Letter"),
        "blank": paper(con, 8, "An empty scan", "", "Letter"),
    }
    for key, who in (("cousin", "Max Baker"), ("deed", "Ben Baker"), ("grandpa", "Ben Baker"),
                     ("mother", "Ruth Miller"), ("stranger", "Ollie Stranger"), ("private", "Ben Baker")):
        about(con, papers[key], ids[who])
    con.commit()
    emma = access.Viewer(account={"id": 5, "person_id": ids["Emma Miller"], "role": "admin"}, admin=True)
    order = [p["id"] for p in introductions.ranked(con, con, emma, date(2026, 11, 26))]
    by_id = {v: k for k, v in papers.items()}
    # her mother, then her grandfather (his letter before his deed), her cousin, then those not of her family
    assert [by_id[i] for i in order] == ["mother", "grandpa", "deed", "cousin", "stranger", "nobody"]
    first = introductions.offer(con, con, con, emma, date(2026, 11, 26))
    assert first["id"] == papers["mother"] and first["about"][0]["kin"] == "mother"
    assert introductions.offer(con, con, con, emma)["id"] == papers["mother"]  # the same, until opened or skipped
    introductions.mark_seen(con, emma, papers["mother"])
    second = introductions.offer(con, con, con, emma)
    assert second["id"] == papers["grandpa"] and second["about"][0]["kin"] == "grandfather"
    introductions.skip(con, emma, papers["grandpa"])
    con.commit()
    assert introductions.offer(con, con, con, emma)["id"] == papers["deed"]  # Show me another: behind the rest
    # a letter written on this day in an earlier year comes first that day
    historical.set_details(con, papers["cousin"], historical.Details("School record", "Max's report card",
                                                                     "November 26, 1990"))
    con.commit()
    assert introductions.ranked(con, con, emma, date(2026, 11, 26))[0]["id"] == papers["cousin"]


def test_who_gets_introductions(con, cfg):
    ids = family_tree(con)
    letter = paper(con, 1, "Ben's letter", "Dear Ruth")
    about(con, letter, ids["Ben Baker"])
    con.commit()
    house = access.Viewer()  # no family accounts: by the person marked as "me"
    assert introductions.ranked(con, con, house) == []
    settings.put(con, "me", ids["Emma Miller"])
    assert [p["id"] for p in introductions.ranked(con, con, house)] == [letter]
    # a friend's account (someone in the tree with no family there): none, unless a line was shared with it
    friend_id = accounts.add(con, "friend@example.com", ids["Ollie Stranger"])
    con.commit()
    friend = access.compute(con, dict(accounts.get(con, friend_id)))
    assert introductions.who_for(con, friend) == (None, [])
    con.execute("INSERT INTO access_rule(account_id, person_id, show, branch) VALUES (?, ?, 1, 1)",
                (friend_id, ids["Ruth Miller"]))
    con.commit()
    friend = access.compute(con, dict(accounts.get(con, friend_id)))
    scoped = access.scope(con, friend)
    found = introductions.ranked(scoped, con, friend)
    assert [p["id"] for p in found] == [letter]
    assert introductions.about_line(con, friend, found[0])[0]["kin"] is None  # named, not "your grandfather"


def test_the_mark_in_the_menu(con):
    letter = paper(con, 1, "A letter", "Dear Ruth")
    con.commit()
    house = access.Viewer()
    morning = datetime(2026, 11, 26, 9, 0)
    assert introductions.waiting_mark(con, house, "day", morning)
    assert not introductions.waiting_mark(con, house, "never", morning)
    con.execute("INSERT INTO paper_offer(account_id, paper_id, offered_at) VALUES (0, ?, '2026-11-26T08:00:00')",
                (letter,))
    assert not introductions.waiting_mark(con, house, "day", morning)  # offered today already
    assert introductions.waiting_mark(con, house, "day", datetime(2026, 11, 27, 9, 0))
    assert not introductions.waiting_mark(con, house, "week", datetime(2026, 11, 27, 9, 0))  # the same week
    introductions.mark_seen(con, house, letter)
    assert not introductions.waiting_mark(con, house, "day", datetime(2026, 11, 28, 9, 0))  # nothing left to meet


# ---- the pages ---------------------------------------------------------------------------------------------------------

def test_the_pages_of_phases_two_and_three(con, cfg):
    ids = family_tree(con)
    settings.put(con, "me", ids["Emma Miller"])
    letter = paper(con, 1, "Ben's letter", "Dear Ruth, the farm is sold.")
    note = paper(con, 2, "A note", "The farm is ours again.")
    about(con, letter, ids["Ben Baker"])
    con.execute("INSERT INTO paper_person(paper_id, person_id, role, name, state, at) "
                "VALUES (?, ?, 'named', 'Ruth', 'suggested', '2026-11-26')", (letter, ids["Ruth Miller"]))
    con.commit()
    indexed(con)
    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get("/historical").text
    assert "From your family" in page and "your grandfather Ben Baker" in page
    assert f"/api/historical/introduction/{letter}/skip" in page
    assert 'class="side-waiting"' not in page  # offered just now: no mark
    found = client.get("/historical?q=farm").text
    assert "<mark>farm</mark>" in found and "Ben" in found and "From your family" not in found
    part = client.get("/search/papers?q=farm").text
    assert "Historical" in part and f"/historical/{letter}#page-1" in part
    assert 'data-later="/search/papers?q=farm"' in client.get("/search?q=farm").text
    assert client.get("/search/papers?q=nothingatall").text.strip() == ""
    one = client.get(f"/historical/{letter}").text  # opened: marked seen, never offered again
    assert "Is “Ruth” Ruth Miller?" in one and "In it:" in one
    assert con.execute("SELECT count(*) FROM paper_seen WHERE paper_id = ?", (letter,)).fetchone()[0] == 1
    assert f"/historical/{note}" in client.get("/historical").text.split("</section>")[0]  # the next one offered
    r = client.post(f"/api/historical/{letter}/people/{ids['Ruth Miller']}", json={"yes": True}, headers=APP)
    assert r.status_code == 200 and "Is “Ruth”" not in client.get(f"/historical/{letter}").text
    papers_part = client.get(f"/person/{ids['Ben Baker']}/papers").text
    assert "Ben&#39;s letter" in papers_part and "about them" in papers_part
    assert client.get(f"/person/{ids['Max Baker']}/papers").text.strip() == ""
    assert client.post(f"/api/historical/introduction/{note}/skip", headers=APP).status_code == 200
    assert client.post("/api/settings", json={"introduce_every": "week"}, headers=APP).status_code == 200
    assert settings.current(con, cfg).introduce_every == "week"
    assert client.post("/api/settings", json={"introduce_every": "hourly"}, headers=APP).status_code == 400


def test_new_documents_on_new_for_you(con):
    assert outbox.new_papers(con) == 0  # the first time: nothing told, it is all new
    con.execute("UPDATE settings SET value = '\"2020-01-01T00:00:00\"' WHERE key = ?", (outbox.PAPERS_TOLD,))
    letter = paper(con, 1, "A letter", "Dear Ruth")
    unread = photo(con, 2, None, path="f1/two.jpg")
    historical.mark(con, unread, "Letter", "Not read yet")
    con.commit()
    assert outbox.new_papers(con) == 1  # the one read; the other waits until it is
    events = [e for e in outbox.for_viewer(con, access.Viewer()) if e["kind"] == "new_papers"]
    assert len(events) == 1 and events[0]["text"] == "A new document in Historical"
    assert events[0]["href"] == f"/historical/{letter}"
    assert settings.get(con, outbox.PAPERS_WAITING) == [unread]
