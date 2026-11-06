"""Minimal Ollama client: one chat call with a JSON-schema answer and optional images; whether the chosen model can
look at pictures; pulling it."""
import base64
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import httpx

from lineagebox.config import Config

VISION_MODELS = "ministral-3 or gemma3"  # suggested when the chosen model has no vision


class OllamaError(Exception):
    """Ollama answered, with an error: its own message, e.g. "model 'x' not found"."""


class CutOff(ValueError):
    """The answer reached max_tokens before it was finished, so it isn't whole JSON: it repeated itself, or the
    question needed a longer answer than allowed for."""


@dataclass
class Usage:
    prompt_tokens: int
    output_tokens: int
    seconds: float


@dataclass
class ModelProblem:
    """Why the chosen model can't describe photos. text_ok: it can still read folder names (it only lacks vision)."""
    message: str
    text_ok: bool = False


class Ollama:
    def __init__(self, cfg: Config, timeout: float = 600):
        self.model = cfg.ollama_model
        self.keep_alive = cfg.ollama_keep_alive
        # a sleeping or switched-off Ollama PC should fail fast, not after the long answer timeout
        self.http = httpx.Client(base_url=cfg.ollama_url, timeout=httpx.Timeout(timeout, connect=10))
        self._thinks: bool | None = None

    def thinks(self) -> bool:
        """Whether the model thinks before it answers (qwen3, deepseek-r1), asked of Ollama once. Its thinking is
        switched off: it spends the answer's tokens on it. Measured on Ollama 0.40: qwen3.5:4b, left to think, thought
        through all 4,096 on a three-sentence obituary and never answered; told not to, it answered in 321.
        deepseek-r1 thinks all the same, then answers."""
        if self._thinks is None:
            try:
                r = self.http.post("/api/show", json={"model": self.model, "name": self.model}, timeout=30)
                self._thinks = r.is_success and "thinking" in (r.json().get("capabilities") or [])
            except (httpx.HTTPError, ValueError):
                return False  # asked again next time
        return self._thinks

    def reachable(self) -> bool:
        try:
            return self.http.get("/api/version", timeout=10).is_success
        except httpx.HTTPError:
            return False

    def model_problem(self) -> ModelProblem | None:
        """None when the model is there and can look at pictures (or this Ollama is too old to say)."""
        try:
            r = self.http.post("/api/show", json={"model": self.model, "name": self.model}, timeout=30)
        except httpx.HTTPError:
            return None  # not answering: the worker deals with that on its own
        if r.status_code == 404:
            return ModelProblem(f"Ollama has no model named {self.model}. Download it with the button on the Settings "
                                f"page, or pick another model.")
        if not r.is_success:
            return ModelProblem(f"Ollama could not say what {self.model} is: {_error_text(r)}")
        capabilities = r.json().get("capabilities")
        if capabilities is not None and "vision" not in capabilities:
            return ModelProblem(f"{self.model} can't look at pictures (it can do: {', '.join(capabilities) or 'nothing'})."
                                f" Pick a model with vision, such as {VISION_MODELS}.", text_ok=True)
        return None

    def check(self) -> str:
        """A sentence for the Settings page: does Ollama answer, does it have the model, can the model see."""
        try:
            version = self.http.get("/api/version", timeout=10).json().get("version", "?")
            names = [m.get("name", "") for m in self.http.get("/api/tags", timeout=10).json().get("models", [])]
        except (httpx.HTTPError, ValueError) as e:
            return f"Not answering ({type(e).__name__}). Is the machine on and Ollama running?"
        if not any(n == self.model or n.split(":")[0] == self.model for n in names):
            return (f"Ollama {version} answers, but has no model named {self.model}. It has: {', '.join(names) or 'none'}."
                    " Download it with the button below, or pick one of those.")
        if problem := self.model_problem():
            return f"Ollama {version} answers and has {self.model}, but: {problem.message}"
        return f"Ollama {version} answers and has {self.model}, which can look at pictures."

    def chat_json(self, system: str, user: str, schema: dict, images: Sequence[bytes] = (),
                  temperature: float = 0.1, max_tokens: int = 1024) -> tuple[dict, Usage]:
        """images: JPEGs, in order (a video's frames). max_tokens stops an answer that repeats itself until
        it is cut off; the longest photo descriptions are about 400 tokens, and a question whose answer grows
        with it (a batch of folders) passes more. Raises ValueError for an answer that is not valid JSON (CutOff
        when it stopped at max_tokens), OllamaError when Ollama answers with an error."""
        message = {"role": "user", "content": user}
        if images:
            message["images"] = [base64.b64encode(im).decode() for im in images]
        body = {
            "model": self.model, "stream": False, "keep_alive": self.keep_alive, "format": schema,
            # No num_ctx: other clients (Open WebUI, say) may share this server, and a context size that differs
            # from the server default makes Ollama reload the model every time requests alternate.
            "options": {"temperature": temperature, "num_predict": max_tokens},
            "messages": [{"role": "system", "content": system}, message],
        }
        if self.thinks():
            body["think"] = False
        r = self.http.post("/api/chat", json=body)
        if not r.is_success:
            raise OllamaError(f"Ollama answered {r.status_code}: {_error_text(r)}")
        body = r.json()
        usage = Usage(body.get("prompt_eval_count", 0), body.get("eval_count", 0), body.get("total_duration", 0) / 1e9)
        try:
            return json.loads(body["message"]["content"]), usage
        except ValueError as e:
            if body.get("done_reason") == "length":
                raise CutOff(f"the answer was cut off at {max_tokens} tokens ({e})") from e
            raise


def pull(cfg: Config, con: sqlite3.Connection) -> None:
    """Have Ollama download the chosen model, with its progress on the Status page. Runs in a thread of the web app,
    with a connection of its own, which it closes. When it is done, the worker starts a pass to use it."""
    from lineagebox import downloads, settings

    progress = downloads.Progress(con, "ollama", f"{cfg.ollama_model}, for Ollama")
    layers: dict[str, tuple[int, int]] = {}  # digest -> (done, total): a model is several files
    try:
        # long waits between lines while Ollama checks what it downloaded
        with httpx.stream("POST", f"{cfg.ollama_url}/api/pull", json={"model": cfg.ollama_model, "stream": True},
                          timeout=httpx.Timeout(900, connect=10)) as r:
            if not r.is_success:
                r.read()
                raise OllamaError(f"Ollama answered {r.status_code}: {_error_text(r)}")
            for line in r.iter_lines():
                if not line.strip():
                    continue
                msg = json.loads(line)
                if msg.get("error"):
                    raise OllamaError(msg["error"])
                if msg.get("digest") and msg.get("total"):
                    layers[msg["digest"]] = (int(msg.get("completed") or 0), int(msg["total"]))
                    progress.update(sum(d for d, _ in layers.values()), sum(t for _, t in layers.values()))
        progress.finish()
        settings.put(con, "run_requested", datetime.now().isoformat(timespec="seconds"))
    except (httpx.HTTPError, ValueError, OllamaError) as e:
        progress.fail(f"{type(e).__name__}: {e}" if not isinstance(e, OllamaError) else str(e))
    finally:
        con.close()


def _error_text(r: httpx.Response) -> str:
    try:
        return str(r.json().get("error") or r.text)[:300]
    except ValueError:
        return r.text[:300] or r.reason_phrase
