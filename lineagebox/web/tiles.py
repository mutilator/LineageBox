"""A grid's tiles: what each says to someone who can't see it (who is in the photo, when it was taken, a video or a
recording), and a sharper picture for sharp screens.

Alt text. The grid macros (_macros.html) ask tile_alts() for the photos they show, which reads the people named in
them in one query on the viewer's own connection: a member's person view holds only the people they see, so nobody
from outside their part of the family is named. page() (deps.py) makes that connection the one read while a page is
made (reading()); a page made without one gets the date alone, as before.

Sharp tiles. The thumbnail is cfg.thumb_size (400 px) on its long side, a little soft on a phone's screen, where a
tile is three times as many pixels as it looks. A tile twice that size is made from the 2,048 px preview the first time
a sharp screen asks for it (the grids' srcset), and kept beside the thumbnails (cfg.sharp_thumb_path): never for the
whole library at once, which would take a long time on a small router and room that most photos never need. It is
made again when the preview is newer (a photo turned, a preview made again). A photo with no preview gets its
thumbnail instead, so a tile is never left empty, and so does every photo while the disk is nearly full, or while
others wait their turn too long (WAIT); the browser asks again for those the next time it shows them.

Shapes. A square tile shows the middle of a photo, which cuts the heads off people standing in a tall one and the
people at the ends off a wide one. tile_shapes() aims each tile's crop at the photo's faces instead (focus()), and
gives each photo's shape, for those who choose Whole photos (More → Photo tiles): rows of photos in their own shapes,
as Home's days are.
"""
import contextlib
import contextvars
import json
import shutil
import sqlite3
import threading
from collections import defaultdict
from pathlib import Path

from PIL import Image

from lineagebox import media
from lineagebox.config import Config
from lineagebox.uploads import MARGIN
from lineagebox.web.common import fmt_date

READING: contextvars.ContextVar = contextvars.ContextVar("tiles_reading", default=None)
MOST_NAMES = 4  # named in a tile's alt text; any more are "and 3 others"
KINDS = {"video": "Video", "audio": "Sound recording"}

# Sharp tiles made at the same time: each takes a moment of the router's CPU, and a phone asks for a screenful at once
MAKING = threading.Semaphore(2)
# Seconds a request waits for its turn to make one; then it gets the thumbnail for now (sharp the next time). Each
# waiting request holds one of the web app's threads, which every other request needs too, even to be signed in.
WAIT = 2


@contextlib.contextmanager
def reading(con: sqlite3.Connection | None):
    """While a page is made: the connection the tiles' names are read on (the viewer's own)."""
    token = READING.set(con)
    try:
        yield
    finally:
        READING.reset(token)


def names(con: sqlite3.Connection, ids) -> dict[int, list[str]]:
    """{image id: the names of the people in it}, left to right as their faces are, then those added by hand. On a
    member's connection, only people they see."""
    ids = sorted({int(i) for i in ids})
    if not ids:
        return {}
    out: dict[int, list[str]] = defaultdict(list)
    for image_id, name in con.execute("""
            SELECT x.image_id, p.name FROM (
                SELECT image_id, person_id, min(x1) AS at FROM face
                WHERE person_id IS NOT NULL AND image_id IN (SELECT value FROM json_each(:ids))
                GROUP BY image_id, person_id
                UNION ALL
                SELECT image_id, person_id, 2 FROM image_person WHERE image_id IN (SELECT value FROM json_each(:ids))
            ) x JOIN person p ON p.id = x.person_id ORDER BY x.image_id, x.at, p.name""", {"ids": json.dumps(ids)}):
        if name not in out[image_id]:
            out[image_id].append(name)
    return out


def said(people: list[str]) -> str:
    """'Emma', 'Emma and Jack', 'Emma, Jack and Lily', 'Emma, Jack, Lily, Ann and 3 others'."""
    shown, rest = people[:MOST_NAMES], len(people) - MOST_NAMES
    if rest > 0:
        return f"{', '.join(shown)} and {rest} other{'' if rest == 1 else 's'}"
    return shown[0] if len(shown) == 1 else f"{', '.join(shown[:-1])} and {shown[-1]}"


def _get(im, key: str):
    """A field of a photo's row or dict, None if the list didn't ask for it (Cleanup's lists have fewer)."""
    try:
        return im[key]
    except (KeyError, IndexError):
        return None


def alt(im, people: list[str]) -> str:
    """A tile's alt text: 'Photo of Emma and Jack, Jun 3, 2012', or 'Video from Jun 3, 2012' with nobody named."""
    kind = KINDS.get(_get(im, "media"), "Photo")
    taken = _get(im, "taken_at")
    try:
        when = fmt_date(taken, False) if taken else ""
    except (TypeError, ValueError):  # not a date as the catalog keeps them
        when = ""
    if people:
        return f"{kind} of {said(people)}" + (f", {when}" if when else "")
    return f"{kind} from {when}" if when else f"{kind}, date not known"


def tile_alts(images) -> dict[int, str]:
    """For the grid macros: {image id: alt text} for the photos of one grid, in one query."""
    images = list(images or [])
    con = READING.get()
    found = {}
    if con is not None and images:
        try:
            found = names(con, (im["id"] for im in images))
        except sqlite3.Error:  # a page is still shown with dates alone (a connection already closed, a busy catalog)
            found = {}
    return {im["id"]: alt(im, found.get(im["id"], [])) for im in images}


# A face less than this share of the largest one's width is someone in the background: it doesn't move the crop
BACKGROUND = 0.4
# Whole photos: shapes kept in bounds, as Home's days (routes/home.py), so a panorama or a sliver never takes a row
WIDEST, NARROWEST = 2.5, 0.4


def focus(width: int | None, height: int | None, boxes) -> str | None:
    """Where a square tile's crop of a photo is aimed (CSS object-position): the middle of the faces that matter (the
    largest, and those at least BACKGROUND of its width), as near the tile's middle as the photo's edges allow; when
    they don't all fit, the stretch that shows the most of them (larger faces counting for more), rather than the
    empty middle between two people at the ends of a wide photo. None when the middle will do: no faces, a square
    photo, a shape not known. boxes: (x1, y1, x2, y2) as shares of the upright photo, as faces keep them.

    A tile of another shape shows more of the photo than a square, around the same place, so the same aim keeps the
    faces in it too (a Whole photos tile, a panorama kept in bounds)."""
    boxes = [b for b in boxes if b[2] > b[0] and b[3] > b[1]]
    if not width or not height or width == height or not boxes:
        return None
    largest = max(x2 - x1 for x1, _, x2, _ in boxes)
    near = [b for b in boxes if b[2] - b[0] >= BACKGROUND * largest]
    if width > height:  # a square shows height/width of its width
        return f"{_aim([(b[0], b[2]) for b in near], height / width):.0f}% 50%"
    return f"50% {_aim([(b[1], b[3]) for b in near], width / height):.0f}%"


def _aim(spans: list[tuple[float, float]], shown: float) -> float:
    """The object-position, in percent, of a crop that shows `shown` of a photo's side, for faces that span `spans`
    of that side: their middle, as near the crop's middle as the photo's edges allow, or, when they don't all fit,
    the crop that holds the most of them whole (by their size), the nearest to their middle of those."""
    first, last = min(a for a, _ in spans), max(b for _, b in spans)
    middle = (first + last) / 2
    if last - first > shown:
        starts = [a for a, _ in spans] + [b - shown for _, b in spans]
        held = lambda s: sum(b - a for a, b in spans if a >= s - 1e-9 and b <= s + shown + 1e-9)
        best = max(starts, key=lambda s: (round(held(s), 6), -abs(s + shown / 2 - middle)))
        middle = best + shown / 2
    start = min(max(middle - shown / 2, 0.0), 1 - shown)
    return start / (1 - shown) * 100


def shape(width: int | None, height: int | None) -> float:
    """A photo's width over its height, kept in bounds; 1 for a shape not known (a recording)."""
    return round(min(WIDEST, max(NARROWEST, width / height)), 3) if width and height else 1.0


def tile_shapes(images) -> dict[int, dict]:
    """For the grid macros: {image id: {"ratio": its shape, "focus": where a square crop is aimed, or None}} for the
    photos of one grid, in two queries. Faces on a video's frames don't aim its tile (its thumbnail is another frame),
    and nor do boxes someone said aren't a face."""
    ids = sorted({int(im["id"]) for im in images or []})
    con = READING.get()
    if con is None or not ids:
        return {}
    try:
        sizes = {r[0]: (r[1], r[2]) for r in con.execute(
            "SELECT id, width, height FROM image WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(ids),))}
        boxes: dict[int, list] = defaultdict(list)
        for r in con.execute("""SELECT image_id, x1, y1, x2, y2 FROM face
                                WHERE image_id IN (SELECT value FROM json_each(?)) AND frame IS NULL
                                  AND x1 IS NOT NULL AND assigned_by IS NOT 'not_a_face'""", (json.dumps(ids),)):
            boxes[r[0]].append(tuple(r[1:]))
    except sqlite3.Error:  # the page is still shown, with its tiles cropped in the middle
        return {}
    return {i: {"ratio": shape(*sizes.get(i, (None, None))), "focus": focus(*sizes.get(i, (None, None)), boxes[i])}
            for i in ids}


def sharp(cfg: Config, sha: str) -> Path | None:
    """The tile twice the thumbnail's size, made now if it isn't there or its preview is newer; None when there is no
    preview to make it from, the disk is nearly full, or others kept it waiting more than WAIT seconds."""
    preview, path = cfg.preview_path(sha), cfg.sharp_thumb_path(sha)
    try:
        made_from = preview.stat().st_mtime
    except OSError:
        return None
    if _fresh(path, made_from):
        return path
    if not MAKING.acquire(timeout=WAIT):  # a screenful is being made: the thumbnail for now
        return None
    try:
        if _fresh(path, made_from):  # made while this waited
            return path
        if _room(cfg) < MARGIN:  # the disk is nearly full: what room there is goes to the photos (uploads.MARGIN)
            return None
        try:
            with Image.open(preview) as im:
                size = cfg.thumb_size * 2
                im.draft("RGB", (size, size))  # JPEG: decoded at half or a quarter of the size, which is quicker
                tile = im.convert("RGB") if im.mode not in ("RGB", "RGBA") else im.copy()
        except OSError:
            return None
        tile.thumbnail((size, size), Image.Resampling.LANCZOS)
        try:
            media._atomic_save(tile, path, "WEBP", quality=80)
        except OSError:  # a full disk, a data folder it can't write: the thumbnail does
            return None
    finally:
        MAKING.release()
    return path


def _room(cfg: Config) -> int:
    try:
        return shutil.disk_usage(cfg.data_dir).free
    except OSError:
        return 0


def _fresh(path: Path, made_from: float) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    return st.st_size > 0 and st.st_mtime >= made_from
