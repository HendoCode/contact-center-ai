"""The offline golden-set run (`make evals`) inside `make test`: no network, no keys, no DB.

The first test is the same regression gate CI runs. The others show the evaluators bite
when the graph or its tools break, and cover the live run's offline-testable parts.
"""

import json
import socket
from types import SimpleNamespace

import pytest

pytest.importorskip("fastmcp", reason="install the agent group: uv sync --extra dev --group agent")

from evals import run as eval_run  # noqa: E402
from evals.offline import OfflineToolbox  # noqa: E402
from evals.run import (  # noqa: E402
    KNOWN_FAILURES_PATH, failures, gate, load_golden, run_offline, sync_dataset,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"network connection attempted: {args[1:]}")

    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture(scope="module")
def items():
    eval_run.ensure_corpus()
    return load_golden()


@pytest.mark.asyncio
async def test_offline_run_matches_the_known_failures_exactly(items):
    rows = await run_offline(items)
    known = json.loads(KNOWN_FAILURES_PATH.read_text())["failures"]

    assert len(rows) == len(items)
    assert gate(failures(rows), known) == []


@pytest.mark.asyncio
async def test_a_tool_that_drops_sql_fails_the_sql_check(items, monkeypatch):
    def no_sql(self, names):
        return "MetricFlow result:\n  x: 1"

    monkeypatch.setattr(OfflineToolbox, "_render", no_sql)
    found = failures(await run_offline(items))

    metric_ids = {i["id"] for i in items if i["expect_sql"]}
    assert {item for item, keys in found.items() if "sql" in keys} == metric_ids


@pytest.mark.asyncio
async def test_a_terms_file_that_never_matches_fails_the_interrupt_check(items, monkeypatch):
    import agent.subgraphs.analyst as analyst

    monkeypatch.setattr(analyst, "match_terms", lambda question, skip=(): [])
    found = failures(await run_offline(items))

    ambiguous = {i["id"] for i in items if i["kind"] == "ambiguous"}
    assert ambiguous <= {item for item, keys in found.items() if "interrupt" in keys}


@pytest.mark.asyncio
async def test_invented_citations_fail_the_citation_check(items, monkeypatch):
    original = OfflineToolbox._search_transcripts

    def invent(self, query, k=5):
        return original(self, query, k) + "\nAlso CALL-99999."

    monkeypatch.setattr(OfflineToolbox, "_search_transcripts", invent)
    found = failures(await run_offline(items))

    open_ids = {i["id"] for i in items if i["kind"] == "open"}
    assert open_ids <= {item for item, keys in found.items() if "citations" in keys}


def test_offline_main_passes_the_gate(capsys):
    assert eval_run.main([]) == 0
    assert "regression gate passed" in capsys.readouterr().out


# ── live-run parts that need no LangSmith ──────────────────────────────────────

class FakeClient:
    def __init__(self, existing: list[str] | None = None):
        self.datasets = {}
        if existing is not None:
            self.datasets["d"] = [SimpleNamespace(metadata={"golden_id": g}) for g in existing]
        self.created: list[dict] = []

    def has_dataset(self, *, dataset_name):
        return dataset_name in self.datasets

    def create_dataset(self, name, description=""):
        self.datasets[name] = []

    def list_examples(self, *, dataset_name):
        return list(self.datasets[dataset_name])

    def create_examples(self, *, dataset_name, examples):
        self.created.extend(examples)
        self.datasets[dataset_name] += [SimpleNamespace(metadata=e["metadata"]) for e in examples]


def test_sync_dataset_is_idempotent_by_golden_id(items):
    client = FakeClient()
    sync_dataset(client, items, "d")
    assert len(client.created) == len(items)
    first = client.created[0]
    assert set(first["inputs"]) == {"question", "clarify_with"}
    assert first["outputs"]["route"] == items[0]["route"]
    assert "question" not in first["outputs"]

    sync_dataset(client, items, "d")
    assert len(client.created) == len(items)  # nothing twice

    partial = FakeClient(existing=[i["id"] for i in items[:10]])
    sync_dataset(partial, items, "d")
    assert [e["metadata"]["golden_id"] for e in partial.created] == [i["id"] for i in items[10:]]


def test_live_run_refuses_without_a_langsmith_key(monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.setattr(eval_run, "load_dotenv", lambda: None)
    with pytest.raises(SystemExit, match="LANGSMITH_API_KEY"):
        eval_run.main(["--live"])


def test_live_rows_are_read_from_langsmith_results():
    result = {
        "example": SimpleNamespace(metadata={"golden_id": "g01", "kind": "ambiguous"}),
        "run": SimpleNamespace(error=None),
        "evaluation_results": {"results": [SimpleNamespace(key="route", score=True, comment=None)]},
    }
    assert eval_run._row(result) == {
        "id": "g01", "kind": "ambiguous", "error": None,
        "results": {"route": {"key": "route", "score": True, "comment": ""}},
    }
