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

from models.providers import get_llm, reasoning_effort, with_output_cap  # noqa: F401  (re-exported)
from retrieval import Hit, get_retriever
from retrieval.base import backend_name


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


SYNTHETIC_DIR = Path(__file__).parent.parent / "data" / "synthetic"


def _load_olap_ids(data_dir: Path = SYNTHETIC_DIR) -> tuple[dict[str, int | None], dict[int, list[int]]]:
    """
    Build OLTP id lookups so transcript metadata can carry the same ids the
    OLAP star schema uses.

    Returns:
        call_id -> interaction_id (or None if interactions.json missing)
        interaction_id -> [account_id, ...] (subject role first, then others)
    """
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


def _load_home_branches(data_dir: Path = SYNTHETIC_DIR) -> dict[str, str]:
    """member_id ("MBR-000012") -> home branch, from members.json (empty if it is missing)."""
    path = data_dir / "members.json"
    if not path.exists():
        return {}
    with open(path) as f:
        return {f"MBR-{m['member_id']:06d}": m["home_branch"]
                for m in json.load(f) if m.get("home_branch")}


def transcripts_to_documents(transcripts: list[dict], data_dir: Path = SYNTHETIC_DIR) -> list[Document]:
    """Convert transcript records to LangChain Document objects.

    Metadata carries the OLTP ids (interaction_id + account_id) alongside the
    existing string ids so RAG answers and OLAP aggregates can cite the same
    member/account.
    """
    interaction_by_call, account_ids = _load_olap_ids(data_dir)
    home_branches = _load_home_branches(data_dir)
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
        # A fact about the member (where they bank), so it is filterable like a category.
        if t["member_id"] in home_branches:
            metadata["home_branch"] = home_branches[t["member_id"]]
        interaction_id = interaction_by_call.get(t["call_id"])
        if interaction_id is not None:
            metadata["interaction_id"] = interaction_id
            accts = account_ids.get(interaction_id, [])
            if accts:
                metadata["account_id"] = accts[0]
                metadata["account_ids"] = accts
        docs.append(Document(page_content=t["full_text"], metadata=metadata))
    return docs


def load_csat_surveys(docs: list[Document], connect=None) -> dict[str, tuple[int, str | None]]:
    """call_id -> (score, surveyed_at ISO date) from the Postgres `csat_survey` table.

    Calls are matched through the `interaction_id` the documents carry (falling back to the
    number in the call id, as `olap/seed.py` does); a call with no survey has no entry.
    """
    import psycopg2

    from retrieval.pgvector_backend import CONNECTION_STRING

    by_interaction = {}
    for d in docs:
        call_id = d.metadata["call_id"]
        by_interaction[d.metadata.get("interaction_id") or int(call_id.rsplit("-", 1)[1])] = call_id
    with (connect or (lambda: psycopg2.connect(CONNECTION_STRING)))() as conn, conn.cursor() as cur:
        cur.execute("SELECT interaction_id, score, surveyed_at FROM csat_survey")
        rows = cur.fetchall()
    return {
        by_interaction[iid]: (int(score), day.isoformat() if day else None)
        for iid, score, day in rows
        if iid in by_interaction
    }


def ingest(source: str = "synthetic", use_cache: bool = True):
    """
    Ingest call transcripts into the vector store.

    Args:
        source: "synthetic" (local JSON) or "s3" (TODO: S3 integration)
        use_cache: read and fill the on-disk embedding cache (.cache/embeddings/) that
            `make bench` shares, so only transcripts never embedded before hit the model.
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

    from rag.embedding_cache import CachedEmbeddings, cache_file_for, embed_progress
    from rag.embeddings import get_embeddings

    embeddings = get_embeddings()  # an Ollama model is pulled here if missing
    cached = CachedEmbeddings(embeddings, cache_file=cache_file_for(embeddings) if use_cache else None,
                              label="ingest")
    cached.warm_documents((d.page_content for d in docs), progress=embed_progress("ingest"))

    retriever = get_retriever(embeddings=cached)
    written = retriever.ingest(
        [{"call_id": d.metadata["call_id"], "text": d.page_content, "metadata": d.metadata}
         for d in docs]
    )

    print(f"Ingestion complete. {written} documents upserted ({retriever.count()} total, {retriever.name}).")
    if hasattr(retriever, "set_csat"):
        # Runs after every ingest, before the tag: a rebuilt table or newly inserted rows get their
        # scores here, and the seed tag names a table that has them.
        scored = retriever.set_csat(load_csat_surveys(docs))
        print(f"CSAT scores attached to {scored} documents (from csat_survey).")
    if hasattr(retriever, "tag_seed"):  # versioned stores (LanceDB) name this snapshot
        print(f"Tagged the store {retriever.tag_seed()} (roll back with `make lance-rollback TAG=...`).")


# ── Retrieve ──────────────────────────────────────────────────────────────────

def retrieve(query: str, k: int = 5, where: dict | None = None, mode: str = "vector") -> list[Hit]:
    """Retrieve the top-k most relevant call transcripts for a query."""
    return get_retriever().search(query, k=k, where=where, mode=mode)


# ── RAG query ─────────────────────────────────────────────────────────────────

def csat_scope(where: dict | None) -> str:
    """The survey filter in words ("with a CSAT score of 1 to 2"), or "" when there is none."""
    cond = (where or {}).get("csat_score")
    if cond is None:
        return ""
    if not isinstance(cond, dict):
        return f"with a CSAT score of {cond}"
    lo, hi = cond.get("gte"), cond.get("lte")
    if lo is not None and hi is not None:
        return f"with a CSAT score of {lo} to {hi}" if lo != hi else f"with a CSAT score of {lo}"
    if lo is not None:
        return f"with a CSAT score of {lo} or higher"
    if hi is not None:
        return f"with a CSAT score of {hi} or lower"
    return "within the given CSAT filter"


def sample_statement(hits: list[Hit], count, where: dict | None = None) -> str:
    """What the answer rests on: N retrieved calls out of M in their category, from
    `count(where)` over the same store, e.g. "Based on 5 of 143 calls in fraud_dispute".

    With a CSAT filter in `where`, M counts only the surveyed calls that pass it, and the
    statement says so ("... in fee_dispute with a CSAT score of 1 to 2")."""
    scope = csat_scope(where)
    csat_filter = {"csat_score": where["csat_score"]} if scope else {}
    n = len(hits)
    if not n:
        return "No matching calls were retrieved."
    by_cat: dict[str, int] = {}
    for hit in hits:
        cat = hit.metadata.get("category")
        by_cat[cat] = by_cat.get(cat, 0) + 1
    parts = []
    for cat, got in sorted(by_cat.items(), key=lambda kv: (-kv[1], str(kv[0]))):
        try:
            total = count({"category": cat, **csat_filter}) if cat is not None else None
        except Exception:  # noqa: BLE001 - a size we cannot count is said so, never guessed
            total = None
        label = f"calls in {cat}" if cat is not None else "calls with no category"
        label = f"{label} {scope}" if scope else label
        parts.append(f"{got} of {total} {label}" if total is not None else f"{got} {label} (category size unavailable)")
    return f"Based on a sample of {n} retrieved call{'s' if n != 1 else ''}: " + "; ".join(parts) + "."


def rag_query(query: str, k: int = 5, mode: str = "vector", where: dict | None = None) -> str:
    """
    Run a full RAG query: retrieve relevant transcripts, then generate a response.

    The response opens with the sample statement (how many calls it read, out of how many
    in each category), so a few retrieved calls are never presented as a whole category.

    `where` is the portable filter of `retrieval.base` (for example `{"csat_score": {"lte": 2}}`,
    LanceDB only); it is applied before the top-k is taken.

    This is the core function exposed by the MCP tools.
    """
    retriever = get_retriever()
    hits = retriever.search(query, k=k, where=where, mode=mode)
    sample = sample_statement(hits, retriever.count, where)
    scope = csat_scope(where)
    if scope:
        sample += f" Only calls {scope} were searched."

    context = "\n\n---\n\n".join(
        f"Call ID: {hit.call_id}\n"
        f"Date: {hit.metadata['date']}\n"
        f"Category: {hit.metadata['category']}\n"
        f"Outcome: {hit.metadata['outcome']}\n"
        + (f"CSAT score: {hit.metadata['csat_score']}\n" if hit.metadata.get("csat_score") else "")
        + f"\n{hit.text}"
        for hit in hits
    )

    llm = get_llm()

    prompt = f"""You are an assistant helping contact center supervisors understand call patterns and member issues.

Based on the following call transcripts, answer the question below. Be specific and reference call IDs where relevant.
These transcripts are a retrieved sample, not every call: {sample} Describe what this sample shows; do not say "all calls" or "every call" about the whole category.

TRANSCRIPTS:
{context}

QUESTION: {query}

ANSWER:"""

    response = llm.invoke(prompt)
    return f"{sample}\n\n{response.content}"


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAG pipeline for call center data")
    parser.add_argument("--ingest", action="store_true", help="Ingest data into vector store")
    parser.add_argument("--source", default="synthetic", help="Data source: synthetic or s3")
    parser.add_argument("--query", type=str, help="Query to run against the vector store")
    parser.add_argument("--reset", action="store_true", help="Delete the vector store collection")
    parser.add_argument("--no-cache", action="store_true",
                        help="ingest: do not read or write the embedding cache (.cache/embeddings/)")
    args = parser.parse_args()

    from rag.ollama_models import OllamaUnavailable

    try:
        if args.reset:
            if backend_name() == "lancedb":
                get_retriever().reset()
            else:
                from retrieval.pgvector_backend import get_vector_store
                get_vector_store().delete_collection()
            print("Collection deleted. Re-run --ingest to rebuild it.")
        elif args.ingest:
            ingest(source=args.source, use_cache=not args.no_cache)
        elif args.query:
            print(rag_query(args.query))
        else:
            parser.print_help()
    except OllamaUnavailable as e:
        raise SystemExit(f"rag: {e}") from None
