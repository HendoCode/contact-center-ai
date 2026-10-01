# contact-center-ai — shared task runner.
#
# `up` / `down` / `seed` / `ingest` mirror the quickstart and the published
# "Anchoring AI" post commands. `test` / `lint` / `check-public` are the CI
# gates. `demo` / `bench` / `evals` belong to not-yet-landed tickets (L3, R3,
# L2) and print "not yet" until those land.

.PHONY: up down seed ingest test lint check-public demo bench evals

PYTHON := uv run python

# ── Local data pipeline ───────────────────────────────────────────────────────

up:
	docker compose up -d

down:
	docker compose down

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

# ── Placeholders until their owning tickets land (L3 demo, R3 bench, L2 evals) ─
demo bench evals:
	@echo "not yet: $@"