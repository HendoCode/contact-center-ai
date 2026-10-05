"""tools/evals-retrieval-experiment.sh with stand-in make and uv: order, settings, run names,
the comparison by name, --k10, --dry-run, and stopping at the first failure."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "evals-retrieval-experiment.sh"

FAKE_MAKE = """#!/usr/bin/env bash
printf '%s|%s|%s|%s\\n' "${RETRIEVER_BACKEND:-}" "${AGENT_RETRIEVAL_MODE:-}" "${AGENT_RETRIEVAL_K:-}" "$*" >> "$LOG"
[[ -n "${FAIL_ON:-}" && "$*" == *"$FAIL_ON"* ]] && exit 1
exit 0
"""
FAKE_UV = """#!/usr/bin/env bash
echo '==> Estimated cost for 12 question(s): $0.11 to $0.27'
"""


def run(tmp_path, *args, **env):
    for name, body in (("make", FAKE_MAKE), ("uv", FAKE_UV)):
        (tmp_path / name).write_text(body)
        (tmp_path / name).chmod(0o755)
    full = {**os.environ, "MAKE": str(tmp_path / "make"), "UV": str(tmp_path / "uv"),
            "LOG": str(tmp_path / "log"), **env}
    res = subprocess.run([str(SCRIPT), *args], capture_output=True, text=True, env=full)
    log = (tmp_path / "log").read_text().splitlines() if (tmp_path / "log").exists() else []
    return res, log


def test_runs_in_order_and_compares_by_name(tmp_path):
    res, log = run(tmp_path)
    assert res.returncode == 0, res.stderr
    assert "Total estimate for 2 open-group runs: $0.22 to $0.54" in res.stdout
    assert log == [
        "pgvector|vector|5|-s evals-live ARGS=--group open --run open-pgvector-vector",
        "lancedb|||-s ingest",
        "lancedb|hybrid|5|-s evals-live ARGS=--group open --run open-lance-hybrid",
        "|||-s evals-compare A=open-pgvector-vector B=open-lance-hybrid",
    ]


def test_k10_adds_a_run_and_a_comparison(tmp_path):
    res, log = run(tmp_path, "--k10")
    assert res.returncode == 0, res.stderr
    assert "Total estimate for 3 open-group runs: $0.33 to $0.81" in res.stdout
    assert "lancedb|hybrid|10|-s evals-live ARGS=--group open --run open-lance-hybrid-k10" in log
    assert log[-1] == "|||-s evals-compare A=open-lance-hybrid B=open-lance-hybrid-k10"


def test_dry_run_only_dry_runs(tmp_path):
    res, log = run(tmp_path, "--dry-run")
    assert res.returncode == 0, res.stderr
    assert all("DRY=1" in line for line in log) and len(log) == 2
    assert "would ingest: RETRIEVER_BACKEND=lancedb make ingest" in res.stdout
    assert "would compare: make evals-compare A=open-pgvector-vector B=open-lance-hybrid" in res.stdout


def test_first_failure_stops_with_the_fix(tmp_path):
    res, log = run(tmp_path, FAIL_ON="ingest")
    assert res.returncode == 1
    assert len(log) == 2  # the pgvector run, then the failed ingest; nothing after
    assert "evals-retrieval-experiment: ingest LanceDB store failed: check the embedding provider" in res.stderr
