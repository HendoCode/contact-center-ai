"""
Column types for the warehouse loaders, derived from the OLTP schema.

olap/oltp/schema.sql is the single source of truth for the raw tables. This module parses its
`CREATE TABLE` blocks and maps each Postgres type to a Snowflake and a Databricks type, so the
warehouse DDL cannot drift from the Postgres one. An unmapped type raises instead of guessing.
"""

import re
from dataclasses import dataclass
from pathlib import Path

SCHEMA_SQL = Path(__file__).resolve().parents[2] / "oltp" / "schema.sql"

_TABLE_RE = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", re.DOTALL)
# A column line is indented exactly four spaces; CHECK and FOREIGN KEY continuation lines are
# indented deeper, and `CONSTRAINT ...` lines start with the keyword.
_COLUMN_RE = re.compile(r"^ {4}(?!CONSTRAINT\b)(\w+)\s+([A-Z]+)(?:\((\d+),\s*(\d+)\))?", re.MULTILINE)

# Postgres type -> (Snowflake type, Databricks type). NUMERIC keeps its precision and scale.
_TYPES = {
    "UUID": ("VARCHAR(36)", "STRING"),
    "BIGINT": ("BIGINT", "BIGINT"),
    "INT": ("INTEGER", "INT"),
    "TEXT": ("VARCHAR", "STRING"),
    "DATE": ("DATE", "DATE"),
    "BOOLEAN": ("BOOLEAN", "BOOLEAN"),
    "TIMESTAMPTZ": ("TIMESTAMP_TZ", "TIMESTAMP"),
    # Written to the file as a JSON string. Snowflake parses it into a VARIANT; Databricks keeps
    # the JSON text in a STRING column (no model reads the utterances).
    "JSONB": ("VARIANT", "STRING"),
}


@dataclass(frozen=True)
class Column:
    name: str
    pg_type: str  # e.g. "NUMERIC(14,2)"
    snowflake: str
    databricks: str


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]

    def column(self, name: str) -> Column:
        return next(c for c in self.columns if c.name == name)


def _map_type(pg: str, precision: str | None, scale: str | None) -> tuple[str, str, str]:
    if pg == "NUMERIC":
        if precision is None or scale is None:
            raise ValueError("NUMERIC columns need an explicit precision and scale")
        return (
            f"NUMERIC({precision},{scale})",
            f"NUMBER({precision},{scale})",
            f"DECIMAL({precision},{scale})",
        )
    if pg not in _TYPES:
        raise ValueError(f"no warehouse type mapping for Postgres type {pg!r}")
    snowflake, databricks = _TYPES[pg]
    return pg, snowflake, databricks


def parse_schema(sql: str | None = None) -> dict[str, Table]:
    """Every table in schema.sql, in file (dependency) order, with mapped column types."""
    text = re.sub(r"--[^\n]*", "", SCHEMA_SQL.read_text() if sql is None else sql)
    tables: dict[str, Table] = {}
    for name, body in _TABLE_RE.findall(text):
        columns = tuple(
            Column(col, *_map_type(pg, precision or None, scale or None))
            for col, pg, precision, scale in _COLUMN_RE.findall(body)
        )
        tables[name] = Table(name, columns)
    return tables
