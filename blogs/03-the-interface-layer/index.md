---
title: "The Interface Layer"
subtitle: "MCP as enterprise glue: how three tools become a shared AI platform"
author: Stephen Henderson
date: "June 2026"
post_number: "03"
description: "Post 3 of Anchoring AI: inside the MCP server — tool schemas, the stdio transport, call routing, and why the Model Context Protocol is the right abstraction for a multi-team AI platform."
prev_url: "../02-from-text-to-vectors/"
prev_title: "From Text to Vectors"
next_url: "../04-run-anywhere/"
next_title: "Run Anywhere"
---

The RAG pipeline from Post 2 is powerful, but it's a library. It has no interface. You can call it from a script, embed it in a web app, or wire it to an API — but each of those choices means building something new, and each team that wants to use it would build their own version.

The Model Context Protocol changes that. Instead of building a team-specific application on top of the RAG pipeline, we expose it once as MCP tools, and any MCP-compatible client — Claude Desktop, a future internal app, another team's AI workflow — can call those tools without knowing anything about the underlying implementation.

This post is about how that exposure works.

---

## What MCP Actually Is

MCP is a protocol — a standardized way for AI clients and tool servers to talk to each other. If you've worked with language model function calling (OpenAI's `tools` parameter, Anthropic's `tools` block), the concept is similar: a model can decide to invoke a named function, receives a result, and incorporates it into its response.

What MCP adds is the standardization layer. Instead of each LLM vendor defining their own tool-calling format, MCP defines one protocol that any client and any server can speak. A tool server written today works with Claude Desktop, and — in principle — with any other MCP-compatible client that exists or will exist.

The other key difference from function calling: **the tool server is a separate process.** It runs independently, potentially on a different machine or in a cloud service. The client connects to it over stdio (for local development) or HTTPS (for production). The server registers its tools and handles calls; it doesn't need to know anything about the client.

For our use case — multiple teams at a credit union all wanting access to the same call center data — this matters a lot. The MCP server is infrastructure, not a feature. You deploy it once and every team's AI client can use it.

---

## The Server in 30 Lines

The entire entry point is `mcp/server.py`. Here's the core of it:

```python
from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.tools import search_transcripts, get_call_summary, query_csat

app = Server("contact-center-ai")

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="search_transcripts", description="...", inputSchema={...}),
        Tool(name="get_call_summary",   description="...", inputSchema={...}),
        Tool(name="query_csat",         description="...", inputSchema={...}),
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "search_transcripts":
        result = search_transcripts(query=arguments["query"], k=arguments.get("k", 5))
    elif name == "get_call_summary":
        result = get_call_summary(call_id=arguments["call_id"])
    elif name == "query_csat":
        result = query_csat(...)
    else:
        result = f"Unknown tool: {name}"
    return [TextContent(type="text", text=result)]

async def main():
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, InitializationOptions(...))
```

Three components. `list_tools()` registers what the server can do. `call_tool()` handles incoming calls. `main()` runs the server loop over stdio. That's it.

The simplicity is intentional. The MCP SDK handles the protocol mechanics — handshakes, message framing, error responses. The application code stays focused on what it knows: which tools exist and how to execute them.

---

## Tool Schemas: How the Client Knows What to Ask

The `inputSchema` on each tool is a JSON Schema object. It's how the MCP client — and ultimately the LLM — learns what parameters a tool accepts, which ones are required, and what values are valid.

Here's the schema for `search_transcripts`:

```python
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
)
```

Two things shape how a tool gets used in practice:

**The description is for the model, not the user.** When Claude Desktop connects and the supervisor types a question, Claude reads the tool description to decide whether to invoke the tool and how to fill in the parameters. A vague description like "search calls" produces worse tool use than one with concrete examples. The examples in the description (`'fraud disputes from last week'`, `'calls that were escalated'`) aren't documentation — they're few-shot prompts that guide the model toward useful queries.

**`required` controls what the model must provide.** `query` is required; `k` is optional with a default. The model will always fill in the query, and will only specify `k` if the user gives a reason to retrieve more or fewer results. This keeps the common case simple and the power-user case available.

---

## The Dispatch Router

`call_tool()` is the simplest possible router: a chain of `if/elif` statements.

```python
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
```

Everything returns `list[TextContent]` — a list containing one text object. The MCP protocol supports richer content types (images, structured data), but text is the right choice here: the LLM that called the tool is going to read the result and incorporate it into a natural-language response. Plain text is what it needs.

The `.get()` calls with defaults are intentional: they make the router tolerant of optional parameters that the model might not pass. If the model doesn't specify `k`, `arguments.get("k", 5)` returns 5. If it doesn't specify `min_score`, `arguments.get("min_score")` returns `None`, and the tool function handles `None` gracefully.

---

## The Three Tool Implementations

The actual work happens in `mcp/tools.py`. Let's look at each one honestly.

### `search_transcripts` — the clean one

```python
def search_transcripts(query: str, k: int = 5) -> str:
    return rag_query(query, k=k)
```

One line. It delegates entirely to `rag_query()` from Post 2. This is the cleanest tool: its schema matches its implementation exactly, and the implementation is just the RAG pipeline.

### `get_call_summary` — the quirky one

```python
def get_call_summary(call_id: str) -> str:
    results = retrieve(f"call_id:{call_id}", k=10)
    matches = [doc for doc in results if doc.metadata.get("call_id") == call_id]

    if not matches:
        return f"No transcript found for call ID: {call_id}"

    doc = matches[0]
    return rag_query(
        "Summarize this call: what was the member's issue, "
        "how did the agent handle it, and what was the outcome?",
        k=1,
    )
```

This one is worth studying, because it has an interesting inefficiency. The function:

1. Retrieves documents by embedding the string `"call_id:CALL-00042"` and doing a semantic search — using the vector store as a lookup when a direct metadata filter would be cleaner
2. Filters the results to find the exact `call_id` match
3. Finds the right document... and then ignores it
4. Calls `rag_query()` with a fixed summary prompt and `k=1`, which does a *fresh* retrieval pass on its own

The `doc` variable on line 4 is never used in the final response. The summary is generated from whatever `rag_query()` retrieves with `k=1` — which may or may not be the specific call being asked about.

For a portfolio project with a small, consistent dataset, this usually works fine in practice: the `k=1` retrieval typically surfaces the right call. In production, with a growing corpus, it would be wrong often enough to matter. The fix is to pass the retrieved document directly to the LLM rather than triggering another retrieval pass.

This is the kind of thing that's worth being honest about. The architecture is sound; the implementation has rough edges.

### `query_csat` — the bypass

```python
def query_csat(min_score=None, max_score=None, category=None) -> str:
    csat_path = Path(__file__).parent.parent / "data" / "synthetic" / "csat.json"
    with open(csat_path) as f:
        csat_data = json.load(f)

    filtered = csat_data
    if min_score is not None:
        filtered = [r for r in filtered if r["score"] >= min_score]
    if max_score is not None:
        filtered = [r for r in filtered if r["score"] <= max_score]

    avg_score = sum(r["score"] for r in filtered) / len(filtered)
    score_dist = {i: sum(1 for r in filtered if r["score"] == i) for i in range(1, 6)}
    sample_comments = [r["comment"] for r in filtered[:5]]

    return f"CSAT Summary ({len(filtered)} responses)\nAverage score: {avg_score:.2f}/5\n..."
```

`query_csat` doesn't touch pgvector or the LLM. It reads `csat.json` directly, filters in Python, and returns a formatted summary. Two things worth noting:

First, the `category` parameter is defined in the tool schema and accepted by the function signature — but the implementation never filters by it. The parameter is silently ignored. This is a genuine bug: if Claude passes `category="fraud_dispute"` because the user asked about fraud call satisfaction, the filter doesn't apply and the result is unscoped. Easy fix for a future post.

Second, this bypass is actually the *correct* architecture for structured data at this scale. CSAT data is small, it has a clear schema, and questions about it are aggregations over numeric scores — exactly the kind of thing in-memory filtering handles perfectly. Adding it to pgvector would add complexity and latency with no benefit. If the dataset grew to millions of records, you'd move to SQL; at 112 rows, loading a JSON file is the right call.

---

## How stdio Works

Running `python -m mcp.server` starts the server and puts it into `stdio_server()` mode. The process reads MCP messages from stdin and writes responses to stdout. That's the entire transport.

Claude Desktop connects by spawning the server process directly. The `claude_desktop_config.json` entry for this server looks like:

```json
{
  "mcpServers": {
    "contact-center-ai": {
      "command": "python",
      "args": ["-m", "mcp.server"],
      "cwd": "/path/to/contact-center-ai",
      "env": {
        "OPENAI_API_KEY": "sk-...",
        "DATABASE_URL": "postgresql://..."
      }
    }
  }
}
```

Claude Desktop spawns the process, sends an initialization handshake, calls `list_tools()` to discover what's available, and then routes relevant user queries to the appropriate tool. The user sees tool results incorporated into Claude's response — they don't see the raw tool call at all.

The stdio model is clean for local development but doesn't work for production: you can't spawn a local process from a cloud-hosted client. In production on Azure App Service, the MCP SDK's HTTP transport layer surfaces the same tools over HTTPS. The Python code is identical; only the transport changes. We'll cover the infrastructure side of that in Post 5.

---

## Why MCP and Not a REST API?

The question comes up. If you already have a RAG pipeline, why not expose it as a REST endpoint and call it with `fetch()`?

Three reasons for this system in particular:

**Schema-driven tool use.** A REST API requires the calling application to know when to call it and how to format the request. With MCP, the LLM reads the tool schema and decides on its own whether a user's question warrants a tool call and what parameters to pass. The schema *is* the integration contract.

**Multi-client without glue code.** A REST API means every client team writes their own HTTP client, their own auth handling, their own error handling. MCP clients implement the protocol once and pick up new tools automatically when the server adds them. When the compliance team adds a `search_policies` tool to this server next quarter, every connected client gets it without a code change on their end.

**Transport agnosticism.** The same server code runs over stdio locally and over HTTPS in production. Swapping transports doesn't touch the business logic.

The trade-off is ecosystem maturity — MCP is newer than REST and there are fewer off-the-shelf tools for testing, monitoring, and debugging it. That's a real cost, and it's part of why observability (Posts 6 and 7) matters more here than it would for a well-worn REST API.

---

## What's Next

The MCP server is built. The RAG pipeline is working behind it. In Post 4, we'll swap the provider — replacing OpenAI with Ollama so the entire system runs locally without an API key — and talk about what that tradeoff actually looks like in practice.

If you want to connect Claude Desktop to this server right now:

```bash
# Start the server (leave this running)
python -m mcp.server

# In claude_desktop_config.json, add the server entry above.
# Restart Claude Desktop and the tools appear automatically.
```

Then ask Claude: *"Use the search_transcripts tool to find calls where members were frustrated with wait times."* It will invoke the tool, retrieve from pgvector, and synthesize an answer — all transparently.
