"""retrieve and summarize_call: one MCP tool call each, explicit arguments."""

from agent.state import AgentState
from agent.tools import Toolbox


def make_retrieve(toolbox: Toolbox):
    async def retrieve(state: AgentState) -> dict:
        output = await toolbox.call("search_transcripts", {"query": state["question"]})
        return {"tool_output": output}

    return retrieve


def make_summarize_call(toolbox: Toolbox):
    async def summarize_call(state: AgentState) -> dict:
        output = await toolbox.call("get_call_summary", {"call_id": state["call_id"]})
        return {"tool_output": output}

    return summarize_call
