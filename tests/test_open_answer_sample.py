"""Open answers state their sample: N retrieved calls of M in the category, M counted from
the store itself (never from the evals' references)."""

from types import SimpleNamespace as NS

import pytest

import rag.pipeline as pipeline
from retrieval.base import Hit
from retrieval.pgvector_backend import PgVectorRetriever


def hit(call_id, category):
    return Hit(call_id=call_id, text=f"text of {call_id}", score=0.9,
               metadata={"category": category, "date": "2026-01-01", "outcome": "resolved"})


SIZES = {"fraud_dispute": 143, "fee_dispute": 37}


def count(where=None):
    return SIZES[where["category"]]


def test_one_category():
    hits = [hit(f"CALL-0000{i}", "fraud_dispute") for i in range(5)]
    assert pipeline.sample_statement(hits, count) == \
        "Based on a sample of 5 retrieved calls: 5 of 143 calls in fraud_dispute."


def test_mixed_categories_largest_first():
    hits = [hit("CALL-1", "fee_dispute"), hit("CALL-2", "fraud_dispute"), hit("CALL-3", "fraud_dispute")]
    assert pipeline.sample_statement(hits, count) == \
        "Based on a sample of 3 retrieved calls: 2 of 143 calls in fraud_dispute; 1 of 37 calls in fee_dispute."


def test_a_size_that_cannot_be_counted_is_said_so():
    def broken(where=None):
        raise RuntimeError("db down")
    assert pipeline.sample_statement([hit("CALL-1", "fee_dispute")], broken) == \
        "Based on a sample of 1 retrieved call: 1 calls in fee_dispute (category size unavailable)."


def test_no_hits():
    assert pipeline.sample_statement([], count) == "No matching calls were retrieved."


class FakeRetriever:
    def search(self, query, k, mode):
        return [hit(f"CALL-0000{i}", "fraud_dispute") for i in range(k)]

    def count(self, where=None):
        return count(where)


def test_rag_query_opens_with_n_of_m_and_tells_the_model_it_is_a_sample(monkeypatch):
    seen = {}

    class LLM:
        def invoke(self, prompt):
            seen["prompt"] = prompt
            return NS(content="Members report unrecognized charges (CALL-00000).")

    monkeypatch.setattr(pipeline, "get_retriever", lambda: FakeRetriever())
    monkeypatch.setattr(pipeline, "get_llm", lambda: LLM())
    out = pipeline.rag_query("What are members saying about fraud disputes?", k=5)
    assert out.startswith("Based on a sample of 5 retrieved calls: 5 of 143 calls in fraud_dispute.\n\n")
    assert out.endswith("Members report unrecognized charges (CALL-00000).")
    assert "a retrieved sample, not every call" in seen["prompt"]
    assert "5 of 143 calls in fraud_dispute" in seen["prompt"]


class FakeCursor:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params):
        self.log.append((sql, params))

    def fetchone(self):
        return (143,)


def test_pgvector_counts_by_metadata_equality():
    log = []
    r = PgVectorRetriever(store=object(), connect=lambda: FakeCursor(log))
    assert r.count({"category": "fraud_dispute"}) == 143
    sql, params = log[0]
    assert sql.endswith("WHERE c.name = %s AND e.cmetadata->>%s = %s")
    assert params[1:] == ["category", "fraud_dispute"]
    with pytest.raises(NotImplementedError):
        r.count({"date": {"gte": "2026-01-01"}})


def test_lancedb_counts_with_the_same_where(tmp_path):
    pytest.importorskip("lancedb")
    from langchain_core.embeddings import DeterministicFakeEmbedding

    from retrieval.lancedb_backend import LanceDBRetriever

    r = LanceDBRetriever(uri=str(tmp_path), embeddings=DeterministicFakeEmbedding(size=8))
    docs = [{"call_id": f"CALL-{i}", "text": f"t{i}",
             "metadata": {"call_id": f"CALL-{i}", "category": "fee_dispute" if i < 3 else "fraud_dispute",
                          "date": "2026-01-01", "outcome": "resolved"}} for i in range(5)]
    r.ingest(docs)
    assert r.count() == 5
    assert r.count({"category": "fee_dispute"}) == 3
