"""Offline tests for the LanceDB backend against a 50-document fixture (no network, no keys)."""

import pytest

from retrieval import Hit, get_retriever
from retrieval.base import Retriever
from retrieval.lancedb_backend import LanceDBRetriever, translate_where

QUERY = "unauthorized card charge dispute"


# ── where translation ─────────────────────────────────────────────────────────

def test_where_none_and_empty_translate_to_no_filter():
    assert translate_where(None) is None
    assert translate_where({}) is None


def test_where_equality_in_range_and_quote_escaping():
    assert translate_where({"category": "fraud"}) == "category = 'fraud'"
    assert translate_where({"outcome": {"in": ["a", "b"]}}) == "outcome IN ('a', 'b')"
    assert translate_where({"outcome": {"in": []}}) == "1 = 0"
    assert translate_where({"call_id": "x' OR '1'='1"}) == "call_id = 'x'' OR ''1''=''1'"
    assert translate_where(
        {"category": "fraud", "date": {"gte": "2026-08-01", "lte": "2026-08-31"}}
    ) == "category = 'fraud' AND date >= '2026-08-01' AND date <= '2026-08-31'"


@pytest.mark.parametrize("bad", [{"member_id": "x"}, {"category": {"gte": "a"}}, {"category": {}}])
def test_where_rejects_outside_portable_subset(bad):
    with pytest.raises(ValueError):
        translate_where(bad)


# ── selection ─────────────────────────────────────────────────────────────────

def test_get_retriever_selects_lancedb(monkeypatch, tmp_path):
    monkeypatch.setenv("RETRIEVER_BACKEND", "lancedb")
    monkeypatch.setenv("LANCE_URI", str(tmp_path / "lance"))
    r = get_retriever()
    assert isinstance(r, LanceDBRetriever) and isinstance(r, Retriever)
    assert r.name == "lancedb" and r.uri == str(tmp_path / "lance")


# ── ingest / count ────────────────────────────────────────────────────────────

def test_empty_store_is_safe(make_retriever):
    r = make_retriever()
    assert r.count() == 0
    assert r.ingest([]) == 0
    assert r.get_by_id("CALL-00000") is None
    for mode in ("vector", "fts", "hybrid"):
        assert r.search(QUERY, mode=mode) == []


def test_ingest_is_idempotent_upsert_by_call_id(make_retriever, docs):
    r = make_retriever()
    assert r.ingest(docs) == 50
    assert r.ingest(docs) == 50
    assert r.count() == 50  # not doubled
    changed = {**docs[3], "text": "completely rewritten transcript about mortgages"}
    r.ingest([changed, {**docs[3], "text": "last one wins about overdraft fees"}])
    assert r.count() == 50
    assert r.get_by_id(docs[3]["call_id"]).text == "last one wins about overdraft fees"


def test_ingest_in_new_process_sees_existing_rows(make_retriever, docs):
    make_retriever().ingest(docs)
    again = make_retriever()  # fresh connection to the same directory
    assert again.count() == 50
    again.ingest(docs[:5])
    assert again.count() == 50


def test_ingest_rejects_embedding_dimension_change(make_retriever, docs, fake_embeddings):
    r = make_retriever()
    r.ingest(docs)

    class Wider:
        def embed_documents(self, texts):
            return [v + [0.0] for v in fake_embeddings.embed_documents(texts)]

    r._embeddings = Wider()
    with pytest.raises(ValueError, match="dimension"):
        r.ingest(docs[:2])


# ── vector ────────────────────────────────────────────────────────────────────

def test_vector_search_ranks_semantically_and_scores_higher_is_better(retriever):
    hits = retriever.search(QUERY, k=5)
    assert len(hits) == 5 and all(isinstance(h, Hit) for h in hits)
    assert {h.metadata["category"] for h in hits} == {"fraud_dispute"}
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    assert 0.0 < hits[0].score <= 1.0


def test_vector_search_exact_text_scores_one(retriever, docs):
    top = retriever.search(docs[7]["text"], k=1)[0]
    assert top.call_id == docs[7]["call_id"]
    assert top.score == pytest.approx(1.0, abs=1e-5)


def test_metadata_round_trips(retriever, docs):
    hit = retriever.search(docs[2]["text"], k=1)[0]
    assert hit.metadata == {**docs[2]["metadata"], "call_id": docs[2]["call_id"]}


# ── fts ───────────────────────────────────────────────────────────────────────

def test_fts_finds_exact_token_vectors_would_not_pin(retriever):
    hits = retriever.search("zq023", k=3, mode="fts")
    assert hits[0].call_id == "CALL-00023"
    assert hits[0].score == pytest.approx(1.0)
    assert all(0.0 < h.score <= 1.0 for h in hits)


def test_fts_tolerates_punctuation(retriever):
    assert retriever.search('lost "debit" card? (replacement)', k=3, mode="fts")


def test_fts_sees_rows_added_after_first_ingest(retriever):
    retriever.ingest([{"call_id": "CALL-LATE", "text": "unique wordxylophone here", "metadata": {}}])
    assert retriever.search("wordxylophone", mode="fts")[0].call_id == "CALL-LATE"


# ── hybrid ────────────────────────────────────────────────────────────────────

def test_hybrid_fuses_vector_and_fts_with_normalised_scores(retriever):
    hits = retriever.search("fraud dispute zq010", k=5, mode="hybrid")
    assert hits and len(hits) <= 5
    assert hits[0].call_id == "CALL-00010"  # exact token (fts) and topic (vector) agree
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    assert all(0.0 < h.score <= 1.0 for h in hits)


def test_hybrid_surfaces_fts_match_the_vector_list_misses(retriever):
    # The exact-token document (zq004) is a fraud call, but the token only matches via FTS;
    # fusion must still place it in the top 2.
    ids = [h.call_id for h in retriever.search("fraud dispute zq004", k=2, mode="hybrid")]
    assert "CALL-00004" in ids


# ── where (prefiltered) ───────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["vector", "fts", "hybrid"])
def test_where_equality_prefilters_to_k_results_in_every_mode(retriever, mode):
    # Only 10 of 50 docs are fraud_dispute; the unfiltered top 5 for this loan query contains
    # none of them (they match only "member"), so a post-filter would return nothing.
    # Prefiltering still fills k.
    hits = retriever.search(
        "member auto loan rates", k=5, mode=mode, where={"category": "fraud_dispute"}
    )
    assert len(hits) == 5
    assert {h.metadata["category"] for h in hits} == {"fraud_dispute"}


def test_where_in_and_date_range_and_combination(retriever, docs):
    hits = retriever.search(QUERY, k=50, where={"outcome": {"in": ["escalated", "follow_up"]}})
    assert hits and {h.metadata["outcome"] for h in hits} <= {"escalated", "follow_up"}
    assert len(hits) == sum(d["metadata"]["outcome"] != "resolved" for d in docs)

    window = {"gte": "2026-03-01", "lte": "2026-05-31"}
    hits = retriever.search(QUERY, k=50, where={"date": window})
    assert hits and all("2026-03-01" <= h.metadata["date"] <= "2026-05-31" for h in hits)
    assert len(hits) == sum("2026-03-01" <= d["metadata"]["date"] <= "2026-05-31" for d in docs)

    hits = retriever.search(QUERY, k=50, where={"category": "fraud_dispute", "outcome": "resolved"})
    assert hits and all(
        h.metadata["category"] == "fraud_dispute" and h.metadata["outcome"] == "resolved" for h in hits
    )


def test_where_no_match_returns_empty(retriever):
    assert retriever.search(QUERY, where={"category": "nope"}) == []
    assert retriever.search(QUERY, where={"outcome": {"in": []}}) == []


def test_where_with_quote_is_not_injectable(retriever):
    assert retriever.search(QUERY, where={"category": "x' OR '1'='1"}) == []


def test_invalid_where_and_mode_raise(retriever):
    with pytest.raises(ValueError):
        retriever.search(QUERY, where={"member_id": "x"})
    with pytest.raises(ValueError, match="mode"):
        retriever.search(QUERY, mode="bm25")


# ── get_by_id ─────────────────────────────────────────────────────────────────

def test_get_by_id_is_a_pure_filter(retriever, docs):
    hit = retriever.get_by_id("CALL-00031")
    assert hit == Hit("CALL-00031", docs[31]["text"], 1.0, {**docs[31]["metadata"], "call_id": "CALL-00031"})
    assert retriever.get_by_id("CALL-99999") is None
    assert retriever.get_by_id("x' OR '1'='1") is None


def test_get_by_id_never_embeds(make_retriever, docs):
    r = make_retriever()
    r.ingest(docs)

    class Boom:
        def embed_query(self, *_):
            raise AssertionError("get_by_id must not embed")

    r._embeddings = Boom()
    assert r.get_by_id("CALL-00001").call_id == "CALL-00001"
    assert r.count() == 50


# ── vector index policy ───────────────────────────────────────────────────────

def _indexed_columns(r):
    return {idx.columns[0] for idx in r._table().list_indices()}


def test_no_vector_index_below_threshold(retriever):
    assert _indexed_columns(retriever) == {"call_id", "text"}


def test_vector_index_built_at_threshold_and_search_still_works(make_retriever, docs_factory):
    big = docs_factory(300)
    r = make_retriever(vector_index_min_rows=300)
    r.ingest(big)
    assert "vector" in _indexed_columns(r)
    assert r.count() == 300
    r.ingest(big)  # idempotent with an index present
    assert r.count() == 300
    top = r.search(big[42]["text"], k=3, where={"category": "balance_inquiry"})
    assert len(top) == 3 and {h.metadata["category"] for h in top} == {"balance_inquiry"}
