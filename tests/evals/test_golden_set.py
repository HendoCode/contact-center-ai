"""Offline checks on the L2 golden set: schema, mix, and reproducibility from committed data."""

import json
from collections import Counter
from pathlib import Path

import pytest
import yaml

pytest.importorskip("langgraph", reason="install the agent group: uv sync --extra dev --group agent")

from evals.datasets import build_golden  # noqa: E402
from evals.datasets.build_golden import (  # noqa: E402
    GOLDEN_PATH, KINDS, METRICS_PATH, ROUTE_BY_KIND, TERMS_PATH,
)
from evals.run import ensure_corpus, load_golden  # noqa: E402

FIELDS = [
    "id", "kind", "question", "clarify_with", "route", "expect_interrupt", "interrupts",
    "metric_names", "expect_sql", "call_id", "expect_citations", "categories", "reference",
    "derivation",
]


@pytest.fixture(scope="module")
def items():
    ensure_corpus()
    return load_golden()


@pytest.fixture(scope="module")
def corpus_ids():
    ensure_corpus()
    return {c["call_id"] for c in build_golden.load_calls()}


def test_every_item_has_the_schema(items):
    for item in items:
        assert list(item) == FIELDS, item["id"]
        assert item["kind"] in KINDS
        assert item["route"] == ROUTE_BY_KIND[item["kind"]]
        assert item["expect_interrupt"] == bool(item["interrupts"])
        assert item["expect_sql"] == (item["route"] == "resolve_metric")
        assert item["reference"] and item["derivation"]


def test_mix_covers_every_kind(items):
    assert 45 <= len(items) <= 55
    counts = Counter(item["kind"] for item in items)
    assert set(counts) == set(KINDS)
    assert min(counts.values()) >= 8
    assert len({item["question"] for item in items}) == len(items)
    assert len({item["id"] for item in items}) == len(items)


def test_golden_set_reproduces_from_committed_data(items):
    """agent_golden.jsonl is exactly what build_golden derives; nothing was hand-edited."""
    assert build_golden.dumps(build_golden.build()) == GOLDEN_PATH.read_text()


def test_check_mode_flags_a_stale_file(items, tmp_path, monkeypatch, capsys):
    assert build_golden.main(["--check"]) == 0
    stale = tmp_path / "agent_golden.jsonl"
    stale.write_text(GOLDEN_PATH.read_text().replace('"route": "retrieve"', '"route": "x"', 1))
    monkeypatch.setattr(build_golden, "GOLDEN_PATH", stale)
    assert build_golden.main(["--check"]) == 1
    assert "stale" in capsys.readouterr().out


def test_derivation_does_not_use_the_agent():
    """Expectations come from data files, not from the code under test."""
    source = Path(build_golden.__file__).read_text(encoding="utf-8")
    assert "import agent" not in source and "from agent" not in source


def test_metric_expectations_are_declared_metrics(items):
    declared = {m["name"] for m in yaml.safe_load(METRICS_PATH.read_text())["metrics"]}
    for item in items:
        assert set(item["metric_names"]) <= declared, item["id"]
        for i in item["interrupts"]:
            assert set(i["options"]) <= declared, item["id"]


def test_interrupt_options_are_candidates_of_their_term(items):
    terms = yaml.safe_load(TERMS_PATH.read_text())["terms"]
    for item in items:
        for i in item["interrupts"]:
            assert set(i["options"]) <= set(terms[i["term"]]["candidates"]), item["id"]
            assert len(i["options"]) >= 2, item["id"]
        if item["kind"] == "ambiguous":
            assert item["clarify_with"] and set(item["clarify_with"]) == set(item["metric_names"])
        if item["kind"] == "metric":
            assert item["clarify_with"] == item["metric_names"]


def test_call_lookups_point_at_generator_calls(items, corpus_ids):
    lookups = [item for item in items if item["kind"] == "call_lookup"]
    for item in lookups:
        assert item["call_id"].lower() in item["question"].lower()
        assert (item["call_id"] in corpus_ids) == item["expect_citations"], item["id"]
    assert any(not item["expect_citations"] for item in lookups)  # one id that does not exist


def test_open_questions_name_real_categories(items):
    ensure_corpus()
    categories = {c["category"] for c in build_golden.load_calls()}
    for item in items:
        if item["kind"] == "open":
            assert item["categories"] and set(item["categories"]) <= categories
            assert item["expect_citations"] and not item["interrupts"]


@pytest.mark.parametrize("question, names, interrupts", [
    ("What is our average rate?", [], [("rate", 7)]),
    ("What is our first contact resolution rate?", ["first_contact_resolution_rate"], []),
    ("What is the average savings rate?", ["average_deposit_apy"], []),
    ("What is the checking available balance?", ["checking_available_balance"], []),
    ("How many rate locks expired?", ["rate_locks_expired"], []),
])
def test_expected_resolution_reads_the_data_files(question, names, interrupts):
    metrics, terms = build_golden.load_metrics(), build_golden.load_terms()
    got_names, got_interrupts = build_golden.expected_resolution(question, metrics, terms)
    assert got_names == names
    assert [(i["term"], len(i["options"])) for i in got_interrupts] == interrupts


def test_jsonl_is_one_object_per_line():
    for line in GOLDEN_PATH.read_text().splitlines():
        assert isinstance(json.loads(line), dict)
