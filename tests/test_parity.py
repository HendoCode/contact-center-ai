"""
Offline tests for `tools.parity`.

These tests do not need a warehouse, 1Password, or dbt; they exercise the CSV/SQL
parsing, numeric diff, manifest handling, and full `run_parity` orchestration
with fake targets.
"""

from pathlib import Path

import pytest

import tools.parity as parity


class TestParseCsvRow:
    def test_parses_integers_floats_and_strings(self, tmp_path: Path):
        csv_path = tmp_path / "mf.csv"
        csv_path.write_text("call_volume,average_rate,status\n1250,6.589,ok\n")
        row = parity.parse_csv_row(csv_path)
        assert row == {"call_volume": 1250, "average_rate": 6.589, "status": "ok"}

    def test_lower_cases_column_names(self, tmp_path: Path):
        csv_path = tmp_path / "mf.csv"
        csv_path.write_text("CALL_VOLUME,Banking_Balance\n1250,38287038.69\n")
        row = parity.parse_csv_row(csv_path)
        assert row == {"call_volume": 1250, "banking_balance": 38287038.69}


class TestExtractSql:
    def test_extracts_after_sql_header(self):
        out = "some spinner\n\n🔎 SQL (remove --explain to see data):\n\nSELECT 1\nFROM t\n"
        assert parity.extract_sql(out) == "SELECT 1\nFROM t"

    def test_falls_back_to_select(self):
        out = "noise\nSELECT a FROM b\n"
        assert parity.extract_sql(out) == "SELECT a FROM b"

    def test_returns_empty_when_no_sql(self):
        assert parity.extract_sql("just spinner output") == ""


class TestDiffMetric:
    def test_agreement_within_tolerance(self):
        values = {"postgres": 6.5888, "snowflake": 6.5888412, "databricks": 6.588841176}
        result = parity.diff_metric(values, 0.0001, list(values))
        assert result["max_diff"] == pytest.approx(4.12e-05)
        assert result["ok"] is True

    def test_mismatch_beyond_tolerance(self):
        values = {"postgres": 100.00, "snowflake": 100.01}
        result = parity.diff_metric(values, 0.005, list(values))
        assert result["ok"] is False
        assert result["max_diff"] == pytest.approx(0.01)

    def test_missing_target_fails(self):
        values = {"postgres": 1.0, "snowflake": 1.0}
        result = parity.diff_metric(values, 0.01, ["postgres", "snowflake", "databricks"])
        assert result["ok"] is False
        assert "missing targets" in result["note"]
        assert "databricks" in result["note"]

    def test_fewer_than_two_targets(self):
        result = parity.diff_metric({"postgres": 1.0}, 0.01, ["postgres"])
        assert result["ok"] is False
        assert "fewer than 2 targets" in result["note"]


class TestSanitizeSql:
    def test_redacts_1password_placeholder(self):
        assert parity.sanitize_sql("FROM `<concealed by 1Password>`.t") == "FROM `<name>`.t"

    def test_redacts_env_account_values(self, monkeypatch):
        monkeypatch.setenv("PARITY_TEST_ACCOUNT", "acct_12345")
        sql = "SELECT * FROM acct_12345.marts.f_interaction"
        assert parity.sanitize_sql(sql) == "SELECT * FROM <redacted>.marts.f_interaction"

    def test_redacts_snowflake_and_databricks_identifier_env_vars(self, monkeypatch):
        # These are the exact names that appear in generated SQL when credentials
        # come from plain environment variables rather than 1Password.
        env = {
            "SNOWFLAKE_DATABASE": "sfdb_123",
            "SNOWFLAKE_SCHEMA": "sfschema_456",
            "SNOWFLAKE_USER": "sfuser_789",
            "SNOWFLAKE_ROLE": "sfrole_012",
            "SNOWFLAKE_WAREHOUSE": "sfwh_345",
            "DATABRICKS_SCHEMA": "dbschema_678",
        }
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        sql = (
            "SELECT * FROM sfdb_123.sfschema_456.table_one "
            "WHERE user = 'sfuser_789' AND role = 'sfrole_012' "
            "AND warehouse = 'sfwh_345' AND other = 'dbschema_678'"
        )
        redacted = parity.sanitize_sql(sql)
        for value in env.values():
            assert value not in redacted
        assert redacted.count("<redacted>") >= 6

    def test_redacts_url_and_hostname(self, monkeypatch):
        monkeypatch.setenv("PARITY_TEST_HOST", "https://dbc-abc-123.cloud.databricks.com")
        sql = (
            "SELECT * FROM some_table "
            "WHERE host = 'https://dbc-abc-123.cloud.databricks.com' OR host = 'dbc-abc-123.cloud.databricks.com'"
        )
        redacted = parity.sanitize_sql(sql)
        assert "dbc-abc-123" not in redacted
        assert "<redacted>" in redacted


# ---------------------------------------------------------------------------
# run_parity orchestration with fake targets
# ---------------------------------------------------------------------------


def _fake_run_mf_target(values_by_target: dict, observed_manifests: list | None = None):
    """Return a run_mf_target replacement that writes fixture CSVs and records manifest snapshots."""
    def inner(target: str, metrics: list, csv_path: Path, profiles_dir: Path):
        manifest_path = parity.DBT_DIR / "target" / "semantic_manifest.json"
        if observed_manifests is not None:
            observed_manifests.append((target, manifest_path.read_text()))
        row = values_by_target.get(target, {"status": "error", "error": "not configured"})
        if row["status"] == "ok":
            header = ",".join(metrics)
            data = ",".join(str(row["values"].get(m, "")) for m in metrics)
            csv_path.write_text(f"{header}\n{data}\n")
        return row
    return inner


def _fake_target_config() -> dict:
    return {
        "profile_target": "fake_wh",
        "adapter": "fake",
        "groups": [],
        "wrapper": None,
    }


class TestRunParityFake:
    @pytest.fixture
    def fake_env(self, monkeypatch, tmp_path: Path):
        """Set up in-memory targets, metrics, and a temp dbt/results tree."""
        dbt_dir = tmp_path / "dbt"
        results_dir = tmp_path / "results"
        (dbt_dir / "target" / "fake_wh").mkdir(parents=True)
        (dbt_dir / "profiles.yml").write_text("ccai_semantic:\n  target: dev\n  outputs:\n    dev:\n")
        (dbt_dir / "target" / "semantic_manifest.json").write_text("ORIGINAL")
        (dbt_dir / "target" / "fake_wh" / "semantic_manifest.json").write_text("WAREHOUSE")

        monkeypatch.setattr(parity, "METRICS", {"m1": {"tolerance": 0}})
        monkeypatch.setattr(parity, "TARGETS", {
            "fake_a": _fake_target_config(),
            "fake_b": _fake_target_config(),
        })
        monkeypatch.setattr(parity, "DBT_DIR", dbt_dir)
        monkeypatch.setattr(parity, "RESULTS_DIR", results_dir)
        monkeypatch.setattr(parity, "check_postgres_reachable", lambda: True)
        return tmp_path

    def test_run_parity_succeeds_with_fake_targets(self, fake_env, monkeypatch):
        values = {
            "fake_a": {"status": "ok", "values": {"m1": 100}, "sql": "SELECT 100"},
            "fake_b": {"status": "ok", "values": {"m1": 100}, "sql": "SELECT 100"},
        }
        monkeypatch.setattr(parity, "run_mf_target", _fake_run_mf_target(values))

        result = parity.run_parity(["fake_a", "fake_b"])

        assert result["run_ok"] is True
        assert result["results"]["fake_a"]["status"] == "ok"
        assert result["results"]["fake_b"]["status"] == "ok"
        assert result["diff"]["m1"]["ok"] is True
        assert (parity.RESULTS_DIR / f"{parity._run_stamp()}_parity_fake_a.sql").exists()
        assert (parity.RESULTS_DIR / f"{parity._run_stamp()}_parity_fake_b.sql").exists()
        # Manifest restored to original.
        assert (parity.DBT_DIR / "target" / "semantic_manifest.json").read_text() == "ORIGINAL"

    def test_run_parity_fails_when_target_errors(self, fake_env, monkeypatch):
        values = {
            "fake_a": {"status": "ok", "values": {"m1": 100}, "sql": "SELECT 100"},
            "fake_b": {"status": "error", "error": "boom"},
        }
        monkeypatch.setattr(parity, "run_mf_target", _fake_run_mf_target(values))

        result = parity.run_parity(["fake_a", "fake_b"])

        assert result["run_ok"] is False
        assert result["results"]["fake_b"]["status"] == "error"
        record = parity.build_record(result, ["fake_a", "fake_b"])
        assert "Target errors" in record["notes"]

    def test_run_parity_fails_when_metric_missing_on_a_target(self, fake_env, monkeypatch):
        values = {
            "fake_a": {"status": "ok", "values": {"m1": 100}, "sql": "SELECT 100"},
            "fake_b": {"status": "ok", "values": {}, "sql": "SELECT 100"},
        }
        monkeypatch.setattr(parity, "run_mf_target", _fake_run_mf_target(values))

        result = parity.run_parity(["fake_a", "fake_b"])

        assert result["run_ok"] is False
        assert result["diff"]["m1"]["ok"] is False
        assert "missing targets" in result["diff"]["m1"]["note"]

    def test_manifest_restored_on_exception(self, fake_env, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError(" simulated failure")
        monkeypatch.setattr(parity, "run_mf_target", boom)

        with pytest.raises(RuntimeError):
            parity.run_parity(["fake_a"])

        assert (parity.DBT_DIR / "target" / "semantic_manifest.json").read_text() == "ORIGINAL"

    def test_manifest_removed_when_no_original_existed(self, monkeypatch, tmp_path: Path):
        dbt_dir = tmp_path / "dbt"
        results_dir = tmp_path / "results"
        (dbt_dir / "target" / "fake_wh").mkdir(parents=True)
        (dbt_dir / "profiles.yml").write_text("p:\n")
        (dbt_dir / "target" / "fake_wh" / "semantic_manifest.json").write_text("WAREHOUSE")

        monkeypatch.setattr(parity, "METRICS", {"m1": {"tolerance": 0}})
        monkeypatch.setattr(parity, "TARGETS", {"fake_a": _fake_target_config()})
        monkeypatch.setattr(parity, "DBT_DIR", dbt_dir)
        monkeypatch.setattr(parity, "RESULTS_DIR", results_dir)

        def boom(*args, **kwargs):
            raise RuntimeError("simulated failure")
        monkeypatch.setattr(parity, "run_mf_target", boom)

        with pytest.raises(RuntimeError):
            parity.run_parity(["fake_a"])

        assert not (dbt_dir / "target" / "semantic_manifest.json").exists()

    def test_postgres_runs_against_original_manifest_regardless_of_order(self, fake_env, monkeypatch):
        monkeypatch.setattr(parity, "TARGETS", {
            "fake_wh": _fake_target_config(),
            "postgres": {"profile_target": "dev", "adapter": "postgres", "groups": ["dbt"], "wrapper": None},
        })
        observed = []
        values = {
            "fake_wh": {"status": "ok", "values": {"m1": 100}, "sql": "SELECT 100"},
            "postgres": {"status": "ok", "values": {"m1": 100}, "sql": "SELECT 100"},
        }
        monkeypatch.setattr(parity, "run_mf_target", _fake_run_mf_target(values, observed))

        parity.run_parity(["fake_wh", "postgres"])

        assert len(observed) == 2
        assert observed[0] == ("fake_wh", "WAREHOUSE")
        assert observed[1] == ("postgres", "ORIGINAL")
        assert (parity.DBT_DIR / "target" / "semantic_manifest.json").read_text() == "ORIGINAL"


# ---------------------------------------------------------------------------
# Result record
# ---------------------------------------------------------------------------


def _diff_fixture(overrides: dict | None = None, targets=None) -> dict:
    overrides = overrides or {}
    targets = targets or ["postgres", "snowflake"]
    base = {
        "call_volume": {"values": {"postgres": 1250, "snowflake": 1250}, "max_diff": 0, "ok": True},
        "average_mortgage_note_rate": {"values": {"postgres": 6.5888, "snowflake": 6.5888412}, "max_diff": 4.12e-05, "ok": True},
        "banking_available_balance": {"values": {"postgres": 38287038.69, "snowflake": 38287038.69}, "max_diff": 0.0, "ok": True},
        "credit_card_outstanding": {"values": {"postgres": 2354869.83, "snowflake": 2354869.83}, "max_diff": 0.0, "ok": True},
        "first_contact_resolution_rate": {"values": {"postgres": 0.5752, "snowflake": 0.5752}, "max_diff": 0.0, "ok": True},
    }
    base.update(overrides)
    return base


class TestBuildRecord:
    def test_record_conforms_to_results_contract(self):
        values = {"call_volume": 1250, "average_mortgage_note_rate": 6.5888, "banking_available_balance": 38287038.69, "credit_card_outstanding": 2354869.83, "first_contact_resolution_rate": 0.5752}
        parity_result = {
            "results": {
                "postgres": {"status": "ok", "values": values},
                "snowflake": {"status": "ok", "values": values},
            },
            "diff": _diff_fixture(),
            "sql_files": {"postgres": "2026-10-06_parity_postgres.sql"},
            "run_ok": True,
        }
        record = parity.build_record(parity_result, ["postgres", "snowflake"])

        required = {"date", "git_sha", "area", "run", "hardware", "versions", "params", "metrics", "notes"}
        assert required <= set(record)
        assert record["area"] == "semantics"
        assert record["versions"]["python"]

        from tools.results import validate
        assert validate(record) == []

    def test_record_notes_mismatch(self):
        values_ok = {"call_volume": 1250, "average_mortgage_note_rate": 6.5888, "banking_available_balance": 38287038.69, "credit_card_outstanding": 2354869.83, "first_contact_resolution_rate": 0.5752}
        values_bad = dict(values_ok)
        values_bad["call_volume"] = 1251
        parity_result = {
            "results": {
                "postgres": {"status": "ok", "values": values_ok},
                "snowflake": {"status": "ok", "values": values_bad},
            },
            "diff": _diff_fixture({"call_volume": {"values": {"postgres": 1250, "snowflake": 1251}, "max_diff": 1, "ok": False}}, targets=["postgres", "snowflake"]),
            "sql_files": {},
            "run_ok": False,
        }
        record = parity.build_record(parity_result, ["postgres", "snowflake"])
        assert "Mismatches beyond tolerance or missing data" in record["notes"]
        assert record["metrics"]["call_volume"]["ok"] is False


class TestDryRun:
    def test_dry_run_exits_zero(self, capsys):
        assert parity.main(["--dry-run"]) == 0
        captured = capsys.readouterr()
        assert "Would run parity for targets" in captured.out


class TestMainExitCode:
    def test_main_returns_nonzero_when_one_of_two_targets_errors(self, monkeypatch, tmp_path: Path):
        dbt_dir = tmp_path / "dbt"
        results_dir = tmp_path / "results"
        (dbt_dir / "target" / "fake_wh").mkdir(parents=True)
        (dbt_dir / "profiles.yml").write_text("p:\n")
        (dbt_dir / "target" / "semantic_manifest.json").write_text("ORIGINAL")
        (dbt_dir / "target" / "fake_wh" / "semantic_manifest.json").write_text("WAREHOUSE")

        monkeypatch.setattr(parity, "METRICS", {"m1": {"tolerance": 0}})
        monkeypatch.setattr(parity, "TARGETS", {
            "fake_ok": _fake_target_config(),
            "fake_bad": _fake_target_config(),
        })
        monkeypatch.setattr(parity, "DBT_DIR", dbt_dir)
        monkeypatch.setattr(parity, "RESULTS_DIR", results_dir)
        monkeypatch.setattr(parity, "check_postgres_reachable", lambda: True)

        def fake_run(target, *args, **kwargs):
            if target == "fake_ok":
                return {"status": "ok", "values": {"m1": 100}, "sql": "SELECT 100"}
            return {"status": "error", "error": "boom"}

        monkeypatch.setattr(parity, "run_mf_target", fake_run)

        assert parity.main(["--targets", "fake_ok,fake_bad"]) == 1
