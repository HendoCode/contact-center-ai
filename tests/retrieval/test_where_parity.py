"""`count` and `search` agree on the portable `where`, on both backends, including `home_branch`.

LanceDB runs for real (embedded, fake embeddings). The pgvector side runs the backend's own code
for `search` (the filter it hands to LangChain's PGVector) and for `count` (the SQL it sends to
Postgres), against stand-ins that evaluate them in memory and in SQLite's JSON functions. That
checks the translation, not Postgres itself; the Compose database is not needed.
"""

import json
import re
import sqlite3
from types import SimpleNamespace

import pytest

from retrieval.pgvector_backend import PgVectorRetriever

BRANCHES = ("Westside", "Downtown", "Northgate", "Riverside")


def docs_with_branches(make_docs, n: int = 60) -> list[dict]:
    docs = make_docs(n)
    for i, doc in enumerate(docs):
        # one document per branch in turn, plus a few with no branch at all
        doc["metadata"]["home_branch"] = None if i % 11 == 0 else BRANCHES[i % 4]
        doc["metadata"] = {k: v for k, v in doc["metadata"].items() if v is not None}
    return docs


WHERES = [
    None,
    {},
    {"category": "fraud_dispute"},
    {"home_branch": "Westside"},
    {"home_branch": {"in": ["Westside", "Downtown"]}},
    {"home_branch": {"in": []}},
    {"category": "fraud_dispute", "home_branch": "Westside"},
    {"outcome": {"in": ["resolved", "escalated"]}, "home_branch": {"in": ["Northgate"]}},
    {"date": {"gte": "2026-03-01", "lte": "2026-05-31"}},
    {"date": {"lt": "2026-04-02"}, "home_branch": "Riverside"},
    {"call_id": "CALL-00007"},
    {"call_id": {"in": ["CALL-00001", "CALL-00002", "CALL-99999"]}},
]


# ── pgvector stand-ins ────────────────────────────────────────────────────────

def _matches(meta: dict, clause: dict) -> bool:
    """Evaluate one LangChain PGVector filter clause against a metadata dict (text comparison)."""
    if "$and" in clause:
        return all(_matches(meta, c) for c in clause["$and"])
    ((field, cond),) = clause.items()
    ((op, value),) = cond.items()
    have = meta.get(field)
    if op == "$eq":
        return have == value if value is not None else have is None
    if op == "$in":
        return have in value
    if have is None:
        return False
    return {"$gte": have >= value if op == "$gte" else False,
            "$lte": have <= value if op == "$lte" else False,
            "$lt": have < value if op == "$lt" else False}[op]


class StoreStandIn:
    def __init__(self, rows: dict[str, tuple[str, dict]]):
        self.rows = rows

    def similarity_search_with_score(self, query, k, filter):
        hits = [(SimpleNamespace(page_content=text, metadata=dict(meta)), 0.1)
                for text, meta in self.rows.values() if filter is None or _matches(meta, filter)]
        return hits[:k]


class SqliteCursor:
    """Runs the backend's count SQL (psycopg2 `%s` style) on SQLite, `->>` included."""

    def __init__(self, conn):
        self.conn, self.cur = conn, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params=()):
        self.cur = self.conn.execute(re.sub(r"%s", "?", sql), params)

    def fetchone(self):
        return self.cur.fetchone()


@pytest.fixture
def pgvector(docs_factory):
    docs = docs_with_branches(docs_factory)
    rows = {d["call_id"]: (d["text"], {**d["metadata"], "call_id": d["call_id"]}) for d in docs}
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE langchain_pg_collection (uuid TEXT, name TEXT)")
    conn.execute("CREATE TABLE langchain_pg_embedding (document TEXT, cmetadata TEXT, collection_id TEXT)")
    from rag.embeddings import COLLECTION_NAME

    conn.execute("INSERT INTO langchain_pg_collection VALUES ('c1', ?)", (COLLECTION_NAME,))
    conn.executemany("INSERT INTO langchain_pg_embedding VALUES (?, ?, 'c1')",
                     [(text, json.dumps(meta)) for text, meta in rows.values()])
    return PgVectorRetriever(store=StoreStandIn(rows), connect=lambda: SqliteCursor(conn)), docs


# ── the parity test ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("where", WHERES, ids=lambda w: json.dumps(w, sort_keys=True))
def test_count_and_search_agree_on_both_backends(where, pgvector, make_retriever):
    pg, docs = pgvector
    lance = make_retriever()
    lance.ingest(docs)
    everything = len(docs)

    expected = {d["call_id"] for d in docs
                if _passes({**d["metadata"], "call_id": d["call_id"]}, where)}

    found = {}
    for name, retriever in (("pgvector", pg), ("lancedb", lance)):
        searched = {h.call_id for h in retriever.search("member account", k=everything, where=where)}
        found[name] = (searched, retriever.count(where))
        assert searched == expected, name
        assert retriever.count(where) == len(expected), name
    assert found["pgvector"][1] == found["lancedb"][1]


def test_home_branch_filters_return_only_that_branch(pgvector, make_retriever):
    pg, docs = pgvector
    lance = make_retriever()
    lance.ingest(docs)
    for retriever in (pg, lance):
        hits = retriever.search("member account", k=len(docs), where={"home_branch": "Westside"})
        assert hits and {h.metadata["home_branch"] for h in hits} == {"Westside"}


def test_existing_lance_tables_without_the_column_ask_for_a_rebuild(tmp_path, fake_embeddings, docs_factory):
    import lancedb
    import pyarrow as pa

    from retrieval.lancedb_backend import LanceDBRetriever

    uri = str(tmp_path / "old")
    old_schema = pa.schema([
        pa.field("call_id", pa.string(), nullable=False), pa.field("text", pa.string()),
        pa.field("category", pa.string()), pa.field("outcome", pa.string()),
        pa.field("date", pa.string()), pa.field("metadata", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), 64)),
    ])
    lancedb.connect(uri).create_table("call_transcripts", schema=old_schema)
    retriever = LanceDBRetriever(uri=uri, embeddings=fake_embeddings)
    with pytest.raises(ValueError, match="--reset"):
        retriever.ingest(docs_factory(3))
    retriever.reset()
    assert retriever.ingest(docs_factory(3)) == 3 and retriever.count({"home_branch": "x"}) == 0


def _passes(meta: dict, where: dict | None) -> bool:
    """Reference semantics for the portable `where`, straight from the metadata."""
    for field, cond in (where or {}).items():
        have = meta.get(field)
        if not isinstance(cond, dict):
            if have != cond:
                return False
            continue
        for op, value in cond.items():
            if op == "in" and have not in value:
                return False
            if op == "gte" and (have is None or have < value):
                return False
            if op == "lte" and (have is None or have[:10] > value):
                return False
            if op == "lt" and (have is None or have >= value):
                return False
    return True
