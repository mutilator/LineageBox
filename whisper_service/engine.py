"""Speech to text with whisper.cpp, holding GPU memory only while there is work.

The GPU is often shared with Ollama (the catalog's describing, and other clients such as Open WebUI, can use it all
day), so whisper-server is started for the first request and stopped after idle_minutes without one, and it goes on the
GPU only when the GPU has room for it with gpu_spare_mb left over; otherwise it runs on the CPU (about 3x realtime
instead of 60x).

It runs on Windows (start.ps1) and on Linux (the Docker image in this folder, on the CPU). With download=True,
missing models are downloaded in the background first; /health says how far that is.

Silero VAD (whisper.cpp's VAD tool, on the CPU) finds the speech first and each stretch of it is sent on its own:
Whisper writes "Thank you." over wind, engines and music. whisper-server's built-in VAD is not used because it joins
the stretches end to end, and a segment that crosses a join gets times interpolated across the cut-out audio (a line
said at 3:02 came back as 2:26-3:03).
"""
import ctypes
import hashlib
import io
import math
import os
import re
import socket
import struct
import subprocess
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from whisper_service.checksums import SHA256

HOME = Path(os.environ.get("WHISPER_HOME") or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "lineagebox-whisper")
EXE = ".exe" if os.name == "nt" else ""
# Where missing models come from: the repositories whisper.cpp's own download scripts use, at a fixed commit, so a file
# changed there later is never fetched. Each download must match its SHA-256 in checksums.py.
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/{name}"
VAD_URL = "https://huggingface.co/ggml-org/whisper-vad/resolve/9ffd54a1e1ee413ddf265af9913beaf518d1639b/{name}"
RATE = 16000
BYTES_PER_SECOND = RATE * 2
PAD = 0.3           # seconds of audio kept on each side of a stretch of speech
JOIN_GAP = 1.0      # stretches closer than this go to Whisper together
MAX_REGION = 300.0  # seconds per request to whisper-server
MIN_REGION = 1.1    # whisper.cpp ignores input under 1 s; shorter stretches are padded with silence
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows: no console window flashes up


@dataclass(frozen=True)
class Settings:
    bin_dir: Path = Path(os.environ.get("WHISPER_BIN") or HOME / "bin")
    model: Path = HOME / "models" / "ggml-large-v3-turbo-q5_0.bin"
    vad_model: Path = HOME / "models" / "ggml-silero-v6.2.0.bin"
    device: str = "auto"           # auto: the GPU when it has room, else the CPU; cpu: always the CPU
    gpu_need_mb: int = 1100        # whisper-server's VRAM with the default model: measured 800-1030 MiB, most after long files
    gpu_spare_mb: int = 500        # left free on top, for Ollama and the desktop
    idle_minutes: float = 10
    threads: int = min(8, os.cpu_count() or 4)
    vad_threshold: float = 0.35    # the default 0.5 dropped real speech over outdoor noise


class EngineError(Exception):
    def __init__(self, message: str, status: int = 500):
        super().__init__(message)
        self.status = status


def log(message: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {message}", flush=True)


def read_wav(data: bytes) -> bytes:
    """The samples of a 16 kHz mono 16-bit PCM WAV file. A size of 0 or 0xFFFFFFFF in the header (written by encoders
    that stream to a pipe) means "to the end of the file"."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a WAV file")
    pos, fmt = 12, None
    try:
        while pos + 8 <= len(data):
            chunk, size = data[pos:pos + 4], int.from_bytes(data[pos + 4:pos + 8], "little")
            start = pos + 8
            if chunk == b"fmt ":
                tag, channels, rate = struct.unpack_from("<HHI", data, start)
                bits = struct.unpack_from("<H", data, start + 14)[0]
                if tag == 0xFFFE:  # WAVE_FORMAT_EXTENSIBLE: the real tag starts the sub-format GUID
                    tag = struct.unpack_from("<H", data, start + 24)[0]
                fmt = (tag, channels, rate, bits)
            elif chunk == b"data":
                if fmt != (1, 1, RATE, 16):
                    raise ValueError("expected 16 kHz mono 16-bit PCM, got " + (
                        f"format {fmt[0]}, {fmt[1]} channel(s), {fmt[2]} Hz, {fmt[3]}-bit" if fmt else "no fmt chunk"))
                end = len(data) if size in (0, 0xFFFFFFFF) else min(len(data), start + size)
                return data[start:end - (end - start) % 2]
            pos = start + size + size % 2
    except struct.error:
        raise ValueError("the WAV header is cut short") from None
    raise ValueError("the WAV file has no data chunk")


def wav_file(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def speech_regions(speech: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    """Stretches of speech, padded and joined where they nearly touch: what is sent to Whisper, one request each."""
    regions: list[list[float]] = []
    for start, end in speech:
        start, end = max(0.0, start - PAD, regions[-1][1] if regions else 0.0), min(duration, end + PAD)
        if regions and start - regions[-1][1] < JOIN_GAP and end - regions[-1][0] <= MAX_REGION:
            regions[-1][1] = max(regions[-1][1], end)
        else:
            regions.append([start, end])
    return [(s, e) for s, e in regions if e > s]


def gpu_free_mb() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW).stdout
        used, total = (int(v) for v in out.splitlines()[0].split(","))
        return total - used
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def cpu_only_env() -> dict:
    # ggml loads its CUDA backend even with --no-gpu; hiding the GPU keeps a CPU run from taking any VRAM at all
    return {**os.environ, "CUDA_VISIBLE_DEVICES": "-1"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def number(value) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return round(value, 4) if math.isfinite(value) else None


class Engine:
    def __init__(self, settings: Settings):
        self.s = settings
        self.server_exe = settings.bin_dir / f"whisper-server{EXE}"
        self.vad_exe = settings.bin_dir / f"whisper-vad-speech-segments{EXE}"
        version = settings.bin_dir / "VERSION"
        self.version = version.read_text().strip() if version.exists() else "whisper.cpp (version unknown)"
        self.english_only = re.search(r"\.en[.-]", settings.model.name) is not None
        self.lock = threading.Lock()  # one transcription at a time; also keeps the idle check off a running one
        self.proc: subprocess.Popen | None = None
        self.device: str | None = None
        self.url = ""
        self.server_log = None
        self.last_used = time.monotonic()
        self.http = httpx.Client(timeout=httpx.Timeout(3600, connect=10))
        self.downloading: dict | None = None  # {"file", "done", "total"} while a model is being downloaded
        self.download_error: str | None = None
        self.job = kill_on_close_job() if os.name == "nt" else None
        (HOME / "tmp").mkdir(parents=True, exist_ok=True)
        for leftover in (HOME / "tmp").glob("*.wav"):  # from a run that was killed mid-way
            leftover.unlink(missing_ok=True)
        threading.Thread(target=self._unload_when_idle, daemon=True).start()

    def missing(self) -> list[str]:
        return [str(p) for p in (self.server_exe, self.vad_exe, self.s.model, self.s.vad_model) if not p.exists()]

    def loaded(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def health(self) -> dict:
        loaded = self.loaded()
        missing = self.missing()
        out = {"ok": not missing, "engine": self.version, "model": self.s.model.name,
               "device": self.device if loaded else self._pick_device()[0], "loaded": loaded}
        if self.downloading:
            out["downloading"] = dict(self.downloading)
        elif missing:
            out["error"] = self.download_error or "missing: " + ", ".join(Path(m).name for m in missing)
        return out

    def download_missing(self) -> None:
        """Download the model and the VAD model if they are not there yet, one after the other, in the background. Each
        is used only when its SHA-256 is the one in checksums.py; one that isn't listed there is not downloaded."""
        def run():
            for path, url in ((self.s.vad_model, VAD_URL), (self.s.model, MODEL_URL)):
                if path.exists():
                    continue
                expected = SHA256.get(path.name)
                if expected is None:
                    self.download_error = (f"{path.name} is not a model this service can check, so it is not "
                                           f"downloaded: put the file in {path.parent} yourself")
                    log(self.download_error)
                    break
                part = path.with_name(path.name + ".part")
                self.downloading = {"file": path.name, "done": 0, "total": None}
                log(f"downloading {path.name}")
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with httpx.stream("GET", url.format(name=path.name), follow_redirects=True,
                                      timeout=httpx.Timeout(120, connect=20)) as r:
                        r.raise_for_status()
                        self.downloading["total"] = int(r.headers.get("content-length") or 0) or None
                        digest = hashlib.sha256()
                        with open(part, "wb") as f:
                            for chunk in r.iter_bytes(1 << 20):
                                f.write(chunk)
                                digest.update(chunk)
                                self.downloading["done"] += len(chunk)
                    if digest.hexdigest() != expected:
                        part.unlink(missing_ok=True)
                        self.download_error = (f"the download of {path.name} is not the file expected (its SHA-256 "
                                               "differs), so it was not used")
                        log(self.download_error)
                        break
                    part.replace(path)
                    log(f"downloaded {path.name}")
                except (httpx.HTTPError, OSError) as e:
                    part.unlink(missing_ok=True)
                    self.download_error = f"could not download {path.name}: {type(e).__name__}: {e}"
                    log(self.download_error)
                    break
                finally:
                    self.downloading = None
        threading.Thread(target=run, daemon=True, name="download").start()

    def transcribe(self, pcm: bytes, language: str) -> dict:
        if missing := self.missing():
            raise EngineError("not ready: " + (f"downloading {self.downloading['file']}" if self.downloading
                                               else "missing " + ", ".join(Path(m).name for m in missing)), 503)
        with self.lock:
            began = self.last_used = time.monotonic()
            try:
                duration = len(pcm) / BYTES_PER_SECOND
                regions = speech_regions(self._find_speech(pcm), duration)
                segments, detected = [], None
                if regions:
                    self._ensure_running()
                for start, end in regions:
                    found, lang = self._transcribe_region(pcm, start, end, language)
                    segments += found
                    detected = detected or lang
                seconds = time.monotonic() - began
                log(f"transcribed {duration:.0f} s of audio ({len(regions)} stretches of speech, "
                    f"{sum(e - s for s, e in regions):.0f} s) in {seconds:.1f} s" + (f" on {self.device}" if regions else ""))
            finally:
                self.last_used = time.monotonic()
        if self.english_only:
            language = "en"
        elif language == "auto":
            language = detected or "auto"  # whisper-server names it ("english"), not a code
        return {"model": self.s.model.name, "language": language, "duration": round(duration, 3),
                "seconds": round(seconds, 2), "segments": segments}

    def close(self) -> None:
        with self.lock:
            self._stop("the service is stopping")

    def _find_speech(self, pcm: bytes) -> list[tuple[float, float]]:
        fd, path = tempfile.mkstemp(suffix=".wav", dir=HOME / "tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(wav_file(pcm))
            run = subprocess.run([str(self.vad_exe), "-f", path, "-vm", str(self.s.vad_model), "-t", str(self.s.threads),
                                  "-vt", str(self.s.vad_threshold), "-np"],
                                 capture_output=True, text=True, timeout=3600, env=cpu_only_env(), creationflags=NO_WINDOW)
        finally:
            os.unlink(path)
        if run.returncode:
            raise EngineError(f"voice detection failed (exit {run.returncode}): {run.stderr.strip()[-300:]}")
        # centiseconds
        return [(float(a) / 100, float(b) / 100) for a, b in re.findall(r"start = ([\d.]+), end = ([\d.]+)", run.stdout)]

    def _transcribe_region(self, pcm: bytes, start: float, end: float, language: str) -> tuple[list[dict], str | None]:
        audio = pcm[int(start * RATE) * 2:int(end * RATE) * 2]
        audio += bytes(max(0, int(MIN_REGION * BYTES_PER_SECOND) - len(audio)))
        try:
            r = self.http.post(f"{self.url}/inference", files={"file": ("audio.wav", wav_file(audio), "audio/wav")}, data={
                "response_format": "verbose_json", "language": language, "no_language_probabilities": "true",
                # word timings would make whisper-server re-wrap the segments at 60 characters
                "token_timestamps": "false"})
        except httpx.HTTPError as e:
            self._stop(f"it did not answer ({type(e).__name__})")
            raise EngineError(f"whisper-server did not answer ({type(e).__name__}: {e})", 502) from None
        if r.status_code != 200:
            raise EngineError(f"whisper-server answered {r.status_code}: {r.text[:300]}", 502)
        body = r.json()
        segments = []
        for seg in body.get("segments", []):
            # times past `end` are in the silence that pads a short stretch
            s = min(start + (number(seg.get("start")) or 0.0), end)
            e = min(start + (number(seg.get("end")) or 0.0), end)
            segments.append({"start": round(s, 2), "end": round(max(s, e), 2), "text": str(seg.get("text", "")).strip(),
                             "avg_logprob": number(seg.get("avg_logprob")),
                             "no_speech_prob": number(seg.get("no_speech_prob"))})
        return segments, body.get("language")

    def _pick_device(self) -> tuple[str, int | None]:
        if self.s.device == "cpu":
            return "cpu", None
        free = gpu_free_mb()
        return ("gpu" if free is not None and free >= self.s.gpu_need_mb + self.s.gpu_spare_mb else "cpu"), free

    def _ensure_running(self) -> None:
        if self.loaded() and self.device == "gpu":
            return
        device, free = self._pick_device()
        if self.loaded():
            if device == "cpu":
                return
            self._stop("the GPU has room again")
        self._start(device, free)

    def _start(self, device: str, free: int | None) -> None:
        log(f"starting whisper-server on the {device.upper()}" + (f" ({free} MiB of VRAM free; it needs "
            f"{self.s.gpu_need_mb} + {self.s.gpu_spare_mb} spare)" if free is not None else ""))
        port = free_port()
        args = [str(self.server_exe), "-m", str(self.s.model), "--host", "127.0.0.1", "--port", str(port),
                "-t", str(self.s.threads), "-bs", "5"]
        if device == "cpu":
            args.append("-ng")
        self.server_log = open(HOME / "whisper-server.log", "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(args, stdout=self.server_log, stderr=subprocess.STDOUT, cwd=HOME / "tmp",
                                     env=cpu_only_env() if device == "cpu" else None, creationflags=NO_WINDOW)
        if self.job:
            ctypes.windll.kernel32.AssignProcessToJobObject(self.job, ctypes.c_void_p(int(self.proc._handle)))
        self.device, self.url = device, f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 180
        while True:
            if self.proc.poll() is not None:
                code = self.proc.returncode
                self._stop("it exited")
                raise EngineError(f"whisper-server exited with code {code} while loading; see {HOME / 'whisper-server.log'}", 503)
            try:
                if self.http.get(f"{self.url}/health", timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                self._stop("it did not finish loading")
                raise EngineError("whisper-server did not finish loading in 3 minutes", 503)
            time.sleep(0.2)

    def _stop(self, why: str) -> None:
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.terminate()
                self.proc.wait(30)
            log(f"stopped whisper-server: {why}")
        if self.server_log:
            self.server_log.close()
        self.proc, self.server_log = None, None

    def _unload_when_idle(self) -> None:
        while True:
            time.sleep(15)
            if self.proc is None or time.monotonic() - self.last_used < self.s.idle_minutes * 60:
                continue
            if self.lock.acquire(blocking=False):
                try:
                    if self.proc is not None and time.monotonic() - self.last_used >= self.s.idle_minutes * 60:
                        self._stop(f"idle for {self.s.idle_minutes:g} minutes")
                finally:
                    self.lock.release()


def kill_on_close_job():
    """A Windows job that ends whisper-server when this process ends, however it ends (Stop-Process, a crash), so an
    orphaned whisper-server can never sit on GPU memory. None if the job cannot be made. (In Docker, the container
    ends with the service.)"""
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.windll.kernel32
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    job = k32.CreateJobObjectW(None, None)
    info = ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not job or not k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info)):
        log("could not make a job object: whisper-server may outlive this service if it is killed")
        return None
    return wintypes.HANDLE(job)
