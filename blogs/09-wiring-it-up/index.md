---
title: "Wiring It Up"
subtitle: "OpenTelemetry instrumentation, Grafana in docker-compose, and live LLM/RAG dashboards"
author: Stephen Henderson
date: "TBD"
post_number: "09"
description: "Post 9 of Anchoring AI: implementing the observability stack designed in Post 8 — OTel instrumentation in Python, adding Grafana + Prometheus to docker-compose, and building live dashboards."
prev_url: "../08-what-should-we-measure/"
prev_title: "What Should We Measure?"
next_url: "../10-trust-but-verify/"
next_title: "Trust, but Verify"
---

> **Status: Placeholder.** This post is planned. The outline and key concepts below describe what it will cover.

---

## What This Post Covers

Post 6 designed the observability stack. This post builds it. We'll add OpenTelemetry instrumentation to `rag/pipeline.py` and `mcp/tools.py`, extend `docker-compose.yml` with a Grafana + Prometheus + OTel Collector stack, and wire up the dashboards described in Post 6.

By the end, every MCP tool call is a traced span, every RAG query emits latency and retrieval score metrics, and every LLM call reports token counts to Grafana — all visible in a live dashboard.

---

## Key Concepts

- **`opentelemetry-sdk`** — the Python OTel SDK; `tracer`, `meter`, `logger` providers; how to initialize them in a long-running process
- **Instrumentation points** — where to add `tracer.start_as_current_span()` and `meter.record()` in the existing code
- **OTel Collector** — the sidecar that receives OTel data from the Python process and exports to Prometheus; why you want the collector vs. direct Prometheus export
- **Prometheus** — scraping the OTel Collector; the metrics format; retention
- **Grafana** — provisioning datasources and dashboards as YAML/JSON; the panels we'll build
- **`docker-compose.yml` additions** — the three new services: otel-collector, prometheus, grafana; their interconnections
- **Context propagation** — how trace context flows from MCP server → RAG pipeline → embeddings module so the full call tree is one trace

---

## Planned New Services (docker-compose additions)

```yaml
# New services to add:
otel-collector:
  image: otel/opentelemetry-collector-contrib
  # receives from Python app on 4317 (gRPC) / 4318 (HTTP)
  # exports to Prometheus on 8889

prometheus:
  image: prom/prometheus
  # scrapes otel-collector on 8889

grafana:
  image: grafana/grafana
  # provisioned datasource: Prometheus
  # provisioned dashboard: Anchoring AI LLM/RAG Metrics
```

---

## Planned Outline

1. **The plan** — what we're adding, where it goes, how it connects
2. **Installing OTel** — `opentelemetry-sdk`, `opentelemetry-exporter-otlp`, the Python package list
3. **Initializing the SDK** — where to call `TracerProvider`, `MeterProvider`, `LoggerProvider`; the `Resource` that identifies this service
4. **Instrumenting `mcp/tools.py`** — spans for each tool call; tagging with tool name and input parameters
5. **Instrumenting `rag/pipeline.py`** — spans for `rag_query()` and `retrieve()`; emitting `rag_retrieval_score`, token counts, document counts
6. **Instrumenting `rag/embeddings.py`** — timing the embedding API call; emitting `embedding_duration_seconds`
7. **Docker Compose additions** — the three new service definitions; the OTel Collector config YAML; Prometheus scrape config
8. **Grafana setup** — provisioning the datasource; importing the dashboard JSON; walking through each panel
9. **Firing a query and watching it land** — live trace in Grafana Explore; metrics updating on the dashboard
10. **What's missing** — alerting rules (teased for Post 8); log correlation; distributed traces across service boundaries

---

## Code Changes for This Post

- Add OTel SDK to `pyproject.toml` dependencies
- Add OTel initialization module (e.g., `rag/telemetry.py`)
- Instrument `mcp/tools.py`, `rag/pipeline.py`, `rag/embeddings.py`
- Extend `docker-compose.yml` with otel-collector, prometheus, grafana services
- Add `infra/otel/collector-config.yaml`, `infra/prometheus/prometheus.yml`
- Add `infra/grafana/dashboards/anchoring-ai.json` (provisioned dashboard)

---

## Outstanding Questions / TBD

- Use `opentelemetry-instrumentation-langchain` for automatic LangChain spans vs. manual instrumentation?
- Include Grafana alerting rule configuration?
- Show how to export the OTel data to a cloud provider (Honeycomb, Datadog, etc.) instead of local Prometheus?
