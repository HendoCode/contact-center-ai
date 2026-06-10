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
flowchart LR
    supervisor(["Call Center Supervisor"])
    teams(["Other Teams (future)"])

    subgraph ccai ["Contact Center AI"]
        mcp["MCP Server"]
        rag["RAG Pipeline"]
        pg[("pgvector")]
    end

    openai["OpenAI API"]
    ollama["Ollama - local"]
    azure["Azure App Service"]
    entra["Microsoft Entra\nEasy Auth"]
    s3["AWS S3\n(planned)"]

    supervisor -->|"MCP queries"| mcp
    teams -->|"MCP queries"| mcp
    mcp --> rag
    rag --> pg
    rag -->|"embeddings + completions"| openai
    rag -->|"local alternative"| ollama
    ccai -->|"Terraform"| azure
    entra -->|"validates tokens"| azure
    ccai -.->|"planned"| s3
```

*Figure 1 — System context. Two details are worth noting: Entra authentication is handled entirely at the infrastructure layer (the Python application contains zero auth code), and the Ollama path is a drop-in swap controlled by a single environment variable.*

---

### Containers

Zooming in, the system is six containers. Two are live data paths; one is a future-planned external source.

```mermaid
flowchart TB
    supervisor(["Supervisor"])

    subgraph ccai ["Contact Center AI"]
        mcpServer["MCP Server\nPython / MCP SDK 1.0\nmcp/server.py + tools.py"]
        ragPipeline["RAG Pipeline\nPython / LangChain 0.3\nrag/pipeline.py"]
        embedModule["Embeddings Module\nPython / LangChain\nrag/embeddings.py"]
        pgvector[("PostgreSQL + pgvector\n1536-dim embeddings")]
        dataFiles[/"Data Files\ntranscripts.json + csat.json"/]
    end

    llmProvider["LLM Provider\nOpenAI API or Ollama\nswap via LLM_PROVIDER env var"]

    supervisor -->|"MCP stdio / HTTPS"| mcpServer
    mcpServer -->|"rag_query()"| ragPipeline
    mcpServer -->|"reads csat.json directly"| dataFiles
    ragPipeline -->|"get_vector_store()"| embedModule
    ragPipeline -->|"reads transcripts.json"| dataFiles
    ragPipeline -->|"SQL / pgvector"| pgvector
    ragPipeline -->|"answer synthesis"| llmProvider
    embedModule -->|"vectorize text"| llmProvider
    embedModule -->|"read/write embeddings"| pgvector
```

*Figure 2 — Container view. The split between `search_transcripts`/`get_call_summary` (which go through the full RAG stack) and `query_csat` (which reads JSON directly) is a deliberate design choice — CSAT data is structured and small enough that vector search adds no value.*

---

### Components — The MCP Tool Layer

The MCP server is a single Python process. `server.py` is the async entry point; `tools.py` is where the actual work happens.

```mermaid
flowchart LR
    subgraph server ["mcp/server.py"]
        listTools["list_tools handler\nregisters 3 tool schemas"]
        callTool["call_tool handler\nroutes by tool name"]
    end

    subgraph tools ["mcp/tools.py"]
        searchTool["search_transcripts\nquery + k via rag_query()"]
        summaryTool["get_call_summary\ncall_id via retrieve + rag_query()"]
        csatTool["query_csat\nload csat.json, filter in-memory"]
    end

    ragPipeline["RAG Pipeline\nrag_query() + retrieve()"]
    dataFiles[/"csat.json"/]

    callTool -->|"search_transcripts call"| searchTool
    callTool -->|"get_call_summary call"| summaryTool
    callTool -->|"query_csat call"| csatTool
    searchTool -->|"rag_query(query, k)"| ragPipeline
    summaryTool -->|"retrieve() then rag_query()"| ragPipeline
    csatTool -->|"bypasses RAG entirely"| dataFiles
```

*Figure 3 — The tool dispatch layer. `query_csat` is the outlier: it's the only tool that doesn't go through the vector store or the LLM. It's just a JSON file and some Python filtering logic.*

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
    participant Tools as Tool Dispatch
    participant RAG as RAG Pipeline
    participant Embed as Embeddings
    participant PG as pgvector
    participant LLM as LLM Provider

    Supervisor->>CD: show me fraud disputes from last week
    CD->>MCP: call_tool search_transcripts query k=5
    MCP->>Tools: search_transcripts(query, k=5)
    Tools->>RAG: rag_query(query, k=5)
    RAG->>Embed: get_vector_store()
    Embed-->>RAG: PGVector instance ready
    RAG->>LLM: embed query string to 1536-dim vector
    LLM-->>RAG: query embedding
    RAG->>PG: similarity_search(embedding, k=5)
    PG-->>RAG: top-5 LangChain Documents
    RAG->>LLM: ChatOpenAI.invoke(prompt + retrieved context)
    LLM-->>RAG: grounded answer text
    RAG-->>Tools: answer string
    Tools-->>MCP: TextContent(answer)
    MCP-->>CD: tool result
    CD-->>Supervisor: displays answer
```

*Figure 4 — MCP query flow. The round-trip involves two LLM calls: one to embed the query (fast, cheap), one to synthesize the answer (slower, more expensive). Both happen over the same provider — swap the `LLM_PROVIDER` env var and both switch simultaneously.*

---

### Flow 2 — Ingest and Embedding

Before any query can work, the transcripts need to be in the vector store. This is the ingestion flow:

```mermaid
sequenceDiagram
    participant Gen as Data Generator
    participant JSON as transcripts.json
    participant CLI as Ingest Script
    participant Embed as Embeddings
    participant LLM as LLM Provider
    participant PG as pgvector

    Gen->>JSON: write 150 synthetic call records
    CLI->>JSON: load_synthetic_data()
    JSON-->>CLI: 150 raw dicts
    CLI->>CLI: transcripts_to_documents - wrap in LangChain Documents
    CLI->>Embed: get_vector_store()
    Embed->>LLM: get_embeddings - text-embedding-3-small or nomic-embed-text
    Embed->>PG: initialize schema and pgvector extension
    PG-->>Embed: ready
    CLI->>Embed: vector_store.add_documents(docs)
    loop for each document
        Embed->>LLM: embed page_content
        LLM-->>Embed: 1536-dim float vector
        Embed->>PG: INSERT embedding and JSONB metadata
    end
    PG-->>CLI: 150 documents stored
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
