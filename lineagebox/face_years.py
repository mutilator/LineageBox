"""Someone's face through the years: one photo of them for each year, their face lined up on the eyes, shown one after
another (Through the years, on the person page: web/routes/browse.py, the pictures in web/routes/faces.py).

Two ways to pick the photos:
- every photo: their clearest face each year, in any photo (a snapshot, a group);
- portraits: only photos the AI described as a portrait or a selfie, or tagged portrait, selfie or headshot (by hand or
  by the AI), with nobody else as near the camera (no other face ALONE of their face's width). Posed, facing the camera
  and alone, so one year follows on from the next more smoothly. A photo whose portrait tag was taken off isn't one.

The clearest face is large on the photo's preview (more of it to line up), facing the camera (facing()) and sharp
(ArcFace's embedding norm, face.quality, which blur and odd angles lower). One named by hand counts a little more than
one recognised, which may be someone else. Each year keeps its best PER_YEAR, so a page can offer another from that
year.

Left out: videos (their faces are on frames, not the photo), faces set aside, faces with no landmarks or too small to
line up (SMALLEST_EYES), photos with no date of their own (only a file's time), and photos dated by hand across more
than SPAN_YEARS ("1960s"), which can't say which year they belong to.

The picture (aligned()) is the face turned so the eyes are level, at the same place and the same distance apart in
every picture, from the photo's preview, kept beside the face crops (cfg.aligned_face_path).
"""
import sqlite3
from dataclasses import dataclass, field
from math import hypot
from pathlib import Path

import numpy as np
from PIL import Image

from lineagebox import events
from lineagebox.catalog import VISIBLE

PER_YEAR = 4          # faces kept for each year: the best, and the next ones
SMALLEST_EYES = 20    # px between the eyes on the preview: a smaller face lined up is too blurred to be worth showing
SHARP_EYES = 90       # px between the eyes past which a face is no clearer in the picture
SPAN_YEARS = 2        # a date set by hand may span this many years and still give the photo its year
ALONE = 0.5           # portraits: nobody else's face is half as wide as theirs, or wider
PORTRAIT_TYPES = ("portrait", "selfie")              # the AI's photo_type (describe.py)
PORTRAIT_TAGS = ("portrait", "selfie", "headshot")   # tags, as stored (catalog.tag_name)
# A face looking at the camera, measured on one boy's faces from birth to 15 (2026-11-11): its nose this far from its
# eyes (0) to its mouth (1), and its mouth at least FRONT_MOUTH of the distance between its eyes below them. Looking
# down puts the nose near the mouth and the mouth near the eyes (0.91, 0.62); a photo taken from below, the nose up by
# the eyes (-0.07, 0.20). Babies' faces are shorter (0.7), which costs every face of their first year alike.
FRONT_NOSE = (0.5, 0.75)
FRONT_MOUTH = 0.85

SIZE = (400, 500)     # the picture: a head and shoulders, a little taller than wide
EYES_AT = 0.42        # the eyes' height in it, from the top
EYES_APART = 0.25     # the distance between the eyes, as a share of its width: the face, hair and some shoulder

_PORTRAITS = f"""
    AND (i.id IN (SELECT a.image_id FROM analysis a
                  WHERE a.photo_type IN ({", ".join(f"'{t}'" for t in PORTRAIT_TYPES)})
                    AND a.created_at = (SELECT max(b.created_at) FROM analysis b WHERE b.image_id = a.image_id))
         OR i.id IN (SELECT it.image_id FROM image_tag it JOIN tag t ON t.id = it.tag_id
                     WHERE t.name IN ({", ".join(f"'{t}'" for t in PORTRAIT_TAGS)}) AND it.source != 'not'))
    AND i.id NOT IN (SELECT it.image_id FROM image_tag it JOIN tag t ON t.id = it.tag_id
                     WHERE t.name = 'portrait' AND it.source = 'not')
    AND NOT EXISTS (SELECT 1 FROM face o WHERE o.image_id = f.image_id AND o.id != f.id AND o.frame IS NULL
                    AND o.assigned_by IS NOT 'not_a_face' AND (o.x2 - o.x1) >= :alone * (f.x2 - f.x1))"""


@dataclass
class Year:
    year: str
    faces: list[dict] = field(default_factory=list)  # the best first: id, image_id, taken_at, date_text, age, score


def _points(landmarks: bytes | None) -> np.ndarray | None:
    """The five landmarks (eyes, nose, mouth corners: the eye on the photo's left first) as shares of the picture."""
    if not landmarks:
        return None
    pts = np.frombuffer(landmarks, dtype=np.float32)
    return pts.reshape(5, 2).astype(np.float64) if pts.size == 10 and np.isfinite(pts).all() else None


def measure(landmarks: bytes | None, width: int | None, height: int | None, preview_size: int) -> tuple | None:
    """(pixels between the eyes on the preview, how squarely the face looks at the camera: facing()), or None for a
    face that can't be lined up."""
    pts = _points(landmarks)
    if pts is None or not width or not height:
        return None
    scale = min(1.0, preview_size / max(width, height))
    px = pts * np.array([width * scale, height * scale])
    apart = float(hypot(*(px[1] - px[0])))
    return None if apart < SMALLEST_EYES else (apart, facing(px))


def facing(px: np.ndarray) -> float:
    """How squarely a face looks at the camera, from its landmarks in pixels: 1, down to 0.05 for one in profile or
    tipped well up or down. Turned aside, its nose isn't midway between its eyes; tipped, its nose isn't where
    FRONT_NOSE says between its eyes and its mouth, and its mouth comes up towards its eyes (FRONT_MOUTH)."""
    left, right, nose, mouth = px[0], px[1], px[2], (px[3] + px[4]) / 2
    apart = float(hypot(*(right - left)))
    if apart <= 0:
        return 0.05
    along = (right - left) / apart
    down_face = np.array([-along[1], along[0]])  # across the eyes, towards the mouth, however the face is turned
    middle = (left + right) / 2
    mouth_below = float(np.dot(mouth - middle, down_face))
    if mouth_below <= 0:  # a mouth above the eyes: landmarks that make no face
        return 0.05
    turned = min(0.5, abs(float(np.dot(nose - left, along)) / apart - 0.5))  # 0 midway, 0.5 over an eye
    nose_at = float(np.dot(nose - middle, down_face)) / mouth_below
    tipped = max(0.0, FRONT_NOSE[0] - nose_at, nose_at - FRONT_NOSE[1])
    short = max(0.0, FRONT_MOUTH - mouth_below / apart)
    return max(0.05, 1 - 2.5 * turned - 1.5 * tipped - 1.5 * short)


def score(apart: float, facing: float, quality: float | None, by_hand: bool) -> float:
    """How good a face is for its year: large, facing the camera, sharp, and named by hand. 0 to about 1.15."""
    size = min(apart, SHARP_EYES) / SHARP_EYES
    sharp = min(1.0, max(0.2, ((quality or 0) - 12) / 14))
    return size * facing * sharp * (1.15 if by_hand else 1.0)


def _year(r) -> str | None:
    """The year a photo belongs to: its date's, unless that is a span set by hand of more than SPAN_YEARS."""
    if r["taken_at_source"] == "user" and r["date_from"] and r["date_to"]:
        if int(r["date_to"][:4]) - int(r["date_from"][:4]) > SPAN_YEARS:
            return None
    return r["taken_at"][:4]


def years(con: sqlite3.Connection, pid: int, preview_size: int, portraits: bool = False,
          per_year: int = PER_YEAR) -> list[Year]:
    """Someone's years with a face of them good enough to line up, the earliest first, each with its best faces. On a
    member's connection, only photos they see."""
    rows = con.execute(f"""
        SELECT f.id, f.image_id, f.landmarks, f.quality, f.assigned_by, i.width, i.height, i.taken_at,
               i.taken_at_source, i.date_from, i.date_to, i.date_text
        FROM face f JOIN image i ON i.id = f.image_id
        WHERE f.person_id = :p AND f.assigned_by IN ('user', 'auto') AND f.frame IS NULL AND f.landmarks IS NOT NULL
          AND i.media = 'photo' AND {VISIBLE} AND i.taken_at IS NOT NULL AND i.taken_at_source != 'mtime'
          {_PORTRAITS if portraits else ""}""", {"p": pid, "alone": ALONE}).fetchall()
    birth = con.execute("SELECT sort_date FROM event WHERE person_id = ? AND tag = 'BIRT' AND sort_date IS NOT NULL",
                        (pid,)).fetchone()
    births = {pid: birth[0]} if birth else {}
    by_year: dict[str, list[dict]] = {}
    for r in rows:
        year, measured = _year(r), measure(r["landmarks"], r["width"], r["height"], preview_size)
        if year is None or measured is None:
            continue
        by_year.setdefault(year, []).append({
            "id": r["id"], "image_id": r["image_id"], "taken_at": r["taken_at"],
            "date_text": r["date_text"] if r["taken_at_source"] == "user" else None,
            "score": round(score(*measured, r["quality"], r["assigned_by"] == "user"), 4), "row": r})
    out = []
    for year in sorted(by_year):
        best = sorted(by_year[year], key=lambda x: (-x["score"], x["taken_at"], x["id"]))[:per_year]
        for x in best:
            x["age"] = _age(con, x.pop("row"), pid, births)
        out.append(Year(year, best))
    return out


def _age(con: sqlite3.Connection, img, pid: int, births: dict) -> str | None:
    """Their age in the photo ("6", "about 6", "6–7"), as its page says it; None without a birth date."""
    if not births:
        return None
    try:
        return events.ages(con, img, [pid], births).get(pid)
    except (TypeError, ValueError):  # a date the catalog can't read as a day
        return None


def aligned(picture: Path, landmarks: bytes) -> Image.Image:
    """The face on this picture (a photo's preview), turned so its eyes are level, scaled so they are EYES_APART of
    the width apart, and moved so they sit EYES_AT down the middle of a SIZE picture. Past the photo's edges (a face
    near one, or turned) is the photo's own average colour, which stands out less than black.
    Raises ValueError for landmarks that can't be lined up (both eyes in one place)."""
    pts = _points(landmarks)
    if pts is None:
        raise ValueError("This face has no landmarks to line up")
    with Image.open(picture) as im:
        im = im.convert("RGB")
    width, height = SIZE
    wanted = EYES_APART * width
    apart = hypot(*((pts[1] - pts[0]) * np.array(im.size)))
    if apart < 1:
        raise ValueError("This face's eyes are in one place")
    shrink = int(apart / wanted)  # much larger than wanted: made smaller first, so the turn doesn't leave it jagged
    if shrink >= 2:
        im = im.reduce(shrink)
    (lx, ly), (rx, ry) = pts[0] * np.array(im.size), pts[1] * np.array(im.size)
    apart = hypot(rx - lx, ry - ly)
    cos, sin, k = (rx - lx) / apart, (ry - ly) / apart, apart / wanted  # the eyes' line, and photo pixels per pixel
    tx, ty = (width - wanted) / 2, EYES_AT * height  # where the eye on the left goes
    # the picture's (x, y) is the photo's (k cos x - k sin y + c, k sin x + k cos y + f): the eye on the left at
    # (tx, ty), the other EYES_APART of the width to its right
    c = lx - k * cos * tx + k * sin * ty
    f = ly - k * sin * tx - k * cos * ty
    fill = im.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
    return im.transform(SIZE, Image.Transform.AFFINE, (k * cos, -k * sin, c, k * sin, k * cos, f),
                        resample=Image.Resampling.BICUBIC, fillcolor=fill)

