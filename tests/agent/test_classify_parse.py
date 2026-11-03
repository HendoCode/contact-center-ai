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


class _Failing:
    """A model whose structured call raises (as the SDK does) and whose plain call answers."""

    def __init__(self, error, plain):
        self.error, self.plain, self.plain_prompts = error, plain, []

    def with_structured_output(self, schema, **_):
        async def respond(prompt):
            raise self.error

        from langchain_core.runnables import RunnableLambda
        return RunnableLambda(respond)

    async def ainvoke(self, prompt):
        self.plain_prompts.append(prompt)
        if isinstance(self.plain, Exception):
            raise self.plain
        return AIMessage(content=self.plain)


def _schema_invalid():
    from agent.nodes.classify import RouteDecision
    try:
        RouteDecision.model_validate({"route": "nonsense"})
    except Exception as exc:  # noqa: BLE001
        return exc


def _length_error():
    from openai import LengthFinishReasonError
    return LengthFinishReasonError(completion=type("C", (), {"usage": None})())


async def _route_from(model):
    classify = make_classify(lambda: model)
    return (await classify({"question": "Why did online banking calls go up in late July?"}))["route"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    _schema_invalid(),
    __import__("langchain_core.exceptions", fromlist=["x"]).OutputParserException("Invalid JSON"),
    _length_error(),
], ids=["schema-invalid", "non-json", "length-capped"])
async def test_classify_re_asks_as_plain_text_when_the_structured_call_raises(error):
    model = _Failing(error, "explain_change")
    assert await _route_from(model) == "explain_change"
    assert "only the route name" in model.plain_prompts[0]


@pytest.mark.asyncio
async def test_classify_falls_back_to_retrieve_when_the_plain_re_ask_is_also_unusable():
    assert await _route_from(_Failing(_schema_invalid(), "no idea")) == "retrieve"


@pytest.mark.asyncio
async def test_classify_gives_a_clear_error_when_the_plain_re_ask_hits_the_cap():
    with pytest.raises(RuntimeError, match="LLM_STRUCTURED_MAX_TOKENS"):
        await _route_from(_Failing(_schema_invalid(), _length_error()))


@pytest.mark.asyncio
async def test_classify_does_not_swallow_unrelated_errors():
    with pytest.raises(KeyError):
        await _route_from(_Failing(KeyError("bug"), "retrieve"))


@pytest.mark.asyncio
async def test_classify_gives_routing_the_structured_output_cap(monkeypatch):
    from langchain_openai import ChatOpenAI
    monkeypatch.setenv("LLM_STRUCTURED_MAX_TOKENS", "4096")
    seen = []

    class Capped(ChatOpenAI):
        def with_structured_output(self, schema, **kw):
            seen.append(self.max_tokens)
            from langchain_core.runnables import RunnableLambda

            async def respond(prompt):
                return {"raw": AIMessage(content="retrieve"), "parsed": None, "parsing_error": None}
            return RunnableLambda(respond)

    model = Capped(model="m", api_key="k", max_tokens=2048)
    assert await _route_from(model) == "retrieve"
    assert seen == [4096]
