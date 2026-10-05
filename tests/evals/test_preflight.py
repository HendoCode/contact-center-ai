"""The pure parts of evals.preflight: cost arithmetic, model pricing choice, and the
missing-prerequisite messages. No network: prices are a stub table, and the Postgres and
Ollama checks point at a closed local port."""

import json
import socket

import pytest

from evals import preflight
from evals.preflight import PreflightError, Usage

PRICES = {"z-ai/glm-5.3-flash": (0.15e-6, 0.50e-6), "anthropic/claude-opus-5.5": (4e-6, 20e-6)}
FLAT = {"agent": Usage(calls=(2, 4), input_tokens=(1_000, 2_000), output_tokens=(100, 200)),
        "judge": Usage(calls=(1, 1), input_tokens=(1_000, 1_000), output_tokens=(100, 100))}


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_role_cost_low_and_high():
    # 10 questions x 2 calls x (1000 x 4e-6 + 100 x 20e-6) = 0.12; high 10 x 4 x (0.008 + 0.004) = 0.48
    low, high = preflight.role_cost((4e-6, 20e-6), FLAT["agent"], 10)
    assert low == pytest.approx(0.12)
    assert high == pytest.approx(0.48)


def test_estimate_sums_roles_and_reports_each():
    low, high, lines = preflight.estimate(
        PRICES, {"agent": "z-ai/glm-5.3-flash", "judge": "anthropic/claude-opus-5.5"}, 10, FLAT)
    agent_low, agent_high = preflight.role_cost(PRICES["z-ai/glm-5.3-flash"], FLAT["agent"], 10)
    judge = preflight.role_cost(PRICES["anthropic/claude-opus-5.5"], FLAT["judge"], 10)
    assert low == pytest.approx(agent_low + judge[0])
    assert high == pytest.approx(agent_high + judge[1])
    assert lines[1] == "judge: anthropic/claude-opus-5.5 at $4.00/$20.00 per M in/out tokens -> $0.06 to $0.06"


def test_estimate_leaves_out_unpriced_roles():
    low, high, lines = preflight.estimate(PRICES, {"agent": None, "judge": "nobody/unknown"}, 10, FLAT)
    assert (low, high) == (0, 0)
    assert lines == ["agent: not priced (not served through OpenRouter)",
                     "judge: nobody/unknown not in OpenRouter's model list, not priced"]


def test_assumptions_are_labeled():
    assert preflight.assumption_text(FLAT) == (
        "assumed per question: agent 2-4 call(s) x 1,000-2,000 in / 100-200 out tokens;"
        " judge 1 call(s) x 1,000-1,000 in / 100-100 out tokens")


def test_priced_models_follow_the_providers():
    env = {"LLM_PROVIDER": "openai", "EVAL_JUDGE_PROVIDER": "openai",
           "EVAL_JUDGE_MODEL": "anthropic/claude-opus-5.5"}
    assert preflight.priced_models(env) == {"agent": "z-ai/glm-5.3-flash", "judge": "anthropic/claude-opus-5.5"}
    assert preflight.priced_models({**env, "LLM_PROVIDER": "ollama"})["agent"] is None
    assert preflight.priced_models({**env, "LLM_BASE_URL": "http://gw/v1"}) == {"agent": None, "judge": None}
    assert preflight.priced_models({"EVAL_JUDGE_PROVIDER": "anthropic"})["judge"] is None


def test_golden_count_matches_the_set():
    assert preflight.golden_count() == 51


def test_postgres_unreachable_message():
    url = f"postgresql://postgres:postgres@127.0.0.1:{closed_port()}/contactcenter"
    with pytest.raises(PreflightError, match=r"Postgres is not reachable at DATABASE_URL .*run 'make up'"):
        preflight.check_postgres(url, "call_transcripts")


def test_ollama_unreachable_message():
    # The real ollama client against a closed port; the fake-client cases are in test_ollama_models.
    with pytest.raises(PreflightError, match=r"Ollama is not reachable at http://127.0.0.1:\d+: .*make up"):
        preflight.check_ollama(f"http://127.0.0.1:{closed_port()}", "nomic-embed-text")


def test_judge_equal_to_agent_is_refused(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "anthropic/claude-opus-5.5")
    monkeypatch.setenv("EVAL_JUDGE_PROVIDER", "openai")
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "anthropic/claude-opus-5.5")
    with pytest.raises(PreflightError, match="the judge model .* is the agent's model"):
        preflight.check_models_differ()


def test_checks_skip_postgres_and_ollama_when_not_used(monkeypatch):
    monkeypatch.setattr(preflight, "check_models_differ", lambda: "models ok")
    monkeypatch.setattr(preflight, "check_manifest", lambda: "manifest ok")
    monkeypatch.setattr(preflight, "mart_relations", lambda path: [])
    monkeypatch.setattr(preflight, "check_marts", lambda rels, conninfo: "marts ok")
    monkeypatch.setattr(preflight, "check_postgres", lambda *a: pytest.fail("pgvector not in use"))
    monkeypatch.setattr(preflight, "check_ollama", lambda *a: pytest.fail("ollama not in use"))
    env = {"RETRIEVER_BACKEND": "lancedb", "EMBEDDING_PROVIDER": "openai"}
    assert list(preflight.run_checks(env)) == ["models ok", "manifest ok", "marts ok"]


def test_mart_relations_come_from_the_semantic_manifest(tmp_path):
    f = tmp_path / "semantic_manifest.json"
    rel = lambda schema, alias: {"node_relation": {"schema_name": schema, "alias": alias}}  # noqa: E731
    f.write_text(json.dumps({"semantic_models": [rel("marts", "f_transaction"), rel("marts", "d_account"),
                                                 rel("marts", "f_transaction")]}))
    assert preflight.mart_relations(f) == [("marts", "d_account"), ("marts", "f_transaction")]


def test_dev_conninfo_follows_the_dbt_profile_defaults():
    assert preflight.dev_conninfo({}) == ("host=localhost port=5432 user=postgres password=postgres "
                                          "dbname=contactcenter")
    assert "host=db port=5433" in preflight.dev_conninfo({"DBT_HOST": "db", "DBT_PORT": "5433"})


class _FakeConn:
    def __init__(self, existing):
        self.existing, self.last = existing, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params):
        self.last = params[0]

    def fetchone(self):
        return (self.last if self.last in self.existing else None,)


def test_missing_marts_is_one_line_naming_the_build(monkeypatch):
    import psycopg

    have = {'"marts"."d_account"'}
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: _FakeConn(have))
    rels = [("marts", "d_account"), ("marts", "f_transaction"), ("marts", "f_interaction"),
            ("marts", "f_account_snapshot"), ("marts", "f_loan")]
    with pytest.raises(PreflightError) as err:
        preflight.check_marts(rels, "host=x")
    assert str(err.value) == ("marts not built (marts.f_transaction, marts.f_interaction, marts.f_account_snapshot "
                              "and 1 more missing): run 'make dbt-build-dev' (or cd olap/dbt && uv run --group dbt "
                              "dbt build)")


def test_built_marts_pass(monkeypatch):
    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: _FakeConn({'"marts"."f_transaction"'}))
    assert preflight.check_marts([("marts", "f_transaction")], "host=x") == \
        "marts built: 1 tables the metric tool queries exist"


def test_unreachable_dev_postgres_says_make_up():
    with pytest.raises(PreflightError, match=r"dbt dev Postgres is not reachable .*make up"):
        preflight.check_marts([("marts", "f_transaction")],
                              f"host=127.0.0.1 port={closed_port()} user=postgres dbname=x")
