"""An in-memory semantic layer, retriever and model for ccai_mcp/change.py.

A small world of calls with one planted change: from CHANGE on, calls of one category
quadruple, their survey scores drop a point, their first-contact resolution falls and they run
longer. The fakes answer the same calls the real ones do (MetricFlow's `query_metric_raw`,
`Retriever.count` and `search`, a chat model's structured output), so the tool's own logic is
what the tests exercise.
"""

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from langchain_core.runnables import RunnableLambda

from retrieval.base import Hit, validate_where

START = date(2026, 1, 5)  # a Monday
END = date(2026, 7, 6)  # exclusive: 26 whole weeks
CHANGE = date(2026, 4, 6)  # a Monday, 13 weeks in
HOT = "alpha"  # the category the change hits
OTHERS = ("beta", "gamma")
NOTICE = "Nobody sent me a notice about this."
REFUND = "I can offer a partial refund once a year."

TEXTS = {
    "notice": (f"MEMBER: {NOTICE} AGENT: The notice went out by mail. "
               "MEMBER: I never saw it."),
    "refund": (f"MEMBER: I would like a refund. AGENT: {REFUND} "
               "MEMBER: That is not what I was told."),
    "other": "MEMBER: I have a question about my account. AGENT: Happy to help with that.",
}


@dataclass
class Call:
    call_id: str
    day: date
    category: str
    branch: str
    score: int | None
    resolved: bool
    seconds: int
    kind: str  # notice | refund | other
    text: str

    @property
    def stamp(self) -> str:
        return datetime(self.day.year, self.day.month, self.day.day, 10).isoformat()


def build_world(*, event: bool = True, extra_after: int = 0) -> list[Call]:
    """Weekday calls: one a day in each category, four a day in HOT from CHANGE on.
    `extra_after` adds that many more HOT calls after the change (to pass the sample cap)."""
    calls: list[Call] = []
    day, index = START, 0
    while day < END:
        if day.weekday() < 5:
            for category in (HOT, *OTHERS):
                per_day = 4 if event and category == HOT and day >= CHANGE else 1
                for _ in range(per_day):
                    index += 1
                    hot = event and category == HOT and day >= CHANGE
                    kind = ("notice", "refund", "other")[index % 3] if hot else "other"
                    score = None if index % 2 else (4, 5, 4, 3, 5)[(index // 2) % 5]
                    if score is not None and hot:
                        score = max(1, score - 1)
                    calls.append(Call(
                        call_id=f"CALL-{index:05d}", day=day, category=category,
                        branch="South" if index % 5 == 0 else "North", score=score,
                        resolved=not (hot and index % 2 == 0) and index % 3 != 0,
                        seconds=300 + (index % 5) * 10 + (120 if hot else 0), kind=kind,
                        text=TEXTS[kind],
                    ))
        day += timedelta(days=1)
    for extra in range(extra_after):
        index += 1
        calls.append(Call(
            call_id=f"CALL-{index:05d}", day=CHANGE + timedelta(days=extra % 30), category=HOT,
            branch="North", score=None, resolved=True, seconds=400,
            kind=("notice", "refund")[extra % 2], text=TEXTS[("notice", "refund")[extra % 2]],
        ))
    return calls


def in_window(calls: list[Call], start: date, end: date, **filters) -> list[Call]:
    return [c for c in calls if start <= c.day < end
            and (not filters.get("category") or c.category in filters["category"])
            and (not filters.get("home_branch") or c.branch in filters["home_branch"])]


# ── the semantic layer ───────────────────────────────────────────────────────

def _value(metric: str, rows: list[Call]):
    if metric == "call_volume":
        return len(rows)
    scored = [c.score for c in rows if c.score is not None]
    if metric == "responses":
        return len(scored)
    if metric == "csat_average_score":
        return sum(scored) / len(scored) if scored else None
    if metric == "first_contact_resolution_rate":
        return sum(c.resolved for c in rows) / len(rows) if rows else None
    if metric == "average_handle_time":
        return sum(c.seconds for c in rows) / len(rows) if rows else None
    raise KeyError(metric)


def _group_key(group: str, call: Call) -> str:
    if group == "metric_time__week":
        return (call.day - timedelta(days=call.day.weekday())).isoformat() + " 00:00:00"
    if group == "metric_time__day":
        return call.day.isoformat() + " 00:00:00"
    return {"interaction__category_code": call.category,
            "member__home_branch": call.branch}[group]


class FakeSemanticLayer:
    """`query_metric_raw`, over the world; every query is kept in `queries`."""

    def __init__(self, calls: list[Call]):
        self.calls = calls
        self.queries: list[dict] = []

    def __call__(self, metric_names, group_by=None, limit=None, *, time_window=None, where=None):
        self.queries.append({"metrics": list(metric_names), "group_by": group_by,
                             "time_window": time_window, "where": where})
        rows = self.calls
        if time_window:
            start = date.fromisoformat(time_window["start"])
            end = date.fromisoformat(time_window["end"])
            rows = [c for c in rows if start <= c.day < end]
        for clause in where or []:
            attr = {"interaction__category_code": "category", "member__home_branch": "branch"}[
                clause["dimension"]]
            rows = [c for c in rows if getattr(c, attr) in clause["value"]]
        groups: dict[tuple, list[Call]] = {}
        for call in rows:
            groups.setdefault(tuple(_group_key(g, call) for g in group_by or []), []).append(call)
        if not group_by:
            groups = {(): rows}
        table = [[*key, *("" if (v := _value(m, members)) is None else str(v)
                          for m in metric_names)] for key, members in sorted(groups.items())]
        window = f"[{time_window['start']}, {time_window['end']})" if time_window else "all"
        return {
            "metric_names": list(metric_names), "columns": [*(group_by or []), *metric_names],
            "rows": table,
            "sql": f"-- fake semantic layer: {','.join(metric_names)} window {window} "
                   f"by {group_by} where {where}\nSELECT 1",
        }


# ── the retriever ────────────────────────────────────────────────────────────

class FakeRetriever:
    """`Retriever.count` and `search` over the world, with the portable `where`."""

    def __init__(self, calls: list[Call]):
        self.calls = calls
        self.searches: list[dict] = []

    def _match(self, call: Call, where: dict | None) -> bool:
        tests = {
            "eq": lambda actual, value: actual == value,
            "in": lambda actual, value: actual in value,
            "gte": lambda actual, value: actual >= value,
            "lt": lambda actual, value: actual < value,
            "lte": lambda actual, value: actual <= value,
        }
        for field, op, value in validate_where(where):
            actual = {"category": call.category, "home_branch": call.branch,
                      "date": call.stamp, "call_id": call.call_id}[field]
            if not tests[op](actual, value):
                return False
        return True

    def count(self, where=None) -> int:
        return sum(self._match(c, where) for c in self.calls)

    def search(self, query, k=5, where=None, mode="vector") -> list[Hit]:
        self.searches.append({"query": query, "k": k, "where": where})
        found = [c for c in self.calls if self._match(c, where)]
        return [Hit(c.call_id, c.text, 1.0, {"category": c.category, "date": c.stamp})
                for c in found[:k]]


# ── the model ────────────────────────────────────────────────────────────────

def sentence_with(text: str, word: str) -> str | None:
    for part in text.replace("AGENT:", "|AGENT:").replace("MEMBER:", "|MEMBER:").split("|"):
        if word in part.lower():
            return part.strip()
    return None


class FakeModel:
    """Proposes two reasons (or two coaching patterns) and assigns calls to them by a word
    each one uses. `quotes` can corrupt the quotes to prove the tool does not trust them."""

    def __init__(self, quotes: str = "honest", fail: bool = False):
        self.quotes, self.fail = quotes, fail
        self.prompts: list[str] = []
        self.reasons = [("Members were not told in advance", "A change the member was not told about.", "notice"),
                        ("Refund requests were turned down", "The member asked for money back.", "refund")]
        self.patterns = [("Rule described differently", "The agent describes a rule in different ways.", "once a year")]

    def with_structured_output(self, schema):
        def respond(prompt: str):
            self.prompts.append(prompt)
            if self.fail:
                raise ValueError("model unavailable")
            coaching = "AGENT handled" in prompt or "agent pattern" in prompt
            if "reasons" in schema.model_fields:
                chosen = self.patterns if coaching else self.reasons
                return schema(reasons=[{"label": label, "definition": definition}
                                       for label, definition, _ in chosen])
            chosen = self.patterns if coaching else self.reasons
            blocks = prompt.split("CALLS:\n", 1)[1].split("\n\n[")
            rows = []
            for block in blocks:
                call_id, _, text = block.lstrip("[").partition("]\n")
                number, quote = None, None
                for i, (_, _, word) in enumerate(chosen, start=1):
                    if word in text.lower():
                        number, quote = i, sentence_with(text, word)
                        break
                if self.quotes == "invented" and number:
                    quote = "This sentence does not appear in the call at all."
                elif self.quotes == "agent_id" and number:
                    quote = "AGT-017 said something here."
                elif self.quotes == "bad_number" and number:
                    number = 99
                rows.append({"call_id": call_id, "number": number, "quote": quote})
            if self.quotes == "forget_half":
                rows = rows[::2]
            return schema(assignments=rows)

        return RunnableLambda(respond)


def hash_order(call_ids) -> list[str]:
    return sorted(call_ids, key=lambda c: hashlib.sha256(c.encode()).hexdigest())
