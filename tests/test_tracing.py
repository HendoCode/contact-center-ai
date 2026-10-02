"""LangSmith tracing is on by env and off in tests. Offline: no key, no network."""

import socket

import pytest
from langchain_core.callbacks.manager import CallbackManager
from langchain_core.tracers.langchain import LangChainTracer
from langsmith import utils as ls_utils

from agent.tracing import TRACING_FLAGS, disable_tracing, tracing_enabled, tracing_status


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"network connection attempted: {args[1:]}")

    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture
def user_env(monkeypatch):
    """The environment a user turns tracing on with; restored (off) afterwards."""
    for flag in TRACING_FLAGS:
        monkeypatch.delenv(flag, raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "placeholder-not-a-key")
    monkeypatch.setenv("LANGSMITH_PROJECT", "ccai-test-project")
    _clear_caches()
    yield
    _clear_caches()


def _clear_caches():
    """LangSmith caches env lookups per process."""
    ls_utils.get_env_var.cache_clear()
    ls_utils.get_tracer_project.cache_clear()


def test_tracing_is_off_in_the_test_session():
    assert tracing_enabled() is False
    assert tracing_status() == "off"
    assert CallbackManager.configure().handlers == []


def test_the_env_turns_tracing_on(user_env, no_network):
    assert tracing_enabled() is True
    assert tracing_status() == "on (project ccai-test-project, LANGSMITH_API_KEY set)"
    tracers = [h for h in CallbackManager.configure().handlers if isinstance(h, LangChainTracer)]
    assert [t.project_name for t in tracers] == ["ccai-test-project"]


def test_disable_tracing_overrides_the_env(user_env):
    disable_tracing()
    assert tracing_enabled() is False


def test_tracing_is_off_unless_the_switch_is_set(monkeypatch):
    """A key alone does not trace: LANGSMITH_TRACING=true is the switch."""
    for flag in TRACING_FLAGS:
        monkeypatch.delenv(flag, raising=False)
    monkeypatch.setenv("LANGSMITH_API_KEY", "placeholder-not-a-key")
    _clear_caches()
    try:
        assert tracing_enabled() is False
    finally:
        _clear_caches()


@pytest.mark.asyncio
async def test_a_graph_run_in_tests_creates_no_tracer(monkeypatch, no_network):
    pytest.importorskip("fastmcp", reason="install the agent group: uv sync --group agent")
    from langgraph.checkpoint.memory import InMemorySaver

    from agent.graph import build_graph
    from evals.offline import OfflineToolbox, ScriptedClassifier, corpus_call_exists
    from evals.run import load_golden, run_question

    created = []
    original = LangChainTracer.__init__

    def record(self, *args, **kwargs):
        created.append(self)
        original(self, *args, **kwargs)

    monkeypatch.setattr(LangChainTracer, "__init__", record)
    items = load_golden()[:1]
    graph = build_graph(
        get_llm=lambda: ScriptedClassifier({items[0]["question"]: items[0]["route"]}),
        toolbox=OfflineToolbox(items, []),
        call_exists=corpus_call_exists(set()),
        checkpointer=InMemorySaver(),
    )
    out = await run_question(graph, items[0]["question"], items[0]["clarify_with"])

    assert out["route"] == items[0]["route"]
    assert created == []
