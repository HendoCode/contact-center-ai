---
title: "The Blueprint"
subtitle: "Designing a production RAG system from the ground up"
author: Stephen Henderson
date: "June 2026"
post_number: "01"
description: "Post 1 of Anchoring AI: architecture overview, C4 diagrams, and sequence diagrams for a RAG pipeline behind an MCP server."
prev_url: ""
prev_title: ""
next_url: "../02-from-text-to-vectors/"
next_title: "From Text to Vectors"
---

There's a question that haunts every call center manager: "What's actually going on out there?"

The data exists. Hundreds of call transcripts, recorded and transcribed every week. CSAT surveys from members who bothered to respond. Agent notes. Outcome codes. But turning that pile of raw data into an answer to "show me all fraud disputes that escalated last month" requires someone to actually dig through it — manually. Keyword searching, listening to recordings, building mental models across dozens of calls. It's slow, it misses context, and it doesn't scale.

This series is about building a system that makes that question answerable in seconds, using natural language. Not as a demo, not as a tutorial with 10 rows of fake data — but as a real, deployable system with production infrastructure, swap-friendly LLM providers, and the kind of observability that lets you trust what you've built.

---

## The Context

The system documented in this series grew out of a consulting engagement with a financial institution — let's call them Northgate Federal Credit Union, a name I've invented. They had a problem familiar to any enterprise mid-AI-adoption: multiple teams independently using Claude, OpenAI, and Gemini, no shared infrastructure, and no way to reuse the work across teams.

The call center team wanted to query call recordings and transcripts in natural language. The compliance team wanted to run ad-hoc CSAT analyses. The operations team had something different in mind. All of them were going to end up building separate pipelines to the same underlying data.

The design constraint that shaped everything: **build one thing that all of them can use.** That meant the core retrieval and data access layer needed to be a shared service, not a team-specific application. MCP — the Model Context Protocol — turned out to be the right abstraction for that.

This repo (and this series) uses fully synthetic data. No real member information, no real call recordings, no PII. The architecture decisions are real. The code is real. The numbers are fake.

---

## What We're Building

At its core, this is a **RAG pipeline** (Retrieval-Augmented Generation) surfaced through an **MCP server**. Call transcripts are embedded into a vector database. When a supervisor asks a natural language question, the system finds the most semantically relevant transcripts, constructs a context window, and lets an LLM synthesize an answer grounded in the actual call data.

The MCP server exposes three tools:

- **`search_transcripts`** — semantic search across call transcripts; returns an AI-generated answer grounded in the top-k most relevant calls
- **`get_call_summary`** — retrieve and summarize a specific call by ID
- **`query_csat`** — filter and aggregate CSAT survey data by score range and call category

Any MCP-compatible client — Claude Desktop, a custom web UI, or another team's internal tool — can call these tools without knowing anything about the underlying retrieval machinery.

---

## The Architecture

### System Context

The outermost view shows the actors and systems this solution touches.

```mermaid
C4Context
    title System Context — Anchoring AI

    Person(supervisor, "Call Center Supervisor", "Queries call transcripts and CSAT data using natural language via an MCP client")
    Person(teams, "Other Teams", "Future consumers of the shared MCP tools")

    System(ccai, "Contact Center AI", "RAG pipeline and MCP server. Exposes call center data as three tools to any MCP-compatible client.")

    System_Ext(openai, "OpenAI API", "text-embedding-3-small for vectorisation, gpt-4o-mini for answer synthesis")
    System_Ext(ollama, "Ollama (local)", "nomic-embed-text + llama3.2 — local alternative, no API key required")
    System_Ext(azure, "Azure App Service", "Production host for the MCP server")
    System_Ext(entra, "Microsoft Entra", "Intercepts requests and validates Bearer tokens — auth at the infra layer, not in Python code")
    System_Ext(s3, "AWS S3", "Planned: source of production call recordings (currently stubbed)")

    Rel(supervisor, ccai, "Queries call data", "MCP over stdio (local) / HTTPS (production)")
    Rel(teams, ccai, "Future consumers", "MCP")
    Rel(ccai, openai, "Embeddings + completions", "HTTPS")
    Rel(ccai, ollama, "Local alternative", "HTTP")
    Rel(ccai, azure, "Deployed to", "Terraform")
    Rel(entra, azure, "Validates tokens before requests reach Python", "Easy Auth middleware")
    Rel(ccai, s3, "Planned ingestion path", "HTTPS — NotImplementedError today")
```

*Figure 1 — System context. Two details are worth noting: Entra authentication is handled entirely at the infrastructure layer (the Python application contains zero auth code), and the Ollama path is a drop-in swap controlled by a single environment variable.*

---

### Containers

Zooming in, the system is six containers. Two are live data paths; one is a future-planned external source.

```mermaid
C4Container
    title Container View — Anchoring AI

    Person(supervisor, "Call Center Supervisor")

    System_Boundary(ccai, "Contact Center AI") {
        Container(mcpServer, "MCP Server", "Python / MCP SDK 1.0", "Async stdio/HTTP server. Registers tool schemas and routes incoming tool calls. Entry point: mcp/server.py.")
        Container(ragPipeline, "RAG Pipeline", "Python / LangChain 0.3", "Orchestrates ingestion, semantic retrieval, and answer synthesis. rag/pipeline.py.")
        Container(embedModule, "Embeddings Module", "Python / LangChain", "Provider abstraction layer. get_embeddings() and get_vector_store() are the only provider-coupling points. rag/embeddings.py.")
        ContainerDb(pgvector, "PostgreSQL + pgvector", "PostgreSQL 16", "Stores 1536-dim call transcript embeddings in the call_transcripts collection.")
        Container(dataFiles, "Data Files", "JSON on disk", "transcripts.json (150 synthetic calls) + csat.json (112 CSAT surveys). Generated by generate_data.py.")
    }

    System_Ext(llmProvider, "LLM Provider", "OpenAI API or Ollama — controlled by LLM_PROVIDER env var")

    Rel(supervisor, mcpServer, "Tool calls", "MCP stdio (local) / HTTPS (production)")
    Rel(mcpServer, ragPipeline, "rag_query(), retrieve()", "Python function call")
    Rel(mcpServer, dataFiles, "Reads csat.json directly", "File I/O — bypasses vector store")
    Rel(ragPipeline, embedModule, "get_vector_store()", "Python function call")
    Rel(ragPipeline, dataFiles, "Reads transcripts.json", "File I/O")
    Rel(ragPipeline, pgvector, "Store + similarity search", "SQL / pgvector extension")
    Rel(ragPipeline, llmProvider, "Answer synthesis", "HTTPS or local HTTP")
    Rel(embedModule, llmProvider, "Vectorize text", "HTTPS or local HTTP")
    Rel(embedModule, pgvector, "Read/write embeddings", "SQL via psycopg2")
```

*Figure 2 — Container view. The split between `search_transcripts`/`get_call_summary` (which go through the full RAG stack) and `query_csat` (which reads JSON directly) is a deliberate design choice — CSAT data is structured and small enough that vector search adds no value.*

---

### Components — The MCP Tool Layer

The MCP server is a single Python process. `server.py` is the async entry point; `tools.py` is where the actual work happens.

```mermaid
C4Component
    title Component View — MCP Server and Tools

    Container_Boundary(mcp, "MCP Server (mcp/server.py + mcp/tools.py)") {
        Component(listTools, "list_tools handler", "Python async", "Registers the three MCP tool definitions — name, description, inputSchema — with the SDK.")
        Component(callTool, "call_tool handler", "Python async", "Routes incoming tool calls by name to the correct function. Wraps results in TextContent.")
        Component(mainFn, "main()", "Python async", "Initialises stdio_server context and starts the MCP application loop.")

        Component(searchTool, "search_transcripts()", "Python", "Accepts a natural-language query + optional k. Calls rag_query(). Returns an AI-generated answer grounded in top-k retrieved transcripts.")
        Component(summaryTool, "get_call_summary()", "Python", "Accepts a call_id. Retrieves the matching transcript via retrieve(), then calls rag_query() to produce a concise summary.")
        Component(csatTool, "query_csat()", "Python", "Accepts optional score range and category. Loads csat.json from disk. Filters in-memory. Does not call pgvector or the LLM.")
    }

    Container(ragPipeline, "RAG Pipeline", "Python", "rag_query() and retrieve()")
    Container(dataFiles, "Data Files", "JSON", "csat.json")

    Rel(callTool, searchTool, "routes search_transcripts calls")
    Rel(callTool, summaryTool, "routes get_call_summary calls")
    Rel(callTool, csatTool, "routes query_csat calls")
    Rel(searchTool, ragPipeline, "calls rag_query(query, k)")
    Rel(summaryTool, ragPipeline, "calls retrieve() then rag_query()")
    Rel(csatTool, dataFiles, "reads csat.json — bypasses RAG entirely")
```

*Figure 3 — The tool dispatch layer. `query_csat()` is the outlier: it's the only tool that doesn't go through the vector store or the LLM. It's just a JSON file and some Python filtering logic.*

---

## The Flows

Architecture diagrams show structure. Sequence diagrams show what actually happens when a request comes in.

### Flow 1 — A Single MCP Query

When a supervisor types "show me fraud disputes from last week" into Claude Desktop, here's the full path:

```mermaid
sequenceDiagram
    actor Supervisor
    participant CD as Claude Desktop
    participant MCP as MCP Server
    participant Tools as tools.py
    participant RAG as pipeline.py
    participant Embed as embeddings.py
    participant PG as pgvector
    participant LLM as OpenAI / Ollama

    Supervisor->>CD: "Show me fraud disputes from last week"
    CD->>MCP: call_tool("search_transcripts",<br/>{query: "fraud disputes last week", k: 5})
    MCP->>Tools: search_transcripts(query, k=5)
    Tools->>RAG: rag_query(query, k=5)
    RAG->>Embed: get_vector_store()
    Embed-->>RAG: PGVector instance (connection established)
    RAG->>LLM: embed(query) → 1536-dim vector
    LLM-->>RAG: query embedding
    RAG->>PG: similarity_search(query_embedding, k=5)
    PG-->>RAG: top-5 LangChain Documents<br/>(call text + metadata)
    RAG->>LLM: ChatOpenAI.invoke(<br/>  system_prompt + retrieved context)
    LLM-->>RAG: "Based on the transcripts, 3 fraud disputes were logged..."
    RAG-->>Tools: answer string
    Tools-->>MCP: TextContent(answer)
    MCP-->>CD: tool result
    CD-->>Supervisor: displays grounded answer
```

*Figure 4 — MCP query flow. The round-trip involves two LLM calls: one to embed the query (fast, cheap), one to synthesize the answer (slower, more expensive). Both happen over the same provider — swap the `LLM_PROVIDER` env var and both switch simultaneously.*

---

### Flow 2 — Ingest and Embedding

Before any query can work, the transcripts need to be in the vector store. This is the ingestion flow:

```mermaid
sequenceDiagram
    participant Gen as generate_data.py
    participant JSON as transcripts.json
    participant CLI as pipeline.py --ingest
    participant Embed as embeddings.py
    participant LLM as OpenAI Embeddings<br/>(or Ollama nomic-embed-text)
    participant PG as pgvector

    Gen->>JSON: write 150 synthetic call records<br/>(call_id, date, category, outcome, full_text)
    CLI->>JSON: load_synthetic_data() reads file
    JSON-->>CLI: 150 raw dicts
    CLI->>CLI: transcripts_to_documents()<br/>wrap in LangChain Document objects<br/>(page_content=full_text, metadata=call fields)
    CLI->>Embed: get_vector_store()
    Embed->>LLM: get_embeddings()<br/>text-embedding-3-small (1536 dims)<br/>or nomic-embed-text (Ollama)
    Embed->>PG: initialize pgvector extension + schema<br/>(first run only)
    PG-->>Embed: ready
    CLI->>Embed: vector_store.add_documents(docs)
    loop for each document batch
        Embed->>LLM: embed(doc.page_content)
        LLM-->>Embed: 1536-dim float vector
        Embed->>PG: INSERT (vector, JSONB metadata)
    end
    PG-->>CLI: 150 documents stored
    CLI->>CLI: print("Ingested 150 documents")
```

*Figure 5 — Ingest flow. One thing to note: there's no deduplication guard. Running `--ingest` twice creates duplicate rows. Fine for a portfolio project; a production deployment would need upsert logic or an idempotency check before calling `add_documents()`.*

---

## What's Coming

This first post covered the architecture at a high level. The next eight posts build the system layer by layer:

| Post | Title | What gets built |
|---|---|---|
| 02 | [From Text to Vectors](../02-from-text-to-vectors/) | Deep dive on the data pipeline: synthetic generation, embeddings, pgvector setup, LangChain abstractions |
| 03 | [The Interface Layer](../03-the-interface-layer/) | MCP internals: tool schemas, the stdio transport, connecting Claude Desktop, reusability across teams |
| 04 | [Run Anywhere](../04-run-anywhere/) | Swapping LLM providers via `LLM_PROVIDER`; running entirely local with Ollama; cost and privacy trade-offs |
| 05 | [Built to Last](../05-built-to-last/) | Infrastructure as code: Terraform/OpenTofu on Azure, App Service, Entra Easy Auth pattern |
| 06 | [What Should We Measure?](../06-what-should-we-measure/) | LLM and RAG observability design: what metrics matter, what Grafana and OpenTelemetry bring |
| 07 | [Wiring It Up](../07-wiring-it-up/) | Implementing observability: OTel instrumentation, docker-compose additions, live Grafana dashboards |
| 08 | [Trust, but Verify](../08-trust-but-verify/) | Detecting RAG degradation: embedding drift, retrieval quality signals, CSAT as ground truth, SLOs |
| 09 | [What's Next](../09-whats-next/) | The emerging landscape: GitAgent, LangGraph, Azure AI Foundry — and how this MCP-first approach stays durable |

---

## Try It Yourself

The full code is at [github.com/HendoCode/contact-center-ai](https://github.com/HendoCode/contact-center-ai). Here's the quickstart:

```bash
# 1. Start the local infrastructure
docker compose up -d

# 2. Create a virtualenv and install dependencies (uses uv)
uv venv --python 3.12
uv sync --extra dev

# 3. Configure your environment
cp .env.example .env
# Edit .env: set OPENAI_API_KEY (or set LLM_PROVIDER=ollama for no API key)

# 4. Generate synthetic data
python data/synthetic/generate_data.py

# 5. Embed and store in pgvector
python -m rag.pipeline --ingest

# 6. Test a query end-to-end
python -m rag.pipeline --query "fraud disputes from last week"

# 7. Start the MCP server (connect with Claude Desktop)
python -m mcp.server
```

Post 2 goes much deeper on what each of these steps actually does — the data structures, the LangChain abstractions, the SQL that pgvector generates under the hood.
