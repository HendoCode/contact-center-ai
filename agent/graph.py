"""The agent graph.

    START -> classify -+- retrieve --------+
                       +- summarize_call --+-> ground -> answer -> END
                       +- resolve_metric --+   (the `analyst` subgraph)
                       +- explain_change --+   (the `change` subgraph)

Design: docs/design/agent-graph.md. Build with `build_graph(...)`; `make_graph()`
is the zero-argument factory `langgraph dev` loads from agent/langgraph.json.
"""

from collections.abc import Callable
from functools import cache

from langgraph.graph import END, START, StateGraph

from agent.nodes.answer import answer
from agent.nodes.calls import make_retrieve, make_summarize_call
from agent.nodes.classify import make_classify
from agent.nodes.ground import CallDate, CallExists, make_ground, retriever_call_date, retriever_call_exists
from agent.state import AgentInput, AgentOutput, AgentState
from agent.subgraphs.analyst import build_analyst
from agent.subgraphs.change import build_change
from agent.tools import MCPToolbox, Toolbox


def build_graph(
    *,
    get_llm: Callable[[], object],
    toolbox: Toolbox,
    call_exists: CallExists = retriever_call_exists,
    call_date: CallDate = retriever_call_date,
    checkpointer=None,
):
    """Compile the graph. `get_llm` is called when classify runs, never at build time."""
    graph = StateGraph(AgentState, input_schema=AgentInput, output_schema=AgentOutput)
    graph.add_node("classify", make_classify(get_llm))
    graph.add_node("retrieve", make_retrieve(toolbox))
    graph.add_node("summarize_call", make_summarize_call(toolbox))
    graph.add_node("resolve_metric", build_analyst(toolbox))
    graph.add_node("explain_change", build_change(toolbox, get_llm))
    graph.add_node("ground", make_ground(call_exists, call_date))
    graph.add_node("answer", answer)

    graph.add_edge(START, "classify")
    routes = ["retrieve", "summarize_call", "resolve_metric", "explain_change"]
    graph.add_conditional_edges("classify", lambda s: s["route"], routes)
    for route in routes:
        graph.add_edge(route, "ground")
    graph.add_edge("ground", "answer")
    graph.add_edge("answer", END)
    return graph.compile(checkpointer=checkpointer, name="agent")


@cache
def _toolbox() -> MCPToolbox:
    return MCPToolbox()


def make_graph():
    """Graph for `langgraph dev` / Studio: no checkpointer (the dev server supplies one)."""
    from rag.pipeline import get_llm

    return build_graph(get_llm=get_llm, toolbox=_toolbox())
