"""Offline tests for the R3 benchmark's metric functions, scale-up and results contract."""

import json

import pytest

from retrieval.bench import (
    CachedEmbeddings,
    percentile,
    reciprocal_rank,
    recall_at_k,
    scale_up,
)
from tools.results import main as results_main
from tools.results import render, validate

# ── recall@k (capped) and MRR on hand-built rankings ──────────────────────────

RANKED = ["a", "x", "b", "y", "z", "c", "q", "r", "s", "t"]


def test_recall_counts_relevant_in_top_k_over_capped_denominator():
    # 2 of 3 relevant in the top 5; denominator min(5, 3) = 3.
    assert recall_at_k(RANKED, {"a", "b", "c"}, 5) == pytest.approx(2 / 3)
    # all 3 within the top 10.
    assert recall_at_k(RANKED, {"a", "b", "c"}, 10) == 1.0


def test_recall_caps_denominator_at_k_for_large_relevant_sets():
    relevant = {f"r{i}" for i in range(100)}
    ranked = [f"r{i}" for i in range(4)] + ["x"] * 6
    assert recall_at_k(ranked, relevant, 5) == pytest.approx(4 / 5)
    assert recall_at_k(ranked, relevant, 10) == pytest.approx(4 / 10)


def test_recall_handles_short_and_empty_result_lists():
    assert recall_at_k([], {"a"}, 5) == 0.0
    assert recall_at_k(["a"], {"a", "b"}, 10) == 0.5
    with pytest.raises(ValueError):
        recall_at_k(["a"], set(), 5)


def test_reciprocal_rank_is_first_relevant_position():
    assert reciprocal_rank(RANKED, {"a"}) == 1.0
    assert reciprocal_rank(RANKED, {"b", "c"}) == pytest.approx(1 / 3)
    assert reciprocal_rank(RANKED, {"nope"}) == 0.0
    assert reciprocal_rank(RANKED, {"c"}, k=5) == 0.0  # beyond the cut-off


def test_percentile_interpolates_between_order_statistics():
    assert percentile([5.0], 95) == 5.0
    assert percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)
    assert percentile(list(range(1, 101)), 95) == pytest.approx(95.05)
    with pytest.raises(ValueError):
        percentile([], 50)


# ── scale-up and embedding cache ──────────────────────────────────────────────

def test_scale_up_replicates_with_new_ids_and_extends_relevant_sets():
    docs = [{"call_id": f"C{i}", "text": f"t{i}", "metadata": {"call_id": f"C{i}"}}
            for i in range(3)]
    queries = [{"id": "q1", "relevant": ["C1"]}]
    big_docs, big_queries = scale_up(docs, queries, 4)
    ids = [d["call_id"] for d in big_docs]
    assert len(ids) == 12 and len(set(ids)) == 12
    assert all(d["metadata"]["call_id"] == d["call_id"] for d in big_docs)
    assert sorted(big_queries[0]["relevant"]) == ["C1", "C1~r001", "C1~r002", "C1~r003"]
    assert scale_up(docs, queries, 1) == (docs, queries)
    with pytest.raises(ValueError):
        scale_up(docs, queries, 0)


def test_cached_embeddings_embed_each_unique_text_once():
    class Counting:
        def __init__(self):
            self.docs, self.queries = [], []

        def embed_documents(self, texts):
            self.docs += texts
            return [[float(len(t))] for t in texts]

        def embed_query(self, text):
            self.queries.append(text)
            return [0.0]

    inner = Counting()
    cached = CachedEmbeddings(inner)
    n, _ = cached.warm_documents(["a", "bb", "a"])
    assert n == 2
    assert cached.embed_documents(["bb", "a", "ccc"]) == [[2.0], [1.0], [3.0]]
    assert inner.docs == ["a", "bb", "ccc"]
    cached.warm_queries(["q", "q"])
    cached.embed_query("q")
    assert inner.queries == ["q"]


# ── §4.3 results contract and renderer ────────────────────────────────────────

def _record(**over):
    rec = {
        "date": "2026-10-05", "git_sha": "abc123", "area": "retrieval", "run": "bench-test",
        "hardware": "test box", "versions": {"python": "3.12.0"}, "params": {"k": 10},
        "metrics": {
            "lancedb/fts": {"status": "ok", "recall@10": 0.5, "index_build_s": None,
                            "recall@10_by_type": {"exact": 1.0}},
            "pgvector/fts": {"status": "not supported"},
            "embed_docs_s": 1.25,
        },
        "notes": "",
    }
    rec.update(over)
    return rec


def test_validate_accepts_contract_record():
    assert validate(_record()) == []


@pytest.mark.parametrize("over, fragment", [
    ({"date": "Oct 5"}, "YYYY-MM-DD"),
    ({"area": "nope"}, "area"),
    ({"versions": {}}, "python"),
    ({"metrics": {}}, "metrics is empty"),
    ({"metrics": []}, "must be dict"),
    ({"git_sha": " "}, "empty"),
])
def test_validate_rejects_contract_violations(over, fragment):
    assert any(fragment in e for e in validate(_record(**over)))


def test_validate_reports_missing_keys():
    rec = _record()
    del rec["hardware"]
    assert validate(rec) == ["missing key 'hardware'"]


def test_render_without_runs_says_not_run(tmp_path):
    text = render("retrieval", tmp_path).read_text()
    assert "not run" in text


def test_render_tables_rows_breakdowns_and_scalars(tmp_path):
    area = tmp_path / "retrieval"
    area.mkdir()
    (area / "2026-10-05_bench-test.json").write_text(json.dumps(_record()))
    text = render("retrieval", tmp_path).read_text()
    assert "| config | status | recall@10 | index_build_s |" in text
    assert "| lancedb/fts | ok | 0.500 | n/a |" in text
    assert "| pgvector/fts | not supported | n/a | n/a |" in text
    assert "**recall@10_by_type**" in text and "| lancedb/fts | 1.000 |" in text
    assert "- embed_docs_s: 1.250" in text


def test_render_refuses_invalid_run_json(tmp_path):
    area = tmp_path / "retrieval"
    area.mkdir()
    (area / "bad.json").write_text(json.dumps(_record(date="yesterday")))
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        render("retrieval", tmp_path)


def test_validate_cli_exit_status(tmp_path, capsys):
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_text(json.dumps(_record()))
    bad.write_text(json.dumps(_record(area="x")))
    assert results_main(["validate", str(good)]) == 0
    assert results_main(["validate", str(good), str(bad)]) == 1
