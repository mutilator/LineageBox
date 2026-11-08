"""Recorded stories: a voice note about a photo, or about a person or one of their life events, recorded in the
browser or picked from a phone's own recorder. Older relatives tell what they remember instead of typing it.

The sound is kept as it came, in the data folder's stories/ (never on the photo share: it is the family's, like the
catalog, and the nightly backup copies it beside the catalog's copy, backup.py). Whisper, when it is on, writes down
what is said (speech.py), and a photo's stories are found by search with its caption and comments (search.py).

Anyone who can see the photo or the person may record one; whoever recorded it, or an admin, may correct its words or
take it away. Taking one away only hides it (removed_at), so Undo (changes.py) can bring it back; KEEP_REMOVED_DAYS
later the worker deletes it and its file for good (purge), and with them files no story refers to any more (a
recording undone, a photo or person gone).
"""
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from lineagebox import errors, video
from lineagebox.config import Config

MOST_BYTES = 60 * 2**20  # an hour of a phone's voice memo; 30 minutes recorded in the browser is about 8 MB
MOST_SECONDS = 30 * 60   # a story, not a tape: a longer recording goes in a photo folder (it is catalogued there)
TITLE_MOST = 120         # characters in the few words typed with one
WORDS_MOST = 20000       # characters in a corrected transcript (30 minutes of speech is about 25,000)
KEEP_REMOVED_DAYS = 30   # a story taken away can be brought back (Undo) this long
LEFT_OVER = timedelta(days=1)  # a file no story refers to, this old, is set aside (newer: one being saved just now)
FOLDER = "stories"
# The kinds of sound accepted, by what the file starts with: what browsers record (WebM/Opus, MP4/AAC, Ogg) and what
# phones' recorders save (M4A, 3GP, AMR, WAV, MP3). Anything else is refused, whatever it says it is.
KINDS = {
    "webm": ("audio/webm", lambda b: b[:4] == b"\x1a\x45\xdf\xa3"),
    "ogg": ("audio/ogg", lambda b: b[:4] == b"OggS"),
    "m4a": ("audio/mp4", lambda b: b[4:8] == b"ftyp" and b[8:11] not in (b"3gp", b"3g2")),
    "3gp": ("audio/3gpp", lambda b: b[4:8] == b"ftyp"),
    "wav": ("audio/wav", lambda b: b[:4] == b"RIFF" and b[8:12] == b"WAVE"),
    "mp3": ("audio/mpeg", lambda b: b[:3] == b"ID3" or (len(b) > 1 and b[0] == 0xFF and b[1] & 0xE0 == 0xE0)),
    "amr": ("audio/amr", lambda b: b[:5] == b"#!AMR"),
    "flac": ("audio/flac", lambda b: b[:4] == b"fLaC"),
}


class Problem(errors.Problem):
    """A story that can't be saved; the message is shown to whoever recorded it."""


class TooBig(Problem):
    status = 413


def folder(cfg: Config) -> Path:
    return cfg.data_dir / FOLDER


def path_of(cfg: Config, row) -> Path:
    return folder(cfg) / row["file"]


def kind_of(head: bytes) -> str | None:
    """The kind of sound a file is, from its first bytes (KINDS); None if it isn't one taken."""
    return next((k for k, (_, looks) in KINDS.items() if looks(head)), None)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _title(text: str | None) -> str | None:
    text = " ".join((text or "").split())
    if len(text) > TITLE_MOST:
        raise Problem(f"Keep its title under {TITLE_MOST} characters: the story itself says the rest.")
    return text or None


def save(con: sqlite3.Connection, cfg: Config, data: bytes, account_id: int | None, *, image_id: int | None = None,
         person_id: int | None = None, event_id: int | None = None, title: str | None = None,
         seconds: float | None = None) -> int:
    """Keep a recorded story about a photo, or a person (and one of their events). The file is written first, then
    the row (the caller's tracked change); a file whose row never comes is deleted later (purge). seconds: how long
    the browser says it recorded, used when ffprobe can't tell (a browser's WebM often doesn't say). Returns its id.
    Doesn't commit."""
    if (image_id is None) == (person_id is None):
        raise Problem("A story is about a photo, or about a person.")
    if not data:
        raise Problem("Nothing was recorded: try again, a little closer to the microphone.")
    if len(data) > MOST_BYTES:
        raise TooBig(f"That recording is over {MOST_BYTES >> 20} MB. Record it in parts, or keep the long recording "
                     "in a photo folder: it is catalogued there.")
    kind = kind_of(data[:16])
    if kind is None:
        raise Problem("That isn't a sound recording LineageBox can keep (it takes what browsers and phones record: "
                      "WebM, M4A, MP3, WAV, Ogg, 3GP, AMR or FLAC).")
    title = _title(title)
    name = secrets.token_hex(16)
    rel = f"{name[:2]}/{name}.{kind}"
    target = folder(cfg) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(f".{target.name}.part")
    try:
        part.write_bytes(data)
        os.replace(part, target)
    finally:
        part.unlink(missing_ok=True)
    seconds = _length(target) or (round(float(seconds), 1) if seconds and 0 < seconds < 86400 else None)
    if seconds is not None and seconds > MOST_SECONDS + 5:
        target.unlink(missing_ok=True)
        raise TooBig(f"That recording is {round(seconds / 60)} minutes long: a story can be up to "
                     f"{MOST_SECONDS // 60}. Record it in parts, or keep the long recording in a photo folder.")
    return con.execute("""INSERT INTO main.story(image_id, person_id, event_id, file, mime, bytes, seconds, title,
                                                 account_id, at)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       (image_id, person_id, event_id, rel, KINDS[kind][0], len(data), seconds, title, account_id,
                        _now())).lastrowid


def _length(path: Path) -> float | None:
    """How long a recording is, when ffprobe is there to tell (a browser's WebM often says nothing of its length in
    its header, so ffprobe may not know either)."""
    if not video.available():
        return None
    try:
        return video.probe(path).duration
    except video.Error:
        return None


def get(con: sqlite3.Connection, story_id: int) -> sqlite3.Row | None:
    """A story that is there (not taken away); on a member's connection, only one about what they see."""
    return con.execute("SELECT * FROM story WHERE id = ? AND removed_at IS NULL", (story_id,)).fetchone()


def may_change(row, account_id: int | None, admin: bool) -> bool:
    """A story's words are corrected, or it is taken away, by whoever recorded it, or by an admin."""
    return admin or (account_id is not None and row["account_id"] == account_id)


def remove(con: sqlite3.Connection, story_id: int) -> None:
    """Take a story away: hidden at once, deleted for good after KEEP_REMOVED_DAYS (purge). Doesn't commit."""
    con.execute("UPDATE main.story SET removed_at = ? WHERE id = ? AND removed_at IS NULL", (_now(), story_id))


def correct(con: sqlite3.Connection, story_id: int, title: str | None, words: str | None) -> None:
    """Change a story's title, and what it says as written down (words; None leaves them as they are; the very words
    Whisper heard leave them as Whisper's). Words a person wrote are never written over by Whisper. Doesn't commit."""
    title = _title(title)
    con.execute("UPDATE main.story SET title = ? WHERE id = ? AND coalesce(title, '') != coalesce(?, '')",
                (title, story_id, title))
    if words is not None:
        words = "\n".join(" ".join(line.split()) for line in words.strip().splitlines()).strip()
        if len(words) > WORDS_MOST:
            raise Problem(f"That is {len(words):,} characters: keep it under {WORDS_MOST:,}.")
        heard = con.execute("SELECT heard FROM main.story WHERE id = ?", (story_id,)).fetchone()[0]
        mine = None if words == (heard or "") else words
        con.execute("UPDATE main.story SET words = ? WHERE id = ? AND coalesce(words, '') != coalesce(?, '')",
                    (mine, story_id, mine))


def listing(con: sqlite3.Connection, where: str, args: tuple) -> list[dict]:
    """Stories (not taken away), oldest first, each with who recorded it (their name in the family, else the start of
    their email; None at home without family accounts) and the event it is about. con: the viewer's own connection
    (a member's holds only the stories about what they see); who recorded each comes from every account, and the
    caller hides the names a member may not see (named())."""
    rows = con.execute(f"""SELECT s.*, p.name AS person_name, a.person_id AS by_person, a.email,
                                  e.tag AS event_tag, e.type AS event_type, e.date AS event_date
                           FROM story s LEFT JOIN main.account a ON a.id = s.account_id
                           LEFT JOIN main.person p ON p.id = a.person_id LEFT JOIN event e ON e.id = s.event_id
                           WHERE s.removed_at IS NULL AND ({where}) ORDER BY s.at, s.id""", args).fetchall()
    return [{"id": r["id"], "image_id": r["image_id"], "person_id": r["person_id"], "event_id": r["event_id"],
             "title": r["title"], "seconds": r["seconds"], "at": r["at"], "account_id": r["account_id"],
             "by_person": r["by_person"], "mime": r["mime"],
             "by": r["person_name"] or (r["email"].split("@")[0] if r["email"] else None),
             "words": r["words"] if r["words"] is not None else r["heard"], "corrected": r["words"] is not None,
             "heard": r["heard"] is not None,
             "event": {"tag": r["event_tag"], "type": r["event_type"], "date": r["event_date"]}
             if r["event_id"] else None}
            for r in rows]


def for_image(con: sqlite3.Connection, image_id: int) -> list[dict]:
    return listing(con, "s.image_id = ?", (image_id,))


def for_person(con: sqlite3.Connection, person_id: int) -> list[dict]:
    """A person's stories: about them, and about their life events (a wedding's, from either partner's page)."""
    return listing(con, """s.person_id = :p OR s.event_id IN (SELECT id FROM event WHERE person_id = :p
                                                              OR couple_a = :p OR couple_b = :p)""", {"p": person_id})


def named(items: list[dict], viewer) -> list[dict]:
    """A member sees who recorded a story only if that is someone in their part of the family (or themselves)."""
    if viewer.limited:
        for item in items:
            if item["account_id"] != viewer.account_id and item["by_person"] not in viewer.people:
                item["by"] = "a family member"
    return items


def purge(con: sqlite3.Connection, cfg: Config, now: datetime | None = None) -> int:
    """Delete for good the stories taken away KEEP_REMOVED_DAYS ago, with their sound. A file no story refers to is
    never deleted: it may be a recording made after a catalog put back from an older backup. Once it is a day old, and
    undoing a change wouldn't bring its story back (_undoable), it is moved into stories/SET_ASIDE, out of the way but
    kept. Returns how many files were deleted or set aside. Commits."""
    now = now or datetime.now()
    cutoff = (now - timedelta(days=KEEP_REMOVED_DAYS)).isoformat(timespec="seconds")
    gone = [r[0] for r in con.execute("SELECT file FROM story WHERE removed_at IS NOT NULL AND removed_at < ?",
                                      (cutoff,))]
    con.execute("DELETE FROM story WHERE removed_at IS NOT NULL AND removed_at < ?", (cutoff,))
    con.commit()
    kept = {r[0] for r in con.execute("SELECT file FROM story")} | _undoable(con)
    root, done = folder(cfg), 0
    if not root.is_dir():
        return 0
    for rel in gone:  # taken away a month ago, by whoever recorded it or an admin
        if rel and rel not in kept and (root / rel).is_file():
            (root / rel).unlink(missing_ok=True)
            done += 1
    for path in root.glob("*/*"):
        if path.parent.name == SET_ASIDE:
            continue
        rel = path.relative_to(root).as_posix()
        try:
            old = datetime.fromtimestamp(path.stat().st_mtime) < now - LEFT_OVER
        except OSError:
            continue
        if rel not in kept and old and path.is_file():
            (root / SET_ASIDE).mkdir(exist_ok=True)
            path.replace(root / SET_ASIDE / path.name)  # names are random: no two the same
            done += 1
    return done


SET_ASIDE = "set-aside"  # sound files no story refers to, kept out of the way (never deleted by LineageBox)


def _undoable(con: sqlite3.Connection) -> set[str]:
    """The files of stories a change deleted that undoing it would put back (someone removed, and with them the
    stories taken away from them, ON DELETE CASCADE): kept for as long as that change can be undone (changes.undo;
    one undone can't be undone again)."""
    out = set()
    for (undo,) in con.execute("""SELECT c.undo FROM change_log c WHERE c.undone_at IS NULL
                                  AND instr(c.undo, '"story"') AND EXISTS (SELECT 1 FROM
                                  json_each(c.undo, '$.before.story'))"""):
        saved = json.loads(undo)
        after = saved.get("after", {}).get("story", {})
        out |= {row["file"] for key, row in saved["before"]["story"].items() if key not in after and row.get("file")}
    return out


def for_export(con: sqlite3.Connection) -> list[dict]:
    """Every story there is, for the export (export.py): what it is about, who recorded it and when, its words, and
    its file's name in the stories folder."""
    out = []
    for r in con.execute("""SELECT s.*, a.email, a.person_id AS by_person, e.person_id AS event_person, e.couple_a,
                                   e.couple_b, e.tag, e.type, e.date FROM story s
                            LEFT JOIN account a ON a.id = s.account_id LEFT JOIN event e ON e.id = s.event_id
                            WHERE s.removed_at IS NULL ORDER BY s.id"""):
        out.append({"id": r["id"], "image_id": r["image_id"], "person": r["person_id"], "file": r["file"],
                    "mime": r["mime"], "seconds": r["seconds"], "title": r["title"], "at": r["at"],
                    "by": {k: v for k, v in (("email", r["email"]), ("person", r["by_person"])) if v} or None,
                    "words": r["words"] if r["words"] is not None else r["heard"],
                    "words_by_hand": r["words"] is not None,
                    "event": {k: v for k, v in (("tag", r["tag"]), ("type", r["type"]), ("date", r["date"]))
                              if v} if r["event_id"] else None})
    return out


def counts(con: sqlite3.Connection) -> dict:
    """How many stories there are and how many wait for Whisper, for the Status page."""
    row = con.execute("""SELECT count(*), coalesce(sum(heard IS NULL), 0) FROM story
                         WHERE removed_at IS NULL""").fetchone()
    return {"stories": row[0], "untold": row[1]}
