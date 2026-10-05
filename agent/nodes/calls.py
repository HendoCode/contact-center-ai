"""retrieve and summarize_call: one MCP tool call each, explicit arguments."""

import os

from agent.state import AgentState
from agent.tools import Toolbox


def search_arguments(question: str) -> dict:
    """search_transcripts arguments: the question, plus `k` when AGENT_RETRIEVAL_K sets it
    (otherwise the tool's default, 5). The search mode is the tool's AGENT_RETRIEVAL_MODE."""
    args: dict = {"query": question}
    k = os.getenv("AGENT_RETRIEVAL_K", "").strip()
    if k:
        args["k"] = int(k)
    return args


def make_retrieve(toolbox: Toolbox):
    async def retrieve(state: AgentState) -> dict:
        output = await toolbox.call("search_transcripts", search_arguments(state["question"]))
        return {"tool_output": output}

    return retrieve


def make_summarize_call(toolbox: Toolbox):
    async def summarize_call(state: AgentState) -> dict:
        output = await toolbox.call("get_call_summary", {"call_id": state["call_id"]})
        return {"tool_output": output}

    return summarize_call
