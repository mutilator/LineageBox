"""Reading into the family tree: an obituary, an announcement, a letter or notes, pasted or photographed, read by the
AI into changes that are looked over and made one by one (family_reader.py). The AI takes a minute or so, so the
reading runs beside the site and its page follows it."""
import base64
import binascii
import json
import threading
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from lineagebox import catalog, db, events, family_reader, people
from lineagebox.web.common import ADMIN, _from_app
from lineagebox.web.deps import con, page, site, viewer
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/family/read": ADMIN,
    "/family/read/{reading_id}": ADMIN,
    "/family/read/{reading_id}/progress": ADMIN,
    "/api/family/read": ADMIN,
    "/api/family/read/{reading_id}/choices": ADMIN,
    "/api/family/read/{reading_id}/ticks": ADMIN,
    "/api/family/read/{reading_id}/apply": ADMIN,
    "/api/family/read/{reading_id}/again": ADMIN,
    "/api/family/read/{reading_id}/delete": ADMIN,
}
MOST_PHOTO_BYTES = 20 * 2**20  # each, as sent: the page makes them smaller first
GIVEN_UP_AFTER = 20 * 60       # seconds: a reading still going then was stopped (the site restarted meanwhile)


class ReadBody(BaseModel):
    text: str = ""
    source: str = ""
    image_id: str = ""
    photos: list[str] = []  # data: URLs, as the page sends them


class AgainBody(BaseModel):
    text: str = ""


class ApplyBody(BaseModel):
    do: list[str] = []


class TicksBody(BaseModel):
    ticked: dict[str, bool] = {}


def _photo(data_url: str) -> bytes:
    try:
        data = base64.b64decode(data_url.partition(",")[2] or data_url, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "One of the photos didn't come through: add it again.") from None
    if len(data) > MOST_PHOTO_BYTES:
        raise HTTPException(413, f"One of the photos is over {MOST_PHOTO_BYTES // 2**20} MB.")
    return data


def _read_beside(s: Site, now, reading_id: int, photos: list[bytes]) -> None:
    con_ = db.connect(s.cfg, migrate_schema=False, check_same_thread=False)
    threading.Thread(target=family_reader.run, args=(con_, now, reading_id, photos), daemon=True,
                     name=f"family reading {reading_id}").start()


def _found_or_404(c, reading_id: int):
    found = family_reader.load(c, reading_id)
    if found is None:
        raise HTTPException(404, "No such reading")
    return found


def _stopped(row) -> bool:
    """Still 'reading' long after it began: the site restarted while the AI read it."""
    if row["state"] != "reading":
        return False
    return (datetime.now() - datetime.fromisoformat(row["created_at"])).total_seconds() > GIVEN_UP_AFTER


def readings(c) -> list:
    """The readings so far, the newest first: on the reading page, and in the Family page's panel."""
    return c.execute("""SELECT id, title, state, created_at, applied_at, source FROM family_reading
                        ORDER BY id DESC LIMIT 50""").fetchall()


@router.get("/family/read", response_class=HTMLResponse)
def read_page(request: Request, image: int | None = None, c=Depends(con), s: Site = Depends(site)):
    img = catalog.image(c, image) if image else None
    if image and (img is None or img["media"] != "photo"):
        raise HTTPException(404, "No such photo")
    return page(request, "family_read.html", nav="family", img=img, readings=readings(c),
                ai_on=s.live(c).describe_enabled, most_photos=family_reader.MOST_PHOTOS)


@router.post("/api/family/read", dependencies=[Depends(_from_app)])
def start_reading(body: ReadBody, v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    """Something to read: the text typed, and photos sent or one of the library's. Kept at once, and read beside the
    site; the answer is where the reading's page is."""
    now = s.live(c)
    if not now.describe_enabled:
        raise HTTPException(400, "The AI is turned off on the Settings page (AI descriptions): it does the reading.")
    if len(body.photos) > family_reader.MOST_PHOTOS:
        raise HTTPException(400, f"Up to {family_reader.MOST_PHOTOS} photos at a time.")
    photos = [_photo(p) for p in body.photos if p]
    image_id = int(body.image_id) if body.image_id.isdecimal() else None
    if image_id is not None:
        img = catalog.image(c, image_id)
        if img is None or img["media"] != "photo":
            raise HTTPException(404, "No such photo")
        try:
            photos.insert(0, now.preview_path(img["sha256"]).read_bytes())
        except OSError:
            raise HTTPException(409, "That photo's preview isn't made yet: try again after the next scan.") from None
    text = family_reader.clean_text(body.text)
    if not text and not photos:
        raise HTTPException(400, "Paste the text, or add photos of it.")
    if len(text) > family_reader.MOST_TEXT:
        raise HTTPException(400, f"That is too long to read at once ({len(text):,} characters; up to "
                                 f"{family_reader.MOST_TEXT:,}). Read it in parts, one notice at a time.")
    for p in photos:  # one that can't be opened is said now, not after the wait
        try:
            family_reader.photo_for_model(p)
        except family_reader.Problem as e:
            raise HTTPException(400, str(e)) from None
    reading_id = family_reader.start(c, text, body.source, image_id, v.account_id, len(photos))
    _read_beside(s, now, reading_id, photos)
    return {"go": f"/family/read/{reading_id}"}


def _took(row) -> int:
    return max(0, int((datetime.now() - datetime.fromisoformat(row["created_at"])).total_seconds()))


def _made(report: dict) -> list[dict]:
    """The changes made, by the person in the tree each was listed under, in the order made: [{'person', 'changes'}].
    Readings applied before the report said whose have one list, under None."""
    by: dict = {}
    for d in report["done"]:
        d = d if isinstance(d, dict) else {"text": d, "person": None}
        by.setdefault(d.get("person"), []).append(d["text"])
    return [{"person": pid, "changes": texts} for pid, texts in by.items()]


@router.get("/family/read/{reading_id}", response_class=HTMLResponse)
def reading_page(request: Request, reading_id: int, c=Depends(con)):
    """While the AI reads, a page that follows it; then the changes to tick; once they are made, only what changed
    (what wasn't ticked is gone from the page: reading it again lists it again)."""
    row, found, choices = _found_or_404(c, reading_id)
    report = json.loads(row["report"]) if row["report"] else None
    # made, and not read again since; when none of the ticked ones could be made, they are listed to tick again
    applied = bool(report and report["done"] and not report.get("read_again"))
    proposal = None
    if found is not None and row["state"] == "ready" and not applied:
        proposal = family_reader.propose(c, found, choices, row["source"] or "")
    names = dict(c.execute("SELECT id, name FROM person"))
    added = list(dict.fromkeys(report["added"].values())) if applied else []
    by_text = {q["id"]: q for q in found["people"]} if found else {}
    gone = {pid: family_reader.display_name(by_text[x]) for x, pid in (report or {}).get("added", {}).items()
            if pid not in names and x in by_text}  # added, then removed again: called as they were
    img = catalog.image(c, row["image_id"]) if row["image_id"] else None
    return page(request, "family_read_review.html", nav="family", reading=row, found=found, proposal=proposal,
                names=names, img=img, stopped=_stopped(row), took=_took(row), report=report, applied=applied,
                made=_made(report) if applied else [], added=added, gone=gone,
                removable={pid for pid in added if pid in names and people.why_kept(c, pid) is None},
                show_date=events.show_date)


@router.get("/family/read/{reading_id}/progress", response_class=HTMLResponse)
def reading_progress(request: Request, reading_id: int, c=Depends(con)):
    """What the reading's page shows while the AI reads, every few seconds; once it is done, a mark that makes the
    page load again."""
    row, _, _ = _found_or_404(c, reading_id)
    if row["state"] != "reading" or _stopped(row):
        return HTMLResponse('<span data-reload-page></span>')
    return page(request, "_family_read_waiting.html", reading=row, took=_took(row))


@router.post("/api/family/read/{reading_id}/choices", dependencies=[Depends(_from_app)])
def reading_choices(reading_id: int, body: dict, c=Depends(con)):
    """Who is who, as picked: text person -> a person here, 'new' or 'skip'; a name typed, as the GEDCOM import's."""
    row, found, _ = _found_or_404(c, reading_id)
    if found is None:
        raise HTTPException(409, "It hasn't been read yet.")
    picked = {k: v for k, v in body.items() if not k.startswith("name:")}
    for k, val in body.items():
        if k.startswith("name:") and not k.endswith("_id") and str(val).strip():
            picked[k[5:]] = str(body.get(f"{k}_id") or val)
    family_reader.set_choices(c, reading_id, picked)
    return {}


@router.post("/api/family/read/{reading_id}/ticks", dependencies=[Depends(_from_app)])
def reading_ticks(reading_id: int, body: TicksBody, c=Depends(con)):
    """The changes ticked and unticked, kept as they are clicked (family_reader.save_ticks)."""
    _found_or_404(c, reading_id)
    family_reader.save_ticks(c, reading_id, body.ticked)
    return {}


@router.post("/api/family/read/{reading_id}/apply", dependencies=[Depends(_from_app)])
def reading_apply(reading_id: int, body: ApplyBody, c=Depends(con)):
    row, found, _ = _found_or_404(c, reading_id)
    if found is None or row["state"] != "ready":
        raise HTTPException(409, "It hasn't been read yet.")
    if not body.do:
        raise HTTPException(400, "Tick the changes to make first.")
    report = family_reader.apply_kept(c, reading_id, set(body.do))
    done, skipped = len(report["done"]), len(report["skipped"])
    return {"message": f"{done} change{'' if done == 1 else 's'} made" + (f", {skipped} not" if skipped else "") + "."}


@router.post("/api/family/read/{reading_id}/again", dependencies=[Depends(_from_app)])
def reading_again(reading_id: int, body: AgainBody, c=Depends(con), s: Site = Depends(site)):
    """Read it again, as it is or as corrected. Who is who is matched again: anyone added from it is in the tree now,
    and found there."""
    row, _, _ = _found_or_404(c, reading_id)
    now = s.live(c)
    if not now.describe_enabled:
        raise HTTPException(400, "The AI is turned off on the Settings page (AI descriptions): it does the reading.")
    if row["state"] == "reading" and not _stopped(row):
        raise HTTPException(409, "It is being read now.")
    text = family_reader.clean_text(body.text) or row["text"]
    if not text:
        raise HTTPException(400, "There is no text to read.")
    if len(text) > family_reader.MOST_TEXT:
        raise HTTPException(400, f"That is too long to read at once (up to {family_reader.MOST_TEXT:,} characters).")
    # what was made from it before stays in the report, said of an earlier reading of it
    c.execute("""UPDATE family_reading SET text = ?, state = 'reading', error = NULL, created_at = ?,
                 report = CASE WHEN report IS NULL THEN NULL ELSE json_set(report, '$.read_again', json('true')) END
                 WHERE id = ?""", (text, datetime.now().isoformat(timespec="seconds"), reading_id))
    c.commit()
    _read_beside(s, now, reading_id, [])
    return {"go": f"/family/read/{reading_id}"}


@router.post("/api/family/read/{reading_id}/delete", dependencies=[Depends(_from_app)])
def reading_delete(reading_id: int, c=Depends(con)):
    """The reading goes; what it changed in the tree stays."""
    _found_or_404(c, reading_id)
    c.execute("DELETE FROM family_reading WHERE id = ?", (reading_id,))
    c.commit()
    return {"go": "/family/read"}
