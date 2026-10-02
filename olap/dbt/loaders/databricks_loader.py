"""
Databricks loader: PUT each table's NDJSON file into a Unity Catalog volume, then COPY INTO.

Per table: CREATE TABLE IF NOT EXISTS, PUT the file into the volume (OVERWRITE), TRUNCATE,
COPY INTO with explicit casts (force, so the load history never skips the file). Truncate-and-load
makes a re-run land on the same row counts with no duplicates; if a COPY fails the table is left
empty and the next run repairs it. The JSON is read with every primitive as a string and cast
explicitly, so Spark's schema inference cannot change a column's type. The catalog and SQL
warehouse must already exist; the raw schema and the volume are created here.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from .data import TableData
from .plan import Plan, TablePlan, check_ident
from .schema import Column, Table

VOLUME = "ccai_loader_stage"
DEFAULT_RAW_SCHEMA = "raw"
REQUIRED_ENV = ["DATABRICKS_HOST", "DATABRICKS_HTTP_PATH", "DATABRICKS_TOKEN", "DATABRICKS_CATALOG"]


@dataclass(frozen=True)
class DatabricksConfig:
    host: str
    http_path: str
    token: str
    catalog: str
    raw_schema: str

    @property
    def schema_fq(self) -> str:
        return f"`{self.catalog}`.`{self.raw_schema}`"

    @property
    def volume_dir(self) -> str:
        return f"/Volumes/{self.catalog}/{self.raw_schema}/{VOLUME}"

    @classmethod
    def from_env(cls, dry_run: bool = False) -> "DatabricksConfig":
        missing = [v for v in REQUIRED_ENV if not os.getenv(v)]
        if missing and not dry_run:
            raise SystemExit(f"missing environment variables: {', '.join(missing)}")
        env = os.getenv
        return cls(
            host=env("DATABRICKS_HOST", "<workspace-host>"),
            http_path=env("DATABRICKS_HTTP_PATH", "<http-path>"),
            token=env("DATABRICKS_TOKEN", ""),
            catalog=check_ident(env("DATABRICKS_CATALOG", "my_catalog"), "DATABRICKS_CATALOG"),
            raw_schema=check_ident(
                env("DATABRICKS_RAW_SCHEMA", DEFAULT_RAW_SCHEMA), "DATABRICKS_RAW_SCHEMA"
            ),
        )


def _expr(col: Column) -> str:
    return f"cast(`{col.name}` AS {col.databricks}) AS `{col.name}`"


def create_table_sql(cfg: DatabricksConfig, table: Table) -> str:
    columns = ", ".join(f"`{c.name}` {c.databricks}" for c in table.columns)
    return f"CREATE TABLE IF NOT EXISTS {cfg.schema_fq}.`{table.name}` ({columns})"


def copy_into_sql(cfg: DatabricksConfig, table: Table, data: TableData) -> str:
    select = ", ".join(_expr(table.column(name)) for name in data.columns)
    source = f"{cfg.volume_dir}/{data.filename}"
    return (
        f"COPY INTO {cfg.schema_fq}.`{table.name}` "
        f"FROM (SELECT {select} FROM '{source}') "
        "FILEFORMAT = JSON FORMAT_OPTIONS ('primitivesAsString' = 'true') "
        "COPY_OPTIONS ('force' = 'true')"
    )


def build_plan(
    cfg: DatabricksConfig, schema: dict[str, Table], data: list[TableData], files: dict[str, Path]
) -> Plan:
    plan = Plan(
        "databricks",
        setup=[
            f"CREATE SCHEMA IF NOT EXISTS {cfg.schema_fq}",
            f"CREATE VOLUME IF NOT EXISTS {cfg.schema_fq}.`{VOLUME}`",
        ],
    )
    for td in data:
        table = schema[td.name]
        fq = f"{cfg.schema_fq}.`{td.name}`"
        plan.tables.append(
            TablePlan(
                name=td.name,
                expected_rows=len(td.rows),
                statements=[
                    create_table_sql(cfg, table),
                    f"PUT '{files[td.name]}' INTO '{cfg.volume_dir}/{td.filename}' OVERWRITE",
                    f"TRUNCATE TABLE {fq}",
                    copy_into_sql(cfg, table, td),
                ],
                count_sql=f"SELECT count(*) FROM {fq}",
            )
        )
    return plan


def connect(cfg: DatabricksConfig, staging_dir: Path):
    from databricks import sql

    return sql.connect(
        server_hostname=cfg.host.removeprefix("https://").rstrip("/"),
        http_path=cfg.http_path,
        access_token=cfg.token,
        catalog=cfg.catalog,
        staging_allowed_local_path=str(staging_dir),
    )
