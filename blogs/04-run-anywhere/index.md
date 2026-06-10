---
title: "Run Anywhere"
subtitle: "Provider agnosticism: swapping OpenAI for Ollama with one environment variable"
author: Stephen Henderson
date: "June 2026"
post_number: "04"
description: "Post 4 of Anchoring AI: how the LLM_PROVIDER pattern works, running the full stack locally with Ollama, the embedding dimension constraint, and when local models are good enough."
prev_url: "../03-the-interface-layer/"
prev_title: "The Interface Layer"
next_url: "../05-real-data-in/"
next_title: "Real Data In"
---

Early in the engagement, a question came up that shaped a lot of the architecture: *what happens if the client decides to stop using OpenAI?*

It's not a hypothetical. The credit union had already watched one team sign a two-year contract with a vendor that pivoted its product and left them stuck. They were cautious about building on any single provider's API, and they had a compliance team asking pointed questions about which member data was being sent where.

Both concerns point to the same design requirement: **the system should be able to run without calling any external API.** Not as a fallback, not as a workaround — as a first-class operating mode. An environment variable decides which provider runs. Nothing else changes.

This post is about how that works, what it costs in quality, and when running local is actually the right call.

---

## The Pattern

The entire provider selection lives in two functions across two files. That's it.

In `rag/embeddings.py`, `get_embeddings()` checks `LLM_PROVIDER` and returns the appropriate embedding model:

```python
def get_embeddings():
    provider = os.getenv("LLM_PROVIDER", "openai").lower()

    if provider == "ollama":
        from langchain_ollama import OllamaEmbeddings
        return OllamaEmbeddings(
            model=os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text"),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        )

    from langchain_openai import OpenAIEmbeddings
    return OpenAIEmbeddings(
        model="text-embedding-3-small",
        api_key=os.getenv("OPENAI_API_KEY"),
    )
```

In `rag/pipeline.py`, `rag_query()` does the same for the completion model:

```python
provider = os.getenv("LLM_PROVIDER", "openai").lower()
if provider == "ollama":
    from langchain_ollama import ChatOllama
    llm = ChatOllama(
        model=os.getenv("OLLAMA_MODEL", "llama3.2"),
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
    )
else:
    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(model="gpt-4o-mini", api_key=os.getenv("OPENAI_API_KEY"))
```

Everything else in the codebase — the ingestion pipeline, the retrieval step, the MCP server — calls `get_embeddings()` or `get_vector_store()` and gets whatever comes back. Neither the MCP tools nor the server have any awareness of which provider is running.

Notice the lazy imports: `from langchain_ollama import ...` lives inside the `if` branch, not at the top of the file. This means the import only happens when that provider is actually selected. Both packages are installed (they're in `pyproject.toml`), but the pattern keeps the coupling explicit at the point where the choice is made.

---

## Setting Up Ollama

Ollama is already in `docker-compose.yml`:

```yaml
ollama:
  image: ollama/ollama:latest
  container_name: ollama
  tty: true
  restart: unless-stopped
  ports:
    - "11434:11434"
  volumes:
    - ollama:/root/.ollama
```

The container runs but doesn't come with any models pre-loaded. You pull them after it starts:

```bash
docker compose up -d ollama

# Pull the models
docker compose exec ollama ollama pull nomic-embed-text
docker compose exec ollama ollama pull llama3.2
```

`nomic-embed-text` is the embedding model — the local equivalent of `text-embedding-3-small`. `llama3.2` is the completion model — the local equivalent of `gpt-4o-mini`. Both run inside the container. No network calls once they're pulled.

Then flip the provider:

```bash
# In .env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
```

Restart the server and the entire stack is running locally. The MCP tools work identically from the client's perspective. The only difference is where the compute happens.

---

## The Dimension Problem

There's a constraint worth understanding before you switch providers on an existing system: **you cannot swap embedding models on a populated vector store.**

`text-embedding-3-small` produces 1,536-dimensional vectors. `nomic-embed-text` produces 768-dimensional vectors. The two are fundamentally incompatible — you can't do cosine similarity between a 1,536-dim query vector and a 768-dim document vector, and pgvector will reject the attempt.

If you switch `LLM_PROVIDER` from `openai` to `ollama` without re-ingesting, your queries will fail at the vector search step. The documents in pgvector were embedded with OpenAI's model; the query is now being embedded with Ollama's. The dimensions don't match.

The fix is a full re-ingest:

```bash
# Drop the existing collection (connect to psql and truncate, or
# delete via psycopg2 — PGVector has no built-in drop collection CLI)

# Re-ingest with the new provider active
LLM_PROVIDER=ollama python -m rag.pipeline --ingest
```

This also means that if you're running in production with OpenAI embeddings and you want to evaluate Ollama, you'd stand up a second collection (`call_transcripts_ollama`), ingest into it, and compare results — not swap the live collection in place. We'll return to this shadow-mode evaluation pattern in Post 8 when we talk about drift detection.

The practical rule: treat a provider change as a schema migration. Plan it, test it, don't do it live.

---

## open-webui: Testing Models Before You Commit

The docker-compose file includes one more service worth knowing about:

```yaml
open-webui:
  image: ghcr.io/open-webui/open-webui:main
  ports:
    - "9090:8080"
  environment:
    - OLLAMA_BASE_URL=http://ollama:11434
  depends_on:
    - ollama
```

Open WebUI is a ChatGPT-style interface that talks to your local Ollama instance. Navigate to `http://localhost:9090` after `docker compose up -d` and you can chat directly with any model you've pulled.

This is useful before committing a model to the pipeline. You can test `llama3.2` interactively — ask it call-center-style questions, probe its reasoning, check if its output format matches what the RAG prompt expects. Models have different personalities: some are verbose, some terse, some follow instructions precisely, some paraphrase the prompt back to you. Knowing what you're getting before it's embedded in production is worth the five minutes.

---

## Quality: An Honest Comparison

The capability gap between `gpt-4o-mini` and `llama3.2` is real, and it shows up in specific ways for this use case.

**Where Ollama holds up well:** Summarization. If the RAG pipeline retrieves the right documents (the embedding quality determines this), `llama3.2` does a reasonable job of synthesizing them into a coherent answer. The model isn't being asked to reason from scratch — it's being asked to read a set of transcripts and report what it finds. That's a task where a smaller model performs adequately.

**Where OpenAI pulls ahead:** Complex queries. "What patterns do you see in calls that escalated after 8pm compared to those that escalated during business hours?" requires the model to reason across multiple retrieved documents, identify trends, and produce a structured comparison. `gpt-4o-mini` handles this more reliably. `llama3.2` tends to produce flatter answers that restate the retrieved content without the synthesis layer.

**Embedding quality matters more than completion quality.** If the wrong documents are retrieved, even `gpt-4o-mini` can't produce a good answer — it'll hallucinate or tell you nothing useful was found. `nomic-embed-text` is a strong embedding model and performs comparably to `text-embedding-3-small` for most retrieval tasks in this domain. The quality difference between the two embedding models is smaller than the quality difference between the two completion models.

The practical implication: if you're optimizing for cost and data privacy, use Ollama for both embeddings and completions. If you need the best possible answers and can afford the API calls, use OpenAI for completions and you could argue for either embedding model. Don't mix embedding providers on the same collection.

---

## Adding a Third Provider

The pattern is designed to extend. Adding Anthropic or Gemini embeddings means editing two functions in one file. Here's what adding Anthropic's embedding model would look like in `get_embeddings()`:

```python
if provider == "anthropic":
    # Anthropic doesn't yet publish a standalone embeddings API;
    # this would use a third-party wrapper or voyage-ai embeddings
    from langchain_voyageai import VoyageAIEmbeddings
    return VoyageAIEmbeddings(
        model="voyage-3",
        api_key=os.getenv("VOYAGE_API_KEY"),
    )
```

And the corresponding completion model in `rag_query()`:

```python
if provider == "anthropic":
    from langchain_anthropic import ChatAnthropic
    llm = ChatAnthropic(
        model="claude-sonnet-4-5",
        api_key=os.getenv("ANTHROPIC_API_KEY"),
    )
```

That's the entire change. LangChain's provider abstractions (`ChatOpenAI`, `ChatOllama`, `ChatAnthropic`) all implement the same interface, so the `llm.invoke(prompt)` call in `rag_query()` works identically regardless of which object it gets.

The credit union's multi-vendor reality — Claude, OpenAI, Gemini running in parallel across different teams — is actually a good fit for this architecture. Each team's deployment can point at a different provider via environment variable. The MCP server code is identical. The data pipeline is identical. Only the provider config differs.

---

## When to Use Which

A decision guide based on what actually matters:

| Scenario | Recommendation |
|---|---|
| Development and prototyping | Ollama — no API costs, no key management, works offline |
| Regulated data that cannot leave the network | Ollama on self-hosted infrastructure |
| Best answer quality, cost is secondary | OpenAI (`gpt-4o-mini` + `text-embedding-3-small`) |
| High query volume, cost is primary | Ollama on adequately sized hardware |
| Client has Azure enterprise agreement | Azure OpenAI Service (same models, different endpoint) |

The one thing worth resisting: switching providers mid-project to chase marginal quality gains. Each switch is a re-ingest event and a validation exercise. The engineering cost is real, and the quality difference between well-tuned retrieval with `nomic-embed-text` and retrieval with `text-embedding-3-small` is usually smaller than the difference between good prompts and bad ones.

---

## What's Next

Post 5 completes the ingestion pipeline. There's a `NotImplementedError` sitting in `pipeline.py` — the S3 source path has always been a stub. We'll implement it using MinIO, a local S3-compatible Docker service, so the full stack runs without touching AWS. The same code connects to a real S3 bucket or Azure Blob container with one environment variable change.

To switch to Ollama locally right now:

```bash
docker compose up -d ollama
docker compose exec ollama ollama pull nomic-embed-text
docker compose exec ollama ollama pull llama3.2

# Update .env
echo "LLM_PROVIDER=ollama" >> .env

# Re-ingest with the local provider
python -m rag.pipeline --ingest

# Test
python -m rag.pipeline --query "fraud disputes where members were frustrated"
```
