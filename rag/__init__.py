"""RAG pipeline package.

Loads .env at import time so the pipeline CLI, the MCP server, and the
tools all see the same configuration before any env var is read.
"""

from dotenv import load_dotenv

load_dotenv()
