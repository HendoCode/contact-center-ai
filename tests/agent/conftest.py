"""Offline fixtures for the agent graph: fake LLM, stub tools, in-memory checkpointer."""

import pytest

pytest.importorskip(
    "langchain_mcp_adapters", reason="install the agent group: uv sync --extra dev --group agent"
)

from agent_support import FakeLLM, StubToolbox, call_exists  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from agent.graph import build_graph  # noqa: E402


@pytest.fixture
def toolbox():
    return StubToolbox()


@pytest.fixture
def saver():
    return InMemorySaver()


@pytest.fixture
def make(toolbox, saver):
    def _make(route: str = "retrieve", *, tools=None, checkpointer=None):
        llm = FakeLLM(route)
        graph = build_graph(
            get_llm=lambda: llm,
            toolbox=tools or toolbox,
            call_exists=call_exists,
            checkpointer=checkpointer or saver,
        )
        return graph, llm

    return _make
