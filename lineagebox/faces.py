"""Stage: find faces on the local previews and compute ArcFace embeddings (InsightFace buffalo_l).

Videos are looked at frame by frame (the frames ingest saved). The same person is on many of them, so each video keeps
one face per person: the clearest. Otherwise a long clip of one child would make a face group of its own and weigh on
recognition like dozens of photos.
"""
import hashlib
import importlib.util
import itertools
import json
import shutil
import sqlite3
import threading
import time
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
from PIL import Image
from tqdm import tqdm

from lineagebox.catalog import VISIBLE
from lineagebox.config import Config
from lineagebox.ingest import STAGE_INGEST

STAGE_FACES = 2
MODELS = "buffalo_l"
MODELS_URL = f"https://github.com/deepinsight/insightface/releases/download/model-zoo/{MODELS}.zip"  # as InsightFace's own
# The zip's SHA-256, checked before it is unpacked: a release asset can be replaced (this one was on 2026-09-03), and
# onnxruntime parses whatever it is given. This is the one GitHub lists for it, and what it hashed to when downloaded.
MODELS_SHA256 = "80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f"
MISMATCH = ("The face recognition models downloaded from GitHub are not the ones this version of LineageBox expects "
            "(their SHA-256 differs), so they were not used. An update of LineageBox may fix it; until then faces "
            "wait, and the download is tried again once a day.")
MODEL_FILES = ("det_10g.onnx", "w600k_r50.onnx", "genderage.onnx")  # the ones load_model uses
SAVE_EVERY, SAVE_SECONDS = 50, 5  # photos' faces are saved together, at most this many or this often
NEEDS = ("onnxruntime", "insightface")  # the faces extra: pip install "lineagebox[faces]" (the Docker image has them)
MISSING = ('Face recognition needs InsightFace and onnxruntime, which are not installed here: pip install '
           '"lineagebox[faces]" (or onnxruntime-gpu on a PC with an NVIDIA card), or switch faces off on the '
           'Settings page.')
PREFETCH = 8
# What counts as a usable face for clustering and recognition (measured on the library LineageBox was developed on:
# blurry faces, backs of heads, toys and pets cluster together below an embedding norm of about 15).
MIN_DET_SCORE = 0.6
MIN_FACE_PX = 40
MIN_QUALITY = 15.0
# Two faces on different frames of one video with embeddings this similar are the same person. Measured on that library,
# one adult across decades scores 0.6-0.75 and two young brothers about 0.5, so 0.6 keeps brothers apart.
SAME_PERSON_IN_VIDEO = 0.6
# Looking again at one photo, when asked on its page: the first look scales each photo to 640 pixels (and 128, for big
# faces) and keeps faces the detector is at least 50% sure of, which misses small faces in group photos. Looking again
# scales it to 1280 and keeps faces from 35%. On a small ARM board that takes 10-15 s, and 1-2 s more for each
# new face read: a crowd photo with 127 faces gave 23 more, of 20-30 pixels on the preview.
LOOK_AGAIN_SIZES = [(1280, 1280)]
LOOK_AGAIN_THRESHOLD = 0.35
SAME_FACE = 0.4  # two boxes overlapping this much (intersection over union) are one face found twice
_looking = threading.Lock()  # one closer look at a time: each loads the models (about half a gigabyte)


@dataclass
class FaceStats:
    pending: int = 0
    images: int = 0
    faces: int = 0
    interrupted: bool = False
    seconds: float = 0.0
    provider: str = ""
    unreadable: int = 0          # photos whose preview or frames couldn't be opened: handed back to ingest


class Unavailable(RuntimeError):
    """The libraries face recognition runs on aren't installed (MISSING says what to do)."""


def ensure_models(con: sqlite3.Connection, models_dir: Path) -> None:
    """Download the models the first time they are needed, with the progress on the Status page. InsightFace would
    download them itself, but silently, and it takes a half-unpacked folder for a finished one. Raises Unavailable,
    before downloading anything, when the libraries that would use them aren't there."""
    if not all(importlib.util.find_spec(m) for m in NEEDS):
        raise Unavailable(MISSING)
    target = models_dir / "models" / MODELS
    if all((target / f).is_file() for f in MODEL_FILES):
        return
    from lineagebox import downloads

    if downloads.failed_lately(con, "faces", MISMATCH):  # 288 MB each pass for the same wrong file: once a day
        raise Unavailable(MISMATCH)
    progress = downloads.Progress(con, "faces", f"Face recognition models (InsightFace {MODELS})")
    zipped, unpacked = target.with_name(f"{MODELS}.zip.part"), target.with_name(f"{MODELS}.new")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with httpx.stream("GET", MODELS_URL, follow_redirects=True, timeout=httpx.Timeout(120, connect=20)) as r:
            r.raise_for_status()
            total, done, digest = int(r.headers.get("content-length") or 0) or None, 0, hashlib.sha256()
            with open(zipped, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    progress.update(done, total)
        if digest.hexdigest() != MODELS_SHA256:
            progress.fail(MISMATCH)
            raise Unavailable(MISMATCH)
        shutil.rmtree(unpacked, ignore_errors=True)
        with zipfile.ZipFile(zipped) as z:
            z.extractall(unpacked)
        if not all((unpacked / f).is_file() for f in MODEL_FILES):
            raise OSError(f"the download has no {', '.join(MODEL_FILES)}")
        shutil.rmtree(target, ignore_errors=True)
        unpacked.rename(target)
    except (httpx.HTTPError, OSError, zipfile.BadZipFile) as e:
        progress.fail(f"{type(e).__name__}: {e}")
        raise
    finally:
        zipped.unlink(missing_ok=True)
        shutil.rmtree(unpacked, ignore_errors=True)
    progress.finish()


def load_model(models_dir: Path, gpu: bool = True, det_size=None, det_thresh: float = 0.5):
    import onnxruntime as ort

    providers = ["CPUExecutionProvider"]
    gpu = gpu and "CUDAExecutionProvider" in ort.get_available_providers()  # only onnxruntime-gpu builds have it
    if gpu:
        ort.preload_dlls()  # CUDA/cuDNN DLLs from the nvidia-* wheels
        providers.insert(0, "CUDAExecutionProvider")
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(name="buffalo_l", root=str(models_dir), providers=providers,
                       allowed_modules=["detection", "recognition", "genderage"])
    app.prepare(ctx_id=0 if gpu else -1, det_thresh=det_thresh, det_size=det_size)
    return app


def detect(con: sqlite3.Connection, cfg: Config, limit: int | None = None, gpu: bool = True,
           show_progress: bool = True) -> FaceStats:
    rows = con.execute(f"""SELECT i.id, i.sha256, i.media FROM image i WHERE NOT (i.stage & {STAGE_FACES}) AND {VISIBLE}
                           ORDER BY i.id""").fetchall()[:limit]
    stats = FaceStats(pending=len(rows))
    if not rows:
        return stats
    ensure_models(con, cfg.models_dir)
    app = load_model(cfg.models_dir, gpu)
    stats.provider = app.det_model.session.get_providers()[0]
    t0 = time.monotonic()
    frames = {}
    for r in rows:
        if r["media"] == "video":
            frames[r["id"]] = [f[0] for f in con.execute("SELECT idx FROM video_frame WHERE image_id = ? ORDER BY idx",
                                                         (r["id"],))]

    def jobs():
        """(image id, frame or None, picture, last picture of its image): one per photo, one per video frame."""
        for r in rows:
            if r["media"] != "video":
                yield r["id"], None, cfg.preview_path(r["sha256"]), True
                continue
            idxs = frames[r["id"]] or [None]  # no frames recorded: the preview is all there is
            for n, idx in enumerate(idxs):
                yield r["id"], idx, cfg.picture_path(r["sha256"], idx), n == len(idxs) - 1

    def load(job):
        image_id, frame, path, last = job
        try:
            im = Image.open(path).convert("RGB")
        except (OSError, ValueError):  # gone, or damaged on the data disk: one photo, not the whole stage
            return image_id, frame, last, None
        return image_id, frame, last, np.asarray(im)[:, :, ::-1].copy()  # InsightFace wants BGR

    found = []  # (image id, its faces) waiting to be saved
    on_image = []  # faces so far on the frames of the image being looked at
    unreadable = set()  # images with a picture that couldn't be opened
    saved_at = time.monotonic()
    todo = jobs()
    # decode upcoming previews in threads so the GPU never waits, but only a few ahead (each is ~8 MB decoded)
    with ThreadPoolExecutor(4) as pool, tqdm(total=len(rows), unit="img", disable=not show_progress) as bar:
        ahead = deque(pool.submit(load, j) for j in itertools.islice(todo, PREFETCH))
        try:
            while ahead:
                image_id, frame, last, bgr = ahead.popleft().result()
                if (job := next(todo, None)) is not None:
                    ahead.append(pool.submit(load, job))
                if bgr is None:
                    unreadable.add(image_id)
                elif image_id not in unreadable:
                    h, w = bgr.shape[:2]
                    on_image += [(image_id, *(float(v) for v in (f.bbox / [w, h, w, h])),
                                  (f.kps / np.array([w, h])).astype(np.float32).tobytes(),
                                  float(f.det_score), float(f.embedding_norm), int(f.age), f.sex,
                                  f.normed_embedding.astype(np.float32).tobytes(), frame) for f in app.get(bgr)]
                if not last:
                    continue
                if image_id in unreadable:
                    on_image = []
                    bar.update()
                    continue
                faces = one_per_person(on_image) if frame is not None else on_image
                found.append((image_id, faces))
                on_image = []
                stats.images += 1
                stats.faces += len(faces)
                if len(found) >= SAVE_EVERY or time.monotonic() - saved_at >= SAVE_SECONDS:
                    if _save(con, found):
                        found = []
                    saved_at = time.monotonic()
                bar.update()
        except KeyboardInterrupt:
            stats.interrupted = True
            for fut in ahead:
                fut.cancel()
        finally:
            if found and not _save(con, found):
                stats.images -= len(found)  # not saved: found again on the next run
            if unreadable:  # ingest makes their previews and frames again from the originals, then faces come
                con.execute(f"UPDATE image SET stage = stage & ~{STAGE_INGEST} WHERE id IN "
                            f"({','.join('?' * len(unreadable))})", sorted(unreadable))
                con.commit()
                stats.unreadable = len(unreadable)
    stats.seconds = time.monotonic() - t0
    return stats


def look_again(con: sqlite3.Connection, cfg: Config, image_id: int, app=None, bar: float | None = None,
               bring_back: bool = True, turned: bool = False) -> tuple[list[int], list[int]]:
    """Look for faces in one photo (or a video's frames) again, more closely than the first time. Faces not found
    before are added; one found before and marked as not a face is brought back (bring_back: when someone asked about
    this photo; never when going through every photo, which leaves what people said alone). Returns (new faces, faces
    brought back). app: the closer look's models, loaded already (look_again_waiting); else they are loaded for this
    one. bar: keep only new faces the detector is at least this sure of (None: from LOOK_AGAIN_THRESHOLD). turned: a
    photo with no face at all, even now, is looked at turned each way (_found_turned)."""
    img = con.execute("SELECT sha256, media FROM image WHERE id = ?", (image_id,)).fetchone()
    if img is None:
        raise FileNotFoundError("That photo isn't in the catalog any more.")
    pictures = [(None, cfg.preview_path(img["sha256"]))]
    if img["media"] == "video":
        pictures = [(idx, cfg.frame_path(img["sha256"], idx)) for (idx,) in con.execute(
            "SELECT idx FROM video_frame WHERE image_id = ? ORDER BY idx", (image_id,))] or pictures
    pictures = [(frame, path) for frame, path in pictures if path.is_file()]
    if not pictures:
        raise FileNotFoundError("There is no preview of this photo to look at yet.")
    known = con.execute("SELECT id, x1, y1, x2, y2, frame, assigned_by, embedding FROM face WHERE image_id = ?",
                        (image_id,)).fetchall()
    found, back = [], set()
    with _looking:
        if app is None:
            ensure_models(con, cfg.models_dir)
            app = closer_look_model(cfg)
        from insightface.app.common import Face
        for frame, path in pictures:
            rgb = Image.open(path).convert("RGB")
            bgr = np.asarray(rgb)[:, :, ::-1].copy()
            h, w = bgr.shape[:2]
            # found, then read (embedding, age) only if new: on that ARM board a crowd of 150 faces took 47 s read whole
            boxes, points = app.det_model.detect(bgr, max_num=0, metric="default")
            for n, (*corners, score) in enumerate(boxes):
                box = tuple(float(v) for v in np.array(corners) / [w, h, w, h])
                same = [k for k in known if k["frame"] == frame and _overlap(box, k) >= SAME_FACE]
                if same:
                    if bring_back:
                        back |= {k["id"] for k in same if k["assigned_by"] == "not_a_face"}
                    continue
                if bar is not None and score < bar:
                    continue
                f = Face(bbox=np.array(corners), kps=points[n], det_score=score)
                for task, model in app.models.items():
                    if task != "detection":
                        model.get(bgr, f)
                found.append((image_id, *box, (f.kps / np.array([w, h])).astype(np.float32).tobytes(),
                              float(f.det_score), float(f.embedding_norm), None if f.age is None else int(f.age),
                              f.sex, f.normed_embedding.astype(np.float32).tobytes(), frame))
        found_turned = False
        if turned and img["media"] == "photo" and not found and not back                 and all(k["assigned_by"] == "not_a_face" for k in known):
            found = _found_turned(app, rgb, image_id)
            found_turned = bool(found)
    if img["media"] == "video":  # one face per person, and none of someone the video already has a face of
        seen = [np.frombuffer(k["embedding"], dtype=np.float32) for k in known]
        found = [f for f in one_per_person(found) if not seen or max(
            float(np.frombuffer(f[10], dtype=np.float32) @ e) for e in seen) < SAME_PERSON_IN_VIDEO]
    new = [con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, landmarks, det_score, quality, est_age, est_sex,
                                           embedding, frame) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       f).lastrowid for f in found]
    con.executemany("UPDATE face SET assigned_by = NULL WHERE id = ?", ((i,) for i in back))
    con.execute("UPDATE image SET faces_again = ? WHERE id = ?",  # off the list of photos with faces missed (missed)
                (datetime.now().isoformat(timespec="seconds"), image_id))
    con.commit()
    if found_turned:  # Turned photos lists it as sure, whatever the AI says (orientation.suggestions)
        from lineagebox import settings

        settings.put(con, FOUND_TURNED_KEY, sorted({*settings.get(con, FOUND_TURNED_KEY, []), image_id}))
    return new, sorted(back)


TURNS = (90, 180, 270)  # the turns clockwise a photo with no face found is looked at in (_found_turned)
# settings: photos whose faces were found only once turned. None as they are, even looking closely, and clear ones
# turned, says the photo is on its side more surely than the AI, which in a folder of scanned prints called most of
# those fine: Cleanup → Turned photos lists them as sure (orientation.suggestions)
FOUND_TURNED_KEY = "turn_found"


def _found_turned(app, rgb: Image.Image, image_id: int) -> list[tuple]:
    """Faces found once a photo is turned: one taken sideways or upside down, which no camera tag turned upright (a
    scan, an old camera), whose faces the detector misses as it is. Of the three turns, the one that finds the most
    faces the detector is at least MIN_DET_SCORE sure of (the clearer on a tie); their boxes and landmarks put back on
    the photo as it is, so they show where they are, and their landmarks then say that the photo is turned (Cleanup →
    Turned photos). Each face is read (embedding, age) on the turned photo, where it is upright. Rows for face, as
    look_again makes them; none when no turn finds a face."""
    from insightface.app.common import Face

    size = rgb.size
    best = None  # (number of faces, their scores together, turn, the turned picture, [(box, landmarks)])
    for turn in TURNS:
        bgr = np.asarray(rgb.rotate(-turn, expand=True))[:, :, ::-1].copy()  # Pillow turns counter-clockwise
        boxes, points = app.det_model.detect(bgr, max_num=0, metric="default")
        keep = [(b, p) for b, p in zip(boxes, points) if b[4] >= MIN_DET_SCORE]
        if keep and (best is None or (len(keep), sum(float(b[4]) for b, _ in keep)) > best[:2]):
            best = (len(keep), sum(float(b[4]) for b, _ in keep), turn, bgr, keep)
    if best is None:
        return []
    _, _, turn, bgr, keep = best
    out = []
    for b, p in keep:
        f = Face(bbox=np.array(b[:4]), kps=p, det_score=b[4])
        for task, model in app.models.items():
            if task != "detection":
                model.get(bgr, f)
        corners = unturn(np.array([[b[0], b[1]], [b[2], b[3]]], dtype=np.float64), turn, size)
        (x1, y1), (x2, y2) = corners.min(axis=0) / size, corners.max(axis=0) / size
        marks = (unturn(np.asarray(p, dtype=np.float64), turn, size) / size).astype(np.float32).tobytes()
        out.append((image_id, float(x1), float(y1), float(x2), float(y2), marks, float(b[4]), float(f.embedding_norm),
                    None if f.age is None else int(f.age), f.sex, f.normed_embedding.astype(np.float32).tobytes(),
                    None))
    return out


def unturn(points: np.ndarray, turn: int, size: tuple[int, int]) -> np.ndarray:
    """Points (x, y), in pixels on a picture turned `turn` degrees clockwise, as pixels on the picture as it was;
    size: that one's width and height."""
    width, height = size
    u, v = points[:, 0], points[:, 1]
    if turn == 90:
        return np.stack([v, height - u], axis=1)
    if turn == 180:
        return np.stack([width - u, height - v], axis=1)
    if turn == 270:
        return np.stack([width - v, u], axis=1)
    return np.stack([u, v], axis=1)


def closer_look_model(cfg: Config):
    """The models as look_again uses them: each photo scaled to twice the size, and faces kept from a lower bar."""
    return load_model(cfg.models_dir, det_size=LOOK_AGAIN_SIZES, det_thresh=LOOK_AGAIN_THRESHOLD)


# Photos with no face found where the AI is sure it sees people (Cleanup → No faces found). Looking again more
# closely finds the faces of some (turned a little, small, in shadow); the rest are people seen from behind or too far
# off, which an admin sets aside (NONE_OK_KEY) so that they aren't listed again. Faces missed (missed) is the group
# photos with some faces found, so a photo is on one list or the other, and one looked at again (faces_again) leaves
# both. On the library LineageBox was developed on (2026-11-08), 5,578 of 38,743 photos were like that. In one stretch
# of 1,400 phone photos whose faces had never been looked for properly (in a move between two computers), half had
# clear faces that the first look finds today; elsewhere a sample of 60 had none to find.
NONE_OK_KEY = "no_faces_ok"  # settings: photos an admin said have no faces to find
LOOK_AGAIN_KEY = "faces_look_again"  # settings: photos ticked to be looked at again by the worker (look_again_waiting)


def _counted(found: str) -> str:
    """SQL: the AI's careful count of the people in photo i (headcount h, headcount.py) is one it is sure of, and it
    isn't as sure that the faces it sees are no more than those found (found: SQL for how many): a hand holding
    something, people seen from behind."""
    from lineagebox.headcount import SURE

    return f"h.people_sure >= {SURE} AND NOT (coalesce(h.faces_sure, 0) >= {SURE} AND h.faces <= {found})"


def no_faces(con: sqlite3.Connection, offset: int = 0, limit: int = 50) -> tuple[list[dict], int]:
    """Photos looked at for faces with none found, where the AI is sure it sees someone (_counted): not looked at
    again, set aside or waiting to be looked at again. The newest first, so an event's photos come together. (The page,
    each with the people counted, how sure the AI is of it and the description's caption; how many there are.)"""
    from lineagebox import settings

    left_out = set(settings.get(con, NONE_OK_KEY, [])) | set(settings.get(con, LOOK_AGAIN_KEY, []))
    ids = [r[0] for r in con.execute(f"""
        SELECT i.id FROM image i JOIN headcount h ON h.image_id = i.id
        WHERE i.media = 'photo' AND (i.stage & {STAGE_FACES}) AND i.faces_again IS NULL AND h.people >= 1
          AND {_counted("0")} AND {VISIBLE}
          AND NOT EXISTS (SELECT 1 FROM face f WHERE f.image_id = i.id
                          AND (f.assigned_by IS NULL OR f.assigned_by != 'not_a_face'))
        ORDER BY i.taken_at DESC, i.id""") if r[0] not in left_out]
    shown = ids[offset:offset + limit]
    rows = {r["id"]: dict(r) for r in con.execute("""
        SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.media, h.people,
               min(99, CAST(round(h.people_sure * 100) AS INTEGER)) AS sure,  -- a whole percentage
               (SELECT a.caption FROM analysis a WHERE a.image_id = i.id ORDER BY a.created_at DESC LIMIT 1) AS caption
        FROM image i JOIN headcount h ON h.image_id = i.id
        WHERE i.id IN (SELECT value FROM json_each(?))""", (json.dumps(shown),))}
    return [rows[i] for i in shown if i in rows], len(ids)


def look_again_waiting(con: sqlite3.Connection, cfg: Config, stop=None) -> tuple[int, list[int]]:
    """Look again, more closely, at the photos ticked for it (LOOK_AGAIN_KEY), the models loaded once, until stop()
    says so; each is taken off the waiting list as it is done. Returns (photos looked at, faces found or brought
    back)."""
    from lineagebox import settings

    if not settings.get(con, LOOK_AGAIN_KEY, []):
        return 0, []
    ensure_models(con, cfg.models_dir)
    app = closer_look_model(cfg)
    done, faces = 0, []
    while waiting := settings.get(con, LOOK_AGAIN_KEY, []):
        if stop is not None and stop():
            break
        image_id = waiting[0]
        try:
            new, back = look_again(con, cfg, image_id, app=app)
            faces += new + back
        except FileNotFoundError:  # no preview to look at (it is being made again), or the photo is gone
            pass
        except sqlite3.OperationalError as e:  # the web app holds the catalog: the rest wait for the next pass
            con.rollback()
            if "locked" not in str(e):
                raise
            break
        # read again: someone may have ticked more meanwhile
        settings.put(con, LOOK_AGAIN_KEY, [i for i in settings.get(con, LOOK_AGAIN_KEY, []) if i != image_id])
        done += 1
    return done, faces


# Looking again at every photo (the worker's stage, Settings → Faces): the closer look a photo's page gives, for every
# photo that only had the first look. Measured on the library LineageBox was developed on (2026-11-11): of 80 photos
# picked at random, 22 had faces the first look missed, 88 in all, nearly all small (at twice the size most scored
# 0.6 to 0.86; 14 under 0.5). It takes 1.45 s a photo on the router's processor (the first look 0.55 s): about 16
# hours for 38,000 photos, so the worker gives it some minutes a pass. Each photo is looked at once (faces_again),
# and a new one soon after its first look. A round (ROUND_KEY) looks at every photo again, every few months as
# Settings → Faces says (faces_again_every) or when an admin asks: the same look finds nothing new in a photo that
# hasn't changed, but does in one turned since, or after an update that looks better.
#
# A photo where the AI sees people but no face is found, even now, is looked at turned too (_found_turned): in a folder
# of scanned wedding prints with no turn in their files, 4 of 8 such photos had 2 or 3 clear faces once turned (0.83 to
# 0.88), which then put them on Cleanup → Turned photos; at random, 4 photos in 80 were like that, none with faces.
ROUND_KEY = "faces_round"  # settings: {"started": when this round began, "finished": when it ended, None while it runs}
ROUND_BAR = 0.5            # new faces kept: the first look's bar, not the 0.35 of a photo someone asked about
ROUND_AT_ONCE = 2000       # photos picked at a time; a pass stops sooner, when its minutes are up


def _due(state: dict) -> tuple[str, dict]:
    """The photos this round still has to look at: those whose first look is done and that haven't been looked at
    closely since it began (all of them, if none ever did)."""
    return (f"""i.media = 'photo' AND (i.stage & {STAGE_FACES}) AND {VISIBLE}
                AND (i.faces_again IS NULL OR i.faces_again < :started)""", {"started": state.get("started") or ""})


def round_state(con: sqlite3.Connection) -> dict:
    from lineagebox import settings

    return settings.get(con, ROUND_KEY, {}) or {}


def start_round(con: sqlite3.Connection, now: datetime | None = None) -> None:
    """Look again at every photo, from now: an admin asked, or it is time (faces_again_every)."""
    from lineagebox import settings

    settings.put(con, ROUND_KEY, {"started": (now or datetime.now()).isoformat(timespec="seconds"), "finished": None})


def count_due(con: sqlite3.Connection) -> int:
    """Photos still to look at closely (the Status page's list of what is waiting)."""
    where, args = _due(round_state(con))
    return con.execute(f"SELECT count(*) FROM image i WHERE {where}", args).fetchone()[0]


def round_progress(con: sqlite3.Connection) -> dict:
    """For Settings → Faces: {"photos": photos whose faces were looked for, "left": those still to look at closely,
    "started", "finished": the round's, "hours": about how long the rest takes on a small router}."""
    state = round_state(con)
    photos = con.execute(f"""SELECT count(*) FROM image i WHERE i.media = 'photo' AND (i.stage & {STAGE_FACES})
                             AND {VISIBLE}""").fetchone()[0]
    left = count_due(con)
    return {"photos": photos, "left": left, "started": state.get("started"), "finished": state.get("finished"),
            "hours": round(left * 1.6 / 3600, 1)}


def look_again_all(con: sqlite3.Connection, cfg: Config, every_months: int, stop=None,
                   now: datetime | None = None) -> tuple[int, list[int]]:
    """The worker's closer look at the photos this round still has to look at, the newest first, the models loaded
    once, until stop() says so. Starts the first round, and a new one when the last finished more than every_months
    ago (0: only when asked); marks a round finished when no photo is left. New faces are kept from ROUND_BAR, a face
    marked as not a face stays so, and a photo where the AI sees people but no face is found is looked at turned.
    Returns (photos looked at, new faces)."""
    from lineagebox import settings

    now = now or datetime.now()
    state = round_state(con)
    if not state.get("started"):
        start_round(con, now)
    elif every_months and state.get("finished") and             state["finished"] < (now - timedelta(days=round(every_months * 30.44))).isoformat(timespec="seconds"):
        start_round(con, now)
    state = round_state(con)
    where, args = _due(state)
    ids = [r[0] for r in con.execute(f"SELECT i.id FROM image i WHERE {where} ORDER BY i.id DESC LIMIT {ROUND_AT_ONCE}",
                                     args)]
    if not ids:
        if not state.get("finished"):
            settings.put(con, ROUND_KEY, {**state, "finished": now.isoformat(timespec="seconds")})
            con.commit()
        return 0, []
    seen = dict(con.execute("""SELECT image_id, max(people_count) FROM analysis
                               WHERE image_id IN (SELECT value FROM json_each(?)) GROUP BY image_id""",
                            (json.dumps(ids),)).fetchall())
    ensure_models(con, cfg.models_dir)
    app = closer_look_model(cfg)
    done, found = 0, []
    for image_id in ids:
        if stop is not None and stop():
            break
        try:
            new, _ = look_again(con, cfg, image_id, app=app, bar=ROUND_BAR, bring_back=False,
                                turned=(seen.get(image_id) or 0) >= 1)
            found += new
        except FileNotFoundError:  # no preview to look at just now (it is being made again): a later pass
            continue
        except sqlite3.OperationalError as e:  # the web app holds the catalog: the rest wait for the next pass
            con.rollback()
            if "locked" not in str(e):
                raise
            break
        done += 1
    return done, found


# Photos with faces missed (Cleanup → Confirm faces → Faces missed): the AI counts the people it sees, and where it
# counts MISSED_GROUP or more and MISSED_MORE more than the faces found, looking again (look_again, at twice the size
# and a lower bar) may find the small faces of a group photo. On the library LineageBox was developed on, 3,495 of
# 36,969 described photos were like that by their descriptions' counts, 882 of them with someone named in them: those
# come first, as photos of the family. But a description's count of a group was as often wildly wrong (100 people at a
# concert with 8 in sight), so only the AI's careful count goes, and only when it is sure of it (_counted,
# headcount.py): rarely, for a group of more than three.
MISSED_GROUP = 3
MISSED_MORE = 2
_FOUND = """(SELECT count(*) FROM face f WHERE f.image_id = i.id
             AND (f.assigned_by IS NULL OR f.assigned_by != 'not_a_face'))"""


def missed(con: sqlite3.Connection, offset: int = 0, limit: int = 25) -> tuple[list[sqlite3.Row], int]:
    """Photos where the AI is sure it sees more people than faces were found (see MISSED_GROUP), not yet looked at
    again, those with someone named in them first, then the most missed: (the page, how many there are). Only photos
    with some faces found: those with none are on the list of no faces found (no_faces)."""
    rows = con.execute(f"""
        SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.media, h.people,
               min(99, CAST(round(h.people_sure * 100) AS INTEGER)) AS sure,  -- a whole percentage
               {_FOUND} AS found,
               EXISTS (SELECT 1 FROM face f WHERE f.image_id = i.id AND f.person_id IS NOT NULL) AS named
        FROM image i JOIN headcount h ON h.image_id = i.id
        WHERE i.media = 'photo' AND i.faces_again IS NULL AND h.people >= {MISSED_GROUP} AND {VISIBLE}
          AND h.people - {_FOUND} >= {MISSED_MORE} AND {_counted(_FOUND)}
          AND EXISTS (SELECT 1 FROM face f WHERE f.image_id = i.id
                      AND (f.assigned_by IS NULL OR f.assigned_by != 'not_a_face'))
        ORDER BY named DESC, h.people - found DESC, i.taken_at DESC, i.id""").fetchall()
    return rows[offset:offset + limit], len(rows)


def _overlap(box: tuple, face) -> float:
    """How much a box (x1, y1, x2, y2) and a face's box overlap: their intersection over their union."""
    w = min(box[2], face["x2"]) - max(box[0], face["x1"])
    h = min(box[3], face["y2"]) - max(box[1], face["y1"])
    if w <= 0 or h <= 0:
        return 0.0
    both = w * h
    return both / ((box[2] - box[0]) * (box[3] - box[1]) + (face["x2"] - face["x1"]) * (face["y2"] - face["y1"]) - both)


def one_per_person(faces: list[tuple]) -> list[tuple]:
    """Of a video's faces (rows as _save writes them), the clearest of each person. Faces on the same frame are
    never the same person."""
    tracks: list[tuple[list[np.ndarray], set, tuple]] = []  # (embeddings, frames, clearest face)
    for f in sorted(faces, key=lambda f: -f[7]):  # clearest (largest embedding norm) first
        e, frame = np.frombuffer(f[10], dtype=np.float32), f[11]
        for embeddings, on_frames, _ in tracks:
            if frame not in on_frames and max(float(e @ x) for x in embeddings) >= SAME_PERSON_IN_VIDEO:
                embeddings.append(e)
                on_frames.add(frame)
                break
        else:
            tracks.append(([e], {frame}, f))
    return [best for _, _, best in tracks]


def _save(con: sqlite3.Connection, found: list) -> bool:
    """Write the faces of a few photos in one short transaction, so the web app never waits on face detection
    (writing as each photo was done held the database for a minute or more on an ARM board). False, with nothing
    written, if the database stayed locked: the caller keeps the batch for next time."""
    try:
        con.executemany("""INSERT INTO face(image_id, x1, y1, x2, y2, landmarks, det_score, quality, est_age, est_sex,
                                            embedding, frame) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        [face for _, faces in found for face in faces])
        con.executemany("UPDATE image SET stage = stage | ? WHERE id = ?", [(STAGE_FACES, i) for i, _ in found])
        con.commit()
        return True
    except sqlite3.OperationalError as e:
        con.rollback()
        if "locked" not in str(e):
            raise
        return False


def usable_faces_sql(alias: str = "f") -> str:
    """Faces good enough to cluster and recognise (box size measured on the preview, max side 2048)."""
    return f"""{alias}.det_score >= {MIN_DET_SCORE} AND {alias}.quality >= {MIN_QUALITY}
               AND min(({alias}.x2 - {alias}.x1) * min(i.width, i.width * 2048.0 / max(i.width, i.height)),
                       ({alias}.y2 - {alias}.y1) * min(i.height, i.height * 2048.0 / max(i.width, i.height)))
                   >= {MIN_FACE_PX}"""
