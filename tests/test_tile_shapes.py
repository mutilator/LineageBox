"""Photo tiles: a square's crop aimed at the faces in a photo, and each photo's shape for Whole photos (tiles.py,
_macros.html, More → Photo tiles)."""
import re

import numpy as np
from fastapi.testclient import TestClient

from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp, tiles
from tests.conftest import LAN, rel, write_jpeg


def test_a_square_is_aimed_at_the_faces():
    # nobody, a square photo, or a shape not known: the middle, as before
    assert tiles.focus(400, 300, []) is None
    assert tiles.focus(300, 300, [(0.0, 0.0, 0.2, 0.2)]) is None
    assert tiles.focus(None, 300, [(0.0, 0.0, 0.2, 0.2)]) is None
    # a wide photo shows 3/4 of its width in a square: a face at the left edge keeps the left edge, one at the right
    # the right, one in the middle the middle; one a little right of the middle is moved as far as it needs
    assert tiles.focus(400, 300, [(0.0, 0.4, 0.1, 0.6)]) == "0% 50%"
    assert tiles.focus(400, 300, [(0.85, 0.4, 0.95, 0.6)]) == "100% 50%"
    assert tiles.focus(400, 300, [(0.45, 0.4, 0.55, 0.6)]) == "50% 50%"
    assert tiles.focus(400, 300, [(0.55, 0.4, 0.65, 0.6)]) == "90% 50%"  # middle 0.6: the crop starts at 0.225 of 0.25
    # a tall photo: its head near the top stays in the tile, not cut off for the middle of the body
    assert tiles.focus(300, 600, [(0.4, 0.05, 0.6, 0.2)]) == "50% 0%"
    # two people: the middle of both; someone small in the background doesn't pull it
    assert tiles.focus(400, 300, [(0.1, 0.4, 0.2, 0.6), (0.3, 0.4, 0.4, 0.6)]) == "0% 50%"
    assert tiles.focus(400, 300, [(0.45, 0.4, 0.55, 0.6), (0.96, 0.1, 0.98, 0.12)]) == "50% 50%"
    # one at each end of a wide photo: they can't both fit, so the larger one is shown whole, not the empty middle
    assert tiles.focus(1600, 1000, [(0.0, 0.1, 0.15, 0.4), (0.88, 0.1, 0.99, 0.4)]) == "0% 50%"
    assert tiles.focus(1600, 1000, [(0.0, 0.1, 0.1, 0.4), (0.85, 0.1, 0.99, 0.4)]) == "97% 50%"  # all of it, to 0.99
    # three where two fit: the larger two together
    aim = tiles.focus(1600, 1000, [(0.05, 0.1, 0.12, 0.4), (0.5, 0.1, 0.6, 0.4), (0.62, 0.1, 0.72, 0.4)])
    p = float(aim.split("%")[0]) / 100 * (1 - 1000 / 1600)
    assert p <= 0.5 and 0.72 <= p + 1000 / 1600 + 0.005 and p > 0.05  # aimed to a whole percent
    # a box with no size (a bad row) is no face
    assert tiles.focus(400, 300, [(0.9, 0.4, 0.9, 0.6)]) is None


def test_the_aim_keeps_the_faces_in_any_tile_wider_than_a_square():
    # a Whole photos tile, or a panorama kept in bounds, shows more of the photo than a square around the same place:
    # the faces' middle stays in sight
    for width, middle in ((400, 0.05), (400, 0.62), (400, 0.97), (1000, 0.3), (1000, 0.9)):
        aim = tiles.focus(width, 300, [(middle - 0.02, 0.4, middle + 0.02, 0.6)])
        p = float(aim.split("%")[0]) / 100
        for ratio in (300 / width, 0.6, 0.9):  # the share of the width a tile shows: a square's, then wider ones
            if ratio < 300 / width:
                continue
            start = (1 - ratio) * p
            assert start <= middle <= start + ratio, (width, middle, ratio)


def test_shapes_are_kept_in_bounds():
    assert tiles.shape(400, 300) == 1.333
    assert tiles.shape(4000, 300) == tiles.WIDEST and tiles.shape(100, 4000) == tiles.NARROWEST
    assert tiles.shape(None, None) == 1.0


def _face(con, image_id, box, **kw):
    v = np.ones(512, dtype=np.float32) / np.sqrt(512)
    fields = {"frame": None, "assigned_by": None, **kw}
    con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding, frame, assigned_by)
                   VALUES (?, ?, ?, ?, ?, 0.9, 25, ?, ?, ?)""",
                (image_id, *box, v.tobytes(), fields["frame"], fields["assigned_by"]))


def test_the_grids_aim_their_tiles_and_give_their_shapes(tmp_path, cfg, con, library):
    write_jpeg(library / "Wide" / "left.jpg", size=(400, 300), taken="2015:05:01 10:00:00")
    write_jpeg(library / "Wide" / "notface.jpg", size=(400, 300), taken="2015:05:02 10:00:00")
    write_jpeg(library / "Wide" / "tall.jpg", size=(300, 600), taken="2015:05:03 10:00:00")
    scan(con, cfg)
    ingest(con, cfg)
    ids = {name: con.execute("SELECT image_id FROM file WHERE path = ?", (rel("Wide", name),)).fetchone()[0]
           for name in ("left.jpg", "notface.jpg", "tall.jpg")}
    _face(con, ids["left.jpg"], (0.0, 0.4, 0.1, 0.6))
    _face(con, ids["notface.jpg"], (0.0, 0.4, 0.1, 0.6), assigned_by="not_a_face")  # said not to be a face
    _face(con, ids["tall.jpg"], (0.4, 0.05, 0.6, 0.2), frame=0)  # a video's frame's face doesn't aim it
    con.commit()
    folder = con.execute("SELECT id FROM folder WHERE path = ?", (rel("Wide"),)).fetchone()[0]
    page = TestClient(webapp.create_app(cfg), client=LAN).get(f"/folder/{folder}").text
    tile = {i: re.search(rf'<a class="tile" href="/image/{i}\?[^>]*>\s*<img [^>]*>', page).group(0)
            for i in ids.values()}
    assert 'style="--r: 1.333"' in tile[ids["left.jpg"]] and 'style="object-position: 0% 50%"' in tile[ids["left.jpg"]]
    assert "object-position" not in tile[ids["notface.jpg"]]
    assert 'style="--r: 0.5"' in tile[ids["tall.jpg"]] and "object-position" not in tile[ids["tall.jpg"]]
    assert 'sizes="auto, ' in tile[ids["left.jpg"]]
    # the menu offers the choices, kept in the browser (theme.js, app.js)
    assert 'data-look="tiles" data-look-value="whole"' in page and 'data-look="tileSize" data-look-value="l"' in page
