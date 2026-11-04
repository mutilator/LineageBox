"""Stage: a sharpness score per image, for the "Blank and blurry" cleanup list.

Variance of the Laplacian in each tile of a 4x4 grid over the preview scaled to 1024 px, keeping the second-sharpest
tile: a sharp subject anywhere keeps a photo off the list even when its background is soft. Measured on 3,000 photos of
the library LineageBox was developed on: below ~10 (the lowest 1%) it is almost all black frames, blank sky and lens-cap
shots; 10-30 mixes blurry shots with soft keepers (babies, dim rooms); above that, ordinary photos. So it ranks a review
list and never decides on its own; the list's cut-off is the blurry_below setting. (The LLM's "blurry" flag is no use
for this: it was set on 74% of photos.)
"""
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image
from tqdm import tqdm

from lineagebox.catalog import VISIBLE
from lineagebox.config import Config

BATCH = 200


def sharpness(path) -> float:
    return sharpness_of(Image.open(path))


def sharpness_of(im: Image.Image) -> float:
    im = im.convert("L")
    im.thumbnail((1024, 1024))
    a = np.asarray(im, dtype=np.float32)
    lap = -4 * a[1:-1, 1:-1] + a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:]
    h, w = lap.shape[0] // 4, lap.shape[1] // 4
    tiles = sorted(float(lap[y * h:(y + 1) * h, x * w:(x + 1) * w].var()) for y in range(4) for x in range(4))
    return tiles[-2]


def measure(con: sqlite3.Connection, cfg: Config, show_progress: bool = True) -> int:
    """Score every image that has no score yet. Returns how many were scored."""
    rows = con.execute(f"""SELECT i.id, i.sha256 FROM image i
                           WHERE i.sharpness IS NULL AND i.media = 'photo' AND {VISIBLE}""").fetchall()

    def score(row) -> float:
        try:
            return sharpness(cfg.preview_path(row["sha256"]))
        except OSError:
            return -1.0

    with ThreadPoolExecutor(cfg.ingest_threads) as pool, \
            tqdm(total=len(rows), unit="img", disable=not show_progress) as bar:
        for start in range(0, len(rows), BATCH):  # computed first, then saved in one quick write per batch
            batch = rows[start:start + BATCH]
            scores = list(pool.map(score, batch))
            con.executemany("UPDATE image SET sharpness = ? WHERE id = ?",
                            ((s, r["id"]) for r, s in zip(batch, scores)))
            con.commit()
            bar.update(len(batch))
    return len(rows)
