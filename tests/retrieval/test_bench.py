"""The R3 bench end to end on the 50-document fixture with the fake embedder (offline)."""

import json

import pytest

import rag.embeddings
from retrieval import bench
from retrieval.base import Hit
from tools.results import validate


def fixture_queries(docs):
    """Labels derived from the fixture's metadata, one or more per query type."""
    def where_cat(cat, **extra):
        return sorted(d["call_id"] for d in docs if d["metadata"]["category"] == cat
                      and all(d["metadata"][k] == v for k, v in extra.items()))

    def q(i, typ, text, relevant, where=None):
        return {"id": f"t{i}", "type": typ, "query": text, "where": where,
                "relevant": relevant, "derivation": "fixture"}

    return [
        q(1, "topical", "unauthorized card charge dispute", where_cat("fraud_dispute")),
        q(2, "topical", "auto loan rates and the application process", where_cat("loan_inquiry")),
        q(3, "exact", "zq007", ["CALL-00007"]),
        q(4, "exact", "zq042", ["CALL-00042"]),
        q(5, "filter", "unauthorized card charge", where_cat("fraud_dispute", outcome="escalated"),
          where={"outcome": "escalated"}),
        q(6, "paraphrase", "somebody took money with my plastic", where_cat("fraud_dispute")),
    ]


class VectorOnly:
    """A stand-in backend that, like pgvector, supports only mode='vector'."""

    name = "stub"

    def __init__(self):
        self.ids: list[str] = []

    def ingest(self, docs):
        self.ids = [d["call_id"] for d in docs]
        return len(self.ids)

    def search(self, query, k=5, where=None, mode="vector"):
        if mode != "vector":
            raise NotImplementedError(f"stub supports mode='vector' only, not {mode!r}")
        return [Hit(call_id=c, text="", score=1.0) for c in self.ids[:k]]

    def get_by_id(self, call_id):
        return None

    def count(self):
        return len(self.ids)


OPENERS = {"lancedb": bench.open_lancedb, "stub": lambda emb, workdir: bench.Store(VectorOnly())}


@pytest.fixture
def metrics(docs, fake_embeddings, tmp_path):
    return bench.run_bench(docs, fixture_queries(docs), fake_embeddings,
                           backends=["lancedb", "stub"], repeats=2, openers=OPENERS,
                           workdir=tmp_path)


def test_bench_scores_every_backend_and_mode(metrics):
    for mode in bench.MODES:
        row = metrics[f"lancedb/{mode}"]
        assert row["status"] == "ok"
        for key in ("recall@5", "recall@10", "mrr@10"):
            assert 0.0 <= row[key] <= 1.0
        assert 0 < row["p50_ms"] <= row["p95_ms"]
        assert row["ingest_s"] > 0 and 0 < row["index_build_s"] <= row["ingest_s"]
        assert set(row["recall@10_by_type"]) == set(bench.QUERY_TYPES)
    assert metrics["embed_unique_docs"] == 50


def test_fts_finds_exact_tokens(metrics):
    fts = metrics["lancedb/fts"]
    assert fts["recall@10_by_type"]["exact"] == 1.0
    assert fts["mrr@10_by_type"]["exact"] == 1.0


def test_unsupported_modes_are_recorded_not_fatal(metrics):
    assert metrics["stub/vector"]["status"] == "ok"
    assert metrics["stub/vector"]["index_build_s"] is None
    for mode in ("fts", "hybrid"):
        assert metrics[f"stub/{mode}"]["status"].startswith("not supported")


def test_bench_store_is_temporary(docs, fake_embeddings, tmp_path):
    bench.run_bench(docs, fixture_queries(docs), fake_embeddings, backends=["lancedb"],
                    repeats=1, workdir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_bench_cli_writes_valid_json_and_prints_table(docs, fake_embeddings, tmp_path, monkeypatch,
                                                      capsys):
    labels = tmp_path / "queries.jsonl"
    labels.write_text("".join(json.dumps(q) + "\n" for q in fixture_queries(docs)))
    monkeypatch.setattr(bench, "load_corpus", lambda: docs)
    monkeypatch.setattr(bench, "RESULTS_ROOT", tmp_path / "results")
    monkeypatch.setattr(rag.embeddings, "get_embeddings", lambda: fake_embeddings)

    assert bench.main(["--backends", "lancedb", "--scale", "2", "--repeats", "1",
                       "--run", "fixture", "--hardware", "ci", "--labels", str(labels)]) == 0

    [out] = (tmp_path / "results" / "retrieval").glob("*_fixture.json")
    record = json.loads(out.read_text())
    assert validate(record) == []
    assert record["params"]["rows"] == 100 and record["params"]["scale"] == 2
    assert "SYNTHETIC SCALE-UP" in record["notes"]
    readme = (tmp_path / "results" / "retrieval" / "README.md").read_text()
    printed = capsys.readouterr().out
    assert "| lancedb/hybrid | ok |" in printed and "| lancedb/hybrid | ok |" in readme
