"""
Retriever interface shared by every vector-search backend.

`get_retriever()` is the only entry point consumers (rag/, ccai_mcp/, the
benchmark) should use. Backends embed with the one shared
`rag.embeddings.get_embeddings()`, so backend comparisons are fair.

Portable `where` subset
-----------------------
A dict of ANDed conditions over the fields `category`, `outcome`, `date` and
`call_id`. Each value is one of:

    {"category": "fraud_dispute"}                      # equality
    {"outcome": {"in": ["resolved", "escalated"]}}     # membership
    {"date": {"gte": "2026-08-01", "lte": "2026-08-31"}}  # range: gte, lte, lt

Stored `date` is an ISO datetime ("2026-03-31T15:00:00"); bounds are ISO strings,
compared lexicographically. A date-only bound (`YYYY-MM-DD`) means the whole day:
`gte` starts at 00:00, `lte` includes the full day, `lt` excludes it. A datetime
bound keeps its exact meaning. `validate_where` does this normalization once, so
both backends see the same triples. Anything outside this subset raises
ValueError. Each backend translates it.
"""

import os
from datetime import date, datetime, timedelta
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

WHERE_FIELDS = ("category", "outcome", "date", "call_id")
RANGE_FIELDS = ("date",)
_OPERATORS = ("eq", "in", "gte", "lte", "lt")
_RANGE_OPS = ("gte", "lte", "lt")


@dataclass(frozen=True)
class Hit:
    call_id: str
    text: str
    # Higher = more relevant. For `search` it is normalized to [0, 1]-ish per
    # backend (pgvector: 1 - cosine distance). `get_by_id` is not a ranked
    # lookup and returns score 1.0.
    score: float
    metadata: dict = field(default_factory=dict)


@runtime_checkable
class Retriever(Protocol):
    name: str  # "pgvector" | "lancedb"

    def ingest(self, docs: list[dict]) -> int:
        """Upsert docs by call_id. Each doc: {"call_id", "text", "metadata"}.
        Returns the number of rows written."""
        ...

    def search(
        self,
        query: str,
        k: int = 5,
        where: dict | None = None,
        mode: Literal["vector", "fts", "hybrid"] = "vector",
    ) -> list[Hit]: ...

    def get_by_id(self, call_id: str) -> Hit | None:
        """Metadata lookup, never semantic."""
        ...

    def count(self, where: dict | None = None) -> int:
        """Rows in the store, or only those matching the portable `where`."""
        ...


def _normalize_bound(op: str, value: object) -> tuple[str, object]:
    """Validate an ISO date/datetime bound; widen a date-only `lte` to `lt` next day."""
    if not isinstance(value, str):
        raise ValueError(f"Date bound must be an ISO string, got {value!r}")
    try:
        day = date.fromisoformat(value) if len(value) == 10 else None
        if day is None:
            datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"Invalid ISO date or datetime bound {value!r}") from None
    if day is not None and op == "lte":
        return "lt", (day + timedelta(days=1)).isoformat()
    return op, value


def validate_where(where: dict | None) -> list[tuple[str, str, object]]:
    """Validate a portable `where` and flatten it to (field, op, value) triples."""
    triples: list[tuple[str, str, object]] = []
    for fld, cond in (where or {}).items():
        if fld not in WHERE_FIELDS:
            raise ValueError(f"Unsupported where field {fld!r}; allowed: {WHERE_FIELDS}")
        if not isinstance(cond, dict):
            triples.append((fld, "eq", cond))
            continue
        if not cond:
            raise ValueError(f"Empty condition for where field {fld!r}")
        for op, value in cond.items():
            if op not in _OPERATORS:
                raise ValueError(f"Unsupported where operator {op!r}; allowed: {_OPERATORS}")
            if op in _RANGE_OPS and fld not in RANGE_FIELDS:
                raise ValueError(f"Range operator {op!r} is only supported on {RANGE_FIELDS}")
            if op == "in" and not isinstance(value, (list, tuple, set)):
                raise ValueError("'in' expects a list of values")
            if op in _RANGE_OPS:
                op, value = _normalize_bound(op, value)
            triples.append((fld, op, list(value) if op == "in" else value))
    return triples


def get_retriever(backend: str | None = None, embeddings=None) -> Retriever:
    """Return the backend named by `backend` or RETRIEVER_BACKEND (default "pgvector").

    `embeddings` overrides the shared `get_embeddings()` model (ingest passes a cached one).
    """
    backend = (backend or os.getenv("RETRIEVER_BACKEND", "pgvector")).lower()
    if backend == "pgvector":
        from retrieval.pgvector_backend import PgVectorRetriever
        if embeddings is None:
            return PgVectorRetriever()
        from rag.embeddings import get_vector_store
        return PgVectorRetriever(store=get_vector_store(embeddings))
    if backend == "lancedb":
        from retrieval.lancedb_backend import LanceDBRetriever
        return LanceDBRetriever(embeddings=embeddings)
    raise ValueError(f"Unknown retriever backend: {backend!r} (available: pgvector, lancedb)")
