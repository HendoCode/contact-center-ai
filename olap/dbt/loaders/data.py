"""
The OLTP rows the loaders send, taken from the same registry the Postgres seed uses.

olap/seed.py owns the JSON -> typed-row preparers and the table list (columns, primary key,
source file). Reusing them means the warehouses get exactly the rows Postgres gets. Rows are
written as newline-delimited JSON, one file per table, with every column present (nulls are
explicit) so both warehouses see the full column set even when a column is all null.
"""

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from olap.seed import TABLES, load_json


@dataclass(frozen=True)
class TableData:
    name: str
    columns: list[str]
    pk: list[str]
    rows: list[dict[str, Any]]

    @property
    def filename(self) -> str:
        return f"{self.name}.ndjson"


def _jsonable(value: Any) -> Any:
    # psycopg2's Json wrapper (utterances) carries the Python object in `.adapted`; the
    # warehouses take it as a JSON string.
    if hasattr(value, "adapted"):
        return json.dumps(value.adapted, separators=(",", ":"))
    if isinstance(value, datetime):
        # seed.py stores these as timestamptz in a UTC session; send the offset explicitly.
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def load_tables() -> list[TableData]:
    """Every OLTP table, in the seed's dependency order."""
    tables = []
    for name, columns, pk, preparer, source in TABLES:
        rows = [
            {c: _jsonable(row[c]) for c in columns} for row in preparer(load_json(source))
        ]
        tables.append(TableData(name, list(columns), list(pk), rows))
    return tables


def write_ndjson(table: TableData, directory: Path) -> Path:
    path = directory / table.filename
    with path.open("w") as f:
        for row in table.rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    return path
