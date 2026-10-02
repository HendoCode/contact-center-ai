"""Evaluators for the agent golden set (evals/datasets/agent_golden.jsonl).

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

import re
from collections.abc import Callable
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
    expected = [(i["term"], sorted(i["options"])) for i in reference_outputs["interrupts"]]
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
    if not reference_outputs["expect_sql"]:
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
    if not reference_outputs["expect_citations"]:
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
    expected = reference_outputs["call_id"]
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
