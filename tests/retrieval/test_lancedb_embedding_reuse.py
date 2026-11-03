"""Stored vectors: the Lance table remembers which text and model made each vector, and
`rag.pipeline.ingest` reuses them, so a re-seed with nothing new embeds nothing.

Offline: counting fake embeddings, a temp Lance directory, stand-ins for Postgres. Between
ingests the file cache under `.cache/embeddings/` is wiped, which is what a rebuilt container
looks like; only the Lance table (the `lance-data` volume) survives.
"""

import hashlib
import shutil

import pytest
from langchain_core.documents import Document

from rag import embedding_cache, pipeline
from retrieval.lancedb_backend import LanceDBRetriever

PROVENANCE = ("content_hash", "embedding_model")


class CountingEmbeddings:
    """Deterministic vectors that depend on the model name and dimension; records every text embedded."""

    def __init__(self, model="embed-a", dim=16):
        self.model, self.dim, self.embedded = model, dim, []

    def _vec(self, text):
        digest = hashlib.sha256(f"{self.model}|{text}".encode()).digest()
        return [digest[i % len(digest)] / 255.0 for i in range(self.dim)]

    def embed_documents(self, texts):
        self.embedded.extend(texts)
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


class FakePgvector:
    """Stands in for the pgvector retriever: embeds through the object ingest hands it, keeps the result."""

    name = "pgvector"

    def __init__(self, embeddings):
        self.embeddings, self.vectors = embeddings, {}

    def ingest(self, docs):
        for d, v in zip(docs, self.embeddings.embed_documents([d["text"] for d in docs])):
            self.vectors[d["call_id"]] = v
        return len(docs)

    def count(self):
        return len(self.vectors)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(embedding_cache, "CACHE_DIR", tmp_path / "embeddings")
    monkeypatch.setattr(embedding_cache, "LEGACY_CACHE_DIR", tmp_path / "bench-embeddings")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setenv("LANCE_URI", str(tmp_path / "lance"))
    monkeypatch.setattr(pipeline, "load_synthetic_data", lambda: [])
    monkeypatch.setattr(pipeline, "load_csat_surveys", lambda _docs: {})
    return tmp_path


def lance():
    return LanceDBRetriever()


def run_ingest(env, monkeypatch, docs, embedder, backend="lancedb", wipe_file_cache=True, **kw):
    """One `rag.pipeline.ingest` with `embedder` as the configured model; returns the retriever it wrote."""
    import rag.embeddings

    if wipe_file_cache:
        shutil.rmtree(env / "embeddings", ignore_errors=True)
    documents = [Document(page_content=d["text"], metadata={**d["metadata"], "call_id": d["call_id"]})
                 for d in docs]
    made = {}

    def get_retriever(embeddings=None):
        made["r"] = LanceDBRetriever(embeddings=embeddings) if backend == "lancedb" else FakePgvector(embeddings)
        return made["r"]

    monkeypatch.setattr(pipeline, "transcripts_to_documents", lambda _t: documents)
    monkeypatch.setattr(rag.embeddings, "get_embeddings", lambda: embedder)
    monkeypatch.setattr(pipeline, "get_retriever", get_retriever)
    pipeline.ingest(**kw)
    return made["r"]


def recorded(r):
    rows = r._table().search().select(["call_id", *PROVENANCE]).limit(10_000).to_arrow().to_pylist()
    return {x["call_id"]: (x["content_hash"], x["embedding_model"]) for x in rows}


def vectors(r):
    rows = r._table().search().select(["call_id", "vector"]).limit(10_000).to_arrow().to_pylist()
    return {x["call_id"]: x["vector"] for x in rows}


# ── the table states what produced each vector ─────────────────────────────────

def test_each_row_records_the_text_hash_and_the_model(env, monkeypatch, docs):
    r = run_ingest(env, monkeypatch, docs, CountingEmbeddings("embed-a"))
    rows = recorded(r)
    assert len(rows) == len(docs)
    assert {m for _h, m in rows.values()} == {"ollama:embed-a"}
    assert rows[docs[0]["call_id"]][0] == hashlib.sha256(docs[0]["text"].encode()).hexdigest()


# ── an unchanged re-seed embeds nothing ────────────────────────────────────────

def test_second_ingest_makes_zero_embed_calls(env, monkeypatch, docs, capsys):
    first = CountingEmbeddings()
    r = run_ingest(env, monkeypatch, docs, first)
    assert sorted(first.embedded) == sorted(d["text"] for d in docs)
    before = vectors(r)

    again = CountingEmbeddings()  # a rebuilt container: new process, empty file cache
    r = run_ingest(env, monkeypatch, docs, again)
    assert again.embedded == []
    assert r.count() == len(docs) and vectors(r) == before
    assert f"{len(docs)} of {len(docs)} vectors reused from the Lance table" in capsys.readouterr().out


def test_no_cache_embeds_everything_again(env, monkeypatch, docs):
    run_ingest(env, monkeypatch, docs, CountingEmbeddings())
    fresh = CountingEmbeddings()
    run_ingest(env, monkeypatch, docs, fresh, use_cache=False)
    assert len(fresh.embedded) == len(docs)


# ── only new or changed transcripts are embedded ───────────────────────────────

def test_a_changed_transcript_is_reembedded_alone(env, monkeypatch, docs):
    r = run_ingest(env, monkeypatch, docs, CountingEmbeddings())
    before = vectors(r)

    changed = [dict(d) for d in docs]
    changed[3]["text"] += " The member also asked about a card limit."
    added = {"call_id": "CALL-99999", "text": "A brand new call about a wire transfer.",
             "metadata": {"category": "wire", "outcome": "resolved", "date": "2026-02-01"}}
    inner = CountingEmbeddings()
    r = run_ingest(env, monkeypatch, changed + [added], inner)

    assert sorted(inner.embedded) == sorted([changed[3]["text"], added["text"]])
    after = vectors(r)
    assert after[docs[3]["call_id"]] != before[docs[3]["call_id"]]
    assert all(after[d["call_id"]] == before[d["call_id"]] for i, d in enumerate(docs) if i != 3)
    assert recorded(r)[docs[3]["call_id"]][0] == hashlib.sha256(changed[3]["text"].encode()).hexdigest()


# ── a different model replaces every vector, behind a tag ──────────────────────

@pytest.mark.parametrize("new_dim", [16, 24], ids=["same-dimension", "new-dimension"])
def test_a_model_change_reembeds_everything_and_keeps_the_old_version(env, monkeypatch, docs, new_dim):
    old = CountingEmbeddings("embed-a", dim=16)
    r = run_ingest(env, monkeypatch, docs, old)
    old_vectors = vectors(r)
    old_tags = set(r._table().tags.list())
    assert any(t.startswith("seed-") for t in old_tags)

    new = CountingEmbeddings("embed-b", dim=new_dim)
    r = run_ingest(env, monkeypatch, docs, new)
    assert sorted(new.embedded) == sorted(d["text"] for d in docs)
    assert {m for _h, m in recorded(r).values()} == {"ollama:embed-b"}  # one model in the column
    assert r.count() == len(docs)
    assert r._table().schema.field("vector").type.list_size == new_dim

    # The previous seed tag still names the embed-a table, and rolling back restores it.
    old_seed = max(t for t in old_tags if t.startswith("seed-"))
    assert LanceDBRetriever(as_of=old_seed).count() == len(docs)
    r.rollback(old_seed)
    assert {m for _h, m in recorded(r).values()} == {"ollama:embed-a"}
    assert vectors(r) == old_vectors


def test_a_model_change_tags_an_untagged_version_first(env, monkeypatch, docs):
    r = LanceDBRetriever(embeddings=CountingEmbeddings("embed-a"))
    r.ingest(docs)  # written directly: no seed tag
    assert not r._table().tags.list()

    r2 = LanceDBRetriever(embeddings=CountingEmbeddings("embed-b", dim=8))
    r2.ingest(docs)
    tags = r2._table().tags.list()
    assert list(tags) == ["pre-model-change-1"]
    assert {m for _h, m in recorded(r2).values()} == {"ollama:embed-b"}
    r2.rollback("pre-model-change-1")
    assert r2._table().schema.field("vector").type.list_size == 16


def test_a_model_change_refuses_a_partial_ingest(env, monkeypatch, docs):
    run_ingest(env, monkeypatch, docs, CountingEmbeddings("embed-a"))
    with pytest.raises(ValueError, match="mix two models"):
        run_ingest(env, monkeypatch, docs[:10], CountingEmbeddings("embed-b"))
    # Nothing was written: the table is still all embed-a.
    assert {m for _h, m in recorded(lance()).values()} == {"ollama:embed-a"}


# ── the pgvector rollback ingest reuses the same vectors ───────────────────────

def test_pgvector_ingest_reuses_the_lance_vectors(env, monkeypatch, docs):
    r = run_ingest(env, monkeypatch, docs, CountingEmbeddings())
    lance_vectors = vectors(r)

    inner = CountingEmbeddings()
    pg = run_ingest(env, monkeypatch, docs, inner, backend="pgvector")
    assert inner.embedded == []
    assert pg.count() == len(docs)
    assert {k: [round(x, 6) for x in v] for k, v in pg.vectors.items()} == \
        {k: [round(x, 6) for x in v] for k, v in lance_vectors.items()}


def test_pgvector_ingest_embeds_only_what_lance_does_not_hold(env, monkeypatch, docs):
    run_ingest(env, monkeypatch, docs, CountingEmbeddings("embed-a"))
    inner = CountingEmbeddings("embed-b")  # another model: nothing stored for it
    pg = run_ingest(env, monkeypatch, docs, inner, backend="pgvector")
    assert len(inner.embedded) == len(docs) and pg.count() == len(docs)


def test_pgvector_ingest_without_a_lance_table_just_embeds(env, monkeypatch, docs):
    inner = CountingEmbeddings()
    pg = run_ingest(env, monkeypatch, docs, inner, backend="pgvector")
    assert len(inner.embedded) == len(docs) and pg.count() == len(docs)


# ── a table from before the columns existed ────────────────────────────────────

def test_an_older_table_gets_the_columns_by_schema_evolution(env, monkeypatch, docs):
    r = run_ingest(env, monkeypatch, docs, CountingEmbeddings())
    table = r._table()
    table.drop_columns(list(PROVENANCE))
    assert not set(PROVENANCE) & set(table.schema.names)

    # Nothing says which model made those vectors, so they are not trusted: one full re-embed...
    inner = CountingEmbeddings()
    r = run_ingest(env, monkeypatch, docs, inner)
    assert len(inner.embedded) == len(docs)
    assert {m for _h, m in recorded(r).values()} == {"ollama:embed-a"}
    # ...after which the next one embeds nothing.
    again = CountingEmbeddings()
    run_ingest(env, monkeypatch, docs, again)
    assert again.embedded == []
