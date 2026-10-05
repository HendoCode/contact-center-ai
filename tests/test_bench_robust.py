"""The R3 bench at scale: batched ingest, short failure rows, partial results, disk cache.

Offline: fake backends and fake embeddings; no Postgres, Ollama or network.
"""

import json

import pytest

from retrieval import bench
from retrieval.base import Hit

HUGE = "INSERT INTO langchain_pg_embedding VALUES " + "(%(id_m0)s, [0.1, 0.2, 0.3]), " * 50_000


class Echo:
    def embed_documents(self, texts):
        return [[float(len(t)), 1.0] for t in texts]

    def embed_query(self, text):
        return [float(len(text)), 1.0]


class FakeRetriever:
    def __init__(self, name="fake", fail_ingest=False, fail_mode=None):
        self.name, self.fail_ingest, self.fail_mode = name, fail_ingest, fail_mode
        self.batches, self.docs = [], {}

    def ingest(self, docs):
        if self.fail_ingest:
            raise RuntimeError(HUGE)
        self.batches.append(len(docs))
        self.docs.update({d["call_id"]: d for d in docs})
        return len(docs)

    def search(self, query, k=5, where=None, mode="vector"):
        if mode == self.fail_mode:
            raise ValueError(HUGE)
        return [Hit(call_id=c, text="", score=1.0, metadata={}) for c in list(self.docs)[:k]]


def opener(retriever):
    return lambda embeddings, workdir: bench.Store(retriever)


DOCS = [{"call_id": f"C{i}", "text": f"doc {i}", "metadata": {}} for i in range(25)]
QUERIES = [{"id": "q1", "query": "doc", "where": None, "relevant": ["C0"], "type": "topical"}]


def test_short_error_is_one_line_and_bounded():
    msg = bench.short_error(RuntimeError(HUGE + "\n\n tail"))
    assert msg.startswith("RuntimeError: INSERT INTO") and msg.endswith("...[truncated]")
    assert len(msg) <= bench.ERROR_CHARS and "\n" not in msg


def test_ingest_runs_in_bounded_batches(capsys):
    r = FakeRetriever()
    rows = bench.bench_backend(bench.Store(r), DOCS, QUERIES, ["vector"], repeats=1, ingest_batch=10)
    assert r.batches == [10, 10, 5]
    assert rows["fake/vector"]["status"] == "ok"
    assert "ingest fake: batch 3/3 (5 rows)" in capsys.readouterr().out


def test_failing_backend_and_mode_keep_the_rest_and_stay_short(capsys):
    metrics = bench.run_bench(
        DOCS, QUERIES, Echo(), backends=["bad", "good"], modes=["vector", "fts"], repeats=1,
        openers={"bad": opener(FakeRetriever("bad", fail_ingest=True)),
                 "good": opener(FakeRetriever("good", fail_mode="fts"))})
    assert metrics["bad/vector"]["status"].startswith("failed: RuntimeError: INSERT INTO")
    assert metrics["bad/fts"]["status"] == metrics["bad/vector"]["status"]
    assert metrics["good/vector"]["status"] == "ok"
    assert metrics["good/fts"]["status"].startswith("failed: ValueError")
    for row in metrics.values():
        if isinstance(row, dict) and "status" in row:
            assert len(row["status"]) <= bench.ERROR_CHARS + len("failed: ")
    out = capsys.readouterr()
    assert max(len(line) for line in (out.out + out.err).splitlines()) < 400


def test_embedding_cache_skips_the_model_on_rerun(tmp_path, capsys):
    class Counting(Echo):
        calls = 0

        def embed_documents(self, texts):
            Counting.calls += len(texts)
            return super().embed_documents(texts)

    cache = tmp_path / "model.jsonl"
    first = bench.CachedEmbeddings(Counting(), cache_file=cache)
    first.warm_documents(["a", "bb", "a"])
    assert Counting.calls == 2 and cache.exists()
    again = bench.CachedEmbeddings(Counting(), cache_file=cache)
    n, _ = again.warm_documents(["a", "bb", "ccc"])
    assert Counting.calls == 3  # only "ccc" was embedded
    assert again.embed_documents(["a", "bb"]) == first.embed_documents(["a", "bb"])
    assert "2 document embeddings loaded from" in capsys.readouterr().out


def test_main_writes_results_even_when_a_backend_fails(tmp_path, monkeypatch, capsys):
    import rag.embeddings

    monkeypatch.setattr(rag.embeddings, "get_embeddings", lambda: Echo())
    monkeypatch.setattr(bench, "RESULTS_ROOT", tmp_path)
    monkeypatch.setattr("rag.embedding_cache.CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr("rag.embedding_cache.LEGACY_CACHE_DIR", tmp_path / "legacy")
    monkeypatch.setattr(bench, "OPENERS", {"pgvector": opener(FakeRetriever("pgvector", fail_ingest=True)),
                                           "lancedb": opener(FakeRetriever("lancedb"))})
    monkeypatch.setattr(bench, "postgres_versions", lambda: (_ for _ in ()).throw(OSError(HUGE)))
    assert bench.main(["--repeats", "1", "--run", "partial"]) == 0
    [out] = (tmp_path / "retrieval").glob("*_partial.json")
    record = json.loads(out.read_text())
    assert record["metrics"]["pgvector/vector"]["status"].startswith("failed: RuntimeError")
    assert record["metrics"]["lancedb/vector"]["status"] == "ok"
    assert record["params"]["ingest_batch"] == bench.INGEST_BATCH
    assert record["versions"]["postgres"].startswith("unknown (OSError")
    text = capsys.readouterr()
    assert max(len(line) for line in (text.out + text.err).splitlines()) < 400


@pytest.mark.parametrize("argv", [["--ingest-batch", "0"], ["--backends", "nope"]])
def test_bad_arguments_are_refused(argv):
    with pytest.raises(SystemExit):
        bench.main(argv)
