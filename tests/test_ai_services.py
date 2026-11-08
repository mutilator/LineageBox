"""Ollama, Whisper and faces: turning each off, a model that can't do the work, errors shown to people, and the
downloads the Status page follows."""
import dataclasses
import hashlib
import io
import json
import time
import zipfile
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from lineagebox import db, downloads, faces, llm, retry, settings, speech, worker
from lineagebox import describe as describing
from lineagebox.faces import FaceStats
from lineagebox.web import app as webapp
from tests.conftest import LAN
from tests.test_recognition import FakeOllama

APP = {"X-Requested-With": "lineagebox"}


def mock_ollama(cfg, handler) -> llm.Ollama:
    o = llm.Ollama(cfg)
    o.http = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    return o


def show(capabilities=None, status=200):
    def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.11"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "ministral-3:latest"}, {"name": "llama3:latest"}]})
        if request.url.path == "/api/show":
            if status != 200:
                return httpx.Response(status, json={"error": "model not found"})
            return httpx.Response(200, json={} if capabilities is None else {"capabilities": capabilities})
        return httpx.Response(400, json={"error": "llama3 does not support images"})
    return handler


def test_what_the_model_can_do(cfg):
    vision = dataclasses.replace(cfg, ollama_model="ministral-3")
    assert mock_ollama(vision, show(["completion", "vision"])).model_problem() is None
    assert "can look at pictures" in mock_ollama(vision, show(["completion", "vision"])).check()
    assert mock_ollama(vision, show()).model_problem() is None  # an Ollama too old to say: not held against it

    text = dataclasses.replace(cfg, ollama_model="llama3")
    problem = mock_ollama(text, show(["completion", "tools"])).model_problem()
    assert problem.text_ok and "can't look at pictures" in problem.message and "ministral-3" in problem.message
    assert "can't look at pictures" in mock_ollama(text, show(["completion"])).check()

    missing = dataclasses.replace(cfg, ollama_model="qwen2.5vl")
    problem = mock_ollama(missing, show(status=404)).model_problem()
    assert not problem.text_ok and "no model named qwen2.5vl" in problem.message
    assert "no model named qwen2.5vl" in mock_ollama(missing, show()).check()  # the list of models doesn't have it


def test_a_model_that_thinks_is_asked_not_to(cfg):
    """A thinking model (qwen3, deepseek-r1) would spend the answer's tokens thinking: it is told not to. One that
    doesn't think is asked as before, without the word, which an older Ollama doesn't know."""
    for capabilities, think in ((["completion", "thinking"], False), (["completion", "vision"], None)):
        sent = []

        def handler(request):
            if request.url.path == "/api/show":
                return httpx.Response(200, json={"capabilities": capabilities})
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"message": {"content": '{"ok": true}'}, "done_reason": "stop"})

        o = mock_ollama(cfg, handler)
        assert o.chat_json("system", "user", {})[0] == {"ok": True}
        o.chat_json("system", "user", {})
        assert [b.get("think") for b in sent] == [think, think]


def test_ollama_errors_say_what_ollama_said(cfg):
    o = mock_ollama(dataclasses.replace(cfg, ollama_model="llama3"), show())
    try:
        o.chat_json("system", "Catalog this photo.", {}, images=[b"jpeg"])
    except llm.OllamaError as e:
        assert str(e) == "Ollama answered 400: llama3 does not support images"
    else:
        raise AssertionError("no error")


def test_a_model_killed_as_it_loads_is_said_to_need_memory(cfg):
    """A container's memory limit stops Ollama's model part way through loading, and Ollama says only that it was
    killed: the message says what that most likely means."""
    killed = mock_ollama(cfg, lambda request: httpx.Response(
        500, json={"error": "llama-server process has terminated: signal: killed"}))
    with pytest.raises(llm.OllamaError, match="signal: killed. The model most likely ran out of memory"):
        killed.chat_json("system", "Catalog this photo.", {}, images=[b"jpeg"])


def test_a_model_that_cant_see_is_shown_and_nothing_is_described(con, cfg, library, monkeypatch):
    class Blind(FakeOllama):
        def reachable(self):
            return True

        def model_problem(self):
            return llm.ModelProblem("llama3 can't look at pictures.", text_ok=True)

    monkeypatch.setattr(worker, "Ollama", Blind)
    monkeypatch.setattr(worker, "detect", lambda con, cfg, show_progress: FaceStats())
    monkeypatch.setattr(worker, "describe", lambda *a, **k: (_ for _ in ()).throw(AssertionError("described")))
    r = worker.run_once(con, cfg)
    assert r.waiting_for == "model"
    assert settings.problems(con)["ollama"]["message"] == "llama3 can't look at pictures."
    worker.set_state(con, waiting_for=r.waiting_for)  # as run_forever does after each pass
    page = TestClient(webapp.create_app(cfg), client=LAN).get("/status").text
    assert "llama3 can&#39;t look at pictures." in page
    assert "Waiting for a model that can look at pictures" in page


def test_errors_ollama_answers_every_photo_with_are_shown(con, cfg, library, monkeypatch):
    class Errors(FakeOllama):
        def reachable(self):
            return True

        def chat_json(self, *a, **k):
            raise llm.OllamaError("Ollama answered 500: model requires more system memory (9.1 GiB)")

    monkeypatch.setattr(worker, "Ollama", Errors)
    monkeypatch.setattr(describing, "Ollama", Errors)
    monkeypatch.setattr(worker, "detect", lambda con, cfg, show_progress: FaceStats())
    worker.run_once(con, cfg)
    message = settings.problems(con)["ollama"]["message"]
    assert message == "Describing failed: OllamaError: Ollama answered 500: model requires more system memory (9.1 GiB)"
    assert "Describing failed" in TestClient(webapp.create_app(cfg), client=LAN).get("/settings").text

    monkeypatch.setattr(describing, "Ollama", OnlineFake)
    monkeypatch.setattr(worker, "Ollama", OnlineFake)
    worker.run_once(con, cfg)
    assert settings.problems(con) == {}  # it works again


class OnlineFake(FakeOllama):
    def reachable(self):
        return True


def test_each_kind_of_ai_can_be_turned_off(con, cfg, library, monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "Ollama", lambda cfg: calls.append("ollama"))
    monkeypatch.setattr(worker, "detect", lambda con, cfg, show_progress: calls.append("faces") or FaceStats())
    monkeypatch.setattr(worker.speech, "Whisper", lambda cfg: calls.append("whisper"))
    off = settings.save(con, cfg, {"describe_enabled": False, "transcribe_enabled": "false", "faces_enabled": False})
    assert (off.describe_enabled, off.transcribe_enabled, off.faces_enabled) == (False, False, False)
    r = worker.run_once(con, settings.current(con, cfg))
    assert calls == [] and r.waiting_for is None
    live = TestClient(webapp.create_app(cfg), client=LAN).get("/status/live").text
    assert "AI descriptions are off" in live
    page = TestClient(webapp.create_app(cfg), client=LAN).get("/settings").text
    assert 'name="describe_enabled">' in page and 'name="faces_enabled">' in page  # unticked


def test_download_progress(con):
    p = downloads.Progress(con, "faces", "Face recognition models")
    p.update(50_000_000, 280_000_000)
    [d] = downloads.current(con)
    assert d["state"] == "downloading" and d["label"] == "Face recognition models"
    p.written = 0  # as if a few seconds went by
    p.update(140_000_000)
    assert downloads.current(con)[0]["percent"] == 50
    later = datetime.now() + timedelta(minutes=10)
    assert downloads.current(con, later)[0]["state"] == "stalled"  # cut off by a restart
    p.finish()
    assert downloads.current(con)[0]["state"] == "done"
    assert downloads.current(con, datetime.now() + timedelta(days=2)) == []  # gone from the page a day later


def test_face_models_are_downloaded_with_progress(con, tmp_path, monkeypatch):
    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, "w") as z:
        for name in faces.MODEL_FILES:
            z.writestr(name, b"onnx" * 1000)
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=zipped.getvalue())))
    monkeypatch.setattr(faces.httpx, "stream", lambda method, url, **kw: client.stream(method, url))
    target = tmp_path / "insightface" / "models" / "buffalo_l"

    # not the file this version expects: nothing unpacked, the reason on the Status page, not fetched again today
    with pytest.raises(faces.Unavailable, match="SHA-256"):
        faces.ensure_models(con, tmp_path / "insightface")
    assert not target.exists() and not list(target.parent.iterdir())
    assert downloads.current(con)[0]["state"] == "failed"
    monkeypatch.setattr(faces.httpx, "stream", None)
    with pytest.raises(faces.Unavailable, match="SHA-256"):
        faces.ensure_models(con, tmp_path / "insightface")

    monkeypatch.setattr(faces.httpx, "stream", lambda method, url, **kw: client.stream(method, url))
    monkeypatch.setattr(faces, "MODELS_SHA256", hashlib.sha256(zipped.getvalue()).hexdigest())
    con.execute("DELETE FROM settings WHERE key = 'download:faces'")  # a day later
    con.commit()
    faces.ensure_models(con, tmp_path / "insightface")
    assert sorted(p.name for p in target.iterdir()) == sorted(faces.MODEL_FILES)
    assert downloads.current(con)[0]["state"] == "done"
    monkeypatch.setattr(faces.httpx, "stream", None)  # there now: not downloaded again
    faces.ensure_models(con, tmp_path / "insightface")


def test_ollama_pulls_a_model_for_the_status_page(cfg, con, monkeypatch):
    lines = [{"status": "pulling manifest"},
             {"status": "pulling a", "digest": "sha256:a", "total": 4_000_000, "completed": 1_000_000},
             {"status": "pulling b", "digest": "sha256:b", "total": 1_000_000, "completed": 1_000_000},
             {"status": "pulling a", "digest": "sha256:a", "total": 4_000_000, "completed": 4_000_000},
             {"status": "success"}]
    body = "\n".join(json.dumps(m) for m in lines).encode()
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)))
    monkeypatch.setattr(llm.httpx, "stream", lambda method, url, **kw: client.stream(method, url, json=kw["json"]))
    llm.pull(cfg, db.connect(cfg, migrate_schema=False))
    [d] = downloads.current(con)
    assert (d["state"], d["done"], d["total"], d["label"]) == ("done", 5_000_000, 5_000_000, "ministral-3, for Ollama")
    assert settings.get(con, "run_requested")  # the worker starts a pass to use it

    body = json.dumps({"error": "pull model manifest: file does not exist"}).encode()
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)))
    llm.pull(cfg, db.connect(cfg, migrate_schema=False))
    assert downloads.current(con)[0]["error"] == "pull model manifest: file does not exist"


def test_downloads_on_the_status_page(cfg, con, monkeypatch):
    started = []
    monkeypatch.setattr(llm, "pull", lambda cfg, c: (started.append(cfg.ollama_model), c.close()))
    c = TestClient(webapp.create_app(cfg), client=LAN)
    assert "Nothing is downloading" in c.get("/status").text
    assert "is downloading ministral-3" in c.post("/api/ollama/pull", headers=APP).json()["message"]
    for _ in range(200):  # it runs in a thread of its own
        if started:
            break
        time.sleep(0.01)
    assert started == ["ministral-3"]
    downloads.Progress(con, "faces", "Face recognition models").update(1, 2)
    monkeypatch.setattr(speech.Whisper, "health", lambda self, timeout=2: {
        "ok": False, "downloading": {"file": "ggml-base.bin", "done": 70_000_000, "total": 140_000_000}})
    whisper = dataclasses.replace(cfg, whisper_url="http://whisper.test")
    page = TestClient(webapp.create_app(whisper), client=LAN).get("/status/live").text
    assert "Face recognition models" in page and "ggml-base.bin, for Whisper" in page and "70 MB of 140 MB" in page


def test_photos_that_cant_be_described_dont_hold_up_the_rest(con, cfg, library, monkeypatch):
    """The newest photos first: a dozen whose previews are gone are skipped, and the older ones are described."""
    from tests.conftest import write_jpeg
    for i in range(12):
        write_jpeg(library / "new" / f"{i}.jpg", color=(i * 20, 90, 120))
    monkeypatch.setattr(describing, "Ollama", OnlineFake)
    worker.scan(con, cfg)
    worker.ingest(con, cfg, show_progress=False)
    newest = [r[0] for r in con.execute("SELECT sha256 FROM image ORDER BY id DESC LIMIT 12")]
    for sha in newest:
        cfg.preview_path(sha).unlink()
    s = describing.describe(con, cfg, show_progress=False)
    assert not s.gave_up and s.failed == 12 and s.done > 0
    assert s.last_error is None  # not Ollama's problem: nothing to show on the Status page
    # tried again later, a while longer after each failure, instead of first on the next pass
    assert describing.count_pending(con, OnlineFake(cfg).model) == 0
    assert retry.summary(con) == [{"stage": "describe", "what": retry.STAGES["describe"], "count": 12,
                                   "error": retry.summary(con)[0]["error"]}]
    assert "couldn't be done and are tried again later" in TestClient(webapp.create_app(cfg), client=LAN).get(
        "/status").text
    con.execute("UPDATE retry SET next_at = 0")  # their time has come
    con.commit()
    assert describing.count_pending(con, OnlineFake(cfg).model) == 12
