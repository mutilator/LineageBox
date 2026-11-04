"""python -m whisper_service [options]: the speech-to-text service for the catalog. start.ps1 runs it in the background."""
import argparse
import sys
from pathlib import Path

import uvicorn

from whisper_service.app import create_app
from whisper_service.engine import HOME, Engine, Settings, log

KEEP_LOG = 5 * 2**20


def main() -> None:
    d = Settings()
    p = argparse.ArgumentParser(prog="python -m whisper_service")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=9300)
    p.add_argument("--model", type=Path, default=d.model,
                   help="a whisper.cpp ggml model file, or the name of one in the models folder")
    p.add_argument("--device", choices=("auto", "cpu"), default=d.device,
                   help="auto: the GPU when it has room (--gpu-need-mb plus --gpu-spare-mb free), else the CPU")
    p.add_argument("--gpu-need-mb", type=int, default=d.gpu_need_mb, help="whisper-server's VRAM with --model")
    p.add_argument("--gpu-spare-mb", type=int, default=d.gpu_spare_mb)
    p.add_argument("--idle-minutes", type=float, default=d.idle_minutes, help="stop whisper-server after this long unused")
    p.add_argument("--vad-threshold", type=float, default=d.vad_threshold)
    p.add_argument("--threads", type=int, default=d.threads, help="CPU threads for Whisper and voice detection")
    p.add_argument("--download", action="store_true", help="download missing models first, in the background")
    p.add_argument("--log", type=Path, help="append output to this file (pythonw has no console)")
    a = p.parse_args()
    if a.model.parent == Path("."):  # a name: ggml-small.bin
        a.model = HOME / "models" / a.model
    if a.log:
        if a.log.exists() and a.log.stat().st_size > KEEP_LOG:
            a.log.replace(a.log.with_suffix(a.log.suffix + ".1"))
        sys.stdout = sys.stderr = open(a.log, "a", buffering=1, encoding="utf-8", errors="replace")
    engine = Engine(Settings(model=a.model, device=a.device, gpu_need_mb=a.gpu_need_mb, gpu_spare_mb=a.gpu_spare_mb,
                             idle_minutes=a.idle_minutes, vad_threshold=a.vad_threshold, threads=a.threads))
    log(f"{engine.version}, {a.model.name}; listening on {a.host}:{a.port}")
    if a.download:
        engine.download_missing()
    elif missing := engine.missing():
        log("missing: " + ", ".join(missing))
    uvicorn.run(create_app(engine), host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
