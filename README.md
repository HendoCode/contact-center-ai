# contact-center-ai

A RAG pipeline and MCP server that gives contact center supervisors natural language access to call recordings, transcripts, and CSAT survey data.

Built to be **provider-agnostic** (swap OpenAI for Anthropic or Gemini via LangChain) and **reusable** — the MCP server is designed as a shared enterprise tool, not tied to a single team or use case.

## What it does

- Ingests call transcripts and CSAT survey results into a pgvector store
- Exposes a RAG-powered query interface: ask questions like *"what were the most common complaints last week?"* or *"show me calls where the member mentioned fraud"*
- Serves these capabilities as MCP tools so any GenAI client (Claude, OpenAI, Gemini) can consume them without rebuilding the data layer

## Architecture

```
Call recordings / transcripts / CSAT surveys
         │
         ▼
    S3 (staging)
         │
         ▼
  Ingestion pipeline
  (LangChain + OpenAI embeddings)
         │
         ▼
   pgvector (PostgreSQL)
         │
         ▼
   MCP Server (Python)
   ├── search_transcripts(query)
   ├── get_call_summary(call_id)
   └── query_csat(date_range, filters)
         │
         ▼
  Any MCP-compatible GenAI client
  (Claude Desktop, custom app, etc.)
```

**Auth:** Microsoft Entra via Azure App Service Easy Auth for MCP — governs which teams and identities can access the server in production.

## Stack

- **Python 3.12** (managed by uv)
- **LangChain** — orchestration and LLM abstraction
- **pgvector** — vector similarity search inside PostgreSQL
- **OpenAI** — embeddings and completions (default; swappable)
- **Ollama** — local embeddings and completions, no API key required (optional)
- **Python MCP SDK** — MCP server implementation
- **Terraform** — Azure infrastructure (App Service + PostgreSQL)
- **Microsoft Entra** — authentication (production)

## Quick start

### Prerequisites
- Docker and Docker Compose
- [uv](https://docs.astral.sh/uv/) (manages Python 3.12 automatically)
- OpenAI API key — or use Ollama locally (see [Swapping LLM providers](#swapping-llm-providers))

### Run locally

```bash
# Clone the repo
git clone https://github.com/yourusername/contact-center-ai
cd contact-center-ai

# Copy and configure environment
cp .env.example .env
# Edit .env — set OPENAI_API_KEY, or set LLM_PROVIDER=ollama

# Start PostgreSQL with pgvector (and Ollama, if using it)
docker compose up -d

# Create virtualenv and install dependencies
uv venv --python 3.12
uv sync --extra dev

# Generate synthetic call data
python data/synthetic/generate_data.py

# Ingest data into vector store
python -m rag.pipeline --ingest

# Start the MCP server
python -m mcp.server
```

## Connecting a client

Once the MCP server is running locally, add it to your Claude Desktop config:

```json
{
  "mcpServers": {
    "contact-center": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/contact-center-ai", "python", "-m", "mcp.server"]
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

### OpenAI (default)

```bash
LLM_PROVIDER=openai   # or omit — openai is the default
OPENAI_API_KEY=sk-...
```

The pipeline is also designed so you can add other LangChain-supported providers (Anthropic, Google Vertex AI, etc.) by extending `rag/embeddings.py` and `rag/pipeline.py`.

## Production deployment

See `infra/terraform/` for Azure App Service + PostgreSQL infrastructure.
Entra auth is configured at the App Service level — see `infra/terraform/main.tf`.

## Notes

This repo uses **synthetic data only**. The real engagement this mirrors involves a confidential financial institution client. No real member data, call recordings, or CSAT results are included anywhere in this repository.
