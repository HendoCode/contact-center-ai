"""
MCP server entry point.

Exposes call center RAG capabilities as MCP tools consumable by any
MCP-compatible client (Claude Desktop, custom GenAI apps, etc.).

Local dev:
    python -m ccai_mcp.server

Production (Azure App Service):
    - Auth handled by Entra via App Service Easy Auth for MCP
    - See: https://learn.microsoft.com/en-us/azure/app-service/configure-authentication-mcp
    - Infra: infra/terraform/main.tf
"""

import asyncio

import jsonschema
import mcp.server.stdio
from mcp.server import Server, ServerRequestContext
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)

from ccai_mcp.metrics import ask_the_analyst, query_metric
from ccai_mcp.tools import get_call_summary, query_csat, search_transcripts

TOOLS = [
    Tool(
        name="search_transcripts",
        description=(
            "Search call transcripts using natural language. "
            "Use this to find calls about specific topics, issues, or patterns. "
            "Examples: 'calls where members complained about fees', "
            "'fraud disputes where the member was frustrated', 'calls that were escalated'."
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
            "Query CSAT survey results from Postgres. Filter by score range or call category. "
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
    Tool(
        name="query_metric",
        description=(
            "Query one or more DECLARED metrics from the MetricFlow semantic layer "
            "(olap/dbt/models/marts/semantic/metrics.yml) and return the result "
            "table plus the SQL the semantic layer generated. There is no bare "
            "ambiguous metric ('interest rate', 'balance', 'LCV'): resolve the "
            "word to its specific lob-qualified metric names first, e.g. "
            "average_mortgage_note_rate, average_deposit_apy, "
            "banking_available_balance, net_member_liquidity, member_lifetime_value."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "metrics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Metric name(s) from metrics.yml (e.g. average_mortgage_note_rate)"
                },
                "group_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional dimensions/entities to group by"
                },
                "decimals": {
                    "type": "integer",
                    "description": "Optional fixed-decimal rounding for displayed numbers"
                },
                "limit": {
                    "type": "integer",
                    "description": "Optional row limit"
                }
            },
            "required": ["metrics"]
        }
    ),
    Tool(
        name="ask_the_analyst",
        description=(
            "Answer a natural-language analytics question by resolving it to one or "
            "more DECLARED metrics (seeded with the metrics.yml descriptions), then "
            "executing those metrics through the semantic layer and returning a "
            "grounded answer with the generated SQL. Use this for aggregate metric "
            "questions like 'what is our average interest rate?' — it will NOT "
            "return a single blended number; it returns each declared metric."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Natural-language analytics question (e.g. 'what is our average interest rate?')"
                },
                "decimals": {
                    "type": "integer",
                    "description": "Optional fixed-decimal rounding for displayed numbers"
                }
            },
            "required": ["question"]
        }
    ),
]

async def list_tools(
    ctx: ServerRequestContext, params: PaginatedRequestParams | None
) -> ListToolsResult:
    return ListToolsResult(tools=TOOLS)


def _error(message: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=message)], isError=True)


async def call_tool(ctx: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
    """Run one tool. Bad arguments and tool failures come back as `isError` results.

    mcp 1.x's decorator did both for us; the 2.x low-level `Server` lets an exception
    escape as a JSON-RPC error instead, so a client would lose the message the model reads.
    """
    name, arguments = params.name, params.arguments or {}
    tool = next((t for t in TOOLS if t.name == name), None)
    if tool is not None:
        try:
            jsonschema.validate(arguments, tool.input_schema)
        except jsonschema.ValidationError as e:
            return _error(f"Input validation error: {e.message}")
    try:
        result = await _run_tool(name, arguments)
    except Exception as e:
        return _error(str(e))
    return CallToolResult(content=[TextContent(type="text", text=result)])


async def _run_tool(name: str, arguments: dict) -> str:
    if name == "search_transcripts":
        result = await asyncio.to_thread(
            search_transcripts,
            query=arguments["query"],
            k=arguments.get("k", 5),
        )
    elif name == "get_call_summary":
        result = await asyncio.to_thread(get_call_summary, call_id=arguments["call_id"])
    elif name == "query_csat":
        result = await asyncio.to_thread(
            query_csat,
            min_score=arguments.get("min_score"),
            max_score=arguments.get("max_score"),
            category=arguments.get("category"),
        )
    elif name == "query_metric":
        result = await asyncio.to_thread(
            query_metric,
            metrics=arguments["metrics"],
            group_by=arguments.get("group_by"),
            decimals=arguments.get("decimals"),
            limit=arguments.get("limit"),
        )
    elif name == "ask_the_analyst":
        result = await asyncio.to_thread(
            ask_the_analyst,
            question=arguments["question"],
            decimals=arguments.get("decimals"),
        )
    else:
        result = f"Unknown tool: {name}"
    return result


app = Server(
    "contact-center-ai",
    version="0.1.0",
    on_list_tools=list_tools,
    on_call_tool=call_tool,
)


async def main():
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
