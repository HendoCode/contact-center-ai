"""Fail-fast check of the configured transcript store, for the HTTP `/healthz` probe.

Every transcript tool goes through `get_retriever()`. When the store behind it is empty
(a fresh Lance volume, a seed that has not run) each lookup answers "No transcript found",
which looks like a data problem rather than a deployment one. This turns that state into an
unhealthy probe instead.
"""

import logging
import os

from retrieval import get_retriever
from retrieval.base import backend_name

logger = logging.getLogger("ccai_mcp.store")


def store_problem() -> str | None:
    """None when the configured store holds at least one transcript, else a short reason."""
    if (os.getenv("MCP_STORE_CHECK") or "").strip().lower() in ("off", "0", "false", "no"):
        return None
    backend = backend_name()
    try:
        rows = get_retriever().count()
    except Exception as exc:  # unreadable for any reason: missing package, bad URI, no database
        return f"{backend} store unreadable ({type(exc).__name__}: {exc})"
    if rows == 0:
        return f"{backend} store is empty"
    return None
