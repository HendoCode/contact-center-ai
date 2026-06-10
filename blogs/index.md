---
title: "Series Overview"
subtitle: "Nine posts on building a production RAG system from scratch"
author: Stephen Henderson
date: "2026"
description: "Anchoring AI is a nine-part blog series about building a real RAG pipeline, MCP server, and observability stack for a call center — from synthetic data to production infrastructure."
---

## What is Anchoring AI?

This is a nine-post series about building a real AI system — not a demo, not a tutorial with a toy dataset, but the kind of thing you'd actually deploy for a paying client. The codebase is a portfolio mirror of consulting work I did for a credit union. The data is synthetic. The architecture decisions, trade-offs, and lessons are real.

The series follows a **build-with-me** arc: each post adds a layer to the system. By the end, we'll have a RAG pipeline, an MCP server, Azure infrastructure, a Grafana observability stack, and a validation framework — all grounded in a real use case.

The repo lives at [github.com/HendoCode/contact-center-ai](https://github.com/HendoCode/contact-center-ai).

---

## The Posts

| # | Title | What it covers |
|---|---|---|
| [01](./01-the-blueprint/) | **The Blueprint** | Series intro, C4 architecture diagrams, MCP query and ingest sequence diagrams |
| [02](./02-from-text-to-vectors/) | **From Text to Vectors** | Synthetic data generation, the RAG pipeline, pgvector, LangChain embeddings |
| [03](./03-the-interface-layer/) | **The Interface Layer** | MCP server internals, the 3 tools, routing logic, connecting Claude Desktop |
| [04](./04-run-anywhere/) | **Run Anywhere** | Provider agnosticism — swap OpenAI for Ollama with one env var; cost and privacy trade-offs |
| [05](./05-built-to-last/) | **Built to Last** | Terraform/OpenTofu on Azure, App Service, PostgreSQL Flexible, Entra Easy Auth pattern |
| [06](./06-what-should-we-measure/) | **What Should We Measure?** | LLM and RAG-specific metrics, Grafana + OpenTelemetry landscape, dashboard design |
| [07](./07-wiring-it-up/) | **Wiring It Up** | OpenTelemetry instrumentation, docker-compose additions, live Grafana dashboards |
| [08](./08-trust-but-verify/) | **Trust, but Verify** | Validation strategies, drift detection, CSAT as a ground-truth signal, SLOs for AI systems |
| [09](./09-whats-next/) | **What's Next** | GitAgent, LangGraph, Azure AI Foundry — comparing what we built with the emerging landscape |

