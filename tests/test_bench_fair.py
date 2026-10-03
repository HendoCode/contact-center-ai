"""Fair x80 comparison: replica jitter, index/search knobs and their plumbing, table columns.

Offline except the last test, which needs a Postgres with pgvector (`-m integration`,
PGVECTOR_TEST=1, DATABASE_URL pointing at it).
"""

import functools
import math
import os

import pytest

from retrieval import bench
from tools.results import metrics_tables


def _cos(a, b):
    return sum(x * y for x, y in zip(a, b)) / math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))


def test_jitter_is_deterministic_small_and_unit_length():
    v = [0.6, 0.8] + [0.0] * 62
    a, b = bench.jitter(v, "text", 1), bench.jitter(v, "text", 1)
    assert a == b  # same seed every run and backend
    assert bench.jitter(v, "text", 2) != a and bench.jitter(v, "other", 1) != a
    assert abs(math.sqrt(sum(x * x for x in a)) - 1) < 1e-9
    assert 0.99 < _cos(v, a) < 1.0


def test_only_repeats_are_jittered_and_queries_never():
    class Inner:
        def embed_documents(self, texts):
            return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

        def embed_query(self, text):
            return [1.0, 0.0, 0.0, 0.0]

    j = bench.JitteredEmbeddings(Inner())
    first = j.embed_documents(["a", "b"])
    again = j.embed_documents(["a", "a"])
    assert first == [[1.0, 0.0, 0.0, 0.0]] * 2  # first occurrences untouched
    assert again[0] != first[0] and again[0] != again[1]  # 2nd and 3rd "a" differ from each other
    assert j.embed_query("a") == [1.0, 0.0, 0.0, 0.0]
    fresh = bench.JitteredEmbeddings(Inner())  # one per backend: the same vectors again
    fresh.embed_documents(["a", "b"])
    assert fresh.embed_documents(["a", "a"]) == again


def test_hnsw_sql_uses_the_index_expression_and_parameterised_filters():
    sql, params = bench.hnsw_sql(768, {"category": {"in": ["fraud", "loan"]}, "date": {"gte": "2026-01-01"}})
    assert "ORDER BY (embedding::vector(768)) <=> %s::vector(768) LIMIT %s" in sql
    assert "collection_id = %s::uuid" in sql
    assert "(cmetadata->>%s) = ANY(%s)" in sql and "(cmetadata->>%s) >= %s" in sql
    assert params == ["category", ["fraud", "loan"], "date", "2026-01-01"]
    assert "fraud" not in sql  # values only as parameters


def _fake_main(monkeypatch, tmp_path, argv):
    import rag.embeddings

    class Emb:
        def embed_documents(self, texts):
            return [[1.0, float(len(t))] for t in texts]

        def embed_query(self, text):
            return [1.0, float(len(text))]

    seen = {}

    def spy(embeddings, workdir, cfg=None):
        seen["cfg"], seen["jitter"] = cfg, isinstance(embeddings, bench.JitteredEmbeddings)
        raise RuntimeError("stop after plumbing")

    monkeypatch.setattr(rag.embeddings, "get_embeddings", lambda: Emb())
    monkeypatch.setattr(bench, "RESULTS_ROOT", tmp_path)
    monkeypatch.setattr(bench, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(bench, "open_lancedb", spy)
    monkeypatch.setattr(bench, "OPENERS", {"lancedb": spy})
    assert bench.main(["--backends", "lancedb", "--repeats", "1", *argv]) == 0
    return seen, next((tmp_path / "retrieval").glob("*.json")).read_text()


def test_flags_reach_the_openers_and_the_results(monkeypatch, tmp_path):
    seen, record = _fake_main(monkeypatch, tmp_path, [
        "--scale", "2", "--nprobes", "30", "--refine-factor", "5", "--num-partitions", "64",
        "--hnsw-ef-search", "80", "--sweep", "nprobes=10,20"])
    cfg = seen["cfg"]
    assert (cfg.nprobes, cfg.refine_factor, cfg.num_partitions, cfg.hnsw_ef_search) == (30, 5, 64, 80)
    assert cfg.sweep == ({"nprobes": 10}, {"nprobes": 20}) and cfg.pgvector_index == "hnsw"
    assert seen["jitter"] is True  # default on for --scale > 1
    assert '"jitter_eps": 0.05' in record and '"nprobes": 30' in record


def test_x1_default_has_no_jitter(monkeypatch, tmp_path):
    seen, record = _fake_main(monkeypatch, tmp_path, [])
    assert seen["jitter"] is False and '"jitter_eps": 0.0' in record


@pytest.mark.parametrize("sweep", [["nprobes="], ["probes=1,2"], ["nprobes=0"], ["nprobes=a"],
                                   ["nprobes=1", "nprobes=2"]])
def test_bad_sweep_is_refused(sweep):
    with pytest.raises(SystemExit):
        bench.main([a for v in sweep for a in ("--sweep", v)])


def test_sweep_combines_into_a_grid():
    assert bench.parse_sweep(["nprobes=20,50", "refine_factor=1,20"]) == (
        {"nprobes": 20, "refine_factor": 1}, {"nprobes": 20, "refine_factor": 20},
        {"nprobes": 50, "refine_factor": 1}, {"nprobes": 50, "refine_factor": 20})
    assert bench.parse_sweep([]) == ()


def test_grid_flags_and_new_knobs_reach_the_config(monkeypatch, tmp_path):
    seen, record = _fake_main(monkeypatch, tmp_path, [
        "--sweep", "nprobes=20,50", "--sweep", "refine_factor=1,20", "--num-sub-vectors", "48",
        "--pgvector-parallel-workers", "0"])
    cfg = seen["cfg"]
    assert len(cfg.sweep) == 4 and cfg.num_sub_vectors == 48 and cfg.pgvector_parallel_workers == 0
    assert '"num_sub_vectors": 48' in record and '"pgvector_parallel_workers": 0' in record


def test_sweep_table_is_compact():
    ok = {"status": "ok", "recall@10": 0.5, "mrr@10": 0.6, "p50_ms": 12.34, "p95_ms": 20.0}
    table = bench.sweep_table({"lancedb/vector": ok, "lancedb[nprobes=20,refine_factor=5]/vector": ok,
                               "lancedb/fts": ok, "pgvector-hnsw/vector": {"status": "failed: DiskFull: x"},
                               "embed_docs_s": 1.0})
    assert table.splitlines() == [
        "| config | recall@10 | mrr@10 | p50 ms | p95 ms |", "|---|---|---|---|---|",
        "| lancedb/vector | 0.500 | 0.600 | 12.3 | 20.0 |",
        "| lancedb[nprobes=20,refine_factor=5]/vector | 0.500 | 0.600 | 12.3 | 20.0 |",
        "| pgvector-hnsw/vector | failed | | | |"]


def test_shared_memory_exhaustion_gets_the_one_line_fix():
    shm = OSError("could not resize shared memory segment /PostgreSQL.1298614002 to 533761504 bytes: "
                  "No space left on device" + " x" * 500)
    err = bench.explain_build_error(shm)
    assert str(err).startswith("HNSW build ran out of /dev/shm: raise the db container's shm_size")
    assert "--pgvector-parallel-workers 0" in str(err) and len(str(err)) < 400
    other = ValueError("something else")
    assert bench.explain_build_error(other) is other


def test_results_table_shows_index_and_search_columns():
    record = {"metrics": {
        "pgvector/vector": {"status": "ok", "index": "none (exact scan)", "search": "exact", "recall@10": 0.8},
        "pgvector-hnsw/vector": {"status": "ok", "index": "hnsw (cosine, m=16, ef_construction=64)",
                                 "search": "ef_search=40", "recall@10": 0.79}}}
    table = metrics_tables(record)
    assert table.splitlines()[0] == "| config | status | index | search | recall@10 |"
    assert "| pgvector-hnsw/vector | ok | hnsw (cosine, m=16, ef_construction=64) | ef_search=40 | 0.790 |" in table


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("PGVECTOR_TEST"), reason="set PGVECTOR_TEST=1 with a pgvector Postgres")
def test_pgvector_hnsw_variant_on_a_real_postgres(tmp_path):
    from tests.retrieval.conftest import FakeEmbeddings

    docs, queries = bench.scale_up(bench.load_corpus(), bench.load_queries(), 2)
    cfg = bench.IndexConfig()
    m = bench.run_bench(docs, queries, FakeEmbeddings(), backends=["pgvector"], modes=["vector"], repeats=1,
                        openers={"pgvector": functools.partial(bench.open_pgvector, cfg=cfg)},
                        jitter_eps=bench.JITTER_EPS, workdir=tmp_path)
    exact, hnsw = m["pgvector/vector"], m["pgvector-hnsw/vector"]
    assert exact["status"] == hnsw["status"] == "ok", (exact, hnsw)
    assert hnsw["index"].startswith("hnsw") and hnsw["index_build_s"] > 0
    assert abs(hnsw["recall@10"] - exact["recall@10"]) < 0.1
