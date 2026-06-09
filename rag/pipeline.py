"""
Main RAG pipeline: ingest, embed, store, and retrieve call center data.

Usage:
    # Ingest synthetic data into vector store
    python rag/pipeline.py --ingest

    # Query the vector store
    python rag/pipeline.py --query "What were common fraud complaints last month?"
"""

import argparse
import json
import os
from pathlib import Path

from langchain_core.documents import Document

from rag.embeddings import get_vector_store, get_embeddings


# ── Ingest ────────────────────────────────────────────────────────────────────

def load_synthetic_data() -> list[dict]:
    """Load synthetic transcripts from data/synthetic/transcripts.json."""
    data_path = Path(__file__).parent.parent / "data" / "synthetic" / "transcripts.json"
    if not data_path.exists():
        raise FileNotFoundError(
            f"Synthetic data not found at {data_path}. "
            "Run: python data/synthetic/generate_data.py"
        )
    with open(data_path) as f:
        return json.load(f)


def transcripts_to_documents(transcripts: list[dict]) -> list[Document]:
    """Convert transcript records to LangChain Document objects."""
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


def ingest(source: str = "synthetic"):
    """
    Ingest call transcripts into pgvector.

    Args:
        source: "synthetic" (local JSON) or "s3" (TODO: S3 integration)
    """
    print(f"Loading transcripts from source: {source}")

    if source == "synthetic":
        transcripts = load_synthetic_data()
    elif source == "s3":
        # TODO: implement S3 ingestion
        # from rag.s3_loader import load_from_s3
        # transcripts = load_from_s3(bucket=os.getenv("S3_BUCKET_NAME"), prefix=os.getenv("S3_PREFIX"))
        raise NotImplementedError("S3 ingestion not yet implemented.")
    else:
        raise ValueError(f"Unknown source: {source}")

    docs = transcripts_to_documents(transcripts)
    print(f"Embedding and storing {len(docs)} documents...")

    vector_store = get_vector_store()
    vector_store.add_documents(docs)

    print(f"Ingestion complete. {len(docs)} documents stored in pgvector.")


# ── Retrieve ──────────────────────────────────────────────────────────────────

def retrieve(query: str, k: int = 5) -> list[Document]:
    """Retrieve the top-k most relevant call transcripts for a query."""
    vector_store = get_vector_store()
    return vector_store.similarity_search(query, k=k)


# ── RAG query ─────────────────────────────────────────────────────────────────

def rag_query(query: str, k: int = 5) -> str:
    """
    Run a full RAG query: retrieve relevant transcripts, then generate a response.

    This is the core function exposed by the MCP tools.
    """
    docs = retrieve(query, k=k)

    context = "\n\n---\n\n".join(
        f"Call ID: {doc.metadata['call_id']}\n"
        f"Date: {doc.metadata['date']}\n"
        f"Category: {doc.metadata['category']}\n"
        f"Outcome: {doc.metadata['outcome']}\n\n"
        f"{doc.page_content}"
        for doc in docs
    )

    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    if provider == "ollama":
        from langchain_ollama import ChatOllama
        llm = ChatOllama(
            model=os.getenv("OLLAMA_MODEL", "llama3.2"),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        )
    else:
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            api_key=os.getenv("OPENAI_API_KEY"),
        )

    prompt = f"""You are an assistant helping contact center supervisors understand call patterns and member issues.

Based on the following call transcripts, answer the question below. Be specific and reference call IDs where relevant.

TRANSCRIPTS:
{context}

QUESTION: {query}

ANSWER:"""

    response = llm.invoke(prompt)
    return response.content


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAG pipeline for call center data")
    parser.add_argument("--ingest", action="store_true", help="Ingest data into vector store")
    parser.add_argument("--source", default="synthetic", help="Data source: synthetic or s3")
    parser.add_argument("--query", type=str, help="Query to run against the vector store")
    args = parser.parse_args()

    if args.ingest:
        ingest(source=args.source)
    elif args.query:
        print(rag_query(args.query))
    else:
        parser.print_help()
