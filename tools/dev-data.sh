#!/usr/bin/env bash
# Build all local data on the host, in order: the same steps as the Compose `seed` service,
# plus the dev dbt build. Backs `make dev-data`.
#
#   tools/dev-data.sh
#
# 1 generate  synthetic JSON (data/synthetic/generate_data.py, seed 42)
# 2 schema    OLTP tables in Postgres (olap/oltp/apply.sh)
# 3 load      the JSON into those tables (olap/seed.py)
# 4 ingest    transcripts into the vector store (rag.pipeline --ingest; reuses the
#             .cache/embeddings/ cache, so a rerun embeds only new text)
# 5 dbt       the marts and the semantic manifest for the dev target (dbt build --target dev)
#
# Every step is idempotent. One line per step; the first failure stops the run with the
# command to fix or rerun. Needs Postgres up (make up, or docker compose up -d db).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV="${UV:-uv}"
LOG="$(mktemp "${TMPDIR:-/tmp}/ccai-dev-data.XXXXXX")"
trap 'rm -f -- "$LOG"' EXIT

step() {
  local n="$1" name="$2" fix="$3"
  shift 3
  printf '[%s/5] %-8s ... ' "$n" "$name"
  if (cd "$ROOT" && "$@") >"$LOG" 2>&1; then
    echo ok
  else
    echo FAILED
    tail -n 5 "$LOG" | sed 's/^/    /' >&2
    printf 'dev-data: step %s (%s) failed: %s\n' "$n" "$name" "$fix" >&2
    exit 1
  fi
}

step 1 generate "rerun 'uv run python data/synthetic/generate_data.py'" \
  "$UV" run python data/synthetic/generate_data.py
step 2 schema "start Postgres ('make up' or 'docker compose up -d db'), then rerun 'olap/oltp/apply.sh'" \
  olap/oltp/apply.sh
step 3 load "rerun 'uv run python olap/seed.py' after the schema step succeeds" \
  "$UV" run python olap/seed.py
step 4 ingest "check the embedding provider (Ollama up for EMBEDDING_PROVIDER=ollama), then 'make ingest'" \
  "$UV" run python -m rag.pipeline --ingest
step 5 dbt "rerun 'make dbt-build-dev' and read the failing model in its output" \
  "$UV" run --group dbt dbt build --project-dir olap/dbt --profiles-dir olap/dbt --target dev
echo "dev-data: done: raw tables, vector store, marts and the dev semantic manifest are built"
