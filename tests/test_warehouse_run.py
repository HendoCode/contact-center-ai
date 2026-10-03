"""Offline tests for tools/warehouse-run.sh: no 1Password, warehouse or dependency sync.

A fake `op` resolves the template's op:// references from FAKE_OP_VALUES (JSON) and runs
the command, like `op run --env-file`; a fake `uv` stands in for the dependency check
and the SELECT 1 probe.
"""

import json
import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "warehouse-run.sh"

# PEM markers built at runtime so no key-header literal sits in the source (secret scanners).
_PEM = "-----{} {}KEY-----"
FAKE_KEY = f"{_PEM.format('BEGIN', 'PRIVATE ')}\nMIIfake\n{_PEM.format('END', 'PRIVATE ')}"

FAKE_OP = r"""#!/usr/bin/env python3
import json, os, re, sys
a = sys.argv[1:]
if a[0] == "whoami":
    sys.exit(0 if os.environ.get("OP_SIGNED_IN") else 1)
if a[:2] == ["item", "get"]:
    sys.exit(0 if a[2] in os.environ.get("OP_ITEMS", "").split(",") else 1)
if a[0] == "run":
    values = json.loads(os.environ.get("FAKE_OP_VALUES", "{}"))
    env = dict(os.environ)
    for line in open(a[a.index("--env-file") + 1]):
        m = re.match(r"([A-Z_][A-Z0-9_]*)=op://\S+", line)
        if m:
            env[m.group(1)] = values.get(m.group(1), "")
    cmd = a[a.index("--") + 1:]
    os.execvpe(cmd[0], cmd, env)
sys.exit(2)
"""

FAKE_UV = """#!/usr/bin/env bash
# uv stand-in: UV_FAIL=import|probe makes that step fail.
case "$*" in
  *"SELECT 1"*) [[ "${UV_FAIL:-}" == probe ]] && { echo "250001: Could not connect" >&2; exit 1; } ;;
  *"import "*) [[ "${UV_FAIL:-}" == import ]] && exit 1 ;;
esac
exit 0
"""

SNOWFLAKE_VALUES = {
    "SNOWFLAKE_ACCOUNT": "ORG-ACCT", "SNOWFLAKE_USER": "CCAI_DBT", "SNOWFLAKE_ROLE": "CCAI_ROLE",
    "SNOWFLAKE_WAREHOUSE": "CCAI_WH", "SNOWFLAKE_DATABASE": "CCAI", "SNOWFLAKE_SCHEMA": "MARTS",
    "SNOWFLAKE_PRIVATE_KEY_PEM": FAKE_KEY,
}
DATABRICKS_VALUES = {"DATABRICKS_HOST": "adb-1.2.azuredatabricks.net",
                     "DATABRICKS_HTTP_PATH": "/sql/1.0/warehouses/abc",
                     "DATABRICKS_TOKEN": "dapi-test-token", "DATABRICKS_CATALOG": "workspace"}


def run(args, tmp_path, values=None, **env):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name, body in (("op", FAKE_OP), ("uv", FAKE_UV)):
        (bindir / name).write_text(body)
        (bindir / name).chmod(0o755)
    full = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "UV": str(bindir / "uv"),
            "TMPDIR": str(tmp_path), "OP_SIGNED_IN": "1", "OP_ITEMS": "snowflake-ccai,databricks-ccai",
            "FAKE_OP_VALUES": json.dumps(values or {}), **env}
    return subprocess.run([str(SCRIPT), *args], capture_output=True, text=True, env=full)


def test_key_exists_only_for_the_run_and_the_pem_never_reaches_the_command(tmp_path):
    probe = tmp_path / "seen.json"
    cmd = ["python3", "-c", "import json,os,stat,sys; p=os.environ['SNOWFLAKE_PRIVATE_KEY_PATH'];"
           f"json.dump({{'path': p, 'mode': oct(stat.S_IMODE(os.stat(p).st_mode)), 'key': open(p).read(),"
           f"'pem_in_env': 'SNOWFLAKE_PRIVATE_KEY_PEM' in os.environ}}, open('{probe}', 'w'))"]
    res = run(["snowflake", "--", *cmd], tmp_path, SNOWFLAKE_VALUES)
    assert res.returncode == 0, res.stderr
    seen = json.loads(probe.read_text())
    assert seen["mode"] == "0o600"
    assert seen["key"].strip() == FAKE_KEY
    assert seen["pem_in_env"] is False
    assert not Path(seen["path"]).exists()  # removed on exit
    assert "MIIfake" not in res.stdout + res.stderr


def test_key_is_removed_even_when_the_command_fails(tmp_path):
    res = run(["--no-check", "snowflake", "--", "bash", "-c",
               'echo "$SNOWFLAKE_PRIVATE_KEY_PATH" > "$TMPDIR/path"; exit 7'], tmp_path, SNOWFLAKE_VALUES)
    assert res.returncode == 7
    assert not Path((tmp_path / "path").read_text().strip()).exists()


def test_dry_run_prints_names_only_and_runs_nothing(tmp_path):
    marker = tmp_path / "ran"
    res = run(["--dry-run", "databricks", "--", "touch", str(marker)], tmp_path, DATABRICKS_VALUES)
    assert res.returncode == 0, res.stderr
    assert "Resolved from 1Password (names only): DATABRICKS_HOST DATABRICKS_HTTP_PATH DATABRICKS_TOKEN DATABRICKS_CATALOG" in res.stdout
    assert f"Would run: touch {marker}" in res.stdout
    assert not marker.exists()
    for value in DATABRICKS_VALUES.values():
        assert value not in res.stdout.replace(f"touch {marker}", "")


def test_dry_run_without_op_still_explains(tmp_path):
    res = run(["--dry-run", "snowflake", "--", "true"], tmp_path, OP_SIGNED_IN="")
    assert res.returncode == 0 and "Not resolved: op is not installed or not signed in" in res.stdout


def test_snowflake_dry_run_names_the_key_without_writing_it(tmp_path):
    res = run(["--dry-run", "snowflake", "--", "true"], tmp_path, SNOWFLAKE_VALUES)
    assert "SNOWFLAKE_PRIVATE_KEY_PEM (written to a temp key file at run time)" in res.stdout
    assert not list(tmp_path.glob("ccai-snowflake-key.*"))


def test_missing_prerequisites_each_get_one_clear_message(tmp_path):
    cases = [
        ({"OP_SIGNED_IN": ""}, "op is not signed in"),
        ({"OP_ITEMS": "databricks-ccai"}, "1Password item CMW/snowflake-ccai not found"),
        ({"UV_FAIL": "import"}, "run 'uv sync --group dbt --group snowflake'"),
        ({"UV_FAIL": "probe"}, "did not answer SELECT 1: 250001: Could not connect"),
    ]
    for env, message in cases:
        res = run(["snowflake", "--", "true"], tmp_path, SNOWFLAKE_VALUES, **env)
        assert res.returncode != 0 and message in res.stderr, (env, res.stderr)
    res = run(["--no-check", "databricks", "--", "true"], tmp_path,
              {**DATABRICKS_VALUES, "DATABRICKS_TOKEN": ""})
    assert res.returncode != 0 and "missing or empty after resolving 1Password: DATABRICKS_TOKEN" in res.stderr


def test_bad_arguments(tmp_path):
    assert run(["redshift", "--", "true"], tmp_path).returncode != 0
    assert "no command after --" in run(["snowflake", "--"], tmp_path).stderr


def test_templates_reference_only_the_connection_fields():
    sf = (ROOT / "tools" / "op" / "snowflake.env").read_text()
    names = [ln.split("=")[0] for ln in sf.splitlines() if ln and not ln.startswith("#")]
    assert names == list(SNOWFLAKE_VALUES)
    assert all("op://CMW/snowflake-ccai/" + n in sf for n in names)
    assert not any(n.startswith("SNOWFLAKE_ADMIN") for n in names)
    db = (ROOT / "tools" / "op" / "databricks.env").read_text()
    assert [ln.split("=")[0] for ln in db.splitlines() if ln and not ln.startswith("#")] == list(DATABRICKS_VALUES)


def test_make_targets_route_through_the_wrapper_and_direct_still_works():
    def dry(*args):
        return subprocess.run(["make", "-n", *args], cwd=ROOT, capture_output=True, text=True).stdout

    assert "tools/warehouse-run.sh  snowflake -- uv run --group dbt --group snowflake" in dry("load-snowflake")
    assert dry("load-databricks", "DIRECT=1").strip() == \
        "uv run --group dbt --group databricks python -m olap.dbt.loaders databricks"
    assert "tools/warehouse-run.sh --dry-run snowflake -- uv run --group dbt --group snowflake dbt build " \
        "--project-dir olap/dbt --profiles-dir olap/dbt --target snowflake" in dry("dbt-build", "WAREHOUSE=snowflake", "DRY=1")
    bad = subprocess.run(["make", "dbt-build"], cwd=ROOT, capture_output=True, text=True)
    assert bad.returncode != 0 and "usage: make dbt-build WAREHOUSE=snowflake|databricks" in bad.stderr
    assert stat.S_IMODE(SCRIPT.stat().st_mode) & 0o111
