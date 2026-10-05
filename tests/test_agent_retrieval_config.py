"""AGENT_RETRIEVAL_MODE / AGENT_RETRIEVAL_K: defaults keep today's search, the values reach
the search, the preflight refuses what the backend cannot serve, and compare shows them."""

import pytest

from agent.nodes.calls import search_arguments
from ccai_mcp import tools
from evals import compare, preflight
from evals.preflight import PreflightError


def test_defaults_are_todays_search(monkeypatch):
    monkeypatch.delenv("AGENT_RETRIEVAL_K", raising=False)
    monkeypatch.delenv("AGENT_RETRIEVAL_MODE", raising=False)
    assert search_arguments("why fees?") == {"query": "why fees?"}
    assert tools.retrieval_mode() == "vector"


def test_k_and_mode_reach_the_search(monkeypatch):
    monkeypatch.setenv("AGENT_RETRIEVAL_K", "10")
    monkeypatch.setenv("AGENT_RETRIEVAL_MODE", "Hybrid")
    assert search_arguments("why fees?") == {"query": "why fees?", "k": 10}
    seen = {}
    monkeypatch.setattr(tools, "rag_query", lambda query, k, mode: seen.update(q=query, k=k, mode=mode) or "ok")
    assert tools.search_transcripts("why fees?", k=10) == "ok"
    assert seen == {"q": "why fees?", "k": 10, "mode": "hybrid"}


def test_unknown_mode_is_refused(monkeypatch):
    monkeypatch.setenv("AGENT_RETRIEVAL_MODE", "semantic")
    with pytest.raises(ValueError, match="use one of vector, fts, hybrid"):
        tools.retrieval_mode()


@pytest.mark.parametrize("env, message", [
    ({"AGENT_RETRIEVAL_MODE": "hybrid"}, "AGENT_RETRIEVAL_MODE=hybrid needs RETRIEVER_BACKEND=lancedb (pgvector serves vector search only)"),
    ({"AGENT_RETRIEVAL_MODE": "bm25"}, "AGENT_RETRIEVAL_MODE='bm25': use vector, fts or hybrid"),
    ({"AGENT_RETRIEVAL_K": "0"}, "AGENT_RETRIEVAL_K='0': a positive whole number"),
])
def test_preflight_refuses_what_cannot_run(env, message):
    with pytest.raises(PreflightError) as err:
        preflight.check_retrieval(env)
    assert str(err.value) == message


def test_preflight_accepts_lance_hybrid():
    env = {"RETRIEVER_BACKEND": "lancedb", "AGENT_RETRIEVAL_MODE": "hybrid", "AGENT_RETRIEVAL_K": "10"}
    assert preflight.check_retrieval(env) == "search: lancedb, hybrid, k=10"


def _record(**params):
    return {"date": "2026-10-05", "git_sha": "abc1234", "area": "evals", "run": "r", "hardware": "t",
            "versions": {"python": "3.12", "agent_model": "a", "judge_model": "j"},
            "params": {"judge_prompt": "v1", "golden_sha256": "x" * 64, "limit": None, **params},
            "metrics": {"open": {"n": 12, "judge": 0.58}}, "notes": ""}


def test_compare_shows_the_search_and_flags_a_change():
    old = _record()  # recorded before these params existed: pgvector, vector, k=5
    new = _record(retriever_backend="lancedb", retrieval_mode="hybrid", retrieval_k=5)
    out = compare.compare(old, new)
    assert "WARNING: retriever differs (pgvector -> lancedb): the delta measures this change" in out
    assert "WARNING: search mode differs (vector -> hybrid): the delta measures this change" in out
    assert "search k" in out and "WARNING: search k" not in out
