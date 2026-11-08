"""A camera whose clock was wrong for a while, put right from its own photos (Cleanup → Camera clocks, for admins).

A camera's clock runs on after a wrong setting, a time zone left behind or a flat battery that took it back to its
first day: every photo it takes until someone sets it again is off by about the same amount, and a clock gains or
loses a little as the months go by. An admin looks through the camera's photos in the order it took them (by the dates
it wrote), picks the first and the last it dated wrong (a stretch), and says when one of them was really taken (a pin):
every photo of the stretch moves as much as that one. Pins on two or more of its photos spread what changed between
them over the photos in between, as a clock that drifted; before the first pin and after the last, the nearest one's
amount holds. Photos with someone named in them before they were born (face_ages) are marked, as the usual sign.

The stretches are kept (camera_clock): a photo of that camera read again later, or copied in again, is moved too
(ingest calls apply_new), and a stretch can run on to every later photo, those read later too. image.camera_taken_at
keeps what the camera wrote, so a stretch changed works from that and never moves a photo twice. A date set by hand is
the person's and stays; its automatic date, to go back to, is moved. Undoable on Recent changes like any change (the
routes keep it with changes.tracking; undoing calls redate).
"""
import calendar
import json
import sqlite3
from datetime import datetime, time, timedelta

from lineagebox import errors
from lineagebox.catalog import VISIBLE

OWN = ("exif", "camera")  # dates a camera wrote: the photo's own (EXIF) or a video's
OWN_SQL = """coalesce(i.camera_taken_at,
                     CASE WHEN i.taken_at_source IN ('exif', 'camera') THEN i.taken_at
                          WHEN i.taken_at_source = 'user' AND i.auto_taken_at_source IN ('exif', 'camera')
                          THEN i.auto_taken_at END)"""  # own_date, in SQL
END = "9999-12-31T23:59:59"  # a stretch's last that runs on to every later photo
FIRST_PHOTO_YEAR = 1826  # the oldest photograph known: a date before it is a typing mistake
PER_PAGE = 300  # a month's photos shown at once
REINDEX_AT_ONCE = 200  # photos searched again at once; more wait for the worker's next pass (search_stale)
_EPOCH = datetime(1970, 1, 1)
_COLUMNS = ("i.id, i.sha256, i.media, i.dup_group_id, i.taken_at, i.taken_at_source, i.auto_taken_at, "
            "i.auto_taken_at_source, i.camera_taken_at")


class Problem(errors.Problem):
    """A change that can't be made; the message is shown to the admin."""


def own_date(row) -> str | None:
    """The date the camera wrote on a photo, before any move: kept in camera_taken_at once it was moved; else its
    date, or (dated by hand) its automatic date, when that is the camera's. None when the camera wrote none."""
    if row["camera_taken_at"]:
        return row["camera_taken_at"]
    if row["taken_at_source"] in OWN:
        return row["taken_at"]
    if row["taken_at_source"] == "user" and row["auto_taken_at_source"] in OWN:
        return row["auto_taken_at"]
    return None


def _seconds(iso: str) -> int:
    return int((datetime.fromisoformat(iso) - _EPOCH).total_seconds())


def moved(iso: str, seconds: int) -> str:
    """A date and time moved by seconds; one that would leave the calendar (a camera that wrote the year 9999) stays."""
    try:
        return (datetime.fromisoformat(iso) + timedelta(seconds=seconds)).isoformat(timespec="seconds")
    except OverflowError:
        return iso


def offset(pins: list[dict], at: str) -> int:
    """How many seconds a stretch moves its photo the camera dated `at`: as much as its pin; with more, as much as the
    pins either side of it, in proportion to where it is between them (a clock that gained or lost time as it went);
    before the first pin and after the last, as much as that one. pins: in the order the camera took them."""
    points = [(_seconds(p["at"]), _seconds(p["real"]) - _seconds(p["at"])) for p in pins]
    t = _seconds(at)
    if t <= points[0][0]:
        return points[0][1]
    for (t0, d0), (t1, d1) in zip(points, points[1:]):
        if t <= t1:
            return round(d0 + (d1 - d0) * (t - t0) / (t1 - t0)) if t1 > t0 else d1
    return points[-1][1]


def _stretch(row) -> dict:
    return {**dict(row), "pins": sorted(json.loads(row["pins"]), key=lambda p: (p["at"], p["real"]))}


def stretches(con: sqlite3.Connection, camera: str) -> list[dict]:
    """The camera's stretches of wrong dates, in the order it took them, each with its pins."""
    return [_stretch(r) for r in con.execute("SELECT * FROM camera_clock WHERE camera = ? ORDER BY first, id",
                                             (camera,))]


def stretch(con: sqlite3.Connection, stretch_id: int) -> dict | None:
    row = con.execute("SELECT * FROM camera_clock WHERE id = ?", (stretch_id,)).fetchone()
    return _stretch(row) if row else None


def holding(camera_stretches: list[dict], own: str) -> dict | None:
    """The stretch a date the camera wrote is in, or None."""
    return next((s for s in camera_stretches if s["first"] <= own <= s["last"]), None)


def date_for(camera_stretches: list[dict], own: str) -> str:
    """Where the camera's stretches put one of its dates."""
    s = holding(camera_stretches, own)
    return moved(own, offset(s["pins"], own)) if s else own


def cameras(con: sqlite3.Connection, born_after: dict[str, int] | None = None) -> list[dict]:
    """Every camera with its photos, the first and last dates it wrote, its stretches, and (born_after: face_ages.
    before_birth_by_camera) how many named faces in its photos were taken before the person was born; those with such
    faces first."""
    out = []
    for r in con.execute(f"""SELECT i.camera, count(*) AS photos, min({OWN_SQL}) AS first, max({OWN_SQL}) AS last,
                                    (SELECT count(*) FROM camera_clock k WHERE k.camera = i.camera) AS stretches
                             FROM image i WHERE i.camera IS NOT NULL AND {VISIBLE}
                               AND (i.dup_group_id IS NULL OR i.dup_group_id = i.id) GROUP BY i.camera"""):
        out.append({**dict(r), "born_after": (born_after or {}).get(r["camera"], 0)})
    return sorted(out, key=lambda c: (-c["born_after"], -c["photos"], c["camera"].lower()))


def photos(con: sqlite3.Connection, camera: str) -> list[dict]:
    """The camera's photos still on the share, one of each group of copies, in the order it took them (by the date it
    wrote: "own"); those it wrote no date on are left out."""
    rows = con.execute(f"SELECT {_COLUMNS} FROM image i WHERE i.camera = ? AND {VISIBLE}", (camera,)).fetchall()
    kept: dict[int, dict] = {}
    for r in rows:
        if (own := own_date(r)) is None:
            continue
        group = r["dup_group_id"] or r["id"]
        if group not in kept or r["id"] == group:  # the group's own photo, when it is this camera's
            kept[group] = {**dict(r), "own": own}
    return sorted(kept.values(), key=lambda p: (p["own"], p["id"]))


def _photo(con: sqlite3.Connection, camera: str, image_id: int) -> dict:
    """One of the camera's photos, with the date it wrote (own)."""
    row = con.execute(f"SELECT {_COLUMNS} FROM image i WHERE i.id = ? AND i.camera = ?", (image_id, camera)).fetchone()
    if row is None or own_date(row) is None:
        raise Problem("That photo isn't one this camera dated.")
    return {**dict(row), "own": own_date(row)}


def real_date(day: str, at: str, own: str) -> str:
    """When a photo was really taken, from the day and time of day typed; with no time, the time the camera wrote."""
    try:
        d = datetime.strptime(day.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise Problem("Pick the day it was really taken.") from None
    try:
        t = time.fromisoformat(at.strip()) if at.strip() else datetime.fromisoformat(own).time()
    except ValueError:
        raise Problem("Type the time of day as 14:30, or leave it empty for the camera's.") from None
    real = datetime.combine(d, t.replace(microsecond=0, tzinfo=None))
    if real.year < FIRST_PHOTO_YEAR:
        raise Problem("That's before any photo was taken: check the year.")
    if real > datetime.now() + timedelta(days=1):
        raise Problem("That day hasn't come yet: check the year.")
    return real.isoformat(timespec="seconds")


def _check(con: sqlite3.Connection, new: dict) -> None:
    """A stretch as it is about to be kept: not over another of the camera's, its pins inside it and in the order the
    camera took them, and none of its photos put in the future."""
    if new["last"] < new["first"]:
        raise Problem("The last photo comes before the first.")
    for other in stretches(con, new["camera"]):
        if other["id"] != new.get("id") and other["first"] <= new["last"] and new["first"] <= other["last"]:
            raise Problem("Some of these photos are put right already, with others: change those instead, or put them "
                          "back first.")
    pins = new["pins"]
    if not pins:
        raise Problem("Say when one of its photos was really taken.")
    if any(not new["first"] <= p["at"] <= new["last"] for p in pins):
        raise Problem("That photo isn't one of these.")
    for a, b in zip(pins, pins[1:]):
        if b["real"] < a["real"]:
            raise Problem(f"The camera took that one after the photo dated {a['real'][:16].replace('T', ' ')}, so it "
                          "can't have been taken before it: a clock only runs forward.")
    soon = (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds")
    for r in con.execute(f"SELECT {_COLUMNS} FROM image i WHERE i.camera = ?", (new["camera"],)):
        own = own_date(r)
        if own and new["first"] <= own <= new["last"] and moved(own, offset(pins, own)) > soon:
            raise Problem(f"That would put a photo the camera dated {own[:10]} in the future: check the date, or end "
                          "the wrong dates sooner.")


def create(con: sqlite3.Connection, camera: str, first_id: int, last_id: int | None, image_id: int, day: str,
           at: str, account_id: int | None = None) -> int:
    """A new stretch, from the camera's photo first_id to last_id (None: on to every later photo), with image_id said
    to have been taken on day at the time of day typed. Commits. Returns the stretch's id."""
    a = _photo(con, camera, first_id)["own"]
    b = _photo(con, camera, last_id)["own"] if last_id is not None else END
    a, b = min(a, b), max(a, b)
    p = _photo(con, camera, image_id)
    pins = [{"at": p["own"], "real": real_date(day, at, p["own"]), "image": image_id}]
    _check(con, {"camera": camera, "first": a, "last": b, "pins": pins})
    new_id = con.execute("""INSERT INTO camera_clock(camera, first, last, pins, made_at, account_id)
                            VALUES (?, ?, ?, ?, ?, ?)""",
                         (camera, a, b, json.dumps(pins), datetime.now().isoformat(timespec="seconds"),
                          account_id)).lastrowid
    _apply(con, camera)
    return new_id


def _kept(con: sqlite3.Connection, s: dict) -> None:
    _check(con, s)
    con.execute("UPDATE camera_clock SET first = ?, last = ?, pins = ? WHERE id = ?",
                (s["first"], s["last"], json.dumps(s["pins"]), s["id"]))
    _apply(con, s["camera"])


def pin(con: sqlite3.Connection, s: dict, image_id: int, day: str, at: str) -> None:
    """One more of the stretch's photos said to have been taken on day at a time (or its date said again). Commits."""
    p = _photo(con, s["camera"], image_id)
    if not s["first"] <= p["own"] <= s["last"]:
        raise Problem("That photo isn't one of these.")
    real = real_date(day, at, p["own"])
    pins = [q for q in s["pins"] if q["image"] != image_id and q["at"] != p["own"]]  # a photo taken the same second
    _kept(con, {**s, "pins": sorted([*pins, {"at": p["own"], "real": real, "image": image_id}],
                                    key=lambda q: (q["at"], q["real"]))})


def unpin(con: sqlite3.Connection, s: dict, image_id: int) -> None:
    """A photo's date forgotten: the stretch's others say how far its photos move. Commits."""
    pins = [q for q in s["pins"] if q["image"] != image_id]
    if len(pins) == len(s["pins"]):
        raise Problem("That photo wasn't dated here.")
    if not pins:
        raise Problem("It's the only photo of these you dated: put them all back instead.")
    _kept(con, {**s, "pins": pins})


def bounds(con: sqlite3.Connection, s: dict, first_id: int | None = None, last_id: int | None = None,
           to_end: bool = False) -> int:
    """The stretch made to start or end at one of the camera's photos (to_end: run on to every later photo). The
    photos dated in it that it no longer holds are forgotten; returns how many. Commits."""
    first = _photo(con, s["camera"], first_id)["own"] if first_id is not None else s["first"]
    last = END if to_end else _photo(con, s["camera"], last_id)["own"] if last_id is not None else s["last"]
    if last < first:
        raise Problem("The last photo comes before the first.")
    pins = [q for q in s["pins"] if first <= q["at"] <= last]
    if not pins:
        raise Problem("Keep at least one of the photos you dated in it, or put them all back instead.")
    _kept(con, {**s, "first": first, "last": last, "pins": pins})
    return len(s["pins"]) - len(pins)


def remove(con: sqlite3.Connection, s: dict) -> None:
    """The stretch forgotten: its photos take the dates the camera wrote again. Commits."""
    con.execute("DELETE FROM camera_clock WHERE id = ?", (s["id"],))
    _apply(con, s["camera"])


def count(camera_photos: list[dict], first: str, last: str) -> int:
    return sum(first <= p["own"] <= last for p in camera_photos)


def _apply(con: sqlite3.Connection, camera: str) -> None:
    """Every photo of the camera dated from what it wrote and its stretches, then their copies and the search."""
    changed = _redate(con, stretches(con, camera), con.execute(
        f"SELECT {_COLUMNS} FROM image i WHERE i.camera = ?", (camera,)).fetchall())
    con.commit()
    from lineagebox import dedup  # copies of these photos take their new dates
    dedup.share_dates(con)
    _search_again(con, changed)


def apply_new(con: sqlite3.Connection, image_id: int, camera: str | None) -> None:
    """A photo just read (ingest): moved as the camera's stretches say. Doesn't commit."""
    if not camera:
        return
    if camera_stretches := stretches(con, camera):
        _redate(con, camera_stretches, con.execute(f"SELECT {_COLUMNS} FROM image i WHERE i.id = ?",
                                                   (image_id,)).fetchall())


def redate(con: sqlite3.Connection, camera: str) -> int:
    """Every photo of the camera dated again from what it wrote and the stretches kept: once a change is undone
    (Recent changes puts back only the photos it moved), the photos read since, which apply_new moved, go back too.
    Commits. Returns how many photos' dates changed."""
    changed = _redate(con, stretches(con, camera), con.execute(
        f"SELECT {_COLUMNS} FROM image i WHERE i.camera = ?", (camera,)).fetchall())
    con.commit()
    _search_again(con, changed)
    return len(changed)


def _redate(con: sqlite3.Connection, camera_stretches: list[dict], rows) -> list[int]:
    """Date each photo from what its camera wrote and the stretches. Returns the photos whose date changed."""
    changed = []
    for r in rows:
        own = own_date(r)
        if own is None:
            continue
        new = date_for(camera_stretches, own)
        kept = own if new != own else None
        if r["taken_at_source"] in OWN:
            if (new, kept) != (r["taken_at"], r["camera_taken_at"]):
                con.execute("UPDATE image SET taken_at = ?, camera_taken_at = ? WHERE id = ?", (new, kept, r["id"]))
                if new != r["taken_at"]:
                    changed.append(r["id"])
        elif r["taken_at_source"] == "user" and r["auto_taken_at_source"] in OWN:
            if (new, kept) != (r["auto_taken_at"], r["camera_taken_at"]):
                con.execute("UPDATE image SET auto_taken_at = ?, camera_taken_at = ? WHERE id = ?",
                            (new, kept, r["id"]))
    return changed


def _search_again(con: sqlite3.Connection, image_ids: list[int]) -> None:
    """The photos are found by their new years: a few at once, the rest by the worker's next pass."""
    from lineagebox import search
    from lineagebox.folders import FolderIndex
    con.executemany("INSERT OR IGNORE INTO search_stale(image_id) VALUES (?)", ((i,) for i in image_ids))
    con.commit()
    folders = FolderIndex(con)
    for n, image_id in enumerate(image_ids[:REINDEX_AT_ONCE], 1):
        search.index_image(con, image_id, folders)
        if n % search.COMMIT_EVERY == 0:
            con.commit()
    con.commit()


# ---- what the page shows ---------------------------------------------------------------------------------------------

def _plus_months(dt: datetime, months: int) -> datetime:
    y, m = divmod(dt.month - 1 + months, 12)
    return dt.replace(year=dt.year + y, month=m + 1, day=min(dt.day, calendar.monthrange(dt.year + y, m + 1)[1]))


def amount(seconds: int, at: str = "") -> list[tuple[int, str]]:
    """How far a photo moves, as people say it, to the minute: by the calendar from `at`, the date the camera wrote
    (two years from Nov 28, 2008 is Nov 28, 2010: (2, "years")), else in days, hours and minutes (a clock's drift).
    Its two largest parts when there are years or months, else three."""
    if at and seconds:
        a, b = sorted((datetime.fromisoformat(at), datetime.fromisoformat(moved(at, seconds))))
        months = (b.year - a.year) * 12 + b.month - a.month
        while months and _plus_months(a, months) > b:
            months -= 1
        rest = b - _plus_months(a, months)
        parts = [(months // 12, "years"), (months % 12, "months"), (rest.days, "days"),
                 (rest.seconds // 3600, "hours"), (rest.seconds % 3600 // 60, "minutes")]
    else:
        left = abs(seconds) // 60
        parts = [(left // 1440, "days"), (left % 1440 // 60, "hours"), (left % 60, "minutes")]
    out = [(n, unit) for n, unit in parts if n]
    return out[:2] if out and out[0][1] in ("years", "months") else out[:3]


def described(s: dict, camera_photos: list[dict]) -> dict:
    """A stretch for the page: its first and last photo, how many it holds, how far they move (at its start and end,
    which differ when its clock drifted) and its pins with their photos."""
    inside = [p for p in camera_photos if s["first"] <= p["own"] <= s["last"]]
    start = offset(s["pins"], inside[0]["own"] if inside else s["first"])
    end = offset(s["pins"], inside[-1]["own"] if inside else s["pins"][-1]["at"])
    by_id = {p["id"]: p for p in inside}
    return {**s, "count": len(inside), "first_photo": inside[0] if inside else None,
            "last_photo": inside[-1] if inside else None, "open": s["last"] == END,
            "move": start, "drift": end - start,
            "pinned": [{**q, "photo": by_id.get(q["image"])} for q in s["pins"]]}


def _born_after(checks: list[dict], camera: str) -> dict[int, list[str]]:
    """The camera's photos with someone named in them before they were born: photo -> their names."""
    out: dict[int, list[str]] = {}
    for c in checks:
        if c["why"] == "born after" and c["camera"] == camera and c["source"] in OWN:
            out.setdefault(c["image_id"], [])
            if c["name"] not in out[c["image_id"]]:
                out[c["image_id"]].append(c["name"])
    return out


def _in_it(con: sqlite3.Connection, image_id: int, born: dict[int, str]) -> list[dict]:
    """Who is named in a photo, with their birth date when the tree has one: a help to tell when it was taken."""
    rows = con.execute("""SELECT DISTINCT p.id, p.name FROM face f JOIN person p ON p.id = f.person_id
                          WHERE f.image_id = ? AND f.assigned_by IN ('user', 'auto') ORDER BY p.name""", (image_id,))
    return [{"id": r["id"], "name": r["name"], "born": born.get(r["id"])} for r in rows]


def page(con: sqlite3.Connection, camera: str, checks: list[dict], year: str = "", month: str = "",
         page_no: int = 0, photo_id: int | None = None, from_id: int | None = None, to: str = "") -> dict:
    """What the camera clock page shows for one camera: its years and months, a month of its photos by day (each
    marked with its stretch, its pin, someone in it not born yet), the stretches, the photo picked with who is in it,
    and the stretch being picked (from_id to `to`: a photo's id, "end", or nothing yet). checks: face_ages.age_checks.
    """
    from lineagebox import face_ages
    camera_photos = photos(con, camera)
    by_id = {p["id"]: p for p in camera_photos}
    camera_stretches = [described(s, camera_photos) for s in stretches(con, camera)]
    flagged = _born_after(checks, camera)
    picked = by_id.get(photo_id) if photo_id else None
    draft = _draft(camera_photos, camera_stretches, by_id.get(from_id) if from_id else None, to, by_id)

    if not (year and month and any(p["own"][:7] == f"{year}-{month}" for p in camera_photos)):
        # where to start: the year asked for, the photo picked, the first of the stretch being picked, the earliest
        # photo with someone not born yet, the first stretch, else the camera's first photo
        lead = (next((p for p in camera_photos if p["own"][:4] == year), None)
                or picked or (draft and draft["from"]) or next((p for p in camera_photos if p["id"] in flagged), None)
                or next((s["first_photo"] for s in camera_stretches if s["first_photo"]), None)
                or (camera_photos[0] if camera_photos else None))
        year, month = (lead["own"][:4], lead["own"][5:7]) if lead else ("", "")
    years: dict[str, dict] = {}
    months: dict[str, dict] = {}
    for p in camera_photos:
        for key, into in ((p["own"][:4], years), (p["own"][5:7], months if p["own"][:4] == year else None)):
            if into is not None:
                n = into.setdefault(key, {"photos": 0, "flagged": 0})
                n["photos"] += 1
                n["flagged"] += p["id"] in flagged
    shown = [p for p in camera_photos if p["own"][:7] == f"{year}-{month}"]
    pages = max(1, -(-len(shown) // PER_PAGE))
    if picked in shown and not page_no:
        page_no = shown.index(picked) // PER_PAGE
    page_no = min(max(page_no, 0), pages - 1)
    in_draft = lambda p: bool(draft and draft["last"] and draft["first"] <= p["own"] <= draft["last"])
    days: list[dict] = []
    for p in shown[page_no * PER_PAGE:(page_no + 1) * PER_PAGE]:
        s = holding(camera_stretches, p["own"])
        tile = {**p, "stretch": s["id"] if s else None,
                "pinned": bool(s and any(q["image"] == p["id"] for q in s["pins"])),
                "by_hand": p["taken_at_source"] == "user", "flagged": flagged.get(p["id"], []), "draft": in_draft(p),
                "start": bool(draft and p["id"] == draft["from"]["id"])}
        if not days or days[-1]["day"] != p["own"][:10]:
            days.append({"day": p["own"][:10], "photos": []})
        days[-1]["photos"].append(tile)

    chosen = None
    if picked:
        s = holding(camera_stretches, picked["own"])
        chosen = {**picked, "stretch": s, "by_hand": picked["taken_at_source"] == "user", "in_draft": in_draft(picked),
                  "move": offset(s["pins"], picked["own"]) if s else 0,
                  "pin": next((q for q in s["pins"] if q["image"] == picked["id"]), None) if s else None,
                  "people": _in_it(con, picked["id"], face_ages.births(con)),
                  "flagged": flagged.get(picked["id"], []),
                  # the stretches either side of it, to take it into: the nearest before and after it
                  "before": None if s else next((t for t in reversed(camera_stretches)
                                                 if t["last"] < picked["own"]), None),
                  "after": None if s else next((t for t in camera_stretches if t["first"] > picked["own"]), None)}
    return {"years": sorted(years.items()), "year": year, "month": month, "months": sorted(months.items()),
            "days": days, "page": page_no, "pages": pages, "in_month": len(shown), "stretches": camera_stretches,
            "picked": chosen, "draft": draft, "flagged": len(flagged), "photos": len(camera_photos)}


def _draft(camera_photos: list[dict], camera_stretches: list[dict], start: dict | None, to: str,
           by_id: dict) -> dict | None:
    """The stretch being picked: from its first photo to its last (to: a photo's id), on to every later photo (to
    "end"), or its first photo only so far; with how many photos it holds and a stretch it would overlap."""
    if start is None:
        return None
    end = by_id.get(int(to)) if to.isdecimal() else None
    if end and end["own"] < start["own"]:
        start, end = end, start
    first, last = start["own"], END if to == "end" else end["own"] if end else None
    return {"from": start, "to": end, "open": to == "end", "first": first, "last": last,
            "count": count(camera_photos, first, last) if last else None,
            "over": next((s for s in camera_stretches if last and s["first"] <= last and first <= s["last"]), None)}

