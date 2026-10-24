"""Generator runs for the event tests: three offline runs into temp folders, none in the repo."""

import json
import subprocess
from functools import cached_property
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "data" / "synthetic" / "generate_data.py"
EVENTS_YML = REPO_ROOT / "data" / "synthetic" / "events.yml"


def run_generator(out_dir: Path, events: Path = EVENTS_YML) -> Path:
    subprocess.run(
        [sys.executable, str(GENERATOR), "--output-dir", str(out_dir), "--events", str(events)],
        cwd=REPO_ROOT, check=True, capture_output=True, timeout=300,
    )
    return out_dir


@dataclass
class Corpus:
    """One generator run, read back."""

    dir: Path

    def load(self, name: str):
        return json.loads((self.dir / name).read_text())

    @cached_property
    def truth(self) -> dict:
        return self.load("ground_truth/events.json")

    @cached_property
    def transcripts(self) -> dict[str, dict]:
        return {t["call_id"]: t for t in self.load("transcripts.json")}

    @cached_property
    def interactions(self) -> dict[str, dict]:
        return {i["call_id"]: i for i in self.load("interactions.json")}


@pytest.fixture(scope="session")
def events_config() -> dict:
    return yaml.safe_load(EVENTS_YML.read_text())


@pytest.fixture(scope="session")
def corpus(tmp_path_factory) -> Corpus:
    return Corpus(run_generator(tmp_path_factory.mktemp("run_a")))


@pytest.fixture(scope="session")
def corpus_again(tmp_path_factory) -> Corpus:
    return Corpus(run_generator(tmp_path_factory.mktemp("run_b")))


@pytest.fixture(scope="session")
def baseline_only(tmp_path_factory) -> Corpus:
    """The same seed with no events: what the baseline stream produces on its own."""
    config = yaml.safe_load(EVENTS_YML.read_text())
    config["events"] = []
    events = tmp_path_factory.mktemp("no_events") / "events.yml"
    events.write_text(yaml.safe_dump(config, sort_keys=False))
    return Corpus(run_generator(tmp_path_factory.mktemp("run_base"), events))
