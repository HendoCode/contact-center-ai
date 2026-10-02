"""agent.demo's five scripted steps run end to end against a fake LLM and stub tools. Offline."""

import pytest
from agent_support import StubToolbox, call_exists
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver

from agent.demo import STEPS, run_steps
from agent.graph import build_graph


class QuestionRoutingLLM:
    """Routes by question text, as the scripted steps expect a real model to."""

    def with_structured_output(self, schema):
        def respond(prompt):
            question = prompt.rsplit("Question:", 1)[1]
            if "fraud" in question:
                return schema(route="retrieve")
            return schema(route="resolve_metric")

        return RunnableLambda(respond)


def _graph(llm):
    return build_graph(
        get_llm=lambda: llm, toolbox=StubToolbox(), call_exists=call_exists,
        checkpointer=InMemorySaver(),
    )


def test_the_script_covers_each_route_and_one_interrupt():
    assert len(STEPS) == 5
    assert {s.route for s in STEPS} == {"retrieve", "summarize_call", "resolve_metric"}
    assert [s.label for s in STEPS if s.interrupt] == ["interrupt, then resume"]


@pytest.mark.asyncio
async def test_all_five_steps_run_as_scripted(capsys):
    matched, failures = await run_steps(_graph(QuestionRoutingLLM()), STEPS)

    out = capsys.readouterr().out
    assert (matched, failures) == (5, [])
    assert "INTERRUPT:" in out and "average_mortgage_note_rate" in out
    assert "resume:" in out
    assert "MISMATCH" not in out


@pytest.mark.asyncio
async def test_a_misrouted_step_is_reported_not_raised(capsys):
    class AlwaysMetric:
        def with_structured_output(self, schema):
            return RunnableLambda(lambda prompt: schema(route="resolve_metric"))

    matched, failures = await run_steps(_graph(AlwaysMetric()), STEPS)

    assert failures == []
    assert matched == 4  # the fraud question went to a metric
    assert "MISMATCH: routed to resolve_metric, scripted route is retrieve" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_a_tool_failure_is_a_failure(capsys):
    class BrokenTools(StubToolbox):
        async def call(self, name, arguments):
            raise ConnectionError("db is down")

    graph = build_graph(
        get_llm=lambda: QuestionRoutingLLM(), toolbox=BrokenTools(), call_exists=call_exists,
        checkpointer=InMemorySaver(),
    )
    matched, failures = await run_steps(graph, STEPS)

    assert matched == 0
    assert len(failures) == 5 and "ConnectionError: db is down" in failures[0]
