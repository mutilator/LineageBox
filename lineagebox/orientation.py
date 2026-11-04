"""Photos on their side or upside down, found by the faces in them, for Cleanup → Turned photos.

A face's landmarks (the eyes and the corners of the mouth) say which way its head is: from the mouth up to the eyes.
A photo is suggested when its clear faces agree that it is turned, by the same quarter turn, and none of them is
upright: two people standing side by side in a photo taken sideways. A child hanging upside down from the monkey bars
among people standing is not, because the people standing are upright.

People lying down side by side, or bent over the same thing, have their heads the same way too, in a photo that is the
right way up. When Ollama is on, the AI gives a second opinion: shown the photo both ways (check_with_ai), it says which
is the right way up. Measured on the library LineageBox was developed on, about 19 in 20 of the photos whose clear
faces agreed (none upright) were turned; the rest were people asleep or bent over something. The AI called most of
those rest fine, and a few of the turned ones too. So a photo is "sure" when the faces agree and the AI does not say
otherwise (nearly all of them turned), and "worth a look" when it does (about 1 in 10). The AI's own descriptions are
no help: written from the photo as it is, they call people in a sideways photo "lying down".

Most photos taken sideways have one person in them (dozens of times as many there as with two or more faces left
after the first ones were turned), and one face alone is often a head tilted or someone lying down. So the AI is asked
about those too, and a photo with one clear face is "sure" when the AI agrees it is turned. The setting turn_suggest
picks what is listed: "sure" (the default), "more" (also those worth a look, and photos with one clear face the AI
hasn't agreed on), or "off". Photos with no clear face can't be told by their faces at all.

Turning a photo writes its orientation tag (writeback.py), so other programs show it the same way.
"""
import io
import math
import random
import sqlite3
from collections import Counter, defaultdict

import numpy as np
from PIL import Image

from lineagebox import settings, writeback
from lineagebox.catalog import VISIBLE
from lineagebox.config import Config
from lineagebox.faces import usable_faces_sql

OK_KEY = "turn_ok"   # settings: photos marked fine as they are
AI_KEY = "turn_ai"   # settings: image id -> what the AI said: "turned" or "fine"
TILT = 30            # degrees from a quarter turn that still count as it; more is a head tilted, which says nothing
# EXIF orientation <-> (mirrored, degrees clockwise the picture is turned to be shown)
_TAGS = {1: (False, 0), 6: (False, 90), 3: (False, 180), 8: (False, 270),
         2: (True, 0), 7: (True, 90), 4: (True, 180), 5: (True, 270)}
ASK = ("These are the same photo, the second turned a different way from the first. Which one is the right way up, as "
       "the photographer saw the scene? Judge by the whole scene: floors and ground at the bottom, ceilings and sky at "
       "the top, walls, trees and doors standing upright, and whether people are standing, sitting or lying down. "
       "People lying down or leaning over do not make a photo sideways. Answer first, second, both or neither.")
ANSWER = {"type": "object", "properties": {"upright": {"type": "string", "enum": ["first", "second", "both", "neither"]}},
          "required": ["upright"]}


def face_turn(landmarks: bytes, width: int, height: int) -> int | None:
    """The turn clockwise (0, 90, 180 or 270) that puts this face's head up, or None for a head tilted between."""
    k = np.frombuffer(landmarks, np.float32).reshape(5, 2) * (width, height)
    up_x, up_y = (k[0] + k[1]) / 2 - (k[3] + k[4]) / 2  # from the middle of the mouth to between the eyes
    head = math.degrees(math.atan2(up_x, -up_y)) % 360  # 0: up, 90: to the right, 180: down, 270: to the left
    quarter = round(head / 90) % 4 * 90
    if min(abs(head - quarter), 360 - abs(head - quarter)) > TILT:
        return None
    return (360 - quarter) % 360


def candidates(con: sqlite3.Connection, faces: int) -> dict[int, tuple[int, int]]:
    """image id -> (the turn clockwise that puts it upright, how many faces say so), for photos whose clear faces (at
    least `faces` of them) all agree on one turn, none of them upright; those marked fine left out."""
    turns = defaultdict(Counter)
    for image_id, landmarks, width, height in con.execute(f"""
            SELECT f.image_id, f.landmarks, i.width, i.height FROM face f JOIN image i ON i.id = f.image_id
            WHERE f.frame IS NULL AND f.landmarks IS NOT NULL AND i.media = 'photo'
              AND (f.assigned_by IS NULL OR f.assigned_by != 'not_a_face') AND {usable_faces_sql()} AND {VISIBLE}"""):
        if (turn := face_turn(landmarks, width, height)) is not None:
            turns[image_id][turn] += 1
    fine = set(settings.get(con, OK_KEY, []))
    return {image_id: (turn, n) for image_id, found in turns.items() if image_id not in fine and len(found) == 1
            for turn, n in found.items() if turn and n >= faces}


def suggestions(con: sqlite3.Connection, level: str) -> list[dict]:
    """The photos to list for the setting turn_suggest: id, turn, faces, and doubt (None when sure, else why not):
    sure when two or more faces agree and the AI doesn't say otherwise, or one face does and the AI agrees."""
    if level not in ("sure", "more"):
        return []
    found = candidates(con, 1)
    said = settings.get(con, AI_KEY, {})
    out = []
    for image_id, (turn, faces) in found.items():
        verdict = said.get(str(image_id))
        doubt = ("the AI thinks it is the right way up" if verdict == "fine"
                 else "only one face says so" if faces < 2 and verdict != "turned" else None)
        if doubt is None or level == "more":
            out.append({"id": image_id, "turn": turn, "faces": faces, "doubt": doubt})
    return sorted(out, key=lambda s: (s["doubt"] is not None, s["id"]))


def check_with_ai(con: sqlite3.Connection, cfg: Config, ollama, level: str, stop=None) -> int:
    """Show the AI each photo the faces say is turned, as it is and turned, and keep which it says is the right way
    up. Each photo is asked about once. Returns how many were asked."""
    said = settings.get(con, AI_KEY, {})
    asked = 0
    found = candidates(con, 1)  # one face alone needs the AI to agree before it is listed as sure
    for image_id, (turn, _) in sorted(found.items(), key=lambda kv: (-kv[1][1], kv[0])):  # most faces first
        if str(image_id) in said or level not in ("sure", "more"):
            continue
        if stop is not None and stop():
            break
        row = con.execute("SELECT sha256 FROM image WHERE id = ?", (image_id,)).fetchone()
        try:
            with Image.open(cfg.preview_path(row[0])) as im:
                shown = im.convert("RGB")
        except OSError:
            continue
        shown.thumbnail((512, 512))
        pictures = [shown, shown.rotate(-turn, expand=True)]  # Pillow turns counter-clockwise
        turned_first = random.Random(image_id).random() < 0.5  # either order, so a habit of picking one says nothing
        if turned_first:
            pictures.reverse()
        answer, _ = ollama.chat_json("You judge which way up photos are.", ASK, ANSWER, [_jpeg(p) for p in pictures])
        if answer.get("upright") in ("first", "second"):
            said[str(image_id)] = "turned" if (answer["upright"] == "first") == turned_first else "fine"
            settings.put(con, AI_KEY, said)
            asked += 1
    return asked


def _jpeg(im: Image.Image) -> bytes:
    out = io.BytesIO()
    im.save(out, "JPEG", quality=85)
    return out.getvalue()


def turn(con: sqlite3.Connection, cfg: Config, image_id: int, degrees: int) -> None:
    """Turn a photo clockwise by 90, 180 or 270 degrees, in its file and in the catalog. Raises writeback.Problem."""
    if degrees % 360 not in (90, 180, 270):
        raise writeback.Problem("A photo is turned by a quarter, a half or three quarters.")
    row = con.execute("SELECT path FROM file WHERE image_id = ? AND NOT missing ORDER BY id LIMIT 1",
                      (image_id,)).fetchone()
    if row is None:
        raise writeback.Problem("Its file is no longer in the photo folders.")
    try:  # its orientation now: the tag in the file, as the previews read it
        with Image.open(cfg.source_path(row[0])) as im:
            now = im.getexif().get(0x0112, 1)
    except OSError:
        now = 1
    mirrored, shown = _TAGS.get(now, (False, 0))
    tag = next(t for t, v in _TAGS.items() if v == (mirrored, (shown + degrees) % 360))
    writeback.write(con, cfg, image_id, [f"-Orientation#={tag}"], turn=degrees % 360)


def mark_fine(con: sqlite3.Connection, image_ids: list[int]) -> None:
    settings.put(con, OK_KEY, sorted({*settings.get(con, OK_KEY, []), *image_ids}))
