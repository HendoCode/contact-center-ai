"""ground: check what the tools claim before the answer is written. No LLM, no loop."""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable

from agent.nodes.classify import CALL_ID_RE
from agent.state import AgentState

CallExists = Callable[[str], Awaitable[bool]]
CallDate = Callable[[str], Awaitable[str | None]]  # the call's ISO date, or None if no such call
AGENT_ID_RE = re.compile(r"\bAGT-\d+\b")  # a change answer reports patterns, never an agent


async def retriever_call_exists(call_id: str) -> bool:
    """Real lookup: Retriever.get_by_id, a metadata filter and never a semantic search."""
    from retrieval import get_retriever

    return await asyncio.to_thread(get_retriever().get_by_id, call_id) is not None


async def retriever_call_date(call_id: str) -> str | None:
    """The stored date of a call (the same metadata lookup), or None when there is no such call."""
    from retrieval import get_retriever

    hit = await asyncio.to_thread(get_retriever().get_by_id, call_id)
    return (str(hit.metadata.get("date") or "") or None) if hit else None


def _in_windows(day: str | None, change: dict) -> bool:
    """Whether a call's date falls in [before.start, after.end): the dates the answer states,
    compared with the call's own date rather than the tool's lists of call ids."""
    windows = change.get("windows")
    if not day or not windows:
        return False
    return windows["before"]["start"] <= day[:10] < windows["after"]["end"]


def _windows_have_sql(change: dict) -> bool:
    windows = change.get("windows")
    if windows:
        return all((windows.get(name) or {}).get("sql") for name in ("before", "after"))
    # No change point was found: the weekly series searched is the evidence.
    return bool((change.get("series") or {}).get("sql"))


async def _ground_change(state: AgentState, call_date: CallDate) -> dict:
    """An explain_change answer counts only with both window SQLs, citations that are real calls
    inside the windows, and no agent id. A refusal says what could not be resolved and claims
    nothing, so there is nothing to check."""
    if state.get("refusal"):
        return {"citations": [], "grounded": True}
    change = state.get("change") or {}
    cited = state.get("citations", [])
    real = [c for c in cited if _in_windows(await call_date(c), change)]
    shown = state.get("tool_output", "") + json.dumps(change)
    # A detected shift with no driver in the calls is reported as unsupported: it claims nothing.
    point = change.get("change_point", {})
    found_change_point = point.get("source") != "none" and not (
        point.get("source") == "detected" and not change.get("drivers") and not change.get("patterns")
    )
    grounded = (
        bool(change)
        and bool(state.get("sql"))
        and _windows_have_sql(change)
        and not AGENT_ID_RE.search(shown)
        and len(real) == len(cited)
        and (bool(real) or not found_change_point)
    )
    return {"citations": real, "grounded": grounded}


def make_ground(call_exists: CallExists, call_date: CallDate = retriever_call_date):
    async def ground(state: AgentState) -> dict:
        if state["route"] == "explain_change":
            return await _ground_change(state, call_date)
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
