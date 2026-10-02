"""
Regenerate docs/demo-chat.md: a sample supervisor chat with the output of the real tools.

    uv run --group agent --group dbt python -m tools.demo_chat [--out docs/demo-chat.md]

Needs the seeded stack: Postgres with the OLTP schema loaded (`olap/oltp/apply.sh`,
`olap/seed.py`), `dbt build` run from olap/dbt, and the transcripts ingested (`make ingest`).
Every number, SQL string and call id in the output is read from the running system:

- `query_csat` and `query_metric` are called through the MCP server over stdio
  (`agent.tools.MCPToolbox`, the same binding the agent uses).
- `ask_the_analyst` runs in-process with its metric-resolution step scripted (it needs a chat
  model); the catalog lookup, the MetricFlow query and the SQL are real.
- The clarify turn is the real agent graph with a scripted classifier (as `make evals` does) and
  the real MCP tools; the interrupt payload and the resumed answer are what the graph returned.
- `get_call_summary` and `search_transcripts` need a chat model (and embeddings): this script
  captures the metadata lookup that `get_call_summary` starts with and marks the model-written
  answers as illustrative in the output.
- Allison Hill's account figures come from direct SQL on the marts, labelled as such.

The notes about what was not captured describe the run that committed docs/demo-chat.md (no chat
or embedding model, placeholder embeddings in pgvector); edit them if you regenerate with real
models. No state is written: the script only reads.
"""

import argparse
import asyncio
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

import psycopg2
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.graph import build_graph
from agent.nodes.answer import split_sql
from agent.terms import load_terms
from agent.tools import MCPToolbox
from ccai_mcp import metrics
from evals.offline import ScriptedClassifier
from rag.embeddings import CONNECTION_STRING
from retrieval import get_retriever

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "docs" / "demo-chat.md"

CALL_RATES = "CALL-00421"  # Allison Hill asks about loan rates
CALL_BALANCE = "CALL-00260"  # the agent reads her a balance
RATE_QUESTION = "what is our average interest rate?"
CLARIFY_QUESTION = "What is our average interest rate?"
CLARIFY_CHOICES = ["average_mortgage_note_rate", "weighted_mortgage_portfolio_rate"]
BALANCE_METRICS = [
    "banking_available_balance", "banking_ledger_balance",
    "credit_card_outstanding", "net_member_liquidity",
]
LCV_METRICS = ["loan_to_value", "member_lifetime_value"]
LCV_EXPR = "relationship_revenue * 24 / active_members"

_VALUE_RE = re.compile(r"^\s+([a-z0-9_]+): (\S+)$", re.MULTILINE)


# ── capture ──────────────────────────────────────────────────────────────────

class _ScriptedResolver:
    """Stands in for the chat model in `resolve_metrics`: returns a fixed JSON array."""

    def __init__(self, names: list[str]):
        self.names = names

    def invoke(self, prompt: str):
        return type("Reply", (), {"content": json.dumps(self.names)})()


def values(tool_output: str) -> dict[str, str]:
    """`name: value` lines of a metric tool's result table."""
    return dict(_VALUE_RE.findall(split_sql(tool_output)[0]))


def _rows(sql: str, params: tuple = ()) -> list[tuple]:
    with psycopg2.connect(CONNECTION_STRING) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _member(hit) -> dict:
    """Allison Hill's rows straight from the marts: the cross-check for the transcript figures."""
    member_id = int(hit.metadata["member_id"].removeprefix("MBR-"))
    name = _rows("SELECT legal_name FROM marts.d_member WHERE member_id = %s", (member_id,))[0][0]
    banking = _rows(
        "SELECT banking_available_balance, banking_ledger_balance FROM marts.f_account_snapshot "
        "WHERE member_id = %s AND lob = 'banking'", (member_id,))[0]
    mortgage = _rows(
        "SELECT mortgage_principal_balance, mortgage_note_rate, ltv_at_origination "
        "FROM marts.f_account_snapshot WHERE member_id = %s AND lob = 'mortgage'",
        (member_id,))[0]
    csat = _rows("SELECT score FROM marts.f_csat WHERE interaction_id = %s",
                 (hit.metadata["interaction_id"],))
    return {
        "member_id": member_id, "name": name, "available": banking[0], "ledger": banking[1],
        "principal": mortgage[0], "note_rate": mortgage[1], "ltv": mortgage[2],
        "csat_loan_call": csat[0][0] if csat else None,
    }


async def _clarify_turn(toolbox: MCPToolbox) -> dict:
    classifier = ScriptedClassifier({CLARIFY_QUESTION: "resolve_metric"})
    graph = build_graph(get_llm=lambda: classifier, toolbox=toolbox, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "demo-chat"}}
    paused = await graph.ainvoke({"question": CLARIFY_QUESTION}, config)
    payload = paused["__interrupt__"][0].value
    result = await graph.ainvoke(Command(resume={"choices": CLARIFY_CHOICES}), config)
    return {"payload": payload, "result": result}


async def capture() -> dict:
    toolbox = MCPToolbox()
    retriever = get_retriever()
    cap: dict = {}
    for key, call_id in (("rates_call", CALL_RATES), ("balance_call", CALL_BALANCE)):
        hit = retriever.get_by_id(call_id)
        if hit is None:
            sys.exit(f"{call_id} is not in the vector store: run `make ingest` first.")
        cap[key] = hit
    cap["member"] = _member(cap["rates_call"])
    cap["csat_all"] = await toolbox.call("query_csat", {"category": "loan_inquiry"})
    cap["csat_low"] = await toolbox.call("query_csat", {"category": "loan_inquiry", "max_score": 2})

    rate_candidates = list(load_terms()["rate"].candidates)
    metrics.get_llm = lambda: _ScriptedResolver(rate_candidates)
    cap["rate_candidates"] = rate_candidates
    cap["analyst"] = metrics.ask_the_analyst(RATE_QUESTION, decimals=4)

    cap["clarify"] = await _clarify_turn(toolbox)
    cap["balance"] = await toolbox.call(
        "query_metric", {"metrics": BALANCE_METRICS, "decimals": 2})
    cap["lcv"] = await toolbox.call("query_metric", {"metrics": LCV_METRICS, "decimals": 4})

    cap["catalog"] = metrics.load_metric_catalog()
    cap["env"] = _environment()
    return cap


def _environment() -> dict:
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", ".", ":!docs/demo-chat.md"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.strip()
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=True).stdout.strip()
    postgres = _rows("SHOW server_version")[0][0]
    return {
        "sha": sha + (" (+ uncommitted changes)" if status else ""),
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "python": ".".join(map(str, sys.version_info[:3])),
        "postgres": postgres,
        "versions": {p: metadata.version(p)
                     for p in ("dbt-core", "dbt-postgres", "dbt-metricflow", "langgraph")},
        "calls": _rows("SELECT count(*) FROM interaction")[0][0],
        "csat": _rows("SELECT count(*) FROM csat_survey")[0][0],
    }


# ── render ───────────────────────────────────────────────────────────────────

def _money(value) -> str:
    return f"{float(value):,.2f}"


def _fence(text: str, lang: str = "") -> str:
    return f"```{lang}\n{text.strip()}\n```"


def _json(obj) -> str:
    return _fence(json.dumps(obj, indent=2, ensure_ascii=False), "json")


def _fold(summary: str, body: str) -> str:
    return f"<details>\n<summary>{summary}</summary>\n\n{_fence(body, 'sql')}\n\n</details>"


def _result(tool_output: str) -> str:
    """The tool result with its SQL folded away (the SQL is shown under the answer)."""
    return _fence(split_sql(tool_output)[0])


def _hit_json(hit) -> str:
    return _json({"call_id": hit.call_id, "metadata": hit.metadata, "text": hit.text})


def _csat_numbers(tool_output: str) -> tuple[int, str, dict[str, int]]:
    count = re.search(r"\((\d+) responses\)", tool_output).group(1)
    average = re.search(r"Average score: ([\d.]+)/5", tool_output).group(1)
    dist = {int(k): int(v) for k, v in re.findall(r"(\d): (\d+)", tool_output.split("distribution:")[1])}
    return int(count), average, dist


def _first_sentence(text: str) -> str:
    return text.split(". ")[0].rstrip(".") + "."


def _illustrative(text: str, command: str) -> str:
    quoted = "\n".join(f"> {line}" if line else ">" for line in text.strip().splitlines())
    return (
        "> **ILLUSTRATIVE, not captured output.** Written by hand to show the shape of the answer"
        " from what the transcript says; the wording and the retrieved set depend on the chat"
        " and embedding models. To capture it, run:\n"
        f"> `{command}`\n>\n{quoted}"
    )


def render(cap: dict) -> str:
    env, member = cap["env"], cap["member"]
    rates_call, balance_call = cap["rates_call"], cap["balance_call"]
    catalog = cap["catalog"]
    bal, lcv = values(cap["balance"]), values(cap["lcv"])
    n_all, avg_all, _ = _csat_numbers(cap["csat_all"])
    n_low, _, dist_low = _csat_numbers(cap["csat_low"])
    analyst_vals = values(cap["analyst"])
    clarify = cap["clarify"]
    answer_body = clarify["result"]["answer"].split("\n\nSQL:\n")[0]
    assert LCV_EXPR in split_sql(cap["lcv"])[1], "member_lifetime_value SQL changed; update LCV_EXPR"
    low_note = ", so it is one of those low scores" if (member["csat_loan_call"] or 9) <= 2 else ""
    ledger_in_call = f"${_money(member['ledger'])}" in balance_call.text
    rate_in_call = f"{member['note_rate']}%" in rates_call.text
    principal_in_call = f"${_money(member['principal'])}" in rates_call.text

    parts = [f"""# Demo chat: a supervisor, five tools, four ambiguous words

A sample conversation between a call-center supervisor and the MCP server, to show what each
tool returns and how the semantic layer settles words like "rate", "balance" and "LTV". It
follows one member, **{member['name']}** (`MBR-{member['member_id']:06d}`, seed-42 synthetic
data), through that member's calls.

Generated {env['date']} from commit `{env['sha']}` by `tools/demo_chat.py`, against Postgres
{env['postgres']}, {env['calls']:,} calls and {env['csat']} CSAT responses, marts built by `dbt build`,
Python {env['python']}, {', '.join(f"{p} {v}" for p, v in env['versions'].items())}.

## How to read it

Each turn is **User**, **Tool call** (name and JSON arguments), **Tool result**, **Answer**.
Tool results are verbatim except that the generated SQL is moved into a fold under the answer.
Three kinds of content, never mixed within a block:

| Marker | What it is |
|---|---|
| (no marker) | Captured. The tool or graph ran against the seeded stack and this is what it returned. |
| **Scripted step** | A decision a chat model would make, replaced by a fixed value because there is no model offline. Everything downstream of it ran for real. |
| **ILLUSTRATIVE** | Not run. Shown for shape, with the command that captures it. |

The user's messages and the choice of which tool to call are scripted: no chat model was
available when this was generated. Tool calls are made through the MCP server over stdio, as a
client such as Claude Desktop would. A client that already knows the catalog's metric names
calls `query_metric` directly (turns 6 and 7); the agent asks first (turn 5). Where an **Answer** is not a verbatim tool or graph output,
the sentences are filled in by the generator from the captured values, so a refresh changes them
with the data.

Regenerate (with the stack from `docs/TOUR.md` section B2 running):

```bash
make ingest                                    # transcripts into pgvector
uv run --group agent --group dbt python -m tools.demo_chat
```

---

## Turn 1: what are members saying about loan rates?

**User:** What are members saying when they call about current loan rates?

**Tool call:** `search_transcripts`

{_json({"query": "what are members saying when they call about current loan rates?", "k": 5})}

**Tool result / Answer:**

{_illustrative(
    f"In {CALL_RATES} the member asks about current loan rates for something comparable to"
    f" an account ending 0531, is quoted a note rate and the existing principal balance, and"
    f" is offered a pre-qualification; the call is logged unresolved.",
    'uv run python -c "from ccai_mcp.tools import search_transcripts; '
    'print(search_transcripts(\'what are members saying when they call about current loan '
    'rates?\', k=5))"')}

`search_transcripts` embeds the query, takes the five nearest transcripts from pgvector and has
the chat model answer from them. It is not captured here: the run that produced this page had no embedding or
chat model, and its vector store held placeholder embeddings, which is enough for the metadata
lookup in turn 2 but would rank results meaninglessly.

## Turn 2: summarize that call

**User:** Summarize {CALL_RATES}.

**Tool call:** `get_call_summary`

{_json({"call_id": CALL_RATES})}

**Tool result (captured):** the lookup `get_call_summary` does first, `Retriever.get_by_id`, a
metadata filter on `call_id` and never a semantic search. The metadata carries the same ids the
OLAP star schema uses.

{_hit_json(rates_call)}

**Answer:**

{_illustrative(
    f"A member asked about current loan rates for something comparable to an account ending"
    f" 0531. The agent quoted a note rate of {member['note_rate']}% and the existing"
    f" principal balance of ${_money(member['principal'])}, then offered a pre-qualification."
    f" Outcome: unresolved.",
    f'uv run python -c "from ccai_mcp.tools import get_call_summary; '
    f'print(get_call_summary(\'{CALL_RATES}\'))"')}

Cross-check, direct SQL on the marts (not a tool): `marts.d_member` says member
{member['member_id']} is {member['name']}; the member's mortgage snapshot has `mortgage_note_rate`
{member['note_rate']} and `mortgage_principal_balance` {_money(member['principal'])}. The rate
{'is' if rate_in_call else 'is NOT'} in the transcript above, and so
{'is' if principal_in_call else 'is NOT'} the principal.

## Turn 3: did callers like it?

**User:** How did loan-inquiry callers rate us, and how many were unhappy?

**Tool call:** `query_csat`

{_json({"category": "loan_inquiry"})}

{_result(cap["csat_all"])}

**Tool call:** `query_csat`

{_json({"category": "loan_inquiry", "max_score": 2})}

{_result(cap["csat_low"])}

**Answer:** {n_all} loan-inquiry survey responses average {avg_all} out of 5.
{n_low} of them scored 2 or lower ({dist_low[1]} ones, {dist_low[2]} twos).
{member['name']}'s {CALL_RATES} scored {member['csat_loan_call']} (`marts.f_csat`, interaction
{rates_call.metadata['interaction_id']}){low_note}.
`query_csat` returns the aggregate and a handful of comments, not per-call rows; it reads the
Postgres `f_csat` fact, and CSAT is not in the vector store, so this cannot be combined with a
semantic transcript search.

## Turn 4: "what is our average interest rate?"

**User:** What is our average interest rate?

**Tool call:** `ask_the_analyst`

{_json({"question": RATE_QUESTION, "decimals": 4})}

**Scripted step:** the model maps the question to declared metrics. Here it returns all
{len(cap['rate_candidates'])} candidates listed for "rate" in `agent/data/ambiguous_terms.yml`.
The catalog lookup, the MetricFlow query and the SQL below ran for real.

{_result(cap["analyst"])}

**Answer:** There is no single "interest rate": the catalog declares no metric by that name, so
it resolves to {len(cap['rate_candidates'])} distinct, line-of-business metrics.

| Metric | Value |
|---|---|
""" + "\n".join(
        f"| `{name}` ({catalog[name]['label']}) | {analyst_vals[name]} |"
        for name in cap["rate_candidates"]
    ) + f"""

{_fold("SQL generated by MetricFlow", split_sql(cap["analyst"])[1])}

Each is filtered to its own `product__lob`, so the average mortgage note rate
({analyst_vals['average_mortgage_note_rate']}) is not blended with card APR
({analyst_vals['average_credit_card_purchase_apr']}) or deposit APY
({analyst_vals['average_deposit_apy']}).

## Turn 5: the agent asks instead of guessing

**User:** {CLARIFY_QUESTION}

The same question as turn 4, through the LangGraph agent. "rate" is an ambiguous term in
`agent/data/ambiguous_terms.yml`, so instead of running all seven metrics the graph pauses at
its only `interrupt()` and asks.

**Scripted step:** the classifier returns the `resolve_metric` route. The interrupt below is
what the graph returned.

**Interrupt payload** (`clarify_metric`, version 1):

{_json(clarify['payload'])}

**User:** (picks the two mortgage metrics)

**Resume:** `Command(resume=...)` on the same `thread_id`, with this value:

{_json({"choices": CLARIFY_CHOICES})}

**Answer** (graph output, `route` = `{clarify['result']['route']}`, `grounded` =
`{str(clarify['result']['grounded']).lower()}`, metrics `{', '.join(clarify['result']['metric_names'])}`):

{_fence(answer_body)}

{_fold("SQL attached to the answer", clarify['result']['sql'])}

The two numbers differ because one is a plain average over mortgage accounts and the other is
weighted by principal. The graph attaches the SQL and marks the answer grounded only when a
declared metric and its SQL are both present.

## Turn 6: "balance", and net liquidity

**User:** The agent read {member['name']} a balance of ${_money(member['ledger'])} on
{CALL_BALANCE}. Which balance is that, and what is our net member liquidity?

The transcript, from the same metadata lookup as turn 2:

{_fence(balance_call.text)}

**Tool call:** `query_metric`

{_json({"metrics": BALANCE_METRICS, "decimals": 2})}

{_result(cap["balance"])}

**Answer:** "balance" is not one metric: the catalog declares `banking_available_balance`
(holds and pending excluded), `banking_ledger_balance`, `credit_card_outstanding` (a
liability) and others. Across all members:

| Metric | Value |
|---|---|
""" + "\n".join(f"| `{name}` | {_money(bal[name])} |" for name in BALANCE_METRICS) + f"""

`net_member_liquidity` is the one declared blend, `banking_available_balance -
credit_card_outstanding` ({_money(bal['banking_available_balance'])} - {_money(bal['credit_card_outstanding'])}
= {_money(bal['net_member_liquidity'])}). Only the semantic layer knows the sign conventions; a
raw `SUM(balance)` would add a liability to assets.

{_fold("SQL generated by MetricFlow", split_sql(cap["balance"])[1])}

Cross-check, direct SQL on the marts (not a tool): {member['name']}'s banking account has
`banking_ledger_balance` {_money(member['ledger'])} and `banking_available_balance`
{_money(member['available'])}. The ${_money(member['ledger'])} the agent read out in
{CALL_BALANCE} {'is' if ledger_in_call else 'is NOT'} the **ledger** balance, not the
available one: two declared metrics, ${_money(member['ledger'] - member['available'])} apart on
this account.

## Turn 7: LCV or LTV?

**User:** What's our LTV? Marketing keeps calling it LCV.

**Tool call:** `query_metric`

{_json({"metrics": LCV_METRICS, "decimals": 4})}

{_result(cap["lcv"])}

**Answer:** The two words are different metrics with different meanings.

| Metric | Value | What it is |
|---|---|---|
| `loan_to_value` | {lcv['loan_to_value']} | {_first_sentence(catalog['loan_to_value']['description'])} |
| `member_lifetime_value` | {lcv['member_lifetime_value']} | {_first_sentence(catalog['member_lifetime_value']['description'])} |

The 24 in `{LCV_EXPR}` is the declared convention (the catalog description explains it), stated
once in the metric instead of re-invented per dashboard. It is in the SQL below.

{_fold("SQL generated by MetricFlow", split_sql(cap["lcv"])[1])}

For scale, {member['name']}'s mortgage was written at `ltv_at_origination`
{member['ltv']} (direct SQL, not a tool), against the portfolio average above.

---

## What was and was not captured

- **Captured:** the lookups and every metric, CSAT and SQL figure above, the clarify interrupt
  payload and the graph's resumed answer, and the cross-check SQL.
- **Scripted:** the user's messages, which tool is called, the classifier's route, and the metric
  names `ask_the_analyst` resolves to.
- **Not captured:** `search_transcripts` and the written summary in `get_call_summary`, which
  need a chat model and real embeddings. Each says so where it appears.
"""]
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Regenerate docs/demo-chat.md from live tools.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="file to write")
    args = parser.parse_args()
    args.out.write_text(render(asyncio.run(capture())), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
