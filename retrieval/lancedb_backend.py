"""
LanceDB backend: embedded vector, full-text and hybrid search behind the Retriever interface.

LanceDB OSS runs in-process (no server, no Compose service). `LANCE_URI` points at a
local directory (default `data/lance`, gitignored) or an object-store URI such as
`az://container/path` or `s3://bucket/path`; the same code path serves all of them,
with credentials supplied by the environment as the Lance object-store layer expects.

Storage
-------
One table (`COLLECTION_NAME`, default `call_transcripts`) with columns `call_id`,
`text`, `vector` (fixed-size float32, from the shared `get_embeddings()`), the filterable
fields `category`, `outcome`, `date` and `home_branch`, and `metadata` (the full metadata dict as JSON).
Ingest is `merge_insert` on `call_id` (update when matched, insert otherwise), so
re-running it never duplicates rows.

Versions
--------
Each write commits an immutable table version. `make ingest` tags the finished table
`seed-<git sha>`; `LanceDBRetriever(as_of=<tag or version>)` reads that snapshot read-only,
`snapshot()` names the version being read, and `rollback()` restores a tag. See
`retrieval/lance_versions.py`.

Indexes
-------
* `fts`: native Lance full-text (BM25) index on `text`. Built on first ingest and
  refreshed with `optimize()` after later ones so new rows are indexed, not scanned.
* `call_id`: BTree scalar index, which keeps the merge and `get_by_id` lookups fast.
* `vector`: **none below `VECTOR_INDEX_MIN_ROWS` (100,000 rows)**. The synthetic corpus is
  ~1,540 documents. A brute-force (flat) scan over that many vectors is exact and takes
  milliseconds, while IVF-PQ has to train k-means centroids and a PQ codebook from the
  data (PQ training needs at least 256 rows), costs recall to quantization, and only wins
  once a scan over every vector is the bottleneck. At or above the threshold, ingest builds
  an IVF-PQ index (cosine, `sqrt(n)` partitions). Pass `vector_index_min_rows` to override.
  The R3 benchmark's `--scale N` option is where the index path gets measured.

Search
------
Every mode applies `where` as a SQL filter with **prefiltering**: rows are filtered before
the top-k is taken, so a selective filter still returns k hits rather than fewer.

* `vector`: cosine distance. `score = 1 - distance` (same as pgvector), so higher is better.
* `fts`: BM25 over `text`. BM25 is unbounded and not comparable across queries, so
  `score = bm25 / max bm25 in this result set`: the top hit is 1.0 and the rest are relative.
* `hybrid`: vector and FTS candidate lists fused with Reciprocal Rank Fusion
  (`RRFReranker`, K=60), which uses ranks only and so needs no score calibration between
  cosine and BM25. Each list contributes `1 / (K + rank)`; `score` is the fused value divided
  by its maximum, `2 / (K + 1)` (a document ranked first in both lists), giving [0, 1].
"""

import json
import math
import os
from pathlib import Path
from typing import Literal

import pyarrow as pa

from retrieval.base import Hit, validate_where

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LANCE_URI = str(REPO_ROOT / "data" / "lance")
VECTOR_INDEX_MIN_ROWS = 100_000
# Vector index types in the pinned lancedb (0.39: IvfPq, IvfFlat, IvfSq, IvfHnswSq, IvfHnswPq).
INDEX_TYPES = ("ivf_pq", "ivf_flat", "ivf_sq", "ivf_hnsw_sq", "ivf_hnsw_pq")
RRF_K = 60
EMBED_BATCH = 64

_SQL_OPS = {"gte": ">=", "lte": "<=", "lt": "<"}


def _literal(value: object) -> str:
    """SQL string literal (single quotes doubled). All filterable columns are strings."""
    return "'" + str(value).replace("'", "''") + "'"


def translate_where(where: dict | None) -> str | None:
    """Portable `where` -> LanceDB SQL filter string (None when there is no filter)."""
    clauses = []
    for fld, op, value in validate_where(where):
        if op == "eq":
            clauses.append(f"{fld} IS NULL" if value is None else f"{fld} = {_literal(value)}")
        elif op == "in":
            clauses.append(
                f"{fld} IN ({', '.join(_literal(v) for v in value)})" if value else "1 = 0"
            )
        else:
            clauses.append(f"{fld} {_SQL_OPS[op]} {_literal(value)}")
    return " AND ".join(clauses) or None


class LanceDBRetriever:
    name = "lancedb"

    def __init__(
        self,
        uri: str | None = None,
        embeddings=None,
        table_name: str | None = None,
        vector_index_min_rows: int = VECTOR_INDEX_MIN_ROWS,
        storage_options: dict[str, str] | None = None,
        nprobes: int | None = None,
        refine_factor: int | None = None,
        num_partitions: int | None = None,
        num_sub_vectors: int | None = None,
        index_type: str = "ivf_pq",
        num_bits: int | None = None,
        as_of: int | str | None = None,
    ):
        # A table version number or tag to read instead of the newest (time travel, read-only).
        self.as_of = as_of
        self.uri = uri or os.getenv("LANCE_URI") or DEFAULT_LANCE_URI
        # Object-store settings for az:// / s3:// (e.g. azure_storage_account_name,
        # azure_use_azure_cli); None leaves Lance to read them from the environment.
        self.storage_options = storage_options
        # IVF-PQ knobs; None keeps LanceDB's defaults (num_partitions: sqrt(rows)).
        self.nprobes, self.refine_factor, self.num_partitions = nprobes, refine_factor, num_partitions
        self.num_sub_vectors = num_sub_vectors  # PQ sub-vectors; None = LanceDB's default
        if index_type not in INDEX_TYPES:
            raise ValueError(f"index_type {index_type!r}; use one of {', '.join(INDEX_TYPES)}")
        self.index_type, self.num_bits = index_type, num_bits
        self.table_name = table_name or os.getenv("COLLECTION_NAME", "call_transcripts")
        self.vector_index_min_rows = vector_index_min_rows
        self._embeddings = embeddings
        self._db = None

    # ── plumbing ──────────────────────────────────────────────────────────────

    @property
    def embeddings(self):
        if self._embeddings is None:
            from rag.embeddings import get_embeddings
            self._embeddings = get_embeddings()
        return self._embeddings

    @property
    def db(self):
        if self._db is None:
            import lancedb
            self._db = lancedb.connect(self.uri, storage_options=self.storage_options)
        return self._db

    def _table(self):
        """The table (at `as_of`, when set), or None before the first ingest."""
        if self.table_name not in self.db.list_tables().tables:
            return None
        table = self.db.open_table(self.table_name)
        if self.as_of is not None:
            table.checkout(self.as_of)
        return table

    def _embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH):
            vectors.extend(self.embeddings.embed_documents(texts[i : i + EMBED_BATCH]))
        return vectors

    @staticmethod
    def _schema(dim: int) -> pa.Schema:
        return pa.schema(
            [
                pa.field("call_id", pa.string(), nullable=False),
                pa.field("text", pa.string()),
                pa.field("category", pa.string()),
                pa.field("outcome", pa.string()),
                pa.field("date", pa.string()),
                pa.field("home_branch", pa.string()),
                pa.field("metadata", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), dim)),
            ]
        )

    def _refuse_write_as_of(self) -> None:
        # merge_insert on a checked-out handle would still commit to the newest version.
        if self.as_of is not None:
            raise ValueError(f"This retriever reads a snapshot (as_of={self.as_of!r}) and is read-only")

    def reset(self) -> None:
        """Drop the table (a later ingest recreates it, with the current columns)."""
        self._refuse_write_as_of()
        if self._table() is not None:
            self.db.drop_table(self.table_name)

    # ── versions ──────────────────────────────────────────────────────────────

    def snapshot(self) -> dict | None:
        """Backend, Lance version, tag and row count this retriever reads (None before ingest)."""
        from retrieval import lance_versions

        table = self._table()
        return None if table is None else lance_versions.snapshot(table)

    def tag_seed(self, label: str | None = None, keep: int | None = None) -> str:
        """Tag the table as ingested: `seed-<git sha>`, after applying the retention policy."""
        from retrieval import lance_versions

        self._refuse_write_as_of()
        table = self._require_table()
        return lance_versions.tag_seed(table, label, keep or lance_versions.KEEP_SEED_TAGS)

    def versions(self) -> list[dict]:
        from retrieval import lance_versions

        return lance_versions.list_versions(self._require_table())

    def rollback(self, ref: int | str) -> int:
        """Restore a tag or version as the newest one; returns the new version number."""
        from retrieval import lance_versions

        self._refuse_write_as_of()
        return lance_versions.rollback(self._require_table(), ref)

    def _require_table(self):
        table = self._table()
        if table is None:
            raise ValueError(f"No table {self.table_name!r} at {self.uri!r}; ingest first")
        return table

    # ── ingest ────────────────────────────────────────────────────────────────

    def ingest(self, docs: list[dict]) -> int:
        self._refuse_write_as_of()
        # Last occurrence of a call_id wins, as in the pgvector backend.
        by_id = {d["call_id"]: d for d in docs}
        if not by_id:
            return 0
        ids = list(by_id)
        vectors = self._embed_documents([by_id[i]["text"] for i in ids])
        dim = len(vectors[0])

        rows = []
        for call_id, vector in zip(ids, vectors):
            doc = by_id[call_id]
            meta = {**doc.get("metadata", {}), "call_id": call_id}
            rows.append(
                {
                    "call_id": call_id,
                    "text": doc["text"],
                    "category": meta.get("category"),
                    "outcome": meta.get("outcome"),
                    "date": meta.get("date"),
                    "home_branch": meta.get("home_branch"),
                    "metadata": json.dumps(meta, default=str),
                    "vector": vector,
                }
            )
        data = pa.Table.from_pylist(rows, schema=self._schema(dim))

        table = self._table()
        if table is None:
            table = self.db.create_table(self.table_name, data=data, schema=self._schema(dim))
        else:
            if "home_branch" not in table.schema.names:
                raise ValueError(
                    f"Table {self.table_name!r} at {self.uri!r} predates the home_branch column. "
                    "Rebuild it: python -m rag.pipeline --reset, then --ingest."
                )
            existing = table.schema.field("vector").type.list_size
            if existing != dim:
                raise ValueError(
                    f"Embedding dimension {dim} does not match the table's {existing}; the "
                    f"embedding model changed. Delete {self.uri!r} table {self.table_name!r} "
                    "and re-ingest."
                )
            (
                table.merge_insert("call_id")
                .when_matched_update_all()
                .when_not_matched_insert_all()
                .execute(data)
            )
        self._ensure_indexes(table)
        return len(rows)

    def _ensure_indexes(self, table) -> None:
        from lancedb.index import FTS, BTree

        existing = {idx.columns[0] for idx in table.list_indices()}
        if "call_id" not in existing:
            table.create_index("call_id", config=BTree())
        if "text" not in existing:
            table.create_index("text", config=FTS())
        n = table.count_rows()
        if "vector" not in existing and n >= self.vector_index_min_rows:
            table.create_index("vector", config=self._vector_index_config(n))
        # Fold rows written since the last run into the existing indexes.
        table.optimize()

    def _vector_index_config(self, n: int):
        """The vector index for `index_type` (all cosine); PQ knobs apply only to the *_pq types."""
        import lancedb.index as li

        kw = {"distance_type": "cosine",
              "num_partitions": self.num_partitions or max(1, int(math.sqrt(n)))}
        pq = {k: v for k, v in (("num_sub_vectors", self.num_sub_vectors), ("num_bits", self.num_bits))
              if v is not None}
        cls = {"ivf_pq": li.IvfPq, "ivf_flat": li.IvfFlat, "ivf_sq": li.IvfSq,
               "ivf_hnsw_sq": li.IvfHnswSq, "ivf_hnsw_pq": li.IvfHnswPq}[self.index_type]
        return cls(**kw, **(pq if self.index_type.endswith("_pq") else {}))

    # ── search ────────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        k: int = 5,
        where: dict | None = None,
        mode: Literal["vector", "fts", "hybrid"] = "vector",
    ) -> list[Hit]:
        if mode not in ("vector", "fts", "hybrid"):
            raise ValueError(f"Unknown search mode {mode!r}; use 'vector', 'fts' or 'hybrid'")
        flt = translate_where(where)  # validate even when the table is empty
        table = self._table()
        if table is None:
            return []
        rows = self._query(table, query, k, flt, mode).to_list()
        return self._to_hits(rows, mode)

    def _query(self, table, query: str, k: int, flt: str | None, mode: str):
        """The LanceDB query builder for one search (`explain_plan()` shows nprobes etc.)."""
        if mode == "fts":
            q = table.search(query, query_type="fts")
        elif mode == "vector":
            q = table.search(self.embeddings.embed_query(query), query_type="vector")
            q = q.distance_type("cosine")
        else:
            from lancedb.rerankers import RRFReranker

            q = (
                table.search(query_type="hybrid")
                .vector(self.embeddings.embed_query(query))
                .text(query)
                .distance_type("cosine")
                .rerank(RRFReranker(K=RRF_K))
            )
        if mode != "fts":
            if self.nprobes:
                q = q.nprobes(self.nprobes)
            if self.refine_factor:
                q = q.refine_factor(self.refine_factor)
        if flt:
            q = q.where(flt, prefilter=True)
        return q.limit(k)

    @staticmethod
    def _to_hits(rows: list[dict], mode: str) -> list[Hit]:
        if mode == "vector":
            scores = [1.0 - r["_distance"] for r in rows]
        elif mode == "fts":
            top = max((r["_score"] for r in rows), default=0.0)
            scores = [r["_score"] / top if top > 0 else 0.0 for r in rows]
        else:
            best = 2.0 / (RRF_K + 1)
            scores = [r["_relevance_score"] / best for r in rows]
        return [
            Hit(call_id=r["call_id"], text=r["text"], score=float(s), metadata=json.loads(r["metadata"]))
            for r, s in zip(rows, scores)
        ]

    # ── lookup ────────────────────────────────────────────────────────────────

    def get_by_id(self, call_id: str) -> Hit | None:
        table = self._table()
        if table is None:
            return None
        rows = (
            table.search()
            .where(f"call_id = {_literal(call_id)}")
            .select(["call_id", "text", "metadata"])
            .limit(1)
            .to_list()
        )
        if not rows:
            return None
        r = rows[0]
        return Hit(call_id=r["call_id"], text=r["text"], score=1.0, metadata=json.loads(r["metadata"]))

    def count(self, where: dict | None = None) -> int:
        table = self._table()
        return 0 if table is None else table.count_rows(translate_where(where))
