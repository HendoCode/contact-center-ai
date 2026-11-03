---
title: "Series Overview"
subtitle: "Building a governed semantic layer for a contact center and serving it to AI assistants — an ongoing series"
author: Stephen Henderson
date: "2026"
description: "Anchoring AI is a growing blog series about building a governed semantic layer and transcript search for a credit-union contact center, and serving both to AI assistants through one MCP server."
---

## What is Anchoring AI?

Anchoring AI is a growing series about building a real AI system — not a demo, not a tutorial with a toy dataset, but the kind of thing you'd actually deploy for a paying client. The codebase is a portfolio mirror of consulting work I did for a credit union. The data is synthetic. The architecture decisions, trade-offs, and lessons are real.

The series follows a **build-with-me** arc: each post adds a layer to the system. Posts 01–04 built the transcript pipeline and the first MCP server. Posts 05–12 follow what the system has become since: a semantic layer of 47 governed metrics, LanceDB as the transcript store, metric and evidence tools served to AI assistants over MCP, evals for the agent that uses them, and the coding agents that build it.

The repo lives at [github.com/HendoCode/contact-center-ai](https://github.com/HendoCode/contact-center-ai).

---

## The Posts

| # | Title | What it covers |
|---|---|---|
| [01](./01-the-blueprint/) | **The Blueprint** | Series intro, C4 architecture diagrams, MCP query and ingest sequence diagrams |
| [02](./02-from-text-to-vectors/) | **From Text to Vectors** | Synthetic data generation, the RAG pipeline, pgvector, LangChain embeddings |
| [03](./03-the-interface-layer/) | **The Interface Layer** | MCP server internals, the 3 tools, routing logic, connecting Claude Desktop |
| [04](./04-run-anywhere/) | **Run Anywhere** | Provider agnosticism — swap OpenAI for Ollama with one env var; cost and privacy trade-offs |
| [05](./05-real-data-in/) | **Real Data In** | The OLTP ledger behind the metrics: the Postgres schema and seed, loaders that put the same rows into Snowflake and Databricks, and a parity check that the metrics agree on all three |
| [06](./06-built-to-last/) | **Built to Last** | LanceDB as the transcript store: a benchmark against pgvector on 100,000 calls, CSAT score filters, and versioning with tags, rollback and retention |
| [07](./07-the-developers-toolkit/) | **The Developer's Toolkit** | The local stack: Docker Compose profiles, one command to build all local data, notebooks, architecture diagrams from one model, and Terraform for Azure Container Apps checked in CI |
| [08](./08-what-should-we-measure/) | **What Should We Measure?** | The semantic layer: dbt and MetricFlow over a star schema, 47 governed metrics, and how one word like "rate" maps to several named metrics |
| [09](./09-wiring-it-up/) | **Wiring It Up** | Serving the semantic layer over MCP: the metric tools, `explain_change` with cited calls, streamable HTTP, and connecting Claude, Gemini, Cursor and Open WebUI to the same server |
| [10](./10-trust-but-verify/) | **Trust, but Verify** | Evals for the agent and the tools: a golden set, an offline gate in CI, live runs traced in LangSmith, and what the first live run measured |
| [11](./11-whats-next/) | **What's Next** | The LangGraph agent that asks which metric you mean before it answers, and the work still on the roadmap |
| [12](./12-agent-harness/) | **The Agent Harness** | Coding agents working on this codebase, from task prompt to reviewed pull request, and what changes when an agent owns a task end to end |

