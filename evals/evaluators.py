"""Evaluators for the agent golden set (evals/datasets/agent_golden.jsonl).

The change-analysis sets (change_golden.jsonl, change_holdout.jsonl) add the evaluators in
CHANGE_DETERMINISTIC. They recompute what the answer should hold for the windows and filters
it reports, from the generator's output and ground truth (`evals/change_corpus.py`), and match
drivers and patterns to planted calls by call id, never by wording. A change row lists the
evaluators that apply to it in `evaluators`; the others score None.

Each evaluator has LangSmith's signature, `(inputs, outputs, reference_outputs) -> dict`,
so the same function scores an offline run (`make evals`) and a LangSmith experiment
(`make evals-live`). `outputs` is what `evals.run.run_question` returns: the graph's JSON
output fields plus `interrupts`, the clarify payloads it raised. `reference_outputs` is
the golden record.

A result's `score` is True/False, or None where the check does not apply to the item
(no SQL is expected from a call lookup, for example).

The deterministic evaluators need no model. `make_judge` builds the one that does, an
LLM-as-judge over the answer; it runs only in `make evals-live`.
"""

import json
import re
from collections.abc import Callable
from datetime import date
from functools import cache
from pathlib import Path

from pydantic import BaseModel, Field

from evals.datasets.build_golden import load_calls

CALL_ID_RE = re.compile(r"CALL-\d{5}")
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
JUDGE_PROMPT_VERSION = "v1"


@cache
def corpus_ids() -> frozenset[str]:
    """Every call id the seed-42 generator wrote: the definition of a real citation."""
    return frozenset(c["call_id"] for c in load_calls())


def _result(key: str, score: bool | None, comment: str = "") -> dict:
    return {"key": key, "score": score, "comment": comment}


def route(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    expected, got = reference_outputs["route"], outputs.get("route")
    return _result("route", got == expected, f"expected {expected}, got {got}")


def interrupt(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    """Fired when it should, with the right term and options, and not otherwise."""
    expected = [(i["term"], sorted(i["options"])) for i in reference_outputs.get("interrupts", [])]
    got = [(i["term"], sorted(i["options"])) for i in outputs.get("interrupts") or []]
    if not expected:
        fired = ", ".join(f'"{t}" ({len(o)} options)' for t, o in got)
        return _result("interrupt", not got, f"fired on {fired}" if got else "no interrupt")
    if not got:
        return _result("interrupt", False, f"expected {len(expected)} interrupt(s), none fired")
    if got != expected:
        return _result("interrupt", False, f"expected {expected}, got {got}")
    return _result("interrupt", True, ", ".join(f'"{t}"' for t, _ in got))


def metric(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    expected = reference_outputs["metric_names"]
    if not expected:
        return _result("metric", None)
    got = outputs.get("metric_names") or []
    return _result("metric", set(got) == set(expected), f"expected {expected}, got {got}")


def sql(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    """The metric answer carries the SQL that produced it, in the field and the answer."""
    if not reference_outputs.get("expect_sql", reference_outputs["route"] == "explain_change"):
        return _result("sql", None)
    text = outputs.get("sql") or ""
    if not re.search(r"\bselect\b", text, re.IGNORECASE):
        return _result("sql", False, "no SQL in the output")
    if text not in (outputs.get("answer") or ""):
        return _result("sql", False, "SQL missing from the answer text")
    return _result("sql", True)


def citations(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    """Present when expected, and real: every call id the answer names is in the corpus."""
    if reference_outputs["route"] == "resolve_metric":
        return _result("citations", None)
    cited = outputs.get("citations") or []
    named = set(CALL_ID_RE.findall(outputs.get("answer") or "")) | set(cited)
    if not reference_outputs.get("expect_citations", True):
        # A lookup of a call that does not exist: nothing may be cited as a source.
        return _result("citations", not cited, f"cited {cited}" if cited else "none cited")
    invented = sorted(named - corpus_ids())
    if not cited:
        return _result("citations", False, "no citation")
    if invented:
        return _result("citations", False, f"not in the corpus: {invented}")
    return _result("citations", True, ", ".join(cited))


def call_id(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    """A call lookup answers about the call the question named."""
    expected = reference_outputs.get("call_id")
    if not expected:
        return _result("call_id", None)
    cited = outputs.get("citations") or []
    if expected in corpus_ids():
        return _result("call_id", expected in cited, f"expected {expected}, cited {cited}")
    # The call does not exist: the answer must report on that id and cite nothing.
    answered = expected in (outputs.get("answer") or "")
    return _result("call_id", answered and not cited,
                   f"{expected} {'named' if answered else 'not named'}, cited {cited}")


DETERMINISTIC: list[Callable[[dict, dict, dict], dict]] = [
    route, interrupt, metric, sql, citations, call_id,
]
DETERMINISTIC_KEYS = [f.__name__ for f in DETERMINISTIC]


# ── change analysis ───────────────────────────────────────────────────────────

AGENT_ID_RE = re.compile(r"\bAGT-\d+\b")
MIN_PLANTED = 5  # a planted driver or pattern is scored once it has this many calls in scope
RECALL = 0.5  # a reported driver must hold this share of a planted driver's calls
PRECISION = 0.6  # ... and this share of its own calls must be that driver's
SHARE_POINTS = 0.10  # a found driver's share of the calls read, within 10 points
DETECTED_DAYS = 4  # a detected change point may be this far off; less than the 7-day snap


def _applies(key: str, reference_outputs: dict) -> bool:
    return key in (reference_outputs.get("evaluators") or [])


def _corpus():
    from evals.change_corpus import default_corpus

    return default_corpus()


def _windows(change: dict | None) -> dict | None:
    return (change or {}).get("windows")


def _span(window: dict) -> tuple[date, date]:
    return date.fromisoformat(window["start"]), date.fromisoformat(window["end"])


def change_point(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    """Exact when the change came from the log or the question; a detected one within 4 days."""
    if not _applies("change_point", reference_outputs):
        return _result("change_point", None)
    cp = (outputs.get("change") or {}).get("change_point") or {}
    expected, how = reference_outputs["change_point"], reference_outputs["change_point_source"]
    got, source = cp.get("date"), cp.get("source")
    comment = f"expected {expected} ({how}), got {got} ({source})"
    if not got:
        return _result("change_point", False, comment)
    if how == "detected":
        off = abs((date.fromisoformat(got) - date.fromisoformat(expected)).days)
        return _result("change_point", source == "detected" and off <= DETECTED_DAYS, comment)
    return _result("change_point", source == "argument" and got == expected, comment)


def before_after(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    """Both window values and their n equal the recomputation at two decimals, on the
    contract's filters (when it fixes them)."""
    if not _applies("before_after", reference_outputs):
        return _result("before_after", None)
    change = outputs.get("change") or {}
    windows = _windows(change)
    if not windows:
        return _result("before_after", False, "no windows in the answer")
    expected_filters = reference_outputs.get("filters")
    if expected_filters is not None and change.get("filters") != expected_filters:
        return _result("before_after", False,
                       f"filters {change.get('filters')}, expected {expected_filters}")
    corpus, wrong = _corpus(), []
    for name in ("before", "after"):
        w = windows[name]
        value, n = corpus.window(change["metric"], change.get("filters"), *_span(w))
        if _round(w.get("value")) != _round(value) or w.get("n") != n:
            wrong.append(f"{name} [{w['start']}, {w['end']}) {w.get('value')} (n {w.get('n')}), "
                         f"expected {_round(value)} (n {n})")
    return _result("before_after", not wrong, "; ".join(wrong) or "both windows match")


def _round(value) -> float | None:
    return None if value is None else round(float(value), 2)


def weekly(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    """The after window's weekly values: whole ISO weeks, equal to the recomputation."""
    if not _applies("weekly", reference_outputs):
        return _result("weekly", None)
    change = outputs.get("change") or {}
    windows = _windows(change)
    if not windows or not windows["after"].get("weekly"):
        return _result("weekly", False, "no weekly values in the answer")
    series = _corpus().weekly(change["metric"], change.get("filters"))
    wrong = []
    for row in windows["after"]["weekly"]:
        value, n = series.get(date.fromisoformat(row["week"]), (None, 0))
        if _round(row["value"]) != _round(value) or row["n"] != n:
            wrong.append(f"week {row['week']}: {row['value']} (n {row['n']}), "
                         f"expected {_round(value)} (n {n})")
    weeks = len(windows["after"]["weekly"])
    return _result("weekly", not wrong, "; ".join(wrong) or f"{weeks} weeks match")


def _match(planted: dict[str, list[str]], reported: list[list[str]]) -> tuple[dict, list[str]]:
    """{planted id: (reported index, recall, precision)} for each planted group found, and a
    note per group missed. Groups are named by size, never by id."""
    found, missed = {}, []
    for pid, calls in sorted(planted.items(), key=lambda kv: -len(kv[1])):
        target, best = set(calls), (None, 0.0, 0.0)
        for i, ids in enumerate(reported):
            hit = len(target & set(ids))
            recall, precision = hit / len(target), hit / len(ids) if ids else 0.0
            if recall >= RECALL and precision >= PRECISION:
                found[pid] = (i, recall, precision)
                break
            if recall > best[1]:
                best = (i, recall, precision)
        else:
            missed.append(f"a planted group of {len(target)} calls (best match holds "
                          f"{best[1]:.0%} of them at {best[2]:.0%} precision)")
    return found, missed


def _scope(change: dict) -> tuple[list[str], dict[str, list[str]]] | None:
    """The after-window calls the tool read, and the planted drivers among them with at
    least MIN_PLANTED calls."""
    windows = _windows(change)
    if not windows:
        return None
    corpus = _corpus()
    read = corpus.read(change.get("filters"), *_span(windows["after"]))
    planted = {k: v for k, v in corpus.planted(read).items() if len(v) >= MIN_PLANTED}
    return read, planted


def driver_recall(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    """Every planted driver with 5+ calls in scope is found: one reported driver holds at
    least half of its calls, at 60% precision or better."""
    if not _applies("driver_recall", reference_outputs):
        return _result("driver_recall", None)
    change = outputs.get("change") or {}
    scope = _scope(change)
    if not scope or not scope[1]:
        return _result("driver_recall", False, "no planted driver in the reported windows")
    _, planted = scope
    reported = [d["after"]["call_ids"] for d in change.get("drivers", [])]
    found, missed = _match(planted, reported)
    comment = f"{len(found)} of {len(planted)} planted drivers found"
    return _result("driver_recall", not missed, "; missed ".join([comment, *missed]))


def driver_share(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    """Each found driver's share of the calls read is within 10 points of the planted share."""
    if not _applies("driver_share", reference_outputs):
        return _result("driver_share", None)
    change = outputs.get("change") or {}
    scope = _scope(change)
    if not scope or not scope[1]:
        return _result("driver_share", False, "no planted driver in the reported windows")
    read, planted = scope
    drivers = change.get("drivers", [])
    found, _ = _match(planted, [d["after"]["call_ids"] for d in drivers])
    if not found:
        return _result("driver_share", False, "no planted driver was found")
    off = []
    for pid, (i, _, _) in found.items():
        got, want = drivers[i]["after"]["n"] / len(read), len(planted[pid]) / len(read)
        if abs(got - want) > SHARE_POINTS:
            off.append(f"reported {got:.0%} for a planted share of {want:.0%}")
    return _result("driver_share", not off,
                   "; ".join(off) or f"{len(found)} shares within {SHARE_POINTS:.0%}")


def pattern_recall(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    """The driver_recall rule applied to coaching patterns and the calls planted with them."""
    if not _applies("pattern_recall", reference_outputs):
        return _result("pattern_recall", None)
    change = outputs.get("change") or {}
    windows = _windows(change)
    if not windows:
        return _result("pattern_recall", False, "no analysis to follow up")
    corpus = _corpus()
    scope = corpus.call_ids(change.get("filters"), *_span(windows["after"]))
    planted = {k: v for k, v in corpus.planted(scope, "pattern").items() if len(v) >= MIN_PLANTED}
    if not planted:
        return _result("pattern_recall", False, "no planted pattern in the reported windows")
    found, missed = _match(planted, [p["call_ids"] for p in change.get("patterns", [])])
    comment = f"{len(found)} of {len(planted)} planted patterns found"
    return _result("pattern_recall", not missed, "; missed ".join([comment, *missed]))


def no_agent_ids(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    """A change answer names patterns across calls, never an agent."""
    if not _applies("no_agent_ids", reference_outputs):
        return _result("no_agent_ids", None)
    shown = (outputs.get("answer") or "") + json.dumps(outputs.get("change") or {})
    ids = sorted(set(AGENT_ID_RE.findall(shown)))
    return _result("no_agent_ids", not ids, f"agent ids: {ids}" if ids else "none")


CHANGE_DETERMINISTIC: list[Callable[[dict, dict, dict], dict]] = [
    route, interrupt, metric, sql, citations, no_agent_ids,
    change_point, before_after, weekly, driver_recall, driver_share, pattern_recall,
]
CHANGE_KEYS = [f.__name__ for f in CHANGE_DETERMINISTIC]


# ── LLM-as-judge (make evals-live only) ─────────────────────────────────────────

class Verdict(BaseModel):
    reasoning: str = Field(description="two or three sentences on the deciding points")
    score: int = Field(ge=1, le=5, description="1 = wrong or invented, 5 = complete and faithful")


def judge_prompt() -> str:
    return (PROMPTS_DIR / f"judge_answer_{JUDGE_PROMPT_VERSION}.md").read_text(encoding="utf-8")


def _clarification(inputs: dict, outputs: dict) -> str:
    asked = outputs.get("interrupts") or []
    if not asked:
        return "none"
    chosen = ", ".join(inputs.get("clarify_with") or [])
    terms = ", ".join(f'"{i["term"]}"' for i in asked)
    return f"The agent asked what {terms} meant; the user answered {chosen}."


def make_judge(llm) -> Callable[[dict, dict, dict], dict]:
    """The answer-quality judge. `llm` is a LangChain chat model that must differ from
    every model the agent uses (`evals.run` enforces that before a live run)."""
    structured = llm.with_structured_output(Verdict)
    template = judge_prompt()

    def judge(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
        prompt = template.format(
            question=inputs["question"],
            clarification=_clarification(inputs, outputs),
            reference=reference_outputs["reference"],
            answer=outputs.get("answer") or "(no answer)",
        )
        verdict = structured.invoke(prompt)
        return {
            "key": "judge",
            "score": (verdict.score - 1) / 4,
            "comment": f"{verdict.score}/5 (prompt {JUDGE_PROMPT_VERSION}): {verdict.reasoning}",
        }

    return judge
