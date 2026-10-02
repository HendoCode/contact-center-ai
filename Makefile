# contact-center-ai — shared task runner.
#
# `up` / `down` / `seed` / `ingest` mirror the quickstart and the published
# "Anchoring AI" post commands. `test` / `lint` / `check-public` are the CI
# gates. `bench` is the R3 retrieval benchmark. `demo` is the L3 Compose demo.
# `evals` belongs to a not-yet-landed ticket (L2) and prints "not yet" until it lands.

.PHONY: up down seed ingest test lint check-public demo bench evals finetune-data finetune-label

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

# ── Placeholder until its owning ticket lands (L2 evals) ──────────────────────
# L2 replaces this. Until then `$(COMPOSE_APP) run --rm evals` runs the deterministic agent
# tests as a clearly labeled stub; it is not an eval set.
evals:
	@echo "not yet: $@"