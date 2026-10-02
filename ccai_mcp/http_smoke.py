"""
Smoke client for the streamable-HTTP transport: list the tools, optionally call one.

    python -m ccai_mcp.http_smoke
    python -m ccai_mcp.http_smoke --call get_call_summary '{"call_id": "CALL-00042"}'

Reads `MCP_SERVER_URL` (default http://127.0.0.1:8000/mcp) and, when the server has one,
`MCP_AUTH_TOKEN`, so one `.env` configures both ends. Exit code 1 if the server is
unreachable, refuses the token, or a called tool returns an error.
"""

import argparse
import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

DEFAULT_URL = "http://127.0.0.1:8000/mcp"


async def smoke(url: str, token: str | None, call: tuple[str, dict] | None) -> int:
    headers = {"Authorization": f"Bearer {token}"} if token else None
    async with (
        create_mcp_http_client(headers=headers) as http,
        streamable_http_client(url, http_client=http) as (read, write),
        ClientSession(read, write) as session,
    ):
        init = await session.initialize()
        print(f"connected: {init.server_info.name} {init.server_info.version} at {url}")
        for tool in (await session.list_tools()).tools:
            print(f"  tool: {tool.name}")
        if call is None:
            return 0
        name, arguments = call
        result = await session.call_tool(name, arguments)
        print(f"call {name}({json.dumps(arguments)}): {'ERROR' if result.is_error else 'ok'}")
        for block in result.content:
            print(getattr(block, "text", block))
        return 1 if result.is_error else 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m ccai_mcp.http_smoke", description=__doc__.split("\n")[1])
    parser.add_argument("--call", nargs=2, metavar=("TOOL", "JSON_ARGS"), help="also call TOOL with JSON_ARGS")
    args = parser.parse_args(argv)
    call = (args.call[0], json.loads(args.call[1])) if args.call else None
    url = os.environ.get("MCP_SERVER_URL") or DEFAULT_URL
    token = (os.environ.get("MCP_AUTH_TOKEN") or "").strip() or None
    try:
        sys.exit(asyncio.run(smoke(url, token, call)))
    except Exception as e:
        while isinstance(e, BaseExceptionGroup):  # the client wraps connect/401 failures in groups
            e = e.exceptions[0]
        hint = " (a 401 shows up as this error: check MCP_AUTH_TOKEN)" if type(e).__name__ == "MCPError" else ""
        sys.exit(f"smoke failed against {url}: {type(e).__name__}: {e}{hint}")


if __name__ == "__main__":
    main()
