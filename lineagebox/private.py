"""Private papers (Cleanup → Private papers, admins): photos of identity cards, money and tax papers, health records
and certificates of people still living, which family members shouldn't see, whoever is in them.

They are found in the AI's description of each photo (describe.py): a phrase in its caption, objects or tags
(PHRASES), or a pattern in the text it read off the photo (READ). That text is never shown on the list, only what kind
of thing it looked like. An admin hides them from family members (image.hidden: access.hidden leaves them out of every
member's view, of shares and of photo frames; whoever uploaded one still sees it), or says one is fine to show
(OK_KEY): either takes it off the list. The worker tells admins of new ones (outbox.private_papers).

Measured on the library LineageBox was developed on (38,829 photos described, 2026-11-12): the phrases found 137
photos, 115 of them the AI's "document/screenshot": driver's licenses, Social Security cards, W-2 and 1099 forms,
checks, insurance cards, immunization records, a child's birth certificate. On the kinds of photo that are of people
(PEOPLE_TYPES) they were mostly something else (an identification badge on a lanyard at the zoo, a marriage license
signed at a wedding), so a phrase counts only on other kinds. A number set out like a Social Security number or a
card's counts on any.
"""
import json
import re
import sqlite3
from datetime import date

from lineagebox import access, settings
from lineagebox.catalog import VISIBLE

KINDS = {"id": "Identity cards", "money": "Money and taxes", "health": "Health records",
         "certificates": "Certificates of living people"}
PHRASES = {
    "id": ["driver's license", "drivers license", "driver license", "driver's licence", "driving licence", "id card",
           "identification card", "identity card", "photo id", "state id", "passport", "social security card",
           "social security number", "green card", "permanent resident card", "military id", "student id"],
    "money": ["credit card", "debit card", "bank card", "bank statement", "checkbook", "check book", "personal check",
              "bank check", "cheque", "pay stub", "paystub", "payslip", "pay slip", "tax return", "tax form",
              "tax forms", "w-2", "w2 form", "1099", "form 1040", "account number", "routing number", "password",
              "pin number"],
    "health": ["insurance card", "health insurance", "medicare card", "medicaid card", "medical record",
               "medical records", "lab results", "lab report", "test results", "blood test", "vaccination card",
               "vaccination record", "immunization record", "immunization records", "prescription"],
    "certificates": ["birth certificate", "certificate of birth", "certificate of live birth", "marriage certificate",
                     "marriage license", "adoption papers", "adoption decree", "adoption certificate"],
}
# glasses made to a prescription are no health record
_BUT_NOT = {"prescription": r"(?! (?:sun)?glasses| eyeglasses| lenses)"}
_PHRASE = [(kind, p, re.compile(r"\b" + re.escape(p) + r"\b" + _BUT_NOT.get(p, ""))) for kind, ps in PHRASES.items()
           for p in ps]
# in the text the AI read off the photo: (kind, what it looked like, pattern, on any kind of photo)
READ = [("id", "a number set out like a Social Security number", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), True),
        ("money", "a number set out like a bank card's", re.compile(r"\b(?:\d{4}[ -]){3}\d{4}\b"), True),
        ("id", "the words Social Security", re.compile(r"social security", re.I), False),
        ("id", "a driver's license's words", re.compile(r"\bdriver'?s? licen[cs]e\b", re.I), False),
        ("id", "a date of birth", re.compile(r"\b(?:dob|date of birth)\b", re.I), False)]
PEOPLE_TYPES = {"candid", "posed group", "portrait", "selfie", "landscape"}
OK_KEY = "private_ok"  # settings: photos an admin said are fine to show
GONE_AFTER = 110  # years: someone born longer ago than this, with no death entered, has died all the same


def looks_private(caption: str | None, raw_json: str | None, text_read: str | None,
                  photo_type: str | None) -> tuple[str, str] | None:
    """(kind, why) when a description says a photo shows private papers, else None. why: the phrase it used, or what
    the text read looked like (never the text)."""
    raw = json.loads(raw_json or "{}")
    papers = photo_type not in PEOPLE_TYPES
    if papers:
        words = " ".join([caption or "", *raw.get("objects", []), *raw.get("tags", [])]).lower()
        for kind, phrase, rx in _PHRASE:
            if rx.search(words):
                return kind, phrase
    for kind, why, rx, anywhere in READ:
        if text_read and (anywhere or papers) and rx.search(text_read):
            return kind, why
    return None


def _all_gone(con: sqlite3.Connection, image_ids: list[int]) -> set[int]:
    """Of these photos, those whose people (named on a face, added by hand, or cited by it as a record) have all died:
    a certificate of theirs is family history, not a living person's papers."""
    ids = json.dumps(image_ids)
    people: dict[int, set[int]] = {}
    for image_id, pid in con.execute("""
            SELECT image_id, person_id FROM face WHERE person_id IS NOT NULL AND assigned_by IN ('user', 'auto')
              AND image_id IN (SELECT value FROM json_each(:ids))
            UNION SELECT image_id, person_id FROM image_person WHERE image_id IN (SELECT value FROM json_each(:ids))
            UNION SELECT c.image_id, coalesce(c.person_id, e.person_id) FROM citation c
              LEFT JOIN event e ON e.id = c.event_id WHERE c.image_id IN (SELECT value FROM json_each(:ids))""",
                                     {"ids": ids}):
        if pid is not None:
            people.setdefault(image_id, set()).add(pid)
    long_ago = f"{date.today().year - GONE_AFTER:04d}"
    gone = {r[0] for r in con.execute("SELECT id FROM person WHERE deceased OR (birth_date IS NOT NULL AND "
                                      "birth_date < ?)", (long_ago,))}
    return {i for i, them in people.items() if them <= gone}


def suggestions(con: sqlite3.Connection) -> list[dict]:
    """The photos that look private (looks_private) and aren't hidden from family members or said fine to show: those
    with someone named in them first (family members who see that person see the photo), then by kind, the newest
    first. Each: id, sha256, taken_at, taken_at_source, caption, kind, why, named."""
    ok = set(settings.get(con, OK_KEY, []))
    not_shown, args = access.hidden(con)
    hidden = {r[0] for r in con.execute(not_shown, args)}
    found, seen = [], set()
    for r in con.execute(f"""SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, a.caption, a.raw_json,
                                    a.text_visible, a.photo_type FROM analysis a JOIN image i ON i.id = a.image_id
                             WHERE i.media = 'photo' AND {VISIBLE}
                             ORDER BY i.id, a.created_at DESC"""):
        if r["id"] in seen:  # an older description of the same photo
            continue
        seen.add(r["id"])
        if r["id"] in ok or r["id"] in hidden:
            continue
        if what := looks_private(r["caption"], r["raw_json"], r["text_visible"], r["photo_type"]):
            found.append({**{k: r[k] for k in ("id", "sha256", "taken_at", "taken_at_source", "caption")},
                          "kind": what[0], "why": what[1]})
    gone = _all_gone(con, [f["id"] for f in found if f["kind"] == "certificates"])
    found = [f for f in found if f["id"] not in gone]
    named = {r[0] for r in con.execute("""
        SELECT image_id FROM face WHERE person_id IS NOT NULL AND assigned_by IN ('user', 'auto')
          AND image_id IN (SELECT value FROM json_each(:ids))
        UNION SELECT image_id FROM image_person WHERE image_id IN (SELECT value FROM json_each(:ids))""",
                                       {"ids": json.dumps([f["id"] for f in found])})}
    order = list(KINDS)
    for f in found:
        f["named"] = f["id"] in named
    return sorted(found, key=lambda f: (not f["named"], order.index(f["kind"]), -_day(f["taken_at"]), -f["id"]))


def _day(taken_at: str | None) -> int:
    """A date as a number to sort by (undated last among the newest first)."""
    return int(re.sub(r"\D", "", (taken_at or "")[:10]) or 0)
