"""Offline doubles for `make evals`: a scripted classifier, stub MCP tools, and a call
lookup over the generator's corpus. No network, no keys, no database.

The graph's own code runs for real: the call-id shortcut, the summarize -> retrieve
fallback, the ambiguous-term interrupt and its resume, metric resolution from terms,
SQL extraction, grounding and the answer format. What a model would decide is scripted
from the golden item instead, because there is no model offline:

- the classifier returns the item's golden route (call-id questions never reach it);
- `ask_the_analyst` resolves to the item's golden metric names;
- `search_transcripts` cites the lowest call ids in the item's golden categories.

So offline scores measure the graph's deterministic behavior around those decisions.
How well the model makes them is what `make evals-live` measures.
"""

from pathlib import Path

import yaml
from langchain_core.runnables import RunnableLambda

from evals.datasets.build_golden import METRICS_PATH

CITED_PER_ANSWER = 3


class ScriptedClassifier:
    """Stands in for the chat model in `classify`: the golden route for each question."""

    def __init__(self, routes: dict[str, str]):
        self.routes = routes
        self.prompts: list[str] = []

    def with_structured_output(self, schema):
        def respond(prompt: str):
            self.prompts.append(prompt)
            question = prompt.rsplit("Question: ", 1)[-1].strip()
            return schema(route=self.routes.get(question, "retrieve"))

        return RunnableLambda(respond)


def _descriptions(path: Path = METRICS_PATH) -> dict[str, str]:
    with open(path, encoding="utf-8") as f:
        metrics = yaml.safe_load(f)["metrics"]
    return {m["name"]: " ".join(str(m.get("description", "")).split()) for m in metrics}


def _stub_sql(names: list[str]) -> str:
    return "-- offline stub: no database\nSELECT " + ", ".join(f"NULL AS {n}" for n in names)


class OfflineToolbox:
    """The five MCP tools' output shapes (ccai_mcp/tools.py, ccai_mcp/metrics.py), filled
    from the golden set and the corpus. Records every call."""

    def __init__(self, items: list[dict], calls: list[dict]):
        self.by_question = {item["question"]: item for item in items}
        self.calls_by_id = {c["call_id"]: c for c in calls}
        self.calls_by_category: dict[str, list[str]] = {}
        for c in calls:
            self.calls_by_category.setdefault(c["category"], []).append(c["call_id"])
        self.descriptions = _descriptions()
        self.log: list[tuple[str, dict]] = []

    async def call(self, name: str, arguments: dict) -> str:
        self.log.append((name, arguments))
        return getattr(self, f"_{name}")(**arguments)

    def _search_transcripts(self, query: str, k: int = 5) -> str:
        item = self.by_question.get(query, {})
        cited = sorted(
            cid for cat in item.get("categories", [])
            for cid in self.calls_by_category.get(cat, [])[:CITED_PER_ANSWER]
        )
        if not cited:
            return "None of the retrieved transcripts address that question."
        lines = [
            f"- {cid}: a {self.calls_by_id[cid]['category'].replace('_', ' ')} call, "
            f"outcome {self.calls_by_id[cid]['outcome']}."
            for cid in cited
        ]
        return "The most relevant calls:\n" + "\n".join(lines)

    def _get_call_summary(self, call_id: str) -> str:
        call = self.calls_by_id.get(call_id)
        if call is None:
            return f"No transcript found for call ID: {call_id}"
        return (
            f"{call_id} ({call['date'][:10]}): a {call['category'].replace('_', ' ')} call "
            f"handled by {call['agent_id']}; outcome {call['outcome']}."
        )

    def _render(self, names: list[str]) -> str:
        rows = "\n".join(f"  {n}: (offline stub)" for n in names)
        return (
            f"MetricFlow result ({len(names)} metric(s)):\n{rows}\n\n"
            f"Generated SQL:\n{_stub_sql(names)}"
        )

    def _query_metric(self, metrics, group_by=None, decimals=None, limit=None) -> str:
        names = [metrics] if isinstance(metrics, str) else list(metrics)
        unknown = [n for n in names if n not in self.descriptions]
        if not names or unknown:
            return f"query_metric error: Unknown metric name(s): {unknown}"
        return self._render(names)

    def _ask_the_analyst(self, question: str, decimals=None) -> str:
        names = self.by_question.get(question, {}).get("metric_names") or []
        if not names:
            return f'No DECLARED metric matches "{question}".'
        lines = [
            f'Question: "{question}"',
            f"Resolved to {len(names)} declared metric(s):",
            "",
            *(f"- {n} — {self.descriptions[n]}" for n in names),
            "",
            self._render(names),
        ]
        return "\n".join(lines)

    def _query_csat(self, **_: object) -> str:
        return "CSAT Summary (offline stub)"


def corpus_call_exists(call_ids: set[str]):
    async def call_exists(call_id: str) -> bool:
        return call_id in call_ids

    return call_exists
