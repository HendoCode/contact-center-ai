# contact-center-ai — shared task runner.
#
# `up` / `down` / `seed` / `ingest` mirror the quickstart and the published
# "Anchoring AI" post commands. `test` / `lint` / `check-public` are the CI
# gates. `bench` is the R3 retrieval benchmark. `demo` is the L3 Compose demo.
# `evals` is the L2 agent golden set offline (a CI gate); `evals-live` is the same set
# against real models, uploaded to LangSmith.

.PHONY: up down seed ingest test lint check-public demo bench evals evals-live evals-compare evals-export finetune-data finetune-label load-snowflake load-databricks load-dry-run dbt-build dbt-build-dev dev-data lance-azure-check

PYTHON := uv run python
# RETRIEVER_BACKEND=lancedb needs the lance dependency group wherever the store is read or written.
LANCE_GROUP = $(if $(filter lancedb,$(RETRIEVER_BACKEND)),--group lance)

# The L3 stack lives in the `app` Compose profile, so a bare `docker compose up -d`
# still starts only db and ollama.
COMPOSE_APP := docker compose --profile app

# `make demo` defaults to the local, keyless path: chat on Ollama (llama3.2) and embeddings
# on Ollama (nomic-embed-text). Override the chat provider with DEMO_LLM_PROVIDER, e.g.
# `make demo DEMO_LLM_PROVIDER=openai` (needs OPENAI_API_KEY in .env, e.g. an OpenRouter key).
DEMO_LLM_PROVIDER ?= ollama

# ── Compose demo (L3) ─────────────────────────────────────────────────────────

# Brings the stack up (db, ollama, model pulls, seed, dbt build, the langgraph dev server)
# and runs the five scripted questions from agent/demo.py. The first run pulls models and
# embeds 1,250 transcripts on CPU, so expect several minutes; later runs reuse the volumes.
# Works from `cp .env.example .env`; no cloud key is needed on the default path.
demo:
	export LLM_PROVIDER=$(DEMO_LLM_PROVIDER) EMBEDDING_PROVIDER=ollama; \
	$(COMPOSE_APP) build seed && $(COMPOSE_APP) run --rm demo
	@echo
	@echo "Stack is still up. LangGraph dev server: http://localhost:2024 (API docs at /docs)."
	@echo "Stop it with: make down"

# ── Local data pipeline ───────────────────────────────────────────────────────

up:
	docker compose up -d

# `--profile '*'` so profiled services (app, ui, gpu) come down too; a bare `down` skips them.
down:
	docker compose --profile '*' down

# Regenerate the deterministic synthetic data (transcripts, csat, OLTP JSON).
seed:
	$(PYTHON) data/synthetic/generate_data.py

# Embed and store the transcripts in the vector store. Vectors are cached per model in
# .cache/embeddings/ (shared with make bench), so a rerun embeds only new text;
# ARGS="--no-cache" embeds everything afresh.
ingest:
	uv run $(LANCE_GROUP) python -m rag.pipeline --ingest $(ARGS)

# ── CI gates ─────────────────────────────────────────────────────────────────

test:
	uv run pytest -m "not integration"

lint:
	uv run ruff check .

check-public:
	tools/check_public.sh

# ── Benchmarks ───────────────────────────────────────────────────────────────

# R3: every labeled query through both retrieval backends and every search mode.
# Needs Postgres (`make up`), seeded data and an embedding provider; pass extra
# flags with ARGS, e.g. `make bench ARGS="--backends lancedb"` or `ARGS="--scale 80"`.
bench:
	uv run --group lance python -m retrieval.bench $(ARGS)

# LanceDB's half of the bench on local disk and on az:// (the envs/dev ADLS account), side by
# side. Needs `az login` (Entra; no account keys). ARGS="--dry-run" prints the plan only.
lance-azure-check:
	uv run --group lance python -m retrieval.lance_azure_check $(ARGS)

# ── Fine-tune dataset (F1) ────────────────────────────────────────────────────

# Gold labels, the 800/150/300 split and test.jsonl under data/finetune/. Offline, no API.
finetune-data:
	$(PYTHON) -m models.finetune.data_gen build

# Teacher labels for train and dev. Calls the LLM_PROVIDER model and costs API money;
# resumable. Pass flags with ARGS, e.g. ARGS="--limit 5" or ARGS="--dry-run".
finetune-label:
	$(PYTHON) -m models.finetune.data_gen label $(ARGS)

# ── Warehouse loaders (S2) ────────────────────────────────────────────────────

# Load the OLTP JSON into raw tables on Snowflake or Databricks. Each warehouse's adapter
# group lives in its own uv env (the two groups conflict), hence one target each.
# The SNOWFLAKE_* / DATABRICKS_* settings come from 1Password through tools/warehouse-run.sh
# (templates in tools/op/). DRY=1 prints what would run and the variable names it resolved;
# DIRECT=1 skips 1Password and reads the variables from the shell, as before.
# `make load-dry-run` prints every statement for both and connects to nothing.
WAREHOUSE_RUN = $(if $(DIRECT),,tools/warehouse-run.sh $(if $(DRY),--dry-run) $(1) --)

load-snowflake:
	$(call WAREHOUSE_RUN,snowflake) uv run --group dbt --group snowflake python -m olap.dbt.loaders snowflake

load-databricks:
	$(call WAREHOUSE_RUN,databricks) uv run --group dbt --group databricks python -m olap.dbt.loaders databricks

# dbt build on a warehouse target, settings from 1Password: make dbt-build WAREHOUSE=snowflake
# Artifacts go to olap/dbt/target/<warehouse>/, so the dev target/semantic_manifest.json that
# the metric tools (mf) read on local Postgres is never overwritten with warehouse SQL.
dbt-build:
	@case "$(WAREHOUSE)" in snowflake|databricks) ;; *) echo "usage: make dbt-build WAREHOUSE=snowflake|databricks" >&2; exit 2 ;; esac
	$(call WAREHOUSE_RUN,$(WAREHOUSE)) uv run --group dbt --group $(WAREHOUSE) dbt build --project-dir olap/dbt --profiles-dir olap/dbt --target $(WAREHOUSE) --target-path target/$(WAREHOUSE)

# All local data on the host, in order, idempotent, one line per step: generate the synthetic
# JSON, apply the OLTP schema, load it, ingest (embedding cache), dbt build for dev. The
# first step to fail stops the run with its fix. Needs Postgres up (make up).
dev-data:
	tools/dev-data.sh

# dbt build on the local dev target (the Compose Postgres): the marts the metric tools and
# make evals-live query. Run it after make seed; artifacts go to the default olap/dbt/target/.
dbt-build-dev:
	uv run --group dbt dbt build --project-dir olap/dbt --profiles-dir olap/dbt --target dev

load-dry-run:
	$(PYTHON) -m olap.dbt.loaders snowflake --dry-run
	$(PYTHON) -m olap.dbt.loaders databricks --dry-run

# ── Agent evals (L2) ──────────────────────────────────────────────────────────

# The golden set (evals/datasets/agent_golden.jsonl) through the real graph with a scripted
# classifier and stub tools: no keys, no DB, no network. Fails on any failure not listed in
# evals/known_failures.json, or on a listed one that now passes. CI runs it.
evals:
	uv run --group agent python -m evals.run

# The same set against the real models and MCP tools, as a LangSmith experiment with an
# LLM-as-judge through OpenRouter; writes results/evals/. Needs the stack (`make up seed
# ingest`, dbt build). Costs API money. LANGSMITH_API_KEY and the OpenRouter OPENAI_API_KEY
# come from 1Password through tools/evals-live-run.sh: set LANGSMITH_KEY_REF and
# EVALS_OPENAI_KEY_REF to your op:// references. It checks the prerequisites and prints a
# cost estimate first. Pass flags with ARGS, e.g. ARGS="--limit 5" for a smoke run.
# DRY=1 prints the variable names and the plan; DIRECT=1 skips 1Password and reads the
# keys and judge settings from the shell or .env, as before.
EVALS_RUN = $(if $(DIRECT),,tools/evals-live-run.sh $(if $(DRY),--dry-run) --)

evals-live:
	$(EVALS_RUN) uv run --group agent $(LANCE_GROUP) python -m evals.run --live $(ARGS)

# Before/after table of two evals-live result files, per group and check, with deltas and a
# warning when the agent, judge, judge prompt, golden set or limit differ. B defaults to the
# file results/evals/LATEST names. Name a run with ARGS="--run <name>" on evals-live.
evals-compare:
	@test -n "$(A)" || { echo "usage: make evals-compare A=results/evals/<before>.json [B=<after>.json]" >&2; exit 2; }
	$(PYTHON) -m evals.compare $(A) $(B)

# Per-item results of one LangSmith experiment, read-only:
#   make evals-export EXP=agent-golden-openai-74042631 [OUT=<file>]
# LANGSMITH_API_KEY comes from 1Password when LANGSMITH_KEY_REF is set (as for evals-live),
# otherwise from the shell or .env.
evals-export:
	@test -n "$(EXP)" || { echo "usage: make evals-export EXP=<LangSmith experiment> [OUT=<file>]" >&2; exit 2; }
	$(if $(LANGSMITH_KEY_REF),LANGSMITH_API_KEY="$$(op read '$(LANGSMITH_KEY_REF)')") uv run --group agent python -m evals.export $(EXP) $(if $(OUT),--out $(OUT))
