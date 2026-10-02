"""LangSmith tracing: on by env, off otherwise.

LangChain and LangGraph trace through LangSmith on their own when the environment asks
for it, so the agent adds no tracing code to the graph:

    LANGSMITH_TRACING=true          the switch; unset or anything else means off
    LANGSMITH_API_KEY=...           where the traces are sent
    LANGSMITH_PROJECT=...           the LangSmith project they land in (default "default")

`.env.example` ships the switch off. The test suite and the offline `make evals` force it
off with `disable_tracing()`, whatever the shell or `.env` says, so neither ever sends a
trace or needs a key.
"""

import os

from langsmith import utils as ls_utils

# LangSmith reads LANGSMITH_* first, then the older LANGCHAIN_* names, and TRACING_V2
# before TRACING; any of them set to "true" turns tracing on.
TRACING_FLAGS = (
    "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGCHAIN_TRACING",
)


def tracing_enabled() -> bool:
    return ls_utils.tracing_is_enabled() is True


def tracing_status() -> str:
    if not tracing_enabled():
        return "off"
    project = ls_utils.get_tracer_project()
    key = "set" if ls_utils.get_api_key(None) else "MISSING"
    return f"on (project {project}, LANGSMITH_API_KEY {key})"


def disable_tracing() -> None:
    """Turn tracing off for this process, overriding the shell and `.env` (which
    `load_dotenv` never overrides once a variable is set)."""
    for name in TRACING_FLAGS:
        os.environ[name] = "false"
    # LangSmith caches env lookups; drop the cache so the change takes effect.
    ls_utils.get_env_var.cache_clear()
