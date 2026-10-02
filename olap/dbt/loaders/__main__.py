"""
Load the OLTP JSON into a warehouse's raw tables.

    python -m olap.dbt.loaders snowflake  [--dry-run]
    python -m olap.dbt.loaders databricks [--dry-run]

--dry-run builds the data and prints every statement without importing a driver or opening a
connection (unset warehouse variables are replaced by placeholders). A real run needs the
warehouse's dbt environment variables, run in that warehouse's own env:

    uv run --group dbt --group snowflake  python -m olap.dbt.loaders snowflake
    uv run --group dbt --group databricks python -m olap.dbt.loaders databricks

Requires data/synthetic/*.json (python data/synthetic/generate_data.py).
"""

import argparse
import tempfile
from pathlib import Path

from . import databricks_loader, snowflake_loader
from .data import load_tables, write_ndjson
from .plan import execute, render
from .schema import parse_schema


def run(warehouse: str, dry_run: bool) -> None:
    schema = parse_schema()
    data = load_tables()
    with tempfile.TemporaryDirectory(prefix="ccai-load-") as tmp:
        staging = Path(tmp)
        files = {td.name: write_ndjson(td, staging) for td in data}
        if warehouse == "snowflake":
            cfg = snowflake_loader.SnowflakeConfig.from_env(dry_run)
            plan = snowflake_loader.build_plan(cfg, schema, data, files)
        else:
            cfg = databricks_loader.DatabricksConfig.from_env(dry_run)
            plan = databricks_loader.build_plan(cfg, schema, data, files)

        if dry_run:
            print(render(plan))
            return

        conn = (
            snowflake_loader.connect(cfg)
            if warehouse == "snowflake"
            else databricks_loader.connect(cfg, staging)
        )
        try:
            cursor = conn.cursor()
            print(f"Loading {len(plan.tables)} tables into {cfg.schema_fq} ({warehouse}):")
            execute(cursor, plan)
        finally:
            conn.close()
    print("\nLoad complete. Re-run to verify idempotency (counts unchanged).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("warehouse", choices=["snowflake", "databricks"])
    parser.add_argument("--dry-run", action="store_true", help="print the SQL, connect to nothing")
    args = parser.parse_args()
    run(args.warehouse, args.dry_run)


if __name__ == "__main__":
    main()
