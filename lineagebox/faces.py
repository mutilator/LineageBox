"""Stage: find faces on the local previews and compute ArcFace embeddings (InsightFace buffalo_l).

Videos are looked at frame by frame (the frames ingest saved). The same person is on many of them, so each video keeps
one face per person: the clearest. Otherwise a long clip of one child would make a face group of its own and weigh on
recognition like dozens of photos.
"""
import hashlib
import importlib.util
import itertools
import shutil
import sqlite3
import threading
import time
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
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


def look_again(con: sqlite3.Connection, cfg: Config, image_id: int) -> tuple[list[int], list[int]]:
    """Look for faces in one photo (or a video's frames) again, more closely than the first time. Faces not found
    before are added; one found before and marked as not a face is brought back. Returns (new faces, faces brought
    back)."""
    img = con.execute("SELECT sha256, media FROM image WHERE id = ?", (image_id,)).fetchone()
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
        ensure_models(con, cfg.models_dir)
        app = load_model(cfg.models_dir, det_size=LOOK_AGAIN_SIZES, det_thresh=LOOK_AGAIN_THRESHOLD)
        from insightface.app.common import Face
        for frame, path in pictures:
            bgr = np.asarray(Image.open(path).convert("RGB"))[:, :, ::-1].copy()
            h, w = bgr.shape[:2]
            # found, then read (embedding, age) only if new: on that ARM board a crowd of 150 faces took 47 s read whole
            boxes, points = app.det_model.detect(bgr, max_num=0, metric="default")
            for n, (*corners, score) in enumerate(boxes):
                box = tuple(float(v) for v in np.array(corners) / [w, h, w, h])
                same = [k for k in known if k["frame"] == frame and _overlap(box, k) >= SAME_FACE]
                if same:
                    back |= {k["id"] for k in same if k["assigned_by"] == "not_a_face"}
                    continue
                f = Face(bbox=np.array(corners), kps=points[n], det_score=score)
                for task, model in app.models.items():
                    if task != "detection":
                        model.get(bgr, f)
                found.append((image_id, *box, (f.kps / np.array([w, h])).astype(np.float32).tobytes(),
                              float(f.det_score), float(f.embedding_norm), None if f.age is None else int(f.age),
                              f.sex, f.normed_embedding.astype(np.float32).tobytes(), frame))
    if img["media"] == "video":  # one face per person, and none of someone the video already has a face of
        seen = [np.frombuffer(k["embedding"], dtype=np.float32) for k in known]
        found = [f for f in one_per_person(found) if not seen or max(
            float(np.frombuffer(f[10], dtype=np.float32) @ e) for e in seen) < SAME_PERSON_IN_VIDEO]
    new = [con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, landmarks, det_score, quality, est_age, est_sex,
                                           embedding, frame) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       f).lastrowid for f in found]
    con.executemany("UPDATE face SET assigned_by = NULL WHERE id = ?", ((i,) for i in back))
    con.commit()
    return new, sorted(back)


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
