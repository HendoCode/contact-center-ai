"""pgvector backend: the existing LangChain PGVector store behind the Retriever interface."""

import psycopg2

from rag.embeddings import COLLECTION_NAME, CONNECTION_STRING, get_vector_store
from retrieval.base import Hit, validate_where

_PG_OPS = {"eq": "$eq", "in": "$in", "gte": "$gte", "lte": "$lte"}

_ROW_SQL = (
    "SELECT e.document, e.cmetadata FROM langchain_pg_embedding AS e "
    "JOIN langchain_pg_collection AS c ON c.uuid = e.collection_id "
    "WHERE c.name = %s"
)


def translate_where(where: dict | None) -> dict | None:
    """Portable `where` -> LangChain PGVector filter syntax."""
    clauses = [{f: {_PG_OPS[op]: v}} for f, op, v in validate_where(where)]
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


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

    def count(self) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(_ROW_SQL.replace("e.document, e.cmetadata", "count(*)"),
                        (COLLECTION_NAME,))
            return cur.fetchone()[0]
