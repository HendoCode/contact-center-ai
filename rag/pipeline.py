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


# ── LLM ───────────────────────────────────────────────────────────────────────

def get_llm():
    """
    Return the chat LLM based on LLM_PROVIDER env var."""
    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=os.getenv("OLLAMA_MODEL", "llama3.2"),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        )
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model="gpt-4o-mini",
        api_key=os.getenv("OPENAI_API_KEY"),
    )


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


def _load_olap_ids() -> tuple[dict[str, int | None], dict[int, list[int]]]:
    """
    Build OLTP id lookups so transcript metadata can carry the same ids the
    OLAP star schema uses.

    Returns:
        call_id -> interaction_id (or None if interactions.json missing)
        interaction_id -> [account_id, ...] (subject role first, then others)
    """
    data_dir = Path(__file__).parent.parent / "data" / "synthetic"

    interaction_by_call: dict[str, int | None] = {}
    interactions_path = data_dir / "interactions.json"
    if interactions_path.exists():
        with open(interactions_path) as f:
            for row in json.load(f):
                interaction_by_call[row["call_id"]] = row["interaction_id"]

    account_ids: dict[int, list[int]] = {}
    accounts_path = data_dir / "interaction_accounts.json"
    if accounts_path.exists():
        with open(accounts_path) as f:
            rows = json.load(f)
        # Bucket subject-role accounts first so they are the canonical `account_id`.
        for role in ("subject", "referenced"):
            for row in rows:
                if row["account_role"] != role:
                    continue
                account_ids.setdefault(row["interaction_id"], []).append(row["account_id"])

    return interaction_by_call, account_ids


def transcripts_to_documents(transcripts: list[dict]) -> list[Document]:
    """Convert transcript records to LangChain Document objects.

    Metadata carries the OLTP ids (interaction_id + account_id) alongside the
    existing string ids so RAG answers and OLAP aggregates can cite the same
    member/account.
    """
    interaction_by_call, account_ids = _load_olap_ids()
    docs = []
    for t in transcripts:
        metadata = {
            "call_id": t["call_id"],
            "date": t["date"],
            "duration_seconds": t["duration_seconds"],
            "category": t["category"],
            "outcome": t["outcome"],
            "member_id": t["member_id"],
            "agent_id": t["agent_id"],
        }
        interaction_id = interaction_by_call.get(t["call_id"])
        if interaction_id is not None:
            metadata["interaction_id"] = interaction_id
            accts = account_ids.get(interaction_id, [])
            if accts:
                metadata["account_id"] = accts[0]
                metadata["account_ids"] = accts
        docs.append(Document(page_content=t["full_text"], metadata=metadata))
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

    llm = get_llm()

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
    parser.add_argument("--reset", action="store_true", help="Delete the vector store collection")
    args = parser.parse_args()

    if args.reset:
        get_vector_store().delete_collection()
        print("Collection deleted. Re-run --ingest to rebuild it.")
    elif args.ingest:
        ingest(source=args.source)
    elif args.query:
        print(rag_query(args.query))
    else:
        parser.print_help()
