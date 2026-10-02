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
    {"date": {"gte": "2026-08-01", "lte": "2026-08-31"}}  # inclusive range (date only)

Dates are ISO `YYYY-MM-DD` strings, so lexicographic order is date order.
Anything outside this subset raises ValueError. Each backend translates it.
"""

import os
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

WHERE_FIELDS = ("category", "outcome", "date", "call_id")
RANGE_FIELDS = ("date",)
_OPERATORS = ("eq", "in", "gte", "lte")


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

    def count(self) -> int: ...


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
            if op in ("gte", "lte") and fld not in RANGE_FIELDS:
                raise ValueError(f"Range operator {op!r} is only supported on {RANGE_FIELDS}")
            if op == "in" and not isinstance(value, (list, tuple, set)):
                raise ValueError("'in' expects a list of values")
            triples.append((fld, op, list(value) if op == "in" else value))
    return triples


def get_retriever(backend: str | None = None) -> Retriever:
    """Return the backend named by `backend` or RETRIEVER_BACKEND (default "pgvector")."""
    backend = (backend or os.getenv("RETRIEVER_BACKEND", "pgvector")).lower()
    if backend == "pgvector":
        from retrieval.pgvector_backend import PgVectorRetriever
        return PgVectorRetriever()
    if backend == "lancedb":
        from retrieval.lancedb_backend import LanceDBRetriever
        return LanceDBRetriever()
    raise ValueError(f"Unknown retriever backend: {backend!r} (available: pgvector, lancedb)")
