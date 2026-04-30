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
docker compose up -d                          # Start pgvector (PostgreSQL)
pip install -r requirements.txt

# Data pipeline
python data/synthetic/generate_data.py        # Generate transcripts.json + csat.json
python rag/pipeline.py --ingest               # Embed and store in pgvector

# Test a RAG query end-to-end
python rag/pipeline.py --query "fraud disputes from last week"

# Start MCP server (stdio, for client connections)
python mcp/server.py

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
pipeline.py --ingest → embed (OpenAI) → pgvector (PostgreSQL)
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
- **Provider swaps are isolated to `rag/embeddings.py`.** LangChain abstractions (`ChatOpenAI`, `OpenAIEmbeddings`, `PGVector`) are used everywhere; swapping providers only requires changing `get_embeddings()` and the `ChatOpenAI` call in `pipeline.py`.
- **MCP server is stdio-only** (not HTTP). Claude Desktop and other clients connect via process I/O. The production Azure deployment adds HTTP transport via App Service.

## Environment variables

```
OPENAI_API_KEY=
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/contactcenter
S3_BUCKET_NAME=
S3_PREFIX=call-data/
AZURE_TENANT_ID=        # used by Terraform, not the app
AZURE_CLIENT_ID=        # used by Terraform, not the app
```

See `.env.example` for the full list including AWS credentials and MCP server host/port.

## What's built vs. what's next

**Built:** synthetic data generator, full RAG ingest/retrieve/respond pipeline, pgvector + OpenAI integration, 3-tool MCP server, Terraform for Azure (App Service + PostgreSQL Flexible Server), Docker Compose for local dev.

**Not yet implemented:** S3 ingestion, CSAT → vector store (currently JSON-only), test suite, linting/formatting, observability, CI/CD.
