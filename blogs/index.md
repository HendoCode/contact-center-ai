---
title: "Series Overview"
subtitle: "Building a production RAG system from scratch — an ongoing series"
author: Stephen Henderson
date: "2026"
description: "Anchoring AI is a growing blog series about building a real RAG pipeline, MCP server, and observability stack for a call center — from synthetic data to production infrastructure."
---

## What is Anchoring AI?

Anchoring AI is a growing series about building a real AI system — not a demo, not a tutorial with a toy dataset, but the kind of thing you'd actually deploy for a paying client. The codebase is a portfolio mirror of consulting work I did for a credit union. The data is synthetic. The architecture decisions, trade-offs, and lessons are real.

The series follows a **build-with-me** arc: each post adds a layer to the system. By the end, we'll have a RAG pipeline, an MCP server, object storage ingestion, a REST API, Azure infrastructure, a Grafana observability stack, and a validation framework — all grounded in a real use case.

The repo lives at [github.com/HendoCode/contact-center-ai](https://github.com/HendoCode/contact-center-ai).

---

## The Posts

| # | Title | What it covers |
|---|---|---|
| [01](./01-the-blueprint/) | **The Blueprint** | Series intro, C4 architecture diagrams, MCP query and ingest sequence diagrams |
| [02](./02-from-text-to-vectors/) | **From Text to Vectors** | Synthetic data generation, the RAG pipeline, pgvector, LangChain embeddings |
| [03](./03-the-interface-layer/) | **The Interface Layer** | MCP server internals, the 3 tools, routing logic, connecting Claude Desktop |
| [04](./04-run-anywhere/) | **Run Anywhere** | Provider agnosticism — swap OpenAI for Ollama with one env var; cost and privacy trade-offs |
| [05](./05-real-data-in/) | **Real Data In** | MinIO, S3-compatible object storage, implementing the S3 ingest stub, deduplication |
| [06](./06-built-to-last/) | **Built to Last** | Terraform/OpenTofu on Azure, App Service, PostgreSQL Flexible, Entra Easy Auth pattern |
| [07](./07-the-developers-toolkit/) | **The Developer's Toolkit** | REST API + OpenAPI/Swagger, PyCharm debugging, Junie for tests and PEP8, HTTP Request files |
| [08](./08-what-should-we-measure/) | **What Should We Measure?** | LLM and RAG-specific metrics, Grafana + OpenTelemetry landscape, dashboard design |
| [09](./09-wiring-it-up/) | **Wiring It Up** | OpenTelemetry instrumentation, docker-compose additions, live Grafana dashboards |
| [10](./10-trust-but-verify/) | **Trust, but Verify** | Validation strategies, drift detection, CSAT as a ground-truth signal, SLOs for AI systems |
| [11](./11-whats-next/) | **What's Next** | GitAgent, LangGraph, Azure AI Foundry — comparing what we built with the emerging landscape |
| [12](./12-agent-harness/) | **The Agent Harness** | gitagent.sh hands-on: connecting an agent harness to this codebase, what changes when an agent has the run of the repo |

