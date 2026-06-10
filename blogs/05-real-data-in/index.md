---
title: "Real Data In"
subtitle: "MinIO, S3-compatible object storage, and completing the ingest pipeline"
author: Stephen Henderson
date: "TBD"
post_number: "05"
description: "Post 5 of Anchoring AI: implementing the S3 ingestion stub in pipeline.py using MinIO as a local S3-compatible Docker service — connecting real object storage to the vector store."
prev_url: "../04-run-anywhere/"
prev_title: "Run Anywhere"
next_url: "../06-built-to-last/"
next_title: "Built to Last"
---

> **Status: Placeholder.** This post is planned. The outline and key concepts below describe what it will cover.

---

## What This Post Covers

There's a `NotImplementedError` in `rag/pipeline.py` — the S3 ingestion path has always been a stub. Transcripts have been loaded directly from a JSON file during development. In production, they'll come from blob storage: S3 in the fictional credit union's case, Azure Blob in the deployment environment.

This post implements the stub using MinIO — a local, Docker-based S3-compatible object storage service. The goal is to complete the ingestion pipeline in a way you can run entirely locally (continuing the "no external APIs required" theme from Post 4), then show how the same code connects to a real S3 bucket or Azure Blob Storage container with an environment variable change.

---

## Key Concepts

- **MinIO** — an open-source, S3-API-compatible object storage server; runs in Docker; `aws` CLI and `boto3` work against it unchanged
- **S3-compatible API** — why "S3-compatible" matters: the same `boto3` calls work against AWS S3, MinIO, Azure Blob (via compatibility layer), and GCS; object storage is a commodity interface
- **`boto3` for ingestion** — listing objects with a prefix, streaming object content, handling large files without loading them fully into memory
- **Chunking strategies** — transcripts can exceed LLM context windows; splitting on sentence boundaries vs. fixed token counts vs. paragraph breaks; what LangChain's text splitters provide
- **Deduplication** — the existing `pipeline.py --ingest` has no deduplication: running it twice creates duplicate documents in pgvector; object storage ingestion makes this worse (every re-run re-reads every object); tracking ingested object ETags or S3 version IDs as a simple dedup guard
- **Source flexibility** — how `pipeline.py` will support `--source json` (current) and `--source s3` (new) with the same downstream embedding and storage logic

---

## MinIO in Docker Compose

```yaml
minio:
  image: minio/minio:latest
  container_name: minio
  ports:
    - "9000:9000"   # S3 API
    - "9001:9001"   # MinIO Console UI
  environment:
    MINIO_ROOT_USER: minioadmin
    MINIO_ROOT_PASSWORD: minioadmin
  command: server /data --console-address ":9001"
  volumes:
    - minio:/data

createbuckets:
  image: minio/mc:latest
  depends_on:
    - minio
  entrypoint: >
    /bin/sh -c "
    mc alias set local http://minio:9000 minioadmin minioadmin &&
    mc mb --ignore-existing local/call-data &&
    mc cp /tmp/transcripts.json local/call-data/transcripts.json
    "
```

---

## Planned Outline

1. **The stub and why it exists** — look at `pipeline.py` lines where `NotImplementedError` is raised; trace back to why ingestion was JSON-first during prototyping
2. **What object storage actually is** — blobs, prefixes, ETags, eventual consistency; how it's different from a filesystem
3. **MinIO setup** — docker-compose additions; the MinIO Console at `localhost:9001`; uploading the synthetic transcripts
4. **Implementing `ingest_from_s3()`** — listing objects, streaming content, building LangChain Documents; handling metadata from S3 object tags
5. **Deduplication** — the existing dedup gap; tracking ingested ETags in a simple state store or a `ingested_objects` table in PostgreSQL
6. **Chunking** — when transcripts are too long; LangChain's `RecursiveCharacterTextSplitter`; choosing chunk size for call center transcripts
7. **Connecting to real S3 or Azure Blob** — environment variable changes only; the `BOTO_ENDPOINT_URL` pattern for endpoint override
8. **Running it** — `python -m rag.pipeline --ingest --source s3`; verifying the documents landed in pgvector

---

## Code Changes for This Post

- Implement `ingest_from_s3()` in `rag/pipeline.py` (replaces `NotImplementedError`)
- Add MinIO + `createbuckets` services to `docker-compose.yml`
- Add `boto3` to `pyproject.toml` dependencies
- Add `S3_ENDPOINT_URL` env var support to `.env.example` (for MinIO override)
- Optionally: add a simple `ingested_objects` tracking table for deduplication

---

## Outstanding Questions / TBD

- Use `RecursiveCharacterTextSplitter` or a simpler fixed-character split for this post? (Full splitter comparison may belong in a separate section)
- Show Azure Blob Storage connection via S3-compatible endpoint, or treat that separately?
- Address the dedup problem fully here or defer to Post 10 (Trust, but Verify)?
- Include a section on IAM/access policy for MinIO buckets vs. AWS S3 bucket policies?
