"""Offline unit tests for the Retriever interface and pgvector backend (no DB, no network)."""

from types import SimpleNamespace

import pytest

from retrieval import Hit, get_retriever
from retrieval.base import Retriever, validate_where
from retrieval.pgvector_backend import PgVectorRetriever, translate_where


class _FakeStore:
    def __init__(self):
        self.rows: dict[str, tuple[str, dict]] = {}  # keyed by id, like ON CONFLICT (id)
        self.search_calls = []

    def add_texts(self, texts, metadatas, ids):
        for i, t, m in zip(ids, texts, metadatas):
            self.rows[i] = (t, m)

    def similarity_search_with_score(self, query, k, filter):
        self.search_calls.append((query, k, filter))
        return [
            (SimpleNamespace(page_content=t, metadata=m), 0.25) for t, m in self.rows.values()
        ][:k]


class _FakeConn:
    def __init__(self, row):
        self.row, self.executed = row, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params=()):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.row


def _doc(i, **meta):
    return {"call_id": f"CALL-{i:05d}", "text": f"text {i}", "metadata": {"category": "fraud", **meta}}


# ── where translation ─────────────────────────────────────────────────────────

def test_where_none_and_empty_translate_to_no_filter():
    assert translate_where(None) is None
    assert translate_where({}) is None


def test_where_equality_in_and_date_range():
    assert translate_where({"category": "fraud"}) == {"category": {"$eq": "fraud"}}
    assert translate_where({"outcome": {"in": ["a", "b"]}}) == {"outcome": {"$in": ["a", "b"]}}
    assert translate_where({"category": "fraud", "date": {"gte": "2026-08-01", "lte": "2026-08-31"}}) == {
        "$and": [
            {"category": {"$eq": "fraud"}},
            {"date": {"$gte": "2026-08-01"}},
            {"date": {"$lte": "2026-08-31"}},
        ]
    }


@pytest.mark.parametrize(
    "bad",
    [
        {"member_id": "x"},
        {"category": {"gte": "a"}},
        {"category": {"like": "a"}},
        {"category": {"in": "notalist"}},
        {"category": {}},
    ],
)
def test_where_rejects_outside_portable_subset(bad):
    with pytest.raises(ValueError):
        validate_where(bad)


# ── selection ─────────────────────────────────────────────────────────────────

def test_get_retriever_defaults_to_pgvector(monkeypatch):
    monkeypatch.delenv("RETRIEVER_BACKEND", raising=False)
    r = get_retriever()
    assert r.name == "pgvector" and isinstance(r, Retriever)


def test_get_retriever_env_and_arg(monkeypatch):
    monkeypatch.setenv("RETRIEVER_BACKEND", "nope")
    with pytest.raises(ValueError, match="nope"):
        get_retriever()
    assert get_retriever("pgvector").name == "pgvector"


# ── ingest / search / lookup ──────────────────────────────────────────────────

def test_ingest_is_idempotent_upsert_by_call_id():
    store = _FakeStore()
    r = PgVectorRetriever(store=store)
    docs = [_doc(1), _doc(2)]
    assert r.ingest(docs) == 2
    assert r.ingest(docs) == 2
    assert sorted(store.rows) == ["CALL-00001", "CALL-00002"]  # not doubled
    assert store.rows["CALL-00001"][1]["call_id"] == "CALL-00001"


def test_ingest_dedupes_within_batch_last_wins_and_handles_empty():
    store = _FakeStore()
    r = PgVectorRetriever(store=store)
    assert r.ingest([]) == 0
    assert r.ingest([_doc(1), {**_doc(1), "text": "newer"}]) == 1
    assert store.rows["CALL-00001"][0] == "newer"


def test_search_returns_hits_with_similarity_score_and_passes_where():
    store = _FakeStore()
    r = PgVectorRetriever(store=store)
    r.ingest([_doc(1, date="2026-08-01")])
    hits = r.search("q", k=3, where={"category": "fraud"})
    assert hits == [Hit("CALL-00001", "text 1", 0.75, store.rows["CALL-00001"][1])]
    assert store.search_calls == [("q", 3, {"category": {"$eq": "fraud"}})]


@pytest.mark.parametrize("mode", ["fts", "hybrid"])
def test_search_unsupported_modes_raise(mode):
    with pytest.raises(NotImplementedError, match="vector"):
        PgVectorRetriever(store=_FakeStore()).search("q", mode=mode)


def test_get_by_id_and_count_use_sql_not_embeddings():
    conn = _FakeConn(("some text", {"call_id": "CALL-00007"}))
    r = PgVectorRetriever(store=None, connect=lambda: conn)
    hit = r.get_by_id("CALL-00007")
    assert hit == Hit("CALL-00007", "some text", 1.0, {"call_id": "CALL-00007"})
    assert "cmetadata->>'call_id' = %s" in conn.executed[0][0]

    assert PgVectorRetriever(connect=lambda: _FakeConn(None)).get_by_id("x") is None
    assert PgVectorRetriever(connect=lambda: _FakeConn((1250,))).count() == 1250
