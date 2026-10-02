"""Tools for the graph, served by the repo's MCP server over stdio.

The graph calls the same five tools Claude Desktop sees, through
langchain-mcp-adapters, so no tool logic lives in `agent/`. Nodes depend on the
small `Toolbox` protocol; tests pass a stub, runtime uses `MCPToolbox`.

Fallback from docs/design/agent-graph.md: `langchain-mcp-adapters==0.3.2`
(needs mcp<2) until the MCP server is ported to mcp 2.x. Only this module
imports the adapter, so the swap to `langchain.mcp` is local.
"""

import sys
from pathlib import Path
from typing import Any, Protocol

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

REPO_ROOT = Path(__file__).resolve().parent.parent


class Toolbox(Protocol):
    async def call(self, name: str, arguments: dict[str, Any]) -> str: ...


def server_connection() -> dict[str, Any]:
    """Stdio launch spec for `python -m ccai_mcp.server`, run from the repo root."""
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": ["-m", "ccai_mcp.server"],
        "cwd": str(REPO_ROOT),
    }


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

    The adapter opens a short-lived stdio session per tool call, so each call
    starts a server process. Fine for one question per run; a persistent
    session is a follow-up if latency matters.
    """

    def __init__(self) -> None:
        self._client = MultiServerMCPClient({"ccai": server_connection()})
        self._tools: dict[str, BaseTool] | None = None

    async def tools(self) -> dict[str, BaseTool]:
        if self._tools is None:
            self._tools = {t.name: t for t in await self._client.get_tools()}
        return self._tools

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        tools = await self.tools()
        if name not in tools:
            raise KeyError(f"MCP server has no tool {name!r}; it lists {sorted(tools)}")
        return _text(await tools[name].ainvoke(arguments))
