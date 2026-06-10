---
title: "Trust, but Verify"
subtitle: "Detecting RAG degradation before your users do"
author: Stephen Henderson
date: "TBD"
post_number: "08"
description: "Post 8 of Anchoring AI: validation strategies for RAG systems — embedding drift, retrieval quality signals, CSAT as a ground-truth signal, LLM-as-judge, and SLOs for AI systems."
prev_url: "../07-wiring-it-up/"
prev_title: "Wiring It Up"
next_url: "../09-whats-next/"
next_title: "What's Next"
---

> **Status: Structured draft.** This post has a detailed outline and will be written after Posts 02–07 are complete.

---

## Thesis

RAG systems degrade silently. Embeddings drift as language evolves, retrieval quality erodes as the corpus grows, and LLM behavior shifts between model versions. By the time users complain, the damage is done. A 200 OK response with a confidently stated wrong answer is worse than an error — at least an error is visible.

This post shows how to use the telemetry from Post 7 to detect degradation early — and how the CSAT scores we already collect become a lagging ground-truth signal for answer quality. The goal is to move from "we hope it's working" to "we know when it's not."

---

## Key Concepts

- **Three failure modes to watch**
  - *Data drift*: new call categories appear that weren't in the original corpus; the model has no good neighbors to retrieve
  - *Concept drift*: the meaning of "fraud" or "escalation" changes as call center language evolves; old embeddings become stale
  - *Retrieval drift*: top-k results become less relevant as the corpus grows and the ANN index quality degrades
- **Embedding drift** — cosine similarity distributions shift over time; how to detect it using the `rag_retrieval_score` metric from Post 7
- **Retrieval quality metrics** — MRR, NDCG, top-k relevance; how to estimate these without human labels using LLM-as-judge
- **CSAT as a lagging ground-truth signal** — when call center agents rate calls poorly *after* using the AI tool, that's signal; correlating CSAT scores with MCP query volume by category reveals where the system is failing
- **LLM-as-judge** — a lightweight evaluation pattern: periodically sample recent query/answer pairs and ask a cheap model to rate answer quality; emit scores as OTel metrics
- **SLOs for AI systems** — what "good enough" looks like expressed as thresholds, not just dashboards
- **Shadow mode evaluation** — running a new embedding model in parallel to compare retrieval quality before promoting it
- **Human-in-the-loop checkpoints** — when to trigger a human review: retrieval score below threshold, new call category detected, embedding distribution shift

---

## Planned Outline

1. **The silent failure problem** — a wrong answer delivered confidently is worse than no answer; why 200 OK means nothing for AI systems

2. **Three failure modes, diagnosed**
   - *Data drift*: detecting new categories that weren't in the corpus at ingest time; how to surface this from query logs
   - *Concept drift*: embedding the same phrase from 2023 vs. 2026 produces different vectors; when to re-embed the corpus
   - *Retrieval drift*: how ANN index quality degrades as a pgvector collection grows; HNSW vs. IVFFlat behavior; when to rebuild the index

3. **The telemetry we have (from Post 7)** — what `rag_retrieval_score`, `rag_documents_retrieved_total`, and `llm_tokens_prompt_total` tell us; what's still missing

4. **Adding the missing retrieval quality signal**
   - Emit max cosine similarity from `retrieve()` as a Prometheus histogram
   - A Grafana alert rule: fire when p50 retrieval score drops below 0.70 for 30 minutes

5. **CSAT as ground truth**
   - Revisit `query_csat()` — correlating low CSAT weeks with MCP query patterns from Post 7 telemetry
   - New Grafana panel: CSAT score distribution vs. RAG query volume by category (time series overlay)
   - What a correlated drop looks like; how to investigate

6. **LLM-as-judge for answer quality**
   - A lightweight `scripts/eval_sample.py`: sample N recent query/answer pairs from logs, ask a cheap model to rate 1–5, emit scores as a metric
   - How to automate this as a nightly job (cron or GitHub Actions)
   - The limits of LLM-as-judge: what it catches and what it misses

7. **Drift detection in practice** — Grafana alert rules for:
   - Retrieval score p50 degradation
   - Embedding latency spikes (OpenAI model API changes)
   - New category labels not seen at original ingest time

8. **SLOs for this system** — proposed thresholds that make sense for the Northgate Federal use case:

   | SLO | Threshold | Window |
   |---|---|---|
   | Retrieval score p50 | > 0.70 | Rolling 7-day |
   | Query latency p95 | < 4 seconds | Rolling 24-hour |
   | CSAT trend | ≥ 4.0 average | Rolling 30-day |
   | LLM-as-judge score p50 | > 3.5 / 5 | Rolling 7-day |

9. **Runbook sketch: what to do when alerts fire**
   - Retrieval score drops → check for corpus skew; consider re-ingest or index rebuild
   - Latency spike → check OpenAI status; consider Ollama fallback
   - CSAT drop → trigger LLM-as-judge eval; review flagged query/answer pairs with a human
   - New category detected → add representative calls; re-embed corpus; update retrieval tests

10. **Shadow mode evaluation** — running a new embedding model (`text-embedding-3-large` or a future Ollama model) in a shadow collection; comparing retrieval scores before promoting

---

## Code Changes for This Post

- Add `retrieval_score` metric to `rag/pipeline.py` (emit highest cosine score from pgvector results as OTel metric)
- Add `scripts/eval_sample.py` — samples N recent queries from logs, rates quality with LLM-as-judge, prints report and optionally emits as metrics
- New Grafana panel: CSAT score vs. RAG query volume (time series, JSON committed to `infra/grafana/`)
- Grafana alert rule for retrieval score degradation (provisioned alert YAML)

---

## Outstanding Questions / TBD

- How to log query/answer pairs for the LLM-as-judge eval? Need a structured log format from Post 7 instrumentation.
- Include Ragas (the open-source RAG eval framework) as an alternative to hand-rolled LLM-as-judge?
- Show the HNSW index rebuild command for pgvector?
- Discuss the annotation / human feedback loop as a path toward true supervised eval?
