#!/usr/bin/env bash
# The open-question retrieval experiment in one command. Backs `make evals-retrieval-experiment`.
#
#   tools/evals-retrieval-experiment.sh [--dry-run] [--k10]
#
# Same agent, same judge, only the open group (12 questions), in order:
# 1 pgvector, vector search, k=5        make evals-live, run open-pgvector-vector
# 2 LanceDB store                      make ingest with RETRIEVER_BACKEND=lancedb (reuses
#                                      .cache/embeddings/, so only new text is embedded)
# 3 LanceDB, hybrid search, k=5         make evals-live, run open-lance-hybrid
# 4 (--k10) LanceDB, hybrid, k=10       make evals-live, run open-lance-hybrid-k10
# 5 compare by run name                 make evals-compare A=open-pgvector-vector B=open-lance-hybrid
#                                      (--k10 also: A=open-lance-hybrid B=open-lance-hybrid-k10)
#
# The total estimate is printed first, and each run prints its own before it is paid for.
# The first step to fail stops the experiment with the fix. --dry-run prints the plan and
# every run's estimate and runs nothing. The keys come the evals-live way (1Password, or
# DIRECT=1 for the shell).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV="${UV:-uv}"
MAKE="${MAKE:-make}"
DRY=0 K10=0

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --k10) K10=1 ;;
    -h | --help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "evals-retrieval-experiment: unknown argument $arg (use --dry-run, --k10)" >&2; exit 2 ;;
  esac
done

say() { printf '==> %s\n' "$*"; }

step() {
  local name="$1" fix="$2"
  shift 2
  say "$name"
  if ! (cd "$ROOT" && "$@"); then
    printf 'evals-retrieval-experiment: %s failed: %s\n' "$name" "$fix" >&2
    exit 1
  fi
}

# One open-group run: env for the search, then evals-live under that run name.
run() {
  local name="$1" backend="$2" mode="$3" k="$4"
  local dry=()
  ((DRY)) && dry=(DRY=1)
  step "run $name ($backend, $mode, k=$k)" \
    "fix the line above, then rerun this command (finished runs are kept; compare uses the newest of each name)" \
    env RETRIEVER_BACKEND="$backend" AGENT_RETRIEVAL_MODE="$mode" AGENT_RETRIEVAL_K="$k" \
    "$MAKE" -s evals-live "${dry[@]}" ARGS="--group open --run $name"
}

runs=2
((K10)) && runs=3
# Price the judge the way evals-live will run it (tools/evals-live-run.sh's defaults).
estimate="$(cd "$ROOT" && EVAL_JUDGE_PROVIDER="${EVAL_JUDGE_PROVIDER:-openai}" \
  EVAL_JUDGE_MODEL="${EVAL_JUDGE_MODEL:-anthropic/claude-opus-5.5}" \
  LLM_BASE_URL="${LLM_BASE_URL:-https://openrouter.ai/api/v1}" \
  "$UV" run -q --group agent python -m evals.preflight --estimate-only --group open 2>/dev/null \
  | sed -n 's/^==> Estimated cost for [0-9]* question(s): \$\([0-9.]*\) to \$\([0-9.]*\)$/\1 \2/p' || true)"
if [[ -n "$estimate" ]]; then
  read -r low high <<<"$estimate"
  total="$(awk -v l="$low" -v h="$high" -v n="$runs" 'BEGIN { printf "$%.2f to $%.2f", l * n, h * n }')"
  extra=""
  ((K10)) && extra=" (the k=10 run reads twice the transcripts; allow up to about \$0.35 for it)"
  say "Total estimate for $runs open-group runs: $total$extra"
else
  say "Total estimate unavailable (OpenRouter's price list did not load); each run still prints its own"
fi
((DRY)) && say "Dry run: every run prints its plan and estimate; nothing is ingested, run or compared"

run open-pgvector-vector pgvector vector 5
if ((DRY)); then
  say "would ingest: RETRIEVER_BACKEND=lancedb make ingest"
else
  step "ingest LanceDB store" \
    "check the embedding provider (Ollama up for EMBEDDING_PROVIDER=ollama), then rerun this command" \
    env RETRIEVER_BACKEND=lancedb "$MAKE" -s ingest
fi
run open-lance-hybrid lancedb hybrid 5
((K10)) && run open-lance-hybrid-k10 lancedb hybrid 10

if ((DRY)); then
  say "would compare: make evals-compare A=open-pgvector-vector B=open-lance-hybrid"
  ((K10)) && say "would compare: make evals-compare A=open-lance-hybrid B=open-lance-hybrid-k10"
  exit 0
fi
step "compare pgvector vector -> LanceDB hybrid" "rerun 'make evals-compare A=open-pgvector-vector B=open-lance-hybrid'" \
  "$MAKE" -s evals-compare A=open-pgvector-vector B=open-lance-hybrid
if ((K10)); then
  step "compare k=5 -> k=10" "rerun 'make evals-compare A=open-lance-hybrid B=open-lance-hybrid-k10'" \
    "$MAKE" -s evals-compare A=open-lance-hybrid B=open-lance-hybrid-k10
fi
say "done: both runs are in results/evals/; the tool-err rows above should read 0"
