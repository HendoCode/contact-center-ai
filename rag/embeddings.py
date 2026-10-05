"""
pgvector setup and embedding helpers.

Handles:
- Database initialization (create pgvector extension + tables)
- Embedding model configuration (OpenAI by default, swappable via LLM_PROVIDER)
- Vector store setup via LangChain
"""

import os
from langchain_postgres import PGVector
from langchain_postgres.vectorstores import PGVector

# ── Config ────────────────────────────────────────────────────────────────────

CONNECTION_STRING = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/contactcenter"
)

COLLECTION_NAME = os.getenv("COLLECTION_NAME", "call_transcripts")


def get_embeddings():
    """
    Return the embedding model based on EMBEDDING_PROVIDER env var
    (falling back to LLM_PROVIDER so the two stay swappable together).

    Embeddings are decoupled from the chat LLM on purpose: as of this
    writing OpenRouter serves chat completions but NO embedding models, so
    when chat is routed through OpenRouter the embeddings need a separate
    cheap/local path. The default ("openai") branch is any OpenAI-compatible
    embeddings endpoint via EMBEDDING_BASE_URL + EMBEDDING_MODEL; the
    recommended no-cost path for an OpenRouter chat setup is to set
    EMBEDDING_PROVIDER=ollama (local nomic-embed-text via Ollama).
    """
    provider = os.getenv(
        "EMBEDDING_PROVIDER", os.getenv("LLM_PROVIDER", "openai")
    ).lower()

    if provider == "ollama":
        from langchain_ollama import OllamaEmbeddings

        from rag.ollama_models import ensure_ready
        embeddings = OllamaEmbeddings(
            model=os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text"),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        )
        ensure_ready(embeddings)  # pulls the model if the server lacks it
        return embeddings

    from langchain_openai import OpenAIEmbeddings
    return OpenAIEmbeddings(
        model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("EMBEDDING_BASE_URL"),
    )


def get_vector_store(embeddings=None) -> PGVector:
    """
    Return a configured PGVector store.

    Creates the pgvector extension and collection table on first run.
    """
    if embeddings is None:
        embeddings = get_embeddings()

    return PGVector(
        embeddings=embeddings,
        collection_name=COLLECTION_NAME,
        connection=CONNECTION_STRING,
        use_jsonb=True,
    )
