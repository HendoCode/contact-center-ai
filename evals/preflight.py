"""Up-front checks and a cost estimate for `make evals-live`, run before any paid call.

    python -m evals.preflight [--limit N] [--estimate-only]

`tools/evals-live-run.sh` runs this inside `op run`, after the keys are resolved. Each
check prints one clear message and the run stops at the first failure:

- the agent's model differs from the judge's (the run itself refuses too, but only after
  loading the corpus);
- Postgres is reachable and the transcripts are embedded in it (the agent searches them),
  when RETRIEVER_BACKEND is pgvector;
- Ollama answers and has the embedding model (pulled once if missing), when
  EMBEDDING_PROVIDER is ollama.

The estimate prices the agent and judge models from OpenRouter's public model list
(https://openrouter.ai/api/v1/models, no key needed), using the token assumptions in
ASSUMPTIONS below. It is a range, not a quote.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
GOLDEN_PATH = Path(__file__).resolve().parent / "datasets" / "agent_golden.jsonl"
DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/contactcenter"


class PreflightError(Exception):
    """A prerequisite is missing; the message says what to do."""


@dataclass(frozen=True)
class Usage:
    """Tokens per question for one role: calls x (input, output) tokens, low and high."""
    calls: tuple[int, int]
    input_tokens: tuple[int, int]
    output_tokens: tuple[int, int]


# Assumed tokens per golden question. The agent classifies, may resolve a metric or
# search, then writes the answer; the judge reads the question, reference and answer once.
ASSUMPTIONS = {
    "agent": Usage(calls=(3, 6), input_tokens=(1_500, 4_000), output_tokens=(150, 400)),
    "judge": Usage(calls=(1, 1), input_tokens=(1_500, 3_000), output_tokens=(100, 300)),
}


def golden_count(path: Path = GOLDEN_PATH) -> int:
    return sum(1 for line in path.read_text().splitlines() if line.strip())


def fetch_prices(url: str = OPENROUTER_MODELS_URL, timeout: float = 15) -> dict[str, tuple[float, float]]:
    """{model id: (USD per input token, USD per output token)} from OpenRouter's model list."""
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        body = json.load(resp)
    prices = {}
    for m in body.get("data", []):
        p = m.get("pricing") or {}
        try:
            prices[m["id"]] = (float(p["prompt"]), float(p["completion"]))
        except (KeyError, TypeError, ValueError):
            continue
    return prices


def role_cost(price: tuple[float, float], usage: Usage, n: int) -> tuple[float, float]:
    """(low, high) USD for n questions."""
    p_in, p_out = price
    low = n * usage.calls[0] * (usage.input_tokens[0] * p_in + usage.output_tokens[0] * p_out)
    high = n * usage.calls[1] * (usage.input_tokens[1] * p_in + usage.output_tokens[1] * p_out)
    return low, high


def estimate(prices: dict[str, tuple[float, float]], models: dict[str, str | None], n: int,
             assumptions: dict[str, Usage] = ASSUMPTIONS) -> tuple[float, float, list[str]]:
    """Total (low, high) USD and one line per role. A role whose model is None (not served
    through OpenRouter) or missing from the price list is reported and left out."""
    total_low = total_high = 0.0
    lines = []
    for role, model in models.items():
        if model is None:
            lines.append(f"{role}: not priced (not served through OpenRouter)")
            continue
        if model not in prices:
            lines.append(f"{role}: {model} not in OpenRouter's model list, not priced")
            continue
        low, high = role_cost(prices[model], assumptions[role], n)
        total_low += low
        total_high += high
        p_in, p_out = prices[model]
        lines.append(f"{role}: {model} at ${p_in * 1e6:.2f}/${p_out * 1e6:.2f} per M in/out tokens"
                     f" -> ${low:.2f} to ${high:.2f}")
    return total_low, total_high, lines


def assumption_text(assumptions: dict[str, Usage] = ASSUMPTIONS) -> str:
    parts = []
    for role, u in assumptions.items():
        calls = f"{u.calls[0]}" if u.calls[0] == u.calls[1] else f"{u.calls[0]}-{u.calls[1]}"
        parts.append(f"{role} {calls} call(s) x {u.input_tokens[0]:,}-{u.input_tokens[1]:,} in"
                     f" / {u.output_tokens[0]:,}-{u.output_tokens[1]:,} out tokens")
    return "assumed per question: " + "; ".join(parts)


def _via_openrouter(base_url: str | None) -> bool:
    return (base_url or OPENROUTER_BASE_URL).rstrip("/") == OPENROUTER_BASE_URL


def priced_models(env: os._Environ | dict = os.environ) -> dict[str, str | None]:
    """The OpenRouter model ids to price for the agent and judge, None when a role does not
    go through OpenRouter. Mirrors rag.pipeline.get_llm and evals.run.build_judge_llm."""
    via = _via_openrouter(env.get("LLM_BASE_URL"))
    agent = None
    if env.get("LLM_PROVIDER", "openai").lower() == "openai" and via:
        agent = env.get("LLM_MODEL", "z-ai/glm-5.3-flash")
    judge = None
    if env.get("EVAL_JUDGE_PROVIDER", "anthropic").lower() == "openai" and via:
        judge = env.get("EVAL_JUDGE_MODEL")
    return {"agent": agent, "judge": judge}


# ── checks ────────────────────────────────────────────────────────────────────

def check_models_differ() -> str:
    from evals.run import build_judge_llm, check_judge_differs, model_name
    from rag.pipeline import get_llm

    try:
        agent, judge = model_name(get_llm()), model_name(build_judge_llm())
    except Exception as exc:  # a client that cannot even be built names its own problem
        raise PreflightError(f"could not set up the agent or judge model: {exc}") from exc
    try:
        check_judge_differs(agent, judge)
    except SystemExit as exc:
        raise PreflightError(str(exc)) from exc
    return f"agent model {agent}, judge model {judge}"


def check_postgres(url: str, collection: str) -> str:
    import psycopg

    try:
        with psycopg.connect(url, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('langchain_pg_embedding') IS NOT NULL")
            if not cur.fetchone()[0]:
                raise PreflightError("Postgres has no embeddings table: run 'make seed ingest'")
            cur.execute("SELECT count(*) FROM langchain_pg_embedding e JOIN langchain_pg_collection c"
                        " ON e.collection_id = c.uuid WHERE c.name = %s", (collection,))
            n = cur.fetchone()[0]
    except psycopg.OperationalError as exc:
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        raise PreflightError(f"Postgres is not reachable at DATABASE_URL ({first}): run 'make up'") from exc
    if n == 0:
        raise PreflightError(f"Postgres has no transcripts in collection {collection!r}: run 'make seed ingest'")
    return f"Postgres reachable, {n} transcripts embedded in {collection!r}"


def check_ollama(base_url: str, model: str, client=None) -> str:
    """Ollama answers and has the embedding model, pulling it once when missing."""
    from rag.ollama_models import OllamaUnavailable, ensure_model

    try:
        ensure_model(model, base_url, client=client)
    except OllamaUnavailable as exc:
        raise PreflightError(f"{exc}, or set EMBEDDING_PROVIDER to another provider") from exc
    return f"Ollama reachable at {base_url}, {model} present"


def run_checks(env: os._Environ | dict = os.environ) -> Iterator[str]:
    """Each check in turn, yielding its result line; the first failure raises."""
    yield check_models_differ()
    if env.get("RETRIEVER_BACKEND", "pgvector").lower() == "pgvector":
        yield check_postgres(env.get("DATABASE_URL", DEFAULT_DATABASE_URL),
                             env.get("COLLECTION_NAME", "call_transcripts"))
    embed = env.get("EMBEDDING_PROVIDER", env.get("LLM_PROVIDER", "openai")).lower()
    if embed == "ollama":
        yield check_ollama(env.get("OLLAMA_BASE_URL", "http://localhost:11434"),
                           env.get("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text"))


def print_estimate(limit: int | None) -> None:
    total = golden_count()
    n = min(limit, total) if limit else total
    models = priced_models()
    try:
        prices = fetch_prices()
    except (OSError, ValueError) as exc:
        print(f"==> Cost estimate unavailable: OpenRouter's model list did not load ({exc})")
        return
    low, high, lines = estimate(prices, models, n)
    print(f"==> Estimated cost for {n} question(s): ${low:.2f} to ${high:.2f}")
    for line in lines:
        print(f"    {line}")
    print(f"    {assumption_text()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Checks and a cost estimate before make evals-live.")
    parser.add_argument("--limit", type=int, help="questions the run will score (default all)")
    parser.add_argument("--estimate-only", action="store_true", help="print the estimate, skip the checks")
    args = parser.parse_args(argv)
    if not args.estimate_only:
        from dotenv import load_dotenv

        load_dotenv()  # the run reads .env too; check what it will see
        try:
            for line in run_checks():
                print(f"==> {line}", flush=True)
        except PreflightError as exc:
            print(f"evals-live: {exc}", file=sys.stderr)
            return 1
    print_estimate(args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
