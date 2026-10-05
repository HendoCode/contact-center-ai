"""
MCP tool definitions — these are the capabilities exposed to any MCP client.

Tools:
    search_transcripts   — semantic search over call transcripts
    get_call_summary     — summarize a specific call by ID
    query_csat           — query CSAT data with optional filters
"""

import os

import psycopg2

from psycopg2 import errors as pg_errors

from rag.embeddings import CONNECTION_STRING
from rag.pipeline import rag_query, get_llm
from retrieval import get_retriever


RETRIEVAL_MODES = ("vector", "fts", "hybrid")


def retrieval_mode() -> str:
    """The search mode for search_transcripts: AGENT_RETRIEVAL_MODE, default vector (pgvector
    serves vector only; fts and hybrid need RETRIEVER_BACKEND=lancedb)."""
    mode = os.getenv("AGENT_RETRIEVAL_MODE", "vector").strip().lower() or "vector"
    if mode not in RETRIEVAL_MODES:
        raise ValueError(f"AGENT_RETRIEVAL_MODE={mode!r}: use one of {', '.join(RETRIEVAL_MODES)}")
    return mode


def search_transcripts(query: str, k: int = 5) -> str:
    """
    Semantic search over call transcripts.

    Args:
        query: Natural language question or search phrase
        k: Number of transcripts to retrieve (default 5)

    Returns:
        AI-generated answer grounded in retrieved transcripts
    """
    return rag_query(query, k=k, mode=retrieval_mode())


def get_call_summary(call_id: str) -> str:
    """
    Retrieve and summarize a specific call by its ID.

    Args:
        call_id: The call identifier (e.g. CALL-00042)

    Returns:
        Summary of the call transcript, or an error if not found
    """
    hit = get_retriever().get_by_id(call_id)

    if hit is None:
        return f"No transcript found for call ID: {call_id}"

    meta = hit.metadata
    prompt = f"""Summarize this call concisely: what was the member's issue, how did the agent handle it, and what was the outcome?

Call ID: {meta.get('call_id')}
Date: {meta.get('date')}
Category: {meta.get('category')}
Outcome: {meta.get('outcome')}

TRANSCRIPT:
{hit.text}"""

    response = get_llm().invoke(prompt)
    return response.content


def query_csat(
    min_score: int | None = None,
    max_score: int | None = None,
    category: str | None = None,
) -> str:
    """
    Query CSAT survey results with optional filters.

    Args:
        min_score: Minimum CSAT score (1-5)
        max_score: Maximum CSAT score (1-5)
        category: Filter by call category (e.g. "fraud_dispute")

    Returns:
        Summary of CSAT results matching the filters
    """
    # Load CSAT data from Postgres (the OLTP csat_survey source / the dbt
    # f_csat fact), not from csat.json on disk.
    where_clauses = []
    params = []
    if min_score is not None:
        where_clauses.append("score >= %s")
        params.append(min_score)
    if max_score is not None:
        where_clauses.append("score <= %s")
        params.append(max_score)
    if category:
        where_clauses.append("category_code = %s")
        params.append(category)
    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    with psycopg2.connect(CONNECTION_STRING) as conn, conn.cursor() as cur:
        try:
            cur.execute(
                "SELECT score, comment, category_code "
                f"FROM marts.f_csat{where_sql}",
                params,
            )
        except pg_errors.UndefinedTable:
            # marts schema not built yet — fall back to the OLTP source
            # (csat_survey joined to interaction for the category).
            cur.execute(
                "SELECT c.score, c.comment, i.category_code "
                "FROM csat_survey AS c "
                "JOIN interaction AS i ON i.interaction_id = c.interaction_id"
                f"{where_sql}",
                params,
            )
        rows = cur.fetchall()

    if not rows:
        return "No CSAT results found matching the given filters."

    avg_score = sum(r[0] for r in rows) / len(rows)
    score_dist = {i: sum(1 for r in rows if r[0] == i) for i in range(1, 6)}
    sample_comments = [r[1] for r in rows[:5] if r[1]]

    return (
        f"CSAT Summary ({len(rows)} responses)\n"
        f"Average score: {avg_score:.2f}/5\n"
        f"Score distribution: {score_dist}\n"
        f"Sample comments:\n" +
        "\n".join(f"  - {c}" for c in sample_comments)
    )
