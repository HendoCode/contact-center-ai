"""Offline doubles for `make evals`: a scripted classifier, stub MCP tools, and a call
lookup over the generator's corpus. No network, no keys, no database.

The graph's own code runs for real: the call-id shortcut, the summarize -> retrieve
fallback, the ambiguous-term interrupt and its resume, metric resolution from terms,
SQL extraction, grounding and the answer format. What a model would decide is scripted
from the golden item instead, because there is no model offline:

- the classifier returns the item's golden route (call-id questions never reach it);
- `ask_the_analyst` resolves to the item's golden metric names;
- `search_transcripts` cites the lowest call ids in the item's golden categories;
- the change route's `frame` node gets the frame the change spec scripts for the question.

`explain_change` runs the real tool code (`ccai_mcp/change.py`) over doubles built from the
generator's output: a semantic layer and a retriever that answer from the corpus with the
semantic layer's time rules (`evals/change_corpus.py`), and a driver model that labels each
call with its planted driver or pattern from the answer key. So offline the change windows,
sampling, counting, quoting, grounding and answer format run for real, and driver recall is
the ceiling a perfect labeler reaches.

So offline scores measure the graph's deterministic behavior around those decisions.
How well the model makes them is what `make evals-live` measures.
"""

import re
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from unittest import mock

import yaml
from langchain_core.runnables import RunnableLambda

from evals.change_corpus import default_corpus, metric_value, monday
from evals.datasets.build_golden import METRICS_PATH
from retrieval.base import Hit, validate_where

CITED_PER_ANSWER = 3
NO_FRAME = {"measure": None, "subject": None, "when": None, "logged_change": None,
            "follow_up": False}


class ScriptedClassifier:
    """Stands in for the chat model in `classify` (the golden route for each question) and in
    the change route's `frame` (the frame its change spec scripts)."""

    def __init__(self, routes: dict[str, str], frames: dict[str, dict] | None = None):
        self.routes = routes
        self.frames = frames or {}
        self.prompts: list[str] = []

    def with_structured_output(self, schema, **_):
        def respond(prompt: str):
            self.prompts.append(prompt)
            question = prompt.rsplit("Question: ", 1)[-1].strip()
            if "measure" in schema.model_fields:
                return schema(**self.frames.get(question, NO_FRAME))
            return schema(route=self.routes.get(question, "retrieve"))

        return RunnableLambda(respond)


def _descriptions(path: Path = METRICS_PATH) -> dict[str, str]:
    with open(path, encoding="utf-8") as f:
        metrics = yaml.safe_load(f)["metrics"]
    return {m["name"]: " ".join(str(m.get("description", "")).split()) for m in metrics}


def _stub_sql(names: list[str]) -> str:
    return "-- offline stub: no database\nSELECT " + ", ".join(f"NULL AS {n}" for n in names)


class OfflineToolbox:
    """The six MCP tools' output shapes (ccai_mcp/tools.py, ccai_mcp/metrics.py,
    ccai_mcp/change.py), filled from the golden set and the corpus. Records every call."""

    def __init__(self, items: list[dict], calls: list[dict], corpus=None):
        self.by_question = {item["question"]: item for item in items}
        self.calls_by_id = {c["call_id"]: c for c in calls}
        self.calls_by_category: dict[str, list[str]] = {}
        for c in calls:
            self.calls_by_category.setdefault(c["category"], []).append(c["call_id"])
        self.descriptions = _descriptions()
        self.log: list[tuple[str, dict]] = []
        self._corpus = corpus  # the change answer key, loaded on the first change question

    @property
    def corpus(self):
        if self._corpus is None:
            self._corpus = default_corpus()
        return self._corpus

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

    def _query_metric(self, metrics, group_by=None, decimals=None, limit=None,
                      time_window=None, where=None) -> str:
        names = [metrics] if isinstance(metrics, str) else list(metrics)
        unknown = [n for n in names if n not in self.descriptions]
        if not names or unknown:
            return f"query_metric error: Unknown metric name(s): {unknown}"
        if group_by and set(group_by) <= set(TIME_GRAINS) and set(names) <= set(CORPUS_METRICS):
            # A time series (the change route reads the months the data covers this way).
            result = CorpusSemanticLayer(self.corpus)(names, group_by)
            rows = "\n".join("  " + "  ".join(row) for row in result["rows"])
            return (f"MetricFlow result ({len(names)} metric(s)) by {', '.join(group_by)}:\n"
                    f"{rows}\n\nGenerated SQL:\n{result['sql']}")
        return self._render(names)

    def _explain_change(self, **arguments) -> str:
        """The real tool, over the corpus doubles; its two blocks joined as MCPToolbox does."""
        from ccai_mcp import change

        with ExitStack() as stack:
            for name, double in (("query_metric_raw", CorpusSemanticLayer(self.corpus)),
                                 ("get_retriever", lambda: CorpusRetriever(self.corpus)),
                                 ("get_llm", lambda: AnswerKeyModel(self.corpus))):
                stack.enter_context(mock.patch.object(change, name, double))
            return "\n".join(change.explain_change_blocks(**arguments))

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


# ── change-analysis doubles ───────────────────────────────────────────────────

CORPUS_METRICS = ("call_volume", "responses", "csat_average_score",
                  "first_contact_resolution_rate", "average_handle_time")
TIME_GRAINS = {"metric_time__day": "day", "metric_time__week": "week",
               "metric_time__month": "month"}
DIMENSIONS = {"interaction__category_code": "category", "member__home_branch": "branch"}
FILTER_KEYS = {"interaction__category_code": "category", "member__home_branch": "home_branch"}


def _bucket(group: str, record) -> str:
    grain = TIME_GRAINS.get(group)
    if grain is None:
        return str(getattr(record, DIMENSIONS[group]))
    day = record.day
    if grain == "week":
        day = monday(day)
    elif grain == "month":
        day = day.replace(day=1)
    return f"{day.isoformat()} 00:00:00"


class CorpusSemanticLayer:
    """`query_metric_raw` over the corpus: windows, `where` on category and branch, and
    grouping by day, week, month, category or branch."""

    def __init__(self, corpus):
        self.corpus = corpus

    def __call__(self, metric_names, group_by=None, limit=None, *, time_window=None, where=None):
        filters = {FILTER_KEYS[c["dimension"]]: list(c["value"]) for c in where or []}
        start = date.fromisoformat(time_window["start"]) if time_window else None
        end = date.fromisoformat(time_window["end"]) if time_window else None
        columns = [*(group_by or []), *metric_names]
        groups: dict[tuple, dict[str, list]] = {}
        for name in metric_names:
            for record in self.corpus.records(name, filters, start, end):
                key = tuple(_bucket(g, record) for g in group_by or [])
                groups.setdefault(key, {}).setdefault(name, []).append(record)
        if not group_by:
            groups.setdefault((), {})
        rows = []
        for key, by_metric in sorted(groups.items()):
            values = [metric_value(n, by_metric.get(n, [])) for n in metric_names]
            rows.append([*key, *("" if v is None else repr(v) for v in values)])
        window = f"[{time_window['start']}, {time_window['end']})" if time_window else "all data"
        return {
            "metric_names": list(metric_names), "columns": columns, "rows": rows,
            "sql": (f"-- offline double over the generated corpus: {', '.join(metric_names)}, "
                    f"{window}, by {group_by or 'nothing'}, where {where or 'nothing'}\nSELECT 1"),
        }


class CorpusRetriever:
    """`Retriever.count` and `search` over the corpus with the portable `where`; search
    returns every match (the tool asks for the whole window)."""

    def __init__(self, corpus):
        self.corpus = corpus

    def _matches(self, call, where) -> bool:
        tests = {"eq": lambda a, v: a == v, "in": lambda a, v: a in v,
                 "gte": lambda a, v: a >= v, "lt": lambda a, v: a < v, "lte": lambda a, v: a <= v}
        fields = {"category": call.category, "home_branch": call.branch, "date": call.started,
                  "call_id": call.call_id}
        return all(tests[op](fields[f], value) for f, op, value in validate_where(where))

    def count(self, where=None) -> int:
        return sum(self._matches(c, where) for c in self.corpus.calls.values())

    def search(self, query, k=5, where=None, mode="vector") -> list[Hit]:
        found = [c for c in self.corpus.calls.values() if self._matches(c, where)]
        return [Hit(c.call_id, c.text, 1.0, {"category": c.category, "date": c.started})
                for c in found[:k]]


_MEMBER_RE = re.compile(r"MEMBER: (.+?)(?= AGENT:|$)")


def _label(label_id: str) -> str:
    return label_id.replace("_", " ").capitalize()


class AnswerKeyModel:
    """The driver model `explain_change` calls, answered from the ground truth: it proposes
    the planted drivers (or, coaching, the planted patterns) present in the calls it is shown,
    and assigns each call its planted one, quoting the member's first line. Unlabeled calls
    get none."""

    def __init__(self, corpus):
        self.corpus = corpus

    def with_structured_output(self, schema, **_):
        def respond(prompt: str):
            key = "pattern" if ("AGENT handled" in prompt or "agent pattern" in prompt) else "driver"
            blocks = prompt.split("CALLS:\n", 1)[1].split("\n\n[")
            calls = [(cid, text) for cid, _, text in
                     (b.lstrip("[").partition("]\n") for b in blocks)]
            planted = [(cid, text, (self.corpus.labels.get(cid) or {}).get(key))
                       for cid, text in calls]
            if "reasons" in schema.model_fields:
                ids = list(dict.fromkeys(label for _, _, label in planted if label))
                return schema(reasons=[
                    {"label": _label(i), "definition": f"Calls the answer key gives this {key}."}
                    for i in ids])
            listing = prompt.split("S:\n", 1)[1].split("\n\nCALLS:", 1)[0]
            numbers = {line.split(". ", 1)[1].split(":", 1)[0]: int(line.split(".", 1)[0])
                       for line in listing.splitlines() if ". " in line}
            rows = []
            for cid, text, label in planted:
                number = numbers.get(_label(label)) if label else None
                quote = _MEMBER_RE.search(text)
                rows.append({"call_id": cid, "number": number,
                             "quote": quote.group(1) if quote and number else None})
            return schema(assignments=rows)

        return RunnableLambda(respond)


def corpus_call_exists(call_ids: set[str]):
    async def call_exists(call_id: str) -> bool:
        return call_id in call_ids

    return call_exists


def corpus_call_date(calls: list[dict]):
    """The stored date of each call, as the retriever's metadata lookup would give it."""
    dates = {c["call_id"]: c["date"] for c in calls}

    async def call_date(call_id: str) -> str | None:
        return dates.get(call_id)

    return call_date
