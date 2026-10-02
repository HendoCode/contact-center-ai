"""Postgres checkpointer: connection string offline; a real resume against Compose's db (integration)."""

import uuid

import pytest
from agent_support import config
from langgraph.types import Command

from agent.checkpoint import SCHEMA, checkpoint_conninfo, open_checkpointer
from agent.graph import build_graph


def test_conninfo_puts_the_saver_in_the_langgraph_schema():
    info = checkpoint_conninfo("postgresql://u:p@db.example:5432/contactcenter")
    assert "dbname=contactcenter" in info
    assert f"search_path={SCHEMA},public" in info


def test_conninfo_reads_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@envhost:5432/x")
    assert "host=envhost" in checkpoint_conninfo()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_interrupted_run_resumes_from_postgres(toolbox):
    """Needs `docker compose up -d db`. A second saver stands in for a process restart."""
    from agent_support import FakeLLM, call_exists

    def graph_over(saver):
        return build_graph(
            get_llm=lambda: FakeLLM("resolve_metric"),
            toolbox=toolbox,
            call_exists=call_exists,
            checkpointer=saver,
        )

    cfg = config(str(uuid.uuid4()))
    async with open_checkpointer() as saver:
        first = await graph_over(saver).ainvoke({"question": "what is our average rate?"}, cfg)
        assert "__interrupt__" in first

    async with open_checkpointer() as saver:  # idempotent setup, fresh connection
        out = await graph_over(saver).ainvoke(
            Command(resume={"choices": ["average_deposit_apy"]}), cfg
        )
    assert out["metric_names"] == ["average_deposit_apy"]
