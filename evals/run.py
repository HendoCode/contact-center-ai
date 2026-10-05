"""
Run the agent golden set through the graph and score it.

    python -m evals.run                 # offline: fake LLM, stub tools, no keys (`make evals`)
    python -m evals.run --live          # real models and tools, uploads to LangSmith (`make evals-live`)

Offline runs the deterministic evaluators against the doubles in `evals/offline.py` and
gates on `evals/known_failures.json`: the run fails on a failure that is not listed there,
and on a listed failure that now passes (so the list only ever shrinks by hand). CI runs it.

Live needs the full stack (`make up seed ingest`, `dbt build`), the agent's provider keys,
LANGSMITH_API_KEY, and a judge model that differs from the agent's (EVAL_JUDGE_PROVIDER,
EVAL_JUDGE_MODEL). It syncs the golden set to a LangSmith dataset named after its content
hash, runs a LangSmith experiment with every evaluator plus the LLM-as-judge, and writes
results/evals/<UTC date-time>_<run>_<short sha>.json (§4.3; never overwritten, LATEST names the
newest) with every item's outputs, scores and judge comments. It costs API money.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from evals.datasets import build_golden
from evals.datasets.build_golden import DATA_DIR, GOLDEN_PATH, KINDS, REPO_ROOT
from evals.evaluators import DETERMINISTIC, DETERMINISTIC_KEYS, JUDGE_PROMPT_VERSION

KNOWN_FAILURES_PATH = Path(__file__).resolve().parent / "known_failures.json"
OUTPUT_KEYS = ("answer", "route", "metric_names", "sql", "citations", "grounded")
INPUT_KEYS = ("question", "clarify_with")
MAX_INTERRUPTS = 4  # more than any golden question can raise; stops a runaway re-ask loop
DATASET_PREFIX = "ccai-agent-golden"
DEFAULT_JUDGE_MODEL = "claude-opus-5-5"
RESULTS_DIR = REPO_ROOT / "results" / "evals"
LATEST_PATH = RESULTS_DIR / "LATEST"


# ── golden set ────────────────────────────────────────────────────────────────

def load_golden(path: Path = GOLDEN_PATH) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def split(item: dict) -> tuple[dict, dict]:
    """(inputs, reference_outputs): what the agent sees, and what it is scored against."""
    inputs = {k: item[k] for k in INPUT_KEYS}
    reference = {k: v for k, v in item.items() if k not in (*INPUT_KEYS, "id", "kind")}
    return inputs, reference


def ensure_corpus() -> None:
    """The generator is deterministic and offline, so a missing corpus is just generated."""
    if not (DATA_DIR / "transcripts.json").exists():
        subprocess.run([sys.executable, "data/synthetic/generate_data.py"], cwd=REPO_ROOT,
                       check=True, capture_output=True)


# ── the target ────────────────────────────────────────────────────────────────

async def run_question(graph, question: str, clarify_with: list[str] | None = None) -> dict:
    """One golden question through the graph, answering any clarify interrupt the way
    the golden user would: with the metrics they mean among the options offered, or the
    first option when none of them is offered. Returns the graph's output fields plus
    `interrupts`, one {term, options} per interrupt raised."""
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    result = await graph.ainvoke({"question": question}, config)
    interrupts = []
    while result.get("__interrupt__") and len(interrupts) < MAX_INTERRUPTS:
        payload = result["__interrupt__"][0].value
        options = [o["id"] for o in payload["options"]]
        interrupts.append({"term": payload["term"], "options": options})
        meant = [m for m in clarify_with or [] if m in options]
        result = await graph.ainvoke(Command(resume={"choices": meant or options[:1]}), config)
    return {**{k: result.get(k) for k in OUTPUT_KEYS}, "interrupts": interrupts}


# ── scoring ───────────────────────────────────────────────────────────────────

def summarize(rows: list[dict], keys: list[str]) -> dict[str, dict]:
    """Pass rate per evaluator (applicable items only), overall and per kind."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups["all"].append(row)
        groups[row["kind"]].append(row)
    table = {}
    for group in ("all", *KINDS):
        members = groups.get(group, [])
        if not members:
            continue
        line: dict = {"n": len(members)}
        for key in keys:
            scores = [r["results"][key]["score"] for r in members
                      if key in r["results"] and r["results"][key]["score"] is not None]
            line[key] = round(sum(float(s) for s in scores) / len(scores), 3) if scores else None
        table[group] = line
    return table


def failures(rows: list[dict]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        for key, res in row["results"].items():
            if res["score"] is False:
                out.setdefault(row["id"], {})[key] = res["comment"]
    return out


def gate(found: dict[str, dict[str, str]], known: dict[str, dict[str, str]]) -> list[str]:
    """Problems that fail the offline run: new failures, and known ones that now pass."""
    problems = []
    for item, keys in sorted(found.items()):
        for key, comment in keys.items():
            if key not in known.get(item, {}):
                problems.append(f"NEW FAILURE {item} {key}: {comment}")
    for item, keys in sorted(known.items()):
        for key in keys:
            if key not in found.get(item, {}):
                problems.append(f"NOW PASSES {item} {key}: remove it from {KNOWN_FAILURES_PATH.name}")
    return problems


def format_table(table: dict[str, dict], keys: list[str]) -> str:
    def cell(v):
        return "n/a" if v is None else f"{v:.2f}" if isinstance(v, float) else str(v)

    header = ["group", "n", *keys]
    lines = ["  ".join(f"{h:>11}" for h in header)]
    for group, line in table.items():
        lines.append("  ".join(f"{cell(v):>11}" for v in [group, line["n"], *(line[k] for k in keys)]))
    return "\n".join(lines)


# ── offline ───────────────────────────────────────────────────────────────────

async def run_offline(items: list[dict]) -> list[dict]:
    """Every item through the real graph on the offline doubles; deterministic scores."""
    from agent.graph import build_graph
    from evals.offline import OfflineToolbox, ScriptedClassifier, corpus_call_exists

    calls = build_golden.load_calls()
    llm = ScriptedClassifier({item["question"]: item["route"] for item in items})
    graph = build_graph(
        get_llm=lambda: llm,
        toolbox=OfflineToolbox(items, calls),
        call_exists=corpus_call_exists({c["call_id"] for c in calls}),
        checkpointer=InMemorySaver(),
    )
    rows = []
    for item in items:
        inputs, reference = split(item)
        outputs = await run_question(graph, inputs["question"], inputs["clarify_with"])
        results = {r["key"]: r for r in (ev(inputs, outputs, reference) for ev in DETERMINISTIC)}
        rows.append({"id": item["id"], "kind": item["kind"], "outputs": outputs,
                     "results": results})
    return rows


def offline_main() -> int:
    from agent.tracing import disable_tracing

    disable_tracing()  # offline means offline, whatever .env says
    ensure_corpus()
    items = load_golden()
    if build_golden.dumps(build_golden.build()) != GOLDEN_PATH.read_text():
        print(f"{GOLDEN_PATH} is stale; run python -m evals.datasets.build_golden",
              file=sys.stderr)
        return 1

    rows = asyncio.run(run_offline(items))
    print(f"agent golden set, offline (scripted classifier, stub tools): {len(rows)} items\n")
    print(format_table(summarize(rows, DETERMINISTIC_KEYS), DETERMINISTIC_KEYS))

    found = failures(rows)
    known = json.loads(KNOWN_FAILURES_PATH.read_text())["failures"]
    if found:
        print("\nfailures (all listed in known_failures.json unless flagged below):")
        for item, keys in found.items():
            for key, comment in keys.items():
                print(f"  {item} {key}: {comment}")
    problems = gate(found, known)
    if problems:
        print("\nregression gate FAILED:")
        for p in problems:
            print(f"  {p}")
        return 1
    print(f"\nregression gate passed ({sum(len(v) for v in known.values())} known failures)")
    return 0


# ── live ──────────────────────────────────────────────────────────────────────

def model_name(llm) -> str:
    return str(getattr(llm, "model_name", None) or getattr(llm, "model", None) or llm)


def build_judge_llm():
    """The judge model, from EVAL_JUDGE_PROVIDER ("anthropic" | "openai") and EVAL_JUDGE_MODEL."""
    provider = os.getenv("EVAL_JUDGE_PROVIDER", "anthropic").lower()
    model = os.getenv("EVAL_JUDGE_MODEL", DEFAULT_JUDGE_MODEL)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, api_key=os.getenv("ANTHROPIC_API_KEY"), temperature=0)
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, api_key=os.getenv("OPENAI_API_KEY"), temperature=0,
                          base_url=os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1"))
    raise SystemExit(f"EVAL_JUDGE_PROVIDER={provider!r}: use 'anthropic' or 'openai'")


def check_judge_differs(agent_model: str, judge_model: str) -> None:
    if agent_model.strip().lower() == judge_model.strip().lower():
        raise SystemExit(
            f"the judge model ({judge_model}) is the agent's model; set EVAL_JUDGE_MODEL "
            "to a different model so the agent is not grading itself"
        )


def golden_sha(path: Path = GOLDEN_PATH) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sync_dataset(client, items: list[dict], name: str) -> None:
    """Create the LangSmith dataset if missing and add any example it lacks, keyed by
    golden id, so a rerun (or a run after a partial upload) creates nothing twice."""
    if client.has_dataset(dataset_name=name):
        have = {(ex.metadata or {}).get("golden_id")
                for ex in client.list_examples(dataset_name=name)}
    else:
        client.create_dataset(name, description=f"agent golden set ({GOLDEN_PATH.name})")
        have = set()
    missing = [item for item in items if item["id"] not in have]
    if missing:
        examples = []
        for item in missing:
            inputs, reference = split(item)
            examples.append({"inputs": inputs, "outputs": reference,
                             "metadata": {"golden_id": item["id"], "kind": item["kind"]}})
        client.create_examples(dataset_name=name, examples=examples)


_HOME_RE = re.compile(r"/(?:home|Users)/[^/\s'\"`]+/")


def redact_home(value):
    """Replace /home/<user>/ and /Users/<user>/ with ~/ in every string of `value`: results
    files are committed to a public repo, and tool errors quote absolute paths."""
    if isinstance(value, str):
        return _HOME_RE.sub("~/", value)
    if isinstance(value, list):
        return [redact_home(v) for v in value]
    if isinstance(value, dict):
        return {k: redact_home(v) for k, v in value.items()}
    return value


def _row(result_row: dict) -> dict:
    example = result_row["example"]
    results = {
        r.key: {"key": r.key, "score": r.score, "comment": r.comment or ""}
        for r in result_row["evaluation_results"]["results"]
    }
    outputs = getattr(result_row["run"], "outputs", None) or {}
    return redact_home({"id": example.metadata["golden_id"], "kind": example.metadata["kind"],
                        "error": result_row["run"].error, "results": results,
                        "outputs": {k: outputs.get(k) for k in OUTPUT_KEYS}})


def result_path(run: str, sha: str, now: datetime | None = None, out_dir: Path | None = None) -> Path:
    """`<UTC date-time>_<run>_<short sha>.json`, never an existing file: a second run in the
    same second gets a `-2`, `-3`, ... suffix instead of overwriting."""
    now = now or datetime.now(UTC)
    out_dir = out_dir or RESULTS_DIR
    stem = f"{now:%Y-%m-%dT%H%M%SZ}_{run}_{sha[:7]}"
    path, n = out_dir / f"{stem}.json", 1
    while path.exists():
        n += 1
        path = out_dir / f"{stem}-{n}.json"
    return path


def write_record(record: dict, path: Path) -> None:
    """Write a run record without ever replacing one, and point LATEST at it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x") as fh:  # "x": fail rather than overwrite
        fh.write(json.dumps(record, indent=2) + "\n")
    (path.parent / LATEST_PATH.name).write_text(path.name + "\n")


async def run_live(limit: int | None, max_concurrency: int, run_name: str | None = None) -> int:
    load_dotenv()  # before the key check: the key normally lives in .env
    if not os.getenv("LANGSMITH_API_KEY"):
        raise SystemExit("make evals-live uploads to LangSmith: set LANGSMITH_API_KEY in .env")

    from langsmith import Client
    from langsmith.evaluation import aevaluate

    from agent.graph import build_graph
    from agent.tools import MCPToolbox
    from evals.evaluators import make_judge
    from rag.pipeline import get_llm
    from retrieval.bench import detect_hardware, git_sha
    from tools.results import render, validate

    ensure_corpus()
    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    agent_model = model_name(get_llm())
    judge_llm = build_judge_llm()
    judge_model = model_name(judge_llm)
    check_judge_differs(agent_model, judge_model)

    items = load_golden()
    client = Client()
    dataset = f"{DATASET_PREFIX}-{golden_sha()[:8]}"
    sync_dataset(client, items, dataset)

    graph = build_graph(get_llm=get_llm, toolbox=MCPToolbox(), checkpointer=InMemorySaver())

    async def target(inputs: dict) -> dict:
        return await run_question(graph, inputs["question"], inputs.get("clarify_with"))

    data = client.list_examples(dataset_name=dataset, limit=limit) if limit else dataset
    params = {
        "dataset": dataset, "golden_sha256": golden_sha(), "limit": limit,
        "llm_provider": provider, "agent_model": agent_model,
        "judge_provider": os.getenv("EVAL_JUDGE_PROVIDER", "anthropic"),
        "judge_model": judge_model, "judge_prompt": JUDGE_PROMPT_VERSION,
        "retriever_backend": os.getenv("RETRIEVER_BACKEND", "pgvector"),
        "embedding_provider": os.getenv("EMBEDDING_PROVIDER", provider),
    }
    results = await aevaluate(
        target, data=data,
        evaluators=[*DETERMINISTIC, make_judge(judge_llm)],
        experiment_prefix=f"agent-golden-{provider}",
        metadata=params, max_concurrency=max_concurrency, client=client,
    )
    rows = [_row(r) async for r in results]
    keys = [*DETERMINISTIC_KEYS, "judge"]
    table = summarize(rows, keys)
    print(format_table(table, keys))

    from importlib import metadata as md
    from platform import python_version

    now = datetime.now(UTC)
    record = {
        "date": now.date().isoformat(), "git_sha": git_sha(), "area": "evals",
        "run": run_name or f"agent-golden-{provider}", "hardware": detect_hardware(),
        "versions": {"python": python_version(), "agent_model": agent_model,
                     "judge_model": judge_model,
                     **{p: md.version(p) for p in ("langgraph", "langsmith", "langchain-core")}},
        "params": {**params, "experiment": results.experiment_name},
        "metrics": {**table, "errors": sum(1 for r in rows if r["error"])},
        "notes": "make evals-live: every golden item through the real graph, MCP tools and "
                 f"models; LLM-as-judge prompt {JUDGE_PROMPT_VERSION}, judge score scaled "
                 "(score - 1) / 4. Pass rates count applicable items only.",
    }
    record["items"] = rows  # per-item outputs, scores and judge comments, for diagnosis
    errors = validate(record)
    if errors:
        raise ValueError("results record violates §4.3: " + "; ".join(errors))
    out = result_path(record["run"], record["git_sha"], now)
    write_record(record, out)
    render("evals")
    print(f"\nwrote {out.relative_to(REPO_ROOT)}; LangSmith experiment {results.experiment_name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score the agent on the golden set.")
    parser.add_argument("--live", action="store_true",
                        help="real models and tools, results to LangSmith (costs money)")
    parser.add_argument("--limit", type=int, help="live only: the first N examples")
    parser.add_argument("--max-concurrency", type=int, default=2,
                        help="live only: examples in flight at once")
    parser.add_argument("--run", help="live only: run name in the results file "
                                      "(default agent-golden-<LLM_PROVIDER>)")
    args = parser.parse_args(argv)
    if args.run is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.run):
        parser.error("--run: letters, digits, '.', '_' and '-' only")
    if args.live:
        return asyncio.run(run_live(args.limit, args.max_concurrency, args.run))
    return offline_main()


if __name__ == "__main__":
    sys.exit(main())
