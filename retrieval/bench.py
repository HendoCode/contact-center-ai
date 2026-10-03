"""
R3 retrieval benchmark: every labeled query through every backend and search mode.

    make bench                                   # both backends, all modes, 1,250 calls
    make bench ARGS="--backends lancedb"         # no Postgres needed
    make bench ARGS="--scale 80"                 # synthetic scale-up to 100,000 rows

Writes `results/retrieval/<date>_<run>.json` (§4.3), re-renders
`results/retrieval/README.md`, and prints this run's table.

Method
------
* Corpus: `transcripts.json` through the same `transcripts_to_documents()` that
  `make ingest` uses. Labels: `retrieval/labels/queries.jsonl` (see its README).
* Isolation: each backend writes to a fresh bench-only store (pgvector collection
  `bench_call_transcripts`, dropped afterwards; LanceDB in a temporary directory), never
  the `make ingest` store.
* Embeddings: one `get_embeddings()` model shared by both backends (§4.1). Every unique
  document and query text is embedded once up front and cached (`embed_*` metrics), so
  `ingest_s` and query latency measure the store, not the embedding model.
* `ingest_s`: wall time of one `Retriever.ingest(docs)` call on an empty store.
  `index_build_s`: the part of it spent building indexes. LanceDB builds its FTS and
  `call_id` indexes always and IVF-PQ at >= 100,000 rows; the pgvector backend builds no
  vector index (exact scan), so it reports n/a.
* Quality: each query is searched once at k=10 with its `where`. recall@k is
  |relevant in top k| / min(k, |relevant|) (capped recall, as in BEIR's R_cap), since most
  relevant sets are larger than k; recall@5 uses the first five hits of the same list.
  MRR@10 is the reciprocal rank of the first relevant hit (0 if none in the top 10).
* Latency: `search()` wall time per query, after one warm-up query, over `--repeats`
  passes; p50/p95 by linear interpolation.
* `--scale N` replicates the corpus N times with new call_ids (`CALL-00001~r001`); each
  query's relevant set gains the replicas of its relevant calls. Replicas share text and
  vectors, so this measures index and scan behaviour at size, not retrieval quality.
"""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from datetime import date
from importlib import metadata
from pathlib import Path

from retrieval.base import Retriever

REPO_ROOT = Path(__file__).resolve().parent.parent
LABELS_PATH = REPO_ROOT / "retrieval" / "labels" / "queries.jsonl"
RESULTS_ROOT = REPO_ROOT / "results"
BACKENDS = ("pgvector", "lancedb")
# Rows per Retriever.ingest() call. 12,500 is the largest single insert proven to work (x10);
# pgvector's add_texts sends a whole batch as ONE multi-row INSERT, so x80's 100,000 rows in
# one call is the prime suspect for the x80 failure.
INGEST_BATCH = 12_500
ERROR_CHARS = 300
CACHE_DIR = REPO_ROOT / ".cache" / "bench-embeddings"
MODES = ("vector", "fts", "hybrid")
QUERY_TYPES = ("topical", "filter", "exact", "paraphrase")
K = 10
BENCH_COLLECTION = "bench_call_transcripts"
REPLICA_SEP = "~r"


# ── metrics ───────────────────────────────────────────────────────────────────

def recall_at_k(ranked: list[str], relevant: Iterable[str], k: int) -> float:
    """Capped recall: |relevant ∩ top-k| / min(k, |relevant|)."""
    relevant = set(relevant)
    if not relevant:
        raise ValueError("relevant set is empty")
    return len(relevant.intersection(ranked[:k])) / min(k, len(relevant))


def reciprocal_rank(ranked: list[str], relevant: Iterable[str], k: int = K) -> float:
    relevant = set(relevant)
    return next((1.0 / i for i, cid in enumerate(ranked[:k], 1) if cid in relevant), 0.0)


def percentile(values: list[float], q: float) -> float:
    """q-th percentile (0-100) with linear interpolation between order statistics."""
    if not values:
        raise ValueError("no values")
    xs = sorted(values)
    pos = (len(xs) - 1) * q / 100
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


# ── labels and corpus ─────────────────────────────────────────────────────────

def validate_query(rec: object) -> list[str]:
    """Schema errors for one labels-file record (empty when valid)."""
    if not isinstance(rec, dict):
        return ["record is not an object"]
    errors = []
    for key, typ in (("id", str), ("type", str), ("query", str), ("relevant", list),
                     ("derivation", str)):
        if not isinstance(rec.get(key), typ):
            errors.append(f"{key!r} missing or not {typ.__name__}")
    if rec.get("type") not in QUERY_TYPES:
        errors.append(f"type {rec.get('type')!r} not in {QUERY_TYPES}")
    if not (rec.get("where") is None or isinstance(rec.get("where"), dict)):
        errors.append("'where' must be null or an object")
    rel = rec.get("relevant")
    if isinstance(rel, list) and (not rel or not all(isinstance(c, str) for c in rel)
                                  or len(set(rel)) != len(rel)):
        errors.append("'relevant' must be a non-empty list of unique call_id strings")
    if isinstance(rec.get("where"), dict):
        from retrieval.base import validate_where
        try:
            validate_where(rec["where"])
        except ValueError as e:
            errors.append(f"'where' outside the portable subset: {e}")
    return errors


def load_queries(path: Path = LABELS_PATH) -> list[dict]:
    queries = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    for q in queries:
        errors = validate_query(q)
        if errors:
            raise ValueError(f"{path} {q.get('id')}: " + "; ".join(errors))
    ids = [q["id"] for q in queries]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path}: duplicate query ids")
    return queries


def load_corpus() -> list[dict]:
    """The docs `make ingest` writes: {call_id, text, metadata}."""
    from rag.pipeline import load_synthetic_data, transcripts_to_documents

    return [
        {"call_id": d.metadata["call_id"], "text": d.page_content, "metadata": d.metadata}
        for d in transcripts_to_documents(load_synthetic_data())
    ]


def scale_up(docs: list[dict], queries: list[dict], n: int) -> tuple[list[dict], list[dict]]:
    """Replicate the corpus n times with new call_ids; extend relevant sets to match."""
    if n < 1:
        raise ValueError("--scale must be >= 1")
    if n == 1:
        return docs, queries

    def rid(cid: str, r: int) -> str:
        return cid if r == 0 else f"{cid}{REPLICA_SEP}{r:03d}"

    out_docs = [
        {**d, "call_id": rid(d["call_id"], r), "metadata": {**d["metadata"],
                                                           "call_id": rid(d["call_id"], r)}}
        for r in range(n) for d in docs
    ]
    out_queries = [
        {**q, "relevant": [rid(c, r) for r in range(n) for c in q["relevant"]]} for q in queries
    ]
    return out_docs, out_queries


# ── embeddings ────────────────────────────────────────────────────────────────

def ensure_ollama_model(embeddings, client=None) -> None:
    """Pull the Ollama embedding model when the server lacks it; a no-op otherwise.

    Only `make demo` pulls models (the Compose `ollama-pull` service), so after a bare
    `make up` the first embed call would 404. Other embedding providers are skipped.
    """
    if type(embeddings).__name__ != "OllamaEmbeddings":
        return
    if client is None:
        import ollama

        client = ollama.Client(host=embeddings.base_url)
    model = embeddings.model
    tagged = model if ":" in model else f"{model}:latest"
    if tagged in {m.model for m in client.list().models}:
        return
    print(f"bench: pulling Ollama model {model} (one-time download; nomic-embed-text is "
          "~270 MB)", flush=True)
    client.pull(model)


def embed_progress() -> Callable[[int, int], None]:
    """A `warm_documents` progress callback: a notice up front, then a line per ~10%."""
    last = 0

    def report(done: int, total: int) -> None:
        nonlocal last
        step = max(1, total // 10)
        if done == 0:
            print(f"bench: embedding {total} unique docs once up front; on a CPU this can "
                  "take several minutes", flush=True)
        elif done == total or done // step > last // step:
            print(f"  embedded {done}/{total}", flush=True)
        last = done

    return report


def short_error(exc: BaseException, limit: int = ERROR_CHARS) -> str:
    """`Class: message` on one line, at most `limit` characters: never a driver's payload dump."""
    text = " ".join(f"{type(exc).__name__}: {exc}".split())
    return text if len(text) <= limit else text[: limit - 15] + " ...[truncated]"


def _text_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class CachedEmbeddings:
    """Embed each unique text once; later calls (ingest, search) hit the cache.

    With `cache_file`, document vectors also persist on disk (JSONL keyed by text hash, one
    file per embedding model), so a rerun skips the slow CPU embedding step.
    """

    def __init__(self, inner, cache_file: Path | None = None):
        self.inner = inner
        self._docs: dict[str, list[float]] = {}
        self._queries: dict[str, list[float]] = {}
        self.cache_file = cache_file
        self._disk: dict[str, list[float]] = {}
        if cache_file and cache_file.exists():
            for line in cache_file.read_text().splitlines():
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
            print(f"bench: {len(hits)} document embeddings loaded from {self.cache_file}", flush=True)
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


# ── backends ──────────────────────────────────────────────────────────────────

class Store:
    """A fresh bench-only backend: the retriever, its index-build timer, cleanup."""

    def __init__(self, retriever: Retriever, cleanup: Callable[[], None] = lambda: None):
        self.retriever = retriever
        self.cleanup = cleanup
        self.index_build_s: float | None = None


def open_lancedb(embeddings, workdir: Path) -> Store:
    from retrieval.lancedb_backend import LanceDBRetriever

    path = Path(tempfile.mkdtemp(prefix="lance-", dir=workdir))
    retriever = LanceDBRetriever(uri=str(path), embeddings=embeddings, table_name="bench")
    store = Store(retriever, cleanup=lambda: shutil.rmtree(path, ignore_errors=True))

    # Time the index build inside ingest() without changing the backend.
    build = retriever._ensure_indexes

    def timed_build(table):
        t0 = time.perf_counter()
        build(table)
        store.index_build_s = (store.index_build_s or 0.0) + time.perf_counter() - t0

    retriever._ensure_indexes = timed_build
    return store


def open_pgvector(embeddings, workdir: Path) -> Store:
    from langchain_postgres import PGVector

    from rag.embeddings import CONNECTION_STRING
    from retrieval.pgvector_backend import PgVectorRetriever

    pg = PGVector(embeddings=embeddings, collection_name=BENCH_COLLECTION,
                  connection=CONNECTION_STRING, use_jsonb=True, pre_delete_collection=True)
    return Store(PgVectorRetriever(store=pg), cleanup=pg.delete_collection)


OPENERS: dict[str, Callable] = {"pgvector": open_pgvector, "lancedb": open_lancedb}


# ── run ───────────────────────────────────────────────────────────────────────

def bench_backend(store: Store, docs: list[dict], queries: list[dict], modes: Iterable[str],
                  repeats: int = 3, ingest_batch: int = INGEST_BATCH) -> dict[str, dict]:
    """Ingest into an empty store in batches, then score and time every query in every mode.

    A failing mode becomes a short `failed: ...` status row; the other modes still run.
    """
    r = store.retriever
    batches = [docs[i : i + ingest_batch] for i in range(0, len(docs), ingest_batch)]
    t0 = time.perf_counter()
    written = 0
    for n, batch in enumerate(batches, 1):
        print(f"bench: ingest {r.name}: batch {n}/{len(batches)} ({len(batch)} rows)", flush=True)
        written += r.ingest(batch)
    ingest_s = time.perf_counter() - t0
    if store.index_build_s is not None:
        print(f"bench: {r.name} index build {store.index_build_s:.2f}s (inside ingest)", flush=True)
    if written != len({d["call_id"] for d in docs}):
        raise RuntimeError(f"{r.name}: ingest wrote {written} rows for {len(docs)} docs")

    rows: dict[str, dict] = {}
    for mode in modes:
        name = f"{r.name}/{mode}"
        try:
            r.search(queries[0]["query"], k=K, where=queries[0]["where"], mode=mode)  # warm-up
        except NotImplementedError as e:
            rows[name] = {"status": f"not supported: {e}"}
            continue
        except Exception as e:  # noqa: BLE001 - recorded, not raised: keep the other modes
            rows[name] = {"status": f"failed: {short_error(e)}"}
            print(f"bench: {name} FAILED: {short_error(e)}", flush=True)
            continue
        print(f"bench: queries {name} ({len(queries)} x {repeats})", flush=True)
        try:
            rows[name] = _score_mode(r, mode, queries, repeats, ingest_s, store.index_build_s)
        except Exception as e:  # noqa: BLE001
            rows[name] = {"status": f"failed: {short_error(e)}"}
            print(f"bench: {name} FAILED: {short_error(e)}", flush=True)
    return rows


def _score_mode(r, mode: str, queries: list[dict], repeats: int, ingest_s: float,
                index_build_s: float | None) -> dict:
    """recall@5/10, MRR@10 and p50/p95 latency for one mode."""
    ranked, latencies = {}, []
    for _ in range(repeats):
        for q in queries:
            t = time.perf_counter()
            hits = r.search(q["query"], k=K, where=q["where"], mode=mode)
            latencies.append((time.perf_counter() - t) * 1000)
            ranked.setdefault(q["id"], [h.call_id for h in hits])

    scores = {
        q["id"]: (recall_at_k(ranked[q["id"]], q["relevant"], 5),
                  recall_at_k(ranked[q["id"]], q["relevant"], 10),
                  reciprocal_rank(ranked[q["id"]], q["relevant"]))
        for q in queries
    }

    def mean(i: int, qs: list[dict], scores: dict = scores) -> float:
        return statistics.fmean(scores[q["id"]][i] for q in qs)

    by_type = {t: [q for q in queries if q["type"] == t] for t in QUERY_TYPES}
    by_type = {t: qs for t, qs in by_type.items() if qs}
    return {
        "status": "ok",
        "recall@5": mean(0, queries),
        "recall@10": mean(1, queries),
        "mrr@10": mean(2, queries),
        "p50_ms": percentile(latencies, 50),
        "p95_ms": percentile(latencies, 95),
        "ingest_s": ingest_s,
        "index_build_s": index_build_s,
        "recall@10_by_type": {t: mean(1, qs) for t, qs in by_type.items()},
        "mrr@10_by_type": {t: mean(2, qs) for t, qs in by_type.items()},
    }


def run_bench(docs: list[dict], queries: list[dict], embeddings, *,
              backends: Iterable[str] = BACKENDS, modes: Iterable[str] = MODES,
              repeats: int = 3, openers: dict[str, Callable] | None = None,
              workdir: Path | None = None, ingest_batch: int = INGEST_BATCH,
              cache_file: Path | None = None) -> dict:
    """Run the benchmark; returns the `metrics` block of the results record.

    A backend that fails (open, ingest or anything else) gets a short `failed: ...` row per
    mode, and the other backends still run, so a partial run still yields a results file.
    """
    openers = openers or OPENERS
    modes = list(modes)
    cached = CachedEmbeddings(embeddings, cache_file=cache_file)
    n_unique, embed_s = cached.warm_documents((d["text"] for d in docs),
                                              progress=embed_progress())
    q_times = cached.warm_queries(q["query"] for q in queries)

    metrics: dict = {
        "embed_unique_docs": n_unique,
        "embed_docs_s": embed_s,
        "embed_query_p50_ms": percentile(q_times, 50) * 1000,
    }
    with tempfile.TemporaryDirectory(prefix="ccai-bench-", dir=workdir) as tmp:
        for name in backends:
            store = None
            try:
                store = openers[name](cached, Path(tmp))
                metrics.update(bench_backend(store, docs, queries, modes, repeats, ingest_batch))
            except Exception as e:  # noqa: BLE001 - recorded, not raised: keep the other backends
                print(f"bench: {name} FAILED: {short_error(e)}", flush=True)
                for mode in modes:
                    metrics.setdefault(f"{name}/{mode}", {"status": f"failed: {short_error(e)}"})
            finally:
                if store is not None:
                    try:
                        store.cleanup()
                    except Exception as e:  # noqa: BLE001
                        print(f"bench: {name} cleanup failed: {short_error(e)}", flush=True)
    return metrics


# ── provenance ────────────────────────────────────────────────────────────────

def _version(pkg: str) -> str:
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return "not installed"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True).stdout


def git_sha() -> str:
    sha = _git("rev-parse", "HEAD").strip() or "unknown"
    dirty = _git("status", "--porcelain", "--untracked-files=no").strip()
    return f"{sha}-dirty" if dirty else sha


def detect_hardware() -> str:
    cpu = platform.machine()
    try:
        if sys.platform == "darwin":
            cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                 capture_output=True, text=True).stdout.strip() or cpu
        elif Path("/proc/cpuinfo").exists():
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
        ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
        return f"{cpu}, {os.cpu_count()} cores, {ram:.0f} GB RAM, {platform.system()}"
    except (OSError, ValueError):
        return f"{cpu}, {platform.system()}"


def postgres_versions() -> dict[str, str]:
    import psycopg2

    from rag.embeddings import CONNECTION_STRING

    with psycopg2.connect(CONNECTION_STRING) as conn, conn.cursor() as cur:
        cur.execute("SHOW server_version")
        server = cur.fetchone()[0]
        cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ext = cur.fetchone()
    return {"postgres": server, "pgvector_ext": ext[0] if ext else "not installed"}


def embedding_model(embeddings) -> str:
    provider = os.getenv("EMBEDDING_PROVIDER", os.getenv("LLM_PROVIDER", "openai")).lower()
    model = getattr(embeddings, "model", None) or type(embeddings).__name__
    return f"{provider}:{model}"


def build_record(metrics: dict, *, run: str, hardware: str, versions: dict, params: dict,
                 notes: str) -> dict:
    return {
        "date": date.today().isoformat(),
        "git_sha": git_sha(),
        "area": "retrieval",
        "run": run,
        "hardware": hardware,
        "versions": versions,
        "params": params,
        "metrics": metrics,
        "notes": notes,
    }


def write_record(record: dict, root: Path | None = None) -> Path:
    from tools.results import validate

    errors = validate(record)
    if errors:
        raise ValueError("results record violates §4.3: " + "; ".join(errors))
    out = Path(root or RESULTS_ROOT) / "retrieval" / f"{record['date']}_{record['run']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2) + "\n")
    return out


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="R3 retrieval benchmark (see module docstring)")
    parser.add_argument("--backends", default=",".join(BACKENDS),
                        help="comma-separated: pgvector,lancedb")
    parser.add_argument("--modes", default=",".join(MODES), help="comma-separated: vector,fts,hybrid")
    parser.add_argument("--scale", type=int, default=1,
                        help="replicate the corpus N times with new ids (synthetic scale-up)")
    parser.add_argument("--repeats", type=int, default=3, help="latency passes per query")
    parser.add_argument("--run", help="run name (default bench-<backends>-x<scale>)")
    parser.add_argument("--hardware", help="hardware description (default: auto-detected)")
    parser.add_argument("--labels", type=Path, help="labels file (default: committed queries.jsonl)")
    parser.add_argument("--ingest-batch", type=int, default=INGEST_BATCH,
                        help=f"rows per ingest call (default {INGEST_BATCH:,})")
    parser.add_argument("--no-cache", action="store_true",
                        help="do not read or write the embedding cache (.cache/bench-embeddings/)")
    args = parser.parse_args(argv)
    if args.ingest_batch < 1:
        parser.error("--ingest-batch must be at least 1")
    backends = [b.strip() for b in args.backends.split(",") if b.strip()]
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    for b in backends:
        if b not in BACKENDS:
            parser.error(f"unknown backend {b!r}")
    for m in modes:
        if m not in MODES:
            parser.error(f"unknown mode {m!r}")
    try:
        return _main(args, backends, modes)
    except Exception as e:  # noqa: BLE001 - one short line, never a payload dump
        print(f"bench: FAILED: {short_error(e)}", file=sys.stderr)
        return 1


def _main(args: argparse.Namespace, backends: list[str], modes: list[str]) -> int:

    from rag.embeddings import get_embeddings  # importing rag loads .env
    from tools.results import metrics_tables, render

    labels = args.labels or LABELS_PATH
    labels_text = labels.read_text()
    queries = load_queries(labels)
    docs, queries = scale_up(load_corpus(), queries, args.scale)
    embeddings = get_embeddings()
    ensure_ollama_model(embeddings)
    print(f"bench: {len(docs)} docs, {len(queries)} queries, backends={backends}, modes={modes}")

    model = embedding_model(embeddings)
    cache_file = None if args.no_cache else CACHE_DIR / (re.sub(r"[^A-Za-z0-9_.-]+", "_", model) + ".jsonl")
    metrics = run_bench(docs, queries, embeddings, backends=backends, modes=modes,
                        repeats=args.repeats, ingest_batch=args.ingest_batch, cache_file=cache_file)

    versions = {"python": platform.python_version(), "model": embedding_model(embeddings)}
    for pkg in ("lancedb", "pyarrow", "pylance", "langchain-postgres", "psycopg2-binary"):
        versions[pkg] = _version(pkg)
    if "pgvector" in backends:
        try:
            versions.update(postgres_versions())
        except Exception as e:  # noqa: BLE001 - never lose a finished run to a version lookup
            versions["postgres"] = f"unknown ({short_error(e, 120)})"
    params = {
        "backends": backends, "modes": modes, "k": K, "repeats": args.repeats,
        "scale": args.scale, "rows": len(docs), "queries": len(queries), "ingest_batch": args.ingest_batch,
        "labels_sha256": hashlib.sha256(labels_text.encode()).hexdigest()[:16],
    }
    notes = ("recall@k is capped recall, |rel ∩ top-k| / min(k, |rel|). Embeddings are "
             "precomputed and cached, so ingest_s and latency exclude the embedding model. "
             "pgvector builds no vector index (exact scan), so index_build_s is n/a.")
    if args.scale > 1:
        notes += (f" SYNTHETIC SCALE-UP: the {len(docs) // args.scale}-call corpus replicated "
                  f"{args.scale}x with new call_ids; replicas share text and vectors.")
    run = args.run or f"bench-{'-'.join(backends)}-x{args.scale}"
    record = build_record(metrics, run=run, hardware=args.hardware or detect_hardware(),
                          versions=versions, params=params, notes=notes)
    out = write_record(record)
    readme = render("retrieval", out.parent.parent)
    print(f"\nwrote {out} and {readme}\n")
    print(metrics_tables(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
