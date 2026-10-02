"""DuckDB reader over the LanceDB dataset.

Offline tests cover what needs neither `duckdb` nor the extension. The `integration` tests
run `INSTALL lance` (a download from DuckDB's extension repository), so they are skipped by
default: `uv run pytest tests/retrieval/test_duckdb_lance.py -m integration`.
"""

import json

import pytest

from retrieval.duckdb_lance import DuckDBLanceReader, where_to_sql

QUERY = "unauthorized card charge dispute"


# ── offline ───────────────────────────────────────────────────────────────────

def test_where_to_sql_is_parameterized():
    assert where_to_sql(None) == ("", [])
    assert where_to_sql({"category": "fraud"}) == (" WHERE category = ?", ["fraud"])
    assert where_to_sql({"outcome": {"in": ["a", "b"]}}) == (" WHERE outcome IN (?, ?)", ["a", "b"])
    assert where_to_sql({"outcome": {"in": []}}) == (" WHERE 1 = 0", [])
    clause, params = where_to_sql({"call_id": "x' OR '1'='1"})
    assert clause == " WHERE call_id = ?" and params == ["x' OR '1'='1"]
    assert where_to_sql({"date": {"gte": "2026-08-01", "lte": "2026-08-31"}}) == (
        " WHERE date >= ? AND date < ?",
        ["2026-08-01", "2026-09-01"],
    )


@pytest.mark.parametrize("bad", [{"member_id": "x"}, {"category": {"gte": "a"}}, {"category": {}}])
def test_where_to_sql_rejects_outside_portable_subset(bad):
    with pytest.raises(ValueError):
        where_to_sql(bad)


def test_dataset_path_follows_lance_uri_and_table(monkeypatch):
    monkeypatch.setenv("LANCE_URI", "/data/lance/")
    monkeypatch.setenv("COLLECTION_NAME", "calls")
    assert DuckDBLanceReader().dataset == "/data/lance/calls.lance"
    assert DuckDBLanceReader(uri="s3://b/p", table_name="t").dataset == "s3://b/p/t.lance"


def test_bad_mode_and_where_fail_before_duckdb_is_touched():
    reader = DuckDBLanceReader(uri="/nonexistent", install=False)
    with pytest.raises(ValueError, match="Unknown search mode"):
        reader.search(QUERY, mode="semantic")
    with pytest.raises(ValueError, match="Unsupported where field"):
        reader.search(QUERY, where={"member_id": "x"})
    assert reader._con is None


def test_count_by_rejects_unknown_columns_without_connecting():
    reader = DuckDBLanceReader(uri="/nonexistent", install=False)
    with pytest.raises(ValueError, match="count_by needs columns"):
        reader.count_by("text")
    with pytest.raises(ValueError, match="count_by needs columns"):
        reader.count_by()
    assert reader._con is None


# ── integration: needs the downloaded `lance` extension ───────────────────────

@pytest.fixture
def reader(make_retriever, docs, fake_embeddings):
    pytest.importorskip("duckdb")
    make_retriever().ingest(docs)  # the 50-document fixture, written by the LanceDB backend
    retriever = make_retriever()
    return DuckDBLanceReader(uri=retriever.uri, embeddings=fake_embeddings)


@pytest.mark.integration
def test_extension_loads_and_reports_versions(reader):
    versions = reader.versions()
    assert versions["duckdb"].startswith("v")
    assert versions["duckdb_lance_extension"]


@pytest.mark.integration
def test_count_and_aggregate_match_the_fixture(reader, docs):
    assert reader.count() == len(docs) == 50
    by_cat = {r["category"]: r["n"] for r in reader.count_by("category")}
    assert by_cat == {c: 10 for c in by_cat} and len(by_cat) == 5
    pairs = reader.count_by("category", "outcome")
    assert sum(r["n"] for r in pairs) == 50
    expected = sum(
        1 for d in docs if d["metadata"]["category"] == "fraud_dispute"
        and d["metadata"]["outcome"] == "escalated"
    )
    assert {(r["category"], r["outcome"]): r["n"] for r in pairs}[("fraud_dispute", "escalated")] == expected
    only = reader.count_by("outcome", where={"category": "fraud_dispute"})
    assert sum(r["n"] for r in only) == 10


@pytest.mark.integration
def test_vector_search_agrees_with_the_lancedb_backend(reader, make_retriever):
    got = reader.search(QUERY, k=5, mode="vector")
    want = make_retriever().search(QUERY, k=5, mode="vector")
    assert [h.call_id for h in got][0] == want[0].call_id
    assert {h.call_id for h in got} == {h.call_id for h in want}
    assert all(h.metadata["category"] == "fraud_dispute" for h in got)
    # Cosine similarity, same quantity as the LanceDB backend reports.
    assert got[0].score == pytest.approx(want[0].score, abs=1e-4)
    assert [h.score for h in got] == sorted((h.score for h in got), reverse=True)


@pytest.mark.integration
def test_vector_search_with_where_is_prefiltered(reader):
    hits = reader.search(QUERY, k=5, where={"outcome": "resolved"}, mode="vector")
    assert len(hits) == 5  # filtered before top-k, so k hits even though few top matches are resolved
    assert {h.metadata["outcome"] for h in hits} == {"resolved"}


@pytest.mark.integration
def test_fts_finds_the_exact_token_and_normalizes_scores(reader):
    hits = reader.search("zq007", k=3, mode="fts")
    assert hits[0].call_id == "CALL-00007"
    assert hits[0].score == pytest.approx(1.0)
    assert all(0.0 <= h.score <= 1.0 for h in hits)
    assert reader.search("nonexistentwordxyz", k=3, mode="fts") == []


@pytest.mark.integration
def test_fts_with_where_filters_after_search(reader):
    hits = reader.search("member card", k=5, where={"category": "card_replacement"}, mode="fts")
    assert hits and {h.metadata["category"] for h in hits} == {"card_replacement"}


@pytest.mark.integration
def test_hybrid_search_returns_hits_and_honours_where(reader):
    hits = reader.search(QUERY, k=5, mode="hybrid")
    assert len(hits) == 5 and hits[0].metadata["category"] == "fraud_dispute"
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)
    filtered = reader.search(QUERY, k=5, where={"outcome": "escalated"}, mode="hybrid")
    assert filtered and {h.metadata["outcome"] for h in filtered} == {"escalated"}


@pytest.mark.integration
def test_get_by_id_is_a_filter_lookup(reader, docs):
    hit = reader.get_by_id("CALL-00012")
    assert hit is not None and hit.score == 1.0
    assert hit.metadata == json.loads(json.dumps({**docs[12]["metadata"], "call_id": "CALL-00012"}))
    assert reader.get_by_id("CALL-99999") is None
