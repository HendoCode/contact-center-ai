"""classify: route the question. A call id short-circuits the LLM."""

import re
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from agent.state import AgentState, Route

CALL_ID_RE = re.compile(r"CALL-\d{5}")
_QUESTION_CALL_ID_RE = re.compile(CALL_ID_RE.pattern, re.IGNORECASE)  # people type "call-00025"
PROMPT = (Path(__file__).parent.parent / "prompts" / "classify.md").read_text(encoding="utf-8")


class RouteDecision(BaseModel):
    route: Route


def make_classify(get_llm: Callable[[], object]):
    async def classify(state: AgentState) -> dict:
        question = state["question"]
        # Reset per-run keys so a reused thread_id never inherits the last question's state.
        # `prior_change` is the one exception: the latest analysis on the thread, kept so a
        # follow-up can reuse its evidence. `change` is only what this run produced.
        prior_change = state.get("change") or state.get("prior_change")
        fresh = {
            "call_id": None, "term": None, "candidates": [], "metric_names": [],
            "resolved_terms": [], "clarify_attempts": 0, "tool_output": "", "sql": None,
            "citations": [], "grounded": False, "answer": "", "change_frame": None,
            "change_filters": {}, "change_point": None, "change_search": None, "refusal": None,
            "change": None, "prior_change": prior_change,
        }
        call_id = _QUESTION_CALL_ID_RE.search(question)
        if call_id:
            return {**fresh, "route": "summarize_call", "call_id": call_id.group(0).upper()}

        llm = get_llm().with_structured_output(RouteDecision)
        prior = "yes" if prior_change else "no"
        decision = await llm.ainvoke(PROMPT.format(question=question, has_prior_change=prior))
        # summarize_call needs an id; without one the question is an open one.
        route = "retrieve" if decision.route == "summarize_call" else decision.route
        return {**fresh, "route": route}

    return classify
