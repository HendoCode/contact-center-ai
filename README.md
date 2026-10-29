# contact-center-ai

A RAG pipeline and MCP server that gives contact center supervisors natural language access to call recordings, transcripts, and CSAT survey data.

Built to be **provider-agnostic** (swap OpenAI for Anthropic or Gemini via LangChain) and **reusable** — the MCP server is designed as a shared enterprise tool, not tied to a single team or use case.

## What it does

- Ingests call transcripts into a vector store (pgvector, or LanceDB as a peer) and CSAT survey results into Postgres
- Exposes a RAG-powered query interface: ask questions like *"what were the most common complaints last week?"* or *"show me calls where the member mentioned fraud"*
- Serves these capabilities as MCP tools so any GenAI client (Claude, OpenAI, Gemini) can consume them without rebuilding the data layer

## Architecture

[docs/MAP.md](docs/MAP.md) maps everything the repo carries, with diagrams and a capability
table; the diagrams' source is `docs/architecture/workspace.dsl`.

```
Synthetic transcripts / CSAT / member ledger   (S3 source: stubbed)
         │
         ▼
  Ingestion pipeline (LangChain; Ollama or OpenAI-compatible embeddings)
         │
         ▼
   pgvector (PostgreSQL) or LanceDB  ·  dbt marts + MetricFlow metrics
         │
         ▼
   MCP Server (Python)
   ├── search_transcripts(query)
   ├── get_call_summary(call_id)
   ├── query_csat(score range, category, dates)
   ├── query_metric(metrics)
   ├── ask_the_analyst(question)
   └── explain_change(metric, filters, change_point)
         │
         ▼
  LangGraph agent, or any MCP-compatible client
  (Claude Desktop, Cursor, etc.)
```

**Auth:** Microsoft Entra via Easy Auth on Azure Container Apps — governs which teams and identities can access the server in production.

## Stack

- **Python 3.12** (managed by uv)
- **LangChain** — orchestration and LLM abstraction
- **pgvector** — vector similarity search inside PostgreSQL
- **OpenAI** — embeddings and completions (default; swappable)
- **Ollama** — local embeddings and completions, no API key required (optional)
- **Python MCP SDK** — MCP server implementation
- **Terraform** — Azure infrastructure (Container Apps + PostgreSQL Flexible Server + ADLS)
- **Microsoft Entra** — authentication (production)

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

# Start PostgreSQL with pgvector (and Ollama, if using it)
docker compose up -d

# Create virtualenv and install dependencies
uv venv --python 3.12
uv sync --extra dev

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

Once the MCP server is running locally, add it to your Claude Desktop config:

```json
{
  "mcpServers": {
    "contact-center": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/contact-center-ai", "python", "-m", "ccai_mcp.server"]
    }
  }
}
```

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

OpenRouter currently serves **no embedding models**, so an OpenRouter chat
setup pairs with the free local Ollama embeddings path:

```bash
EMBEDDING_PROVIDER=ollama
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
```

### OpenAI (default)

```bash
LLM_PROVIDER=openai   # or omit — openai is the default
OPENAI_API_KEY=sk-...
EMBEDDING_MODEL=text-embedding-3-small      # embedding model id (OpenAI-compatible)
EMBEDDING_BASE_URL=https://api.openai.com/v1  # embedding endpoint (default)
```

The pipeline is also designed so you can add other LangChain-supported providers (Anthropic, Google Vertex AI, etc.) by extending `rag/embeddings.py` and `rag/pipeline.py`.

## Production deployment

See `infra/azure/` for the Azure infrastructure (Container Apps, PostgreSQL Flexible Server, ADLS)
and its README for the bootstrap, plan and apply steps. Entra auth is configured as Easy Auth on
the Container App — see `infra/azure/modules/apps/main.tf`. `infra/terraform/` is the deprecated
single-file App Service version.

## Notes

This repo is a working sandbox exploring what AI can do for credit union contact centers, part of a larger effort to show credit unions a range of possibilities. It uses **synthetic data only**: no real member data, call recordings, or CSAT results are included anywhere in this repository.
