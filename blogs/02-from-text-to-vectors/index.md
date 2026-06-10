---
title: "From Text to Vectors"
subtitle: "Building the RAG data pipeline: synthetic data, embeddings, and pgvector"
author: Stephen Henderson
date: "TBD"
post_number: "02"
description: "Post 2 of Anchoring AI: deep dive on the data pipeline — synthetic call generation, LangChain embeddings, pgvector storage, and how it all fits together."
prev_url: "../01-the-blueprint/"
prev_title: "The Blueprint"
next_url: "../03-the-interface-layer/"
next_title: "The Interface Layer"
---

> **Status: Placeholder.** This post is planned. The outline and key concepts below describe what it will cover.

---

## What This Post Covers

Post 1 showed the architecture. This post goes inside the data layer: how 150 synthetic call transcripts go from a Python script to searchable embeddings in PostgreSQL, and what LangChain is actually doing along the way.

By the end, you'll understand why `rag/pipeline.py` and `rag/embeddings.py` are designed the way they are, what pgvector gives you that a standard SQL full-text search doesn't, and what "1536 dimensions" actually means in practice.

---

## Key Concepts

- **Synthetic data generation** — using Faker to create realistic call transcripts with weighted CSAT outcomes
- **Embeddings** — what they are, why `text-embedding-3-small` at 1536 dimensions, and what the numbers actually represent
- **LangChain `Document` objects** — the abstraction that separates content from metadata, and why that matters for retrieval
- **pgvector** — how the vector extension works in PostgreSQL, what an ANN index looks like, and why it's the right choice over a dedicated vector DB for this use case
- **The `PGVector.from_documents()` abstraction** — what LangChain's wrapper does and what it hides from you
- **No deduplication** — why running `--ingest` twice creates duplicate rows and how to fix it

---

## Planned Outline

1. **The data: what's in a transcript** — walking through the `generate_data.py` output; call categories, outcomes, CSAT weighting
2. **From dict to Document** — `transcripts_to_documents()`: why page_content vs. metadata matters for retrieval; what gets stored in JSONB
3. **What are embeddings, actually** — intuition for cosine similarity; why semantic search beats keyword search for call center queries
4. **The provider abstraction** — `get_embeddings()` as the only coupling point to OpenAI; why everything else is provider-agnostic
5. **pgvector under the hood** — the SQL pgvector actually runs; HNSW vs IVFFlat indexes; when you'd need to switch
6. **Ingestion in detail** — stepping through `ingest()` line by line; the `add_documents()` call; batch sizing
7. **The dedup problem** — why the current design has no guard, and what a production fix would look like
8. **Querying** — `retrieve()` and `similarity_search()`; what `k` means in practice; reading the metadata from returned Documents

---

## Code Changes for This Post

No new code changes — this post documents the existing `rag/pipeline.py` and `rag/embeddings.py`. Code walkthrough is the deliverable.

---

## Outstanding Questions / TBD

- Include a visualization of the embedding space? (2D UMAP projection of the 150 transcripts colored by category)
- Show the raw pgvector SQL queries with `EXPLAIN ANALYZE`?
- Compare query results with and without metadata filtering?
