"""A warehouse-built semantic manifest is caught before mf runs it on local Postgres, and stored
eval outputs carry no home-directory paths. No dbt, mf or database needed."""

import json
import subprocess

import pytest

from ccai_mcp import metrics
from evals import preflight
from evals.run import redact_home

POSTGRES_REL = '"contactcenter"."marts"."f_account_snapshot"'
DATABRICKS_REL = "`ccai`.`marts`.`f_account_snapshot`"


def dbt_dir(tmp_path, monkeypatch, relation=POSTGRES_REL, adapter="postgres"):
    """A fake olap/dbt with target/ artifacts; REPO_ROOT moves too so messages stay relative."""
    root = tmp_path / "repo"
    target = root / "olap" / "dbt" / "target"
    target.mkdir(parents=True)
    (target / "semantic_manifest.json").write_text(json.dumps(
        {"semantic_models": [{"node_relation": {"relation_name": relation}}]}))
    if adapter:
        (target / "manifest.json").write_text(json.dumps({"metadata": {"adapter_type": adapter}}))
    monkeypatch.setattr(metrics, "REPO_ROOT", root)
    monkeypatch.setattr(metrics, "DBT_DIR", root / "olap" / "dbt")
    return root / "olap" / "dbt"


def test_dev_manifest_passes(tmp_path, monkeypatch):
    assert metrics.manifest_problem(dbt_dir(tmp_path, monkeypatch)) is None


def test_databricks_manifest_is_one_actionable_line(tmp_path, monkeypatch):
    problem = metrics.manifest_problem(dbt_dir(tmp_path, monkeypatch, DATABRICKS_REL, "databricks"))
    assert problem == ("olap/dbt/target/semantic_manifest.json was built for databricks, but metric "
                       "queries run on the local postgres: run 'cd olap/dbt && uv run --group dbt dbt "
                       "parse' to rebuild it for the local Postgres target")


def test_snowflake_manifest_is_caught_by_its_adapter(tmp_path, monkeypatch):
    problem = metrics.manifest_problem(dbt_dir(tmp_path, monkeypatch, adapter="snowflake"))
    assert "was built for snowflake" in problem


def test_backticks_without_manifest_json_mean_databricks(tmp_path, monkeypatch):
    problem = metrics.manifest_problem(dbt_dir(tmp_path, monkeypatch, DATABRICKS_REL, adapter=None))
    assert "was built for databricks" in problem


def test_missing_manifest_says_to_parse(tmp_path, monkeypatch):
    d = dbt_dir(tmp_path, monkeypatch)
    (d / "target" / "semantic_manifest.json").unlink()
    assert metrics.manifest_problem(d).startswith("no olap/dbt/target/semantic_manifest.json: run 'cd olap/dbt")


def test_query_metric_refuses_before_running_mf(tmp_path, monkeypatch):
    dbt_dir(tmp_path, monkeypatch, DATABRICKS_REL, "databricks")
    monkeypatch.setattr(metrics, "run_metricflow", lambda args: pytest.fail("mf must not run"))
    name = next(iter(metrics.load_metric_catalog()))
    with pytest.raises(RuntimeError, match="was built for databricks"):
        metrics.query_metric_raw([name])


def test_preflight_fails_on_a_warehouse_manifest(tmp_path, monkeypatch):
    dbt_dir(tmp_path, monkeypatch, DATABRICKS_REL, "databricks")
    with pytest.raises(preflight.PreflightError, match="was built for databricks"):
        preflight.check_manifest()


def test_preflight_passes_a_dev_manifest(tmp_path, monkeypatch):
    dbt_dir(tmp_path, monkeypatch)
    assert preflight.check_manifest() == "semantic manifest built for postgres"


def test_warehouse_build_writes_its_own_target_dir():
    out = subprocess.run(["make", "-n", "dbt-build", "WAREHOUSE=databricks", "DIRECT=1"],
                         capture_output=True, text=True, cwd=metrics.REPO_ROOT, check=True).stdout
    assert "--target databricks --target-path target/databricks" in out


def test_home_paths_are_redacted_everywhere():
    err = ("query_metric error: Database Error ... Artifact Path "
           "/home/stephen/code/contact-center-ai/olap/dbt/target/semantic_manifest.json")
    row = {"outputs": {"answer": err, "citations": ["/Users/alex/x.json", "CALL-00001"]},
           "results": {"judge": {"comment": "cites '/home/stephen/a'"}}, "id": "g01", "score": 0.25}
    out = redact_home(row)
    assert out["outputs"]["answer"].endswith("Artifact Path ~/code/contact-center-ai/olap/dbt/target/"
                                             "semantic_manifest.json")
    assert out["outputs"]["citations"] == ["~/x.json", "CALL-00001"]
    assert out["results"]["judge"]["comment"] == "cites '~/a'"
    assert out["score"] == 0.25 and out["id"] == "g01"
    assert "/home/" not in json.dumps(out) and "/Users/" not in json.dumps(out)
