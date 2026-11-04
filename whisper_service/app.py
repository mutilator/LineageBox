"""The HTTP side: GET /health and POST /transcribe (a 16 kHz mono 16-bit WAV as the body), for the catalog's worker.
Home network only, like the catalog's web UI; there is no other access control."""
import asyncio
import ipaddress
import logging
import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from whisper_service.engine import Engine, EngineError, read_wav

# The home network, as LineageBox has it (its config.PRIVATE_NETWORKS; tests/test_whisper_service.py checks they are
# the same), and any others in WHISPER_ALLOWED_NETWORKS, comma-separated (a VPN's 100.64.0.0/10, say).
PRIVATE_NETWORKS = ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "::1/128", "fc00::/7", "fe80::/10")
NETWORKS = [ipaddress.ip_network(n.strip(), strict=False)
            for n in (*PRIVATE_NETWORKS, *os.environ.get("WHISPER_ALLOWED_NETWORKS", "").split(",")) if n.strip()]
MAX_BODY = 300 * 2**20  # ~45 minutes is ~90 MB
AT_ONCE = 2  # transcriptions at a time: each request's audio is held in memory while it waits


def create_app(engine: Engine) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        engine.close()

    app = FastAPI(title="LineageBox Whisper", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    working = asyncio.Semaphore(AT_ONCE)

    @app.middleware("http")
    async def home_network_only(request: Request, call_next):
        if not _allowed(request.client.host if request.client else ""):
            return JSONResponse({"detail": "only available on the home network"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        # the whole error goes to the log; the answer names only its kind, so it never shows paths on this machine
        logging.getLogger("whisper_service").exception("transcribing failed")
        return JSONResponse({"detail": f"{type(exc).__name__} (the service's log has more)"}, status_code=500)

    @app.get("/health")
    def health():
        return engine.health()

    @app.post("/transcribe")
    async def transcribe(request: Request, language: str = "en"):
        if not re.fullmatch(r"[a-z]{2,3}|auto", language):
            raise HTTPException(400, "language must be a language code like en, or auto")
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > MAX_BODY:
            raise HTTPException(413, f"the audio is over {MAX_BODY // 2**20} MB")
        if working.locked():
            raise HTTPException(503, "busy with other videos; try again shortly")
        async with working:
            body = bytearray()
            async for chunk in request.stream():
                body += chunk
                if len(body) > MAX_BODY:
                    raise HTTPException(413, f"the audio is over {MAX_BODY // 2**20} MB")
            try:
                pcm = read_wav(bytes(body))
            except ValueError as e:
                raise HTTPException(400, str(e)) from None
            del body
            try:
                return await run_in_threadpool(engine.transcribe, pcm, language)
            except EngineError as e:
                raise HTTPException(e.status, str(e)) from None

    return app


def _allowed(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in n for n in NETWORKS)
