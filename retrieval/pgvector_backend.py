"""pgvector backend: the existing LangChain PGVector store behind the Retriever interface."""

import os
import threading

import psycopg2
from langchain_postgres.vectorstores import PGVector

from rag.embeddings import get_embeddings
from retrieval.base import Hit, refuse_lance_only, validate_where

CONNECTION_STRING = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/contactcenter"
)

COLLECTION_NAME = os.getenv("COLLECTION_NAME", "call_transcripts")


# langchain_postgres defines its SQLAlchemy tables on the first PGVector it builds, behind
# an unlocked module-level check. Two builds at once in one process (two concurrent tool
# calls in the MCP server, which runs tools in threads) both define them, and every one
# after the first fails with "Table 'langchain_pg_collection' is already defined for this
# MetaData instance". So builds go through one lock, and the default store is built once
# per process and collection, then reused.
_STORE_LOCK = threading.Lock()
_STORES: dict[tuple[str, str], PGVector] = {}


def get_vector_store(embeddings=None) -> PGVector:
    """
    Return a configured PGVector store.

    Creates the pgvector extension and collection table on first run. Without
    `embeddings`, the store for the configured model is built once per process and reused;
    with `embeddings` (e.g. ingest's cached model) a new store is built, under the same lock.
    """
    with _STORE_LOCK:
        if embeddings is not None:
            return _build_store(embeddings)
        key = (CONNECTION_STRING, COLLECTION_NAME)
        if key not in _STORES:
            _STORES[key] = _build_store(get_embeddings())
        return _STORES[key]


def _build_store(embeddings) -> PGVector:
    return PGVector(
        embeddings=embeddings,
        collection_name=COLLECTION_NAME,
        connection=CONNECTION_STRING,
        use_jsonb=True,
    )


_PG_OPS = {"eq": "$eq", "in": "$in", "gte": "$gte", "lte": "$lte", "lt": "$lt"}

_ROW_SQL = (
    "SELECT e.document, e.cmetadata FROM langchain_pg_embedding AS e "
    "JOIN langchain_pg_collection AS c ON c.uuid = e.collection_id "
    "WHERE c.name = %s"
)


def translate_where(where: dict | None) -> dict | None:
    """Portable `where` -> LangChain PGVector filter syntax (`csat_score` is refused)."""
    triples = validate_where(where)
    refuse_lance_only(triples, "pgvector")
    clauses = [{f: {_PG_OPS[op]: v}} for f, op, v in triples]
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


_COUNT_OPS = {"gte": ">=", "lte": "<=", "lt": "<"}


def _count_sql(where: dict | None) -> tuple[str, list]:
    """SQL and parameters for `count`: each portable condition on the JSONB metadata text.

    Dates and every other filterable value are stored as ISO or plain strings, and PGVector's
    own `$gte` and `$lt` compare them as text too, so this agrees with `search`.
    """
    sql, params = _ROW_SQL.replace("e.document, e.cmetadata", "count(*)"), [COLLECTION_NAME]
    triples = validate_where(where)
    refuse_lance_only(triples, "pgvector")
    for fld, op, value in triples:
        if op == "eq" and value is None:
            sql += " AND e.cmetadata->>%s IS NULL"
            params.append(fld)
        elif op == "eq":
            sql += " AND e.cmetadata->>%s = %s"
            params += [fld, str(value)]
        elif op == "in":
            if value:
                sql += f" AND e.cmetadata->>%s IN ({', '.join(['%s'] * len(value))})"
                params += [fld, *(str(v) for v in value)]
            else:
                sql += " AND 1 = 0"
        else:
            sql += f" AND e.cmetadata->>%s {_COUNT_OPS[op]} %s"
            params += [fld, str(value)]
    return sql, params


class PgVectorRetriever:
    name = "pgvector"

    def __init__(self, store=None, connect=None):
        self._store = store
        self._connect = connect or (lambda: psycopg2.connect(CONNECTION_STRING))

    @property
    def store(self):
        if self._store is None:
            self._store = get_vector_store()
        return self._store

    def ingest(self, docs: list[dict]) -> int:
        # Row id == call_id, so PGVector's ON CONFLICT (id) DO UPDATE upserts.
        # De-dupe within the batch (last wins): one INSERT can't hit a key twice.
        by_id = {d["call_id"]: d for d in docs}
        if not by_id:
            return 0
        ids = list(by_id)
        self.store.add_texts(
            texts=[by_id[i]["text"] for i in ids],
            metadatas=[{**by_id[i].get("metadata", {}), "call_id": i} for i in ids],
            ids=ids,
        )
        return len(ids)

    def search(self, query, k=5, where=None, mode="vector") -> list[Hit]:
        if mode != "vector":
            raise NotImplementedError(
                f"pgvector backend supports mode='vector' only, not {mode!r}"
            )
        results = self.store.similarity_search_with_score(
            query, k=k, filter=translate_where(where)
        )
        # PGVector returns cosine *distance* (lower = closer); flip so higher = better.
        return [
            Hit(
                call_id=doc.metadata["call_id"],
                text=doc.page_content,
                score=1.0 - distance,
                metadata=dict(doc.metadata),
            )
            for doc, distance in results
        ]

    def get_by_id(self, call_id: str) -> Hit | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(_ROW_SQL + " AND e.cmetadata->>'call_id' = %s LIMIT 1",
                        (COLLECTION_NAME, call_id))
            row = cur.fetchone()
        if row is None:
            return None
        text, metadata = row
        return Hit(call_id=call_id, text=text, score=1.0, metadata=metadata)

    def count(self, where: dict | None = None) -> int:
        """All rows, or those matching the portable `where`, the same filter `search` takes."""
        sql, params = _count_sql(where)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()[0]
