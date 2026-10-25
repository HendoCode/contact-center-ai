"""
Change analysis: why did a governed metric move around a change?

One MCP tool, `explain_change`, joins the two things this repo has separately: a governed
metric over two time windows (MetricFlow, with the SQL) and cited evidence from the calls in
those windows (the Retriever). Every client gets the same evidence, whether it is the agent's
`explain_change` route or Claude Desktop calling the tool directly.

    explain_change(metric, filters, change_point | search_range, window_days, question)
      1. change point  the given date; or the one logged change inside `search_range`;
                       or the largest weekly shift (detection), snapped to a logged change
                       within 7 days. A shift that is not clear gives "no change point".
      2. windows       [cp - L, cp) and [cp, cp + L), L = min(window_days, days of data on
                       each side). The metric, and the calls, are read for both.
      3. drivers       One model call proposes up to six reasons from the calls after the
                       change; batches of 20 calls are assigned a reason and a quote. Code
                       counts the assignments and keeps only quotes found verbatim in the call.
      4. coaching      `mode="coaching"` reads the agent-side patterns across the calls of
                       the given groups. It never names or scores an individual agent.

Numbers come only from MetricFlow (`query_metric_raw`) and `Retriever.count`; the model
supplies labels and quotes, never a figure. Windows are half-open ISO dates and weeks are ISO
weeks starting Monday (docs/design/change-analysis.md).

Wire format: two text blocks. The first is `render_change(result)`, the cited answer; the
second is the line "CHANGE RESULT (JSON, v1):" followed by the result as JSON, which
`split_change` reads back.
"""

import hashlib
import json
import math
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from ccai_mcp.masking import mask_accounts
from ccai_mcp.metrics import query_metric_raw
from rag.pipeline import get_llm
from retrieval import get_retriever

SYNTHETIC_DIR = Path(__file__).resolve().parent.parent / "data" / "synthetic"

RESULT_MARKER = "CHANGE RESULT (JSON, v1):"
SQL_MARKER = "Generated SQL:"  # the agent's answer node splits the SQL off at this line
RESULT_VERSION = 1

# metric -> how two windows are compared. `weight` is the metric whose value is the number of
# records behind each value (a count is its own weight).
METRICS = {
    "call_volume": {"label": "Call Volume", "kind": "count", "weight": "call_volume"},
    "csat_average_score": {
        "label": "CSAT Average Score", "kind": "mean", "weight": "responses",
    },
    "first_contact_resolution_rate": {
        "label": "First Contact Resolution Rate", "kind": "ratio", "weight": "call_volume",
    },
    "average_handle_time": {
        "label": "Average Handle Time (AHT)", "kind": "average", "weight": "call_volume",
    },
}
SUPPORTED_METRICS = tuple(METRICS)
NEEDS_EXPLICIT_CHANGE_POINT = ("average_handle_time",)
CSAT_SIGMA = 1.0  # a 1-5 score; the shift score treats its spread as 1 point

MODES = ("drivers", "coaching")
FILTER_KEYS = ("category", "home_branch")
WHERE_DIMENSIONS = {"category": "interaction__category_code", "home_branch": "member__home_branch"}

MIN_WINDOW_DAYS = 7
MIN_WINDOW_DAYS_ARG = 14
MAX_WINDOW_DAYS_ARG = 91
MIN_RECORDS = 8  # a side of a candidate change point needs this many records
MIN_SCORE = 2.0  # below this standardized shift there is no clear change point
SNAP_DAYS = 7
DETECT_BEFORE_WEEKS = 13
DETECT_AFTER_WEEKS = 4
CATEGORY_LIMIT = 5

SAMPLE_CAP = 200  # calls read per window; above it, the lowest SHA-256(call_id) values
PROPOSE_CALLS = 30  # calls the driver proposal reads
BATCH = 20
MAX_DRIVERS = 6
MAX_PATTERNS = 3
MAX_CITATIONS = 3
MIN_QUOTE = 12  # characters; shorter quotes say too little to cite
MAX_QUOTE = 300
MAX_GROUPS = 6
MAX_PARALLEL = 4  # queries or model calls in flight at once

_ID_RE = re.compile(r"\b(?:AGT|MBR)-\d+\b")  # staff and member ids are never quoted or labeled
_SPACE_RE = re.compile(r"\s+")


# ── change log ───────────────────────────────────────────────────────────────

def load_change_log(directory: Path | None = None) -> list[dict]:
    """Logged changes as [{"name", "date"}] in date order; [] when the file is not there."""
    try:
        data = json.loads(((directory or SYNTHETIC_DIR) / "change_log.json").read_text("utf-8"))
    except (OSError, ValueError):
        return []
    entries = [
        {"name": str(e["name"]), "date": str(e["date"])}
        for e in data if isinstance(e, dict) and e.get("name") and e.get("date")
    ] if isinstance(data, list) else []
    return sorted(entries, key=lambda e: e["date"])


# ── MetricFlow access ────────────────────────────────────────────────────────

def _num(text) -> float | None:
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _where(filters: dict) -> list[dict] | None:
    clauses = [
        {"dimension": WHERE_DIMENSIONS[key], "op": "in", "value": list(filters[key])}
        for key in FILTER_KEYS if filters.get(key)
    ]
    return clauses or None


def _clean_filters(filters: dict | None) -> dict:
    if not filters:
        return {}
    if not isinstance(filters, dict) or set(filters) - set(FILTER_KEYS):
        raise ValueError(f"filters must be an object with only {list(FILTER_KEYS)} keys")
    cleaned = {}
    for key in FILTER_KEYS:
        values = filters.get(key)
        if values is None:
            continue
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, (list, tuple)) or not all(isinstance(v, str) for v in values):
            raise ValueError(f"filters.{key} must be a list of strings")
        values = list(dict.fromkeys(v.strip() for v in values if v.strip()))
        if values:
            cleaned[key] = values
    return cleaned


def _rows(result: dict) -> list[dict]:
    return [dict(zip(result["columns"], row)) for row in result["rows"]]


def _day(text: str) -> date:
    return date.fromisoformat(str(text)[:10])


def _metric_names(metric: str) -> list[str]:
    spec = METRICS[metric]
    return [metric] if spec["weight"] == metric else [metric, spec["weight"]]


def _window_value(metric: str, filters: dict, start: date, end: date) -> dict:
    """The metric over [start, end): its value, the records behind it, and the SQL."""
    result = query_metric_raw(
        _metric_names(metric),
        time_window={"start": start.isoformat(), "end": end.isoformat()},
        where=_where(filters),
    )
    row = (_rows(result) or [{}])[0]
    weight = _num(row.get(METRICS[metric]["weight"])) or 0.0
    value = weight if METRICS[metric]["kind"] == "count" else _num(row.get(metric))
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "value": value, "n": int(weight), "sql": result["sql"],
    }


def _weekly(metric: str, filters: dict) -> dict:
    """The metric by ISO week over all the data: {week_start: (value, n)} and the SQL."""
    result = query_metric_raw(
        _metric_names(metric), group_by=["metric_time__week"], where=_where(filters)
    )
    weeks = {}
    for row in _rows(result):
        weight = _num(row.get(METRICS[metric]["weight"])) or 0.0
        value = weight if METRICS[metric]["kind"] == "count" else _num(row.get(metric))
        weeks[_day(row["metric_time__week"])] = (value, int(weight))
    return {"weeks": weeks, "sql": result["sql"]}


def _data_range() -> tuple[date, date]:
    """First day with a call, and the day after the last one (end exclusive)."""
    result = query_metric_raw(["call_volume"], group_by=["metric_time__day"])
    days = sorted(_day(r["metric_time__day"]) for r in _rows(result)
                  if (_num(r.get("call_volume")) or 0) > 0)
    if not days:
        raise RuntimeError("the semantic layer holds no calls, so there is nothing to compare")
    return days[0], days[-1] + timedelta(days=1)


def _categories_by_window(start: date, end: date) -> dict[str, int]:
    result = query_metric_raw(
        ["call_volume"], group_by=["interaction__category_code"],
        time_window={"start": start.isoformat(), "end": end.isoformat()},
    )
    return {r["interaction__category_code"]: int(_num(r.get("call_volume")) or 0)
            for r in _rows(result)}


# ── change point ─────────────────────────────────────────────────────────────

def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _side(weeks: dict, kind: str, start: date, end: date, data: tuple[date, date]) -> tuple | None:
    """Pooled value, records and weeks for the data inside [start, end), or None if empty."""
    start, end = max(start, data[0]), min(end, data[1])
    if end <= start:
        return None
    total = pooled = 0.0
    for week, (value, n) in weeks.items():
        if week + timedelta(days=7) <= start or week >= end or value is None:
            continue
        total += n
        pooled += value * n
    if total <= 0:
        return None
    days = (end - start).days
    return pooled / total, total, days / 7


def _rate(kind: str, side: tuple) -> float:
    """What a side is compared on: calls a week for counts, the pooled value otherwise."""
    value, n, weeks = side
    return n / weeks if kind == "count" else value


def _direction(after: float, before: float, *, up: str = "rise", down: str = "fall") -> str | None:
    return up if after > before else down if after < before else None


def _shift_score(kind: str, before: tuple, after: tuple) -> float:
    """Standardized shift between two sides: Poisson rates for counts, a fixed spread for
    scores, binomial for rates, and a pooled spread for other ratios."""
    (vb, nb, wb), (va, na, wa) = before, after
    if kind == "count":
        rb, ra = nb / wb, na / wa
        spread = math.sqrt(ra / wa + rb / wb)
        return abs(ra - rb) / spread if spread else 0.0
    if kind == "mean":
        return abs(va - vb) / (CSAT_SIGMA * math.sqrt(1 / na + 1 / nb))
    pooled = (va * na + vb * nb) / (na + nb)
    spread = math.sqrt(pooled * (1 - pooled) * (1 / na + 1 / nb))
    return abs(va - vb) / spread if spread else 0.0


def _detect(metric: str, weeks: dict, data: tuple[date, date], search: tuple[date, date] | None):
    """The Monday with the largest standardized shift, in either direction:
    (date, score, direction) with direction "rise" or "fall", or (None, best score, None)."""
    kind = METRICS[metric]["kind"]
    first, last = search or (data[0], data[1])
    best_day, best_score, best_direction = None, 0.0, None
    day = _monday(max(first, data[0]))
    if day < first:
        day += timedelta(days=7)
    while day < min(last, data[1]):
        before = _side(weeks, kind, day - timedelta(weeks=DETECT_BEFORE_WEEKS), day, data)
        after = _side(weeks, kind, day, day + timedelta(weeks=DETECT_AFTER_WEEKS), data)
        if before and after and before[1] >= MIN_RECORDS and after[1] >= MIN_RECORDS:
            score = _shift_score(kind, before, after)
            if score > best_score + 1e-9:
                best_day, best_score = day, score
                best_direction = _direction(_rate(kind, after), _rate(kind, before))
        day += timedelta(days=7)
    return best_day, best_score, best_direction


def _logged_within(log: list[dict], day: date, days: int) -> dict | None:
    near = [(abs((_day(e["date"]) - day).days), e["date"], e) for e in log
            if abs((_day(e["date"]) - day).days) <= days]
    return min(near, key=lambda t: t[:2])[2] if near else None


def _logged_inside(log: list[dict], start: date, end: date) -> list[dict]:
    return [e for e in log if start <= _day(e["date"]) < end]


def _parse_date(value, name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"{name} must be an ISO date (YYYY-MM-DD), got {value!r}") from None


def _search_range(search_range: dict | None) -> tuple[date, date] | None:
    if not search_range:
        return None
    if not isinstance(search_range, dict) or not {"start", "end"} <= set(search_range):
        raise ValueError('search_range must be {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}')
    start, end = _parse_date(search_range["start"], "search_range.start"), \
        _parse_date(search_range["end"], "search_range.end")
    if end <= start:
        raise ValueError("search_range end must be after its start")
    return start, end


# ── calls: counting, sampling, model steps ───────────────────────────────────

def _retrieval_where(filters: dict, start: date, end: date) -> dict:
    where: dict = {"date": {"gte": start.isoformat(), "lt": end.isoformat()}}
    for key in FILTER_KEYS:
        if filters.get(key):
            where[key] = {"in": list(filters[key])}
    return where


def _hash_order(call_id: str) -> str:
    return hashlib.sha256(call_id.encode()).hexdigest()


def _read_window(filters: dict, start: date, end: date, query: str) -> dict:
    """Population by `count(where)`, and the calls read: all of them, or the 200 with the
    lowest SHA-256(call_id) above that. Calls are ordered by that hash, so the same window
    always gives the same sample."""
    retriever = get_retriever()
    where = _retrieval_where(filters, start, end)
    population = retriever.count(where)
    hits = retriever.search(query, k=population, where=where) if population else []
    calls = {hit.call_id: hit.text for hit in hits}
    ordered = sorted(calls, key=_hash_order)[:SAMPLE_CAP]
    return {
        "population": population,
        "fetched": len(calls),  # fewer than the population means the store withheld some
        "calls": [(call_id, calls[call_id]) for call_id in ordered],
    }


def _norm(text: str) -> str:
    return _SPACE_RE.sub(" ", text).strip()


def _verbatim(quote, text: str) -> str | None:
    """The quote, if it is a long enough piece of the call's own text (whitespace aside)."""
    if not isinstance(quote, str):
        return None
    quote = _norm(quote).strip("\"'“” ")
    if not MIN_QUOTE <= len(quote) <= MAX_QUOTE or _ID_RE.search(quote):
        return None
    if mask_accounts(quote) != quote:  # a quote never carries a full account number
        return None
    return quote if quote in _norm(text) else None


def _clean_label(text, limit: int) -> str:
    return _norm(_ID_RE.sub("", str(text or "")))[:limit].strip()


class _Reason(BaseModel):
    label: str = Field(description="Three to eight words naming the reason")
    definition: str = Field(description="One sentence saying what these calls have in common")


class _Reasons(BaseModel):
    reasons: list[_Reason]


class _Assignment(BaseModel):
    call_id: str
    number: int | None = Field(
        default=None, description="Number of the best-fitting item, or null if none fits"
    )
    quote: str | None = Field(
        default=None, description="A sentence copied exactly from the call that shows it"
    )


class _Assignments(BaseModel):
    assignments: list[_Assignment]


def _structured(schema, prompt: str, llm=None):
    """One structured model call; a failed parse is tried once more."""
    llm = llm or get_llm()
    last: Exception | None = None
    for _ in range(2):
        try:
            return llm.with_structured_output(schema).invoke(prompt)
        except Exception as exc:  # noqa: BLE001 - a provider or parse error costs the step, not the answer
            last = exc
    raise RuntimeError(f"{type(last).__name__}: {last}")


def _calls_block(calls: list[tuple[str, str]]) -> str:
    return "\n\n".join(f"[{call_id}]\n{_norm(text)}" for call_id, text in calls)


def _propose(calls: list[tuple[str, str]], question: str | None, *, coaching: bool) -> list[dict]:
    asked = f'A supervisor asked: "{question}"\n\n' if question else ""
    if coaching:
        task = (
            f"Find up to {MAX_PATTERNS} patterns in how the AGENT handled these calls that a "
            "supervisor could coach on. Describe what the agent said or did across calls. "
            "Never name, identify or rate an individual agent."
        )
    else:
        task = (
            f"Find up to {MAX_DRIVERS} distinct reasons members called, from what the members "
            "say in these calls. Each reason must be something several calls share. Use "
            "only what the calls themselves say."
        )
    prompt = (
        "You read call transcripts from a credit union contact center.\n"
        f"{asked}These calls came after a change.\n\n{task}\n\nCALLS:\n{_calls_block(calls)}"
    )
    out = _structured(_Reasons, prompt)
    seen, found = set(), []
    for reason in out.reasons:
        label = _clean_label(reason.label, 80)
        if label and label.lower() not in seen:
            seen.add(label.lower())
            found.append({"label": label, "definition": _clean_label(reason.definition, 240)})
    return found[: MAX_PATTERNS if coaching else MAX_DRIVERS]


def _assign(calls: list[tuple[str, str]], items: list[dict], *, coaching: bool) -> dict:
    """Give each call at most one item, in batches. Returns {call_id: (item index, quote)};
    a call missing from the model's answer, or with an out-of-range number, is unassigned."""
    listing = "\n".join(f"{i}. {item['label']}: {item['definition']}"
                        for i, item in enumerate(items, start=1))
    what = "agent pattern" if coaching else "reason"
    batches = [calls[at: at + BATCH] for at in range(0, len(calls), BATCH)]

    def ask(batch: list[tuple[str, str]]):
        prompt = (
            f"For each call below, pick the one {what} from the list that fits it best, or null "
            "if none fits. Also give one sentence copied exactly, word for word, from the call "
            f"that shows it.\n\n{what.upper()}S:\n{listing}\n\nCALLS:\n{_calls_block(batch)}"
        )
        return _structured(_Assignments, prompt).assignments

    assigned: dict[str, tuple[int, str | None]] = {}
    # Batches are independent, so a hosted model can take a few at once; results are read in
    # batch order, so the counts do not depend on which one answers first.
    for batch, rows in zip(batches, _parallel(*(lambda b=b: ask(b) for b in batches))):
        texts = dict(batch)
        for row in rows:
            call_id, number = _norm(row.call_id).strip("[]"), row.number
            if call_id not in texts or call_id in assigned:
                continue
            if isinstance(number, int) and 1 <= number <= len(items):
                assigned[call_id] = (number - 1, _verbatim(row.quote, texts[call_id]))
    return assigned


def _tally(assigned: dict, sample: list[tuple[str, str]], count: int) -> list[dict]:
    """Per item: the calls assigned to it, in sample order, and their verified quotes."""
    tallies = [{"call_ids": [], "quotes": []} for _ in range(count)]
    for call_id, _ in sample:
        if call_id in assigned:
            index, quote = assigned[call_id]
            tallies[index]["call_ids"].append(call_id)
            if quote:
                tallies[index]["quotes"].append({"call_id": call_id, "quote": quote})
    return tallies


def _scaled(n: int, read: dict) -> float:
    """Calls assigned in a window, scaled up from the sample read to the window's population."""
    return n * read["population"] / len(read["calls"]) if read["calls"] else 0.0


_UNSUPPORTED = (
    "Not enough evidence: the shift was found by detection alone, and no reason for it showed up "
    "in the calls after it. It may be ordinary week-to-week variation, so no cause is given."
)


def _unsupported(cp: dict, drivers: list, read_after: dict) -> bool:
    """A change found only by searching the weekly values, with calls read and no driver among them."""
    return cp["source"] == "detected" and not drivers and bool(read_after["calls"])


def _find_drivers(after: dict, before: dict, question: str | None) -> tuple[list, dict, list]:
    """Drivers from the after-window calls, counted in both windows. Returns
    (drivers, unassigned, limits)."""
    if not after["calls"]:
        return [], {"before": len(before["calls"]), "after": 0}, [
            "No calls were found in the after window, so there are no drivers to report."
        ]
    proposed = _propose(after["calls"][:PROPOSE_CALLS], question, coaching=False)
    if not proposed:
        return [], {"before": len(before["calls"]), "after": len(after["calls"])}, [
            "The calls after the change did not group into reasons, so no drivers are reported."
        ]
    after_tally = _tally(_assign(after["calls"], proposed, coaching=False), after["calls"],
                         len(proposed))
    before_tally = _tally(_assign(before["calls"], proposed, coaching=False), before["calls"],
                          len(proposed)) if before["calls"] else [
        {"call_ids": [], "quotes": []} for _ in proposed]
    drivers = []
    for item, a, b in zip(proposed, after_tally, before_tally):
        if not a["call_ids"]:
            continue
        drivers.append({
            "label": item["label"], "definition": item["definition"],
            "direction": _direction(_scaled(len(a["call_ids"]), after),
                                    _scaled(len(b["call_ids"]), before), up="up", down="down")
                         or "flat",
            "before": {"n": len(b["call_ids"]), "call_ids": b["call_ids"]},
            "after": {"n": len(a["call_ids"]), "call_ids": a["call_ids"]},
            "citations": a["quotes"][:MAX_CITATIONS],
        })
    drivers.sort(key=lambda d: -d["after"]["n"])
    unassigned = {
        "before": len(before["calls"]) - sum(d["before"]["n"] for d in drivers),
        "after": len(after["calls"]) - sum(d["after"]["n"] for d in drivers),
    }
    return drivers, unassigned, []


def _find_patterns(groups: list[dict], question: str | None) -> tuple[list, list]:
    """Agent-side patterns inside each group of calls. Returns (patterns, limits)."""
    retriever = get_retriever()
    patterns, limits = [], []
    for group in groups[:MAX_GROUPS]:
        ids = list(dict.fromkeys(group["call_ids"]))
        hits = retriever.search(
            question or group["label"], k=max(len(ids), 1), where={"call_id": {"in": ids}}
        ) if ids else []
        calls = {hit.call_id: hit.text for hit in hits}
        sample = [(c, calls[c]) for c in sorted(calls, key=_hash_order)[:SAMPLE_CAP]]
        if len(sample) < 2:
            continue
        found = _propose(sample[:PROPOSE_CALLS], question, coaching=True)
        if not found:
            continue
        for item, tally in zip(found, _tally(_assign(sample, found, coaching=True), sample,
                                             len(found))):
            if len(tally["call_ids"]) < 2:  # one call is an instance, not a pattern
                continue
            patterns.append({
                "label": item["label"], "definition": item["definition"],
                "driver": group["label"], "n": len(tally["call_ids"]), "of": len(sample),
                "call_ids": tally["call_ids"],
                "citations": tally["quotes"][:MAX_CITATIONS],
            })
        if len(sample) < len(ids):
            limits.append(
                f'For "{group["label"]}", {len(sample)} of {len(ids)} calls could be read.'
            )
    patterns.sort(key=lambda p: -p["n"])
    return patterns, limits


# ── the tool ─────────────────────────────────────────────────────────────────

def _shorten_note(window_days: int, length: int, before_days: int, after_days: int) -> str:
    return (
        f"The windows are {length} days, not the {window_days} asked for: the data holds "
        f"{before_days} days before the change point and {after_days} days from it."
    )


def _parallel(*jobs: Callable):
    with ThreadPoolExecutor(max_workers=max(1, min(len(jobs), MAX_PARALLEL))) as pool:
        futures = [pool.submit(job) for job in jobs]
        return [f.result() for f in futures]


def _pick_categories(before: dict, after: dict) -> list[str]:
    """Categories whose call volume rose across the change point by the count score."""
    scored = []
    for category in set(before) | set(after):
        b, a = before.get(category, 0), after.get(category, 0)
        # Poisson rates over equal windows: (a - b) / sqrt(a + b) once the weeks cancel.
        score = (a - b) / math.sqrt(a + b) if a > b else 0.0
        if score >= MIN_SCORE:
            scored.append((-score, category))
    return [category for _, category in sorted(scored)[:CATEGORY_LIMIT]]


def _resolve_change_point(
    metric: str, day: date | None, search: tuple[date, date] | None, weeks, data, log
) -> dict:
    """Where the change is: {"date", "source", "logged_change", "score", "reason"}."""
    if day:
        logged = next((e for e in log if e["date"] == day.isoformat()), None)
        return {"date": day, "source": "argument", "score": None, "reason": None,
                "logged_change": logged["name"] if logged else None}
    if search:
        inside = _logged_inside(log, *search)
        if len(inside) == 1:
            return {"date": _day(inside[0]["date"]), "source": "argument", "score": None,
                    "logged_change": inside[0]["name"],
                    "reason": f"the one logged change between {search[0]} and {search[1]}"}
    if metric in NEEDS_EXPLICIT_CHANGE_POINT:
        raise ValueError(
            f"{metric} needs an explicit change point (a date, or the name of a logged change): "
            "its weekly values are too thin to search for one."
        )
    day, score, direction = _detect(metric, weeks, data, search)
    where = f"between {search[0]} and {search[1]}" if search else "across all the data"
    if day is None or score < MIN_SCORE:
        return {"date": None, "source": "none", "logged_change": None, "score": round(score, 2),
                "direction": None,
                "reason": f"no week {where} stands out from the weeks around it "
                          f"(largest shift score {score:.1f}, below {MIN_SCORE:g})"}
    logged = _logged_within(log, day, SNAP_DAYS)
    if logged:
        return {"date": _day(logged["date"]), "source": "detected", "logged_change": logged["name"],
                "score": round(score, 2), "direction": direction,
                "reason": f"the largest weekly shift {where} began the week of {day}, "
                          f"within {SNAP_DAYS} days of the logged change"}
    return {"date": day, "source": "detected", "logged_change": None, "score": round(score, 2),
            "direction": direction,
            "reason": f"the largest weekly shift {where} began the week of {day}"}


def explain_change(
    metric: str,
    filters: dict | None = None,
    change_point: str | None = None,
    search_range: dict | None = None,
    window_days: int = 42,
    question: str | None = None,
    mode: str = "drivers",
    groups: list[dict] | None = None,
) -> dict:
    """Compare one governed metric before and after a change and find what the calls say.

    Args:
        metric: one of SUPPORTED_METRICS.
        filters: {"category": [...], "home_branch": [...]}. With no filters, the categories
            whose call volume rose across the change are used.
        change_point: ISO date of the change. Without it the tool uses the one logged change
            inside `search_range`, or detects the largest weekly shift.
        search_range: {"start", "end"} (end exclusive) to look for the change in.
        window_days: window length, 14-91; shorter where the data runs out.
        question: the supervisor's words, to focus the reading of the calls.
        mode: "drivers" (default), or "coaching" to find agent-side patterns in `groups`.
        groups: [{"label", "call_ids"}] for coaching mode.

    Returns the result dict described in docs/design/change-analysis.md (A.4).
    """
    if metric not in METRICS:
        raise ValueError(
            f"explain_change cannot compare {metric!r}. It supports: {', '.join(SUPPORTED_METRICS)}."
        )
    if mode not in MODES:
        raise ValueError(f"mode must be one of {list(MODES)}, got {mode!r}")
    if isinstance(window_days, float) and window_days.is_integer():
        window_days = int(window_days)
    if not isinstance(window_days, int) or isinstance(window_days, bool) or not (
        MIN_WINDOW_DAYS_ARG <= window_days <= MAX_WINDOW_DAYS_ARG
    ):
        raise ValueError(
            f"window_days must be a whole number from {MIN_WINDOW_DAYS_ARG} to {MAX_WINDOW_DAYS_ARG}"
        )
    filters = _clean_filters(filters)
    day = _parse_date(change_point, "change_point") if change_point else None
    if mode == "coaching":
        return _coaching(metric, filters, day, question, groups)
    return _drivers(metric, filters, day, _search_range(search_range), window_days, question)


def _coaching(metric, filters, change_day, question, groups) -> dict:
    clean = [
        {"label": _clean_label(g.get("label"), 80) or "calls",
         "call_ids": [c for c in g.get("call_ids", []) if isinstance(c, str)]}
        for g in (groups or []) if isinstance(g, dict)
    ]
    if not any(g["call_ids"] for g in clean):
        raise ValueError("coaching mode needs `groups`: [{label, call_ids}] with call ids to read")
    patterns, limits = _find_patterns(clean, question)
    if not patterns:
        limits.append("No pattern in how the calls were handled was shared by two or more calls.")
    return {
        "version": RESULT_VERSION, "mode": "coaching", "metric": metric, "filters": filters,
        "change_point": {
            "date": change_day.isoformat() if change_day else None,
            "source": "argument" if change_day else "none",
            "logged_change": None, "score": None, "direction": None, "reason": None,
        },
        "windows": None, "population": None, "coverage": None, "drivers": [],
        "patterns": patterns, "unassigned": None, "series": None,
        "limits": [
            "Patterns are across calls. The data holds no per-agent scores here, and none are "
            "reported.",
            "Patterns are labeled by a language model that reads the calls; the counts are "
            "exact for its assignments.",
            *limits,
        ],
    }


def _drivers(metric, filters, change_day, search, window_days, question) -> dict:
    spec = METRICS[metric]
    log = load_change_log()
    data, series = _parallel(_data_range, lambda: _weekly(metric, filters))

    cp = _resolve_change_point(metric, change_day, search, series["weeks"], data, log)
    notes = []
    if not log and cp["source"] != "argument":
        notes.append("No change log was available to this service, so a change found in the "
                     "weekly values could not be matched to a named change.")
    shown_series = {"sql": series["sql"], "weeks": _series_rows(series["weeks"])} \
        if cp["source"] != "argument" else None
    if cp["source"] == "none":
        return _result(metric, filters, cp, None, None, None, shown_series,
                       ["The timing of a change could not be fixed, so no windows or drivers "
                        "are reported. Give a date, or the name of a logged change.", *notes])

    day = cp["date"]
    before_days, after_days = (day - data[0]).days, (data[1] - day).days
    length = min(window_days, before_days, after_days)
    if length < MIN_WINDOW_DAYS:
        raise ValueError(
            f"{day} leaves {before_days} days of data before it and {after_days} after; "
            f"at least {MIN_WINDOW_DAYS} on each side are needed"
        )
    before_w = (day - timedelta(days=length), day)
    after_w = (day, day + timedelta(days=length))
    limits = [*notes]
    if length < window_days:
        limits.append(_shorten_note(window_days, length, before_days, after_days))

    picked: list[str] = []
    if not filters:
        cats_before, cats_after = _parallel(
            lambda: _categories_by_window(*before_w), lambda: _categories_by_window(*after_w)
        )
        picked = _pick_categories(cats_before, cats_after)
        if picked:
            filters = {"category": picked}
            series = _weekly(metric, filters)
            limits.append("No call type was named, so the analysis covers the call types whose "
                          f"volume rose across the change: {', '.join(picked)}.")
        else:
            limits.append("No call type was named and none stood out, so the analysis covers "
                          "all calls.")

    before_v, after_v = _parallel(
        lambda: _window_value(metric, filters, *before_w),
        lambda: _window_value(metric, filters, *after_w),
    )
    for window in (before_v, after_v):
        window["days"] = length
    after_v["weekly"] = [
        {"week": w.isoformat(), "value": v, "n": n}
        for w, (v, n) in sorted(series["weeks"].items())
        if w + timedelta(days=7) > after_w[0] and w < after_w[1]
    ]
    after_v["weekly_sql"] = series["sql"]

    query = question or f"why {spec['label'].lower()} changed"
    read_before = _read_window(filters, *before_w, query)
    read_after = _read_window(filters, *after_w, query)
    drivers, unassigned = [], None
    try:
        drivers, unassigned, notes = _find_drivers(read_after, read_before, question)
        limits.extend(notes)
    except RuntimeError as exc:
        limits.append(f"The calls could not be grouped into drivers ({exc}).")
    if _unsupported(cp, drivers, read_after):
        limits.insert(0, _UNSUPPORTED)
    for name, read in (("before", read_before), ("after", read_after)):
        if read["fetched"] < read["population"]:
            limits.append(
                f"The retriever returned {read['fetched']} of the {read['population']} calls "
                f"in the {name} window, so the {name} counts rest on those."
            )
    if spec["weight"] != "call_volume" and after_v["n"] != read_after["population"]:
        limits.append(
            f"{metric} counts the {after_v['n']} survey responses, which cover only some of the "
            f"{read_after['population']} calls after the change; drivers describe all those calls."
        )
    return _result(metric, filters, cp, before_v, after_v, (read_before, read_after), shown_series,
                   limits, drivers, unassigned)


def _series_rows(weeks: dict) -> list[dict]:
    return [{"week": w.isoformat(), "value": v, "n": n} for w, (v, n) in sorted(weeks.items())]


def _result(metric, filters, cp, before_v, after_v, reads, series, limits,
            drivers=None, unassigned=None) -> dict:
    sampled = reads and (
        len(reads[0]["calls"]) < reads[0]["population"]
        or len(reads[1]["calls"]) < reads[1]["population"]
    )
    notes = list(limits)
    if reads and sampled:
        notes.append(
            f"A window with more than {SAMPLE_CAP} calls was sampled: the {SAMPLE_CAP} with the "
            "lowest SHA-256 of the call id. Shares are of the calls sampled."
        )
    if reads:
        notes.append("Driver labels come from a language model that reads the calls; the counts "
                     "are exact for its assignments, and quotes are checked against the call text.")
        notes.append("Weekly values are whole ISO weeks, so the week holding the change point "
                     "also holds days before it.")
    notes.append("The timing fits the change, but the data cannot show that the change caused "
                 "the shift.")
    return {
        "version": RESULT_VERSION, "mode": "drivers", "metric": metric, "filters": filters,
        "change_point": {
            "date": cp["date"].isoformat() if cp["date"] else None, "source": cp["source"],
            "logged_change": cp["logged_change"], "score": cp["score"],
            "direction": cp.get("direction"), "reason": cp["reason"],
        },
        "windows": {"before": before_v, "after": after_v} if before_v else None,
        "population": {"before": reads[0]["population"], "after": reads[1]["population"]}
        if reads else None,
        "coverage": {"read_before": len(reads[0]["calls"]), "read_after": len(reads[1]["calls"])}
        if reads else None,
        "drivers": drivers or [], "patterns": [], "unassigned": unassigned, "series": series,
        "limits": notes,
    }


# ── answer text ──────────────────────────────────────────────────────────────

def _fmt(metric: str, value) -> str:
    if value is None:
        return "no data"
    kind = METRICS[metric]["kind"]
    if kind == "count":
        return f"{value:,.0f}"
    if kind == "ratio":
        return f"{value * 100:.1f}%"
    if kind == "average":
        return f"{value:,.1f} s"
    return f"{value:.2f}"


def _per_week(window: dict) -> float:
    return window["n"] / (window["days"] / 7)


def _movement(metric: str, before: dict, after: dict) -> str:
    kind = METRICS[metric]["kind"]
    if kind == "count":
        b, a = _per_week(before), _per_week(after)
        change = f"{a - b:+.1f} calls a week" + (f" ({(a - b) / b:+.0%})" if b else "")
        return f"{b:.1f} a week before, {a:.1f} a week after: {change}"
    if before["value"] is None or after["value"] is None:
        return "a change cannot be computed: one window has no data"
    diff = after["value"] - before["value"]
    if kind == "ratio":
        return f"{diff * 100:+.1f} percentage points"
    unit = " points" if kind == "mean" else " seconds"
    return f"{diff:+.2f}{unit}"


def _how_found(cp: dict) -> str:
    source = cp["source"]
    if source == "argument" and cp["logged_change"]:
        named = f'"{cp["logged_change"]}"'
        return f"from the question: {cp['reason']}, {named}" if cp["reason"] else \
            f"from the question: the logged change {named}"
    if source == "argument":
        return "from the question" + (f" ({cp['reason']})" if cp.get("reason") else "")
    if source == "detected":
        way = f"a {cp['direction']}, " if cp.get("direction") else ""
        found = (f"no date or logged change was given, so found by detection ({cp['reason']}; "
                 f"{way}shift score {cp['score']})")
        return found + (f', matching the logged change "{cp["logged_change"]}"'
                        if cp["logged_change"] else "")
    return f"no clear change point: {cp['reason']}"


def _weekly_line(metric: str, rows: list[dict]) -> str:
    return "; ".join(f"{r['week']}: {_fmt(metric, r['value'])} (n={r['n']})" for r in rows)


def render_change(result: dict, *, with_sql: bool = True) -> str:
    """The cited answer, without a model: only labels and verified quotes are model text."""
    metric, cp = result["metric"], result["change_point"]
    filters = ", ".join(f"{k} = {' or '.join(v)}" for k, v in result["filters"].items()) or "all calls"
    lines = []
    if result["mode"] == "coaching":
        lines.append(f"Coaching patterns across calls ({filters})")
        for p in result["patterns"]:
            lines += ["", f'- {p["label"]}: {p["n"]} of {p["of"]} calls in "{p["driver"]}". '
                          f'{p["definition"]}']
            lines += [f'    {c["call_id"]}: "{mask_accounts(c["quote"])}"' for c in p["citations"]]
        if not result["patterns"]:
            lines += ["", "No pattern in how the calls were handled was found."]
    else:
        lines.append(f"Change analysis: {METRICS[metric]['label']} ({filters})")
        lines.append(f"Change point: {cp['date'] or 'none'}, {_how_found(cp)}.")
        if _UNSUPPORTED in result["limits"]:
            lines.append(_UNSUPPORTED)
        series = result.get("series")
        if series and cp["source"] != "argument":
            weeks, label = series["weeks"], "Weekly values searched"
            if cp["date"]:  # a found change point: show the weeks around it, not the whole range
                at = date.fromisoformat(cp["date"])
                weeks = [w for w in weeks if at - timedelta(weeks=4) <= _day(w["week"])
                         < at + timedelta(weeks=5)]
                label = "Weekly values around the change point"
            lines.append(f"{label}: " + _weekly_line(metric, weeks))
        windows = result["windows"]
        if windows:
            before, after = windows["before"], windows["after"]
            unit = "responses" if METRICS[metric]["kind"] == "mean" else "calls"
            lines += [
                "",
                f"Before [{before['start']}, {before['end']}): {_fmt(metric, before['value'])} "
                f"(n={before['n']} {unit})",
                f"After [{after['start']}, {after['end']}): {_fmt(metric, after['value'])} "
                f"(n={after['n']} {unit})",
                f"Change: {_movement(metric, before, after)}",
            ]
            lines.append("Weekly after the change: " + _weekly_line(metric, after["weekly"]))
            pop, cov = result["population"], result["coverage"]
            if result["drivers"] or cov["read_after"]:
                sampled = cov["read_after"] < pop["after"]
                basis = (f'{cov["read_after"]} sampled' if sampled
                         else f'{pop["after"]} calls in the after window')
                lines += ["", f"Drivers in the calls after the change ({basis}):"]
                for d in result["drivers"]:
                    n = d["after"]["n"]
                    share = (f"{n} of {cov['read_after']} calls sampled" if sampled
                             else f"{n} of {pop['after']} calls in the after window")
                    moved = {"up": "more than before", "down": "fewer than before",
                             "flat": "about the same as before"}.get(d.get("direction"))
                    lines.append(f"- {d['label']}: {share}; {d['before']['n']} in the "
                                 f"before window" + (f" ({moved})" if moved else "")
                                 + f". {d['definition']}")
                    lines += [f'    {c["call_id"]}: "{mask_accounts(c["quote"])}"' for c in d["citations"]]
                if not result["drivers"]:
                    lines.append("- none found")
                if result["unassigned"] and result["unassigned"]["after"]:
                    lines.append(f"- not matched to a driver: {result['unassigned']['after']}")
    lines += ["", "What the data could not determine:"] + [f"- {item}" for item in result["limits"]]
    if with_sql:
        sql = _sql_blocks(result)
        if sql:
            lines += ["", SQL_MARKER, sql]
    return "\n".join(lines)


def _sql_blocks(result: dict) -> str:
    blocks = []
    if result.get("windows"):
        for name in ("before", "after"):
            w = result["windows"][name]
            blocks.append(f"-- {name} window [{w['start']}, {w['end']})\n{w['sql'].strip()}")
        if result["windows"]["after"].get("weekly_sql"):
            blocks.append("-- weekly values\n" + result["windows"]["after"]["weekly_sql"].strip())
    elif result.get("series"):
        blocks.append("-- weekly values searched\n" + result["series"]["sql"].strip())
    return "\n\n".join(blocks)


def split_change(tool_text: str) -> tuple[str, dict]:
    """Split the tool's text into (rendered answer, result dict). Raises ValueError if the
    text holds no result (a tool error, for one)."""
    body, marker, payload = tool_text.partition("\n" + RESULT_MARKER)
    if not marker:
        raise ValueError("no change result in the tool output")
    return body.rstrip(), json.loads(payload.strip())


def explain_change_blocks(**arguments) -> list[str]:
    """MCP tool: the two text blocks of the wire format, or one TOOL ERROR block."""
    try:
        result = explain_change(**arguments)
    except (ValueError, RuntimeError) as exc:
        return [f"TOOL ERROR (explain_change): {exc}"]
    return [render_change(result), f"{RESULT_MARKER}\n{json.dumps(result)}"]
