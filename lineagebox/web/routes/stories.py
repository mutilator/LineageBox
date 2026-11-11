"""Recorded stories (stories.py) and corrected transcripts (speech.correct).

Anyone who can see a photo or a person may record a story about it (or about one of the person's life events); whoever
recorded one, or an admin, may correct its words or take it away. Anyone who can see a video or a recording may put a
line of its transcript right. Every one of these is kept for Undo (changes.tracking), and the search takes the words
in at once.

A story comes as the request's body, the sound as the browser recorded it or as the phone's recorder saved it: read
whole (it is at most stories.MOST_BYTES), then kept by a thread, with the catalog's write. Its file is in the data
folder, served by /story/{id} to whoever may see what it is about.
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from lineagebox import changes, events, search, speech, stories
from lineagebox.folders import FolderIndex
from lineagebox.web.common import MEMBER, PRIVATE, _from_app, _image_or_404, _people_or_404
from lineagebox.web.deps import con, page, site, viewer, writer
from lineagebox.web.site import Site

router = APIRouter()
_RECEIVING = asyncio.Semaphore(2)  # recordings being received at once (each held in memory, up to MOST_BYTES)
ACCESS = {
    "/person/{person_id}/stories": MEMBER,
    "/story/{story_id}": MEMBER,
    "/api/stories": MEMBER,
    "/api/stories/{story_id}/words": MEMBER,   # whoever recorded it, or an admin (stories.may_change)
    "/api/stories/{story_id}/remove": MEMBER,  # the same
    "/api/images/{image_id}/transcript": MEMBER,
}
BODIES = {"/api/stories": None}  # over common.BODY_MOST: read in pieces, to stories.MOST_BYTES


def about_person(c, person_id: int) -> dict:
    """What a person's stories can be about, for the record form: them, or one of their life events."""
    name = c.execute("SELECT name FROM person WHERE id = ?", (person_id,)).fetchone()[0]
    choices = []
    for e in events.for_person(c, person_id):
        label = (e["type"] if e["tag"] == "EVEN" and e["type"] else
                 (events.COUPLE_EVENTS if e["partner"] else events.PERSON_EVENTS).get(e["tag"], e["tag"]))
        if e["partner"]:
            label += f" with {e['partner']}"
        if e["date"]:
            label += f", {events.show_date(e['date'])}"
        choices.append((e["id"], label))
    return {"kind": "person", "id": person_id, "name": name, "events": choices}


@router.get("/person/{person_id}/stories", response_class=HTMLResponse)
def person_stories(request: Request, person_id: int, v=Depends(viewer), c=Depends(con)):
    """A person's stories, with the way to record one, for their page (which loads it once it shows)."""
    _people_or_404(c, person_id)
    return page(request, "_stories.html", stories=stories.named(stories.for_person(c, person_id), v),
                about=about_person(c, person_id), most_minutes=stories.MOST_SECONDS // 60)


@router.get("/story/{story_id}")
def story_sound(story_id: int, c=Depends(con), s: Site = Depends(site)):
    """A story's sound, to whoever may see what it is about. Range requests let the player seek in it."""
    row = stories.get(c, story_id)
    path = stories.path_of(s.cfg, row) if row else None
    if path is None or not path.is_file():
        raise HTTPException(404, "No such story")
    return FileResponse(path, media_type=row["mime"], headers=PRIVATE)


def _story_spec(story_id: int | None = None):
    if story_id is None:  # a story being recorded: the row it adds
        return lambda new: [("story", "id > ?", (new["story"],))]
    return lambda new: [("story", "id = ?", (story_id,))]


def _indexed(w, image_id: int | None) -> None:
    """A photo's stories are found by search at once, on it and its near-duplicates (search shows one of them)."""
    if image_id is not None:
        search.index_group(w, image_id, FolderIndex(w))
    w.commit()


@router.post("/api/stories", dependencies=[Depends(_from_app)])
async def record_story(request: Request, image: int | None = None, person: int | None = None,
                       event: int | None = None, title: str = Query("", max_length=stories.TITLE_MOST + 50),
                       seconds: float | None = Query(None, ge=0, le=86400), v=Depends(viewer), c=Depends(con),
                       w=Depends(writer), s: Site = Depends(site)):
    """A story recorded about a photo (image=) or a person (person=, and event= for one of their life events), as
    the request's body."""
    if (image is None) == (person is None) or (event is not None and person is None):
        raise HTTPException(400, "A story is about a photo, or about a person.")
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > stories.MOST_BYTES:
        raise stories.TooBig(f"That recording is over {stories.MOST_BYTES >> 20} MB. Record it in parts, or keep the "
                             "long recording in a photo folder: it is catalogued there.")

    def allowed():  # before anything is read: what it is about must be theirs to see, and not too many at once
        if image is not None:
            _image_or_404(c, image)
        else:
            _people_or_404(c, person)
            if event is not None and not c.execute("""SELECT 1 FROM event WHERE id = :e AND (person_id = :p
                                                      OR :p IN (couple_a, couple_b))""",
                                                   {"e": event, "p": person}).fetchone():
                raise HTTPException(404, "No such life event")
        if v.limited and not s.upload_tries.allow(str(v.account_id)):
            raise HTTPException(429, "That's a lot of recordings at once: wait an hour, then go on.")

    await run_in_threadpool(allowed)
    async with _RECEIVING:  # each recording is held in memory until it is kept: only a few at once on a small box
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > stories.MOST_BYTES:
                raise stories.TooBig(f"That recording is over {stories.MOST_BYTES >> 20} MB.")
        return await run_in_threadpool(keep_story, w, v, s, data, image, person, event, title, seconds)


def keep_story(w, v, s, data: bytes, image, person, event, title, seconds) -> dict:
    """A recording, checked by record_story, kept and found by search."""
    what = "a photo" if image is not None else "a life event" if event is not None else "a person"
    with changes.tracking(w, v, "story", f"Recorded a story about {what}", _story_spec()) as kept:
        story_id = stories.save(w, s.cfg, bytes(data), v.account_id, image_id=image, person_id=person,
                                event_id=event, title=title, seconds=seconds)
    _indexed(w, image)
    return {"id": story_id, "undo": kept.id, "reload": True,
            "message": "Your story is kept. Thank you for telling it."}


def _story_to_change(c, v, story_id: int):
    row = stories.get(c, story_id)  # c: a member sees only the stories about what they see
    if row is None:
        raise HTTPException(404, "No such story")
    if not stories.may_change(row, v.account_id, v.admin):
        raise HTTPException(403, "Only whoever recorded a story, or an admin, can change it.")
    return row


class StoryWordsBody(BaseModel):
    title: str = Field(default="", max_length=stories.TITLE_MOST + 50)
    words: str | None = Field(default=None, max_length=stories.WORDS_MOST + 500)


@router.post("/api/stories/{story_id}/words", dependencies=[Depends(_from_app)])
def story_words(story_id: int, body: StoryWordsBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    row = _story_to_change(c, v, story_id)
    with changes.tracking(w, v, "story", "Corrected a recorded story's words", _story_spec(story_id)) as kept:
        stories.correct(w, story_id, body.title, body.words)
    _indexed(w, row["image_id"])
    return {"undo": kept.id, "reload": True}


@router.post("/api/stories/{story_id}/remove", dependencies=[Depends(_from_app)])
def remove_story(story_id: int, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    row = _story_to_change(c, v, story_id)
    with changes.tracking(w, v, "story", "Took a recorded story away", _story_spec(story_id)) as kept:
        stories.remove(w, story_id)
    _indexed(w, row["image_id"])
    return {"undo": kept.id, "message": "Taken away."}


class LineBody(BaseModel):
    start: float = Field(ge=0)
    text: str = Field(default="", max_length=speech.LINE_MOST + 100)
    heard: bool = False  # put back what Whisper heard


@router.post("/api/images/{image_id}/transcript", dependencies=[Depends(_from_app)])
def correct_line(image_id: int, body: LineBody, v=Depends(viewer), c=Depends(con), w=Depends(writer)):
    """One line of a video's or recording's transcript put right (or back as Whisper heard it)."""
    _image_or_404(c, image_id)
    if c.execute("SELECT 1 FROM transcript WHERE image_id = ?", (image_id,)).fetchone() is None:
        raise HTTPException(404, "It hasn't been transcribed yet.")
    summary = "Put a transcript's line back as heard" if body.heard else "Corrected a line of a transcript"
    with changes.tracking(w, v, "words", summary, lambda new: [("transcript", "image_id = ?", (image_id,))]) as kept:
        speech.correct(w, image_id, body.start, None if body.heard else body.text)
    search.index_group(w, image_id, FolderIndex(w))
    w.commit()
    return {"undo": kept.id, "reload": True}
