"""Change-analysis demo: six timed questions, behind a preflight that names the fix.

    python -m agent.demo_change [--strict] [--preflight-only] [--skip-preflight]
    python -m agent.demo_change --replay

`--replay` prints the transcript of the last verified live run under a RECORDED banner, with its
date and git SHA, for when a provider or the network is down. A live run that finishes with every
step as scripted writes that record (`results/demo/`).

Supervisors ask why a number moved. The six steps put that to the real graph and the real MCP
tools, in order, printing each answer with its timing:

1. why fee-call satisfaction fell after the fee schedule change (metric, windows, SQL, drivers)
2. "What do we make on fees?": the agent asks which fee, then answers after the choice
3. a follow-up on step 1's thread: what to coach on, as patterns across calls
4. the same kind of question for a change type the route was never tuned on
5. opening one citation from step 1: the call id, date and cited turn with a little context,
   account numbers masked to their last four digits
6. step 1's question as an MCP client asks it, through the `explain_change` tool

The preflight runs first and checks the data files, Postgres, the vector index, the semantic
layer and the chat and embedding models. Every missing piece prints the command that fixes it,
and nothing is asked until all of them are present.

A step passes when its route (and, where scripted, the interrupt, the metric and the evidence)
came out as scripted. A small local model can route differently from run to run; that is
printed as a mismatch and does not fail the run unless `--strict` is given. An exception
(database down, model missing) always fails it. `make demo-change` runs this on the host.
"""

import argparse
import asyncio
import io
import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psycopg2

from agent.citation_view import render_citation
from agent.demo import _clip, _indent
from agent.tools import MCPToolbox
from ccai_mcp.change import RESULT_MARKER, load_change_log
from ccai_mcp.masking import mask_accounts

REPO_ROOT = Path(__file__).resolve().parent.parent
SYNTHETIC_DIR = REPO_ROOT / "data" / "synthetic"
MANIFEST = REPO_ROOT / "olap" / "dbt" / "target" / "semantic_manifest.json"
DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/contactcenter"

MAX_ANSWER_LINES = 40
MAX_SQL_LINES = 4
FEE_QUESTION = "Why did satisfaction on fee calls drop after the fee schedule change?"
FEE_CHANGE = "Fee schedule change"
FEE_CATEGORY = "fee_dispute"
FEE_CHOICE = "overdraft_fee_revenue"
RECORD_PATH = REPO_ROOT / "results" / "demo" / "change-demo-last-verified-run.json"


# ── preflight ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""


def _env(name: str, default: str = "") -> str:
    return os.getenv(name) or default


def _provider(name: str, fallback: str = "openai") -> str:
    return (os.getenv(name) or fallback).lower()


# The env var that names the chat model for each LLM_PROVIDER (see rag/pipeline.py get_llm).
CHAT_MODEL_ENV = {"openai": "LLM_MODEL", "ollama": "OLLAMA_MODEL", "anthropic": "ANTHROPIC_MODEL",
                  "fireworks": "FIREWORKS_MODEL", "vllm": "VLLM_MODEL"}


def _chat_model() -> str:
    return _env(CHAT_MODEL_ENV.get(_provider("LLM_PROVIDER"), "LLM_MODEL"), "unset")


def check_data() -> Check:
    needed = ["transcripts.json", "csat.json", "change_log.json", "branches.json"]
    missing = [n for n in needed if not (SYNTHETIC_DIR / n).exists()]
    if missing:
        return Check("data", False, f"missing {', '.join(missing)} in data/synthetic/",
                     "make seed")
    count = _generated_calls()
    return Check("data", True, f"{count:,} synthetic calls and a change log of "
                 f"{len(load_change_log())} changes")


def _scalar(url: str, sql: str):
    with psycopg2.connect(url, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]


def check_postgres(url: str) -> Check:
    try:
        version = _scalar(url, "SHOW server_version")
    except psycopg2.Error as exc:
        return Check("postgres", False, f"cannot connect to DATABASE_URL: {_first_line(exc)}",
                     "make up   (or point DATABASE_URL at a running Postgres)")
    return Check("postgres", True, f"reachable, version {version}")


def check_oltp(url: str, expected: int) -> Check:
    fix = "make dev-data   (or: olap/oltp/apply.sh && uv run python olap/seed.py)"
    try:
        loaded = _scalar(url, "SELECT count(*) FROM interaction")
    except psycopg2.Error as exc:
        return Check("oltp", False, f"the OLTP tables are not loaded: {_first_line(exc)}", fix)
    if loaded != expected:
        return Check("oltp", False, f"{loaded:,} interactions loaded, {expected:,} generated", fix)
    return Check("oltp", True, f"{loaded:,} interactions loaded, matching the generated data")


def check_marts(url: str) -> Check:
    fix = "make dbt-build-dev"
    try:
        rows = _scalar(url, "SELECT count(*) FROM marts.f_csat")
    except psycopg2.Error as exc:
        return Check("semantic layer", False, f"the marts are not built: {_first_line(exc)}", fix)
    if not MANIFEST.exists():
        return Check("semantic layer", False,
                     "olap/dbt/target/semantic_manifest.json is missing: MetricFlow has nothing "
                     "to read", fix)
    return Check("semantic layer", True, f"marts built ({rows:,} CSAT rows) and the manifest exists")


def check_index(expected: int) -> Check:
    from retrieval import get_retriever

    fix = "make ingest   (after a regenerate: uv run python -m rag.pipeline --reset, then make ingest)"
    try:
        retriever = get_retriever()
        indexed = retriever.count()
    except Exception as exc:  # noqa: BLE001 - any backend failure means the index is unusable
        return Check("index", False, f"cannot read the vector index: {_first_line(exc)}", fix)
    if indexed != expected:
        return Check("index", False, f"{indexed:,} transcripts indexed, {expected:,} generated",
                     fix)
    return Check("index", True, f"{indexed:,} transcripts indexed in {retriever.name}")


def _ollama_models(base_url: str) -> set[str]:
    with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=5) as response:
        return {m["name"] for m in json.load(response)["models"]}


def _tagged(model: str) -> str:
    return model if ":" in model else f"{model}:latest"


def check_ollama(needs: dict[str, str]) -> list[Check]:
    """`needs` maps a role ("chat", "embeddings") to the Ollama model that role uses."""
    base = _env("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        have = _ollama_models(base)
    except Exception as exc:  # noqa: BLE001 - refused, timed out or bad JSON: all mean "not serving"
        return [Check(f"{role} model", False,
                      f"Ollama is not reachable at {base}: {_first_line(exc)}",
                      "make up   (or start Ollama and set OLLAMA_BASE_URL)")
                for role in needs]
    return [
        Check(f"{role} model", _tagged(model) in have,
              f"{model} on Ollama" + ("" if _tagged(model) in have else " is not pulled"),
              "" if _tagged(model) in have else
              f"ollama pull {model}   (Compose: docker compose exec ollama ollama pull {model})")
        for role, model in needs.items()
    ]


def check_models() -> list[Check]:
    chat = _provider("LLM_PROVIDER")
    embeddings = _provider("EMBEDDING_PROVIDER", chat)
    checks: list[Check] = []
    local: dict[str, str] = {}

    if chat == "ollama":
        local["chat"] = _env("OLLAMA_MODEL", "llama3.2")
    else:
        key_name = {"anthropic": "ANTHROPIC_API_KEY", "fireworks": "FIREWORKS_API_KEY"}.get(
            chat, "OPENAI_API_KEY")
        ok = bool(os.getenv(key_name)) or chat == "vllm"
        checks.append(Check("chat model", ok,
                            f"LLM_PROVIDER={chat}" + ("" if ok else f", but {key_name} is not set"),
                            "" if ok else f"set {key_name} in .env, or run with LLM_PROVIDER=ollama"))
    if embeddings == "ollama":
        local["embeddings"] = _env("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
    else:
        ok = bool(os.getenv("OPENAI_API_KEY"))
        checks.append(Check("embeddings model", ok,
                            f"EMBEDDING_PROVIDER={embeddings}"
                            + ("" if ok else ", but OPENAI_API_KEY is not set"),
                            "" if ok else "set OPENAI_API_KEY, or run with EMBEDDING_PROVIDER=ollama"))
    return checks + (check_ollama(local) if local else [])


def _first_line(exc: Exception) -> str:
    return (str(exc).strip().splitlines() or [type(exc).__name__])[0]


def _generated_calls() -> int:
    return len(json.loads((SYNTHETIC_DIR / "transcripts.json").read_text()))


def preflight() -> list[Check]:
    """Every check, in the order the pieces are built. Later ones are skipped when an earlier
    one they depend on failed, so a missing database prints one fix, not five."""
    url = _env("DATABASE_URL", DEFAULT_DATABASE_URL)
    checks = [check_data()]
    expected = _generated_calls() if checks[0].ok else 0
    checks += check_models()
    postgres = check_postgres(url)
    checks.append(postgres)
    if postgres.ok:
        checks.append(check_oltp(url, expected))
        checks.append(check_marts(url))
        if checks[0].ok:
            checks.append(check_index(expected))
    return checks


def print_preflight(checks: list[Check]) -> bool:
    print("preflight")
    for check in checks:
        print(f"  [{'ok' if check.ok else 'FAIL'}] {check.name}: {check.detail}")
        if not check.ok:
            print(f"         fix: {check.fix}")
    failed = [c for c in checks if not c.ok]
    if failed:
        print(f"preflight failed: {len(failed)} of {len(checks)} checks need attention, "
              "nothing was asked.", file=sys.stderr)
    return not failed


# ── the script ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Step:
    label: str
    kind: str  # "ask" (the graph), "citation" (open one cited call) or "mcp" (the tool directly)
    question: str = ""
    thread: str = ""  # steps with the same thread name share one graph thread
    route: str = ""
    metric: str | None = None  # the metric a step's answer should name
    interrupt: bool = False
    resume_with: str | None = None  # option id to answer the clarify question with
    mode: str = "drivers"  # the explain_change mode the answer should carry


STEPS = [
    Step("why a fee-call score fell", "ask", FEE_QUESTION, thread="fees",
         route="explain_change", metric="csat_average_score"),
    Step("which fee? (clarify, then answer)", "ask", "What do we make on fees?", thread="revenue",
         route="resolve_metric", metric=FEE_CHOICE, interrupt=True, resume_with=FEE_CHOICE),
    Step("coaching follow-up on the same thread", "ask",
         "What should supervisors coach on for fee calls?", thread="fees",
         route="explain_change", mode="coaching"),
    Step("a change type the route was never tuned on", "ask",
         "Why did online banking calls go up in late July?", thread="online",
         route="explain_change", metric="call_volume"),
    Step("open one citation", "citation"),
    Step("step 1 from an MCP client", "mcp", FEE_QUESTION, metric="csat_average_score"),
]


@dataclass
class Context:
    """What earlier steps leave for later ones."""

    fee_change: dict | None = None
    threads: dict[str, str] = field(default_factory=dict)

    def thread_id(self, name: str) -> str:
        return self.threads.setdefault(name, str(uuid.uuid4()))


@dataclass
class Outcome:
    label: str
    seconds: float
    mismatches: list[str]
    error: str | None = None


def _print_result(result: dict) -> None:
    print(f"  route:    {result['route']}")
    if result.get("metric_names"):
        print(f"  metrics:  {', '.join(result['metric_names'])}")
    print(f"  grounded: {'yes' if result['grounded'] else 'NO'}   "
          f"citations: {', '.join(result['citations']) or 'none'}")
    answer = result["answer"]
    if result.get("sql"):
        answer = answer.split("\nSQL:\n")[0]
        print("  sql:")
        print(_indent(_clip(result["sql"], MAX_SQL_LINES)))
    print("  answer:")
    print(_indent(_clip(answer, MAX_ANSWER_LINES)))


def _expect_change(step: Step, result: dict) -> list[str]:
    problems: list[str] = []
    change = result.get("change")
    if not change:
        return ["no change evidence came back with the answer"]
    if step.metric and change.get("metric") != step.metric:
        problems.append(f"explained {change.get('metric')}, scripted metric is {step.metric}")
    if change.get("mode") != step.mode:
        problems.append(f"answered in {change.get('mode')} mode, scripted mode is {step.mode}")
    if step.mode == "drivers":
        windows = change.get("windows") or {}
        if not all((windows.get(side) or {}).get("sql") for side in ("before", "after")):
            problems.append("an answer window has no SQL")
        if not any(d.get("citations") for d in change.get("drivers", [])):
            problems.append("no driver carries a citation")
    elif not change.get("patterns"):
        problems.append("no pattern across calls came back")
    return problems


async def run_ask(graph, step: Step, ctx: Context) -> list[str]:
    from langgraph.types import Command

    config = {"configurable": {"thread_id": ctx.thread_id(step.thread)}}
    problems: list[str] = []
    result = await graph.ainvoke({"question": step.question}, config)

    interrupts = result.get("__interrupt__")
    if interrupts:
        payload = interrupts[0].value
        print(f"  INTERRUPT: {payload['prompt']}")
        for option in payload["options"]:
            print(f"    - {option['id']}  ({option['label']})")
        if not step.interrupt:
            problems.append("interrupted, but this step is scripted not to")
        ids = [option["id"] for option in payload["options"]]
        choice = step.resume_with if step.resume_with in ids else ids[0]
        print(f"  resume:   choices=[{choice!r}] on the same thread")
        result = await graph.ainvoke(Command(resume={"choices": [choice]}), config)
    elif step.interrupt:
        problems.append("expected an interrupt on this ambiguous question; none fired")

    _print_result(result)
    if result["route"] != step.route:
        problems.append(f"routed to {result['route']}, scripted route is {step.route}")
    elif step.route == "explain_change":
        problems += _expect_change(step, result)
    elif step.metric and step.metric not in (result.get("metric_names") or []):
        problems.append(f"answered {result.get('metric_names')}, scripted metric is {step.metric}")
    if step.thread == "fees" and step.mode == "drivers" and result.get("change"):
        ctx.fee_change = result["change"]
    return problems


def _first_citation(change: dict | None) -> dict | None:
    for driver in (change or {}).get("drivers", []):
        if driver.get("citations"):
            return driver["citations"][0]
    return None


async def run_citation(ctx: Context, fetch: Callable[[str], object | None]) -> list[str]:
    """Open the first citation of step 1: the call id, date and cited turn, accounts masked."""
    citation = _first_citation(ctx.fee_change)
    if citation is None:
        return ["step 1 left no citation to open"]
    call_id, quote = citation["call_id"], citation["quote"]
    hit = fetch(call_id)
    if hit is None:
        print(f"  citation  {call_id}")
        return [f"{call_id} is not in the vector store"]
    print(_indent(render_citation(call_id, quote, hit.metadata, hit.text)))
    problems = []
    if quote not in hit.text:
        problems.append("the quote is not in the stored transcript")
    if mask_accounts(hit.text) != hit.text:
        problems.append("an account number in the stored transcript is not masked to its last "
                        "four digits (the view above masks it)")
    return problems


def _mcp_arguments() -> dict:
    """What an MCP client's model would send for step 1: the question, read as the tool's inputs."""
    logged = {c["name"]: c["date"] for c in load_change_log()}
    return {"metric": "csat_average_score", "filters": {"category": [FEE_CATEGORY]},
            "change_point": logged[FEE_CHANGE], "question": FEE_QUESTION}


async def run_mcp(toolbox, step: Step, ctx: Context) -> list[str]:
    from ccai_mcp.change import split_change

    arguments = _mcp_arguments()
    print(f"  question: {step.question}")
    print(f"  call:     explain_change({json.dumps(arguments)})")
    print("  note:     the arguments are the client model's reading of the question, scripted here")
    text = await toolbox.call("explain_change", arguments)
    if RESULT_MARKER not in text:
        print(_indent(_clip(text, MAX_ANSWER_LINES)))
        return ["the tool returned no change result"]
    shown, change = split_change(text)
    print("  answer:")
    print(_indent(_clip(shown, MAX_ANSWER_LINES)))
    if ctx.fee_change is None:
        return ["step 1 left no result to compare with"]
    problems = []
    for side in ("before", "after"):
        here, there = change["windows"][side], ctx.fee_change["windows"][side]
        if (here["value"], here["n"]) != (there["value"], there["n"]):
            problems.append(f"{side} window differs from step 1: {here['value']} (n={here['n']}) "
                            f"vs {there['value']} (n={there['n']})")
    if not problems:
        print("  same numbers as step 1: both windows' values and n match")
    return problems


async def run_steps(graph, toolbox, fetch: Callable[[str], object | None],
                    steps: list[Step]) -> tuple[list[Outcome], list[str]]:
    """Run every step in order, printing as it goes. Returns the outcomes and the failures."""
    ctx, outcomes, failures = Context(), [], []
    for number, step in enumerate(steps, start=1):
        print(f"\n== {number}/{len(steps)}  {step.label} " + "=" * 20)
        if step.kind == "ask":
            print(f"  question: {step.question}")
        started = time.perf_counter()
        error, problems = None, []
        try:
            if step.kind == "ask":
                problems = await run_ask(graph, step, ctx)
            elif step.kind == "citation":
                problems = await run_citation(ctx, fetch)
            else:
                problems = await run_mcp(toolbox, step, ctx)
        except Exception as exc:  # noqa: BLE001 - report which step broke, then keep going
            error = f"{type(exc).__name__}: {exc}"
            failures.append(f"step {number} ({step.label}): {error}")
            print(f"  ERROR: {error}")
        seconds = time.perf_counter() - started
        for problem in problems:
            print(f"  MISMATCH: {problem}")
        print(f"  time:     {seconds:.1f}s")
        outcomes.append(Outcome(step.label, seconds, problems, error))
    return outcomes, failures


def print_summary(outcomes: list[Outcome]) -> int:
    print("\nsummary")
    for number, outcome in enumerate(outcomes, start=1):
        status = "error" if outcome.error else "as scripted" if not outcome.mismatches else "mismatch"
        print(f"  {number}. {outcome.label:<44} {outcome.seconds:6.1f}s  {status}")
    matched = sum(1 for o in outcomes if not o.error and not o.mismatches)
    print(f"  total {sum(o.seconds for o in outcomes):.1f}s; {matched}/{len(outcomes)} steps ran "
          "as scripted.")
    return matched


# ── record and replay ────────────────────────────────────────────────────────

class _Tee(io.TextIOBase):
    """Passes writes through to the real stdout and keeps a copy for the record."""

    def __init__(self, stream):
        self.stream, self.kept = stream, io.StringIO()

    def write(self, text: str) -> int:
        self.kept.write(text)
        return self.stream.write(text)

    def flush(self) -> None:
        self.stream.flush()


def _git_state() -> tuple[str, bool]:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()

    try:
        return git("rev-parse", "--short", "HEAD"), bool(git("status", "--porcelain"))
    except (OSError, subprocess.CalledProcessError):
        return "unknown", False


def write_record(transcript: str, path: Path = RECORD_PATH, *, steps: int = len(STEPS)) -> Path:
    """Store a verified run's transcript with its date and git SHA, for `--replay`."""
    sha, dirty = _git_state()
    record = {
        "version": 1,
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_sha": sha + ("-dirty" if dirty else ""),
        "chat_provider": _provider("LLM_PROVIDER"),
        "chat_model": _chat_model(),
        "steps": steps,
        "transcript": mask_accounts(transcript),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n")
    tmp.replace(path)
    return path


def replay(path: Path = RECORD_PATH) -> int:
    """Print the recorded run under a banner that cannot be mistaken for a live one."""
    try:
        record = json.loads(path.read_text())
    except FileNotFoundError:
        print(f"No recorded run at {path.relative_to(REPO_ROOT)}. A live `make demo-change` that "
              "finishes with every step as scripted writes one.", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"The recorded run at {path.name} cannot be read: {exc}", file=sys.stderr)
        return 1
    label = (f"Recorded run of make demo-change, {record['recorded_at'][:10]}, "
             f"commit {record['git_sha']}, model {record.get('chat_model', 'unknown')}")
    bar = "#" * 72
    banner = "\n".join([
        bar,
        "#  RECORDED RUN - NOT LIVE",
        f"#  recorded {record['recorded_at']}   git {record['git_sha']}   "
        f"chat provider {record['chat_provider']}",
        "#  Nothing below was computed just now: this replays the last verified live run.",
        bar,
    ])
    print(label)
    print(banner)
    print(record["transcript"].rstrip("\n"))
    print(banner)
    return 0


async def main(strict: bool, skip_preflight: bool, preflight_only: bool) -> int:
    from dotenv import load_dotenv

    load_dotenv()
    if not skip_preflight:
        if not print_preflight(preflight()):
            return 1
        if preflight_only:
            return 0
    from agent.checkpoint import open_checkpointer
    from agent.graph import build_graph
    from rag.pipeline import get_llm
    from retrieval import get_retriever

    toolbox = MCPToolbox()
    tee = _Tee(sys.stdout)
    sys.stdout = tee
    try:
        async with open_checkpointer() as saver:
            graph = build_graph(get_llm=get_llm, toolbox=toolbox, checkpointer=saver)
            outcomes, failures = await run_steps(graph, toolbox, get_retriever().get_by_id, STEPS)
        matched = print_summary(outcomes)
    finally:
        sys.stdout = tee.stream
    for failure in failures:
        print(f"FAILED {failure}", file=sys.stderr)
    if not failures and matched == len(STEPS):
        print(f"recorded this run for --replay: {write_record(tee.kept.getvalue()).relative_to(REPO_ROOT)}")
    else:
        print("not recorded: only a run with every step as scripted replaces the record.")
    return 1 if failures or (strict and matched < len(STEPS)) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the six change-analysis demo questions.")
    parser.add_argument("--strict", action="store_true",
                        help="exit non-zero when a step comes out differently from the script")
    parser.add_argument("--skip-preflight", action="store_true", help="do not check the setup first")
    parser.add_argument("--preflight-only", action="store_true", help="check the setup and stop")
    parser.add_argument("--replay", action="store_true",
                        help="print the last verified run, labeled as recorded; asks nothing")
    args = parser.parse_args()
    if args.replay:
        sys.exit(replay())
    sys.exit(asyncio.run(main(args.strict, args.skip_preflight, args.preflight_only)))
