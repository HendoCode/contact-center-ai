"""
Smoke test: the MCP server completes a real client handshake and lists its tools.

Regression guard for the crash where get_capabilities() received
notification_options=None. Needs no database and no LLM key.

The schema snapshot guards the MCP contract: tool names, descriptions and input
schemas must not change (docs/AGENT_HANDOFF.md section 3). The fixture was captured
from the mcp 1.x server before the port to 2.x, so it also proves the port left the
listing untouched. Regenerate it only when a ticket changes a tool on purpose.
"""

import json
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SNAPSHOT = Path(__file__).parent / "fixtures" / "mcp_tool_schemas.json"
EXPECTED_TOOLS = {
    "search_transcripts",
    "get_call_summary",
    "query_csat",
    "query_metric",
    "ask_the_analyst",
}


def _server_params(**kwargs) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable, args=["-m", "ccai_mcp.server"], cwd=str(REPO_ROOT), **kwargs
    )


@pytest.mark.asyncio
async def test_server_handshake():
    async with (
        stdio_client(_server_params()) as (read, write),
        ClientSession(read, write) as session,
    ):
        init = await session.initialize()
        assert init.server_info.name == "contact-center-ai"

        tools = await session.list_tools()
        assert {t.name for t in tools.tools} == EXPECTED_TOOLS


@pytest.mark.asyncio
async def test_tool_schemas_match_snapshot():
    async with (
        stdio_client(_server_params()) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        listed = (await session.list_tools()).tools

    wire = {
        t.name: {
            "description": t.description,
            "inputSchema": t.model_dump(by_alias=True, exclude_none=True)["inputSchema"],
        }
        for t in listed
    }
    assert wire == json.loads(SCHEMA_SNAPSHOT.read_text())


@pytest.mark.asyncio
async def test_bad_arguments_are_a_tool_error_not_a_protocol_error():
    """mcp 1.x answered a missing required argument with an `isError` result; keep that."""
    async with (
        stdio_client(_server_params()) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool("get_call_summary", {})

    assert result.is_error
    assert "call_id" in result.content[0].text
