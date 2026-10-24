# agent

LangGraph agent over the MCP server's tools. Design: [docs/design/agent-graph.md](../docs/design/agent-graph.md).

```
START → classify ─┬─ retrieve ───────────────────────────────────────────────────────────┐
                  ├─ summarize_call ─────────────────────────────────────────────────────┤
                  ├─ resolve_metric [disambiguate → clarify? → execute] ─────────────────┼─→ ground → answer → END
                  └─ explain_change [frame → resolve_measure ⇄ clarify → resolve_subject ┘
                                      → resolve_when → analyze]
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

## Why did it change? (`explain_change`)

A question about why a measure moved, or what members said, around a change ("why did satisfaction on fee calls drop after the fee schedule change?") routes to `explain_change`. `frame` reads the measure, the calls, the time and any logged change out of the question. The resolve nodes map each to something checkable and refuse by name when they cannot: a measure that is not one of the four a change analysis compares (`data/change_measures.yml`), calls that match no type or branch (`data/change_subjects.yml`, `branches.json`), a time phrase outside the grammar in `changes.py` (an ISO date, "June 15", "in June", "since April", "late July"), or a change that is not in `change_log.json`. A refusal calls no tool.

A term that leaves several supported metrics goes through the analyst's `clarify` interrupt, unchanged. `analyze` then calls the one `explain_change` MCP tool, which runs the metric over the two windows (with the SQL), reads the calls in them and counts the drivers in code. `ground` accepts the answer only with both window SQLs, citations that are real calls inside the windows, and no agent id.

The stored `change` outlives the run. A follow-up on the same `thread_id` ("what should supervisors coach on?") reuses its evidence and asks the tool for patterns across the calls of each driver, never a score per agent. The change log and branch list are the generator's `change_log.json` and `branches.json` in `data/synthetic/`; a service that cannot see them says so rather than guessing a date.

## Checkpointer

`agent/checkpoint.py: open_checkpointer()` yields an `AsyncPostgresSaver` on `DATABASE_URL`, with its tables in a `langgraph` schema (created if missing). Pass it to `build_graph(checkpointer=...)`. Tests use `InMemorySaver`.

## Tools

`agent/tools.py` starts `python -m ccai_mcp.server` over stdio and calls the six tools through `langchain.mcp` (`MCPAdapter`, on fastmcp 4 and mcp 2.x). No tool logic lives here.

## Tracing

LangSmith tracing is on by env and off otherwise (`agent/tracing.py`). Set `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY` and `LANGSMITH_PROJECT` in `.env`, and every graph run, from `make demo`, `langgraph dev` or `make evals-live`, lands in that project. A key alone does not trace. The test suite and the offline `make evals` force tracing off whatever `.env` says. Evals for the graph are in [evals/](../evals/README.md).
