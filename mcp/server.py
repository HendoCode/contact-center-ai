"""
MCP server entry point.

Exposes call center RAG capabilities as MCP tools consumable by any
MCP-compatible client (Claude Desktop, custom GenAI apps, etc.).

Local dev:
    python mcp/server.py

Production (Azure App Service):
    - Auth handled by Entra via App Service Easy Auth for MCP
    - See: https://learn.microsoft.com/en-us/azure/app-service/configure-authentication-mcp
    - Infra: infra/terraform/main.tf
"""

import mcp.server.stdio
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.types import Tool, TextContent
import mcp.types as types

from mcp.tools import search_transcripts, get_call_summary, query_csat

app = Server("contact-center-ai")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_transcripts",
            description=(
                "Search call transcripts using natural language. "
                "Use this to find calls about specific topics, issues, or patterns. "
                "Examples: 'calls where members complained about fees', "
                "'fraud disputes from last week', 'calls that were escalated'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query"
                    },
                    "k": {
                        "type": "integer",
                        "description": "Number of transcripts to retrieve (default: 5)",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_call_summary",
            description="Get a summary of a specific call by its ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "call_id": {
                        "type": "string",
                        "description": "The call ID (e.g. CALL-00042)"
                    }
                },
                "required": ["call_id"]
            }
        ),
        Tool(
            name="query_csat",
            description=(
                "Query CSAT survey results. Filter by score range or call category. "
                "Use this to understand member satisfaction trends."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "min_score": {
                        "type": "integer",
                        "description": "Minimum CSAT score (1-5)",
                        "minimum": 1,
                        "maximum": 5
                    },
                    "max_score": {
                        "type": "integer",
                        "description": "Maximum CSAT score (1-5)",
                        "minimum": 1,
                        "maximum": 5
                    },
                    "category": {
                        "type": "string",
                        "description": "Filter by call category (e.g. fraud_dispute, loan_inquiry)"
                    }
                }
            }
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "search_transcripts":
        result = search_transcripts(
            query=arguments["query"],
            k=arguments.get("k", 5)
        )
    elif name == "get_call_summary":
        result = get_call_summary(call_id=arguments["call_id"])
    elif name == "query_csat":
        result = query_csat(
            min_score=arguments.get("min_score"),
            max_score=arguments.get("max_score"),
            category=arguments.get("category"),
        )
    else:
        result = f"Unknown tool: {name}"

    return [TextContent(type="text", text=result)]


async def main():
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="contact-center-ai",
                server_version="0.1.0",
                capabilities=app.get_capabilities(
                    notification_options=None,
                    experimental_capabilities={}
                )
            )
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
