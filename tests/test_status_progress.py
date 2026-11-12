"""Status → Work in progress: each kind of work with a bar of how far it is, and the time left at the pace the
worker's notes show (progress.counts, record, pace)."""
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from lineagebox import progress, settings
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from lineagebox.web import app as webapp
from tests.conftest import LAN

NOW = datetime(2026, 11, 11, 20, 0, 0)


def note(hours_ago: float, **left) -> dict:
    return {"at": (NOW - timedelta(hours=hours_ago)).isoformat(timespec="seconds"), "left": left}


def test_the_pace_of_each_kind_of_work():
    notes = [note(30, closely=900), note(10, closely=600), note(4, closely=400), note(0.1, closely=310)]
    # from the earliest note of the last day: 300 fewer in 10 hours, the hours between passes too
    assert progress.pace(notes, "closely", 300, NOW) == ("going", 30.0)
    assert progress.pace(notes, "closely", 600, NOW) == ("still", None)  # no fewer than then: waiting its turn
    assert progress.pace([note(0.2, closely=10)], "closely", 5, NOW) == ("early", None)  # too soon to tell
    assert progress.pace(notes, "faces", 5, NOW) == ("early", None)  # nothing noted of it


def test_the_worker_notes_what_is_left(con, cfg):
    settings.put(con, progress.HISTORY_KEY, [note(60, faces=1), note(30, faces=2)])
    progress.record(con, cfg, now=NOW)
    notes = settings.get(con, progress.HISTORY_KEY)
    assert [n["at"] for n in notes] == [note(30)["at"], NOW.isoformat(timespec="seconds")]  # two days kept
    assert {"files", "faces", "closely", "describe", "check"} <= set(notes[-1]["left"])


def test_the_status_page_shows_bars_and_time_left(con, cfg):
    scan(con, cfg)
    ingest(con, cfg)
    waiting = {w["key"]: w for w in progress.counts(con, cfg)["waiting"]}
    photos = waiting["sharpness"]["left"]
    assert photos and waiting["sharpness"]["total"] == photos and waiting["files"]["left"] == 0
    now = datetime.now()
    settings.put(con, progress.HISTORY_KEY, [{"at": (now - timedelta(hours=2)).isoformat(timespec="seconds"),
                                              "left": {"sharpness": photos + 10, "faces": waiting["faces"]["left"]}}])
    page = TestClient(webapp.create_app(cfg), client=LAN).get("/status").text
    assert "<h2>Work in progress</h2>" in page
    row = lambda label: page[page.index(f'<span class="work-what">{label}</span>'):].split("</li>")[0]
    # read every file: a full bar, done
    files = row("New or changed files to read")
    assert 'aria-valuenow="100"' in files and "done" in files
    # sharpness: none measured yet, 10 an hour since the note two hours ago: under an hour for the rest
    sharp = row("Photos to measure for sharpness")
    assert f"0 of {photos}" in sharp and 'aria-valuenow="0"' in sharp and "under an hour left" in sharp
    # faces: no fewer than two hours ago
    assert "not moving lately" in row("Photos and videos to check for faces")
    # a queue of what was asked for has no bar, and with nothing in it, no row
    assert "Photos asked to be described again" not in page
