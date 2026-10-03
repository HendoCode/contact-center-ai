"""Offline tests for tools/bootstrap-databricks.sh: no workspace, no 1Password.

Pure functions are exercised by sourcing the script. The full flow runs against a fake
`curl` on PATH that answers like the Databricks REST API.
"""

import json
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "bootstrap-databricks.sh"

FAKE_CURL = r"""#!/usr/bin/env python3
# Minimal Databricks REST stand-in. DENY names a statement prefix that fails as denied.
import json, os, sys
args = sys.argv[1:]
url = next(a for a in args if a.startswith("https://"))
method = args[args.index("-X") + 1]
data = args[args.index("--data-binary") + 1] if "--data-binary" in args else ""
auth = open(args[args.index("-H") + 1][1:]).read()
with open(os.environ["CURL_LOG"], "a") as log:
    log.write(json.dumps({"method": method, "url": url, "data": data, "argv": args}) + "\n")
def reply(body, code=200):
    print(json.dumps(body)); print(code, end="")
if "Bearer good-token" not in auth:
    reply({"message": "Invalid access token"}, 403)
elif url.endswith("/scim/v2/Me"):
    reply({"userName": "someone@example.com"})
elif url.endswith("/sql/warehouses") and method == "GET":
    names = os.environ.get("WAREHOUSES", "ccai-sql").split(",")
    reply({"warehouses": [{"id": f"wh{i}", "name": n} for i, n in enumerate(names) if n]})
elif url.endswith("/sql/warehouses") and method == "POST":
    if os.environ.get("WAREHOUSE_LIMIT"):
        reply({"error_code": "RESOURCE_EXHAUSTED",
               "message": "COMMUNITY_EDITION limit: 1 of 1 warehouses"}, 429)
    else:
        reply({"id": "new1"})
elif url.endswith("/sql/statements"):
    stmt = json.loads(data)["statement"]
    if os.environ.get("DENY") and stmt.startswith(os.environ["DENY"]):
        reply({"statement_id": "s1", "status": {"state": "FAILED", "error": {"message": "PERMISSION_DENIED: no privilege"}}})
    else:
        reply({"statement_id": "s1", "status": {"state": "SUCCEEDED"}})
elif "/unity-catalog/catalogs" in url:
    names = os.environ.get("CATALOGS", "workspace,system").split(",")
    reply({"catalogs": [{"name": n} for n in names]})
else:
    reply({"message": "unexpected " + url}, 404)
"""


def sh(body: str, **env) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", f'source "{SCRIPT}"; {body}'], capture_output=True,
                          text=True, env={**os.environ, **env})


def run(args, token, tmp_path, **env):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "curl").write_text(FAKE_CURL)
    (bindir / "curl").chmod(0o755)
    log = tmp_path / "curl.log"
    res = subprocess.run([str(SCRIPT), *args], input=token + "\n", capture_output=True, text=True,
                         env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
                              "CURL_LOG": str(log), **env})
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return res, calls


def test_host_normalization():
    cases = {
        "HTTPS://Adb-1234567890.12.AzureDatabricks.net/?o=1": "adb-1234567890.12.azuredatabricks.net",
        " dbc-abcd1234-ef56.cloud.databricks.com/ ": "dbc-abcd1234-ef56.cloud.databricks.com",
        "adb-1.2.azuredatabricks.net?o=3": "adb-1.2.azuredatabricks.net",
    }
    for given, want in cases.items():
        res = sh(f'normalize_host "{given}"')
        assert res.stdout.strip() == want, (given, res.stderr)
    for bad in ("http://adb-1.2.azuredatabricks.net", "localhost", "not a host", ""):
        assert sh(f'normalize_host "{bad}"').returncode != 0, bad


def test_sql_statements_and_identifier_checks():
    res = sh("parse_args --catalog my_cat --raw-schema raw2; bootstrap_sql")
    assert res.stdout.splitlines() == [
        "CREATE CATALOG IF NOT EXISTS `my_cat`",
        "CREATE SCHEMA IF NOT EXISTS `my_cat`.`raw2`",
        "CREATE SCHEMA IF NOT EXISTS `my_cat`.`marts`",
        "CREATE VOLUME IF NOT EXISTS `my_cat`.`raw2`.`ccai_loader_stage`",
    ]
    assert sh("parse_args --catalog 'bad-cat'").returncode != 0
    assert sh("parse_args --warehouse-name \"x'y\"").returncode != 0
    assert sh("parse_args --nope").returncode != 0


def test_dry_run_reads_and_sends_nothing(tmp_path):
    res, calls = run(["--dry-run", "--host", "adb-1.2.azuredatabricks.net"], "", tmp_path)
    assert res.returncode == 0, res.stderr
    assert calls == []
    for line in ("Host: adb-1.2.azuredatabricks.net", "scim/v2/Me", "Find SQL warehouse 'ccai-sql'",
                 "CREATE VOLUME IF NOT EXISTS `ccai`.`raw`.`ccai_loader_stage`", "CMW/databricks-ccai"):
        assert line in res.stdout, line


def test_full_run_keeps_the_token_out_of_argv_and_output(tmp_path):
    res, calls = run(["--host", "adb-1.2.azuredatabricks.net", "--skip-1password"], "good-token", tmp_path)
    assert res.returncode == 0, res.stderr
    assert "DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/wh0" in res.stdout
    assert [json.loads(c["data"])["statement"] for c in calls if c["url"].endswith("/statements")] == [
        "CREATE CATALOG IF NOT EXISTS `ccai`", "CREATE SCHEMA IF NOT EXISTS `ccai`.`raw`",
        "CREATE SCHEMA IF NOT EXISTS `ccai`.`marts`",
        "CREATE VOLUME IF NOT EXISTS `ccai`.`raw`.`ccai_loader_stage`"]
    assert all("good-token" not in " ".join(c["argv"]) for c in calls)
    assert "good-token" not in res.stdout + res.stderr


def test_bad_token_fails_at_the_check(tmp_path):
    res, calls = run(["--host", "adb-1.2.azuredatabricks.net", "--skip-1password"], "wrong", tmp_path)
    assert res.returncode != 0 and "token check failed" in res.stderr
    assert len(calls) == 1


def test_warehouse_limit_with_one_existing_warehouse_uses_it(tmp_path):
    res, _ = run(["--host", "adb-1.2.azuredatabricks.net", "--skip-1password"], "good-token", tmp_path,
                 WAREHOUSES="Serverless Starter Warehouse", WAREHOUSE_LIMIT="1")
    assert res.returncode == 0, res.stderr
    assert "exactly one exists: using 'Serverless Starter Warehouse' (wh0)" in res.stdout
    assert "DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/wh0" in res.stdout


def test_warehouse_limit_without_a_single_candidate_keeps_the_guidance(tmp_path):
    res, _ = run(["--host", "adb-1.2.azuredatabricks.net", "--skip-1password"], "good-token", tmp_path,
                 WAREHOUSES="a,b", WAREHOUSE_LIMIT="1")
    assert res.returncode != 0
    assert "Existing warehouses: a, b" in res.stderr and "--warehouse-name" in res.stderr
    res, _ = run(["--host", "adb-1.2.azuredatabricks.net", "--skip-1password", "--warehouse-name", "mine"],
                 "good-token", tmp_path, WAREHOUSES="other", WAREHOUSE_LIMIT="1")
    assert res.returncode != 0  # an explicitly named warehouse is never swapped silently


def test_denied_catalog_with_one_usable_catalog_uses_it(tmp_path):
    res, calls = run(["--host", "adb-1.2.azuredatabricks.net", "--skip-1password"], "good-token", tmp_path,
                     DENY="CREATE CATALOG", CATALOGS="workspace,system,samples")
    assert res.returncode == 0, res.stderr
    assert "exactly one usable catalog exists: using 'workspace'" in res.stdout
    stmts = [json.loads(c["data"])["statement"] for c in calls if c["url"].endswith("/statements")]
    assert stmts[1:] == ["CREATE SCHEMA IF NOT EXISTS `workspace`.`raw`",
                         "CREATE SCHEMA IF NOT EXISTS `workspace`.`marts`",
                         "CREATE VOLUME IF NOT EXISTS `workspace`.`raw`.`ccai_loader_stage`"]
    assert "DATABRICKS_CATALOG=workspace" in res.stdout


def test_denied_catalog_prints_the_grant_and_visible_catalogs(tmp_path):
    res, _ = run(["--host", "adb-1.2.azuredatabricks.net", "--skip-1password"], "good-token", tmp_path,
                 DENY="CREATE CATALOG", CATALOGS="workspace,team,system")
    assert res.returncode != 0
    assert "PERMISSION_DENIED" in res.stderr
    assert "GRANT CREATE CATALOG ON METASTORE TO `someone@example.com`;" in res.stderr
    assert "Catalogs you can see: workspace, team, system" in res.stderr
    res, _ = run(["--host", "adb-1.2.azuredatabricks.net", "--skip-1password", "--catalog", "mine"],
                 "good-token", tmp_path, DENY="CREATE CATALOG", CATALOGS="workspace,system")
    assert res.returncode != 0  # an explicit --catalog is never swapped silently


def test_denied_volume_names_schema_grants(tmp_path):
    res, _ = run(["--host", "adb-1.2.azuredatabricks.net", "--skip-1password", "--catalog", "workspace"],
                 "good-token", tmp_path, DENY="CREATE VOLUME")
    assert res.returncode != 0
    assert "CREATE VOLUME ON SCHEMA `workspace`.`raw` TO `someone@example.com`;" in res.stderr


def test_op_template_conceals_the_token():
    res = sh('op_template "DATABRICKS_HOST=h" "DATABRICKS_TOKEN=t\\"x"')
    item = json.loads(res.stdout)
    assert item["title"] == "databricks-ccai"
    types = {f["label"]: (f["type"], f["value"]) for f in item["fields"]}
    assert types == {"DATABRICKS_HOST": ("STRING", "h"), "DATABRICKS_TOKEN": ("CONCEALED", 't"x')}
