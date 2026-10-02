# syntax=docker/dockerfile:1
#
# The one app image. Every Compose app service (seed, dbt, langgraph-api, demo, evals)
# runs from it with a different command; the MCP server is spawned inside it as a
# stdio subprocess by the agent (there is no separate mcp-server service until M1).
#
# Dependencies come from uv.lock: the base install, the dev extra (pytest) and the
# agent and dbt groups. The lance and finetune groups stay out.
FROM python:3.12-slim

# psql is for olap/oltp/apply.sh, which falls back to `psql $DATABASE_URL` when there is
# no docker CLI (always the case inside a container).
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /uvx /bin/

WORKDIR /app
# ccai_mcp/metrics.py looks for /app/.venv/bin/mf; keeping the venv at /app/.venv
# makes that lookup, and PATH, agree.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

# Dependencies first so editing source does not re-resolve the environment.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --extra dev --group agent --group dbt

COPY . .
RUN uv sync --frozen --extra dev --group agent --group dbt
