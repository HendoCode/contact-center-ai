"""What the generator writes is what the change-analysis code reads."""

import pytest

pytest.importorskip("fastmcp", reason="install the agent group: uv sync --extra dev --group agent")

from agent.changes import load_branches, load_change_log  # noqa: E402
from agent.subgraphs.change import load_subjects  # noqa: E402
from ccai_mcp.change import load_change_log as tool_change_log  # noqa: E402


def test_the_agent_and_the_tool_read_the_generated_change_log(corpus, events_config):
    log = load_change_log(corpus.dir)

    assert log == tool_change_log(corpus.dir)
    assert [(e["date"], e["name"]) for e in log] == sorted(
        (str(ev["date"]), ev["change_log_name"]) for ev in events_config["events"]
    )


def test_the_agent_reads_the_generated_branches(corpus, events_config):
    assert load_branches(corpus.dir) == list(events_config["branches"])


def test_every_call_category_has_a_subject_phrase(corpus):
    mapped = {c for _, categories in load_subjects() for c in categories}
    generated = {row["category_code"] for row in corpus.load("interaction_categories.json")}

    assert mapped == generated  # a question about any kind of call can be narrowed to it
