"""Folder names -> structured context (place, event, people hints, date), and each image's merged context."""
import json
import re
import sqlite3
from datetime import date
from dataclasses import dataclass, field

from lineagebox import retry
from lineagebox.events import day_first
from lineagebox.llm import Ollama

BATCH = 25
NO_CONTEXT_KINDS = {"camera_or_device_dump", "junk_or_generated", "media_type"}

SCHEMA = {"type": "object", "required": ["folders"], "properties": {"folders": {"type": "array", "items": {
    "type": "object",
    "required": ["id", "kind", "place", "place_type", "event", "people", "year", "month"],
    "properties": {
        "id": {"type": "integer"},
        "kind": {"type": "string", "enum": ["date", "event", "place", "person", "group_of_people", "time_period",
                                           "camera_or_device_dump", "media_type", "junk_or_generated", "misc"]},
        "place": {"type": ["string", "null"]},
        "place_type": {"type": ["string", "null"]},
        "event": {"type": ["string", "null"]},
        "people": {"type": "array", "items": {"type": "string"}},
        "year": {"type": ["integer", "null"]},
        "month": {"type": ["integer", "null"]},
    }}}}}

SYSTEM = """You interpret folder names from a family photo library{about}.
Each line is "<id>: <path>". Return one entry per id. Describe ONLY the LAST folder segment, using the parent
segments as context.
- kind: what the last segment mainly names.
- place: proper place name if one is named or clearly implied (e.g. "Grand Canyon, Arizona", "San Diego Zoo"), else null.
- place_type: generic type of place if known (zoo, beach, farm, church, school, home, fair, ...), else null.
- event: the occasion (e.g. "Emma's 5th birthday", "Christmas", "Halloween", "wedding of Anna & Tom"), else null.
- people: first names or family names written in the segment (e.g. "Emma", "Jack", "Millers"). Business and
  place names are not people ("Harper's Bakery" names a place). Never guess.
- year/month: only if the path states or clearly implies them (2-digit years like "Easter13" mean 2013).
Folders like Thumbs, Previews, .thumbnails, DCIM, 100NIKON, AVCHD, Data, Modified, Originals are junk_or_generated
or camera_or_device_dump. Folders of camera files such as "Phone Dump" are camera_or_device_dump."""

_DAY = [re.compile(r"(?:img_)?((?:19|20)\d{2})-?(\d{2})-?(\d{2})(?:_\d+)?$", re.I),  # 2007-03-08, 20160427
        re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{2})$")]                          # 6.21.15


def regex_day(name: str, day_first: bool = False) -> tuple[int, int, int] | None:
    """Exact dates written in a folder name; month/year-only hints are left to the LLM. 6.21.15 is month first, or
    with day_first (21.6.15) day first."""
    if m := _DAY[0].search(name):
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    elif m := _DAY[1].search(name):
        mo, d, y = int(m[1]), int(m[2]), 2000 + int(m[3])
        if day_first:
            mo, d = d, mo
        if y > date.today().year:  # a two-digit year from the last century: "5.6.98" is 1998
            y -= 100
    else:
        return None
    return (y, mo, d) if 1 <= mo <= 12 and 1 <= d <= 31 else None


def parse_folders(con: sqlite3.Connection, client: Ollama, progress=None, about: str = "") -> tuple[int, int]:
    """Parse folders that have not been parsed yet (kind IS NULL). Returns (parsed, still missing). about says whose
    library it is ("a family in Oregon, USA"), which helps with local place names. A batch the model can't answer as
    JSON, and folders its answer leaves out, are tried again later (retry.py), not first on every pass."""
    system = SYSTEM.replace("{about}", f" ({about})" if about else "")
    order = day_first(con)
    rows = con.execute(f"""SELECT id, path, name FROM folder f WHERE kind IS NULL AND source = 'auto'
                           AND NOT ignored AND path != '' AND {retry.waiting("folders", "f.id")}
                           ORDER BY path""").fetchall()
    parsed = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        lines = "\n".join(f"{n}: {r['path']}" for n, r in enumerate(batch, 1))
        try:
            try:
                answer, _ = client.chat_json(system, "Folder paths:\n" + lines, SCHEMA, temperature=0.0)
            except ValueError:  # it repeated a phrase until cut off; a less cautious answer usually breaks the loop
                answer, _ = client.chat_json(system, "Folder paths:\n" + lines, SCHEMA, temperature=0.7)
        except ValueError as e:
            for r in batch:
                retry.later(con, "folders", r["id"], f"the AI's answer wasn't JSON: {e}")
            continue
        by_id = {a.get("id"): a for a in answer.get("folders", [])}
        left_out = []
        for n, r in enumerate(batch, 1):
            a = by_id.get(n)
            if a is None:
                left_out.append(r["id"])
                continue
            year, month, day = a.get("year"), a.get("month"), None
            if exact := regex_day(r["name"], order):
                year, month, day = exact
            con.execute("""UPDATE folder SET kind = ?, place = ?, place_type = ?, event = ?, people_hint = ?,
                           year = ?, month = ?, day = ? WHERE id = ?""",
                        (a["kind"], a.get("place"), a.get("place_type"), a.get("event"),
                         json.dumps(a.get("people") or []), year, month, day, r["id"]))
            parsed += 1
        con.commit()
        for folder_id in left_out:
            retry.later(con, "folders", folder_id, "the AI's answer left it out")
        if progress:
            progress(min(i + BATCH, len(rows)), len(rows))
    return parsed, len(rows) - parsed


def date_from_folders(con: sqlite3.Connection) -> int:
    """Re-date photos whose only date is the file time (when a copy was made) from dates in their folder names.

    The folder date wins when it names a month, or names a different year than the file time. A year-only folder
    that agrees with the file time keeps the more precise file time. Returns the number of photos re-dated.
    """
    folders = FolderIndex(con)
    changed = []
    for r in con.execute("SELECT id, taken_at FROM image WHERE taken_at_source = 'mtime'").fetchall():
        ctx = folders.context(r["id"])
        if not ctx.year:
            continue
        if ctx.month is None and r["taken_at"] and int(r["taken_at"][:4]) == ctx.year:
            continue
        changed.append((f"{ctx.year:04d}-{ctx.month or 1:02d}-{ctx.day or 1:02d}T00:00:00", r["id"]))
    con.executemany("UPDATE image SET taken_at = ?, taken_at_source = 'folder' WHERE id = ?", changed)
    con.commit()
    return len(changed)


@dataclass
class Context:
    folders: list[str] = field(default_factory=list)  # every folder path the image is filed under
    places: list[str] = field(default_factory=list)   # "Riverside Zoo (zoo)", deepest folder first
    events: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)   # names in folder names: hints, not identities
    year: int | None = None
    month: int | None = None
    day: int | None = None


class FolderIndex:
    """All folders in memory, to merge context along each file's folder chain."""

    def __init__(self, con: sqlite3.Connection):
        self.rows = {r["id"]: r for r in con.execute("SELECT * FROM folder")}
        self.con = con

    def chain(self, folder_id: int) -> list[sqlite3.Row]:
        out = []
        while folder_id in self.rows:
            row = self.rows[folder_id]
            if row["path"] == "":
                break
            out.append(row)
            folder_id = row["parent_id"]
        return out  # deepest first

    def context(self, image_id: int) -> Context:
        ctx = Context()
        best_precision = 0
        for (folder_id,) in self.con.execute(
                "SELECT DISTINCT folder_id FROM file WHERE image_id = ? AND NOT missing", (image_id,)):
            chain = self.chain(folder_id)
            if not chain:
                continue
            ctx.folders.append(chain[0]["path"])
            year = month = day = None
            for f in chain:
                if f["kind"] not in NO_CONTEXT_KINDS:
                    if f["place"]:
                        _add_place(ctx.places, f["place"], f["place_type"])
                    if f["event"]:
                        _add(ctx.events, f["event"])
                    for name in json.loads(f["people_hint"] or "[]"):
                        _add(ctx.people, name)
                # each part comes from the deepest folder that states it: "2019\Recital May2519" gives 2019-05
                year = year or f["year"]
                month = month or f["month"]
                day = day or f["day"]
            precision = (year is not None) + (month is not None) + (day is not None)
            if precision > best_precision:
                best_precision, ctx.year, ctx.month, ctx.day = precision, year, month, day
        return ctx


def _add(items: list[str], value: str) -> None:
    if value.lower() not in (v.lower() for v in items):
        items.append(value)


def _add_place(places: list[str], name: str, place_type: str | None) -> None:
    """'Riverside Zoo (zoo)': one entry per place name, keeping the type if any folder gives one."""
    label = f"{name} ({place_type})" if place_type else name
    for i, existing in enumerate(places):
        if existing.lower() == name.lower() or existing.lower().startswith(name.lower() + " ("):
            if place_type and existing.lower() == name.lower():
                places[i] = label
            return
    places.append(label)
