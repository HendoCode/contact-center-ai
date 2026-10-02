# agent

LangGraph agent over the MCP server's tools. Design: [docs/design/agent-graph.md](../docs/design/agent-graph.md).

```
START → classify ─┬─ retrieve ───────────────────────────────────────────┐
                  ├─ summarize_call ─────────────────────────────────────┼─→ ground → answer → END
                  └─ resolve_metric [disambiguate → clarify? → execute] ─┘
```

Install and test (offline, no DB, no keys):

```bash
uv sync --extra dev --group agent
uv run pytest tests/agent
```

Dev server (Studio / API on `localhost:2024`, in-memory state, chat model from `LLM_PROVIDER`):

```bash
uv run langgraph dev --config agent/langgraph.json --no-browser
```

## Ambiguous terms

`data/ambiguous_terms.yml` lists terms (`rate`, `balance`, `LCV`/`LTV`) and the declared metrics each could mean. When a question uses one without a qualifier, the graph interrupts instead of guessing. Resume on the same `thread_id`:

```python
from langgraph.types import Command

result = await graph.ainvoke({"question": "what is our average rate?"}, config)
payload = result["__interrupt__"][0].value        # {"kind": "clarify_metric", "options": [...], ...}
result = await graph.ainvoke(Command(resume={"choices": ["average_deposit_apy"]}), config)
```

## Checkpointer

`agent/checkpoint.py: open_checkpointer()` yields an `AsyncPostgresSaver` on `DATABASE_URL`, with its tables in a `langgraph` schema (created if missing). Pass it to `build_graph(checkpointer=...)`. Tests use `InMemorySaver`.

## Tools

`agent/tools.py` starts `python -m ccai_mcp.server` over stdio and calls the five tools through `langchain.mcp` (`MCPAdapter`, on fastmcp 4 and mcp 2.x). No tool logic lives here.
