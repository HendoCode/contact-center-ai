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
python -m mcp.server

# Tests (framework installed, no tests written yet)
pytest
pytest tests/path/to/test_file.py::test_name  # single test

# Terraform (Azure infra)
cd infra/terraform && terraform init
terraform plan -var-file=prod.tfvars
terraform apply
```

No linting or formatting is configured yet (no Makefile, ruff, black, or pre-commit hooks).

## Architecture

```
generate_data.py → transcripts.json + csat.json
                        ↓
pipeline.py --ingest → embed (OpenAI default / Ollama local) → pgvector (PostgreSQL)
                        ↓
mcp/server.py (stdio) → 3 tools exposed to MCP clients
```

**Data flow across files:**
- `data/synthetic/generate_data.py` outputs two JSON files: `transcripts.json` (150 calls with full_text) and `csat.json` (survey scores 1–5). These are the only data sources.
- `rag/embeddings.py` owns pgvector setup — `get_embeddings()` and `get_vector_store()` are the only entry points for the vector store. To swap from OpenAI embeddings to Anthropic/Gemini, only this file needs to change.
- `rag/pipeline.py` owns ingestion (`--ingest`) and querying (`rag_query()`). Ingestion has no deduplication — running `--ingest` twice will create duplicate documents.
- `mcp/tools.py` implements the 3 MCP tools. `search_transcripts` and `get_call_summary` both go through `rag_query()`. `query_csat` bypasses the vector store entirely — it loads `csat.json` directly and filters in-memory.
- `mcp/server.py` is a thin router: it receives MCP tool calls over stdio and dispatches to `mcp/tools.py`.

**Auth:** No authentication logic exists in Python code. In production, Azure App Service Easy Auth (Entra) intercepts all requests before they reach the server; the Python app sees authenticated traffic only. `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` env vars are used by Terraform, not by the app.

**S3 ingestion** is stubbed — `pipeline.py` raises `NotImplementedError` for the `"s3"` source path.

## Key design constraints

- **CSAT is not semantically searchable.** `csat.json` is loaded and filtered in-memory in `query_csat()`. It is not in pgvector. If query volume or dataset size grows, this needs a SQL-backed approach.
- **Provider swaps are controlled by `LLM_PROVIDER` env var.** Set `LLM_PROVIDER=ollama` to use Ollama (via `langchain-ollama`) for both embeddings and completions — no code changes needed. Set `LLM_PROVIDER=openai` (or omit) for OpenAI. To add other providers (Anthropic, Vertex AI, etc.), only `rag/embeddings.py` and the LLM init in `rag/pipeline.py` need to change.
- **MCP server is stdio-only** (not HTTP). Claude Desktop and other clients connect via process I/O. The production Azure deployment adds HTTP transport via App Service.

## Environment variables

```
LLM_PROVIDER=openai          # "openai" (default) or "ollama"
OPENAI_API_KEY=              # required when LLM_PROVIDER=openai
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/contactcenter
S3_BUCKET_NAME=
S3_PREFIX=call-data/
AZURE_TENANT_ID=        # used by Terraform, not the app
AZURE_CLIENT_ID=        # used by Terraform, not the app
```

See `.env.example` for the full list including AWS credentials and MCP server host/port.

## Blog series

An eleven-post "Anchoring AI" series lives in `blogs/`. Posts use this codebase as a live playground.

```bash
# Build all HTML from Markdown (requires pandoc: brew install pandoc)
bash blogs/build.sh

# Build a single post
bash blogs/build.sh 05
```

Posts 1–4 are fully drafted. Posts 5–11 are structured placeholders/drafts.
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

**Known bugs documented in the series (not yet fixed in code):**
- `mcp/tools.py` `get_call_summary()`: retrieved document is never passed to the LLM — a second `rag_query()` call re-retrieves independently, so the specific call may not be summarized
- `mcp/tools.py` `query_csat()`: `category` parameter is accepted in the schema and function signature but the filter is not implemented — the parameter is silently ignored

## What's built vs. what's next

**Built:** synthetic data generator, full RAG ingest/retrieve/respond pipeline, pgvector + OpenAI integration, Ollama as local provider alternative (no API key), 3-tool MCP server, Terraform for Azure (App Service + PostgreSQL Flexible Server), Docker Compose for local dev.

**Not yet implemented:** S3 ingestion, CSAT → vector store (currently JSON-only), test suite, linting/formatting, observability, CI/CD.
