"""search_transcripts(min_csat, max_csat): the tool schema, the argument checks, and the filter
that reaches the retriever. Offline; the retriever and the model are stand-ins."""

import asyncio
from types import SimpleNamespace as NS

import pytest

import rag.pipeline as pipeline
from ccai_mcp import server, tools
from retrieval.base import Hit


def test_csat_where_builds_an_inclusive_range():
    assert tools.csat_where() is None
    assert tools.csat_where(max_csat=2) == {"csat_score": {"lte": 2}}
    assert tools.csat_where(min_csat=4) == {"csat_score": {"gte": 4}}
    assert tools.csat_where(2, 3) == {"csat_score": {"gte": 2, "lte": 3}}


@pytest.mark.parametrize("kwargs, message", [
    ({"min_csat": 0}, "min_csat must be an integer from 1 to 5"),
    ({"max_csat": 6}, "max_csat must be an integer from 1 to 5"),
    ({"max_csat": True}, "max_csat must be an integer from 1 to 5"),
    ({"min_csat": 4, "max_csat": 2}, "min_csat \\(4\\) is above max_csat \\(2\\)"),
])
def test_csat_where_refuses_bad_ranges(kwargs, message):
    with pytest.raises(ValueError, match=message):
        tools.csat_where(**kwargs)


def test_search_transcripts_passes_the_filter_to_rag_query(monkeypatch):
    monkeypatch.delenv("AGENT_RETRIEVAL_MODE", raising=False)
    seen = {}
    monkeypatch.setattr(tools, "rag_query", lambda query, k, mode, where: seen.update(where=where, mode=mode) or "ok")
    assert tools.search_transcripts("fees", max_csat=2) == "ok"
    assert seen == {"where": {"csat_score": {"lte": 2}}, "mode": "vector"}
    tools.search_transcripts("fees")
    assert seen["where"] is None


def test_schema_declares_the_bounds_and_dispatch_forwards_them(monkeypatch):
    tool = next(t for t in server.TOOLS if t.name == "search_transcripts")
    props = tool.input_schema["properties"]
    for name in ("min_csat", "max_csat"):
        assert props[name]["type"] == "integer" and (props[name]["minimum"], props[name]["maximum"]) == (1, 5)
    assert "LanceDB" in props["max_csat"]["description"]
    assert tool.input_schema["required"] == ["query"]

    seen = {}
    monkeypatch.setattr(server, "search_transcripts", lambda **kw: seen.update(kw) or "answer")
    out = asyncio.run(server._run_tool("search_transcripts", {"query": "fees", "max_csat": 2}))
    assert out == "answer"
    assert seen == {"query": "fees", "k": 5, "min_csat": None, "max_csat": 2}


def test_out_of_scale_value_is_a_tool_error():
    params = NS(name="search_transcripts", arguments={"query": "fees", "max_csat": 9})
    result = asyncio.run(server.call_tool(None, params))
    assert result.is_error and "maximum of 5" in result.content[0].text


def test_pgvector_backend_refuses_with_a_tool_error(monkeypatch):
    """With the default backend the filter is refused before any model is called."""
    from retrieval.pgvector_backend import PgVectorRetriever

    class Store:
        def similarity_search_with_score(self, *a, **kw):  # pragma: no cover
            raise AssertionError("must not search")

    monkeypatch.setattr(pipeline, "get_retriever", lambda: PgVectorRetriever(store=Store(), connect=lambda: None))
    monkeypatch.setattr(pipeline, "get_llm", lambda: pytest.fail("the model must not be called"))
    monkeypatch.delenv("AGENT_RETRIEVAL_MODE", raising=False)
    params = NS(name="search_transcripts", arguments={"query": "fees", "max_csat": 2})
    result = asyncio.run(server.call_tool(None, params))
    assert result.is_error
    assert "RETRIEVER_BACKEND=lancedb" in result.content[0].text


def _hit(call_id, score):
    return Hit(call_id=call_id, text=f"text of {call_id}", score=0.9,
               metadata={"category": "fee_dispute", "date": "2026-01-01", "outcome": "resolved",
                         "csat_score": score})


def test_rag_query_filters_counts_and_tells_the_model_what_was_searched(monkeypatch):
    seen = {}

    class Retriever:
        def search(self, query, k, where, mode):
            seen["search"] = where
            return [_hit("CALL-1", 1), _hit("CALL-2", 2)]

        def count(self, where=None):
            seen["count"] = where
            return 21

    class LLM:
        def invoke(self, prompt):
            seen["prompt"] = prompt
            return NS(content="Members cite the overdraft fee.")

    monkeypatch.setattr(pipeline, "get_retriever", lambda: Retriever())
    monkeypatch.setattr(pipeline, "get_llm", lambda: LLM())
    where = {"csat_score": {"gte": 1, "lte": 2}}
    out = pipeline.rag_query("what do unhappy members say about fees?", where=where)
    assert seen["search"] == where
    assert seen["count"] == {"category": "fee_dispute", "csat_score": {"gte": 1, "lte": 2}}
    assert out.startswith(
        "Based on a sample of 2 retrieved calls: 2 of 21 calls in fee_dispute with a CSAT score of 1 to 2. "
        "Only calls with a CSAT score of 1 to 2 were searched.\n\n"
    )
    assert "CSAT score: 1\n" in seen["prompt"] and "CSAT score: 2\n" in seen["prompt"]


@pytest.mark.parametrize("cond, words", [
    ({"lte": 2}, "with a CSAT score of 2 or lower"),
    ({"gte": 4}, "with a CSAT score of 4 or higher"),
    ({"gte": 3, "lte": 3}, "with a CSAT score of 3"),
    ({"in": [1, 2]}, "within the given CSAT filter"),
])
def test_csat_scope_words(cond, words):
    assert pipeline.csat_scope({"csat_score": cond}) == words
    assert pipeline.csat_scope({"category": "x"}) == ""
