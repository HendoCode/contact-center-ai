"""The router reads a route from whatever shape the model replies in; a slip never crashes a step."""

import pytest
from langchain_core.messages import AIMessage

from agent.nodes.classify import make_classify, parse_route


@pytest.mark.parametrize("text, route", [
    ('{"route": "explain_change"}', "explain_change"),
    ('```json\n{"route": "resolve_metric"}\n```', "resolve_metric"),
    ('Here you go:\n```\n{"route": "retrieve"}\n```', "retrieve"),
    ("explain_change", "explain_change"),
    ('  "Explain_Change"\n', "explain_change"),
    ("'resolve_metric'", "resolve_metric"),
    ("```\nexplain_change\n```", "explain_change"),
])
def test_parse_route_accepts_json_fenced_json_and_a_bare_route_name(text, route):
    assert parse_route(text) == route


@pytest.mark.parametrize("text", [
    "", "   ", "garbage", "{not json", '{"route": "nonsense"}', '{"other": 1}', "[]", "null",
    "I think explain_change fits best", "explain_change, retrieve", None, 42,
])
def test_parse_route_returns_none_for_anything_else(text):
    assert parse_route(text) is None


class _Raw:
    """A model whose structured parse failed: only the raw message is left."""

    def __init__(self, content):
        self.content = content

    def with_structured_output(self, schema, **_):
        async def respond(prompt):
            return {"raw": AIMessage(content=self.content), "parsed": None,
                    "parsing_error": ValueError("Invalid JSON")}

        from langchain_core.runnables import RunnableLambda
        return RunnableLambda(respond)


async def _route(content):
    classify = make_classify(lambda: _Raw(content))
    return (await classify({"question": "Why did online banking calls go up in late July?"}))["route"]


@pytest.mark.asyncio
async def test_classify_survives_a_bare_route_name_from_the_model():
    assert await _route("explain_change") == "explain_change"


@pytest.mark.asyncio
async def test_classify_survives_a_fenced_reply():
    assert await _route('```json\n{"route": "resolve_metric"}\n```') == "resolve_metric"


@pytest.mark.asyncio
async def test_classify_falls_back_to_retrieve_on_garbage(caplog):
    assert await _route("I am not sure") == "retrieve"
    assert "not a known route" in caplog.text


@pytest.mark.asyncio
async def test_classify_still_sends_a_route_less_summarize_call_to_retrieve():
    assert await _route("summarize_call") == "retrieve"
