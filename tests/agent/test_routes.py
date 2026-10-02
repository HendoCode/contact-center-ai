"""One test per route, with a fake LLM and stub tools. Offline."""

import pytest

from agent_support import StubToolbox, config

OUTPUT_KEYS = {"answer", "route", "metric_names", "sql", "citations", "grounded"}


@pytest.mark.asyncio
async def test_retrieve_route(make, toolbox):
    graph, llm = make("retrieve")
    out = await graph.ainvoke({"question": "what do members say about fees?"}, config())

    assert set(out) == OUTPUT_KEYS
    assert out["route"] == "retrieve"
    assert toolbox.tool_calls("search_transcripts") == [{"query": "what do members say about fees?"}]
    assert out["citations"] == ["CALL-00001", "CALL-00002"]
    assert out["grounded"] is True
    assert "Sources: CALL-00001, CALL-00002" in out["answer"]
    assert len(llm.prompts) == 1


@pytest.mark.asyncio
async def test_retrieve_with_invented_call_id_is_not_grounded(make):
    graph, _ = make("retrieve", tools=StubToolbox("See CALL-00001 and CALL-99999."))
    out = await graph.ainvoke({"question": "fraud calls"}, config())

    assert out["citations"] == ["CALL-00001"]  # only the verified id is kept
    assert out["grounded"] is False
    assert out["answer"].startswith("Not grounded")


@pytest.mark.asyncio
async def test_retrieve_with_no_citation_is_not_grounded(make):
    graph, _ = make("retrieve", tools=StubToolbox("Members were unhappy."))
    out = await graph.ainvoke({"question": "fraud calls"}, config())

    assert out["citations"] == []
    assert out["grounded"] is False


@pytest.mark.asyncio
async def test_summarize_call_route_skips_the_llm(make, toolbox):
    graph, llm = make("retrieve")  # the LLM would say retrieve; the call id wins
    out = await graph.ainvoke({"question": "summarize CALL-00042 please"}, config())

    assert out["route"] == "summarize_call"
    assert llm.prompts == []
    assert toolbox.tool_calls("get_call_summary") == [{"call_id": "CALL-00042"}]
    assert out["citations"] == ["CALL-00042"]
    assert out["grounded"] is True


@pytest.mark.asyncio
async def test_summarize_call_for_missing_call_is_not_grounded(make):
    graph, _ = make("retrieve")
    out = await graph.ainvoke({"question": "what happened on CALL-77777?"}, config())

    assert out["route"] == "summarize_call"
    assert out["citations"] == []
    assert out["grounded"] is False


@pytest.mark.asyncio
async def test_llm_choosing_summarize_without_a_call_id_falls_back_to_retrieve(make):
    graph, _ = make("summarize_call")
    out = await graph.ainvoke({"question": "summarize yesterday's escalations"}, config())

    assert out["route"] == "retrieve"


@pytest.mark.asyncio
async def test_resolve_metric_route_without_a_term_uses_ask_the_analyst(make, toolbox):
    graph, _ = make("resolve_metric")
    out = await graph.ainvoke({"question": "how many calls did we handle?"}, config())

    assert "__interrupt__" not in out
    assert out["route"] == "resolve_metric"
    assert toolbox.tool_calls("ask_the_analyst") == [{"question": "how many calls did we handle?"}]
    assert toolbox.tool_calls("query_metric") == []
    assert out["metric_names"] == ["call_volume", "average_handle_time"]
    assert out["sql"] == "SELECT 2 AS stub"
    assert out["grounded"] is True
    assert out["answer"].endswith("SQL:\nSELECT 2 AS stub")


@pytest.mark.asyncio
async def test_metric_answer_without_sql_is_not_grounded(make):
    class NoSql(StubToolbox):
        async def call(self, name, arguments):
            if name == "query_metric":
                return "query_metric error: boom"
            return await super().call(name, arguments)

    graph, _ = make("resolve_metric", tools=NoSql())
    out = await graph.ainvoke({"question": "average mortgage note rate"}, config())

    assert out["sql"] is None
    assert out["grounded"] is False


@pytest.mark.asyncio
async def test_reusing_a_thread_does_not_carry_state_between_questions(make):
    graph, _ = make("resolve_metric")
    cfg = config("reused")
    first = await graph.ainvoke({"question": "average mortgage note rate"}, cfg)
    second = await graph.ainvoke({"question": "how many calls did we handle?"}, cfg)

    assert first["metric_names"] == ["average_mortgage_note_rate"]
    assert second["metric_names"] == ["call_volume", "average_handle_time"]


@pytest.mark.asyncio
async def test_lowercase_call_id_is_normalized_before_lookup(make, toolbox):
    graph, llm = make("retrieve")
    out = await graph.ainvoke({"question": "what happened on call-00042?"}, config())

    assert out["route"] == "summarize_call"
    assert llm.prompts == []
    assert toolbox.tool_calls("get_call_summary") == [{"call_id": "CALL-00042"}]
    assert out["citations"] == ["CALL-00042"]
