"""The `explain_change` subgraph: answer "why did X change after, since or around Y".

    frame -> resolve_measure <-> clarify -> resolve_subject -> resolve_when -> analyze
    frame -> analyze                       (a follow-up on a stored change: coaching mode)

It shares the parent's state keys and is compiled without a checkpointer, as `analyst` is, so
it inherits the parent's checkpointer and its one interrupt (`clarify`, the analyst's node
unchanged) surfaces in the parent run. The resolve nodes only read the question: a phrase that
does not resolve ends the run with a refusal that names it, and no tool is called. `analyze`
makes the one `explain_change` MCP tool call; the tool does the metric queries, the call
reading and the counting, so every MCP client gets the same evidence.
"""

import asyncio
import re
import time
from collections.abc import Callable
from functools import cache
from pathlib import Path

import yaml
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from agent.changes import (
    contains_phrase,
    is_iso_date,
    load_branches,
    load_change_log,
    match_logged_change,
    parse_period,
    parse_point,
)
from agent.nodes.answer import split_sql
from agent.state import AgentState
from agent.subgraphs.analyst import clarify
from agent.terms import match_terms, option_label
from agent.tools import Toolbox
from ccai_mcp.change import split_change

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
FRAME_PROMPT = (PROMPT_DIR / "frame.md").read_text(encoding="utf-8")
DEFAULT_METRIC = "call_volume"
RANGE_TTL = 300.0  # seconds the months the data covers are remembered
ANY_DATE = {"start": "0001-01-01", "end": "9999-12-31"}  # for dates that carry their own year


class FrameDecision(BaseModel):
    measure: str | None = None
    subject: str | None = None
    when: str | None = None
    logged_change: str | None = None
    follow_up: bool = False


@cache
def load_measures(path: Path = DATA_DIR / "change_measures.yml") -> dict[str, tuple[str, ...]]:
    """Supported metric -> the phrases a question uses for it."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)["measures"]
    return {metric: tuple(phrases) for metric, phrases in raw.items()}


@cache
def load_subjects(path: Path = DATA_DIR / "change_subjects.yml") -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """[(phrases, categories)] for the subjects a question can name."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)["subjects"]
    return [(tuple(entry["phrases"]), tuple(entry["categories"])) for entry in raw]


def _refuse(reason: str) -> dict:
    """End the run: say why, call no tool."""
    return {"refusal": reason, "tool_output": reason, "sql": None, "citations": []}


def _supported_list() -> str:
    return ", ".join(option_label(metric) for metric in load_measures())


# ── frame ────────────────────────────────────────────────────────────────────

def make_frame(get_llm: Callable[[], object]):
    async def frame(state: AgentState) -> dict:
        log = await asyncio.to_thread(load_change_log)
        listing = "\n".join(f"- {e['name']} ({e['date']})" for e in log) or "(none)"
        prior = state.get("change")
        llm = get_llm().with_structured_output(FrameDecision)
        decision = await llm.ainvoke(FRAME_PROMPT.format(
            change_log=listing, has_prior_change="yes" if prior else "no",
            question=state["question"],
        ))
        parts = decision.model_dump()
        # A follow-up needs an analysis to build on; without one it is a new question.
        parts["follow_up"] = bool(parts["follow_up"]) and bool(prior)
        return {"change_frame": parts}

    return frame


# ── resolve_measure <-> clarify ──────────────────────────────────────────────

def _measure_matches(phrase: str) -> dict[str, int]:
    """Supported metric -> length of its longest phrase found in `phrase`, by label and
    then by change_measures.yml."""
    found: dict[str, int] = {}
    for metric, phrases in load_measures().items():
        candidates = [option_label(metric), metric.replace("_", " "), *phrases]
        lengths = [len(c) for c in candidates if contains_phrase(phrase, c)]
        if lengths:
            found[metric] = max(lengths)
    return found


async def resolve_measure(state: AgentState) -> dict:
    """One supported metric, or a clarify question, or a refusal.

    After `clarify` the chosen metric is in `metric_names`; it must still be exactly one.
    """
    supported = set(load_measures())
    names = [n for n in state.get("metric_names", []) if n in supported]
    if len(names) > 1:
        return _refuse(
            "A change analysis explains one measure at a time, and this one matched "
            f"{', '.join(option_label(n) for n in names)}. Ask about one of them."
        )
    if names:
        return {"metric_names": names, "term": None, "candidates": []}

    phrase = (state.get("change_frame") or {}).get("measure")
    if not phrase:
        return {"metric_names": [DEFAULT_METRIC], "term": None, "candidates": []}

    matches = _measure_matches(phrase)
    if matches:
        best = max(matches.values())
        top = [metric for metric, length in matches.items() if length == best]
        if len(top) == 1:
            return {"metric_names": top, "term": None, "candidates": []}
        return _refuse(
            f'"{phrase}" could mean {", ".join(option_label(m) for m in top)}. '
            "Ask about one of them."
        )

    resolved = list(state.get("resolved_terms", []))
    for match in match_terms(phrase, skip=resolved):
        offered = [m for m in match.candidates if m in supported]
        if len(offered) > 1:
            return {
                "term": match.term, "candidates": offered, "metric_names": [],
                "resolved_terms": resolved, "clarify_attempts": 0,
            }
        if len(offered) == 1:
            return {"metric_names": offered, "term": None, "candidates": [],
                    "resolved_terms": [*resolved, match.term]}
    return _refuse(
        f'I can\'t explain "{phrase}": it is not a measure a change analysis compares '
        f"across a change. I can explain: {_supported_list()}."
    )


# ── resolve_subject ──────────────────────────────────────────────────────────

async def resolve_subject(state: AgentState) -> dict:
    """Categories from change_subjects.yml and branches from branches.json. With no subject
    the filters stay empty and the tool keeps the categories whose volume rose."""
    phrase = (state.get("change_frame") or {}).get("subject")
    if not phrase:
        return {"change_filters": {}}
    categories: list[str] = []
    for phrases, cats in load_subjects():
        if any(contains_phrase(phrase, p) for p in phrases):
            categories += [c for c in cats if c not in categories]
    branches = [b for b in await asyncio.to_thread(load_branches) if contains_phrase(phrase, b)]
    if not categories and not branches:
        known = sorted({p for phrases, _ in load_subjects() for p in phrases[:1]})
        return _refuse(
            f'I can\'t tell which calls "{phrase}" refers to. I can narrow a change analysis to a '
            f"type of call (such as {', '.join(known[:4])}) or to members of a branch."
        )
    filters = {}
    if categories:
        filters["category"] = categories
    if branches:
        filters["home_branch"] = branches
    return {"change_filters": filters}


# ── resolve_when ─────────────────────────────────────────────────────────────

async def _data_range(toolbox: Toolbox) -> dict | None:
    """The months the data covers, from the semantic layer: {"start", "end"} (end exclusive)."""
    output = await toolbox.call(
        "query_metric", {"metrics": ["call_volume"], "group_by": ["metric_time__month"]}
    )
    months = sorted(set(re.findall(r"(\d{4})-(\d{2})-\d{2}", output)))
    if not months:
        return None
    (y0, m0), (y1, m1) = months[0], months[-1]
    end_year, end_month = (int(y1) + 1, 1) if int(m1) == 12 else (int(y1), int(m1) + 1)
    return {"start": f"{y0}-{m0}-01", "end": f"{end_year:04d}-{end_month:02d}-01"}


def make_resolve_when(toolbox: Toolbox):
    months: dict = {}  # the data range, kept for a few minutes: one lookup per conversation

    async def data_range() -> dict | None:
        if months.get("until", 0) < time.monotonic():
            found = await _data_range(toolbox)
            if found is None:  # a failed lookup is not remembered
                return None
            months.update(range=found, until=time.monotonic() + RANGE_TTL)
        return months["range"]

    async def resolve_when(state: AgentState) -> dict:
        frame = state.get("change_frame") or {}
        log = await asyncio.to_thread(load_change_log)
        named, when = frame.get("logged_change"), frame.get("when")

        if named:
            entry = match_logged_change(named, log)
            if entry:
                return {"change_point": entry["date"], "change_search": None}
            if not log:
                return _refuse(
                    f'I can\'t match "{named}" to a date: no change log is available here. '
                    "Give me the date of the change."
                )
            logged = "; ".join(f"{e['name']} ({e['date']})" for e in log)
            return _refuse(f'"{named}" is not in the change log. Logged changes: {logged}.')

        if not when:
            return {"change_point": None, "change_search": None}  # the tool looks for the change

        known = None if is_iso_date(when) else await data_range()
        if known is None and not is_iso_date(when):
            return _refuse(
                f'I can\'t place "{when}": the months the data covers could not be read. '
                "Give the date as YYYY-MM-DD."
            )
        point = parse_point(when, known or ANY_DATE)
        if point:
            return {"change_point": point.isoformat(), "change_search": None}
        period = parse_period(when, known or ANY_DATE)
        if period:
            return {"change_point": None, "change_search": period}
        return _refuse(
            f'I can\'t place "{when}" in time. I understand a date (2026-06-15, June 15), a month '
            '("in June", "since April"), or part of a month ("early", "mid" or "late July") '
            "inside the months the data covers; or name a logged change."
        )

    return resolve_when


# ── analyze ──────────────────────────────────────────────────────────────────

def _cited_ids(result: dict) -> list[str]:
    ids = [c["call_id"] for d in result.get("drivers", []) for c in d.get("citations", [])]
    ids += [c["call_id"] for p in result.get("patterns", []) for c in p.get("citations", [])]
    return list(dict.fromkeys(ids))


def _coaching_arguments(question: str, change: dict) -> dict | None:
    groups = [
        {"label": d["label"], "call_ids": d["after"]["call_ids"]}
        for d in change.get("drivers", []) if d["after"]["call_ids"]
    ]
    if not groups:
        return None
    return {
        "metric": change["metric"], "filters": change.get("filters") or {},
        "change_point": change["change_point"]["date"], "question": question,
        "mode": "coaching", "groups": groups,
    }


def make_analyze(toolbox: Toolbox):
    async def analyze(state: AgentState) -> dict:
        frame, prior = state.get("change_frame") or {}, state.get("change")
        question = state["question"]
        coaching = bool(frame.get("follow_up") and prior)

        if coaching:
            arguments = _coaching_arguments(question, prior)
            if arguments is None:
                return _refuse(
                    "The earlier analysis found no drivers with calls to read, so there is "
                    "nothing to look for coaching patterns in."
                )
        else:
            metric = (state.get("metric_names") or [DEFAULT_METRIC])[0]
            arguments = {"metric": metric, "question": question}
            if state.get("change_filters"):
                arguments["filters"] = state["change_filters"]
            if state.get("change_point"):
                arguments["change_point"] = state["change_point"]
            elif state.get("change_search"):
                arguments["search_range"] = state["change_search"]

        output = await toolbox.call("explain_change", arguments)
        try:
            text, result = split_change(output)
        except ValueError:  # a TOOL ERROR (or anything else without a result): show it, claim nothing
            return {"tool_output": output, "sql": None, "citations": [],
                    "metric_names": [arguments["metric"]]}

        citations = _cited_ids(result)  # what the answer shows, not the stored drivers'
        if coaching:
            # The windows and drivers stay from the analysis being followed up.
            result = {**prior, "mode": "coaching", "patterns": result["patterns"],
                      "limits": result["limits"]}
            sql = _windows_sql(prior)
        else:
            _, sql = split_sql(text)
            if not frame.get("measure"):
                text = "Note: the question names no measure, so this uses call volume.\n\n" + text
        return {
            "change": result, "tool_output": text, "sql": sql, "citations": citations,
            "metric_names": [result["metric"]],
        }

    return analyze


def _windows_sql(change: dict) -> str | None:
    windows = change.get("windows")
    if not windows:
        return None
    blocks = [f"-- {name} window [{w['start']}, {w['end']})\n{w['sql'].strip()}"
              for name, w in windows.items()]
    return "\n\n".join(blocks)


# ── graph ────────────────────────────────────────────────────────────────────

def _after_frame(state: AgentState) -> str:
    frame = state.get("change_frame") or {}
    return "analyze" if frame.get("follow_up") and state.get("change") else "resolve_measure"


def _next_unless_refused(target: str):
    return lambda state: END if state.get("refusal") else target


def build_change(toolbox: Toolbox, get_llm: Callable[[], object]):
    load_measures(), load_subjects()  # read the data files now, not inside a running node
    graph = StateGraph(AgentState)
    graph.add_node("frame", make_frame(get_llm))
    graph.add_node("resolve_measure", resolve_measure)
    graph.add_node("clarify", clarify)
    graph.add_node("resolve_subject", resolve_subject)
    graph.add_node("resolve_when", make_resolve_when(toolbox))
    graph.add_node("analyze", make_analyze(toolbox))

    graph.add_edge(START, "frame")
    graph.add_conditional_edges("frame", _after_frame, ["analyze", "resolve_measure"])
    graph.add_conditional_edges(
        "resolve_measure",
        lambda s: END if s.get("refusal") else "clarify" if s.get("term") else "resolve_subject",
        ["clarify", "resolve_subject", END],
    )
    graph.add_conditional_edges(
        "clarify", lambda s: "clarify" if s.get("term") else "resolve_measure",
        ["clarify", "resolve_measure"],
    )
    graph.add_conditional_edges(
        "resolve_subject", _next_unless_refused("resolve_when"), ["resolve_when", END]
    )
    graph.add_conditional_edges(
        "resolve_when", _next_unless_refused("analyze"), ["analyze", END]
    )
    graph.add_edge("analyze", END)
    return graph.compile(name="explain_change")
