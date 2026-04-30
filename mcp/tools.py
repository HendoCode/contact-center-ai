"""
MCP tool definitions — these are the capabilities exposed to any MCP client.

Tools:
    search_transcripts   — semantic search over call transcripts
    get_call_summary     — summarize a specific call by ID
    query_csat           — query CSAT data with optional filters
"""

import json
from pathlib import Path
from rag.pipeline import rag_query, retrieve


def search_transcripts(query: str, k: int = 5) -> str:
    """
    Semantic search over call transcripts.

    Args:
        query: Natural language question or search phrase
        k: Number of transcripts to retrieve (default 5)

    Returns:
        AI-generated answer grounded in retrieved transcripts
    """
    return rag_query(query, k=k)


def get_call_summary(call_id: str) -> str:
    """
    Retrieve and summarize a specific call by its ID.

    Args:
        call_id: The call identifier (e.g. CALL-00042)

    Returns:
        Summary of the call transcript, or an error if not found
    """
    results = retrieve(f"call_id:{call_id}", k=10)

    # Filter to exact call_id match
    matches = [doc for doc in results if doc.metadata.get("call_id") == call_id]

    if not matches:
        return f"No transcript found for call ID: {call_id}"

    doc = matches[0]
    return rag_query(
        f"Summarize this call concisely: what was the member's issue, how did the agent handle it, and what was the outcome?",
        k=1,
    )


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
    # Load CSAT data
    csat_path = Path(__file__).parent.parent / "data" / "synthetic" / "csat.json"
    if not csat_path.exists():
        return "CSAT data not found. Run: python data/synthetic/generate_data.py"

    with open(csat_path) as f:
        csat_data = json.load(f)

    # Apply filters
    filtered = csat_data
    if min_score is not None:
        filtered = [r for r in filtered if r["score"] >= min_score]
    if max_score is not None:
        filtered = [r for r in filtered if r["score"] <= max_score]

    if not filtered:
        return "No CSAT results found matching the given filters."

    avg_score = sum(r["score"] for r in filtered) / len(filtered)
    score_dist = {i: sum(1 for r in filtered if r["score"] == i) for i in range(1, 6)}
    sample_comments = [r["comment"] for r in filtered[:5]]

    return (
        f"CSAT Summary ({len(filtered)} responses)\n"
        f"Average score: {avg_score:.2f}/5\n"
        f"Score distribution: {score_dist}\n"
        f"Sample comments:\n" +
        "\n".join(f"  - {c}" for c in sample_comments)
    )
