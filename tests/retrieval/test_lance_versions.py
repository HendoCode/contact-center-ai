"""Offline tests for the versioned LanceDB store: tags, time travel, rollback, retention."""

import time
from datetime import datetime

import pytest

from retrieval import lance_versions as lv
from retrieval.base import store_snapshot

QUERY = "unauthorized card charge dispute"


def _extra(n: int):
    return [{"call_id": f"NEW-{i:03d}", "text": f"Call {i}. member asks about a wire transfer.",
             "metadata": {"category": "wire", "outcome": "resolved", "date": "2026-09-01"}}
            for i in range(n)]


# ── tags ──────────────────────────────────────────────────────────────────────

def test_seed_tag_name_is_deduplicated():
    assert lv.seed_tag_name("abc1234", set()) == "seed-abc1234"
    assert lv.seed_tag_name("abc1234", {"seed-abc1234"}) == "seed-abc1234-2"
    assert lv.seed_tag_name("abc1234", {"seed-abc1234", "seed-abc1234-2"}) == "seed-abc1234-3"


def test_tag_seed_tags_the_newest_version_and_never_moves_an_existing_tag(retriever):
    first = retriever.tag_seed("abc1234")
    v1 = retriever.snapshot()["lance_version"]
    retriever.ingest(_extra(3))
    second = retriever.tag_seed("abc1234")
    assert (first, second) == ("seed-abc1234", "seed-abc1234-2")
    tags = retriever._table().tags.list()
    assert tags["seed-abc1234"]["version"] == v1  # untouched by the second ingest
    assert tags["seed-abc1234-2"]["version"] == retriever.snapshot()["lance_version"] > v1


def test_default_tag_uses_the_git_label(retriever, monkeypatch):
    monkeypatch.setattr(lv, "git_label", lambda: "feed123-dirty")
    assert retriever.tag_seed() == "seed-feed123-dirty"


def test_snapshot_names_version_tag_and_rows(retriever):
    assert retriever.snapshot() == {
        "backend": "lancedb", "lance_version": retriever._table().version, "lance_tag": None, "rows": 50}
    retriever.tag_seed("abc1234")
    assert retriever.snapshot()["lance_tag"] == "seed-abc1234"
    retriever.ingest(_extra(1))  # a newer, untagged version
    assert retriever.snapshot()["lance_tag"] is None


def test_snapshot_before_ingest_is_none(make_retriever):
    assert make_retriever().snapshot() is None


# ── time travel ───────────────────────────────────────────────────────────────

def test_as_of_reads_the_tagged_snapshot_and_is_read_only(retriever, make_retriever, docs):
    retriever.tag_seed("abc1234")
    retriever.ingest(_extra(5))
    assert retriever.count() == 55

    past = make_retriever(as_of="seed-abc1234")
    assert past.count() == 50
    assert past.get_by_id("NEW-000") is None
    assert {h.call_id for h in past.search(QUERY, k=50)}.isdisjoint({"NEW-000"})
    assert past.snapshot()["lance_tag"] == "seed-abc1234"
    with pytest.raises(ValueError, match="read-only"):
        past.ingest(_extra(1))
    for write in (past.reset, past.tag_seed, lambda: past.rollback("seed-abc1234")):
        with pytest.raises(ValueError, match="read-only"):
            write()

    assert make_retriever().count() == 55  # the default reader still sees the newest


def test_as_of_accepts_a_version_number(retriever, make_retriever):
    v = retriever.snapshot()["lance_version"]
    retriever.ingest(_extra(2))
    assert make_retriever(as_of=v).count() == 50


def test_as_of_unknown_tag_raises(retriever, make_retriever):
    with pytest.raises(Exception):  # noqa: B017 - the Lance error type is not public
        make_retriever(as_of="seed-nope").count()


# ── rollback ──────────────────────────────────────────────────────────────────

def test_rollback_restores_a_tag_and_keeps_history(retriever):
    retriever.tag_seed("abc1234")
    retriever.ingest(_extra(5))
    before = retriever.snapshot()["lance_version"]

    new = retriever.rollback("seed-abc1234")

    assert new > before  # a new version on top, not a rewrite
    assert retriever.count() == 50
    assert retriever.get_by_id("NEW-000") is None
    assert retriever.search(QUERY, mode="fts") and retriever.search(QUERY, mode="vector")
    # The rolled-back-from version is still there to check out.
    assert retriever.versions()[-1]["current"] and retriever._table().tags.list()["seed-abc1234"]
    from retrieval.lancedb_backend import LanceDBRetriever
    again = LanceDBRetriever(uri=retriever.uri, embeddings=retriever.embeddings, as_of=before)
    assert again.count() == 55


def test_rollback_unknown_tag_raises_and_changes_nothing(retriever):
    v = retriever.snapshot()["lance_version"]
    with pytest.raises(Exception):  # noqa: B017
        retriever.rollback("seed-nope")
    assert retriever.snapshot()["lance_version"] == v


def test_rollback_on_missing_table_raises(make_retriever):
    with pytest.raises(ValueError, match="ingest first"):
        make_retriever().rollback("seed-abc1234")


# ── retention ─────────────────────────────────────────────────────────────────

def test_prune_expires_old_seed_tags_and_cleans_history_before_the_oldest_kept(retriever):
    names = []
    for i in range(4):
        retriever.ingest(_extra(i + 1))
        names.append(retriever.tag_seed(f"s{i}"))
        time.sleep(1.3)  # versions are cleaned by age, with a one-second margin
    tagged = {n: info["version"] for n, info in retriever._table().tags.list().items()}
    before = len(retriever.versions())

    out = lv.prune(retriever._table(), keep=2)

    assert out["expired_tags"] == ["seed-s0", "seed-s1"]
    tags = retriever._table().tags.list()
    assert sorted(tags) == ["seed-s2", "seed-s3"]
    versions = retriever.versions()
    assert len(versions) < before
    assert min(v["version"] for v in versions) > tagged["seed-s1"]  # history before the oldest kept tag is gone
    assert tags["seed-s2"]["version"] in {v["version"] for v in versions}
    for name in ("seed-s2", "seed-s3"):  # the kept tags still check out
        from retrieval.lancedb_backend import LanceDBRetriever
        r = LanceDBRetriever(uri=retriever.uri, embeddings=retriever.embeddings, as_of=name)
        assert r.count() == 50 + (3 if name == "seed-s2" else 4)


def test_prune_never_expires_or_blocks_on_a_manual_tag(retriever):
    table = retriever._table()
    table.tags.create("baseline", table.version)
    retriever.ingest(_extra(2))
    time.sleep(1.3)
    for i in range(3):
        retriever.ingest(_extra(i + 3))
        retriever.tag_seed(f"s{i}", keep=2)  # would raise if the tagged baseline was in the window
    assert "baseline" in retriever._table().tags.list()
    assert sorted(t for t in retriever._table().tags.list() if t.startswith("seed-")) == ["seed-s1", "seed-s2"]


def test_tag_seed_applies_the_retention_policy(retriever):
    for i in range(5):
        retriever.ingest(_extra(i + 1))
        retriever.tag_seed(f"s{i}", keep=3)
    seeds = sorted(t for t in retriever._table().tags.list() if t.startswith("seed-"))
    assert seeds == ["seed-s2", "seed-s3", "seed-s4"]
    assert retriever.snapshot()["lance_tag"] == "seed-s4"  # the tag names the final newest version


def test_prune_without_tags_keeps_recent_history(retriever):
    retriever.ingest(_extra(2))
    kept = {v["version"] for v in retriever.versions()}
    lv.prune(retriever._table())
    assert kept <= {v["version"] for v in retriever.versions()}  # all younger than the retention window


# ── listing and CLI ───────────────────────────────────────────────────────────

def test_list_versions_marks_current_and_tags(retriever):
    retriever.tag_seed("abc1234")
    rows = retriever.versions()
    assert [r["current"] for r in rows].count(True) == 1 and rows[-1]["current"]
    assert rows[-1]["tags"] == ["seed-abc1234"] and rows[-1]["rows"] == 50
    assert isinstance(rows[0]["timestamp"], datetime)
    text = lv.format_versions(rows)
    assert "seed-abc1234" in text and "<- current" in text


def test_cli_list_rollback_prune(retriever, monkeypatch, capsys):
    monkeypatch.setenv("LANCE_URI", retriever.uri)
    monkeypatch.setenv("COLLECTION_NAME", retriever.table_name)
    retriever.tag_seed("abc1234")
    retriever.ingest(_extra(4))

    assert lv.main(["list"]) == 0
    assert "seed-abc1234" in capsys.readouterr().out
    assert lv.main(["rollback", "seed-abc1234"]) == 0
    assert retriever.count() == 50
    assert lv.main(["rollback", "seed-nope"]) == 1
    assert "rollback failed" in capsys.readouterr().err
    assert lv.main(["prune", "--keep", "5"]) == 0


def test_cli_without_a_table_exits_with_a_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("LANCE_URI", str(tmp_path / "empty"))
    with pytest.raises(SystemExit, match="make ingest"):
        lv.main(["list"])


# ── provenance ────────────────────────────────────────────────────────────────

def test_store_snapshot_follows_the_backend(retriever, monkeypatch):
    monkeypatch.setenv("RETRIEVER_BACKEND", "pgvector")
    assert store_snapshot() is None
    monkeypatch.setenv("RETRIEVER_BACKEND", "lancedb")
    monkeypatch.setenv("LANCE_URI", retriever.uri)
    monkeypatch.setenv("COLLECTION_NAME", retriever.table_name)
    retriever.tag_seed("abc1234")
    snap = store_snapshot()
    assert snap["lance_tag"] == "seed-abc1234" and snap["lance_version"] == retriever.snapshot()["lance_version"]


def test_pipeline_ingest_tags_the_store(retriever, monkeypatch, fake_embeddings, capsys):
    import rag.embeddings
    import rag.pipeline as pipeline

    transcripts = [{"call_id": f"CALL-{i}", "full_text": f"text {i}", "date": "2026-01-01",
                    "duration_seconds": 60, "category": "c", "outcome": "o", "member_id": "M",
                    "agent_id": "A"} for i in range(3)]
    monkeypatch.setattr(pipeline, "load_synthetic_data", lambda: transcripts)
    monkeypatch.setattr(rag.embeddings, "get_embeddings", lambda: fake_embeddings)
    monkeypatch.setattr(pipeline, "get_retriever", lambda embeddings=None: retriever)
    monkeypatch.setattr(lv, "git_label", lambda: "abc1234")

    pipeline.ingest(use_cache=False)
    pipeline.ingest(use_cache=False)  # same commit again: a second tag, the first stays

    assert "Tagged the store seed-abc1234" in capsys.readouterr().out
    assert {"seed-abc1234", "seed-abc1234-2"} <= set(retriever._table().tags.list())
    assert retriever.snapshot()["lance_tag"] == "seed-abc1234-2"
