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
import contextlib
import functools
import hashlib
import json
import math
import os
import platform
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

from rag.embedding_cache import CACHE_DIR, CachedEmbeddings, cache_file_for, embed_progress, embedding_model  # noqa: F401
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

def short_error(exc: BaseException, limit: int = ERROR_CHARS) -> str:
    """`Class: message` on one line, at most `limit` characters: never a driver's payload dump."""
    text = " ".join(f"{type(exc).__name__}: {exc}".split())
    return text if len(text) <= limit else text[: limit - 15] + " ...[truncated]"


JITTER_EPS = 0.05


def jitter(vec: list[float], text: str, n: int, eps: float = JITTER_EPS) -> list[float]:
    """`vec` (normalised) plus seeded Gaussian noise of norm ~eps, renormalised.

    The seed is sha256(n, text), so a replica's vector is the same on every run and backend.
    At eps=0.05 the cosine to the original is ~0.999: replicas stay near-duplicates (the
    ground truth is still all of them) but are no longer identical points for k-means/PQ.
    """
    import numpy as np

    seed = int.from_bytes(hashlib.sha256(f"{n}\0{text}".encode()).digest()[:8], "big")
    v = np.asarray(vec, dtype=np.float64)
    v = v / (np.linalg.norm(v) or 1.0)
    v = v + eps * np.random.default_rng(seed).standard_normal(v.shape[0]) / math.sqrt(v.shape[0])
    return (v / np.linalg.norm(v)).tolist()


class JitteredEmbeddings:
    """Scale-up replicas repeat the same text; the n-th repeat of a text (n >= 1) gets
    `jitter(vec, text, n)` so --scale does not feed the vector index identical points.
    The first occurrence and every query embedding are unchanged. One instance per backend,
    so both backends see the same vectors."""

    def __init__(self, inner, eps: float = JITTER_EPS):
        self.inner, self.eps, self._seen = inner, eps, {}

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t, v in zip(texts, self.inner.embed_documents(texts)):
            n = self._seen.get(t, 0)
            self._seen[t] = n + 1
            out.append(v if n == 0 else jitter(v, t, n, self.eps))
        return out

    def embed_query(self, text: str) -> list[float]:
        return self.inner.embed_query(text)


SWEEP_KEYS = ("nprobes", "refine_factor", "ef_search")  # the first two: LanceDB; ef_search: pgvector HNSW


def parse_sweep(specs: list[str]) -> tuple[dict[str, int], ...]:
    """`["nprobes=20,50", "refine_factor=1,20"]` -> the grid of combinations, in order."""
    axes: dict[str, list[int]] = {}
    for spec in specs:
        key, _, values = spec.partition("=")
        key = key.strip()
        try:
            vals = [int(v) for v in values.split(",") if v.strip()]
        except ValueError:
            vals = []
        if key not in SWEEP_KEYS or not vals or min(vals) < 1 or key in axes:
            raise ValueError(f"--sweep {spec!r}: use {' or '.join(k + '=<n>,...' for k in SWEEP_KEYS)} "
                             "(positive integers, each key once)")
        axes[key] = vals
    combos: list[dict[str, int]] = [{}]
    for key, vals in axes.items():
        combos = [{**c, key: v} for c in combos for v in vals]
    return tuple(c for c in combos if c)


def split_sweep(grid: tuple[dict[str, int], ...]) -> tuple[tuple[dict[str, int], ...], tuple[int, ...]]:
    """The --sweep grid -> (LanceDB nprobes/refine_factor combinations, pgvector ef_search values).
    The two stores are swept independently, so ef_search never multiplies the LanceDB grid."""
    lance = {tuple((k, v) for k, v in c.items() if k != "ef_search"): None for c in grid}
    efs = {c["ef_search"]: None for c in grid if "ef_search" in c}
    return tuple(dict(c) for c in lance if c), tuple(efs)


def sweep_table(metrics: dict) -> str:
    """One compact table: the LanceDB baseline, every sweep row, and the pgvector rows for reference."""
    keep = [k for k, v in metrics.items() if isinstance(v, dict) and k.endswith("/vector")
            and (k.startswith("lancedb") or k.startswith("pgvector"))]
    lines = ["| config | recall@10 | ann_recall@10 | mrr@10 | p50 ms | p95 ms |", "|---|---|---|---|---|---|"]
    for k in keep:
        r = metrics[k]
        if r.get("status") != "ok":
            lines.append(f"| {k} | {str(r.get('status', '')).split(':')[0]} | | | | |")
            continue
        ann = "n/a" if r.get("ann_recall@10") is None else f"{r['ann_recall@10']:.3f}"
        lines.append(f"| {k} | {r['recall@10']:.3f} | {ann} | {r['mrr@10']:.3f} | {r['p50_ms']:.1f} | {r['p95_ms']:.1f} |")
    return "\n".join(lines)


# ── backends ──────────────────────────────────────────────────────────────────

class Variant:
    """A second configuration scored on the same ingested data (an extra index, a knob value).

    `setup()` runs once after the base rows are scored (e.g. builds an HNSW index) and its
    wall time is reported as that variant's index_build_s.
    """

    def __init__(self, retriever, modes: Iterable[str], index: str, search: str,
                 setup: Callable[[], None] | None = None):
        self.retriever, self.modes, self.index, self.search = retriever, list(modes), index, search
        self.setup = setup


class Store:
    """A fresh bench-only backend: the retriever, its index-build timer, cleanup.

    `index` / `search` describe the vector index and search parameters for the result rows;
    a callable is evaluated after ingest (LanceDB only builds IVF-PQ at >= 100,000 rows).
    """

    def __init__(self, retriever: Retriever, cleanup: Callable[[], None] = lambda: None,
                 index: str | Callable[[], str] = "", search: str = "",
                 variants: Callable[[], list[Variant]] | None = None):
        self.retriever = retriever
        self.cleanup = cleanup
        self.index_build_s: float | None = None
        self.index, self.search, self.variants = index, search, variants


class IndexConfig:
    """Vector-index and search knobs for both stores (CLI flags; defaults = previous behaviour
    for LanceDB, plus an HNSW variant for pgvector)."""

    def __init__(self, pgvector_index: str = "hnsw", hnsw_m: int = 16, hnsw_ef_construction: int = 64,
                 hnsw_ef_search: int = 40, nprobes: int | None = None, refine_factor: int | None = None,
                 num_partitions: int | None = None, num_sub_vectors: int | None = None,
                 pgvector_parallel_workers: int | None = None,
                 sweep: tuple[dict[str, int], ...] = (), lance_index: str = "ivf_pq",
                 num_bits: int | None = None, sweep_ef_search: tuple[int, ...] = ()):
        self.pgvector_index, self.hnsw_m = pgvector_index, hnsw_m
        self.hnsw_ef_construction, self.hnsw_ef_search = hnsw_ef_construction, hnsw_ef_search
        self.nprobes, self.refine_factor, self.num_partitions = nprobes, refine_factor, num_partitions
        self.num_sub_vectors, self.pgvector_parallel_workers = num_sub_vectors, pgvector_parallel_workers
        self.sweep = sweep  # LanceDB search-knob combinations, e.g. ({"nprobes": 20, "refine_factor": 5}, ...)
        self.lance_index, self.num_bits, self.sweep_ef_search = lance_index, num_bits, sweep_ef_search

    def as_params(self) -> dict:
        return {k: v for k, v in vars(self).items() if v not in (None, ())}


def _lance_search(nprobes: int | None, refine_factor: int | None) -> str:
    return (f"nprobes={nprobes or 'default'}, refine_factor={refine_factor or 'none'}")


def open_lancedb(embeddings, workdir: Path, cfg: IndexConfig | None = None) -> Store:
    from retrieval.lancedb_backend import LanceDBRetriever

    cfg = cfg or IndexConfig()
    path = Path(tempfile.mkdtemp(prefix="lance-", dir=workdir))

    def retriever(name="lancedb", nprobes=cfg.nprobes, refine=cfg.refine_factor):
        r = LanceDBRetriever(uri=str(path), embeddings=embeddings, table_name="bench", nprobes=nprobes,
                             refine_factor=refine, num_partitions=cfg.num_partitions,
                             num_sub_vectors=cfg.num_sub_vectors, index_type=cfg.lance_index,
                             num_bits=cfg.num_bits)
        r.name = name
        return r

    base = retriever()

    def index() -> str:
        table = base._table()
        has_ivf = table is not None and "vector" in {i.columns[0] for i in table.list_indices()}
        if not has_ivf:
            return "flat (exact)"
        parts = cfg.num_partitions or int(math.sqrt(table.count_rows()))
        pq = cfg.lance_index.endswith("_pq")
        subs = f", {cfg.num_sub_vectors} sub-vectors" if pq and cfg.num_sub_vectors else ""
        bits = f", {cfg.num_bits}-bit codes" if pq and cfg.num_bits else ""
        return f"{cfg.lance_index} (cosine, {parts} partitions{subs}{bits})"

    def variants() -> list[Variant]:  # the --sweep grid, scored on the same table
        out = []
        for combo in cfg.sweep:
            n, f = combo.get("nprobes", cfg.nprobes), combo.get("refine_factor", cfg.refine_factor)
            label = ",".join(f"{k}={v}" for k, v in combo.items())
            out.append(Variant(retriever(f"lancedb[{label}]", n, f), ["vector"], index(), _lance_search(n, f)))
        return out

    store = Store(base, cleanup=lambda: shutil.rmtree(path, ignore_errors=True), index=index,
                  search=_lance_search(cfg.nprobes, cfg.refine_factor), variants=variants)

    # Time the index build inside ingest() without changing the backend.
    build = base._ensure_indexes

    def timed_build(table):
        t0 = time.perf_counter()
        build(table)
        store.index_build_s = (store.index_build_s or 0.0) + time.perf_counter() - t0

    base._ensure_indexes = timed_build
    return store


HNSW_INDEX = "ccai_bench_hnsw"
def explain_build_error(e: Exception) -> Exception:
    """A /dev/shm exhaustion during the HNSW build becomes a one-line fix; anything else is unchanged."""
    if "shared memory" in str(e) and "No space left" in str(e):
        return RuntimeError(SHM_HINT + " | " + short_error(e, 150))
    return e


SHM_HINT = ("HNSW build ran out of /dev/shm: raise the db container's shm_size (docker-compose.yml) "
            "or rerun with --pgvector-parallel-workers 0")


def hnsw_sql(dim: int, where: dict | None) -> tuple[str, list]:
    """The bench's HNSW query: same table and metric as PGVector, written so the planner can use
    the partial expression index (`embedding::vector(dim)`). Returns SQL and its leading params
    (vector, collection uuid, filter values); the caller appends the order vector and k."""
    from retrieval.base import validate_where

    ops = {"gte": ">=", "lte": "<=", "lt": "<"}
    clauses, params = [], []
    for fld, op, value in validate_where(where):
        col = "(cmetadata->>%s)"
        if op == "eq" and value is None:
            clauses.append(f"{col} IS NULL")
            params.append(fld)
        elif op == "eq":
            clauses.append(f"{col} = %s")
            params += [fld, str(value)]
        elif op == "in":
            clauses.append(f"{col} = ANY(%s)")
            params += [fld, [str(v) for v in value]]
        else:
            clauses.append(f"{col} {ops[op]} %s")
            params += [fld, str(value)]
    d = int(dim)
    sql = (f"SELECT cmetadata->>'call_id', document, cmetadata, "
           f"1 - ((embedding::vector({d})) <=> %s::vector({d})) "
           f"FROM langchain_pg_embedding WHERE collection_id = %s::uuid"
           + "".join(f" AND {c}" for c in clauses)
           + f" ORDER BY (embedding::vector({d})) <=> %s::vector({d}) LIMIT %s")
    return sql, params


class PgHnswRetriever:
    """Vector search over the bench collection through the HNSW index (bench-only)."""

    name = "pgvector-hnsw"

    def __init__(self, conn, embeddings, collection_uuid: str, dim: int):
        self.conn, self.embeddings, self.uuid, self.dim = conn, embeddings, collection_uuid, dim

    def search(self, query, k=5, where=None, mode="vector"):
        from retrieval.base import Hit

        if mode != "vector":
            raise NotImplementedError(f"pgvector-hnsw supports mode='vector' only, not {mode!r}")
        vec = "[" + ",".join(f"{x:.7g}" for x in self.embeddings.embed_query(query)) + "]"
        sql, filt = hnsw_sql(self.dim, where)
        with self.conn.cursor() as cur:
            cur.execute(sql, [vec, self.uuid, *filt, vec, k])
            return [Hit(call_id=cid, text=doc, score=float(score), metadata=meta)
                    for cid, doc, meta, score in cur.fetchall()]


def open_pgvector(embeddings, workdir: Path, cfg: IndexConfig | None = None) -> Store:
    import psycopg2
    from langchain_postgres import PGVector

    from rag.embeddings import CONNECTION_STRING
    from retrieval.pgvector_backend import PgVectorRetriever

    cfg = cfg or IndexConfig()
    pg = PGVector(embeddings=embeddings, collection_name=BENCH_COLLECTION,
                  connection=CONNECTION_STRING, use_jsonb=True, pre_delete_collection=True)
    conns = []

    def connect():
        conn = psycopg2.connect(CONNECTION_STRING)
        conn.autocommit = True
        conns.append(conn)
        return conn

    def variants() -> list[Variant]:
        if cfg.pgvector_index != "hnsw":
            return []
        conn = connect()
        with conn.cursor() as cur:
            cur.execute("SELECT uuid::text FROM langchain_pg_collection WHERE name = %s", (BENCH_COLLECTION,))
            uuid = cur.fetchone()[0]
            cur.execute("SELECT vector_dims(embedding) FROM langchain_pg_embedding "
                        "WHERE collection_id = %s::uuid LIMIT 1", (uuid,))
            dim = int(cur.fetchone()[0])

        def build():
            with conn.cursor() as cur:
                cur.execute(f"DROP INDEX IF EXISTS {HNSW_INDEX}")
                cur.execute("SET maintenance_work_mem = '512MB'")
                if cfg.pgvector_parallel_workers is not None:
                    cur.execute(f"SET max_parallel_maintenance_workers = {int(cfg.pgvector_parallel_workers)}")
                try:
                    cur.execute(
                        f"CREATE INDEX {HNSW_INDEX} ON langchain_pg_embedding USING hnsw "
                        f"((embedding::vector({dim})) vector_cosine_ops) "
                        f"WITH (m = {int(cfg.hnsw_m)}, ef_construction = {int(cfg.hnsw_ef_construction)}) "
                        "WHERE collection_id = %s::uuid", (uuid,))
                except psycopg2.Error as e:
                    raise explain_build_error(e) from None
                cur.execute(f"SET hnsw.ef_search = {int(cfg.hnsw_ef_search)}")
                # pgvector >= 0.8: keep scanning past filtered-out rows so k hits come back.
                with contextlib.suppress(psycopg2.Error):
                    cur.execute("SET hnsw.iterative_scan = relaxed_order")
                cur.execute("ANALYZE langchain_pg_embedding")

        index = f"hnsw (cosine, m={cfg.hnsw_m}, ef_construction={cfg.hnsw_ef_construction})"
        out = [Variant(PgHnswRetriever(conn, embeddings, uuid, dim), ["vector"], index,
                       f"ef_search={cfg.hnsw_ef_search}, iterative_scan=relaxed_order", build)]
        for ef in cfg.sweep_ef_search:  # same index, only the session's ef_search changes
            r = PgHnswRetriever(conn, embeddings, uuid, dim)
            r.name = f"pgvector-hnsw[ef_search={ef}]"

            def set_ef(ef=ef):
                with conn.cursor() as cur:
                    cur.execute(f"SET hnsw.ef_search = {int(ef)}")
            out.append(Variant(r, ["vector"], index, f"ef_search={ef}, iterative_scan=relaxed_order", set_ef))
        return out

    def cleanup():
        try:
            if conns:
                with conns[0].cursor() as cur:
                    cur.execute(f"DROP INDEX IF EXISTS {HNSW_INDEX}")
        finally:
            for c in conns:
                c.close()
            pg.delete_collection()

    return Store(PgVectorRetriever(store=pg), cleanup=cleanup, index="none (exact scan)",
                 search="exact", variants=variants)


OPENERS: dict[str, Callable] = {"pgvector": open_pgvector, "lancedb": open_lancedb}


# ── run ───────────────────────────────────────────────────────────────────────

def bench_backend(store: Store, docs: list[dict], queries: list[dict], modes: Iterable[str],
                  repeats: int = 3, ingest_batch: int = INGEST_BATCH,
                  reference: "ExactReference | None" = None) -> dict[str, dict]:
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

    index = store.index() if callable(store.index) else store.index
    rows = score_modes(r, queries, modes, repeats, ingest_s, store.index_build_s, index, store.search,
                       reference)
    for v in (store.variants() if store.variants else []):
        build_s = None
        try:
            if v.setup:
                t0 = time.perf_counter()
                v.setup()
                build_s = time.perf_counter() - t0
                print(f"bench: {v.retriever.name} index build {build_s:.2f}s", flush=True)
        except Exception as e:  # noqa: BLE001 - the base rows stand; this variant is marked failed
            print(f"bench: {v.retriever.name} FAILED: {short_error(e)}", flush=True)
            rows.update({f"{v.retriever.name}/{m}": {"status": f"failed: {short_error(e)}"} for m in v.modes})
            continue
        rows.update(score_modes(v.retriever, queries, v.modes, repeats, ingest_s, build_s, v.index, v.search,
                                reference))
    return rows


def score_modes(r, queries: list[dict], modes: Iterable[str], repeats: int, ingest_s: float,
                index_build_s: float | None, index: str = "", search: str = "",
                reference: "ExactReference | None" = None) -> dict[str, dict]:
    """Score every mode for one retriever; a failing mode becomes a short `failed: ...` row."""
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
            row = _score_mode(r, mode, queries, repeats, ingest_s, index_build_s,
                              reference if mode == "vector" else None)
            if index or search:
                row = {"status": row.pop("status"), "index": index if mode != "fts" else "fts (BM25)",
                       "search": search if mode != "fts" else "", **row}
            rows[name] = row
        except Exception as e:  # noqa: BLE001
            rows[name] = {"status": f"failed: {short_error(e)}"}
            print(f"bench: {name} FAILED: {short_error(e)}", flush=True)
    return rows


def _score_mode(r, mode: str, queries: list[dict], repeats: int, ingest_s: float,
                index_build_s: float | None, reference: "ExactReference | None" = None) -> dict:
    """recall@5/10, MRR@10, ann_recall@10 (vector mode) and p50/p95 latency for one mode.

    One untimed pass over every query runs first, so caches and buffers are warm before timing.
    """
    for q in queries:
        r.search(q["query"], k=K, where=q["where"], mode=mode)
    ranked, latencies, lat_by_type = {}, [], {}
    for _ in range(repeats):
        for q in queries:
            t = time.perf_counter()
            hits = r.search(q["query"], k=K, where=q["where"], mode=mode)
            ms = (time.perf_counter() - t) * 1000
            latencies.append(ms)
            lat_by_type.setdefault(q["type"], []).append(ms)
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
    ann = None
    if reference is not None:
        vals = [v for q in queries if (v := reference.recall(q["id"], ranked[q["id"]])) is not None]
        ann = statistics.fmean(vals) if vals else None
    return {
        "status": "ok",
        "recall@5": mean(0, queries),
        "recall@10": mean(1, queries),
        "mrr@10": mean(2, queries),
        "ann_recall@10": ann,
        "p50_ms": percentile(latencies, 50),
        "p95_ms": percentile(latencies, 95),
        "ingest_s": ingest_s,
        "index_build_s": index_build_s,
        "recall@10_by_type": {t: mean(1, qs) for t, qs in by_type.items()},
        "mrr@10_by_type": {t: mean(2, qs) for t, qs in by_type.items()},
        "p50_ms_by_type": {t: percentile(v, 50) for t, v in lat_by_type.items()},
        "p95_ms_by_type": {t: percentile(v, 95) for t, v in lat_by_type.items()},
    }


class ExactReference:
    """Brute-force cosine top-k over exactly the vectors the backends ingested (same order, same
    jitter) with each query's `where` applied: the yardstick for ann_recall@10.

    ann_recall@10 = hits whose true cosine reaches the reference's k-th best (ties count) /
    min(k, rows passing the filter). Unlike label recall it does not depend on which replica
    of a document comes back, so it measures the index, not replica identity.
    """

    def __init__(self, docs: list[dict], embeddings, queries: list[dict], k: int = K):
        import numpy as np

        from retrieval.base import validate_where

        by_id = {d["call_id"]: d for d in docs}  # last occurrence wins, as in both backends
        self.ids = list(by_id)
        self.pos = {cid: i for i, cid in enumerate(self.ids)}
        x = np.asarray(embeddings.embed_documents([by_id[c]["text"] for c in self.ids]), dtype=np.float32)
        self.x = x / np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-12)
        meta = [by_id[c].get("metadata", {}) for c in self.ids]
        ops = {"eq": lambda a, b: a == b, "in": lambda a, b: a in b, "gte": lambda a, b: a is not None and a >= b,
               "lte": lambda a, b: a is not None and a <= b, "lt": lambda a, b: a is not None and a < b}
        self.k, self.ref = k, {}
        for q in queries:
            clauses = [(f, ops[op], [str(v) for v in val] if op == "in" else (None if val is None else str(val)))
                       for f, op, val in validate_where(q["where"])]
            mask = np.fromiter((all(fn(None if m.get(f) is None else str(m.get(f)), v) for f, fn, v in clauses)
                                for m in meta), dtype=bool, count=len(meta))
            n = int(mask.sum())
            if n == 0:
                continue
            v = np.asarray(embeddings.embed_query(q["query"]), dtype=np.float32)
            v /= max(float(np.linalg.norm(v)), 1e-12)
            sims = self.x @ v
            kth = float(np.sort(sims[mask])[-min(k, n)])
            self.ref[q["id"]] = (v, kth, n)

    def recall(self, qid: str, hit_ids: list[str]) -> float | None:
        if qid not in self.ref:
            return None
        v, kth, n = self.ref[qid]
        rows = [self.pos[c] for c in hit_ids[: self.k] if c in self.pos]
        good = int(((self.x[rows] @ v) >= kth - 1e-5).sum()) if rows else 0
        return min(good, self.k) / min(self.k, n)


def run_bench(docs: list[dict], queries: list[dict], embeddings, *,
              backends: Iterable[str] = BACKENDS, modes: Iterable[str] = MODES,
              repeats: int = 3, openers: dict[str, Callable] | None = None,
              workdir: Path | None = None, ingest_batch: int = INGEST_BATCH,
              cache_file: Path | None = None, jitter_eps: float = 0.0) -> dict:
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
    print("bench: exact reference top-10 for ann_recall@10", flush=True)
    reference = ExactReference(docs, JitteredEmbeddings(cached, jitter_eps) if jitter_eps else cached, queries)

    metrics: dict = {
        "embed_unique_docs": n_unique,
        "embed_docs_s": embed_s,
        "embed_query_p50_ms": percentile(q_times, 50) * 1000,
    }
    with tempfile.TemporaryDirectory(prefix="ccai-bench-", dir=workdir) as tmp:
        for name in backends:
            store = None
            try:
                emb = JitteredEmbeddings(cached, jitter_eps) if jitter_eps else cached
                store = openers[name](emb, Path(tmp))
                metrics.update(bench_backend(store, docs, queries, modes, repeats, ingest_batch, reference))
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
    parser.add_argument("--jitter", action=argparse.BooleanOptionalAction, default=None,
                        help="jitter replica vectors (default: on when --scale > 1)")
    parser.add_argument("--jitter-eps", type=float, default=JITTER_EPS,
                        help=f"jitter noise norm on unit vectors (default {JITTER_EPS})")
    parser.add_argument("--pgvector-index", choices=["hnsw", "none"], default="hnsw",
                        help="also score pgvector through an HNSW index (default hnsw); the exact row stays")
    parser.add_argument("--hnsw-m", type=int, default=16, help="HNSW m (default 16)")
    parser.add_argument("--hnsw-ef-construction", type=int, default=64, help="HNSW ef_construction (default 64)")
    parser.add_argument("--hnsw-ef-search", type=int, default=40, help="HNSW ef_search (default 40)")
    parser.add_argument("--nprobes", type=int, help="LanceDB IVF nprobes (default: LanceDB's)")
    parser.add_argument("--refine-factor", type=int, help="LanceDB refine_factor (default: none)")
    parser.add_argument("--num-partitions", type=int, help="LanceDB IVF partitions (default sqrt(rows))")
    parser.add_argument("--lance-index", default="ivf_pq",
                        choices=["ivf_pq", "ivf_flat", "ivf_sq", "ivf_hnsw_sq", "ivf_hnsw_pq"],
                        help="LanceDB vector index at >= 100,000 rows (default ivf_pq)")
    parser.add_argument("--num-sub-vectors", "--lance-num-sub-vectors", dest="num_sub_vectors", type=int,
                        help="LanceDB PQ sub-vectors (default: LanceDB's)")
    parser.add_argument("--lance-num-bits", type=int, help="LanceDB PQ bits per code (default 8)")
    parser.add_argument("--pgvector-parallel-workers", type=int,
                        help="max_parallel_maintenance_workers for the HNSW build (0 = serial; for a small /dev/shm)")
    parser.add_argument("--sweep", action="append", default=[],
                        help="LanceDB recall-vs-latency sweep; repeat to combine into a grid, e.g. "
                             "--sweep nprobes=20,50,100 --sweep refine_factor=1,20")
    parser.add_argument("--no-cache", action="store_true",
                        help="do not read or write the embedding cache (.cache/embeddings/)")
    args = parser.parse_args(argv)
    if args.ingest_batch < 1:
        parser.error("--ingest-batch must be at least 1")
    try:
        sweep = parse_sweep(args.sweep)
    except ValueError as e:
        parser.error(str(e))
    args.index_config = IndexConfig(
        pgvector_index=args.pgvector_index, hnsw_m=args.hnsw_m,
        hnsw_ef_construction=args.hnsw_ef_construction, hnsw_ef_search=args.hnsw_ef_search,
        nprobes=args.nprobes, refine_factor=args.refine_factor, num_partitions=args.num_partitions,
        num_sub_vectors=args.num_sub_vectors, pgvector_parallel_workers=args.pgvector_parallel_workers,
        lance_index=args.lance_index, num_bits=args.lance_num_bits,
        sweep=split_sweep(sweep)[0], sweep_ef_search=split_sweep(sweep)[1])
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
    embeddings = get_embeddings()  # an Ollama model is pulled here if missing
    print(f"bench: {len(docs)} docs, {len(queries)} queries, backends={backends}, modes={modes}")

    cache_file = None if args.no_cache else cache_file_for(embeddings)
    jitter_on = args.jitter if args.jitter is not None else args.scale > 1
    jitter_eps = args.jitter_eps if jitter_on else 0.0
    cfg = args.index_config
    openers = {k: (functools.partial(v, cfg=cfg) if v in (open_pgvector, open_lancedb) else v)
               for k, v in OPENERS.items()}
    metrics = run_bench(docs, queries, embeddings, backends=backends, modes=modes,
                        repeats=args.repeats, ingest_batch=args.ingest_batch, cache_file=cache_file,
                        openers=openers, jitter_eps=jitter_eps)

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
        "jitter_eps": jitter_eps, "index": cfg.as_params(),
        "labels_sha256": hashlib.sha256(labels_text.encode()).hexdigest()[:16],
    }
    notes = ("recall@k is capped recall, |rel ∩ top-k| / min(k, |rel|). Embeddings are "
             "precomputed and cached, so ingest_s and latency exclude the embedding model. "
             "pgvector builds no vector index (exact scan), so index_build_s is n/a.")
    if args.scale > 1:
        notes += (f" SYNTHETIC SCALE-UP: the {len(docs) // args.scale}-call corpus replicated "
                  f"{args.scale}x with new call_ids; replicas share text"
                  + (f" and get seeded vector jitter (eps={jitter_eps}), so they are near-duplicates, "
                     "not identical points." if jitter_eps else " and vectors.")
                  + " All replicas count as relevant, so recall at scale>1 measures retrieval among "
                    "near-duplicates; compare ingest and latency across scales, not recall.")
    run = args.run or f"bench-{'-'.join(backends)}-x{args.scale}"
    record = build_record(metrics, run=run, hardware=args.hardware or detect_hardware(),
                          versions=versions, params=params, notes=notes)
    out = write_record(record)
    readme = render("retrieval", out.parent.parent)
    print(f"\nwrote {out} and {readme}\n")
    print(metrics_tables(record))
    if any("[" in k for k in metrics):
        print("\n" + sweep_table(metrics))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
