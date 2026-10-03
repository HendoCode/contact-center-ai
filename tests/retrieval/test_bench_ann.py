"""ANN quality at scale: nprobes reaches the plan, index types, the exact reference, ann_recall@10."""

import pytest

from retrieval import bench
from retrieval.lancedb_backend import LanceDBRetriever
from tests.retrieval.conftest import FakeEmbeddings, make_docs


def _indexed(tmp_path, **kw) -> LanceDBRetriever:
    r = LanceDBRetriever(uri=str(tmp_path / "lance"), embeddings=FakeEmbeddings(), table_name="t",
                         vector_index_min_rows=300, **kw)
    r.ingest(make_docs(600))
    return r


def test_nprobes_and_refine_factor_reach_the_query_plan(tmp_path):
    r = _indexed(tmp_path, nprobes=7)
    plan = r._query(r._table(), "card dispute", 10, None, "vector").explain_plan(True)
    assert "minimum_nprobes=7" in plan and "maximum_nprobes=Some(7)" in plan
    assert "KNNVectorDistance" not in plan  # no exact re-rank without refine_factor
    r.nprobes, r.refine_factor = 13, 4
    plan = r._query(r._table(), "card dispute", 10, None, "vector").explain_plan(True)
    assert "minimum_nprobes=13" in plan
    assert "ANNSubIndex: name=vector_idx, k=40" in plan and "KNNVectorDistance" in plan  # 10 x 4, re-ranked


@pytest.mark.parametrize("index_type,expected", [("ivf_pq", "IvfPq"), ("ivf_flat", "IvfFlat"),
                                                 ("ivf_hnsw_sq", "IvfHnswSq")])
def test_lance_index_types_build(tmp_path, index_type, expected):
    r = _indexed(tmp_path, index_type=index_type, num_partitions=4)
    types = {i.columns[0]: str(i.index_type) for i in r._table().list_indices()}
    assert expected.lower() in types["vector"].lower().replace("_", "")
    assert r.search("card dispute", k=5)  # and it answers


def test_unknown_index_type_is_refused():
    with pytest.raises(ValueError):
        LanceDBRetriever(uri="x", index_type="diskann")


def test_exact_reference_scores_exact_one_and_respects_filters():
    docs = make_docs(60)
    queries = [{"id": "q1", "query": "lost debit card replacement", "where": None},
               {"id": "q2", "query": "lost debit card replacement", "where": {"category": "fraud_dispute"}}]
    emb = FakeEmbeddings()
    ref = bench.ExactReference(docs, emb, queries)
    import numpy as np

    def exact(qid, where_cat=None):
        v = np.asarray(emb.embed_query("lost debit card replacement"))
        sims = [(float(np.dot(v, emb.embed_query(d["text"]))), d["call_id"]) for d in docs
                if where_cat is None or d["metadata"]["category"] == where_cat]
        return [c for _, c in sorted(sims, reverse=True)[:10]]

    assert ref.recall("q1", exact("q1")) == 1.0
    assert ref.recall("q2", exact("q2", "fraud_dispute")) == 1.0
    wrong = [d["call_id"] for d in docs if d["metadata"]["category"] == "loan_inquiry"][:10]
    assert ref.recall("q2", wrong) < 0.5
    assert ref.recall("unknown", wrong) is None


def test_ann_recall_and_latency_by_type_land_in_the_vector_row():
    docs = make_docs(40)
    queries = [{"id": "q1", "query": "auto loan rates", "where": None, "relevant": [docs[0]["call_id"]],
                "type": "topical"}]
    emb = FakeEmbeddings()
    ref = bench.ExactReference(docs, emb, queries)

    class Exactish:
        name = "fake"

        def search(self, query, k=5, where=None, mode="vector"):
            from retrieval.base import Hit
            import numpy as np

            v = np.asarray(emb.embed_query(query))
            top = sorted(docs, key=lambda d: -float(np.dot(v, emb.embed_query(d["text"]))))[:k]
            return [Hit(call_id=d["call_id"], text="", score=1.0, metadata={}) for d in top]

    rows = bench.score_modes(Exactish(), queries, ["vector", "fts"], 1, 0.1, None, reference=ref)
    assert rows["fake/vector"]["ann_recall@10"] == 1.0
    assert rows["fake/fts"]["ann_recall@10"] is None  # only vector mode has an exact reference
    assert set(rows["fake/vector"]["p50_ms_by_type"]) == {"topical"}


def test_ef_search_sweeps_pgvector_separately_from_the_lance_grid():
    grid = bench.parse_sweep(["nprobes=20,50", "ef_search=40,200"])
    lance, efs = bench.split_sweep(grid)
    assert lance == ({"nprobes": 20}, {"nprobes": 50}) and efs == (40, 200)


def test_index_description_names_the_lance_type(tmp_path):
    cfg = bench.IndexConfig(lance_index="ivf_flat", num_partitions=4, num_sub_vectors=16)
    store = bench.open_lancedb(FakeEmbeddings(), tmp_path, cfg)
    store.retriever.vector_index_min_rows = 300
    store.retriever.ingest(make_docs(600))
    assert store.index() == "ivf_flat (cosine, 4 partitions)"  # PQ knobs only shown for *_pq
