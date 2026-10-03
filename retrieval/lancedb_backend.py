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
fields `category`, `outcome` and `date`, and `metadata` (the full metadata dict as JSON).
Ingest is `merge_insert` on `call_id` (update when matched, insert otherwise), so
re-running it never duplicates rows.

Indexes
-------
* `fts`: native Lance full-text (BM25) index on `text`. Built on first ingest and
  refreshed with `optimize()` after later ones so new rows are indexed, not scanned.
* `call_id`: BTree scalar index, which keeps the merge and `get_by_id` lookups fast.
* `vector`: **none below `VECTOR_INDEX_MIN_ROWS` (100,000 rows)**. The synthetic corpus is
  ~1,250 documents. A brute-force (flat) scan over that many vectors is exact and takes
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
    ):
        self.uri = uri or os.getenv("LANCE_URI") or DEFAULT_LANCE_URI
        # Object-store settings for az:// / s3:// (e.g. azure_storage_account_name,
        # azure_use_azure_cli); None leaves Lance to read them from the environment.
        self.storage_options = storage_options
        # IVF-PQ knobs; None keeps LanceDB's defaults (num_partitions: sqrt(rows)).
        self.nprobes, self.refine_factor, self.num_partitions = nprobes, refine_factor, num_partitions
        self.num_sub_vectors = num_sub_vectors  # PQ sub-vectors; None = LanceDB's default
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
        """The table, or None before the first ingest."""
        if self.table_name not in self.db.list_tables().tables:
            return None
        return self.db.open_table(self.table_name)

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
                pa.field("metadata", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), dim)),
            ]
        )

    # ── ingest ────────────────────────────────────────────────────────────────

    def ingest(self, docs: list[dict]) -> int:
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
                    "metadata": json.dumps(meta, default=str),
                    "vector": vector,
                }
            )
        data = pa.Table.from_pylist(rows, schema=self._schema(dim))

        table = self._table()
        if table is None:
            table = self.db.create_table(self.table_name, data=data, schema=self._schema(dim))
        else:
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
        from lancedb.index import FTS, BTree, IvfPq

        existing = {idx.columns[0] for idx in table.list_indices()}
        if "call_id" not in existing:
            table.create_index("call_id", config=BTree())
        if "text" not in existing:
            table.create_index("text", config=FTS())
        n = table.count_rows()
        if "vector" not in existing and n >= self.vector_index_min_rows:
            table.create_index(
                "vector",
                config=IvfPq(distance_type="cosine",
                             num_partitions=self.num_partitions or max(1, int(math.sqrt(n))),
                             num_sub_vectors=self.num_sub_vectors),
            )
        # Fold rows written since the last run into the existing indexes.
        table.optimize()

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
        rows = q.limit(k).to_list()
        return self._to_hits(rows, mode)

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

    def count(self) -> int:
        table = self._table()
        return 0 if table is None else table.count_rows()
