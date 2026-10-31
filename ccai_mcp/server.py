"""
MCP server entry point.

Exposes call center RAG capabilities as MCP tools consumable by any
MCP-compatible client (Claude Desktop, custom GenAI apps, etc.).

Local dev (stdio, the default):
    python -m ccai_mcp.server

Streamable HTTP for clients on other machines (opt in; see ccai_mcp/README.md):
    python -m ccai_mcp.server --http          # or MCP_TRANSPORT=http
    -> http://127.0.0.1:8000/mcp; a non-loopback MCP_SERVER_HOST requires MCP_AUTH_TOKEN

Production (Azure App Service):
    - Auth handled by Entra via App Service Easy Auth for MCP
    - See: https://learn.microsoft.com/en-us/azure/app-service/configure-authentication-mcp
    - Infra: infra/terraform/main.tf
"""

import argparse
import asyncio
import logging
import os
import sys

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
    ToolAnnotations,
)

from ccai_mcp.change import SUPPORTED_METRICS, explain_change_blocks
from ccai_mcp.metrics import ask_the_analyst, query_metric
from ccai_mcp.tools import get_call_summary, get_transcript, query_csat, search_transcripts

# Every tool only reads: hosted assistants use this hint to skip a confirmation per call.
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)

TOOLS = [
    Tool(
        annotations=READ_ONLY,
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
        annotations=READ_ONLY,
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
        annotations=READ_ONLY,
        name="get_transcript",
        description=(
            "Get the verbatim dialogue of one call by its ID, with a header of its date, "
            "length (minutes:seconds), category, outcome, agent and member. Use this when "
            "the user wants to read or see the transcript itself; get_call_summary returns "
            "only a short written summary. Account numbers are masked."
        ),
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
        annotations=READ_ONLY,
        name="query_csat",
        description=(
            "Query CSAT survey results from Postgres. Filter by score range, call category "
            "or survey date window (start_date inclusive, end_date exclusive). "
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
                },
                "start_date": {
                    "type": "string",
                    "description": "Only surveys on or after this date (YYYY-MM-DD, by survey date)"
                },
                "end_date": {
                    "type": "string",
                    "description": "Only surveys before this date (YYYY-MM-DD, exclusive, by survey date)"
                }
            }
        }
    ),
    Tool(
        annotations=READ_ONLY,
        name="query_metric",
        description=(
            "Query one or more DECLARED metrics from the MetricFlow semantic layer "
            "(olap/dbt/models/marts/semantic/metrics.yml) and return the result "
            "table plus the SQL the semantic layer generated. There is no bare "
            "ambiguous metric ('interest rate', 'balance', 'LCV'): resolve the "
            "word to its specific lob-qualified metric names first, e.g. "
            "average_mortgage_note_rate, average_deposit_apy, "
            "banking_available_balance, net_member_liquidity, member_lifetime_value. "
            "Optionally narrow the query to a half-open time window and to dimension filters "
            "so one metric can be compared across two windows."
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
                },
                "time_window": {
                    "type": "object",
                    "description": "Optional half-open window [start, end) on the metric's own time (calls by started_at, CSAT by surveyed_at)",
                    "properties": {
                        "start": {"type": "string", "description": "ISO date, inclusive"},
                        "end": {"type": "string", "description": "ISO date, exclusive"}
                    }
                },
                "where": {
                    "type": "array",
                    "description": "Optional filters, ANDed together",
                    "items": {
                        "type": "object",
                        "properties": {
                            "dimension": {
                                "type": "string",
                                "enum": ["interaction__category_code", "member__home_branch", "transaction__fee_type"]
                            },
                            "op": {"type": "string", "enum": ["eq", "in"]},
                            "value": {"description": "A value for eq, or a list of values for in"}
                        },
                        "required": ["dimension", "op", "value"]
                    }
                }
            },
            "required": ["metrics"]
        }
    ),
    Tool(
        annotations=READ_ONLY,
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
    Tool(
        annotations=READ_ONLY,
        name="explain_change",
        description=(
            "Explain why a governed metric moved around a change. Compares the metric "
            "before and after a change point (a date you give, the one logged change in a "
            "period, or the largest weekly shift) with the SQL for both windows, then reads "
            "the calls in the windows and reports the drivers it finds as 'n of M calls', "
            "with verbatim quotes and call ids. Always states what the data could not "
            "determine, including that it cannot show the change caused the shift. Reports "
            "patterns across calls, never per-agent scores. mode='coaching' finds patterns "
            "in how calls were handled inside groups of call ids from an earlier answer."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "metric": {
                    "type": "string",
                    "enum": list(SUPPORTED_METRICS),
                    "description": "The metric to explain (average_handle_time needs change_point)"
                },
                "filters": {
                    "type": "object",
                    "description": "Narrow the calls; with none, the call types whose volume rose are used",
                    "properties": {
                        "category": {"type": "array", "items": {"type": "string"},
                                     "description": "Call categories (e.g. fee_dispute)"},
                        "home_branch": {"type": "array", "items": {"type": "string"},
                                        "description": "Members' home branches"}
                    }
                },
                "change_point": {
                    "type": "string",
                    "description": "ISO date the change happened (YYYY-MM-DD)"
                },
                "search_range": {
                    "type": "object",
                    "description": "Half-open period to look for the change in when no date is given",
                    "properties": {
                        "start": {"type": "string", "description": "ISO date, inclusive"},
                        "end": {"type": "string", "description": "ISO date, exclusive"}
                    },
                    "required": ["start", "end"]
                },
                "window_days": {
                    "type": "integer",
                    "description": "Length of each window in days (default 42; shorter where the data ends)",
                    "minimum": 14,
                    "maximum": 91,
                    "default": 42
                },
                "question": {
                    "type": "string",
                    "description": "The question being asked, to focus the reading of the calls"
                },
                "mode": {
                    "type": "string",
                    "enum": ["drivers", "coaching"],
                    "description": "drivers (default) or coaching",
                    "default": "drivers"
                },
                "groups": {
                    "type": "array",
                    "description": "Coaching mode only: groups of calls to look for patterns in",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "call_ids": {"type": "array", "items": {"type": "string"}}
                        },
                        "required": ["label", "call_ids"]
                    }
                }
            },
            "required": ["metric"]
        }
    ),
]

async def list_tools(
    ctx: ServerRequestContext, params: PaginatedRequestParams | None
) -> ListToolsResult:
    return ListToolsResult(tools=TOOLS)


# Every failure a tool reports starts with this, so a client, the agent's answer and the
# evals can tell a tool failure from an answer (evals.run counts them per group).
TOOL_ERROR_PREFIX = "TOOL ERROR"


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
            return _error(f"{TOOL_ERROR_PREFIX} ({name}): Input validation error: {e.message}")
    try:
        result = await _run_tool(name, arguments)
    except Exception as e:
        return _error(f"{TOOL_ERROR_PREFIX} ({name}): {type(e).__name__}: {e}")
    blocks = [result] if isinstance(result, str) else result
    return CallToolResult(content=[TextContent(type="text", text=text) for text in blocks])


async def _run_tool(name: str, arguments: dict) -> str | list[str]:
    """The tool's text: one string, or a list when the tool returns several text blocks."""
    if name == "search_transcripts":
        result = await asyncio.to_thread(
            search_transcripts,
            query=arguments["query"],
            k=arguments.get("k", 5),
        )
    elif name == "get_call_summary":
        result = await asyncio.to_thread(get_call_summary, call_id=arguments["call_id"])
    elif name == "get_transcript":
        result = await asyncio.to_thread(get_transcript, call_id=arguments["call_id"])
    elif name == "query_csat":
        result = await asyncio.to_thread(
            query_csat,
            min_score=arguments.get("min_score"),
            max_score=arguments.get("max_score"),
            category=arguments.get("category"),
            start_date=arguments.get("start_date"),
            end_date=arguments.get("end_date"),
        )
    elif name == "query_metric":
        result = await asyncio.to_thread(
            query_metric,
            metrics=arguments["metrics"],
            group_by=arguments.get("group_by"),
            decimals=arguments.get("decimals"),
            limit=arguments.get("limit"),
            time_window=arguments.get("time_window"),
            where=arguments.get("where"),
        )
    elif name == "ask_the_analyst":
        result = await asyncio.to_thread(
            ask_the_analyst,
            question=arguments["question"],
            decimals=arguments.get("decimals"),
        )
    elif name == "explain_change":
        result = await asyncio.to_thread(explain_change_blocks, **arguments)
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


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m ccai_mcp.server",
        description="Contact-center MCP server. stdio by default; streamable HTTP when asked.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--http", action="store_true", help="serve streamable HTTP (same as MCP_TRANSPORT=http)")
    group.add_argument("--stdio", action="store_true", help="serve stdio (the default)")
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> None:
    """Pick the transport: a flag wins, then MCP_TRANSPORT, then stdio."""
    args = _parse_args(argv)
    transport = "http" if args.http else "stdio" if args.stdio else (os.environ.get("MCP_TRANSPORT") or "stdio")
    transport = transport.strip().lower()
    if transport == "stdio":
        asyncio.run(main())
    elif transport == "http":
        from ccai_mcp.http_transport import ConfigError, HttpSettings, serve_http

        try:
            settings = HttpSettings.from_env()
        except ConfigError as e:
            sys.exit(f"ccai_mcp.server: refusing to start HTTP transport: {e}")
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
        asyncio.run(serve_http(app, settings))
    else:
        sys.exit(f"ccai_mcp.server: MCP_TRANSPORT must be 'stdio' or 'http', got {transport!r}")


if __name__ == "__main__":
    run()
