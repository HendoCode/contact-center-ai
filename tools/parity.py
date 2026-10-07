"""
`make parity`: run five semantic-layer metrics on Postgres, Snowflake and Databricks,
diff the numbers to a tolerance, and write the results to `results/semantics/`.

Each warehouse needs its dbt adapter group and, for Snowflake/Databricks, its
1Password-resolved credentials via `tools/warehouse-run.sh`.  The MetricFlow
semantic manifest in `olap/dbt/target/semantic_manifest.json` is temporarily
switched to the target's adapter for the query, then restored.

Usage:
    uv run python -m tools.parity
    PARITY_TARGETS=postgres,snowflake uv run python -m tools.parity
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
DBT_DIR = REPO_ROOT / "olap" / "dbt"
WAREHOUSE_RUN = REPO_ROOT / "tools" / "warehouse-run.sh"
RESULTS_DIR = REPO_ROOT / "results" / "semantics"

#: Metrics chosen for parity and their tolerances.
#: Counts are exact; monetary amounts to the cent; rates/ratios to 1e-4.
METRICS: dict[str, dict[str, float | int]] = {
    "call_volume": {"tolerance": 0},
    "average_mortgage_note_rate": {"tolerance": 0.0001},
    "banking_available_balance": {"tolerance": 0.01},
    "credit_card_outstanding": {"tolerance": 0.01},
    "first_contact_resolution_rate": {"tolerance": 0.0001},
}

TARGETS: dict[str, dict] = {
    "postgres": {
        "profile_target": "dev",
        "adapter": "postgres",
        "groups": ["dbt"],
        "wrapper": None,
    },
    "snowflake": {
        "profile_target": "snowflake",
        "adapter": "snowflake",
        "groups": ["dbt", "snowflake"],
        "wrapper": str(WAREHOUSE_RUN),
    },
    "databricks": {
        "profile_target": "databricks",
        "adapter": "databricks",
        "groups": ["dbt", "databricks"],
        "wrapper": str(WAREHOUSE_RUN),
    },
}

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_SECRET_PLACEHOLDER_RE = re.compile(r"<concealed by 1Password>")

# Environment variable names whose values may be account identifiers, schemas, or credentials.
_SENSITIVE_ENV_PATTERNS = (
    "ACCOUNT", "HOST", "CATALOG", "DATABASE", "SCHEMA", "USER", "ROLE", "WAREHOUSE",
    "TOKEN", "KEY", "PASSWORD", "PASSPHRASE", "SECRET", "CREDENTIAL", "PRIVATE_KEY",
    "HTTP_PATH",
)


def _sensitive_env_values() -> list[str]:
    """Collect non-empty environment values that look like secrets or account ids."""
    values: set[str] = set()
    for name, value in os.environ.items():
        if not value or len(value) <= 3:
            continue
        if any(pattern in name for pattern in _SENSITIVE_ENV_PATTERNS):
            values.add(value)
            # For URLs, also redact the bare hostname/host:port portion.
            if value.startswith(("http://", "https://")):
                host = value.split("://", 1)[1].split("/")[0]
                if host:
                    values.add(host)
    # Replace longest values first so shorter substrings do not leave partial matches.
    return sorted(values, key=len, reverse=True)


def sanitize_sql(sql: str) -> str:
    """Redact account identifiers, hosts, tokens, and 1Password placeholders."""
    for value in _sensitive_env_values():
        sql = sql.replace(value, "<redacted>")
    sql = _SECRET_PLACEHOLDER_RE.sub("<name>", sql)
    return sql
# Pure helpers
# ---------------------------------------------------------------------------


def clean_stdout(text: str) -> str:
    return ANSI_RE.sub("", text)


def parse_value(raw: str):
    """Best-effort parse a CSV cell to int/float/str."""
    raw = raw.strip()
    if raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def parse_csv_row(path: Path) -> dict[str, object]:
    """Return {column: value} for the first data row of a MetricFlow CSV."""
    text = path.read_text()
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        raise ValueError("MetricFlow returned an empty CSV")
    return {k.lower(): parse_value(v) for k, v in rows[0].items()}


def extract_sql(explain_output: str) -> str:
    """Pull the generated SQL out of `mf query --explain` stdout."""
    lines = explain_output.splitlines()
    for i, line in enumerate(lines):
        if "SQL (" in line:
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            return "\n".join(lines[j:]).strip()
    # Fallback: the SQL itself starts with WITH / SELECT.
    for i, line in enumerate(lines):
        if line.strip().upper().startswith(("WITH", "SELECT")):
            return "\n".join(lines[i:]).strip()
    return ""


def diff_metric(values: dict[str, float], tolerance: float, targets: list[str]) -> dict:
    """Compare numeric values across requested targets."""
    numeric = {k: v for k, v in values.items() if isinstance(v, (int, float))}
    missing = [t for t in targets if t not in numeric]
    if missing:
        return {"max_diff": None, "ok": False, "note": f"missing targets: {missing}"}
    if len(numeric) < 2:
        return {"max_diff": None, "ok": False, "note": "fewer than 2 targets returned numbers"}
    vals = list(numeric.values())
    max_diff = max(vals) - min(vals)
    ok = max_diff <= tolerance
    return {"max_diff": max_diff, "ok": ok}


# ---------------------------------------------------------------------------
# dbt / MetricFlow environment
# ---------------------------------------------------------------------------


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def make_temp_profile(target_name: str) -> Path:
    """Create a temporary profiles dir whose default target is `target_name`."""
    tmp = Path(tempfile.mkdtemp(prefix=f"parity-{target_name}-"))
    original = DBT_DIR / "profiles.yml"
    text = original.read_text()
    text = re.sub(r"^  target: dev$", f"  target: {target_name}", text, flags=re.MULTILINE)
    (tmp / "profiles.yml").write_text(text)
    return tmp


def stage_semantic_manifest(target_name: str, original_path: Path) -> None:
    """Copy the target-specific semantic manifest into place."""
    target_manifest = DBT_DIR / "target" / target_name / "semantic_manifest.json"
    if not target_manifest.exists():
        raise FileNotFoundError(
            f"{target_manifest} not found; run `make dbt-build WAREHOUSE={target_name}` first"
        )
    shutil.copy2(target_manifest, original_path)


def restore_manifest(original_path: Path, original_text: str | None) -> None:
    """Restore the original manifest, or remove it if there was none at the start."""
    if original_text is not None:
        original_path.write_text(original_text)
    elif original_path.exists():
        original_path.unlink()


# ---------------------------------------------------------------------------
# Running MetricFlow per target
# ---------------------------------------------------------------------------


def mf_command(metrics: list[str], csv_path: Path, explain: bool = False) -> list[str]:
    cmd = [
        "mf", "query",
        "--metrics", ",".join(metrics),
        "--csv", str(csv_path),
    ]
    if explain:
        cmd.append("--explain")
    return cmd


def run_subprocess(cmd: list[str], env: dict[str, str] | None = None, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )


def run_mf_target(
    target: str,
    metrics: list[str],
    csv_path: Path,
    profiles_dir: Path,
) -> dict:
    """Run `mf query --csv` and `mf query --explain` for a single target."""
    config = TARGETS[target]
    groups = config["groups"]
    uv_prefix = ["uv", "run"] + [g for grp in groups for g in ("--group", grp)]

    env = os.environ.copy()
    env["DBT_PROFILES_DIR"] = str(profiles_dir)
    env["DBT_PROJECT_DIR"] = str(DBT_DIR)

    # Data query
    data_cmd = uv_prefix + mf_command(metrics, csv_path, explain=False)
    if config["wrapper"]:
        inner = " ".join(shlex.quote(str(c)) for c in data_cmd)
        data_cmd = [config["wrapper"], target, "--", "bash", "-c", inner]

    data_proc = run_subprocess(data_cmd, env=env)
    data_proc.stdout = clean_stdout(data_proc.stdout)
    data_proc.stderr = clean_stdout(data_proc.stderr)

    if data_proc.returncode != 0:
        return {
            "status": "error",
            "error": (data_proc.stdout + "\n" + data_proc.stderr).strip(),
        }

    try:
        values = parse_csv_row(csv_path)
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "status": "error",
            "error": f"Failed to parse MetricFlow CSV: {exc}",
        }

    # Explain query for generated SQL
    explain_cmd = uv_prefix + mf_command(metrics, csv_path, explain=True)
    if config["wrapper"]:
        inner = " ".join(shlex.quote(str(c)) for c in explain_cmd)
        explain_cmd = [config["wrapper"], target, "--", "bash", "-c", inner]

    explain_proc = run_subprocess(explain_cmd, env=env)
    explain_proc.stdout = clean_stdout(explain_proc.stdout)
    sql = extract_sql(explain_proc.stdout)

    return {"status": "ok", "values": values, "sql": sql}


def check_postgres_reachable() -> bool:
    """Cheap probe: can we connect to the local Compose Postgres?"""
    url = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/contactcenter")
    try:
        import psycopg2
        with psycopg2.connect(url):
            return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main parity run
# ---------------------------------------------------------------------------


def run_parity(targets: list[str], dry_run: bool = False) -> dict:
    metrics = list(METRICS.keys())
    original_manifest = DBT_DIR / "target" / "semantic_manifest.json"
    original_text = original_manifest.read_text() if original_manifest.exists() else None

    run_dir = RESULTS_DIR
    run_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, dict] = {}
    sql_files: dict[str, Path | None] = {}

    try:
        for target in targets:
            config = TARGETS[target]
            if target == "postgres" and not check_postgres_reachable():
                results[target] = {
                    "status": "unreachable",
                    "error": "local Postgres is not reachable (run `make up` and seed)",
                }
                sql_files[target] = None
                continue

            if dry_run:
                results[target] = {"status": "dry-run", "values": {}, "sql": "-- dry-run"}
                sql_files[target] = None
                continue

            profiles_dir = DBT_DIR if target == "postgres" else make_temp_profile(config["profile_target"])
            try:
                if target == "postgres":
                    restore_manifest(original_manifest, original_text)
                else:
                    stage_semantic_manifest(config["profile_target"], original_manifest)

                with tempfile.TemporaryDirectory(prefix=f"parity-csv-{target}-") as td:
                    csv_path = Path(td) / "mf_result.csv"
                    results[target] = run_mf_target(target, metrics, csv_path, profiles_dir)
                    if results[target]["status"] == "ok":
                        sql = sanitize_sql(results[target].get("sql", ""))
                        results[target]["sql"] = sql
                        sql_file = run_dir / f"{_run_stamp()}_parity_{target}.sql"
                        sql_file.write_text(sql)
                        sql_files[target] = sql_file.name
                    else:
                        sql_files[target] = None
            finally:
                if target != "postgres":
                    shutil.rmtree(profiles_dir, ignore_errors=True)
    finally:
        restore_manifest(original_manifest, original_text)

    # Diff across requested targets; a missing target is a failure.
    diff: dict[str, dict] = {}
    for metric in metrics:
        values = {
            target: results[target]["values"].get(metric)
            for target in targets
            if results[target].get("status") == "ok" and metric in results[target]["values"]
        }
        tolerance = float(METRICS[metric]["tolerance"])  # type: ignore[arg-type]
        diff[metric] = {"values": values, **diff_metric(values, tolerance, targets)}

    all_targets_ok = all(results[t].get("status") == "ok" for t in targets)
    all_metrics_ok = all(diff[m]["ok"] for m in metrics)
    run_ok = all_targets_ok and all_metrics_ok

    return {
        "results": results,
        "diff": diff,
        "sql_files": sql_files,
        "run_ok": run_ok,
    }


def _run_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def build_record(parity: dict, targets: list[str]) -> dict:
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    sha = git_sha()

    metrics_table: dict[str, dict] = {}
    for metric in METRICS:
        row = {"tolerance": METRICS[metric]["tolerance"]}
        for target in targets:
            row[target] = parity["diff"][metric]["values"].get(target)
        row["max_diff"] = parity["diff"][metric]["max_diff"]
        row["ok"] = parity["diff"][metric]["ok"]
        metrics_table[metric] = row

    status = {target: {"status": parity["results"][target]["status"]} for target in targets}
    for target in targets:
        sql_file = parity["sql_files"].get(target)
        if sql_file:
            status[target]["sql_file"] = sql_file

    all_metrics_ok = all(row["ok"] for row in metrics_table.values())
    all_targets_ok = all(status[t]["status"] == "ok" for t in targets)
    notes = []
    if not all_metrics_ok:
        mismatches = [m for m, row in metrics_table.items() if not row["ok"]]
        notes.append(f"Mismatches beyond tolerance or missing data: {', '.join(mismatches)}")
    if not all_targets_ok:
        bad = [t for t in targets if status[t]["status"] != "ok"]
        notes.append(f"Target errors: {', '.join(bad)}")
    if not notes:
        notes.append("All targets agree within tolerance.")

    return {
        "date": date,
        "git_sha": sha,
        "area": "semantics",
        "run": "parity",
        "hardware": f"{platform.platform()}, {platform.processor() or 'unknown'}".rstrip(", "),
        "versions": {"python": platform.python_version()},
        "params": {
            "metrics": list(METRICS.keys()),
            "targets": targets,
            "tolerances": {m: float(v["tolerance"]) for m, v in METRICS.items()},  # type: ignore[arg-type]
        },
        "metrics": {**metrics_table, **status},
        "notes": " ".join(notes),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Semantic-layer parity across warehouses")
    parser.add_argument(
        "--targets",
        default=os.environ.get("PARITY_TARGETS", "postgres,snowflake,databricks"),
        help="Comma-separated warehouse targets (default: postgres,snowflake,databricks)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan and exit without querying warehouses",
    )
    args = parser.parse_args(argv)

    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    unknown = [t for t in targets if t not in TARGETS]
    if unknown:
        print(f"Unknown target(s): {unknown}; choose from {list(TARGETS)}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"Would run parity for targets: {targets}")
        print(f"Metrics: {list(METRICS.keys())}")
        for t in targets:
            print(f"  {t}: adapter={TARGETS[t]['adapter']}, groups={TARGETS[t]['groups']}")
        return 0

    parity = run_parity(targets)
    record = build_record(parity, targets)

    stamp = _run_stamp()
    results_file = RESULTS_DIR / f"{stamp}_parity.json"
    results_file.write_text(json.dumps(record, indent=2, default=str) + "\n")

    # Render the README
    from tools.results import render
    readme = render("semantics")

    print(f"wrote {results_file}")
    print(f"wrote {readme}")

    # Print a concise table
    print()
    print("| metric | " + " | ".join(targets) + " | tolerance | max_diff | ok |")
    print("|" + "---|" * (len(targets) + 4))
    for metric in METRICS:
        row = record["metrics"][metric]
        vals = " | ".join(str(row.get(t, "n/a")) for t in targets)
        print(f"| {metric} | {vals} | {row['tolerance']} | {row['max_diff']} | {row['ok']} |")

    return 0 if parity["run_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
