"""
Smoke test: the MCP server completes a real client handshake and lists its tools.

Regression guard for the crash where get_capabilities() received
notification_options=None. Needs no database and no LLM key.
"""

import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOLS = {"search_transcripts", "get_call_summary", "query_csat"}


@pytest.mark.asyncio
async def test_server_handshake():
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "ccai_mcp.server"],
        cwd=str(REPO_ROOT),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            assert init.serverInfo.name == "contact-center-ai"

            tools = await session.list_tools()
            assert {t.name for t in tools.tools} == EXPECTED_TOOLS
