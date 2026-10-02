"""Scripted demo: five questions through the real graph, with readable output.

    python -m agent.demo [--strict]

One question per route (retrieve, summarize_call, resolve_metric by declared metric,
resolve_metric through ask_the_analyst) plus one that interrupts and is resumed. It runs
the graph in-process on the Postgres checkpointer, with the chat model from LLM_PROVIDER
and the tools from the MCP server over stdio. `make demo` runs it inside the Compose
stack once `seed` and `dbt` have finished.

A step passes when its route (and, for the interrupt step, the interrupt and the resume)
came out as scripted. A small local model can route differently from run to run; that is
printed as a mismatch and does not fail the run unless `--strict` is given. An exception
(database down, model missing) always fails it.
"""

import argparse
import asyncio
import sys
import uuid
from dataclasses import dataclass

from langgraph.types import Command

from agent.checkpoint import open_checkpointer
from agent.graph import build_graph
from agent.tools import MCPToolbox

MAX_ANSWER_LINES = 14
MAX_SQL_LINES = 6


@dataclass(frozen=True)
class Step:
    label: str
    question: str
    route: str
    interrupt: bool = False
    resume_with: str | None = None  # metric id to answer the clarify question with


STEPS = [
    Step("retrieve", "What are members saying when they call about fraud disputes?", "retrieve"),
    Step("summarize_call", "Summarize CALL-00042.", "summarize_call"),
    Step("resolve_metric, a declared metric", "What is the average mortgage note rate?",
         "resolve_metric"),
    Step("resolve_metric, ask_the_analyst",
         "How many calls did we handle, and what is the average handle time?", "resolve_metric"),
    Step("interrupt, then resume", "What is our average rate?", "resolve_metric",
         interrupt=True, resume_with="average_mortgage_note_rate"),
]


def _clip(text: str, max_lines: int) -> str:
    lines = text.strip().splitlines()
    shown = "\n".join(lines[:max_lines])
    return shown + (f"\n... ({len(lines) - max_lines} more lines)" if len(lines) > max_lines else "")


def _indent(text: str) -> str:
    return "\n".join(f"    {line}" if line else "" for line in text.splitlines())


def _print_result(result: dict) -> None:
    print(f"  route:    {result['route']}")
    if result.get("metric_names"):
        print(f"  metrics:  {', '.join(result['metric_names'])}")
    print(f"  grounded: {'yes' if result['grounded'] else 'NO'}", end="")
    print(f"   citations: {', '.join(result['citations']) or 'none'}")
    answer = result["answer"]
    if result.get("sql"):
        answer = answer.split("\nSQL:\n")[0]
        print("  sql:")
        print(_indent(_clip(result["sql"], MAX_SQL_LINES)))
    print("  answer:")
    print(_indent(_clip(answer, MAX_ANSWER_LINES)))


async def run_step(graph, step: Step) -> tuple[bool, list[str]]:
    """Run one step; returns (ok, mismatches). Raises on an infrastructure failure."""
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    mismatches: list[str] = []
    result = await graph.ainvoke({"question": step.question}, config)

    interrupts = result.get("__interrupt__")
    if interrupts:
        payload = interrupts[0].value
        print(f"  INTERRUPT: {payload['prompt']}")
        for option in payload["options"]:
            print(f"    - {option['id']}  ({option['label']})")
        if not step.interrupt:
            mismatches.append("interrupted, but this step is scripted not to")
        ids = [option["id"] for option in payload["options"]]
        choice = step.resume_with if step.resume_with in ids else ids[0]
        print(f"  resume:   choices=[{choice!r}] on the same thread")
        result = await graph.ainvoke(Command(resume={"choices": [choice]}), config)
    elif step.interrupt:
        mismatches.append("expected an interrupt on this ambiguous question; none fired")

    _print_result(result)
    if result["route"] != step.route:
        mismatches.append(f"routed to {result['route']}, scripted route is {step.route}")
    return not mismatches, mismatches


async def run_steps(graph, steps: list[Step]) -> tuple[int, list[str]]:
    """Run every step, printing as it goes; returns (steps as scripted, failures)."""
    failures: list[str] = []
    matched = 0
    for number, step in enumerate(steps, start=1):
        print(f"\n== {number}/{len(steps)}  {step.label} " + "=" * 20)
        print(f"  question: {step.question}")
        try:
            ok, mismatches = await run_step(graph, step)
        except Exception as exc:  # noqa: BLE001 - report which step broke, then keep going
            failures.append(f"step {number} ({step.label}): {type(exc).__name__}: {exc}")
            print(f"  ERROR: {type(exc).__name__}: {exc}")
            continue
        matched += ok
        for mismatch in mismatches:
            print(f"  MISMATCH: {mismatch}")
    return matched, failures


async def main(strict: bool) -> int:
    from rag.pipeline import get_llm

    async with open_checkpointer() as saver:
        graph = build_graph(get_llm=get_llm, toolbox=MCPToolbox(), checkpointer=saver)
        matched, failures = await run_steps(graph, STEPS)

    print(f"\n{matched}/{len(STEPS)} steps ran as scripted.")
    for failure in failures:
        print(f"FAILED {failure}", file=sys.stderr)
    return 1 if failures or (strict and matched < len(STEPS)) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the five scripted demo questions.")
    parser.add_argument("--strict", action="store_true",
                        help="exit non-zero when a step routes differently from the script")
    sys.exit(asyncio.run(main(parser.parse_args().strict)))
