"""Held-out event content stays out of everything that shapes the agent or scores it.

The held-out events measure whether the agent handles a change it was never tuned for. If a
held-out driver's keywords, or any answer-key id, turn up in a prompt, in the agent's data, in
the change log or in an eval dataset, that measurement is spoiled. This guard scans those
surfaces on every run.

When a ticket adds a new surface that shapes a prompt or an eval, add its path (or glob) to
GUARDED below. That covers the change-analysis tool and the agent's change route: their prompts,
data tables, period grammar and subgraph.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Globs under the repo root. Everything a prompt, an agent lookup table or an eval row is built from.
GUARDED = [
    "agent/prompts/**/*",
    "agent/data/**/*",
    "agent/changes.py",
    "agent/nodes/classify.py",
    "agent/nodes/ground.py",
    "agent/subgraphs/change.py",
    "agent/state.py",
    "agent/demo_change.py",
    "agent/citation_view.py",
    "ccai_mcp/masking.py",
    "results/demo/*.json",
    "tools/demo_chat.py",
    "evals/datasets/*.jsonl",
    "evals/datasets/*.py",
    "evals/*.py",
    "evals/known_failures.json",
    "evals/README.md",
    "evals/prompts/**/*",
    "ccai_mcp/change.py",
    "notebooks/**/*",
]
# File types the guard reads (images and caches are skipped).
SUFFIXES = {".md", ".yml", ".yaml", ".json", ".jsonl", ".py", ".txt", ".toml", ".ipynb"}


def words(text: str) -> re.Pattern:
    return re.compile(rf"(?<![\w-]){re.escape(text)}(?![\w-])", re.IGNORECASE)


def forbidden(events_config) -> dict[str, re.Pattern]:
    """Held-out driver keywords and every answer-key id, as whole-word patterns."""
    found = {}
    for ev in events_config["events"]:
        found[ev["id"]] = words(ev["id"])
        for driver in ev["drivers"]:
            found[driver["id"]] = words(driver["id"])
            if ev["split"] == "heldout":
                for keyword in driver["keywords"]:
                    found[f"keyword:{keyword}"] = words(keyword)
            for pattern in driver.get("patterns", []):
                found[pattern["id"]] = words(pattern["id"])
                for variant in pattern["variants"]:
                    found[variant] = words(variant)
    return found


def guarded_files() -> list[Path]:
    files = {p for pattern in GUARDED for p in REPO_ROOT.glob(pattern)
             if p.is_file() and p.suffix in SUFFIXES and "__pycache__" not in p.parts}
    return sorted(files)


def hits(text: str, patterns: dict[str, re.Pattern]) -> list[str]:
    return [name for name, pattern in patterns.items() if pattern.search(text)]


def test_the_guard_covers_the_surfaces_that_exist():
    names = {p.relative_to(REPO_ROOT).as_posix() for p in guarded_files()}
    assert "agent/prompts/classify.md" in names
    assert "agent/data/ambiguous_terms.yml" in names
    assert {"evals/datasets/agent_golden.jsonl", "evals/datasets/agent_holdout.jsonl",
            "evals/datasets/build_golden.py", "evals/datasets/holdout_specs.py"} <= names
    assert {"agent/prompts/frame.md", "agent/data/change_measures.yml",
            "agent/data/change_subjects.yml", "agent/changes.py", "agent/subgraphs/change.py",
            "ccai_mcp/change.py", "agent/demo_change.py",
            "agent/citation_view.py", "ccai_mcp/masking.py",
            "tools/demo_chat.py"} <= names
    assert {"evals/datasets/change_golden.jsonl", "evals/datasets/change_holdout.jsonl",
            "evals/datasets/change_specs.py", "evals/datasets/change_holdout_specs.py",
            "evals/datasets/build_change.py", "evals/change_corpus.py", "evals/evaluators.py",
            "evals/offline.py", "evals/run.py", "evals/known_failures.json",
            "evals/README.md"} <= names


def test_the_forbidden_list_has_the_labels_and_the_held_out_keywords(events_config):
    patterns = forbidden(events_config)
    ids = [n for n in patterns if not n.startswith("keyword:")]
    assert len(ids) == 21                      # 4 events, 13 drivers, 1 pattern, 3 variants
    keywords = {n.removeprefix("keyword:") for n in patterns if n.startswith("keyword:")}
    assert keywords == {"re-enroll", "one-time passcode", "payees", "coin", "safe deposit box",
                        "notary", "cashier's check"}


def test_no_held_out_keyword_or_label_id_in_prompts_agent_data_or_eval_datasets(events_config):
    patterns = forbidden(events_config)
    for path in guarded_files():
        found = hits(path.read_text(), patterns)
        assert not found, (path.relative_to(REPO_ROOT).as_posix(), found)


def test_no_held_out_keyword_or_label_id_in_the_generated_change_log(corpus, events_config):
    assert not hits((corpus.dir / "change_log.json").read_text(), forbidden(events_config))


# Where drivers are discovered. Nothing here may carry any event's driver wording, golden or
# held out: the drivers an answer reports come from reading the calls, never from a list.
DISCOVERY = [
    "ccai_mcp/change.py",
    "agent/subgraphs/change.py",
    "agent/prompts/frame.md",
    "agent/prompts/classify.md",
    "agent/data/change_measures.yml",
    "agent/data/change_subjects.yml",
]


def driver_words(events_config) -> dict[str, re.Pattern]:
    found = {}
    for ev in events_config["events"]:
        for driver in ev["drivers"]:
            for keyword in driver["keywords"]:
                found[f"keyword:{keyword}"] = words(keyword)
            for pattern in driver.get("patterns", []):
                for variant_words in pattern["keywords"].values():
                    found.update({f"keyword:{k}": words(k) for k in variant_words})
    return found


def test_no_driver_wording_from_any_event_is_in_the_code_that_finds_drivers(events_config):
    patterns = driver_words(events_config)
    assert len(patterns) >= 15
    for name in DISCOVERY:
        found = hits((REPO_ROOT / name).read_text(), patterns)
        assert not found, (name, found)


@pytest.mark.parametrize("planted", [
    "re-enroll biometric login", "the One-Time Passcode never came", "bill pay payees",
    "a safe deposit box", "a notary", "cashier's check", "fee_schedule_change",
    "inconsistent_waiver_explanation", "once_ever",
])
def test_the_scanner_catches_a_planted_keyword_or_id(events_config, planted):
    assert hits(f"# note: {planted}.", forbidden(events_config))


def test_the_scanner_ignores_words_that_only_contain_a_keyword(events_config):
    assert not hits("bitcoin, coinbase, coins and notaries", forbidden(events_config))
