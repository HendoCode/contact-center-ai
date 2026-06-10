---
title: "From Text to Vectors"
subtitle: "Building the RAG data pipeline: synthetic transcripts, embeddings, and pgvector"
author: Stephen Henderson
date: "June 2026"
post_number: "02"
description: "Post 2 of Anchoring AI: how 150 synthetic call transcripts go from a Python script to searchable embeddings in PostgreSQL, and what LangChain is actually doing along the way."
prev_url: "../01-the-blueprint/"
prev_title: "The Blueprint"
next_url: "../03-the-interface-layer/"
next_title: "The Interface Layer"
---

Here's the core claim of any RAG system: *the right information is already in your data — you just need a better way to find it.*

The "better way" is semantic search. Instead of looking for the exact words a user typed, semantic search looks for meaning. A supervisor who asks "show me calls where members were angry about fees" won't necessarily get results containing those exact words. They'll get results where the *meaning* matches — members complaining about unexpected charges, expressing frustration with maintenance fees, threatening to close their accounts. The system understands the *intent* of the query and finds conceptually similar content.

This post explains how that works from the ground up, starting with raw data and ending with a vector store ready to answer natural-language questions.

---

## The Data

Everything starts with `data/synthetic/generate_data.py`. In a real deployment, this would be replaced by an actual call recording pipeline — S3, a transcription service, some ingestion scheduler. For this portfolio project, we generate 150 fake but structurally realistic calls with Faker.

Each call is assigned one of eight categories:

```python
CATEGORIES = [
    "loan_inquiry",
    "account_balance",
    "fraud_dispute",
    "card_replacement",
    "payment_assistance",
    "online_banking_support",
    "mortgage_inquiry",
    "account_opening",
]
```

And one of four outcomes: `resolved`, `escalated`, `callback_scheduled`, `unresolved`. The outcome matters because it's used to weight the CSAT scores — a resolved call skews toward 4-5 stars, an unresolved one toward 1-2. That correlation is intentional: it makes the CSAT data analytically interesting, and later (Post 8) we'll use it as a ground-truth signal for evaluating whether the AI system's answers are actually useful.

The generator builds a `full_text` field by joining the dialogue turns:

```python
"full_text": " ".join(
    f"{t['speaker'].upper()}: {t['text']}" for t in turns
)
```

A fraud dispute call ends up looking like this:

```
MEMBER: I'm seeing a charge on my account I don't recognize.
It's for $84.50 from somewhere called TechMerch Online.
AGENT: I understand how concerning that can be. Let me pull up
your account and look at that transaction.
MEMBER: I definitely didn't make that purchase.
AGENT: I can see the transaction from yesterday. I'm going to go
ahead and initiate a dispute for you and issue a replacement card...
```

This is the text that gets embedded. Not the metadata — the actual conversation. That distinction matters more than it might seem, and we'll get to it shortly.

---

## The Structure That Makes Search Possible

Before any text can be embedded, it needs to be wrapped in a structure that carries both content and context. LangChain uses a `Document` object for this:

```python
def transcripts_to_documents(transcripts: list[dict]) -> list[Document]:
    docs = []
    for t in transcripts:
        docs.append(Document(
            page_content=t["full_text"],
            metadata={
                "call_id": t["call_id"],
                "date": t["date"],
                "duration_seconds": t["duration_seconds"],
                "category": t["category"],
                "outcome": t["outcome"],
                "member_id": t["member_id"],
                "agent_id": t["agent_id"],
            }
        ))
    return docs
```

The split between `page_content` and `metadata` is load-bearing.

**`page_content`** is what gets embedded. It's the text the model converts to a vector. This needs to be semantically rich — the actual conversation, not just identifiers. You wouldn't embed `"CALL-00042, fraud_dispute, resolved"` and expect useful similarity search results.

**`metadata`** is structured data that travels alongside the embedding but isn't embedded itself. It's stored as JSONB in PostgreSQL. When a query retrieves the top-5 similar calls, each returned Document still carries its `call_id`, `category`, `outcome`, and so on. The LLM prompt we build later uses this structured metadata to give the model more context than just the raw transcript text.

One detail worth noticing: `member_name` is deliberately absent from the metadata. The full name is used during generation (Faker generates it), but it's not stored in the vector database. In the real engagement, the member's identity is irrelevant to pattern analysis — we care about categories, outcomes, and call content, not which specific member was on the line.

---

## What Are Embeddings, Actually?

An embedding is a list of floating-point numbers that encodes the *meaning* of a piece of text. OpenAI's `text-embedding-3-small` converts any text into a list of 1,536 numbers. Two pieces of text with similar meaning produce vectors that are close together in that 1,536-dimensional space.

The similarity measure we use is cosine similarity — it measures the angle between two vectors, not the distance. Two vectors pointing in roughly the same direction are semantically similar, regardless of their length.

Why does this matter? Consider two transcript excerpts:

- "I'm disputing a charge I don't recognize on my debit card."
- "There's a transaction on my account that I didn't make."

Keyword search finds neither when you search for "unauthorized transaction." Cosine similarity between their embeddings and the query embedding is high, because the *meaning* overlaps significantly. The model has learned that "charge I don't recognize," "transaction I didn't make," and "unauthorized transaction" are semantically equivalent.

This is what makes the call center use case work. Supervisors don't search for specific phrases. They ask questions like "what drove our escalations last week?" — and the system needs to understand that `escalated` outcomes, frustrated dialogue, and phrases like "I need to speak to a manager" all point to the same underlying concept.

---

## The Provider Abstraction

Embedding model selection lives in exactly one place — `rag/embeddings.py`:

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

That's 15 lines. It's the only place in the entire codebase that knows which embedding model is in use. Everything else — the ingestion pipeline, the retrieval step, the MCP tools — calls `get_embeddings()` and gets whatever it returns. Swap `LLM_PROVIDER=ollama` into your environment and the whole system switches to running locally without a single line of code changing.

There's a catch, though, and it's important: **you can't swap embedding models on an existing collection without re-ingesting everything.** `text-embedding-3-small` produces 1,536-dimensional vectors. Ollama's `nomic-embed-text` produces 768-dimensional vectors. If you embed your transcripts with OpenAI and then try to query with Ollama embeddings, you're comparing apples to oranges — the dimensions don't even match. The pgvector index would reject the query outright.

The practical consequence: pick your embedding model before you ingest, and treat a provider change as a full re-ingest event. We'll cover this more in Post 4 when we walk through the Ollama setup.

---

## pgvector: Why PostgreSQL?

The vector store is PostgreSQL with the `pgvector` extension. The choice warrants a sentence: why not a dedicated vector database like Pinecone, Weaviate, or Chroma?

For this use case, the answer is operational simplicity. The system already needs PostgreSQL for the App Service deployment on Azure (Azure Database for PostgreSQL Flexible Server). Adding a separate vector database would mean another managed service, another connection string, another thing to monitor. pgvector gives us vector similarity search as an extension on top of a database we'd need anyway.

The `get_vector_store()` function wires this up:

```python
def get_vector_store(embeddings=None) -> PGVector:
    if embeddings is None:
        embeddings = get_embeddings()

    return PGVector(
        embeddings=embeddings,
        collection_name=COLLECTION_NAME,
        connection=CONNECTION_STRING,
        use_jsonb=True,
    )
```

`use_jsonb=True` tells LangChain to store document metadata as JSONB rather than individual columns. This means you can add fields to your metadata schema without running migrations. The downside is that JSONB metadata isn't as efficient to filter on as a proper indexed column — something worth revisiting if query-time metadata filtering becomes a bottleneck.

On first run, the PGVector wrapper automatically creates the `pgvector` extension in PostgreSQL and initializes the `langchain_pg_collection` and `langchain_pg_embedding` tables. You don't need to run any migrations manually. On subsequent runs, it connects to the existing tables.

---

## Ingestion: Walking Through the Code

With all the pieces in place, the ingestion path is three steps:

```python
def ingest(source: str = "synthetic"):
    transcripts = load_synthetic_data()       # 1. load
    docs = transcripts_to_documents(transcripts)  # 2. convert
    vector_store = get_vector_store()
    vector_store.add_documents(docs)          # 3. embed + store
```

Step 3 is where the API calls happen. `add_documents()` sends each document's `page_content` to the embedding model, receives a 1,536-dimensional float vector, and writes it to PostgreSQL alongside the JSONB metadata. For 150 documents using OpenAI's API, this takes a few seconds and costs less than a cent. With Ollama running locally, it takes longer and costs nothing.

Run it with:

```bash
python -m rag.pipeline --ingest
```

---

## The Deduplication Problem

There's no deduplication guard in `ingest()`. Run it twice and you'll have 300 rows in pgvector — two copies of every document. This doesn't break the system outright, but it does degrade search quality. Duplicate documents inflate the retrieval results, and when the same call transcript appears twice in the context window the LLM sees, you're wasting tokens and potentially confusing the model.

For a portfolio project, this is an acceptable trade-off. For production, you'd want one of three fixes:

1. **Upsert by call_id** — check if a document with that metadata already exists before inserting. LangChain's PGVector doesn't expose a native upsert, but you can implement it by deleting existing rows by `call_id` before re-inserting.
2. **Idempotency hash** — hash the `full_text` and skip documents where that hash already exists in the collection.
3. **Truncate and re-ingest** — the blunt approach. Drop the collection entirely and re-embed from scratch. Acceptable if ingest is fast and a brief outage is tolerable.

---

## Querying: What Happens When You Call `retrieve()`

```python
def retrieve(query: str, k: int = 5) -> list[Document]:
    vector_store = get_vector_store()
    return vector_store.similarity_search(query, k=k)
```

Behind the scenes, `similarity_search()` does three things:

1. **Embeds the query** — sends the query string to the same embedding model used at ingest time, gets back a 1,536-dimensional vector
2. **Runs an ANN search** — executes a PostgreSQL query that finds the `k` rows in the `langchain_pg_embedding` table with the smallest cosine distance to the query vector
3. **Deserializes and returns** — reconstructs the LangChain `Document` objects, including the JSONB metadata, and returns them

The SQL pgvector runs under the hood looks roughly like:

```sql
SELECT document, cmetadata, embedding <=> $1 AS distance
FROM langchain_pg_embedding
WHERE collection_id = $2
ORDER BY distance
LIMIT $3;
```

The `<=>` operator is pgvector's cosine distance operator. The result is the `k` transcript documents whose embedding vectors are closest in angle to the query embedding — which is to say, the `k` calls most semantically similar to the question being asked.

Those documents are then passed to `rag_query()`, formatted into a context block, and sent to the LLM with the question. The LLM's job is to read the retrieved transcripts and synthesize a grounded answer — one that's anchored to the actual call data rather than generated from nothing.

---

## What's Next

The pipeline we've built here is the foundation. In Post 3, we'll look at the MCP server that sits in front of it — how `search_transcripts`, `get_call_summary`, and `query_csat` are defined, how they route to this pipeline, and how Claude Desktop connects to them.

If you want to run what we've built so far:

```bash
docker compose up -d
uv sync --extra dev
python data/synthetic/generate_data.py
python -m rag.pipeline --ingest
python -m rag.pipeline --query "fraud disputes where the member was frustrated"
```

The last command runs a full RAG cycle — embeds the query, retrieves the top-5 most similar transcripts, and returns an LLM-generated answer grounded in the actual call data.
