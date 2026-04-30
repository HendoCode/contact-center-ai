"""
pgvector setup and embedding helpers.

Handles:
- Database initialization (create pgvector extension + tables)
- Embedding model configuration (OpenAI by default, swappable)
- Vector store setup via LangChain
"""

import os
from langchain_openai import OpenAIEmbeddings
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
    Return the embedding model.

    Swap this function to change providers:
        from langchain_anthropic import AnthropicEmbeddings  (when available)
        from langchain_google_vertexai import VertexAIEmbeddings
    """
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
