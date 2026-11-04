"""The speech-to-text service downloads its models when they are missing, says so in /health, and refuses to
transcribe until they are there."""
import hashlib
import time

import httpx
import pytest

from whisper_service import engine


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "HOME", tmp_path)  # never the real service's folder (it clears its tmp)
    models = tmp_path / "models"
    return engine.Engine(engine.Settings(bin_dir=tmp_path / "bin", model=models / "ggml-base.bin",
                                         vad_model=models / "ggml-silero-v6.2.0.bin"))


def serve(monkeypatch, content=b"m" * 3_000_000, status=200):
    """The two repositories, answering every model with content; returns the addresses asked for."""
    asked = []

    def handler(request):
        asked.append(str(request.url))
        return httpx.Response(status, content=content)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(engine.httpx, "stream", lambda method, url, **kw: client.stream(method, url))
    return asked


def wait_for(done):
    for _ in range(500):
        if done():
            return
        time.sleep(0.01)


def test_models_are_downloaded_when_missing(service, monkeypatch):
    asked = serve(monkeypatch)
    good = hashlib.sha256(b"m" * 3_000_000).hexdigest()
    monkeypatch.setattr(engine, "SHA256", {"ggml-base.bin": good, "ggml-silero-v6.2.0.bin": good})
    assert "missing" in service.health()["error"]
    with pytest.raises(engine.EngineError) as e:
        service.transcribe(b"\0" * 32000, "en")
    assert e.value.status == 503

    service.download_missing()
    wait_for(lambda: service.s.model.exists() and not service.downloading)
    assert service.s.vad_model.stat().st_size == service.s.model.stat().st_size == 3_000_000
    assert asked == [engine.VAD_URL.format(name="ggml-silero-v6.2.0.bin"), engine.MODEL_URL.format(name="ggml-base.bin")]
    assert "/resolve/main/" not in asked[0] + asked[1]  # a fixed commit, never whatever the branch holds now
    assert "downloading" not in service.health() and not list(service.s.model.parent.glob("*.part"))


def test_a_failed_download_is_reported(service, monkeypatch):
    serve(monkeypatch, b"", 404)
    service.download_missing()
    wait_for(lambda: service.download_error)
    assert "could not download ggml-silero-v6.2.0.bin" in service.health()["error"]


def test_a_download_that_isnt_the_file_expected_is_not_used(service, monkeypatch):
    """A file replaced upstream (or on the way) doesn't match its SHA-256: it is thrown away, and /health says why."""
    serve(monkeypatch, b"not the model")
    service.download_missing()
    wait_for(lambda: service.download_error)
    assert "SHA-256" in service.health()["error"] and not service.s.vad_model.exists()
    assert not list(service.s.vad_model.parent.glob("*.part"))


def test_a_model_without_a_checksum_is_not_downloaded(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "HOME", tmp_path)
    asked = serve(monkeypatch)
    good = hashlib.sha256(b"m" * 3_000_000).hexdigest()
    monkeypatch.setattr(engine, "SHA256", {"ggml-silero-v6.2.0.bin": good})
    service = engine.Engine(engine.Settings(bin_dir=tmp_path / "bin", model=tmp_path / "models" / "my-own.bin",
                                            vad_model=tmp_path / "models" / "ggml-silero-v6.2.0.bin"))
    service.download_missing()
    wait_for(lambda: service.download_error)
    assert "my-own.bin is not a model this service can check" in service.health()["error"]
    assert asked == [engine.VAD_URL.format(name="ggml-silero-v6.2.0.bin")]  # the VAD model, which it can


def test_every_default_model_has_a_checksum():
    assert {engine.Settings().model.name, engine.Settings().vad_model.name} <= engine.SHA256.keys()


def test_the_home_network_is_lineageboxs():
    """The service can't import LineageBox, so it has its own copy of the list; they must not drift apart."""
    from lineagebox import config
    from whisper_service import app
    assert app.PRIVATE_NETWORKS == config.PRIVATE_NETWORKS
    assert app._allowed("fd00::5") and app._allowed("192.168.1.20") and not app._allowed("203.0.113.9")
