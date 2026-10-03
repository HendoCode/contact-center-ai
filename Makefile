# contact-center-ai — shared task runner.
#
# `up` / `down` / `seed` / `ingest` mirror the quickstart and the published
# "Anchoring AI" post commands. `test` / `lint` / `check-public` are the CI
# gates. `bench` is the R3 retrieval benchmark. `demo` is the L3 Compose demo.
# `evals` is the L2 agent golden set offline (a CI gate); `evals-live` is the same set
# against real models, uploaded to LangSmith.

.PHONY: up down seed ingest test lint check-public demo bench evals evals-live finetune-data finetune-label load-snowflake load-databricks load-dry-run dbt-build

PYTHON := uv run python

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

# Embed and store the transcripts in the vector store.
ingest:
	$(PYTHON) -m rag.pipeline --ingest

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
dbt-build:
	@case "$(WAREHOUSE)" in snowflake|databricks) ;; *) echo "usage: make dbt-build WAREHOUSE=snowflake|databricks" >&2; exit 2 ;; esac
	$(call WAREHOUSE_RUN,$(WAREHOUSE)) uv run --group dbt --group $(WAREHOUSE) dbt build --project-dir olap/dbt --profiles-dir olap/dbt --target $(WAREHOUSE)

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
# LLM-as-judge; writes results/evals/. Needs the stack (`make up seed ingest`, dbt build),
# provider keys, LANGSMITH_API_KEY and a judge model unlike the agent's. Costs API money.
# Pass flags with ARGS, e.g. ARGS="--limit 5".
evals-live:
	uv run --group agent python -m evals.run --live $(ARGS)