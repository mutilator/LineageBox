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
