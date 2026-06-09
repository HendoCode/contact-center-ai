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

COLLECTION_NAME = "call_transcripts"


def get_embeddings():
    """
    Return the embedding model based on LLM_PROVIDER env var."""
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
