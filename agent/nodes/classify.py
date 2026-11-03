"""classify: route the question. A call id short-circuits the LLM."""

import json
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import get_args

from langchain_core.exceptions import OutputParserException
from openai import LengthFinishReasonError, OpenAIError
from pydantic import BaseModel, ValidationError

from agent.state import AgentState, Route
from models.providers import with_output_cap

log = logging.getLogger(__name__)

CALL_ID_RE = re.compile(r"CALL-\d{5}")
_QUESTION_CALL_ID_RE = re.compile(CALL_ID_RE.pattern, re.IGNORECASE)  # people type "call-00025"
PROMPT = (Path(__file__).parent.parent / "prompts" / "classify.md").read_text(encoding="utf-8")


class RouteDecision(BaseModel):
    route: Route


ROUTES = get_args(Route)
FALLBACK_ROUTE: Route = "retrieve"  # an open question: the safe route when the model's reply is unusable
_FENCE_RE = re.compile(r"```[\w-]*\s*(.*?)```", re.DOTALL)


def parse_route(text: object) -> Route | None:
    """Read a route from a model reply: strict JSON, JSON in a code fence, or a bare route name.

    A bare name is an exact match (case-insensitive) after stripping whitespace and quotes.
    Anything else is None, never an exception."""
    if not isinstance(text, str):
        return None
    candidates = [text]
    fenced = _FENCE_RE.search(text)
    if fenced:
        candidates.insert(0, fenced.group(1))
    for candidate in candidates:
        candidate = candidate.strip()
        try:
            return RouteDecision.model_validate(json.loads(candidate)).route
        except (ValueError, ValidationError):
            pass
        name = candidate.strip(" \t\r\n\"'`").lower()
        for route in ROUTES:
            if name == route:
                return route
    return None


def _decided_route(reply: object) -> Route | None:
    """The route in whatever the structured call returned: a parsed decision, the raw-and-parsed
    dict, or plain text."""
    if isinstance(reply, RouteDecision):
        return reply.route
    if isinstance(reply, dict):
        parsed = reply.get("parsed")
        if isinstance(parsed, RouteDecision):
            return parsed.route
        reply = reply.get("raw")
    return parse_route(_as_text(reply))


# What a structured call raises when the reply is not the schema's JSON: the SDK validates inside
# the call, before include_raw can hand the text back, so these arrive as exceptions.
_PARSE_ERRORS = (ValidationError, OutputParserException, LengthFinishReasonError)
_PLAIN_SUFFIX = "\n\nReply with only the route name, nothing else."


def _as_text(reply: object) -> object:
    content = getattr(reply, "content", reply)
    if isinstance(content, list):
        content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return content


async def _plain_route(llm: object, prompt: str) -> Route | None:
    """Re-ask without a schema and read the route from the text."""
    try:
        reply = await llm.ainvoke(prompt + _PLAIN_SUFFIX)
    except (LengthFinishReasonError, OpenAIError) as exc:
        raise RuntimeError(
            f"classify: the model gave no usable reply ({type(exc).__name__}); raise "
            "LLM_STRUCTURED_MAX_TOKENS or lower LLM_REASONING_EFFORT") from exc
    return parse_route(_as_text(reply))


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

        # include_raw keeps the model's text when it isn't the JSON the schema asks for.
        # The route is a few tokens, but a reasoning model spends the output cap thinking first,
        # so it gets the structured-output cap too.
        base = with_output_cap(get_llm(), int(os.getenv("LLM_STRUCTURED_MAX_TOKENS", "8192")))
        llm = base.with_structured_output(RouteDecision, include_raw=True)
        prior = "yes" if prior_change else "no"
        prompt = PROMPT.format(question=question, has_prior_change=prior)
        try:
            route = _decided_route(await llm.ainvoke(prompt))
        except _PARSE_ERRORS as exc:
            log.warning("classify: structured reply unusable (%s); re-asking as plain text", type(exc).__name__)
            route = await _plain_route(base, prompt)
        if route is None:
            log.warning("classify: the model's reply was not a known route; answering as an open question")
            route = FALLBACK_ROUTE
        # summarize_call needs an id; without one the question is an open one.
        if route == "summarize_call":
            route = FALLBACK_ROUTE
        return {**fresh, "route": route}

    return classify
