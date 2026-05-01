# Architecture Diagrams

Source: `workspace.dsl` (Structurizr DSL)  
Renderer: `structurizr/structurizr local` (Docker — included in `docker-compose.yml`)

## Render locally

```bash
docker compose up structurizr
# Open http://localhost:8080
```

Structurizr Lite watches `workspace.dsl` and hot-reloads on save. No restart required while editing.

## Views

| Key | Type | Description |
|-----|------|-------------|
| `SystemContext` | Level 1 | All actors and external systems |
| `Containers` | Level 2 | Six runtime/storage containers |
| `Components_MCP` | Level 3 | `mcp/server.py` handlers + `mcp/tools.py` functions |
| `Components_RAG` | Level 3 | `rag/pipeline.py` functions |
| `Components_Embeddings` | Level 3 | `rag/embeddings.py` — provider swap point |
| `Dynamic_Ingest` | Dynamic | Data ingestion: generate → embed → pgvector |
| `Dynamic_RAGQuery` | Dynamic | RAG query: MCP call → retrieve → LLM → response |
| `Dynamic_CSATQuery` | Dynamic | CSAT query: MCP call → JSON load → in-memory filter |

## Key architectural decisions surfaced by the diagrams

**CSAT bypasses pgvector** — `Dynamic_CSATQuery` shows a two-step path (MCP Server → Data Files) with no interaction with pgvector or OpenAI. `csat.json` is loaded and filtered in-memory at query time.

**Auth is infrastructure-only** — `azureEntra` sits entirely outside the `contactCenterAI` system boundary in the System Context view. The Python application contains no authentication code; Azure App Service Easy Auth intercepts all HTTP requests.

**Provider coupling is isolated** — In `Components_Embeddings`, `get_embeddings()` and `get_vector_store()` are the only two nodes with outbound edges to OpenAI and PostgreSQL. Swapping LLM providers requires editing only `rag/embeddings.py`.

**Ingestion has no deduplication** — noted in the `Dynamic_Ingest` view description. Running `pipeline.py --ingest` twice inserts duplicate rows.

**S3 is planned, not live** — `awsS3` appears with a dashed border in the System Context view. `pipeline.py` raises `NotImplementedError` for the `source='s3'` path.
