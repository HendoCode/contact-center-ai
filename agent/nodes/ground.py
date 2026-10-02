"""ground: check what the tools claim before the answer is written. No LLM, no loop."""

import asyncio
from collections.abc import Awaitable, Callable

from agent.nodes.classify import CALL_ID_RE
from agent.state import AgentState

CallExists = Callable[[str], Awaitable[bool]]


async def retriever_call_exists(call_id: str) -> bool:
    """Real lookup: Retriever.get_by_id, a metadata filter and never a semantic search."""
    from retrieval import get_retriever

    return await asyncio.to_thread(get_retriever().get_by_id, call_id) is not None


def make_ground(call_exists: CallExists):
    async def ground(state: AgentState) -> dict:
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
