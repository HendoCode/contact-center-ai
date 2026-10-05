"""The PGVector table race behind "Table 'langchain_pg_collection' is already defined", and
tool failures that announce themselves instead of hiding inside an answer's score.

A fake PGVector reproduces langchain_postgres's unlocked check-then-define of its tables,
so no database is needed.
"""

import asyncio
import threading
import time
from types import SimpleNamespace as NS

import pytest

import rag.embeddings as emb
from evals import compare
from evals.run import tool_error


class RacyPGVector:
    """Like langchain_postgres: the first build defines the tables; a second definition fails."""

    defined = False
    builds = 0

    def __init__(self, embeddings, collection_name, connection, use_jsonb):
        if not RacyPGVector.defined:
            time.sleep(0.02)  # the window two threads hit in the real library
            if RacyPGVector.defined:
                raise RuntimeError("Table 'langchain_pg_collection' is already defined for this MetaData instance.")
            RacyPGVector.defined = True
        RacyPGVector.builds += 1
        self.embeddings = embeddings

    def similarity_search_with_score(self, query, k, filter=None):
        return [(NS(page_content=f"text for {query}", metadata={"call_id": "CALL-00001"}), 0.1)]


@pytest.fixture
def racy(monkeypatch):
    RacyPGVector.defined, RacyPGVector.builds = False, 0
    monkeypatch.setattr(emb, "PGVector", RacyPGVector)
    monkeypatch.setattr(emb, "_STORES", {})
    monkeypatch.setattr(emb, "get_embeddings", lambda: object())
    return RacyPGVector


def concurrently(fn, n=4):
    barrier, errors, results = threading.Barrier(n), [], []

    def run():
        barrier.wait()
        try:
            results.append(fn())
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))

    threads = [threading.Thread(target=run) for _ in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return results, errors


def test_the_fake_reproduces_the_race_without_the_lock(racy):
    _, errors = concurrently(lambda: emb._build_store(object()))
    assert errors and "already defined" in errors[0]


def test_concurrent_stores_with_their_own_embeddings_do_not_race(racy):
    results, errors = concurrently(lambda: emb.get_vector_store(object()))
    assert errors == [] and len(results) == 4


def test_the_default_store_is_built_once_per_process(racy):
    results, errors = concurrently(emb.get_vector_store)
    assert errors == []
    assert racy.builds == 1 and all(r is results[0] for r in results)


def test_two_retrievers_search_twice_in_one_process(racy):
    from retrieval.pgvector_backend import PgVectorRetriever

    hits = [PgVectorRetriever().search("fees", k=1) for _ in range(2)]
    hits += concurrently(lambda: PgVectorRetriever().search("fees", k=1))[0]
    assert len(hits) == 6 and all(h[0].call_id == "CALL-00001" for h in hits)
    assert racy.builds == 1


def test_a_failing_tool_says_so(monkeypatch):
    from ccai_mcp import server

    async def boom(name, arguments):
        raise RuntimeError("database went away")

    monkeypatch.setattr(server, "_run_tool", boom)
    params = NS(name="search_transcripts", arguments={"query": "fees"})
    result = asyncio.run(server.call_tool(None, params))
    assert result.is_error
    assert result.content[0].text == "TOOL ERROR (search_transcripts): RuntimeError: database went away"


@pytest.mark.parametrize("answer, found", [
    ("Not grounded: treat this answer as unverified.\n\nTOOL ERROR (search_transcripts): RuntimeError: x",
     "TOOL ERROR (search_transcripts): RuntimeError: x"),
    ("Not grounded: treat this answer as unverified.\n\nTable 'langchain_pg_collection' is already defined "
     "for this MetaData instance.  Specify 'extend_existing=True'",
     "Table 'langchain_pg_collection' is already defined for this MetaData instance.  Specify 'extend_existing=True'"),
    ("query_metric error: Database Error relation does not exist", "query_metric error: Database Error relation does not exist"),
    ("Members call about fees.\n\nSources: CALL-00001", None),
    (None, None),
])
def test_tool_errors_are_recognized_in_answers(answer, found):
    assert tool_error(answer) == found


def _record(items):
    return {"date": "2026-10-05", "git_sha": "abc1234", "area": "evals", "run": "r", "hardware": "t",
            "versions": {"python": "3.12", "agent_model": "a", "judge_model": "j"},
            "params": {"judge_prompt": "v1", "golden_sha256": "x" * 64, "limit": None},
            "metrics": {"all": {"n": len(items), "judge": 0.5}, "open": {"n": len(items), "judge": 0.5}},
            "notes": "", "items": items}


def test_compare_counts_tool_errors_per_group_even_in_old_runs():
    bad = {"kind": "open", "outputs": {"answer": "Not grounded.\n\nTable 'langchain_pg_collection' is already "
                                                 "defined for this MetaData instance."}}
    good = {"kind": "open", "outputs": {"answer": "Members call about fees."}}
    out = compare.compare(_record([bad, bad, good]), _record([good, good, good]), "before", "after")
    assert ("WARNING: before has 2 item(s) whose answer is a tool failure; their scores measure the "
            "failure, not the agent") in out
    assert "  tool err         2       0   items whose answer is a tool failure" in out
