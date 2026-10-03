"""
Offline guards for the portable dbt project (olap/dbt).

The models must run on Postgres, Snowflake and Databricks, and the warehouse targets must
take every value from the environment. These read the committed files only: no dbt install,
no warehouse, no network.
"""

import re
from pathlib import Path

import yaml

DBT_DIR = Path(__file__).resolve().parent.parent / "olap" / "dbt"
ENV_EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"

# Postgres-only spellings that have a portable replacement (dbt macros, cast(), case).
PG_ONLY = {
    r"::": "the `::` cast; use cast(... as ...) or a dbt.type_* macro",
    r"\bto_char\s*\(": "to_char; build names with case and numbers with extract/cast",
    r"(?<!dbt\.)\bgenerate_series\s*\(": "generate_series(); use dbt.date_spine / dbt.generate_series",
    r"\bisodow\b": "extract(isodow ...); derive it with dbt.datediff and mod",
    r"\bdoy\b": "extract(doy ...); derive it with dbt.datediff and dbt.date_trunc",
    r"\binterval\s+'": "interval '...' arithmetic; use dbt.dateadd",
    r"\bilike\b": "ilike; use lower(...) like lower(...)",
}


def _profile_outputs() -> dict:
    return yaml.safe_load((DBT_DIR / "profiles.yml").read_text())["ccai_semantic"]["outputs"]


def _sql_without_comments(path: Path) -> str:
    return re.sub(r"--[^\n]*", "", path.read_text())


def test_models_and_tests_have_no_postgres_only_sql():
    offenders = []
    for path in sorted([*DBT_DIR.glob("models/**/*.sql"), *DBT_DIR.glob("tests/*.sql")]):
        sql = _sql_without_comments(path)
        for pattern, why in PG_ONLY.items():
            if re.search(pattern, sql, flags=re.IGNORECASE):
                offenders.append(f"{path.relative_to(DBT_DIR)}: {why}")
    assert not offenders, "Postgres-only SQL (see olap/dbt/README.md):\n" + "\n".join(offenders)


def test_select_distinct_orders_only_by_its_output_columns():
    """Spark (Databricks) rejects ORDER BY on a column a SELECT DISTINCT does not output,
    which Postgres and Snowflake accept: `select distinct status as code ... order by status`."""
    offenders = []
    for path in sorted([*DBT_DIR.glob("models/**/*.sql"), *DBT_DIR.glob("tests/*.sql")]):
        sql = _sql_without_comments(path)
        for m in re.finditer(r"select\s+distinct\s+(.*?)\s+from\b.*?\border\s+by\s+([^;)]*)", sql,
                             flags=re.IGNORECASE | re.DOTALL):
            select_list, order_list = m.groups()
            outputs = {item.strip().split()[-1].split(".")[-1].lower()
                       for item in select_list.split(",") if item.strip()}
            for term in order_list.split(","):
                col = term.strip().split()[0].lower() if term.strip() else ""
                if col and not col.isdigit() and col not in outputs:
                    offenders.append(f"{path.relative_to(DBT_DIR)}: order by {col}")
    assert not offenders, "SELECT DISTINCT ordered by a non-output column:\n" + "\n".join(offenders)


def test_profiles_define_the_three_targets():
    outputs = _profile_outputs()
    assert set(outputs) == {"dev", "snowflake", "databricks"}
    assert {name: out["type"] for name, out in outputs.items()} == {
        "dev": "postgres",
        "snowflake": "snowflake",
        "databricks": "databricks",
    }


def test_every_profile_value_comes_from_env():
    for target, output in _profile_outputs().items():
        for key, value in output.items():
            if key in {"type", "threads"}:
                continue
            assert "env_var(" in str(value), f"{target}.{key} must come from an env var"


def test_snowflake_uses_key_pair_auth_never_a_password():
    snowflake = _profile_outputs()["snowflake"]
    assert "password" not in snowflake
    assert "private_key_path" in snowflake
    assert "private_key_passphrase" in snowflake


def test_warehouse_targets_have_no_defaulted_account_or_secret():
    # A default would hide a missing identifier; these must fail loudly when unset.
    outputs = _profile_outputs()
    required = {
        "snowflake": ["account", "user", "private_key_path", "role", "warehouse", "database"],
        "databricks": ["host", "http_path", "token", "catalog"],
    }
    for target, keys in required.items():
        for key in keys:
            assert "," not in outputs[target][key], f"{target}.{key} must have no env default"


def test_profile_env_vars_have_placeholders_in_env_example():
    declared = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", ENV_EXAMPLE.read_text(), flags=re.MULTILINE))
    for target in ("snowflake", "databricks"):
        for key, value in _profile_outputs()[target].items():
            for name in re.findall(r"env_var\('([A-Z0-9_]+)'", str(value)):
                assert name in declared, f"{target}.{key}: {name} missing from .env.example"
