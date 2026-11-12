"""A careful count of the people in a photo, with how sure the AI is of it, for Cleanup's lists of faces missed.

A description's people_count is one field of a long answer, and a small model's guess at it can be anything: on the
library LineageBox was developed on (2026-11-11), 397 of 37,599 descriptions counted more than 200 people, the most
5,000,000,000,000,000, and Cleanup → No faces found and Confirm faces → Faces missed listed thousands of photos on such
counts. So the photos whose faces don't add up to their description's count are asked about again, on their own: how
many people, and how many of them show their face. Ollama gives the chance of each token (a digit, here) of the answer
(sure_of); the lists keep to the counts the model is SURE of.

Measured on 90 photos of those lists with ministral-3, about 0.8 s each on a graphics card: every count it was 80% sure
of or more was right (5 of 5, all of one to three people). Of three at 75% to 80%, two were wrong (three boys for two),
and it was never as sure of a group of more than three: those counts were 5% to 60% sure, and most were wrong.
"""
import io
import math
import re
import sqlite3
from datetime import datetime

from lineagebox import retry
from lineagebox.catalog import VISIBLE

SURE = 0.8          # a count the lists go by: the model at least this sure of it
VERSION = 1         # of the question: a new one asks every photo again
PLAUSIBLE = 50      # a description's own count above this isn't shown as a number (shown())
TOP = 20            # alternatives Ollama gives for each token (its most)
PICTURE = 1024      # px, the longest side the AI sees, as for a description
GIVE_UP_AFTER = 5   # photos in a row the AI couldn't count: the rest wait for the next pass
STAGE = "headcount"  # retry.py
retry.STAGES[STAGE] = "photos to count the people in"

SYSTEM = """You count the people in a family photo. Look closely and count them one by one.
- people: how many people can be seen, wholly or in part (a head, an arm, someone from behind). 0 if none.
- faces: how many of those people show their face, enough to see their eyes and nose. 0 if none.
People in a picture inside the photo (a framed photo, a poster, a screen) count too."""
SCHEMA = {"type": "object", "required": ["people", "faces"],
          "properties": {"people": {"type": "integer"}, "faces": {"type": "integer"}}}


class Problem(Exception):
    """A photo the AI couldn't count just now: it is tried again later (retry.py)."""


# ---- how sure ---------------------------------------------------------------------------------------------------------

def _first_digit(token: str) -> bool:
    return bool(re.fullmatch(r"\s*\d+", token))


def _digit_or_end(token: str) -> bool:
    return token.isdigit() or _end(token)


def _end(token: str) -> bool:
    """What may follow a number in the answer's JSON: a space, a comma or the closing brace."""
    return bool(re.fullmatch(r"\s+", token) or re.match(r"\s*[,}]", token))


def _share(t: dict, alike, wanted=None) -> float:
    """The chance of the token the model wrote (or of any wanted() accepts), among those it could have written in its
    place (the ones alike() accepts): their probabilities, as Ollama gives them, are of what the model would write
    unbound by the JSON, so only their shares mean anything. Ollama lists the TOP most likely; each digit it leaves out
    is counted at the chance of the least likely listed, which is at most what it was, so that a digit barely chosen
    isn't taken for a sure one."""
    listed = {x["token"]: math.exp(x["logprob"]) for x in t.get("top_logprobs") or []}
    chosen = math.exp(t["logprob"])
    others = {token: p for token, p in listed.items() if alike(token)} | {t["token"]: chosen}
    digits = {token.strip()[:1] for token in others if token.strip()[:1].isdigit()}
    unlisted = (10 - len(digits)) * min(listed.values(), default=0.0)
    hit = sum(p for token, p in others.items() if wanted(token)) if wanted else chosen
    return hit / (sum(others.values()) + unlisted)


def sure_of(tokens: list[dict], key: str, value: int) -> float | None:
    """How sure the model was that key's number is value, from the answer's tokens (Usage.tokens): the chance of each
    of its digits among the digits it could have written there, and of the number ending where it did. None without
    the tokens (an Ollama too old to give them), or when they don't spell that number."""
    text, starts = "", []
    for t in tokens:
        starts.append(len(text))
        text += t.get("token", "")
    found = re.search(rf'"{re.escape(key)}"\s*:\s*(\d+)', text)
    if not found or int(found.group(1)) != value:
        return None
    start, end = found.span(1)
    sure = 1.0
    for t, at in zip(tokens, starts):
        if at + len(t["token"]) <= start:
            continue
        if at >= end:  # the token after it: the number ends there (with whatever), rather than going on with a digit
            sure *= _share(t, _digit_or_end, wanted=_end)
            break
        sure *= _share(t, _first_digit if at <= start else _digit_or_end)
        if at + len(t["token"]) > end:  # this token ends the number too
            break
    return round(sure, 3)


def shown(con: sqlite3.Connection, analysis) -> dict | None:
    """How many people a photo shows, for its page: {"count", "sure"}, the AI's careful count with how sure it was
    (a whole percentage, at most 99; None from an Ollama that doesn't say), or else its description's count, unless
    that is over PLAUSIBLE. None when neither sees anyone. analysis: the photo's description (catalog.analysis_for),
    whose photo the count is of too, as its group of similar shots shares one."""
    if analysis is None:
        return None
    h = con.execute("SELECT people, people_sure FROM headcount WHERE image_id = ?", (analysis["image_id"],)).fetchone()
    if h is not None:
        sure = h["people_sure"]
        return {"count": h["people"], "sure": None if sure is None else min(99, round(sure * 100))} \
            if h["people"] else None
    n = analysis["people_count"]
    return {"count": n, "sure": None} if n and 0 < n <= PLAUSIBLE else None


# ---- the photos to count, and counting them --------------------------------------------------------------------------

def waiting(con: sqlite3.Connection, model: str | None = None) -> list[int]:
    """The photos to count, the newest first as the lists show them: those whose description counts more people than
    faces were found, looked at for faces but not yet closely (which takes a photo off the lists), not counted with this
    model and question (None: by any), nor set aside, waiting to be looked at again, or to be tried again (retry.py)."""
    from lineagebox import faces, settings

    counted, args = ("h.model = ? AND h.version = ?", (model, VERSION)) if model else ("1", ())
    left_out = set(settings.get(con, faces.NONE_OK_KEY, [])) | set(settings.get(con, faces.LOOK_AGAIN_KEY, []))
    return [r[0] for r in con.execute(f"""
        WITH counted AS (SELECT image_id, max(people_count) AS people FROM analysis GROUP BY image_id)
        SELECT i.id FROM image i JOIN counted n ON n.image_id = i.id
        WHERE i.media = 'photo' AND (i.stage & {faces.STAGE_FACES}) AND i.faces_again IS NULL AND {VISIBLE}
          AND n.people > (SELECT count(*) FROM face f WHERE f.image_id = i.id
                          AND (f.assigned_by IS NULL OR f.assigned_by != 'not_a_face'))
          AND NOT EXISTS (SELECT 1 FROM headcount h WHERE h.image_id = i.id AND {counted})
          AND {retry.waiting(STAGE)}
        ORDER BY i.taken_at DESC, i.id""", args) if r[0] not in left_out]


def _picture(cfg, sha256: str) -> bytes:
    from PIL import Image

    with Image.open(cfg.preview_path(sha256)) as im:
        im = im.convert("RGB")
        im.thumbnail((PICTURE, PICTURE), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=88)
        return out.getvalue()


def count(con: sqlite3.Connection, cfg, image_id: int, client) -> dict:
    """Ask the AI how many people a photo shows, and keep its answer with how sure it was. Returns the row's values.
    Raises Problem when it can't be counted just now (no preview yet, an answer that isn't JSON, an error from
    Ollama about it), httpx.HTTPError when Ollama doesn't answer."""
    from lineagebox.llm import OllamaError

    row = con.execute("SELECT sha256 FROM image WHERE id = ?", (image_id,)).fetchone()
    if row is None:
        raise Problem("No such photo.")
    try:
        picture = _picture(cfg, row["sha256"])
    except OSError:
        raise Problem("Its preview isn't made yet: the worker makes it on its next pass.") from None
    try:
        answer, usage = client.chat_json(SYSTEM, "Count the people in this photo.", SCHEMA, images=[picture],
                                         temperature=0.0, max_tokens=60, logprobs=TOP)
    except ValueError:  # not JSON, or cut off
        raise Problem("The AI's answer didn't make sense.") from None
    except OllamaError as e:
        raise Problem(f"The AI couldn't count them: {e}") from None
    found = {"model": client.model, "version": VERSION, "counted_at": datetime.now().isoformat(timespec="seconds")}
    for key in ("people", "faces"):
        value = answer.get(key)
        value = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        found[key] = value
        found[f"{key}_sure"] = sure_of(usage.tokens, key, value) if value is not None else None
    con.execute("""INSERT OR REPLACE INTO headcount(image_id, model, version, counted_at, people, people_sure, faces,
                                                    faces_sure)
                   VALUES (:image_id, :model, :version, :counted_at, :people, :people_sure, :faces, :faces_sure)""",
                {"image_id": image_id, **found})
    retry.done(con, STAGE, [image_id])
    con.commit()
    return found


def count_waiting(con: sqlite3.Connection, cfg, client, stop=None) -> int:
    """The worker's pass: count the people in the photos waiting (waiting()), the newest first, until stop() says
    so. A photo that can't be counted is tried again later (retry.py); Ollama not answering, or failing on several
    photos in a row, ends the pass. Returns how many were counted."""
    import httpx

    done = failed = 0
    for image_id in waiting(con, client.model):
        if stop is not None and stop():
            break
        try:
            count(con, cfg, image_id, client)
        except Problem as e:
            retry.later(con, STAGE, image_id, str(e))
            failed += 1
            if failed >= GIVE_UP_AFTER:
                break
            continue
        except httpx.HTTPError:
            break
        done += 1
        failed = 0
    return done
