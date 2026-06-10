---
title: "What Should We Measure?"
subtitle: "Designing LLM and RAG observability before writing a line of instrumentation code"
author: Stephen Henderson
date: "TBD"
post_number: "08"
description: "Post 8 of Anchoring AI: what metrics actually matter for LLM and RAG systems, how Grafana and OpenTelemetry fit together, and how to design dashboards before you instrument anything."
prev_url: "../07-the-developers-toolkit/"
prev_title: "The Developer's Toolkit"
next_url: "../09-wiring-it-up/"
next_title: "Wiring It Up"
---

> **Status: Placeholder.** This post is planned. The outline and key concepts below describe what it will cover.

---

## What This Post Covers

Most observability posts start with "add these metrics." This one starts with "what do you actually need to know?" LLM and RAG systems have failure modes that traditional SRE metrics don't capture: answer quality degrades silently, retrieval relevance erodes as the corpus grows, and a 200 response code tells you nothing about whether the answer was correct.

This is a design post. Post 7 implements it. By the end of this post, you'll have a clear mental model of what to measure, why each metric matters, and how Grafana + OpenTelemetry fit into the stack — without having written a single `meter.record()` call yet.

---

## Key Concepts

- **The four observability layers for AI systems** — infrastructure metrics (CPU, memory), API metrics (latency, error rate), LLM metrics (token counts, cost, model version), RAG metrics (retrieval score, result count, context size)
- **Why standard SRE metrics aren't enough** — a 200 OK with a hallucinated answer looks identical to a 200 OK with a correct one
- **OpenTelemetry** — traces, metrics, logs as a unified model; why OTel is the right standard for language-agnostic instrumentation
- **Grafana + Prometheus** — how metrics flow from OTel collector → Prometheus → Grafana; the local docker-compose setup
- **Retrieval quality as a metric** — cosine similarity scores from pgvector as a proxy for answer groundedness; how to emit them as OTel metrics
- **Token cost tracking** — why you want to know how much each MCP query costs; how to compute it from the OpenAI response
- **LLM latency decomposition** — total request time vs. embedding time vs. completion time; where the time actually goes

---

## Planned Metrics Inventory

| Metric | Type | Why it matters |
|---|---|---|
| `mcp_tool_duration_seconds` | Histogram | End-to-end tool latency by tool name |
| `rag_query_duration_seconds` | Histogram | RAG pipeline latency (excludes MCP overhead) |
| `embedding_duration_seconds` | Histogram | Embedding API call latency |
| `llm_completion_duration_seconds` | Histogram | LLM completion latency |
| `llm_tokens_prompt_total` | Counter | Cumulative prompt tokens (cost proxy) |
| `llm_tokens_completion_total` | Counter | Cumulative completion tokens (cost proxy) |
| `rag_retrieval_score` | Gauge/Histogram | Max cosine similarity of returned docs (quality proxy) |
| `rag_documents_retrieved_total` | Histogram | Number of docs returned per query |
| `csat_query_results_count` | Histogram | Number of CSAT records returned per query |

---

## Planned Outline

1. **The problem with AI observability** — why p95 latency and error rate aren't enough; the silent failure modes unique to LLM systems
2. **A mental model for AI system observability** — four layers: infra, API, LLM, RAG; what breaks at each layer
3. **The OpenTelemetry basics** — traces, metrics, logs; why OTel; the collector as the central router
4. **Grafana + Prometheus architecture** — how metrics flow; the docker-compose additions we'll add in Post 7
5. **Metrics that matter for RAG** — retrieval score, context window utilization, document staleness
6. **Metrics that matter for LLM** — token counts, cost per query, model version tracking, latency decomposition
7. **Dashboard design** — what panels to build; alert thresholds that make sense; the CSAT panel idea
8. **What we'll build in Post 7** — a preview of the implementation: OTel instrumentation points, the docker-compose additions, the Grafana dashboard JSON

---

## Code Changes for This Post

None — this is a design post. The next post (Post 7) implements the instrumentation.

---

## Outstanding Questions / TBD

- Include Langfuse as an alternative to Prometheus/Grafana for LLM-specific observability?
- Show a mockup of the Grafana dashboard layout?
- Compare OTel vs. OpenLLMetry vs. Langfuse in a trade-off table?
