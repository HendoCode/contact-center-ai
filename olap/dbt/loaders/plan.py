"""
A load plan: the SQL a loader runs, kept apart from the connection that runs it.

Each warehouse module builds a `Plan` (pure string generation, no driver import), so the
statements can be printed with --dry-run and exercised against a fake cursor in tests.
`execute` runs a plan and checks the post-load row counts against the rows it sent.
"""

import re
from dataclasses import dataclass, field
from typing import Any

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def check_ident(value: str, what: str) -> str:
    """Names come from env vars and land in DDL unquoted, so only plain identifiers pass."""
    if not _IDENT_RE.match(value):
        raise ValueError(f"{what} must be a plain identifier (letters, digits, _), got {value!r}")
    return value


@dataclass(frozen=True)
class TablePlan:
    name: str
    expected_rows: int
    statements: list[str]
    count_sql: str


@dataclass
class Plan:
    warehouse: str
    setup: list[str]
    tables: list[TablePlan] = field(default_factory=list)


def render(plan: Plan) -> str:
    """The plan as readable SQL, for --dry-run."""
    lines = [f"-- {plan.warehouse}: setup"]
    lines += [f"{sql};" for sql in plan.setup]
    for table in plan.tables:
        lines += ["", f"-- {table.name}: {table.expected_rows} rows"]
        lines += [f"{sql};" for sql in table.statements]
        lines.append(f"{table.count_sql};")
    return "\n".join(lines)


def execute(cursor: Any, plan: Plan) -> dict[str, int]:
    """Run the plan, then compare each table's count to what was sent. Returns the counts."""
    for sql in plan.setup:
        cursor.execute(sql)
    counts = {}
    mismatches = []
    for table in plan.tables:
        for sql in table.statements:
            cursor.execute(sql)
        cursor.execute(table.count_sql)
        counts[table.name] = int(cursor.fetchone()[0])
        print(f"  {table.name:<24} {counts[table.name]:>7} rows")
        if counts[table.name] != table.expected_rows:
            mismatches.append(f"{table.name}: sent {table.expected_rows}, found {counts[table.name]}")
    if mismatches:
        raise RuntimeError("row counts differ after load:\n  " + "\n  ".join(mismatches))
    return counts
