"""Gold labels come from generator output plus gold_rules.yml, cover all 14 categories, and stay verbatim."""

import copy
import re
from collections import Counter

import pytest

from models.finetune.gold import (
    build_all_gold,
    build_gold,
    load_calls,
    load_dialogue_templates,
    load_rules,
    spoken_facts,
)
from models.finetune.schema import CATEGORIES, METRIC_NAMES, validate_summary


@pytest.fixture(scope="module")
def rules():
    return load_rules()


@pytest.fixture(scope="module")
def templates():
    return load_dialogue_templates()


@pytest.fixture(scope="module")
def calls(data_dir):
    return load_calls(data_dir)


@pytest.fixture(scope="module")
def gold(calls, rules, templates):
    return build_all_gold(calls, rules, templates)


def test_schema_categories_match_the_generator(templates):
    assert sorted(CATEGORIES) == sorted(templates)


def test_every_call_gets_a_valid_gold_label(calls, gold):
    assert len(gold) == len(calls) == 1250
    for call in calls:
        assert validate_summary(gold[call.call_id], call.text) == []


def test_gold_rules_cover_all_14_categories_and_every_category_occurs(calls, gold, rules):
    assert sorted(rules["categories"]) == sorted(CATEGORIES)
    assert Counter(c.category for c in calls).keys() == set(CATEGORIES)
    assert {g["reason_for_call"] for g in gold.values()} == set(CATEGORIES)


def test_rules_name_every_figure_each_template_speaks(rules, templates):
    """A new {placeholder} in a dialogue must be either a metric or explicitly ignored."""
    ignored = set(rules["ignored_facts"])
    for category, turns in templates.items():
        spoken = {k for _, text in turns for k in re.findall(r"\{(\w+)\}", text)} - ignored
        assert spoken == set(rules["categories"][category]["metrics"]), category


def test_every_metric_name_in_the_glossary_is_used(rules):
    used = {m for rule in rules["categories"].values() for m in rule["metrics"]}
    assert used == set(METRIC_NAMES)


def test_metric_values_are_verbatim_in_the_text(calls, gold):
    for call in calls:
        for metric in gold[call.call_id]["metric_mentioned"]:
            assert metric["value"] in call.text


def test_product_line_follows_the_subject_account_with_documented_exceptions(calls, gold):
    for call in calls:
        line = gold[call.call_id]["product_line"]
        if call.category == "account_opening":
            assert line == "banking"
        elif call.category == "rate_lock_status":
            assert line == "unknown"
        else:
            assert line == call.subject_lob


def test_resolution_and_follow_up_are_fixed_per_category(calls, gold):
    seen: dict[str, set] = {}
    for call in calls:
        g = gold[call.call_id]
        seen.setdefault(call.category, set()).add((g["resolution"], g["follow_up"]["action"]))
    assert all(len(v) == 1 for v in seen.values())


def test_a_drifted_transcript_fails_the_build_instead_of_mislabeling(calls, rules, templates):
    call = next(c for c in calls if c.category == "account_balance")
    turns = copy.deepcopy(call.turns)
    turns[3]["text"] = "Your balance is high."
    drifted = type(call)(call.call_id, call.category, call.text, turns, call.subject_lob)
    with pytest.raises(ValueError, match="does not match its template"):
        spoken_facts(drifted, templates)
    with pytest.raises(ValueError):
        build_gold(drifted, rules, templates)
