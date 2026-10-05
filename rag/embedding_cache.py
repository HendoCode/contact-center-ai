"""On-disk document embedding cache shared by `make ingest`, `make bench` and the Lance check.

One JSONL file per embedding model under `.cache/embeddings/`, each line
`{"h": sha256(text), "v": vector}`, so a rerun embeds only text it has not seen. On a CPU
the 1,250 transcripts take minutes through Ollama; from the cache they take a second.
Files written by older versions under `.cache/bench-embeddings/` are still read, and new
vectors go to `.cache/embeddings/`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO_ROOT / ".cache" / "embeddings"
LEGACY_CACHE_DIR = REPO_ROOT / ".cache" / "bench-embeddings"


def embedding_model(embeddings) -> str:
    """`provider:model`, the cache key for an embedding model."""
    provider = os.getenv("EMBEDDING_PROVIDER", os.getenv("LLM_PROVIDER", "openai")).lower()
    model = getattr(embeddings, "model", None) or type(embeddings).__name__
    return f"{provider}:{model}"


def cache_file_for(embeddings) -> Path:
    """The on-disk embedding cache for this model; every caller shares it."""
    return CACHE_DIR / (re.sub(r"[^A-Za-z0-9_.-]+", "_", embedding_model(embeddings)) + ".jsonl")


def _text_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def embed_progress(label: str = "bench") -> Callable[[int, int], None]:
    """A `warm_documents` progress callback: a notice up front, then a line per ~10%."""
    last = 0

    def report(done: int, total: int) -> None:
        nonlocal last
        step = max(1, total // 10)
        if done == 0:
            print(f"{label}: embedding {total} unique docs once up front; on a CPU this can "
                  "take several minutes", flush=True)
        elif done == total or done // step > last // step:
            print(f"  embedded {done}/{total}", flush=True)
        last = done

    return report


class CachedEmbeddings:
    """Embed each unique text once; later calls (ingest, search) hit the cache.

    With `cache_file`, document vectors also persist on disk (JSONL keyed by text hash, one
    file per embedding model), so a rerun skips the slow CPU embedding step. A file of the
    same name under the legacy directory is read too.
    """

    def __init__(self, inner, cache_file: Path | None = None, label: str = "bench"):
        self.inner = inner
        self.label = label
        self._docs: dict[str, list[float]] = {}
        self._queries: dict[str, list[float]] = {}
        self.cache_file = cache_file
        self._disk: dict[str, list[float]] = {}
        self._sources: list[Path] = []
        if cache_file:
            for path in (LEGACY_CACHE_DIR / cache_file.name, cache_file):
                if path.exists() and path not in self._sources:
                    self._sources.append(path)
                    for line in path.read_text().splitlines():
                        row = json.loads(line)
                        self._disk[row["h"]] = row["v"]

    def warm_documents(self, texts: Iterable[str], batch: int = 64,
                       progress: Callable[[int, int], None] | None = None) -> tuple[int, float]:
        """Embed every uncached text; `progress(done, total)` runs at 0 and after each batch."""
        todo = [t for t in dict.fromkeys(texts) if t not in self._docs]
        hits = [t for t in todo if _text_key(t) in self._disk]
        for t in hits:
            self._docs[t] = self._disk[_text_key(t)]
        if hits:
            where = " and ".join(str(p) for p in self._sources)
            print(f"{self.label}: {len(hits)} document embeddings loaded from {where}", flush=True)
        todo = [t for t in todo if t not in self._docs]
        if progress and todo:
            progress(0, len(todo))
        t0 = time.perf_counter()
        for i in range(0, len(todo), batch):
            chunk = todo[i : i + batch]
            vectors = self.inner.embed_documents(chunk)
            self._docs.update(zip(chunk, vectors))
            if self.cache_file:
                self.cache_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self.cache_file, "a") as fh:
                    for t, v in zip(chunk, vectors):
                        fh.write(json.dumps({"h": _text_key(t), "v": list(v)}) + "\n")
            if progress:
                progress(i + len(chunk), len(todo))
        return len(todo), time.perf_counter() - t0

    def warm_queries(self, texts: Iterable[str]) -> list[float]:
        """Embed each query text; returns per-query seconds."""
        times = []
        for t in dict.fromkeys(texts):
            t0 = time.perf_counter()
            self._queries[t] = self.inner.embed_query(t)
            times.append(time.perf_counter() - t0)
        return times

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        missing = [t for t in texts if t not in self._docs]
        if missing:
            self.warm_documents(missing)
        return [self._docs[t] for t in texts]

    def embed_query(self, text: str) -> list[float]:
        if text not in self._queries:
            self._queries[text] = self.inner.embed_query(text)
        return self._queries[text]
