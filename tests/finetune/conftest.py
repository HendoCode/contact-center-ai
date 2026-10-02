"""Offline fixtures for the F1 dataset tests: the generator corpus and a scripted fake teacher."""

import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from models.finetune import data_gen
from models.finetune.gold import DEFAULT_DATA_DIR, REPO_ROOT

NEEDED = ["transcripts", "interactions", "interaction_accounts", "accounts", "products"]


@pytest.fixture(scope="session")
def data_dir() -> Path:
    """The seed-42 generator output, generated on demand (deterministic, offline)."""
    if not all((DEFAULT_DATA_DIR / f"{n}.json").exists() for n in NEEDED):
        subprocess.run([sys.executable, "data/synthetic/generate_data.py"], cwd=REPO_ROOT, check=True,
                       capture_output=True, timeout=300)
    return DEFAULT_DATA_DIR


@pytest.fixture(scope="session")
def built_dir(data_dir, tmp_path_factory) -> Path:
    """`build` output, shared read-only across tests; copy it before writing."""
    out = tmp_path_factory.mktemp("finetune_built")
    data_gen.build(data_dir, out)
    return out


@pytest.fixture
def out_dir(built_dir, tmp_path) -> Path:
    """A private, writable copy of the built dataset."""
    dest = tmp_path / "finetune"
    shutil.copytree(built_dir, dest)
    return dest


class FakeTeacher:
    """A stand-in chat model: answers from a text -> label table, or from a per-call script.

    `script(call_number, text, gold)` may return a str reply, or raise to simulate an API error.
    """

    model = "fake-teacher"

    def __init__(self, out_dir: Path, script=None):
        self.gold = {r["text"]: r["gold"] for r in data_gen.read_jsonl(out_dir / data_gen.GOLD)}
        self.script = script
        self.calls: list[list] = []
        self._lock = threading.Lock()

    def invoke(self, messages):
        with self._lock:
            self.calls.append(messages)
            n = len(self.calls)
        text = messages[1].content.removeprefix("Transcript:\n")
        gold = self.gold[text]
        reply = self.script(n, text, gold) if self.script else json.dumps(gold)
        return SimpleNamespace(content=reply)


@pytest.fixture
def teacher(out_dir) -> FakeTeacher:
    return FakeTeacher(out_dir)


def no_sleep(_seconds: float) -> None:
    """Replaces time.sleep so retry backoff costs nothing in tests."""
