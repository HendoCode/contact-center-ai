"""
pgvector setup and embedding helpers.

Handles:
- Database initialization (create pgvector extension + tables)
- Embedding model configuration (OpenAI by default, swappable via LLM_PROVIDER)
- Vector store setup via LangChain
"""

import os
import threading
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


# langchain_postgres defines its SQLAlchemy tables on the first PGVector it builds, behind
# an unlocked module-level check. Two builds at once in one process (two concurrent tool
# calls in the MCP server, which runs tools in threads) both define them, and every one
# after the first fails with "Table 'langchain_pg_collection' is already defined for this
# MetaData instance". So builds go through one lock, and the default store is built once
# per process and collection, then reused.
_STORE_LOCK = threading.Lock()
_STORES: dict[tuple[str, str], PGVector] = {}


def get_vector_store(embeddings=None) -> PGVector:
    """
    Return a configured PGVector store.

    Creates the pgvector extension and collection table on first run. Without
    `embeddings`, the store for the configured model is built once per process and reused;
    with `embeddings` (e.g. ingest's cached model) a new store is built, under the same lock.
    """
    with _STORE_LOCK:
        if embeddings is not None:
            return _build_store(embeddings)
        key = (CONNECTION_STRING, COLLECTION_NAME)
        if key not in _STORES:
            _STORES[key] = _build_store(get_embeddings())
        return _STORES[key]


def _build_store(embeddings) -> PGVector:
    return PGVector(
        embeddings=embeddings,
        collection_name=COLLECTION_NAME,
        connection=CONNECTION_STRING,
        use_jsonb=True,
    )
