"""agent/tools.py reaches the real MCP server (stdio, no DB, no key) and sees all five tools."""

import pytest

from agent.tools import MCPToolbox

EXPECTED_TOOLS = {
    "search_transcripts",
    "get_call_summary",
    "query_csat",
    "query_metric",
    "ask_the_analyst",
}


@pytest.mark.asyncio
async def test_toolbox_lists_the_five_real_tools():
    tools = await MCPToolbox().tools()
    assert set(tools) == EXPECTED_TOOLS


@pytest.mark.asyncio
async def test_unknown_tool_name_is_an_error():
    with pytest.raises(KeyError, match="no tool 'nope'"):
        await MCPToolbox().call("nope", {})
