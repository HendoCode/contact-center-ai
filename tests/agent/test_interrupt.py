"""The clarify interrupt: when it fires, and how a run resumes. Offline, in-memory checkpointer."""

import pytest
from langgraph.types import Command

from agent.terms import load_terms
from agent_support import config

RATE_IDS = list(load_terms()["rate"].candidates)


def interrupt_payload(result: dict) -> dict:
    assert "__interrupt__" in result, f"expected an interrupt, got {result}"
    (interrupt,) = result["__interrupt__"]
    return interrupt.value


@pytest.mark.asyncio
async def test_average_rate_interrupts_with_the_contract_payload(make, toolbox):
    graph, _ = make("resolve_metric")
    result = await graph.ainvoke({"question": "what is our average rate?"}, config())

    payload = interrupt_payload(result)
    assert payload["kind"] == "clarify_metric"
    assert payload["version"] == 1
    assert payload["term"] == "rate"
    assert payload["multi_select"] is True
    assert payload["prompt"] == '"rate" matches 7 declared metrics. Which do you mean?'
    assert [o["id"] for o in payload["options"]] == RATE_IDS
    assert payload["options"][0] == {
        "id": "average_mortgage_note_rate", "label": "Average Mortgage Note Rate",
    }
    assert toolbox.calls == []  # nothing ran: the graph asked instead of guessing


@pytest.mark.asyncio
async def test_a_qualified_question_does_not_interrupt(make, toolbox):
    graph, _ = make("resolve_metric")
    out = await graph.ainvoke({"question": "what is our average mortgage note rate?"}, config())

    assert "__interrupt__" not in out
    assert toolbox.tool_calls("query_metric") == [{"metrics": ["average_mortgage_note_rate"]}]
    assert out["metric_names"] == ["average_mortgage_note_rate"]
    assert out["grounded"] is True


@pytest.mark.asyncio
async def test_a_phrase_that_only_contains_the_alias_does_not_interrupt(make, toolbox):
    graph, _ = make("resolve_metric")
    out = await graph.ainvoke({"question": "what is our first contact resolution rate?"}, config())

    assert "__interrupt__" not in out
    assert toolbox.tool_calls("ask_the_analyst")  # no term matched: the analyst resolves it


@pytest.mark.asyncio
async def test_resume_with_a_valid_choice(make, toolbox):
    graph, _ = make("resolve_metric")
    cfg = config()
    await graph.ainvoke({"question": "what is our average rate?"}, cfg)

    chosen = ["average_mortgage_note_rate", "average_deposit_apy"]
    out = await graph.ainvoke(Command(resume={"choices": chosen}), cfg)

    assert "__interrupt__" not in out
    assert toolbox.tool_calls("query_metric") == [{"metrics": chosen}]
    assert out["metric_names"] == chosen
    assert out["sql"] == "SELECT 1 AS stub"
    assert out["grounded"] is True


@pytest.mark.asyncio
async def test_resume_with_an_invalid_choice_asks_again(make, toolbox):
    graph, _ = make("resolve_metric")
    cfg = config()
    await graph.ainvoke({"question": "what is our average rate?"}, cfg)

    again = await graph.ainvoke(Command(resume={"choices": ["interest_rate"]}), cfg)
    payload = interrupt_payload(again)
    assert payload["options"][0]["id"] == "average_mortgage_note_rate"
    assert "Answer with option ids" in payload["prompt"]
    assert toolbox.calls == []

    out = await graph.ainvoke(Command(resume={"choices": ["average_heloc_current_rate"]}), cfg)
    assert out["metric_names"] == ["average_heloc_current_rate"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [{"choices": []}, {"choices": "average_deposit_apy"}, {}, "yes"])
async def test_malformed_resume_values_are_invalid(make, bad):
    graph, _ = make("resolve_metric")
    cfg = config()
    await graph.ainvoke({"question": "what is our average rate?"}, cfg)

    assert "__interrupt__" in await graph.ainvoke(Command(resume=bad), cfg)


@pytest.mark.asyncio
async def test_after_two_re_asks_every_candidate_runs(make, toolbox):
    graph, _ = make("resolve_metric")
    cfg = config()
    result = await graph.ainvoke({"question": "what is our average rate?"}, cfg)
    for _ in range(2):  # two invalid answers: asked again each time
        result = await graph.ainvoke(Command(resume={"choices": ["nope"]}), cfg)
        interrupt_payload(result)

    out = await graph.ainvoke(Command(resume={"choices": ["nope"]}), cfg)  # third: give up

    assert "__interrupt__" not in out
    assert toolbox.tool_calls("query_metric") == [{"metrics": RATE_IDS}]
    assert out["metric_names"] == RATE_IDS


@pytest.mark.asyncio
async def test_resume_after_a_rebuild(make, toolbox, saver):
    graph, _ = make("resolve_metric")
    cfg = config("survives-restart")
    await graph.ainvoke({"question": "what is our average rate?"}, cfg)
    del graph

    rebuilt, _ = make("resolve_metric")  # new graph object, same checkpointer, same thread_id
    out = await rebuilt.ainvoke(Command(resume={"choices": ["average_deposit_apy"]}), cfg)

    assert out["metric_names"] == ["average_deposit_apy"]
    assert toolbox.tool_calls("query_metric") == [{"metrics": ["average_deposit_apy"]}]


@pytest.mark.asyncio
async def test_two_ambiguous_terms_are_asked_one_at_a_time(make, toolbox):
    graph, _ = make("resolve_metric")
    cfg = config()
    first = await graph.ainvoke({"question": "compare our average rate and balance"}, cfg)
    assert interrupt_payload(first)["term"] == "rate"

    second = await graph.ainvoke(Command(resume={"choices": ["average_deposit_apy"]}), cfg)
    assert interrupt_payload(second)["term"] == "balance"

    out = await graph.ainvoke(Command(resume={"choices": ["escrow_balance"]}), cfg)
    assert out["metric_names"] == ["average_deposit_apy", "escrow_balance"]
    assert toolbox.tool_calls("query_metric") == [
        {"metrics": ["average_deposit_apy", "escrow_balance"]}
    ]
