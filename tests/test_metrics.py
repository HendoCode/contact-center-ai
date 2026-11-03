"""
Tests for the semantic-layer MCP tools (query_metric, ask_the_analyst) and the
Postgres-backed query_csat.

Integration tests require the local prerequisites (the `mf` CLI, a built dbt
semantic manifest, and a reachable Postgres); they skip cleanly otherwise.
"""

import json
import shutil

import pytest

from ccai_mcp import metrics
from ccai_mcp.tools import query_csat
from rag.embeddings import CONNECTION_STRING


def _mf_available() -> bool:
    return (metrics.REPO_ROOT / ".venv" / "bin" / "mf").exists() or shutil.which("mf") is not None


def _manifest_available() -> bool:
    return (metrics.DBT_DIR / "target" / "semantic_manifest.json").exists()


def _db_available() -> bool:
    try:
        import psycopg2
        with psycopg2.connect(CONNECTION_STRING):
            return True
    except Exception:
        return False


def _require_prereqs():
    if not (_mf_available() and _manifest_available() and _db_available()):
        pytest.skip("MetricFlow/prereqs unavailable: needs mf CLI, built dbt manifest, Postgres")


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    def __init__(self, content):
        self._content = content

    def invoke(self, prompt):
        return _FakeResponse(self._content)


RATE_METRICS = [
    "average_mortgage_note_rate",
    "average_credit_card_purchase_apr",
    "average_deposit_apy",
    "average_heloc_current_rate",
    "average_investment_return_pct",
]


def test_query_metric_returns_number_and_sql():
    _require_prereqs()
    raw = metrics.query_metric_raw(
        ["average_mortgage_note_rate", "banking_available_balance"]
    )
    assert raw["columns"] == ["average_mortgage_note_rate", "banking_available_balance"]
    assert len(raw["rows"]) == 1
    assert float(raw["rows"][0][0]) == pytest.approx(6.5888, abs=0.001)
    assert float(raw["rows"][0][1]) == pytest.approx(38287038.69, abs=1)
    assert raw["sql"].strip().upper().startswith(("WITH", "SELECT"))


def test_query_metric_renders_table_and_sql():
    _require_prereqs()
    out = metrics.query_metric(
        ["average_mortgage_note_rate", "banking_available_balance"], decimals=4
    )
    assert "6.5888" in out
    assert "38287038.6900" in out
    assert "Generated SQL:" in out


def test_query_metric_rejects_undeclared_metric():
    _require_prereqs()
    with pytest.raises(ValueError, match="Unknown metric"):
        metrics.query_metric_raw(["interest_rate"])


def test_resolve_metrics_maps_interest_rate_to_declared_metrics(monkeypatch):
    # Pure resolution parse — no DB needed; only the catalog + fake LLM.
    monkeypatch.setattr(metrics, "get_llm", lambda: _FakeLLM(json.dumps(RATE_METRICS)))
    resolved = metrics.resolve_metrics("what is our average interest rate?")
    assert resolved == RATE_METRICS


def test_resolve_metrics_drops_bare_ambiguous_name(monkeypatch):
    monkeypatch.setattr(
        metrics,
        "get_llm",
        lambda: _FakeLLM(json.dumps(["interest_rate", "average_mortgage_note_rate"])),
    )
    resolved = metrics.resolve_metrics("what is our average interest rate?")
    assert resolved == ["average_mortgage_note_rate"]


def test_ask_the_analyst_returns_grounded_answer(monkeypatch):
    _require_prereqs()
    monkeypatch.setattr(metrics, "get_llm", lambda: _FakeLLM(json.dumps(RATE_METRICS)))
    out = metrics.ask_the_analyst("what is our average interest rate?")
    for name in RATE_METRICS:
        assert name in out
    assert "net_member_liquidity" not in out  # not the balance blend
    assert "Generated SQL:" in out
    for name in RATE_METRICS:
        assert f"- {name} —" in out  # one description line per metric


def _csat_rows(category: str | None = None) -> list[int]:
    """Scores straight from the seeded OLTP source, independent of the tool under test."""
    import psycopg2
    sql = ("SELECT c.score FROM csat_survey AS c "
           "JOIN interaction AS i ON i.interaction_id = c.interaction_id")
    params: tuple = ()
    if category:
        sql += " WHERE i.category_code = %s"
        params = (category,)
    with psycopg2.connect(CONNECTION_STRING) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [row[0] for row in cur.fetchall()]


@pytest.mark.integration
def test_query_csat_reads_postgres():
    if not _db_available():
        pytest.skip("Postgres unavailable")
    scores = _csat_rows()
    if not scores:
        pytest.skip("csat_survey is empty; run make dev-data")
    out = query_csat()
    assert f"{len(scores)} responses" in out
    assert f"Average score: {sum(scores) / len(scores):.2f}/5" in out

    category = "escrow_analysis"
    scoped_scores = _csat_rows(category)
    scoped = query_csat(category=category)
    assert f"{len(scoped_scores)} responses" in scoped
    assert f"{sum(scoped_scores) / len(scoped_scores):.2f}/5" in scoped
