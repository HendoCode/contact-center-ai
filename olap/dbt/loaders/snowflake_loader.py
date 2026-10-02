"""
Snowflake loader: PUT each table's NDJSON file to an internal stage, then COPY INTO.

Per table: CREATE TABLE IF NOT EXISTS, PUT the file (OVERWRITE), TRUNCATE, COPY INTO with
explicit casts (FORCE, so the load history never skips the file). Truncate-and-load makes a
re-run land on the same row counts with no duplicates; if a COPY fails the table is left empty
and the next run repairs it. Auth is key-pair (the same variables dbt uses); there is no password.
The database, warehouse and role must already exist; the raw schema and stage are created here.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from .data import TableData
from .plan import Plan, TablePlan, check_ident
from .schema import Column, Table

STAGE = "ccai_loader_stage"
DEFAULT_RAW_SCHEMA = "raw"
REQUIRED_ENV = [
    "SNOWFLAKE_ACCOUNT",
    "SNOWFLAKE_USER",
    "SNOWFLAKE_PRIVATE_KEY_PATH",
    "SNOWFLAKE_ROLE",
    "SNOWFLAKE_WAREHOUSE",
    "SNOWFLAKE_DATABASE",
]


@dataclass(frozen=True)
class SnowflakeConfig:
    account: str
    user: str
    private_key_path: str
    private_key_passphrase: str
    role: str
    warehouse: str
    database: str
    raw_schema: str

    @property
    def schema_fq(self) -> str:
        return f"{self.database}.{self.raw_schema}"

    @classmethod
    def from_env(cls, dry_run: bool = False) -> "SnowflakeConfig":
        missing = [v for v in REQUIRED_ENV if not os.getenv(v)]
        if missing and not dry_run:
            raise SystemExit(f"missing environment variables: {', '.join(missing)}")
        env = os.getenv
        return cls(
            account=env("SNOWFLAKE_ACCOUNT", "<account>"),
            user=env("SNOWFLAKE_USER", "<user>"),
            private_key_path=env("SNOWFLAKE_PRIVATE_KEY_PATH", "<private-key.p8>"),
            private_key_passphrase=env("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", ""),
            role=env("SNOWFLAKE_ROLE", "<role>"),
            warehouse=env("SNOWFLAKE_WAREHOUSE", "<warehouse>"),
            database=check_ident(env("SNOWFLAKE_DATABASE", "MY_DATABASE"), "SNOWFLAKE_DATABASE"),
            raw_schema=check_ident(
                env("SNOWFLAKE_RAW_SCHEMA", DEFAULT_RAW_SCHEMA), "SNOWFLAKE_RAW_SCHEMA"
            ),
        )


def _expr(col: Column) -> str:
    """The COPY transform for one column: a typed cast of the JSON field."""
    field = f"$1:{col.name}"
    if col.snowflake == "VARIANT":
        return f"PARSE_JSON({field}::STRING)"
    return f"{field}::{col.snowflake}"


def create_table_sql(cfg: SnowflakeConfig, table: Table) -> str:
    columns = ", ".join(f"{c.name} {c.snowflake}" for c in table.columns)
    return f"CREATE TABLE IF NOT EXISTS {cfg.schema_fq}.{table.name} ({columns})"


def copy_into_sql(cfg: SnowflakeConfig, table: Table, data: TableData) -> str:
    cols = [table.column(name) for name in data.columns]
    names = ", ".join(c.name for c in cols)
    select = ", ".join(_expr(c) for c in cols)
    source = f"@{cfg.schema_fq}.{STAGE}/{table.name}/"
    return (
        f"COPY INTO {cfg.schema_fq}.{table.name} ({names}) "
        f"FROM (SELECT {select} FROM {source}) "
        f"FILE_FORMAT = (TYPE = JSON) FILES = ('{data.filename}') "
        "FORCE = TRUE ON_ERROR = ABORT_STATEMENT"
    )


def build_plan(
    cfg: SnowflakeConfig, schema: dict[str, Table], data: list[TableData], files: dict[str, Path]
) -> Plan:
    plan = Plan(
        "snowflake",
        setup=[
            f"CREATE SCHEMA IF NOT EXISTS {cfg.schema_fq}",
            f"CREATE STAGE IF NOT EXISTS {cfg.schema_fq}.{STAGE}",
        ],
    )
    for td in data:
        table = schema[td.name]
        fq = f"{cfg.schema_fq}.{td.name}"
        plan.tables.append(
            TablePlan(
                name=td.name,
                expected_rows=len(td.rows),
                statements=[
                    create_table_sql(cfg, table),
                    f"PUT 'file://{files[td.name]}' @{cfg.schema_fq}.{STAGE}/{td.name}/ "
                    "OVERWRITE = TRUE AUTO_COMPRESS = FALSE",
                    f"TRUNCATE TABLE {fq}",
                    copy_into_sql(cfg, table, td),
                ],
                count_sql=f"SELECT count(*) FROM {fq}",
            )
        )
    return plan


def connect(cfg: SnowflakeConfig):
    import snowflake.connector

    return snowflake.connector.connect(
        account=cfg.account,
        user=cfg.user,
        private_key_file=cfg.private_key_path,
        private_key_file_pwd=cfg.private_key_passphrase or None,
        role=cfg.role,
        warehouse=cfg.warehouse,
        database=cfg.database,
    )
