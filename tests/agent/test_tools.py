"""agent/tools.py reaches the real MCP server (stdio, no DB, no key) and sees all five tools."""

import pytest

from agent.tools import MCPToolbox, server_connection

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


def test_server_subprocess_inherits_the_callers_environment(monkeypatch):
    # With env=None the MCP client hands the child only PATH, HOME and friends, so a
    # containerized agent's DATABASE_URL and OLLAMA_BASE_URL never reached the server.
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example:5432/contactcenter")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.example:11434")

    env = server_connection().env

    assert env["DATABASE_URL"] == "postgresql://db.example:5432/contactcenter"
    assert env["OLLAMA_BASE_URL"] == "http://ollama.example:11434"
