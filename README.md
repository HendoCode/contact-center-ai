# contact-center-ai

`contact-center-ai` (`ccai` in package and module names) is a governed semantic layer for the
contact center of Meridian Valley Credit Union, a fictional credit union, served to AI assistants
through one MCP server. It runs on synthetic data only.

## What it does

dbt and MetricFlow declare 47 metrics over a star schema. The schema is built from an OLTP ledger,
call records and CSAT surveys. Each ambiguous business word ("rate", "balance", "fee", "LCV") maps to
named metrics filtered by line of business, so an assistant asks which one you mean instead of
returning one blended number.

One MCP server exposes the layer as seven read-only tools:

- **Metric tools:** `query_metric`, `ask_the_analyst`, and `explain_change`, which explains why a
  metric moved around a logged change and cites the calls behind it.
- **Evidence tools** over the call transcripts and CSAT: `search_transcripts`, `get_call_summary`,
  `get_transcript`, `query_csat`.

Claude, Gemini, Cursor, Open WebUI and a LangGraph agent all use the same server. Numbers come from
the metric layer, and the words come from the calls.

The quickest look is the change-analysis demo. `make demo-change-replay` prints the recorded run
with no model, database or network. `make demo-change` runs it live after `make up dev-data`.

Future work is listed in one place: [docs/ROADMAP.md](docs/ROADMAP.md).

## Architecture

[docs/MAP.md](docs/MAP.md) maps everything the repo carries, with diagrams and a capability
table; the diagrams' source is `docs/architecture/workspace.dsl`.

```
Synthetic transcripts / CSAT / member ledger   (S3 source: stubbed)
         │
         ▼
  OLTP schema + seed ─► dbt star schema + 47 MetricFlow metrics
  Transcript ingest (Ollama or OpenAI-compatible embeddings) ─► LanceDB (default) or pgvector
         │
         ▼
   MCP Server (Python, stdio or streamable HTTP)
   ├── query_metric(metrics)
   ├── ask_the_analyst(question)
   ├── explain_change(metric, filters, change_point)
   ├── search_transcripts(query)
   ├── get_call_summary(call_id)
   ├── get_transcript(call_id)
   └── query_csat(score range, category, dates)
         │
         ▼
  LangGraph agent, or any MCP-compatible client
  (Claude, Gemini, Cursor, Open WebUI)
```

### Where the transcripts live

LanceDB is the default transcript store. pgvector stays filled as the rollback and benchmark
peer (`RETRIEVER_BACKEND=pgvector`). CSAT-filtered, full-text and hybrid search and versioned
rollback need LanceDB.

| Run mode | Transcript store | Notes |
|---|---|---|
| Host: `make dev-data`, `python -m ccai_mcp.server`, Claude Desktop | LanceDB in `data/lance` | needs `uv sync --group lance` |
| Compose `app` profile: `make demo`, `mcp-server`, `jupyter` | LanceDB on the `lance-data` volume | pgvector is ingested too, for rollback; a re-seed with unchanged transcripts and model embeds nothing (vectors persist in the Lance table) |
| Azure `mcp-server` Container App | LanceDB on ADLS (`az://` URI) | Terraform validated in CI; not deployed |
| Benchmark or rollback | pgvector | `RETRIEVER_BACKEND=pgvector` |

**Auth:** the HTTP transport has demo-grade guards: a static bearer token (`MCP_AUTH_TOKEN`) or
Cloudflare Access JWT verification. On Azure the design puts the server on Container Apps behind
Microsoft Entra Easy Auth; that infrastructure is validated in CI and has not been deployed.

## Stack

- **Semantic layer:** dbt Core 1.12 and MetricFlow 0.15 on Postgres, with Snowflake and
  Databricks targets; an Apache Ossie export of the metric definitions
- **Transcript stores:** LanceDB 0.39 (default; DuckDB reads it for ad hoc SQL) and pgvector on
  PostgreSQL 16
- **Models:** LangChain; chat through an OpenAI-compatible client (OpenRouter by default), Ollama,
  Anthropic, Fireworks or vLLM; embeddings through Ollama or an OpenAI-compatible endpoint
- **MCP:** the Python MCP SDK, over stdio or streamable HTTP, optionally behind Cloudflare Tunnel
  and Access
- **Agent and evals:** LangGraph, LangSmith tracing and live evals, an offline eval gate in CI
- **Local runtime:** Python 3.12 managed by uv; Docker Compose for Postgres, Ollama, the demo
  stack, Open WebUI, JupyterLab and Structurizr
- **Infrastructure:** Terraform for Azure (Container Apps, PostgreSQL Flexible Server, ADLS,
  Entra Easy Auth) and a Snowflake root

**Defaults:** chat goes through the OpenAI-compatible client to OpenRouter (`z-ai/glm-5.3-flash`);
embeddings use Ollama `nomic-embed-text`; `make demo` runs both on Ollama.

## Quick start

For a guided walk-through of every layer, with what runs offline and a code map, see [docs/TOUR.md](docs/TOUR.md); for the map of what the repo carries and its status, see [docs/MAP.md](docs/MAP.md).

### Prerequisites
- Docker and Docker Compose
- [uv](https://docs.astral.sh/uv/) (manages Python 3.12 automatically)
- OpenAI API key — or use Ollama locally (see [Swapping LLM providers](#swapping-llm-providers))

### Run locally

```bash
# Clone the repo
git clone https://github.com/hendocode/contact-center-ai
cd contact-center-ai

# Copy and configure environment
cp .env.example .env
# Edit .env — set OPENAI_API_KEY, or set LLM_PROVIDER=ollama

# Start Postgres, Ollama, Open WebUI and Structurizr
docker compose up -d

# Create virtualenv and install dependencies
uv venv --python 3.12
uv sync --extra dev --group dbt --group lance

# Build all local data: synthetic JSON, OLTP schema and load, vector-store ingest,
# and the dbt marts (idempotent; one line per step, stops with the fix on failure)
make dev-data

# Start the MCP server
python -m ccai_mcp.server
```

### Run the demo stack (Docker only, no API key)

```bash
cp .env.example .env
make demo
```

`make demo` runs the whole stack in Docker Compose on local models (Ollama): it pulls
`llama3.2` and `nomic-embed-text`, seeds Postgres, embeds the transcripts, builds the dbt
star schema, starts the LangGraph dev server (`localhost:2024`, a dev server rather than
a production one), and runs five scripted questions through the agent: one per route,
one through `ask_the_analyst`, and one that interrupts to ask which "rate" you mean and
resumes. The first run downloads a couple of GB of models and embeds 1,540 transcripts on
CPU. `make down` stops everything.

Compose profiles: a bare `docker compose up -d` is unchanged (`db`, `ollama`, Open WebUI
on `localhost:9090`, Structurizr on `localhost:8080`); `app` is the demo stack; `gpu`
declares a vLLM server.

`make notebooks` serves JupyterLab on `127.0.0.1:8888` with the change-analysis walkthrough
(`notebooks/change-analysis.ipynb`) on the same stack; the login token is `JUPYTER_TOKEN`
(`ccai-demo` when unset). Notebooks are committed with their outputs cleared.

## Connecting a client

Claude Desktop starts the server itself over stdio. Add this to `claude_desktop_config.json`,
replacing `/path/to/contact-center-ai`. The `dbt` group installs the `mf` CLI that the metric
tools call, and the `lance` group installs the default transcript store. Run `make up dev-data`
first.

```json
{
  "mcpServers": {
    "contact-center": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/path/to/contact-center-ai",
        "--group",
        "dbt",
        "--group",
        "lance",
        "python",
        "-m",
        "ccai_mcp.server"
      ],
      "env": {
        "DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/contactcenter",
        "LLM_PROVIDER": "ollama",
        "EMBEDDING_PROVIDER": "ollama"
      }
    }
  }
}
```

[ccai_mcp/README.md](ccai_mcp/README.md) covers the other clients (Claude and Gemini custom
connectors, Cursor, Claude Code) and serving the tools over HTTP.

## Swapping LLM providers

Set `LLM_PROVIDER` in your `.env` to switch providers — no code changes needed.

### Ollama (local, no API key required)

Ollama runs as a Docker Compose service — `docker compose up -d` starts it automatically.

```bash
# Pull models into the running Ollama container
docker compose exec ollama ollama pull llama3.2
docker compose exec ollama ollama pull nomic-embed-text

# In .env:
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
OLLAMA_EMBEDDING_MODEL=nomic-embed-text

# Run the pipeline as normal
python -m rag.pipeline --ingest
python -m rag.pipeline --query "fraud disputes from last week"
```

### OpenRouter (low-cost, OpenAI-compatible — recommended default)

The `openai` provider targets any OpenAI-compatible chat endpoint, and its
defaults route to a low-cost model on OpenRouter — change `LLM_MODEL`/
`LLM_BASE_URL` to point elsewhere without touching code:

```bash
LLM_PROVIDER=openai                    # or omit — openai is the default
OPENAI_API_KEY=sk-or-...               # a scoped OpenRouter key
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=z-ai/glm-5.3-flash           # low-cost; e.g. deepseek/deepseek-v4-flash-latest also works
```

Reasoning models spend output tokens thinking, and those count against
`LLM_MAX_TOKENS`. The openai branch therefore sends OpenRouter's
`reasoning: {"effort": ...}` field from `LLM_REASONING_EFFORT` (default `low`;
`none` leaves the request unchanged), and `explain_change`'s structured calls
use their own cap, `LLM_STRUCTURED_MAX_TOKENS` (default `8192`). If that cap is
still hit, the result's limits say so.

OpenRouter currently serves **no embedding models**, so an OpenRouter chat
setup pairs with the free local Ollama embeddings path:

```bash
EMBEDDING_PROVIDER=ollama
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
```

### OpenAI direct

```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=<an OpenAI chat model id>
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small      # embedding model id (OpenAI-compatible)
EMBEDDING_BASE_URL=https://api.openai.com/v1  # embedding endpoint (default)
```

`LLM_PROVIDER` also accepts `anthropic`, `fireworks` and `vllm`; `.env.example` lists their
variables. A new chat provider goes in `get_llm()` in `models/providers.py`, and a new embedding
provider in `rag/embeddings.py`.

## Azure deployment

`infra/azure/` holds the Terraform for Azure (Container Apps, PostgreSQL Flexible Server, ADLS)
and its README has the bootstrap, plan and apply steps. Entra auth is configured as Easy Auth on
the Container App (`infra/azure/modules/apps/main.tf`). CI validates the Terraform; it has not
been applied, and no workflow builds the server image yet.

## Notes

This repo is a working sandbox exploring what AI can do for credit union contact centers, part of a larger effort to show credit unions a range of possibilities. It uses **synthetic data only**: no real member data, call recordings, or CSAT results are included anywhere in this repository.
