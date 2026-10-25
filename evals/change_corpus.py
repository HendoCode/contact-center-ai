"""The change-analysis answer key, read back from the seed-42 generator's output.

The change evaluators (`evals/evaluators.py`) recompute what a correct `explain_change`
answer holds for whatever windows and filters the answer reports, from the generated files
and the semantic layer's time rules (docs/design/change-analysis.md):

- calls count by `started_at`; call volume, first-contact resolution and handle time use them;
- CSAT counts by `surveyed_at`; a survey takes its call's category and its member's branch;
- windows are half-open ISO dates, weeks are ISO weeks starting Monday;
- a window above SAMPLE_CAP calls is read as the SAMPLE_CAP lowest SHA-256(call_id) values.

Which call carries which planted driver comes only from `ground_truth/events.json`, which
the generator writes and git ignores. The offline doubles in `evals/offline.py` answer
MetricFlow and Retriever calls from the same tables, so offline the windows are a plumbing
check; `make evals-live` compares this recomputation with Postgres.
"""

import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import cache
from pathlib import Path

from ccai_mcp.change import SAMPLE_CAP
from evals.datasets.build_golden import DATA_DIR

GROUND_TRUTH = Path("ground_truth") / "events.json"


@dataclass(frozen=True)
class Call:
    call_id: str
    started: str  # ISO datetime, as stored
    day: date
    category: str
    branch: str | None
    resolved: bool
    seconds: int
    text: str


@dataclass(frozen=True)
class Survey:
    call_id: str
    day: date  # surveyed_at
    score: int
    category: str
    branch: str | None


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _matches(record, filters: dict | None) -> bool:
    filters = filters or {}
    return ((not filters.get("category") or record.category in filters["category"])
            and (not filters.get("home_branch") or record.branch in filters["home_branch"]))


def metric_value(metric: str, rows: list) -> float | None:
    """One metric over the records of its own fact (calls, or surveys for CSAT)."""
    if metric in ("call_volume", "responses"):
        return float(len(rows))
    if not rows:
        return None
    if metric == "csat_average_score":
        return sum(r.score for r in rows) / len(rows)
    if metric == "first_contact_resolution_rate":
        return sum(r.resolved for r in rows) / len(rows)
    if metric == "average_handle_time":
        return sum(r.seconds for r in rows) / len(rows)
    raise KeyError(f"no recomputation for {metric!r}")


def _weight(metric: str) -> str:
    return "responses" if metric in ("csat_average_score", "responses") else "call_volume"


def hash_sample(call_ids) -> list[str]:
    """The calls `explain_change` reads from a window: all, or the SAMPLE_CAP lowest hashes."""
    return sorted(call_ids, key=lambda c: hashlib.sha256(c.encode()).hexdigest())[:SAMPLE_CAP]


@dataclass
class ChangeCorpus:
    calls: dict[str, Call]
    surveys: list[Survey]
    truth: dict
    data_dir: Path = DATA_DIR
    labels: dict[str, dict] = field(init=False)

    def __post_init__(self):
        self.labels = self.truth["calls"]

    # ── the semantic layer's numbers ──────────────────────────────────────────

    def records(self, metric: str, filters: dict | None = None,
                start: date | None = None, end: date | None = None) -> list:
        rows = self.surveys if _weight(metric) == "responses" else self.calls.values()
        return [r for r in rows if _matches(r, filters)
                and (start is None or start <= r.day) and (end is None or r.day < end)]

    def window(self, metric: str, filters: dict | None, start: date, end: date) -> tuple:
        """(value, n) over [start, end), n being the records behind the value."""
        rows = self.records(metric, filters, start, end)
        return metric_value(metric, rows), len(rows)

    def weekly(self, metric: str, filters: dict | None) -> dict[date, tuple]:
        """{ISO week Monday: (value, n)} over all the data."""
        weeks: dict[date, list] = defaultdict(list)
        for r in self.records(metric, filters):
            weeks[monday(r.day)].append(r)
        return {w: (metric_value(metric, rows), len(rows)) for w, rows in sorted(weeks.items())}

    # ── the calls an answer reads, and what was planted in them ───────────────

    def call_ids(self, filters: dict | None, start: date, end: date) -> list[str]:
        return sorted(c.call_id for c in self.records("call_volume", filters, start, end))

    def read(self, filters: dict | None, start: date, end: date) -> list[str]:
        """The window's calls as `explain_change` reads them (sampled above SAMPLE_CAP)."""
        return hash_sample(self.call_ids(filters, start, end))

    def planted(self, call_ids, key: str = "driver") -> dict[str, list[str]]:
        """{driver or pattern id: the call ids among `call_ids` the answer key gives it}."""
        found: dict[str, list[str]] = defaultdict(list)
        for call_id in call_ids:
            label = (self.labels.get(call_id) or {}).get(key)
            if label:
                found[label].append(call_id)
        return dict(found)

    def event(self, name: str) -> dict:
        """An event's answer-key entry, by its change-log name."""
        for entry in self.change_log():
            if entry["name"] == name:
                return next(e for e in self.truth["events"] if e["date"] == entry["date"])
        raise KeyError(f"{name!r} is not in change_log.json")

    def change_log(self) -> list[dict]:
        return json.loads((self.data_dir / "change_log.json").read_text())


def load(data_dir: Path = DATA_DIR) -> ChangeCorpus:
    truth_path = data_dir / GROUND_TRUTH
    if not truth_path.exists():
        sys.exit(f"{truth_path} not found. Run `make seed` first.")
    branches = {f"MBR-{m['member_id']:06d}": m.get("home_branch")
                for m in json.loads((data_dir / "members.json").read_text())}
    resolved = {i["call_id"]: bool(i["first_contact_resolved"])
                for i in json.loads((data_dir / "interactions.json").read_text())}
    calls = {}
    for t in json.loads((data_dir / "transcripts.json").read_text()):
        calls[t["call_id"]] = Call(
            call_id=t["call_id"], started=t["date"], day=date.fromisoformat(t["date"][:10]),
            category=t["category"], branch=branches.get(t["member_id"]),
            resolved=resolved.get(t["call_id"], False), seconds=int(t["duration_seconds"]),
            text=t["full_text"],
        )
    surveys = [
        Survey(call_id=s["call_id"], day=date.fromisoformat(s["survey_date"][:10]),
               score=int(s["score"]), category=calls[s["call_id"]].category,
               branch=calls[s["call_id"]].branch)
        for s in json.loads((data_dir / "csat.json").read_text()) if s["call_id"] in calls
    ]
    return ChangeCorpus(calls=calls, surveys=surveys,
                        truth=json.loads(truth_path.read_text()), data_dir=data_dir)


@cache
def default_corpus() -> ChangeCorpus:
    return load()
