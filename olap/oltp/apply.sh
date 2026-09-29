#!/usr/bin/env bash
#
# Apply the OLTP schema to Postgres.
#
# Default target is the docker-compose `db` service (pgvector/pgvector:pg16)
# exposed on localhost:5432; override with DATABASE_URL if you run Postgres
# elsewhere. Safe to re-run (CREATE ... IF NOT EXISTS throughout).
#
# Usage:
#   olap/oltp/apply.sh                # docker `db` service first, then psql
#   DATABASE_URL=... olap/oltp/apply.sh
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA="$DIR/schema.sql"
DB_URL="${DATABASE_URL:-postgresql://postgres:postgres@localhost:5432/contactcenter}"

apply_via_docker() {
    docker compose exec -T db psql -v ON_ERROR_STOP=1 \
        -U postgres -d contactcenter < "$SCHEMA"
}

apply_via_psql() {
    if ! command -v psql >/dev/null 2>&1; then
        echo "apply.sh: neither a running docker `db` service nor psql is available" >&2
        exit 1
    fi
    psql -v ON_ERROR_STOP=1 "$DB_URL" -f "$SCHEMA"
}

# Prefer the docker service when it is actually up; fall back to host psql
# (which can reach that same db via the published 5432 port, or any other
# Postgres pointed at by DATABASE_URL).
if command -v docker >/dev/null 2>&1 \
   && (docker compose -f "$(git rev-parse --show-toplevel 2>/dev/null || pwd)/docker-compose.yml" ps db --format '{{.Status}}' 2>/dev/null | grep -q 'Up'); then
    echo "apply.sh: applying $SCHEMA to docker-compose db service"
    apply_via_docker
else
    echo "apply.sh: applying $SCHEMA via psql ($DB_URL)"
    apply_via_psql
fi

echo "apply.sh: done."