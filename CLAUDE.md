# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A governed semantic layer for a synthetic credit-union contact center, served to AI assistants through one
MCP server. dbt + MetricFlow declare 47 metrics over a star schema; the server exposes seven read-only tools,
three metric tools (`query_metric`, `ask_the_analyst`, `explain_change`) and four evidence tools over call
transcripts and CSAT (`search_transcripts`, `get_call_summary`, `get_transcript`, `query_csat`).
README "What it does" is the canonical description, and `docs/ROADMAP.md` is the only list of future work.

A cleanroom portfolio mirror of active consulting work for a financial institution (credit union, confidential).
The real engagement involves call center supervisors querying member call transcripts and CSAT
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
docker compose up -d                          # Start Postgres, Ollama, Open WebUI and Structurizr
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

# Terraform (Azure infra; infra/azure/README.md has the full steps)
cd infra/azure/bootstrap && terraform init && terraform apply    # once
cd ../envs/dev && terraform init -backend-config=backend.hcl
terraform plan
terraform apply
```

Ruff linting and the shared task targets live in the root `Makefile` and `pyproject.toml`
(see `make lint`, `make test`, and `make check-public`).

## Architecture

```
generate_data.py → transcripts.json + csat.json
                        ↓
pipeline.py --ingest → embed (OpenAI default / Ollama local) → LanceDB (default store; RETRIEVER_BACKEND=pgvector selects pgvector in PostgreSQL, the rollback and benchmark peer)
                        ↓
ccai_mcp/server.py (stdio; HTTP opt-in) → 7 tools exposed to MCP clients
```

**Data flow across files:**
- `data/synthetic/generate_data.py` outputs `transcripts.json` (1,540 calls with full_text: 1,250 baseline calls plus the calls for four dated events), `csat.json` (survey scores 1–5), and the OLTP JSON files loaded by `olap/seed.py`.
- `rag/embeddings.py` owns the shared `get_embeddings()`; `retrieval/` (`get_retriever()`, `Hit`, `Retriever`) is the only entry point for search/ingest. `get_vector_store()`, `CONNECTION_STRING` and `COLLECTION_NAME` live in `retrieval/pgvector_backend.py`; `retrieval` imports only `get_embeddings()` from `rag`, and `--reset` also calls `get_vector_store()`. `get_llm()`, `with_output_cap()` and `reasoning_effort()` live in `models/providers.py`; `rag/pipeline.py` re-exports them, so `from rag.pipeline import get_llm` still works. A new embedding provider goes in `rag/embeddings.py`.
- `rag/pipeline.py` owns ingestion (`--ingest`) and querying (`rag_query()`). Ingestion upserts by `call_id`, so re-running `--ingest` is idempotent (rows ingested before this change have UUID ids and need `--reset` first).
- `ccai_mcp/tools.py` implements the evidence tools (`search_transcripts`, `get_call_summary`, `get_transcript`, `query_csat`). `search_transcripts` goes through `rag_query()` and `get_call_summary` uses `get_retriever().get_by_id()`; `query_csat` reads the Postgres `f_csat` fact (falling back to the `csat_survey` source), not `csat.json`.
- `ccai_mcp/metrics.py` implements the semantic-layer tools (`query_metric`, `ask_the_analyst`), which shell out to the committed `mf` (MetricFlow) CLI over the `metrics.yml` catalog.
- `ccai_mcp/server.py` holds the seven tool schemas and the dispatch; the transport (stdio or HTTP) is chosen at start. It receives MCP tool calls and dispatches to `ccai_mcp/tools.py`, `ccai_mcp/metrics.py` and `ccai_mcp/change.py` (the `explain_change` tool: a metric before and after a change, plus the calls in between).

**Auth:** Production identity is designed as infrastructure: Easy Auth (Entra) on Azure Container Apps, which intercepts requests before they reach the server (Terraform validated in CI; not deployed). The HTTP transport (`ccai_mcp/http_transport.py`) has demo-grade guards of its own: a static bearer token (`MCP_AUTH_TOKEN`), or Cloudflare Access JWT verification. `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` env vars are used by Terraform, not by the app.

**S3 ingestion** is stubbed — `pipeline.py` raises `NotImplementedError` for the `"s3"` source path.

## Key design constraints

- **CSAT is searchable only on the LanceDB retriever.** `query_csat()` reads the Postgres `f_csat` fact (SQL-backed). With the default `RETRIEVER_BACKEND=lancedb`, `--ingest` also copies each call's `csat_survey` score onto the Lance table as typed columns (`csat_score`, `surveyed_at`; no re-embedding), so `search_transcripts` takes `min_csat` / `max_csat` and prefilters to those calls. pgvector holds no scores and refuses the filter. CSAT text itself is still not embedded.
- **Chat provider swaps are controlled by `LLM_PROVIDER` env var** (`"openai"`, default, or `"ollama"`, `"anthropic"`, `"fireworks"`, `"vllm"`). `get_llm(provider: str | None = None)` in `models/providers.py` (re-exported from `rag/pipeline.py`) takes an explicit provider override so evals can loop providers in one process. The `"openai"` branch is any OpenAI-compatible chat endpoint — its `LLM_BASE_URL` (default `https://openrouter.ai/api/v1`) and `LLM_MODEL` (default low-cost `z-ai/glm-5.3-flash`) are env-configurable; the key is read from `OPENAI_API_KEY`. Fireworks and vLLM go through the OpenAI-compatible client (`FIREWORKS_API_KEY`/`FIREWORKS_MODEL`, `VLLM_BASE_URL`/`VLLM_MODEL`); Anthropic uses `ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL`.
- **Embeddings are decoupled via `EMBEDDING_PROVIDER`** (falling back to `LLM_PROVIDER`, so an unset value keeps the old single-provider behavior). OpenRouter currently serves **no embedding models**, so an OpenRouter chat setup must pair with `EMBEDDING_PROVIDER=ollama` (local, free `nomic-embed-text`) or an OpenAI-compatible `EMBEDDING_BASE_URL`/`EMBEDDING_MODEL`. A new embedding provider goes in `rag/embeddings.py`, and a new chat provider in `get_llm()` in `models/providers.py`.
- **MCP server is stdio by default**; streamable HTTP is opt-in (`MCP_TRANSPORT=http` or `--http`, `ccai_mcp/http_transport.py`) for clients on other machines, bound to loopback unless `MCP_AUTH_TOKEN` is set. Claude Desktop and other local clients can still connect via process I/O. The HTTP mode is designed to run on Azure Container Apps behind Easy Auth (Entra); the Terraform is validated in CI and has not been deployed.

- **The app image build is cached; don't reach for `--no-cache`.** uv and apt use BuildKit cache mounts, and `--no-cache` bypasses them, so it re-downloads everything. Optional `GHCR_MIRROR` (build arg) and `APT_MIRROR` (BuildKit secret, so it never changes cache keys and hosts with and without it share the registry build cache) route the uv image and apt through local mirrors; values come from `.env`, blank = public (see the Dockerfile header). Keep any new mirror setting cache-neutral the same way. There's deliberately no PyPI-mirror arg: `uv sync --frozen` downloads the exact URLs in `uv.lock` whatever index is set.

## Environment variables

```
MCP_TRANSPORT=stdio      # MCP server transport: "stdio" (default) | "http" (streamable HTTP, same as `--http`)
MCP_SERVER_HOST=127.0.0.1  # HTTP bind address; a non-loopback host refuses to start without MCP_AUTH_TOKEN
MCP_SERVER_PORT=8000     # HTTP port
MCP_AUTH_TOKEN=          # static bearer token for the HTTP transport (demo-grade; `openssl rand -hex 32`)
MCP_AUTH_UPSTREAM=false  # "true" lets a non-loopback bind skip the token when Azure Easy Auth fronts it and Container Apps `CONTAINER_APP_*` vars are present
MCP_STORE_CHECK=on       # "off" skips the /healthz check that the configured transcript store is non-empty and readable
MCP_PUBLISH_HOST=127.0.0.1  # Compose `mcp-server` only: host interface the port is published on
MCP_SERVER_URL=http://127.0.0.1:8000/mcp   # client side: `python -m ccai_mcp.http_smoke` reads it
RETRIEVER_BACKEND=lancedb  # retrieval backend behind get_retriever(): "lancedb" (default; needs `uv sync --group lance`) | "pgvector" (rollback and benchmark peer); Compose hard-codes lancedb on a `lance-data` volume
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
DEMO_LLM_PROVIDER=ollama  # `make demo` chat provider (shell/make variable, not read from .env); embeddings stay on Ollama
HF_TOKEN=               # Hugging Face token for gated models under the Compose `gpu` profile (vllm)
SNOWFLAKE_ACCOUNT=      # dbt `snowflake` target (olap/dbt/profiles.yml; `uv sync --group dbt --group snowflake`); key-pair auth, no password
SNOWFLAKE_USER=         # the dbt user that holds the public key
SNOWFLAKE_PRIVATE_KEY_PATH=   # path to the PKCS#8 PEM private key file
SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=   # optional, only for an encrypted key
SNOWFLAKE_ROLE=
SNOWFLAKE_WAREHOUSE=
SNOWFLAKE_DATABASE=
SNOWFLAKE_SCHEMA=marts
SNOWFLAKE_RAW_SCHEMA=raw   # where olap/dbt/loaders puts the raw OLTP tables; also the dbt source schema on the snowflake target
DATABRICKS_HOST=        # dbt `databricks` target (`uv sync --group dbt --group databricks`)
DATABRICKS_HTTP_PATH=   # SQL warehouse HTTP path
DATABRICKS_TOKEN=
DATABRICKS_CATALOG=
DATABRICKS_SCHEMA=marts
DATABRICKS_RAW_SCHEMA=raw  # same, in DATABRICKS_CATALOG, for the databricks target
LANGSMITH_TRACING=false  # "true" turns LangSmith tracing on; forced off in tests and offline `make evals`
LANGSMITH_API_KEY=       # LangSmith key: traces when tracing is on; required by `make evals-live`
LANGSMITH_PROJECT=contact-center-ai  # LangSmith project for traces
EVAL_JUDGE_PROVIDER=anthropic  # `make evals-live` judge: "anthropic" | "openai" (OpenAI-compatible)
EVAL_JUDGE_MODEL=claude-opus-5-5  # judge model; must differ from the agent's model
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

**Built:** the `docs/MAP.md` capability table has the status of each part. In short: the synthetic data generator and OLTP seed; the semantic layer (dbt + MetricFlow, 47 metrics, Postgres/Snowflake/Databricks targets, parity runs in `results/semantics/`); the 7-tool MCP server (four evidence tools and three metric tools) over stdio or streamable HTTP; transcript ingest and retrieval on LanceDB (default, with CSAT score filters) and pgvector as the rollback and benchmark peer; five chat providers; the LangGraph agent with LangSmith tracing and evals; Docker Compose for local dev; Terraform for Azure (`infra/azure`: Container Apps + PostgreSQL Flexible Server + ADLS), validated in CI and not deployed.

**Not yet built:** listed only in `docs/ROADMAP.md`.
