# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A cleanroom portfolio mirror of active consulting work for a financial institution (credit union, confidential).
The real engagement involves call center supervisors querying member call recordings, transcripts, and CSAT
survey data using natural language. This repo uses synthetic data and is safe for public GitHub.

## Fictional client context

- Credit union with multiple teams independently using Claude, OpenAI, Gemini, plus AI in banking EIS systems
- MCP designed to be reusable across all teams, not just call center
- CSAT integration blocked pending client's 3rd-party provider transition
- Data currently staged manually in S3 while experimenting
- Client may consolidate all GenAI efforts in 3-6 months

## Commands

```bash
# Local dev setup (run once)
docker compose up -d                          # Start pgvector + Ollama (if using it)
uv venv --python 3.12                         # Create virtualenv (uv manages Python version)
uv sync --extra dev                           # Install exact versions from uv.lock
# Pull Ollama models (only needed when LLM_PROVIDER=ollama)
docker compose exec ollama ollama pull llama3.2
docker compose exec ollama ollama pull nomic-embed-text

# Data pipeline
python data/synthetic/generate_data.py        # Generate transcripts.json + csat.json
python -m rag.pipeline --ingest               # Embed and store in pgvector

# Test a RAG query end-to-end
python -m rag.pipeline --query "fraud disputes from last week"

# Start MCP server (stdio, for client connections)
python -m ccai_mcp.server

# Tests (framework + Make target live in the repo)
make test                              # == uv run pytest -m "not integration"
pytest tests/path/to/test_file.py::test_name  # single test

# Terraform (Azure infra)
cd infra/terraform && terraform init
terraform plan -var-file=prod.tfvars
terraform apply
```

Ruff linting and the shared task targets live in the root `Makefile` and `pyproject.toml`
(see `make lint`, `make test`, and `make check-public`).

## Architecture

```
generate_data.py → transcripts.json + csat.json
                        ↓
pipeline.py --ingest → embed (OpenAI default / Ollama local) → pgvector (PostgreSQL)
                        ↓
ccai_mcp/server.py (stdio) → 5 tools exposed to MCP clients
```

**Data flow across files:**
- `data/synthetic/generate_data.py` outputs `transcripts.json` (1,250 calls with full_text), `csat.json` (survey scores 1–5), and the OLTP JSON files loaded by `olap/seed.py`.
- `rag/embeddings.py` owns pgvector setup and the shared `get_embeddings()`; `retrieval/` (`get_retriever()`, `Hit`, `Retriever`) is the only entry point for search/ingest — `retrieval/pgvector_backend.py` is the sole caller of `get_vector_store()` apart from `--reset`. To swap from OpenAI embeddings to Anthropic/Gemini, only this file needs to change.
- `rag/pipeline.py` owns ingestion (`--ingest`) and querying (`rag_query()`). Ingestion upserts by `call_id`, so re-running `--ingest` is idempotent (rows ingested before this change have UUID ids and need `--reset` first).
- `ccai_mcp/tools.py` implements the RAG/CSAT tools (`search_transcripts`, `get_call_summary`, `query_csat`). `search_transcripts` goes through `rag_query()` and `get_call_summary` uses `get_retriever().get_by_id()`; `query_csat` reads the Postgres `f_csat` fact (falling back to the `csat_survey` source), not `csat.json`.
- `ccai_mcp/metrics.py` implements the semantic-layer tools (`query_metric`, `ask_the_analyst`), which shell out to the committed `mf` (MetricFlow) CLI over the `metrics.yml` catalog.
- `ccai_mcp/server.py` is a thin router: it receives MCP tool calls over stdio and dispatches to `ccai_mcp/tools.py` and `ccai_mcp/metrics.py`.

**Auth:** No authentication logic exists in Python code. In production, Azure App Service Easy Auth (Entra) intercepts all requests before they reach the server; the Python app sees authenticated traffic only. `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` env vars are used by Terraform, not by the app.

**S3 ingestion** is stubbed — `pipeline.py` raises `NotImplementedError` for the `"s3"` source path.

## Key design constraints

- **CSAT is not semantically searchable.** `query_csat()` reads the Postgres `f_csat` fact (SQL-backed), but CSAT is not embedded in pgvector, so it cannot be combined with semantic transcript search.
- **Chat provider swaps are controlled by `LLM_PROVIDER` env var** (`"openai"`, default, or `"ollama"`, `"anthropic"`, `"fireworks"`, `"vllm"`). `get_llm(provider: str | None = None)` in `rag/pipeline.py` takes an explicit provider override so evals can loop providers in one process. The `"openai"` branch is any OpenAI-compatible chat endpoint — its `LLM_BASE_URL` (default `https://openrouter.ai/api/v1`) and `LLM_MODEL` (default low-cost `z-ai/glm-5.3-flash`) are env-configurable; the key is read from `OPENAI_API_KEY`. Fireworks and vLLM go through the OpenAI-compatible client (`FIREWORKS_API_KEY`/`FIREWORKS_MODEL`, `VLLM_BASE_URL`/`VLLM_MODEL`); Anthropic uses `ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL`.
- **Embeddings are decoupled via `EMBEDDING_PROVIDER`** (falling back to `LLM_PROVIDER`, so an unset value keeps the old single-provider behavior). OpenRouter currently serves **no embedding models**, so an OpenRouter chat setup must pair with `EMBEDDING_PROVIDER=ollama` (local, free `nomic-embed-text`) or an OpenAI-compatible `EMBEDDING_BASE_URL`/`EMBEDDING_MODEL`. To add other providers (Anthropic, Vertex AI, etc.), only `rag/embeddings.py` and the LLM init in `rag/pipeline.py` need to change.
- **MCP server is stdio-only** (not HTTP). Claude Desktop and other clients connect via process I/O. The production Azure deployment adds HTTP transport via App Service.

## Environment variables

```
RETRIEVER_BACKEND=pgvector  # retrieval backend behind get_retriever(): "pgvector" (default) | "lancedb" (needs `uv sync --group lance`)
LANCE_URI=data/lance        # LanceDB location: local dir (gitignored) or az:// / s3:// URI; embedded, no server
LLM_PROVIDER=openai      # chat provider: "openai" (OpenAI-compatible) | "ollama" | "anthropic" | "fireworks" | "vllm"
OPENAI_API_KEY=          # required for the OpenAI-compatible chat branch (e.g. OpenRouter)
LLM_BASE_URL=https://openrouter.ai/api/v1   # OpenAI-compatible chat endpoint
LLM_MODEL=z-ai/glm-5.3-flash              # low-cost OpenRouter chat model (default)
ANTHROPIC_API_KEY=       # required when LLM_PROVIDER=anthropic
ANTHROPIC_MODEL=claude-sonnet-4-5
FIREWORKS_API_KEY=       # required when LLM_PROVIDER=fireworks
FIREWORKS_MODEL=accounts/fireworks/models/llama4-scout-instruct-basic
VLLM_BASE_URL=http://localhost:8000/v1   # self-hosted vLLM server (OpenAI-compatible)
VLLM_MODEL=meta-llama/Llama-3.2-3B-Instruct
EMBEDDING_PROVIDER=ollama  # "openai" (OpenAI-compatible) or "ollama"; falls back to LLM_PROVIDER
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_BASE_URL=https://api.openai.com/v1
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/contactcenter
S3_BUCKET_NAME=
S3_PREFIX=call-data/
AZURE_TENANT_ID=        # used by Terraform, not the app
AZURE_CLIENT_ID=        # used by Terraform, not the app
COMPOSE_PROFILES=       # Compose profiles a bare `docker compose up -d` also starts: "ui" (open-webui, structurizr), "app", "gpu"
DEMO_LLM_PROVIDER=ollama  # `make demo` chat provider (shell/make variable, not read from .env); embeddings stay on Ollama
HF_TOKEN=               # Hugging Face token for gated models under the Compose `gpu` profile (vllm)
```

Low-cost demo shape (chat on OpenRouter, free local embeddings — no secrets, placeholders only):

```
LLM_PROVIDER=openai
OPENAI_API_KEY=<your-scoped-openrouter-key>
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=z-ai/glm-5.3-flash
EMBEDDING_PROVIDER=ollama
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
```

See `.env.example` for the full list including AWS credentials and MCP server host/port.

## Blog series

The "Anchoring AI" series lives in `blogs/`. Posts use this codebase as a live playground.

```bash
# Build all HTML from Markdown (requires pandoc >= 3.3, the release that renamed
# --highlight-style to --syntax-highlighting; older pandoc aborts build.sh)
bash blogs/build.sh

# The committed blogs/**/index.html files are pandoc output, not hand-written.
# Always re-run build.sh after editing a post's index.md so .md and .html agree.

# Build a single post
bash blogs/build.sh 05
```

Posts 1–4 are fully drafted. Posts 5–12 are structured placeholders/drafts.
Deployed via GitHub Actions → GitHub Pages at https://hendocode.github.io/contact-center-ai/

| Post | Slug | Title | Status |
|---|---|---|---|
| 01 | `01-the-blueprint` | The Blueprint | Published |
| 02 | `02-from-text-to-vectors` | From Text to Vectors | Published |
| 03 | `03-the-interface-layer` | The Interface Layer | Published |
| 04 | `04-run-anywhere` | Run Anywhere | Published |
| 05 | `05-real-data-in` | Real Data In | Placeholder |
| 06 | `06-built-to-last` | Built to Last | Placeholder |
| 07 | `07-the-developers-toolkit` | The Developer's Toolkit | Placeholder |
| 08 | `08-what-should-we-measure` | What Should We Measure? | Placeholder |
| 09 | `09-wiring-it-up` | Wiring It Up | Placeholder |
| 10 | `10-trust-but-verify` | Trust, but Verify | Structured draft |
| 11 | `11-whats-next` | What's Next | Structured draft |
| 12 | `12-agent-harness` | The Agent Harness (Claude Code, Pi, firstmate) | Placeholder |

## What's built vs. what's next

**Built:** synthetic data generator, full RAG ingest/retrieve/respond pipeline, pgvector + OpenAI integration, Ollama as local provider alternative (no API key), 5-tool MCP server (RAG/CSAT + semantic-layer metric tools), Terraform for Azure (App Service + PostgreSQL Flexible Server), Docker Compose for local dev.

**Not yet implemented:** S3 ingestion, CSAT → vector store (currently SQL-backed via `f_csat`, not embedded), observability, end-to-end deployment of the CI workflow.
