# Agent graph (W2)

D0.2, 2026-10-02. Feeds M0, L1–L4 and M1. Checked against the LangGraph docs and PyPI on 2026-10-02: langgraph 1.2.12, langgraph-checkpoint-postgres 3.1.2, langgraph-cli 0.4.32, langchain 1.4.3, langchain-mcp-adapters 0.3.2, mcp 2.2.0. The repo pins langchain 1.3.4 and mcp 1.27.2.

```
START → classify ─┬─ retrieve ───────────────────────────────────────────┐
                  ├─ summarize_call ─────────────────────────────────────┼─→ ground → answer → END
                  └─ resolve_metric [disambiguate → clarify? → execute] ─┘
```

Routing is explicit: each node calls a named tool. Rejected: a tool-calling loop, which would make the route a model choice that no deterministic eval can check.

## State, nodes, edges

```python
class AgentState(TypedDict, total=False):
    question: str
    route: Literal["retrieve", "resolve_metric", "summarize_call"]
    call_id: str | None
    term: str | None            # ambiguous term, e.g. "rate"
    candidates: list[str]       # metric names offered
    metric_names: list[str]
    clarify_attempts: int
    tool_output: str
    sql: str | None
    citations: list[str]
    grounded: bool
    answer: str
```

The output is `{answer, route, metric_names, sql, citations, grounded}`, all JSON, so L2 reads fields instead of prose. Each run takes one question.

| Node | Behavior |
|---|---|
| `classify` | Routes to `summarize_call` on a `CALL-\d{5}` regex match. Otherwise one structured LLM call picks the route |
| `retrieve`, `summarize_call` | Call MCP `search_transcripts` and `get_call_summary` |
| `ground` | Checks cited `call_id`s with `Retriever.get_by_id` (R1). On the metric route, requires SQL and declared metrics. No LLM, no loop |
| `answer` | Formats tool output, citations and SQL. Adds no facts |

`resolve_metric` is the `analyst` subgraph. It shares the parent's keys and runs with the default `checkpointer=None`, so it inherits the parent's checkpointer and its interrupt surfaces in the parent. Its nodes:

- `disambiguate` reads `agent/data/ambiguous_terms.yml`, which maps each term to candidate metrics plus qualifier words. Starter terms: `rate` (7 metrics), `balance` (10), `LCV`/`LTV` (2). It goes to `query_metric` when exactly one candidate remains, to `clarify` when several remain, and to `ask_the_analyst` when no term matched.
- `clarify` is the only node that calls `interrupt()`. On an invalid answer an edge routes back to it, at most twice. After that, every candidate runs, matching `ask_the_analyst`, which never blends metrics into one number.
- `execute` calls the chosen MCP tool and writes `tool_output`, `sql` and `metric_names`.

## Interrupt contract

L1 owns this contract; the demo, L4 and L2 consume it.

```json
{"kind": "clarify_metric", "version": 1, "term": "rate",
 "prompt": "\"rate\" matches 7 declared metrics. Which do you mean?",
 "options": [{"id": "average_mortgage_note_rate", "label": "Average Mortgage Note Rate"}],
 "multi_select": true}
```

- **Resume:** `Command(resume={"choices": [...]})` on the same `thread_id`. `choices` must be a non-empty subset of the option ids.
- **Detect:** callers check `result["__interrupt__"]`.
- **Rules (LangGraph docs):** one `interrupt()` per run of the node. Never put it inside `try/except`. Nothing with side effects comes before it, because the node restarts from the top on resume.
- **`thread_id`:** a UUID4 minted by the caller. It is the only key a resume needs.

**Checkpointer.** `AsyncPostgresSaver` on the Compose `db`, in a `langgraph` schema set by `search_path` in the connection string (the saver takes no schema argument). Startup calls `await saver.setup()`, which is idempotent. A run resumes by `thread_id` after a restart. Tests use `InMemorySaver`. Rejected: `SqliteSaver`, a second engine when every route already needs Postgres.

## Tools over MCP

`agent/tools.py` opens one `MCPAdapter({"mcpServers": {"ccai": {"command": sys.executable, "args": ["-m", "ccai_mcp.server"]}}})` per process. Nodes call the resulting tools with explicit arguments, so no tool code lives in `agent/`.

The client is `langchain.mcp`, because langchain-mcp-adapters' README says it is no longer maintained. Catch: `langchain[mcp]` needs fastmcp 4, which needs `mcp>=2`, and mcp 2.0 rebuilt the low-level `Server` that `ccai_mcp/server.py` uses. Because one `uv.lock` covers the repo, M0 ports the server first. If M0 slips, L1 starts on `langchain-mcp-adapters==0.3.2` (which needs `mcp<2`) behind `agent/tools.py`. The transport stays stdio until M1 adds `MCP_SERVER_URL`.

## Serving

`agent/langgraph.json` points at `agent.graph:make_graph`, which compiles without a checkpointer, for `langgraph dev` and Studio. `make demo` runs `python -m agent.demo` in-process.

We don't use the standalone Agent Server. Its docs require Redis, `LANGSMITH_API_KEY` and `LANGGRAPH_CLOUD_LICENSE_KEY`, and warn against scale-to-zero, which breaks L3's no-keys check. Instead, L3's `langgraph-api` runs `langgraph dev --host 0.0.0.0` as a labeled dev server, and L4 serves Azure.

**L1 tests (offline, fake LLM, stub tools):**
- one test per route
- the interrupt fires on "average rate" but not on "average mortgage note rate"
- resume with a valid choice, an invalid choice, and after a rebuild
- every term candidate exists in `metrics.yml`
- `agent/tools.py` lists the five real tools

## Tickets

- **M0 · MCP server on mcp 2.x** (**new**) · S. Names and schemas unchanged. Passes the handshake test and the published command.
- **L1 · Agent** (refined) · S · deps D0.2, R1, M0 or the fallback. Adds an `agent` group: langgraph, langgraph-checkpoint-postgres, `langgraph-cli[inmem]`, `langchain[mcp]`.
- **M1 · Streamable HTTP** (**new**) · S · deps M0. Uses `MCP_SERVER_HOST` and `MCP_SERVER_PORT`; adds `MCP_SERVER_URL` per §4.5.
- **L2** (refined). Golden items carry `route`, `expect_interrupt`, `metric_names` and `call_id`.
- **L3** (refined). Dev server as above. No `mcp-server` service until M1.
- **L4 · Agent HTTP endpoint** (**new**) · S · deps L1. Start and resume runs over `AsyncPostgresSaver`. Safe to scale to zero.
