"""
Semantic-layer MCP tools over the committed MetricFlow catalog.

These are the demo-engine tools that make GenAI answers to aggregate metric
questions *grounded*: the number always comes from a declared metric in
`olap/dbt/models/marts/semantic/metrics.yml`, executed by the repo's `mf`
(MetricFlow) CLI, never improvised by the LLM.

Tools:
    query_metric      — run one or more DECLARED metrics and return the result
                        table AND the generated SQL (`mf query` + `mf query
                        --explain`).
    ask_the_analyst   — resolve a natural-language question to DECLARED metric
                        names (seeded with the metrics.yml descriptions via the
                        shared `LLM_PROVIDER` LLM), then execute them through
                        `query_metric` and return a grounded answer.
"""

import csv
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

from rag.pipeline import get_llm

REPO_ROOT = Path(__file__).resolve().parent.parent
DBT_DIR = REPO_ROOT / "olap" / "dbt"
METRICS_YAML = DBT_DIR / "models" / "marts" / "semantic" / "metrics.yml"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


# ── MetricFlow CLI ───────────────────────────────────────────────────────────

def _mf_binary() -> str:
    """Return the path to the repo's `mf` (MetricFlow) CLI."""
    venv_mf = REPO_ROOT / ".venv" / "bin" / "mf"
    if venv_mf.exists():
        return str(venv_mf)
    found = shutil.which("mf")
    if found:
        return found
    return "mf"


# `mf` reads target/semantic_manifest.json and queries the profile's default target, dev
# (Postgres). A warehouse build that wrote target/ would leave Snowflake or Databricks SQL
# there (Databricks quotes with backticks) and every metric query would fail on Postgres.
LOCAL_ADAPTER = "postgres"
PARSE_HINT = "run 'cd olap/dbt && uv run --group dbt dbt parse' to rebuild it for the local Postgres target"


def manifest_problem(dbt_dir: Path | None = None) -> str | None:
    """Why `mf` cannot query local Postgres with the current target/ artifacts, or None."""
    target = (dbt_dir or DBT_DIR) / "target"
    semantic = target / "semantic_manifest.json"
    if not semantic.exists():
        return f"no {semantic.relative_to(REPO_ROOT)}: {PARSE_HINT}"
    adapter = None
    manifest = target / "manifest.json"
    if manifest.exists():
        try:
            adapter = json.loads(manifest.read_text()).get("metadata", {}).get("adapter_type")
        except (OSError, ValueError):
            adapter = None
    if adapter is None and "`" in semantic.read_text():
        adapter = "databricks"  # backtick-quoted relation names; Postgres and Snowflake use "
    if adapter and adapter != LOCAL_ADAPTER:
        return (f"{semantic.relative_to(REPO_ROOT)} was built for {adapter}, but metric queries "
                f"run on the local {LOCAL_ADAPTER}: {PARSE_HINT}")
    return None


def run_metricflow(args: list[str]) -> str:
    """Run `mf` from the dbt project dir and return cleaned stdout."""
    completed = subprocess.run(
        [_mf_binary(), *args],
        cwd=str(DBT_DIR),
        capture_output=True,
        text=True,
        timeout=300,
    )
    out = _ANSI_RE.sub("", completed.stdout)
    err = _ANSI_RE.sub("", completed.stderr)
    if completed.returncode != 0:
        hint = (
            " (did you run `uv run --group dbt dbt parse` in olap/dbt to emit "
            "target/semantic_manifest.json?)"
        )
        raise RuntimeError(
            f"MetricFlow query failed (exit {completed.returncode}):\n{out}\n{err}{hint}"
        )
    return out


def _extract_sql(explain_output: str) -> str:
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


# ── Metric catalog ───────────────────────────────────────────────────────────

def load_metric_catalog() -> dict[str, dict]:
    """Parse metrics.yml into {name: {label, description, type}}."""
    with open(METRICS_YAML, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    catalog: dict[str, dict] = {}
    for m in data.get("metrics", []):
        catalog[m["name"]] = {
            "label": m.get("label", m["name"]),
            "description": " ".join(str(m.get("description", "")).split()),
            "type": m.get("type", "unknown"),
        }
    return catalog


# ── query_metric ─────────────────────────────────────────────────────────────

def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


def query_metric_raw(
    metric_names,
    group_by=None,
    limit=None,
) -> dict:
    """
    Execute one or more DECLARED metrics and return the structured result.

    Returns {"metric_names", "columns", "rows", "sql"} where rows is parsed
    from `mf query --csv` and sql is extracted from `mf query --explain`.
    """
    names = _as_list(metric_names)
    if not names:
        raise ValueError("query_metric requires at least one metric name")

    catalog = load_metric_catalog()
    unknown = [n for n in names if n not in catalog]
    if unknown:
        raise ValueError(
            f"Unknown metric name(s): {unknown}. Only DECLARED metrics from "
            f"metrics.yml may be queried; available: {sorted(catalog)}"
        )

    base = ["query", "--metrics", ",".join(names)]
    if group_by:
        base += ["--group-by", ",".join(_as_list(group_by))]
    if limit is not None:
        base += ["--limit", str(int(limit))]

    problem = manifest_problem()
    if problem:
        raise RuntimeError(problem)

    with tempfile.TemporaryDirectory() as td:
        csv_path = os.path.join(td, "metricflow_result.csv")
        run_metricflow(base + ["--csv", csv_path])
        with open(csv_path, newline="") as f:
            rows = list(csv.reader(f))
        sql = _extract_sql(run_metricflow(base + ["--explain"]))

    header = rows[0] if rows else []
    data = rows[1:] if len(rows) > 1 else []
    return {
        "metric_names": names,
        "columns": header,
        "rows": data,
        "sql": sql,
    }


def _fmt_number(value, decimals) -> str:
    if decimals is not None:
        try:
            return f"{float(value):.{int(decimals)}f}"
        except (TypeError, ValueError):
            pass
    return str(value)


def _render_result(result: dict, decimals=None) -> str:
    header = result["columns"]
    rows = result["rows"]
    lines = [f"MetricFlow result ({len(result['metric_names'])} metric(s)):"]

    if not rows:
        lines.append("(no rows returned)")
    elif len(rows) == 1:
        prefix = "" if len(header) == 1 else "  "
        for col, val in zip(header, rows[0]):
            if len(header) == 1:
                lines.append(f"{col}: {_fmt_number(val, decimals)}")
            else:
                lines.append(f"  {col}: {_fmt_number(val, decimals)}")
    else:
        grid = [header] + rows
        widths = [
            max(len(_fmt_number(r[i], decimals)) for r in grid)
            for i in range(len(header))
        ]
        lines.append("  ".join(h.ljust(widths[i]) for i, h in enumerate(header)))
        lines.append("  ".join("-" * widths[i] for i in range(len(header))))
        for row in rows:
            lines.append(
                "  ".join(_fmt_number(v, decimals).ljust(widths[i]) for i, v in enumerate(row))
            )

    if result["sql"].strip():
        lines.append("\nGenerated SQL:")
        lines.append(result["sql"].strip())
    return "\n".join(lines)


def query_metric(
    metrics,
    group_by=None,
    decimals=None,
    limit=None,
) -> str:
    """
    MCP tool: execute DECLARED metric(s) via MetricFlow and return the result
    table plus the generated SQL produced by the semantic layer.

    Args:
        metrics: metric name(s) from metrics.yml (list or comma-separated string)
        group_by: optional dimensions/entities to group by
        decimals: optional fixed-decimal rounding for the displayed numbers
        limit: optional row limit
    """
    try:
        result = query_metric_raw(metrics, group_by=group_by, limit=limit)
    except (ValueError, RuntimeError) as exc:
        return f"TOOL ERROR (query_metric): {exc}"
    return _render_result(result, decimals=decimals)


# ── ask_the_analyst ──────────────────────────────────────────────────────────

def _parse_metric_list(text: str, known: set[str]) -> list[str]:
    """Best-effort extraction of a JSON array of metric names from LLM output."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"```\s*$", "", cleaned).strip()
    match = _JSON_ARRAY_RE.search(cleaned)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    resolved: list[str] = []
    for name in parsed:
        if isinstance(name, str):
            n = name.strip()
            if n in known and n not in resolved:
                resolved.append(n)
    return resolved


def resolve_metrics(question: str) -> list[str]:
    """
    Resolve a natural-language question to DECLARED metric names using the
    shared LLM (LLM_PROVIDER swap machinery), seeded with the metrics.yml
    descriptions as retrieval context.
    """
    catalog = load_metric_catalog()
    catalog_lines = "\n".join(
        f"- {name} ({m['label']}): {m['description']}"
        for name, m in catalog.items()
    )
    prompt = (
        "You are a semantic-layer resolver for a credit-union analytics demo. "
        "Map the QUESTION to one or more DECLARED metric names chosen ONLY "
        "from the catalog below. "
        "The catalog deliberately has NO bare ambiguous names: a word like "
        "'interest rate', 'balance', 'limit', or 'LCV' must resolve to the "
        "specific lob-qualified metrics — never to a single blended number.\n\n"
        f"METRIC CATALOG:\n{catalog_lines}\n\n"
        f"QUESTION: {question}\n\n"
        "Return ONLY a JSON array of metric names from the catalog, for "
        'example ["average_mortgage_note_rate", "average_deposit_apy"]. '
        "If no metric fits, return []."
    )
    response = get_llm().invoke(prompt)
    text = response.content if hasattr(response, "content") else str(response)
    return _parse_metric_list(text, set(catalog))


def ask_the_analyst(question: str, decimals=None) -> str:
    """
    MCP tool: natural-language question -> DECLARED metric(s) -> executed
    result. The resolution is seeded with metrics.yml descriptions and the
    resolved metrics are executed through query_metric, so the returned
    answer is grounded in the semantic layer rather than improvised.
    """
    try:
        names = resolve_metrics(question)
    except Exception as exc:  # noqa: BLE001 — surface any LLM/provider error
        return f"ask_the_analyst error resolving metrics: {exc}"

    if not names:
        return (
            f'No DECLARED metric matches "{question}". The semantic layer has '
            "no bare ambiguous metric (no `interest_rate`, `balance`, `limit`, "
            "or `lcv`) — rephrase to the specific metric, or run `mf list "
            "metrics` to see the catalog."
        )

    catalog = load_metric_catalog()
    lines = [
        f'Question: "{question}"',
        f"Resolved to {len(names)} declared metric(s) — there is no single "
        "blended number; each word maps to N distinct, auditable metrics:",
        "",
    ]
    for name in names:
        lines.append(f"- {name} — {catalog[name]['description']}")
    lines.append("")

    try:
        result = query_metric_raw(names)
    except (ValueError, RuntimeError) as exc:
        lines.append(f"Execution failed: {exc}")
        return "\n".join(lines)

    lines.append(_render_result(result, decimals=decimals))
    return "\n".join(lines)
