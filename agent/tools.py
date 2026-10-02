"""Tools for the graph, served by the repo's MCP server over stdio.

The graph calls the same five tools Claude Desktop sees, through
`langchain.mcp` (the `langchain[mcp]` extra, on fastmcp 4 and mcp 2.x), so no tool
logic lives in `agent/`. Nodes depend on the small `Toolbox` protocol; tests pass a
stub, runtime uses `MCPToolbox`. Only this module imports the MCP client.
"""

import os
import sys
from pathlib import Path
from typing import Any, Protocol

from fastmcp.client.transports import StdioTransport
from langchain.mcp import MCPAdapter
from langchain_core.tools import BaseTool

REPO_ROOT = Path(__file__).resolve().parent.parent


class Toolbox(Protocol):
    async def call(self, name: str, arguments: dict[str, Any]) -> str: ...


def server_connection() -> StdioTransport:
    """Stdio launch spec for `python -m ccai_mcp.server`, run from the repo root.

    `keep_alive=False` closes the server process when a call's session ends, so no
    child outlives the run. The child gets the caller's whole environment: with `env=None`
    the MCP client passes only a safe subset (PATH, HOME, ...), so DATABASE_URL, the
    provider settings and OLLAMA_BASE_URL would silently fall back to localhost defaults,
    which is wrong anywhere but a laptop (a Compose container, for one).
    """
    return StdioTransport(
        command=sys.executable,
        args=["-m", "ccai_mcp.server"],
        cwd=str(REPO_ROOT),
        env=dict(os.environ),
        keep_alive=False,
    )


def _text(result: Any) -> str:
    """Flatten an MCP tool result (string or list of content blocks) to text."""
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        return "\n".join(
            block["text"] if isinstance(block, dict) and "text" in block else str(block)
            for block in result
        )
    return str(result)


class MCPToolbox:
    """Lazy MCP client: the server process starts on first use, not at import.

    `MCPAdapter` opens a short-lived stdio session per tool call, so each call
    starts a server process. Fine for one question per run; a persistent
    session is a follow-up if latency matters.
    """

    def __init__(self) -> None:
        self._adapter = MCPAdapter(server_connection())
        self._tools: dict[str, BaseTool] | None = None

    async def tools(self) -> dict[str, BaseTool]:
        if self._tools is None:
            self._tools = {t.name: t for t in await self._adapter.list_tools()}
        return self._tools

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        tools = await self.tools()
        if name not in tools:
            raise KeyError(f"MCP server has no tool {name!r}; it lists {sorted(tools)}")
        return _text(await tools[name].ainvoke(arguments))
