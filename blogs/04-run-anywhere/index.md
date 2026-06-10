---
title: "Run Anywhere"
subtitle: "Provider agnosticism: swapping OpenAI for Ollama with one environment variable"
author: Stephen Henderson
date: "TBD"
post_number: "04"
description: "Post 4 of Anchoring AI: how the LLM_PROVIDER env var pattern works, how to run the entire stack locally with Ollama, and the cost and privacy trade-offs of each approach."
prev_url: "../03-the-interface-layer/"
prev_title: "The Interface Layer"
next_url: "../05-built-to-last/"
next_title: "Built to Last"
---

> **Status: Placeholder.** This post is planned. The outline and key concepts below describe what it will cover.

---

## What This Post Covers

One of the cleaner design decisions in this codebase is that the entire LLM stack — embeddings and completions — can be swapped between OpenAI and Ollama by changing a single environment variable and restarting the server. No code changes. This post explains how that works, why it was designed that way, and what the real-world trade-offs are.

It also covers Ollama specifically: setting it up, pulling the models, and what "local LLM" actually means in practice for a use case like this one.

---

## Key Concepts

- **`LLM_PROVIDER` env var** — how a single variable controls both embedding model and completion model selection
- **`rag/embeddings.py` as the single coupling point** — why provider swaps require editing only this file
- **LangChain provider abstractions** — `OpenAIEmbeddings` vs `OllamaEmbeddings`; `ChatOpenAI` vs `ChatOllama`
- **Ollama** — running models locally; `nomic-embed-text` as an embedding alternative to `text-embedding-3-small`; `llama3.2` as a `gpt-4o-mini` alternative
- **Embedding dimension mismatch** — why you can't switch embedding models mid-stream without re-ingesting; the data compatibility constraint
- **Cost and privacy trade-offs** — API cost vs. hardware cost; what "local" means for data that can't leave the network
- **Model quality gap** — where the quality difference between OpenAI and Ollama matters most for this use case

---

## Planned Outline

1. **The design constraint** — why the credit union needed a "no API key" mode; what drove the provider abstraction
2. **How the swap works** — reading `LLM_PROVIDER` in `embeddings.py` and `pipeline.py`; the conditional import pattern
3. **Ollama setup** — `docker compose up -d ollama`; pulling models; verifying they're available
4. **Running the full stack with Ollama** — step-by-step: ingest, query, MCP server — all local, no external calls
5. **The embedding dimension problem** — `text-embedding-3-small` is 1536 dims; `nomic-embed-text` is 768; why switching mid-stream breaks everything
6. **Quality comparison** — a side-by-side query comparison: the same question answered by OpenAI vs. Ollama; where the gap is visible
7. **Adding a third provider** — what it would take to add Anthropic or Gemini embeddings; what would need to change
8. **When to use which** — decision guide: API key available + quality matters → OpenAI; data privacy + no external calls → Ollama; prototyping → either

---

## Code Changes for This Post

Possibly: add a third provider option as a demonstration (e.g., a stub for Anthropic or a `cohere` embedding option). TBD.

---

## Outstanding Questions / TBD

- Benchmark query latency and quality across providers?
- Show the docker-compose Ollama service in detail?
- Discuss open-webui (already in docker-compose) as a way to test models before integrating?
