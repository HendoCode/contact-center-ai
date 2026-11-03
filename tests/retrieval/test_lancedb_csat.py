"""CSAT-aware search on the LanceDB backend: typed columns added after ingest, prefiltered search.

Offline: fake embeddings, a temp Lance directory, and a stand-in for the Postgres `csat_survey`
query. Nothing is re-embedded when the scores are attached.
"""

from datetime import date
from types import SimpleNamespace

import pytest

from rag import pipeline
from retrieval import get_retriever
from retrieval.base import validate_where
from retrieval.lancedb_backend import translate_where
from retrieval.pgvector_backend import PgVectorRetriever
from retrieval.pgvector_backend import translate_where as pg_translate_where

QUERY = "unauthorized card charge dispute"


def surveys_for(docs, every: int = 2) -> dict:
    """Every `every`-th call has a survey; scores cycle 1..5, so each of them appears."""
    return {
        d["call_id"]: (1 + i % 5, f"2026-02-{1 + i % 28:02d}")
        for i, d in enumerate(docs)
        if i % every == 0
    }


@pytest.fixture
def scored(retriever, docs):
    surveys = surveys_for(docs)
    retriever.set_csat(surveys)
    return retriever, surveys


# ── where ─────────────────────────────────────────────────────────────────────

def test_csat_score_where_translates_to_unquoted_integers():
    assert translate_where({"csat_score": {"lte": 2}}) == "csat_score <= 2"
    assert translate_where({"csat_score": {"gte": 1, "lte": 2}}) == "csat_score >= 1 AND csat_score <= 2"
    assert translate_where({"csat_score": 3}) == "csat_score = 3"
    assert translate_where({"csat_score": {"in": [1, 2]}}) == "csat_score IN (1, 2)"
    assert translate_where({"csat_score": None}) == "csat_score IS NULL"


@pytest.mark.parametrize(
    "bad",
    [
        {"csat_score": "2"}, {"csat_score": 0}, {"csat_score": 6}, {"csat_score": True},
        {"csat_score": {"lte": 2.5}}, {"csat_score": {"lte": "2"}}, {"csat_score": {"in": [1, 9]}},
        {"csat_score": {}},
    ],
)
def test_csat_score_where_rejects_values_outside_the_scale(bad):
    with pytest.raises(ValueError):
        validate_where(bad)


def test_date_bounds_still_normalise_beside_an_integer_range():
    triples = validate_where({"date": {"lte": "2026-08-31"}, "csat_score": {"lte": 2}})
    assert ("date", "lt", "2026-09-01") in triples and ("csat_score", "lte", 2) in triples


# ── set_csat ──────────────────────────────────────────────────────────────────

def test_set_csat_adds_typed_columns_and_a_scalar_index(scored):
    r, surveys = scored
    table = r._table()
    assert str(table.schema.field("csat_score").type) == "int32"
    assert str(table.schema.field("surveyed_at").type) == "string"
    assert {i.columns[0] for i in table.list_indices()} >= {"csat_score", "call_id", "text"}
    assert next(i for i in table.list_indices() if i.columns[0] == "csat_score").index_type == "Bitmap"
    assert r.count({"csat_score": {"gte": 1}}) == len(surveys)
    assert r.count({"csat_score": None}) == r.count() - len(surveys)


def test_set_csat_does_not_reembed_or_rewrite_vectors(retriever, docs):
    table = retriever._table()
    before = table.search().select(["call_id", "vector"]).limit(100).to_arrow().sort_by("call_id")

    class Boom:
        def embed_documents(self, texts):  # pragma: no cover - must not be called
            raise AssertionError("set_csat must not embed")

        embed_query = embed_documents

    retriever._embeddings = Boom()
    retriever.set_csat(surveys_for(docs))
    after = retriever._table().search().select(["call_id", "vector"]).limit(100).to_arrow().sort_by("call_id")
    assert before.equals(after)


def test_set_csat_is_repeatable_and_clears_a_survey_removed_upstream(scored, docs):
    r, surveys = scored
    removed = next(iter(surveys))
    r.set_csat({k: v for k, v in surveys.items() if k != removed})
    assert r.count({"csat_score": {"gte": 1}}) == len(surveys) - 1
    assert r.count({"call_id": removed, "csat_score": None}) == 1
    r.set_csat(surveys)
    r.set_csat(surveys)
    assert r.count({"csat_score": {"gte": 1}}) == len(surveys)
    assert r.count() == len(docs)


def test_set_csat_rejects_a_score_outside_the_scale(retriever, docs):
    with pytest.raises(ValueError, match="1 to 5"):
        retriever.set_csat({docs[0]["call_id"]: (9, None)})


def test_set_csat_on_an_empty_store_is_safe(make_retriever):
    assert make_retriever().set_csat({"CALL-00001": (2, None)}) == 0


def test_re_ingest_keeps_scores_and_the_csat_step_covers_new_rows(scored, docs):
    r, surveys = scored
    r.ingest(docs)  # same rows again, narrower schema than the table
    assert r.count({"csat_score": {"gte": 1}}) == len(surveys)
    late = {"call_id": "CALL-LATE", "text": "fee dispute on a late fee", "metadata": {"category": "fee_dispute"}}
    r.ingest([late])
    assert r.count({"call_id": "CALL-LATE", "csat_score": None}) == 1  # new row: no score yet
    r.set_csat({**surveys, "CALL-LATE": (1, "2026-03-01")})
    assert r.count({"call_id": "CALL-LATE", "csat_score": 1}) == 1


def test_reset_then_ingest_then_set_csat_restores_the_columns(scored, docs):
    r, surveys = scored
    r.reset()
    r.ingest(docs)
    assert "csat_score" not in r._table().schema.names
    r.set_csat(surveys)
    assert r.count({"csat_score": {"gte": 1}}) == len(surveys)


# ── search ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["vector", "fts", "hybrid"])
def test_search_is_prefiltered_by_csat_in_every_mode(scored, mode):
    r, surveys = scored
    low = {c for c, (score, _d) in surveys.items() if score <= 2}
    # k larger than any one category's low-scoring calls: a post-filter would return fewer.
    hits = r.search("member card account", k=len(low), where={"csat_score": {"lte": 2}}, mode=mode)
    assert len(hits) == len(low)
    assert {h.call_id for h in hits} == low
    assert all(h.metadata["csat_score"] <= 2 for h in hits)


def test_csat_filter_combines_with_other_fields_and_ranks_by_relevance(scored):
    r, surveys = scored
    where = {"category": "fraud_dispute", "csat_score": {"gte": 1, "lte": 2}}
    hits = r.search(QUERY, k=10, where=where)
    expected = {
        c for c, (score, _d) in surveys.items()
        if score <= 2 and r.get_by_id(c).metadata["category"] == "fraud_dispute"
    }
    assert hits and {h.call_id for h in hits} == expected
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


def test_hits_carry_the_survey_columns_only_when_present(scored):
    r, surveys = scored
    call_id, (score, day) = next(iter(surveys.items()))
    hit = r.search(r.get_by_id(call_id).text, k=1)[0]
    assert hit.call_id == call_id
    assert (hit.metadata["csat_score"], hit.metadata["surveyed_at"]) == (score, day)
    no_survey = r.search("zq001", k=1, mode="fts")[0]  # CALL-00001 has no survey (odd index)
    assert "csat_score" not in no_survey.metadata


def test_csat_search_before_scores_exist_says_how_to_add_them(retriever):
    with pytest.raises(ValueError, match="ingest"):
        retriever.search(QUERY, where={"csat_score": {"lte": 2}})
    with pytest.raises(ValueError, match="ingest"):
        retriever.count({"csat_score": {"lte": 2}})


def test_csat_filter_on_an_empty_store_returns_nothing(make_retriever):
    assert make_retriever().search(QUERY, where={"csat_score": {"lte": 2}}) == []


# ── pgvector refuses it ───────────────────────────────────────────────────────

def test_pgvector_refuses_a_csat_filter_clearly():
    class Store:
        def similarity_search_with_score(self, *a, **kw):  # pragma: no cover
            raise AssertionError("must refuse before searching")

    with pytest.raises(ValueError, match="RETRIEVER_BACKEND=lancedb"):
        pg_translate_where({"csat_score": {"lte": 2}})
    r = PgVectorRetriever(store=Store(), connect=lambda: None)
    with pytest.raises(ValueError, match="csat_score"):
        r.search(QUERY, where={"csat_score": {"lte": 2}})
    with pytest.raises(ValueError, match="csat_score"):
        r.count({"csat_score": 2})


# ── Postgres source and the ingest step ───────────────────────────────────────

class _Conn:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return self

    def execute(self, sql):
        self.sql.append(sql)

    def fetchall(self):
        return self.rows


def test_load_csat_surveys_reads_postgres_and_maps_interaction_ids_to_call_ids():
    docs = [
        SimpleNamespace(metadata={"call_id": "CALL-00001", "interaction_id": 101}),
        SimpleNamespace(metadata={"call_id": "CALL-00002"}),  # no interaction_id: id suffix
        SimpleNamespace(metadata={"call_id": "CALL-00003", "interaction_id": 103}),
    ]
    conn = _Conn([(101, 2, date(2026, 5, 4)), (2, 5, None), (999, 1, date(2026, 5, 5))])
    assert pipeline.load_csat_surveys(docs, connect=lambda: conn) == {
        "CALL-00001": (2, "2026-05-04"),
        "CALL-00002": (5, None),
    }
    assert "csat_survey" in conn.sql[0]


def test_ingest_runs_the_csat_step_after_every_ingest(monkeypatch, tmp_path, docs, fake_embeddings):
    """`pipeline.ingest` ends with the CSAT step, so a rebuilt table or a re-ingest keeps scores."""
    from langchain_core.documents import Document

    from retrieval.lancedb_backend import LanceDBRetriever

    retriever = LanceDBRetriever(uri=str(tmp_path / "lance"), embeddings=fake_embeddings)
    surveys = surveys_for(docs)
    documents = [Document(page_content=d["text"], metadata={**d["metadata"], "call_id": d["call_id"]}) for d in docs]

    monkeypatch.setattr(pipeline, "load_synthetic_data", lambda: [])
    monkeypatch.setattr(pipeline, "transcripts_to_documents", lambda _t: documents)
    monkeypatch.setattr(pipeline, "get_retriever", lambda embeddings=None: retriever)
    monkeypatch.setattr(pipeline, "load_csat_surveys", lambda _docs: surveys)
    monkeypatch.setattr("rag.embeddings.get_embeddings", lambda: fake_embeddings)

    pipeline.ingest(use_cache=False)
    assert retriever.count({"csat_score": {"gte": 1}}) == len(surveys)
    pipeline.ingest(use_cache=False)  # idempotent
    assert retriever.count({"csat_score": {"gte": 1}}) == len(surveys)
    retriever.reset()
    pipeline.ingest(use_cache=False)  # --reset then --ingest
    assert retriever.count({"csat_score": {"gte": 1}}) == len(surveys)
    assert retriever.count() == len(docs)


def test_pgvector_ingest_does_not_run_the_csat_step(monkeypatch, docs):
    """Only a retriever with `set_csat` gets the step; the pgvector backend has none."""
    assert not hasattr(PgVectorRetriever(store=object(), connect=lambda: None), "set_csat")
    assert hasattr(get_retriever("lancedb", embeddings=object()), "set_csat")


def test_duckdb_reader_refuses_a_csat_filter():
    from retrieval.duckdb_lance import where_to_sql

    with pytest.raises(ValueError, match="LanceDB backend"):
        where_to_sql({"csat_score": {"lte": 2}})


def test_snapshot_reader_cannot_write_scores_and_a_tag_includes_them(scored, make_retriever, docs):
    r, surveys = scored
    ref = r.tag_seed("csat")
    reader = make_retriever(as_of=ref)
    with pytest.raises(ValueError, match="read-only"):
        reader.set_csat(surveys)
    assert reader.count({"csat_score": {"gte": 1}}) == len(surveys)
