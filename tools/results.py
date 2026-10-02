"""
Results contract (docs/AGENT_HANDOFF.md §4.3): validate run JSON and render README tables.

    python -m tools.results render <area>        # results/<area>/*.json -> results/<area>/README.md
    python -m tools.results validate <file>...   # exit 1 on any contract violation

A run record is one JSON object:

    {"date": "YYYY-MM-DD", "git_sha": "...", "area": "...", "run": "...",
     "hardware": "...", "versions": {"python": "...", ...},
     "params": {...}, "metrics": {...}, "notes": "..."}

Rendering convention for `metrics`: an entry whose value is a dict is a table row
(row label = key). Scalar values in those rows become columns of the run's main
table; dict values (e.g. a per-query-type breakdown) become one extra table per
field. Top-level scalar entries render as a short list. Docs quote the rendered
README, never retyped numbers.
"""

import argparse
import json
import re
import sys
from pathlib import Path

RESULTS_ROOT = Path(__file__).resolve().parent.parent / "results"
AREAS = ("retrieval", "finetune", "serving", "evals", "semantics")

_REQUIRED = {
    "date": str, "git_sha": str, "area": str, "run": str, "hardware": str,
    "versions": dict, "params": dict, "metrics": dict, "notes": str,
}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate(record: object) -> list[str]:
    """Return the §4.3 contract violations in `record` (empty when valid)."""
    if not isinstance(record, dict):
        return ["record is not a JSON object"]
    errors = []
    for key, typ in _REQUIRED.items():
        if key not in record:
            errors.append(f"missing key {key!r}")
        elif not isinstance(record[key], typ):
            errors.append(f"{key!r} must be {typ.__name__}, got {type(record[key]).__name__}")
    if errors:
        return errors
    if not _DATE.match(record["date"]):
        errors.append(f"date {record['date']!r} is not YYYY-MM-DD")
    if record["area"] not in AREAS:
        errors.append(f"area {record['area']!r} not in {AREAS}")
    for key in ("git_sha", "run", "hardware"):
        if not record[key].strip():
            errors.append(f"{key!r} is empty")
    if "python" not in record["versions"]:
        errors.append("versions must include 'python'")
    if not record["metrics"]:
        errors.append("metrics is empty")
    return errors


def load(path: Path) -> dict:
    record = json.loads(Path(path).read_text())
    errors = validate(record)
    if errors:
        raise ValueError(f"{path}: " + "; ".join(errors))
    return record


# ── rendering ─────────────────────────────────────────────────────────────────

def _cell(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        return f"{value:.3f}" if abs(value) < 100 else f"{value:.1f}"
    return str(value).replace("|", "\\|")


def _table(header: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _columns(rows: dict[str, dict], nested: bool) -> list[str]:
    cols: list[str] = []
    for row in rows.values():
        for k, v in row.items():
            if isinstance(v, dict) == nested and k not in cols:
                cols.append(k)
    return cols


def metrics_tables(record: dict) -> str:
    """Markdown for a record's metrics: main table, breakdown tables, scalars."""
    metrics = record["metrics"]
    rows = {k: v for k, v in metrics.items() if isinstance(v, dict)}
    scalars = {k: v for k, v in metrics.items() if not isinstance(v, dict)}
    parts = []
    if rows:
        cols = _columns(rows, nested=False)
        parts.append(_table(["config", *cols], [[name, *(r.get(c) for c in cols)]
                                                for name, r in rows.items()]))
        for field in _columns(rows, nested=True):
            sub = {name: r[field] for name, r in rows.items() if isinstance(r.get(field), dict)}
            keys = list(dict.fromkeys(k for d in sub.values() for k in d))
            parts.append(f"**{field}**\n\n" + _table(
                ["config", *keys], [[name, *(d.get(k) for k in keys)] for name, d in sub.items()]
            ))
    if scalars:
        parts.append("\n".join(f"- {k}: {_cell(v)}" for k, v in scalars.items()))
    return "\n\n".join(parts)


def render_record(record: dict) -> str:
    versions = ", ".join(f"{k} {v}" for k, v in record["versions"].items())
    params = ", ".join(f"{k}={json.dumps(v) if isinstance(v, (dict, list)) else v}"
                       for k, v in record["params"].items())
    return "\n\n".join(filter(None, [
        f"## {record['date']} · {record['run']}",
        f"- git: `{record['git_sha']}`\n- hardware: {record['hardware']}\n"
        f"- versions: {versions}\n- params: {params}",
        metrics_tables(record),
        f"_Notes:_ {record['notes']}" if record["notes"] else "",
    ]))


def render(area: str, root: Path | None = None) -> Path:
    """Write results/<area>/README.md from every run JSON there (newest first)."""
    if area not in AREAS:
        raise ValueError(f"unknown area {area!r}; one of {AREAS}")
    area_dir = Path(root or RESULTS_ROOT) / area
    area_dir.mkdir(parents=True, exist_ok=True)
    records = [load(p) for p in sorted(area_dir.glob("*.json"), reverse=True)]
    head = (
        f"# {area} results\n\n"
        f"Generated by `python -m tools.results render {area}` from the run JSON files in "
        "this directory (contract: docs/AGENT_HANDOFF.md §4.3). Do not edit by hand."
    )
    body = [render_record(r) for r in records] or ["**not run** — no run JSON recorded yet."]
    out = area_dir / "README.md"
    out.write_text("\n\n".join([head, *body]) + "\n")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.results")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("render").add_argument("area", choices=AREAS)
    sub.add_parser("validate").add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)

    if args.cmd == "render":
        print(f"wrote {render(args.area)}")
        return 0
    status = 0
    for f in args.files:
        errors = validate(json.loads(f.read_text()))
        print(f"{f}: " + ("ok" if not errors else "; ".join(errors)))
        status |= bool(errors)
    return status


if __name__ == "__main__":
    sys.exit(main())
