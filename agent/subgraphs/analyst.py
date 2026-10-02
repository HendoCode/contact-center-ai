"""The `analyst` subgraph: resolve a metric question to declared metrics.

disambiguate -> clarify? -> execute. It shares the parent's state keys and is
compiled without a checkpointer, so it inherits the parent's and its interrupt
surfaces in the parent run (`result["__interrupt__"]`).

Interrupt contract (version 1): see docs/design/agent-graph.md. Resume with
`Command(resume={"choices": [...]})` on the same thread_id.
"""

import re

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agent.nodes.answer import split_sql
from agent.state import AgentState
from agent.terms import match_terms, option_label
from agent.tools import Toolbox

MAX_REASKS = 2  # invalid answers re-ask this many times, then every candidate runs
_METRIC_LINE_RE = re.compile(r"^- ([a-z0-9_]+) — ", re.MULTILINE)


def _merge(existing: list[str], new: list[str]) -> list[str]:
    return list(dict.fromkeys([*existing, *new]))


async def disambiguate(state: AgentState) -> dict:
    names = list(state.get("metric_names", []))
    resolved = list(state.get("resolved_terms", []))
    for match in match_terms(state["question"], skip=resolved):
        if len(match.candidates) > 1:
            return {
                "term": match.term, "candidates": match.candidates,
                "metric_names": names, "resolved_terms": resolved, "clarify_attempts": 0,
            }
        names = _merge(names, match.candidates)
        resolved.append(match.term)
    return {"term": None, "candidates": [], "metric_names": names, "resolved_terms": resolved}


def _payload(state: AgentState) -> dict:
    term, candidates = state["term"], state["candidates"]
    prompt = f'"{term}" matches {len(candidates)} declared metrics. Which do you mean?'
    if state.get("clarify_attempts"):
        prompt += " Answer with option ids from the list."
    return {
        "kind": "clarify_metric", "version": 1, "term": term, "prompt": prompt,
        "options": [{"id": m, "label": option_label(m)} for m in candidates],
        "multi_select": True,
    }


def _valid_choices(answer: object, candidates: list[str]) -> list[str] | None:
    choices = answer.get("choices") if isinstance(answer, dict) else None
    if not isinstance(choices, list) or not choices:
        return None
    if not all(isinstance(c, str) and c in candidates for c in choices):
        return None
    return list(dict.fromkeys(choices))


async def clarify(state: AgentState) -> dict:
    # The only interrupt() in the graph. It is not wrapped in try/except and no
    # side effect precedes it: the node restarts from the top on resume.
    answer = interrupt(_payload(state))

    choices = _valid_choices(answer, state["candidates"])
    attempts = state.get("clarify_attempts", 0) + 1
    if choices is None and attempts <= MAX_REASKS:
        return {"clarify_attempts": attempts}
    # A valid answer, or too many invalid ones: run every candidate, as
    # ask_the_analyst does, rather than blend them or pick one.
    return {
        "term": None, "candidates": [], "clarify_attempts": 0,
        "metric_names": _merge(state.get("metric_names", []), choices or state["candidates"]),
        "resolved_terms": [*state.get("resolved_terms", []), state["term"]],
    }


def make_execute(toolbox: Toolbox):
    async def execute(state: AgentState) -> dict:
        names = state.get("metric_names", [])
        if names:
            output = await toolbox.call("query_metric", {"metrics": names})
        else:
            output = await toolbox.call("ask_the_analyst", {"question": state["question"]})
            names = _METRIC_LINE_RE.findall(output)
        _, sql = split_sql(output)
        return {"tool_output": output, "sql": sql, "metric_names": names}

    return execute


def build_analyst(toolbox: Toolbox):
    graph = StateGraph(AgentState)
    graph.add_node("disambiguate", disambiguate)
    graph.add_node("clarify", clarify)
    graph.add_node("execute", make_execute(toolbox))
    graph.add_edge(START, "disambiguate")
    graph.add_conditional_edges(
        "disambiguate", lambda s: "clarify" if s.get("term") else "execute", ["clarify", "execute"]
    )
    graph.add_conditional_edges(
        "clarify", lambda s: "clarify" if s.get("term") else "disambiguate",
        ["clarify", "disambiguate"],
    )
    graph.add_edge("execute", END)
    return graph.compile(name="analyst")

