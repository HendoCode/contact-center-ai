"""
Regression guard for the published commands.

Every command shown in the README quickstart and the published "Anchoring AI"
posts must keep working. This module:

  * runs the seeded synthetic-data generator and asserts the corpus it emits
    (1250 transcripts, each with a `call_id` and `full_text`) — and that it is
    deterministic (two runs are bit-identical);
  * imports every package entry point that the published `python -m ...`
    commands dispatch to, without any network, API key, or database.

Marked `@pytest.mark.integration` nothing here — it is fully offline.
"""

import hashlib
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "synthetic"

EXPECTED_TRANSCRIPTS = 1_250

# Entry points of the published commands (`python -m <module>`). The generator
# itself is a script (not an importable package) and is exercised by the tests
# below via subprocess, exactly as the published command runs it.
PUBLISHED_MODULES = [
    "rag.embeddings",
    "rag.pipeline",
    "ccai_mcp.tools",
    "ccai_mcp.metrics",
    "ccai_mcp.server",
]


def _run_generator() -> int:
    """Run `python data/synthetic/generate_data.py` and return its exit code."""
    proc = subprocess.run(
        [sys.executable, "data/synthetic/generate_data.py"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"generator failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return proc.returncode


@pytest.fixture(scope="module")
def generated_corpus():
    """Run the generator once and read back transcripts.json and csat.json."""
    _run_generator()
    transcripts = json.loads((DATA_DIR / "transcripts.json").read_text())
    csat = json.loads((DATA_DIR / "csat.json").read_text())
    return transcripts, csat


def test_generator_emits_the_published_corpus(generated_corpus):
    transcripts, csat = generated_corpus
    assert len(transcripts) == EXPECTED_TRANSCRIPTS
    assert all(isinstance(t.get("call_id"), str) and t["call_id"] for t in transcripts)
    assert all(isinstance(t.get("full_text"), str) for t in transcripts)
    assert len(csat) > 0


def test_generator_is_deterministic():
    """Two generator runs are bit-identical (seeded RNG, fixed order)."""
    _run_generator()
    first = hashlib.sha256((DATA_DIR / "transcripts.json").read_bytes()).hexdigest()
    _run_generator()
    second = hashlib.sha256((DATA_DIR / "transcripts.json").read_bytes()).hexdigest()
    assert first == second


@pytest.mark.parametrize("module_name", PUBLISHED_MODULES)
def test_published_entry_points_import_without_network(module_name):
    """Every `python -m` entry point of a published command imports cleanly."""
    importlib.import_module(module_name)
