"""Search by meaning: what a photo's words mean, as numbers, so that "kids sledding" finds "children on a snowy hill"
and a photo's page can show others like it.

Each photo search shows (one of each group of similar shots) gets one vector, made from the family's caption and the
AI's description of it (its caption, setting and occasion, then what is going on, the things, animals and tags) by a
small embedding model on the family's own Ollama (Settings → AI, "Model for search by meaning"; docs/design-notes.md
says how it was picked). The worker makes them as descriptions and captions are written (embed_new); a search has its
words read by the same model, then compares them with every photo's, in NumPy, as faces are compared.

Whenever Ollama is away (the PC asleep) or slow, search finds words only, as before: it never waits long for it
(ASK_FOR), and after Ollama didn't answer it stops asking for a while (AWAY_FOR).
"""
import hashlib
import json
import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import numpy as np

from lineagebox import catalog, retry
from lineagebox.config import Config
from lineagebox.llm import Ollama, OllamaError

BATCH = 32          # descriptions read in one request to Ollama
MOST_CHARS = 1500   # of a photo's words: the small models read about 256 words, and the caption comes first
ASK_FOR = 2.5       # seconds a search waits for the meaning of its words; slower, and it finds words only
AWAY_FOR = 60       # seconds after Ollama didn't answer a search before a search asks it again
LOOK_EVERY = 10     # seconds between looks for vectors the worker made since (they are kept in memory)
KEPT_WORDS = 500    # searches whose meaning is kept, so paging and ←/→ through the results don't ask again
GIVE_UP_AFTER = 3   # requests in a row that failed: Ollama has stopped answering; the rest waits for the next pass

# Words some models want before what they read, as their makers say: (before a photo's words, before a search's).
PREFIXES = {"nomic-embed-text": ("search_document: ", "search_query: "),
            "mxbai-embed-large": ("", "Represent this sentence for searching relevant passages: ")}
# How close a photo's meaning must be to a search's to be found by it, for the models measured (design-notes.md:
# below these, most were about something else). Another model is held to RELATIVE of the best match instead.
FLOORS = {"all-minilm": 0.38, "nomic-embed-text": 0.62, "mxbai-embed-large": 0.57}
RELATIVE = 0.85
MOST = 300          # photos found by meaning at most, before the filters
LIKE = 12           # photos "More like this" shows
LIKE_PER_DAY = 3    # of them from one day at most: the same party's other photos say the same, and the rest is variety


@dataclass(frozen=True)
class Query:
    """A search's words as the embedding model reads them (asker)."""
    model: str
    vector: np.ndarray


def _base(model: str) -> str:
    return model.split(":")[0].split("/")[-1]


def _prefix(model: str, query: bool) -> str:
    return PREFIXES.get(_base(model), ("", ""))[query]


def floor(model: str, best: float) -> float:
    """The least likeness a photo needs to be found by a search whose best match has this likeness."""
    return FLOORS.get(_base(model), best * RELATIVE)


# ---- what a photo means ----------------------------------------------------------------------------------------------

def text_of(con: sqlite3.Connection, image_id: int) -> str:
    """The words a photo is embedded by: the family's caption (of any copy in its group, this one's first), then the
    AI's description. '' when it has neither."""
    row = con.execute("""SELECT c.text FROM comment c JOIN image m ON m.id = c.image_id WHERE c.kind = 'caption'
                         AND (m.id = :i OR m.dup_group_id = (SELECT dup_group_id FROM image WHERE id = :i))
                         ORDER BY m.id = :i DESC, c.id LIMIT 1""", {"i": image_id}).fetchone()
    a = catalog.analysis_for(con, image_id)
    raw = json.loads(a["raw_json"]) if a else {}
    words = dict.fromkeys(w.strip() for k in ("activities", "objects", "animals", "tags")
                          for w in raw.get(k) or [] if isinstance(w, str) and w.strip())
    parts = [row[0].strip() if row else "", (raw.get("caption") or "").strip(),
             f"Setting: {raw['setting']}." if raw.get("setting") else "",
             f"Occasion: {raw['event']}." if raw.get("event") else "", ", ".join(words)]
    return " ".join(p for p in parts if p)[:MOST_CHARS]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# A photo search shows, with a description or a family caption somewhere in its group
_HAS_WORDS = """(EXISTS (SELECT 1 FROM analysis a WHERE a.image_id = i.id)
                 OR EXISTS (SELECT 1 FROM analysis a JOIN image m ON m.id = a.image_id WHERE m.dup_group_id = i.id)
                 OR EXISTS (SELECT 1 FROM comment c JOIN image m ON m.id = c.image_id
                            WHERE c.kind = 'caption' AND (m.id = i.id OR m.dup_group_id = i.id)))"""
_PENDING = f"""FROM image i WHERE (i.dup_group_id IS NULL OR i.dup_group_id = i.id) AND {catalog.VISIBLE}
    AND {retry.waiting("meaning")}
    AND (i.id IN (SELECT coalesce(m.dup_group_id, m.id) FROM embed_stale s JOIN image m ON m.id = s.image_id)
         OR NOT EXISTS (SELECT 1 FROM embedding e WHERE e.image_id = i.id AND e.model = :model) AND {_HAS_WORDS})"""


def pending(con: sqlite3.Connection, model: str) -> list[int]:
    """Photos to embed (again), newest first: never embedded with this model, or their words changed since."""
    return [r[0] for r in con.execute(f"SELECT i.id {_PENDING} ORDER BY i.id DESC", {"model": model})]


def count_pending(con: sqlite3.Connection, model: str) -> int:
    return con.execute(f"SELECT count(*) {_PENDING}", {"model": model}).fetchone()[0] if model else 0


@dataclass
class EmbedStats:
    pending: int = 0
    done: int = 0
    unchanged: int = 0
    failed: int = 0
    gave_up: bool = False     # Ollama stopped answering: the rest waits for the next pass
    stopped: bool = False     # stop() said so
    missing: str | None = None  # Ollama has no such model: what it said
    last_error: str | None = None


def embed_new(con: sqlite3.Connection, cfg: Config, stop: Callable[[], bool] | None = None) -> EmbedStats:
    """The worker's stage: embed what is pending, BATCH at a time, each saved in a short write of its own. A batch
    Ollama answers an error for waits (retry.py); Ollama not answering, or not having the model, ends the stage."""
    model = cfg.ollama_embed_model
    stats = EmbedStats()
    if not model:
        return stats
    con.execute("DELETE FROM embed_stale WHERE image_id NOT IN (SELECT id FROM image)")
    con.commit()
    todo = pending(con, model)
    stats.pending = len(todo)
    client = Ollama(cfg, timeout=120)
    in_a_row, checked = 0, False
    for start in range(0, len(todo), BATCH):
        if stop is not None and stop():
            stats.stopped = True
            break
        ids = todo[start:start + BATCH]
        texts = {i: text_of(con, i) for i in ids}
        known = dict(con.execute("""SELECT image_id, text_sha FROM embedding WHERE model = ?
                                    AND image_id IN (SELECT value FROM json_each(?))""", (model, json.dumps(ids))))
        ask = [i for i in ids if texts[i] and known.get(i) != _sha(texts[i])]
        vectors = {}
        if ask:
            try:
                got = client.embed([_prefix(model, False) + texts[i] for i in ask])
                vectors = {i: _unit(v) for i, v in zip(ask, got)}
                in_a_row = 0
            except (OllamaError, httpx.HTTPError, ValueError) as e:
                stats.last_error = f"{type(e).__name__}: {e}" if not isinstance(e, OllamaError) else str(e)
                if isinstance(e, OllamaError) and "not found" in str(e).lower():
                    stats.missing = str(e)
                    break
                in_a_row += 1
                if not isinstance(e, httpx.HTTPError):  # an error for these: tried again later, the rest go on
                    for i in ask:
                        retry.later(con, "meaning", i, stats.last_error)
                    stats.failed += len(ask)
                if in_a_row >= GIVE_UP_AFTER or isinstance(e, httpx.HTTPError):
                    stats.gave_up = True
                    break
                continue
        size = next((4 * len(v) for v in vectors.values() if len(v)), None)
        if size and not checked:  # the model under this name was replaced by one whose vectors are of another length
            # (pulled again, a custom one): the old can't be compared with the new, so every photo is read again
            con.execute("DELETE FROM embedding WHERE model = ? AND length(vector) != ?", (model, size))
            checked = True
        _save(con, model, ids, texts, vectors)
        stats.done += len(vectors)
        stats.unchanged += len(ids) - len(vectors)
    return stats


def _unit(values) -> np.ndarray:
    v = np.asarray(values, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n else v


def _save(con: sqlite3.Connection, model: str, ids: list[int], texts: dict, vectors: dict) -> None:
    """One batch, in one short write: the new vectors, those with no words left gone, and all of them off the list
    of what changed (with every copy in their groups, whose captions count for them)."""
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    for i, v in vectors.items():
        con.execute("INSERT OR REPLACE INTO embedding(image_id, model, text_sha, vector, made_at) VALUES (?, ?, ?, ?, ?)",
                    (i, model, _sha(texts[i]), v.astype(np.float32).tobytes(), now))
    gone = [i for i in ids if not texts[i]]
    listed = json.dumps(ids)
    con.execute("DELETE FROM embedding WHERE image_id IN (SELECT value FROM json_each(?))", (json.dumps(gone),))
    con.execute("""DELETE FROM embed_stale WHERE image_id IN (SELECT value FROM json_each(:ids))
                   OR image_id IN (SELECT id FROM image WHERE dup_group_id IN (SELECT value FROM json_each(:ids)))""",
                {"ids": listed})
    retry.done(con, "meaning", ids)
    con.commit()


# ---- every photo's meaning, kept in memory ---------------------------------------------------------------------------

class _Index:
    """The vectors of one catalog and model: rows of a matrix grown in place, so the worker's new ones are added
    without reading the rest again."""

    def __init__(self):
        self.ids = np.empty(0, dtype=np.int64)
        self.vectors = np.empty((0, 0), dtype=np.float32)
        self.pos: dict[int, int] = {}
        self.n = 0
        self.latest = ""
        self.looked = -1e9

    def refresh(self, con: sqlite3.Connection, model: str) -> None:
        self.looked = time.monotonic()
        count, latest = con.execute("SELECT count(*), max(made_at) FROM main.embedding WHERE model = ?",
                                    (model,)).fetchone()
        if count == self.n and (latest or "") == self.latest:
            return
        if count < self.n:  # some went (photos gone, words taken away): read them all again
            self.__init__()
            self.looked = time.monotonic()
        for image_id, blob, made in con.execute("""SELECT image_id, vector, made_at FROM main.embedding
                                                   WHERE model = ? AND made_at >= ?""", (model, self.latest)):
            v = np.frombuffer(blob, dtype=np.float32)
            if not len(v) or self.vectors.shape[1] not in (0, len(v)):
                continue  # another model's length under this one's name: made again on the worker's next pass
            p = self.pos.get(image_id)
            if p is None:
                p = self._grow(len(v), count)
                self.pos[image_id] = p
                self.ids[p] = image_id
            self.vectors[p] = v
            self.latest = max(self.latest, made)

    def _grow(self, dims: int, want: int = 0) -> int:
        """A row for one more vector. The matrix is made as large as the catalog has vectors (want) when they are
        read, then grown by a quarter: 40,000 photos' at 768 numbers are 123 MB, and doubling would hold twice that
        on a small board."""
        if self.n == len(self.ids):
            size = max(1024, self.n + self.n // 4, want)
            ids, vectors = np.zeros(size, dtype=np.int64), np.zeros((size, dims), dtype=np.float32)
            if self.n:
                ids[:self.n], vectors[:self.n] = self.ids[:self.n], self.vectors[:self.n]
            self.ids, self.vectors = ids, vectors
        self.n += 1
        return self.n - 1

    def scores(self, vector: np.ndarray) -> np.ndarray:
        return self.vectors[:self.n] @ vector if self.n and len(vector) == self.vectors.shape[1] else np.empty(0)


_INDEXES: dict[tuple[str, str], _Index] = {}
_LOCK = threading.Lock()


def _index(con: sqlite3.Connection, model: str) -> _Index:
    where = next((r[2] for r in con.execute("PRAGMA database_list") if r[1] == "main"), "")
    with _LOCK:
        for old in [k for k in _INDEXES if k[0] == where and k[1] != model]:  # the model was changed: its vectors
            del _INDEXES[old]  # go from memory (with a few hundred MB for a big library, two would not fit)
        ix = _INDEXES.setdefault((where, model), _Index())
        if time.monotonic() - ix.looked >= LOOK_EVERY:
            ix.refresh(con, model)
        return ix


def forget() -> None:
    """Drop what is kept in memory (tests: a catalog made again under the same name)."""
    with _LOCK:
        _INDEXES.clear()
        _ASKED.clear()
        _AWAY.clear()


# ---- a search's meaning --------------------------------------------------------------------------------------------

_ASKED: OrderedDict = OrderedDict()   # (address, model, words) -> vector
_AWAY: dict = {}                      # (address, model) -> until when (time.monotonic) not to ask
_CLIENTS: dict = {}                   # (address, model, keep alive) -> Ollama


def asker(cfg: Config) -> Callable[[str], Query | None] | None:
    """What reads a search's words for search.query (meaning=), or None when search by meaning is off: AI switched
    off, or no model for it."""
    if not (cfg.describe_enabled and cfg.ollama_embed_model):
        return None
    return lambda words: query_vector(cfg, words)


def query_vector(cfg: Config, words: str) -> Query | None:
    """The meaning of a search's words, or None when Ollama can't say in time (away, slow, without the model)."""
    model = cfg.ollama_embed_model
    words = " ".join(words.lower().split())[:500]
    if not (model and words):
        return None
    key, where = (cfg.ollama_url, model, words), (cfg.ollama_url, model)
    with _LOCK:
        if key in _ASKED:
            _ASKED.move_to_end(key)
            return Query(model, _ASKED[key])
        if _AWAY.get(where, 0) > time.monotonic():
            return None
        client = _CLIENTS.get((*where, cfg.ollama_keep_alive))
        if client is None:
            client = _CLIENTS[(*where, cfg.ollama_keep_alive)] = Ollama(cfg, timeout=ASK_FOR)
    try:
        vector = _unit(client.embed([_prefix(model, True) + words], timeout=httpx.Timeout(ASK_FOR, connect=1.0))[0])
    except (OllamaError, httpx.HTTPError, ValueError, IndexError):
        with _LOCK:
            _AWAY[where] = time.monotonic() + AWAY_FOR
        return None
    with _LOCK:
        _ASKED[key] = vector
        while len(_ASKED) > KEPT_WORDS:
            _ASKED.popitem(last=False)
    return Query(model, vector)


def nearest(con: sqlite3.Connection, q: Query, most: int = MOST) -> list[tuple[int, float]]:
    """The photos whose meaning is closest to a search's, closest first, as (id, likeness), those close enough only
    (floor). Of every photo: the caller keeps those the viewer sees and the filters allow."""
    ix = _index(con, q.model)
    scores = ix.scores(q.vector)
    if not len(scores):
        return []
    k = min(most, len(scores))
    top = np.argpartition(-scores, k - 1)[:k]
    top = top[np.argsort(-scores[top], kind="stable")]
    least = floor(q.model, float(scores[top[0]]))
    return [(int(ix.ids[i]), float(scores[i])) for i in top if scores[i] >= least]


def like_this(con: sqlite3.Connection, cfg: Config, image_id: int, most: int = LIKE) -> list[sqlite3.Row]:
    """The photos whose descriptions are closest to this one's ("More like this"), from other days as much as there
    are (LIKE_PER_DAY a day at most), of those the connection sees. Empty while search by meaning is off, or this
    photo has no vector yet."""
    model = cfg.ollama_embed_model
    row = con.execute(f"SELECT coalesce(i.dup_group_id, i.id) FROM image i WHERE i.id = ? AND {catalog.VISIBLE}",
                      (image_id,)).fetchone()
    if not (model and cfg.describe_enabled and row):
        return []
    ix = _index(con, model)
    p = ix.pos.get(row[0])
    if p is None:
        return []
    scores = ix.scores(ix.vectors[p])
    k = min(most * 10, len(scores))
    top = np.argpartition(-scores, k - 1)[:k]
    top = top[np.argsort(-scores[top], kind="stable")]
    least = floor(model, 1.0)  # as close as a search's match needs to be (the photo itself is the best: 1.0)
    ids = [int(ix.ids[i]) for i in top if ix.ids[i] != row[0] and scores[i] >= least]
    if not ids:
        return []
    found = {r["id"]: r for r in con.execute(f"""
        SELECT i.id, i.sha256, i.taken_at, i.taken_at_source, i.media, i.duration FROM image i
        WHERE i.id IN (SELECT value FROM json_each(?)) AND (i.dup_group_id IS NULL OR i.dup_group_id = i.id)
          AND {catalog.VISIBLE}""", (json.dumps(ids),))}
    out, days = [], {}
    for i in ids:
        r = found.get(i)
        day = (r["taken_at"] or "")[:10] if r else ""
        if r is None or (day and days.get(day, 0) >= LIKE_PER_DAY):
            continue
        days[day] = days.get(day, 0) + 1
        out.append(r)
        if len(out) == most:
            break
    return out
