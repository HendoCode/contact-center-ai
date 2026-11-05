# syntax=docker/dockerfile:1
#
# The one app image. Every Compose app service (seed, dbt, langgraph-api, mcp-server, demo,
# evals) runs from it with a different command. The agent spawns the MCP server inside it
# as a stdio subprocess; the `mcp-server` service runs `python -m ccai_mcp.server --http`.
#
# Dependencies come from uv.lock: the base install, the dev extra (pytest) and the
# agent, dbt, notebooks (JupyterLab) and lance (LanceDB, the Compose transcript store) groups.
# The finetune group stays out.
#
# Optional build-time mirrors (values from a git-ignored .env; empty by default, which means the
# public sources):
# - GHCR_MIRROR (build arg): a registry host that mirrors ghcr.io. An ARG can't go directly in
#   `COPY --from`, hence the named stage. It's cache-neutral: the same image digest yields the
#   same cache key either way.
# - APT_MIRROR (BuildKit *secret*, not a build arg): a base URL replacing `http://deb.debian.org`
#   in the Debian sources. Build-arg values are part of the layer cache key, so an arg would
#   stop hosts with and without a mirror from sharing the registry build cache
#   (docker-compose.cache.yml); secret contents are not.
# There's no PyPI mirror on purpose: `uv sync --frozen` downloads the exact URLs recorded in
# uv.lock, whatever index is configured, so the uv cache mount below is what saves re-downloads.
ARG GHCR_MIRROR=
FROM ${GHCR_MIRROR:-ghcr.io}/astral-sh/uv:0.12.22 AS uv

FROM python:3.12-slim

# psql is for olap/oltp/apply.sh, which falls back to `psql $DATABASE_URL` when there is
# no docker CLI (always the case inside a container).
#
# apt's archives and lists sit on BuildKit cache mounts, so a rebuild of this layer reuses the
# .debs it already downloaded. The image's docker-clean hook (which deletes them) and, when a
# mirror is set, the original Debian sources are set aside and restored in the same step, so the
# shipped image is apt-wise identical to a plain build. `--no-cache` bypasses the mounts too.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    --mount=type=secret,id=apt_mirror,env=APT_MIRROR,required=false \
    mv /etc/apt/apt.conf.d/docker-clean /tmp/docker-clean.orig \
    && echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/zz-build-keep-debs \
    && if [ -n "$APT_MIRROR" ]; then \
      cp /etc/apt/sources.list.d/debian.sources /tmp/debian.sources.orig \
      && sed -i "s|http://deb.debian.org|${APT_MIRROR%/}|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm /etc/apt/apt.conf.d/zz-build-keep-debs \
    && mv /tmp/docker-clean.orig /etc/apt/apt.conf.d/docker-clean \
    && if [ -f /tmp/debian.sources.orig ]; then \
      mv /tmp/debian.sources.orig /etc/apt/sources.list.d/debian.sources; \
    fi

COPY --from=uv /uv /uvx /bin/

WORKDIR /app
# ccai_mcp/metrics.py looks for /app/.venv/bin/mf; keeping the venv at /app/.venv
# makes that lookup, and PATH, agree.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

# uv's package cache (/root/.cache/uv, ~0.8 GB after a sync) is mounted as a BuildKit cache
# instead of being written into the layer, so it never lands in the image. The venv is copied
# out of the cache, so it stays complete without the cache.
#
# Dependencies first so editing source does not re-resolve the environment.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --extra dev --group agent --group dbt --group notebooks --group lance

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra dev --group agent --group dbt --group notebooks --group lance
