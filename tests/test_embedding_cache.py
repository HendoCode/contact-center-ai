"""The shared on-disk embedding cache (rag/embedding_cache.py) and `make ingest` using it."""

import json

import pytest

from rag import embedding_cache
from rag.embedding_cache import CachedEmbeddings


class Counting:
    model = "fake-embed"

    def __init__(self):
        self.embedded = []

    def embed_documents(self, texts):
        self.embedded.extend(texts)
        return [[float(len(t)), 1.0] for t in texts]

    def embed_query(self, text):
        return [float(len(text)), 0.0]


@pytest.fixture
def cache_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(embedding_cache, "CACHE_DIR", tmp_path / "embeddings")
    monkeypatch.setattr(embedding_cache, "LEGACY_CACHE_DIR", tmp_path / "bench-embeddings")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "ollama")
    return tmp_path


def test_cache_file_is_per_model_under_the_neutral_dir(cache_dirs):
    assert embedding_cache.cache_file_for(Counting()) == cache_dirs / "embeddings" / "ollama_fake-embed.jsonl"


def test_cache_hit_skips_embedding(cache_dirs, capsys):
    inner = Counting()
    cache = embedding_cache.cache_file_for(inner)
    CachedEmbeddings(inner, cache_file=cache).warm_documents(["a", "bb"])
    assert inner.embedded == ["a", "bb"]

    again = Counting()
    CachedEmbeddings(again, cache_file=cache, label="ingest").warm_documents(["a", "bb", "ccc"])
    assert again.embedded == ["ccc"]
    assert f"ingest: 2 document embeddings loaded from {cache}" in capsys.readouterr().out


def test_legacy_bench_cache_is_read_and_new_vectors_go_to_the_new_dir(cache_dirs, capsys):
    name = "ollama_fake-embed.jsonl"
    legacy = cache_dirs / "bench-embeddings" / name
    legacy.parent.mkdir()
    legacy.write_text(json.dumps({"h": embedding_cache._text_key("old"), "v": [9.0, 9.0]}) + "\n")
    inner = Counting()
    cached = CachedEmbeddings(inner, cache_file=embedding_cache.cache_file_for(inner))
    assert cached.embed_documents(["old", "new"]) == [[9.0, 9.0], [3.0, 1.0]]
    assert inner.embedded == ["new"]
    assert str(legacy) in capsys.readouterr().out
    new_file = cache_dirs / "embeddings" / name
    assert [json.loads(line)["h"] for line in new_file.read_text().splitlines()] == \
        [embedding_cache._text_key("new")]


def test_no_cache_file_reads_and_writes_nothing(cache_dirs):
    inner = Counting()
    CachedEmbeddings(inner).warm_documents(["a"])
    assert not (cache_dirs / "embeddings").exists()


class FakeRetriever:
    name = "fake"

    def __init__(self, embeddings):
        self.embeddings, self.rows = embeddings, []

    def ingest(self, docs):
        vectors = self.embeddings.embed_documents([d["text"] for d in docs])
        self.rows = list(zip(docs, vectors))
        return len(docs)

    def count(self):
        return len(self.rows)


def _ingest(monkeypatch, inner, **kw):
    import rag.embeddings
    import rag.pipeline as pipeline

    transcripts = [{"call_id": f"CALL-{i}", "full_text": f"text {i}", "date": "2026-01-01",
                    "duration_seconds": 60, "category": "c", "outcome": "o", "member_id": "M",
                    "agent_id": "A"} for i in range(3)]
    made = {}
    monkeypatch.setattr(pipeline, "load_synthetic_data", lambda: transcripts)
    monkeypatch.setattr(rag.embeddings, "get_embeddings", lambda: inner)
    monkeypatch.setattr(pipeline, "get_retriever",
                        lambda embeddings=None: made.setdefault("r", FakeRetriever(embeddings)))
    pipeline.ingest(**kw)
    return made["r"]


def test_ingest_fills_then_reuses_the_cache(cache_dirs, monkeypatch, capsys):
    first = Counting()
    r = _ingest(monkeypatch, first)
    assert sorted(first.embedded) == ["text 0", "text 1", "text 2"] and r.count() == 3
    second = Counting()
    r = _ingest(monkeypatch, second)
    assert second.embedded == [] and r.count() == 3
    assert "ingest: 3 document embeddings loaded from" in capsys.readouterr().out


def test_ingest_no_cache_embeds_everything_and_writes_nothing(cache_dirs, monkeypatch):
    _ingest(monkeypatch, Counting())
    inner = Counting()
    _ingest(monkeypatch, inner, use_cache=False)
    assert len(inner.embedded) == 3
