"""Postgres checkpointer: an interrupted run resumes by thread_id, even after a restart.

Uses the Compose `db` (DATABASE_URL) with the saver's tables in a `langgraph`
schema. The saver takes no schema argument, so the schema comes from the
connection's search_path, and it is created first because the saver's DDL is
unqualified.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.conninfo import make_conninfo

SCHEMA = "langgraph"
DEFAULT_URL = "postgresql://postgres:postgres@localhost:5432/contactcenter"


def checkpoint_conninfo(database_url: str | None = None) -> str:
    url = database_url or os.getenv("DATABASE_URL", DEFAULT_URL)
    return make_conninfo(url, options=f"-c search_path={SCHEMA},public")


@asynccontextmanager
async def open_checkpointer(database_url: str | None = None) -> AsyncIterator[AsyncPostgresSaver]:
    """Create the schema and tables if needed (idempotent) and yield the saver."""
    conninfo = checkpoint_conninfo(database_url)
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    async with AsyncPostgresSaver.from_conn_string(conninfo) as saver:
        await saver.setup()
        yield saver
