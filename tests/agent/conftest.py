"""Offline fixtures for the agent graph: fake LLM, stub tools, in-memory checkpointer."""

import pytest

pytest.importorskip(
    "fastmcp", reason="install the agent group: uv sync --extra dev --group agent"
)

from agent_support import BRANCHES, CHANGE_LOG, FakeLLM, StubToolbox, call_date, call_exists  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from agent.graph import build_graph  # noqa: E402


@pytest.fixture
def toolbox():
    return StubToolbox()


@pytest.fixture
def saver():
    return InMemorySaver()


@pytest.fixture
def change_files(monkeypatch):
    """Services read change_log.json and branches.json from the generated data; tests give
    the change subgraph fixed ones instead, so they need no generator run."""
    from agent.subgraphs import change

    monkeypatch.setattr(change, "load_change_log", lambda: list(CHANGE_LOG))
    monkeypatch.setattr(change, "load_branches", lambda: list(BRANCHES))


@pytest.fixture
def make(toolbox, saver):
    def _make(route: str = "retrieve", *, tools=None, checkpointer=None, frame=None):
        llm = FakeLLM(route, frame)
        graph = build_graph(
            get_llm=lambda: llm,
            toolbox=tools or toolbox,
            call_exists=call_exists,
            call_date=call_date,
            checkpointer=checkpointer or saver,
        )
        return graph, llm

    return _make
