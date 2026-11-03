"""
Retriever interface shared by every vector-search backend.

`get_retriever()` is the only entry point consumers (rag/, ccai_mcp/, the
benchmark) should use. Backends embed with the one shared
`rag.embeddings.get_embeddings()`, so backend comparisons are fair.

Portable `where` subset
-----------------------
A dict of ANDed conditions over the fields `category`, `outcome`, `date`, `call_id`,
`home_branch` (the branch the caller's member banks at) and `csat_score` (the survey score,
1-5; LanceDB only, see below). Each value is one of:

    {"category": "fraud_dispute"}                      # equality
    {"outcome": {"in": ["resolved", "escalated"]}}     # membership
    {"home_branch": "Westside"}                        # a fact about the member
    {"date": {"gte": "2026-08-01", "lte": "2026-08-31"}}  # range: gte, lte, lt
    {"csat_score": {"lte": 2}}                         # integer range: gte, lte, lt

Stored `date` is an ISO datetime ("2026-03-31T15:00:00"); bounds are ISO strings,
compared lexicographically. A date-only bound (`YYYY-MM-DD`) means the whole day:
`gte` starts at 00:00, `lte` includes the full day, `lt` excludes it. A datetime
bound keeps its exact meaning. `validate_where` does this normalization once, so
both backends see the same triples. Anything outside this subset raises
ValueError. Each backend translates it.

`csat_score` is an integer column (1-5, or null for a call with no survey) that only the
LanceDB backend stores: it is filled from the Postgres `csat_survey` table after ingest.
`validate_where` accepts it; the pgvector backend refuses it with a ValueError (see
`LANCE_ONLY_FIELDS`), the way it refuses the `fts` and `hybrid` modes. Its bounds are
integers in 1-5, and `{"csat_score": None}` selects the calls without a survey.
"""

import os
from datetime import date, datetime, timedelta
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

WHERE_FIELDS = ("category", "outcome", "date", "call_id", "home_branch", "csat_score")
RANGE_FIELDS = ("date", "csat_score")
INT_FIELDS = ("csat_score",)  # integer-valued columns, so values are numbers rather than strings
LANCE_ONLY_FIELDS = ("csat_score",)  # fields a backend may refuse, e.g. pgvector keeps no survey data
CSAT_SCORES = (1, 5)  # the survey scale, inclusive
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


def _check_int(fld: str, value: object) -> int:
    """An integer-field value: an int (not a bool) inside the survey scale."""
    lo, hi = CSAT_SCORES
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError(f"{fld} must be an integer from {lo} to {hi}, got {value!r}")
    return value


def refuse_lance_only(triples: list[tuple[str, str, object]], backend: str) -> None:
    """Raise for a where field `backend` does not store (called by backends that lack it)."""
    for fld, _op, _value in triples:
        if fld in LANCE_ONLY_FIELDS:
            raise ValueError(
                f"The {backend} backend does not support filtering on {fld!r}; "
                "it needs RETRIEVER_BACKEND=lancedb"
            )


def validate_where(where: dict | None) -> list[tuple[str, str, object]]:
    """Validate a portable `where` and flatten it to (field, op, value) triples."""
    triples: list[tuple[str, str, object]] = []
    for fld, cond in (where or {}).items():
        if fld not in WHERE_FIELDS:
            raise ValueError(f"Unsupported where field {fld!r}; allowed: {WHERE_FIELDS}")
        if not isinstance(cond, dict):
            if fld in INT_FIELDS and cond is not None:
                _check_int(fld, cond)
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
            if fld in INT_FIELDS:
                for v in (value if op == "in" else [value]):
                    if not (op == "eq" and v is None):
                        _check_int(fld, v)
            elif op in _RANGE_OPS:
                op, value = _normalize_bound(op, value)
            triples.append((fld, op, list(value) if op == "in" else value))
    return triples


def store_snapshot() -> dict | None:
    """Provenance of the store the default retriever reads, for results JSON `params.store`.

    A versioned store (LanceDB) names the table version and tag the run read, so the run can be
    replayed with `LanceDBRetriever(as_of=...)`. Other backends have no version: None.
    """
    if os.getenv("RETRIEVER_BACKEND", "pgvector").lower() != "lancedb":
        return None
    return get_retriever().snapshot()


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
