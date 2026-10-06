"""
Offline tests for `tools.parity`.

These tests do not need a warehouse, 1Password, or dbt; they exercise the CSV/SQL
parsing, numeric diff, and result-record building with fixture data.
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
        assert parity.diff_metric(values, 0.0001) == {"max_diff": pytest.approx(4.12e-05), "ok": True}

    def test_mismatch_beyond_tolerance(self):
        values = {"postgres": 100.00, "snowflake": 100.01}
        result = parity.diff_metric(values, 0.005)
        assert result["ok"] is False
        assert result["max_diff"] == pytest.approx(0.01)

    def test_fewer_than_two_targets(self):
        assert parity.diff_metric({"postgres": 1.0}, 0.01) == {
            "max_diff": None,
            "ok": False,
            "note": "fewer than 2 targets returned numbers",
        }


def _diff_fixture(overrides: dict | None = None) -> dict:
    overrides = overrides or {}
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
        }
        record = parity.build_record(parity_result, ["postgres", "snowflake"])

        # §4.3 contract keys
        required = {"date", "git_sha", "area", "run", "hardware", "versions", "params", "metrics", "notes"}
        assert required <= set(record)
        assert record["area"] == "semantics"
        assert record["versions"]["python"]

        # Validate against the repo's results contract.
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
            "diff": _diff_fixture({"call_volume": {"values": {"postgres": 1250, "snowflake": 1251}, "max_diff": 1, "ok": False}}),
            "sql_files": {},
        }
        record = parity.build_record(parity_result, ["postgres", "snowflake"])
        assert "Mismatches beyond tolerance" in record["notes"]
        assert record["metrics"]["call_volume"]["ok"] is False


class TestDryRun:
    def test_dry_run_exits_zero(self, capsys):
        assert parity.main(["--dry-run"]) == 0
        captured = capsys.readouterr()
        assert "Would run parity for targets" in captured.out
