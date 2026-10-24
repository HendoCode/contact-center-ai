"""ground: check what the tools claim before the answer is written. No LLM, no loop."""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable

from agent.nodes.classify import CALL_ID_RE
from agent.state import AgentState

CallExists = Callable[[str], Awaitable[bool]]
AGENT_ID_RE = re.compile(r"\bAGT-\d+\b")  # a change answer reports patterns, never an agent


async def retriever_call_exists(call_id: str) -> bool:
    """Real lookup: Retriever.get_by_id, a metadata filter and never a semantic search."""
    from retrieval import get_retriever

    return await asyncio.to_thread(get_retriever().get_by_id, call_id) is not None


def _window_call_ids(change: dict) -> set[str]:
    """Every call the analysis attributes to a window or a coaching pattern."""
    ids = {c for d in change.get("drivers", []) for w in ("before", "after") for c in d[w]["call_ids"]}
    return ids | {c for p in change.get("patterns", []) for c in p["call_ids"]}


def _windows_have_sql(change: dict) -> bool:
    windows = change.get("windows")
    if windows:
        return all((windows.get(name) or {}).get("sql") for name in ("before", "after"))
    # No change point was found: the weekly series searched is the evidence.
    return bool((change.get("series") or {}).get("sql"))


async def _ground_change(state: AgentState, call_exists: CallExists) -> dict:
    """An explain_change answer counts only with both window SQLs, citations that are real calls
    inside the windows, and no agent id. A refusal says what could not be resolved and claims
    nothing, so there is nothing to check."""
    if state.get("refusal"):
        return {"citations": [], "grounded": True}
    change = state.get("change") or {}
    cited = state.get("citations", [])
    inside = _window_call_ids(change)
    real = [c for c in cited if c in inside and await call_exists(c)]
    shown = state.get("tool_output", "") + json.dumps(change)
    found_change_point = change.get("change_point", {}).get("source") != "none"
    grounded = (
        bool(change)
        and bool(state.get("sql"))
        and _windows_have_sql(change)
        and not AGENT_ID_RE.search(shown)
        and len(real) == len(cited)
        and (bool(real) or not found_change_point)
    )
    return {"citations": real, "grounded": grounded}


def make_ground(call_exists: CallExists):
    async def ground(state: AgentState) -> dict:
        if state["route"] == "explain_change":
            return await _ground_change(state, call_exists)
        if state["route"] == "resolve_metric":
            # A metric answer counts only with declared metrics and the SQL that produced it.
            return {"grounded": bool(state.get("sql")) and bool(state.get("metric_names"))}

        cited = list(dict.fromkeys(CALL_ID_RE.findall(state.get("tool_output", ""))))
        if state.get("call_id") and state["call_id"] not in cited:
            cited.append(state["call_id"])
        real = [c for c in cited if await call_exists(c)]

        if state["route"] == "summarize_call":
            grounded = state["call_id"] in real
        else:
            # Every cited call must exist, and an answer with no citation is not grounded.
            grounded = bool(real) and len(real) == len(cited)
        return {"citations": real, "grounded": grounded}

    return ground
