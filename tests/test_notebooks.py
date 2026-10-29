"""Committed notebooks carry no outputs.

A notebook's outputs are the agent's answers, including its answers to held-out events, so
they must never be committed. `make strip-notebooks` clears them.
"""

import json
from pathlib import Path

import pytest

NOTEBOOKS = sorted((Path(__file__).resolve().parents[1] / "notebooks").glob("*.ipynb"))


def test_there_is_a_notebook_to_check():
    assert any(p.name == "change-analysis.ipynb" for p in NOTEBOOKS)


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_a_committed_notebook_has_no_outputs(path):
    for i, cell in enumerate(json.loads(path.read_text())["cells"]):
        if cell["cell_type"] == "code":
            assert cell["outputs"] == [], (path.name, i)
            assert cell["execution_count"] is None, (path.name, i)
