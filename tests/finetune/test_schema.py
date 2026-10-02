"""summary_v1: the schema accepts a well-formed label and rejects each way of being wrong."""

import copy

import pytest

from models.finetune.schema import CATEGORIES, METRIC_NAMES, PRODUCT_LINES, RESOLUTIONS, validate_summary

TEXT = "AGENT: Your balance is $1,234.56. MEMBER: Thanks."
GOOD = {
    "reason_for_call": "account_balance",
    "product_line": "banking",
    "metric_mentioned": [{"name": "balance", "value": "$1,234.56"}],
    "resolution": "information_provided",
    "follow_up": {"needed": False, "action": None},
}


def mutate(**changes):
    label = copy.deepcopy(GOOD)
    label.update(changes)
    return label


def test_well_formed_label_is_valid():
    assert validate_summary(GOOD, TEXT) == []


@pytest.mark.parametrize(
    "label",
    [
        {k: v for k, v in GOOD.items() if k != "resolution"},  # missing key
        {**GOOD, "extra": 1},  # extra key
        mutate(reason_for_call="weather"),  # not a category
        mutate(product_line="crypto"),
        mutate(resolution="solved"),
        mutate(metric_mentioned=[{"name": "balance"}]),  # missing value
        mutate(metric_mentioned=[{"name": "nonsense", "value": "$1,234.56"}]),
        mutate(follow_up={"needed": True, "action": None}),  # needed but no action
        mutate(follow_up={"needed": False, "action": "Call back."}),  # action but not needed
        mutate(follow_up={"needed": "yes", "action": None}),
        "not an object",
    ],
)
def test_malformed_label_is_rejected(label):
    assert validate_summary(label, TEXT)


def test_metric_value_must_occur_verbatim_in_the_transcript():
    invented = mutate(metric_mentioned=[{"name": "balance", "value": "$1234.56"}])
    errors = validate_summary(invented, TEXT)
    assert len(errors) == 1 and "verbatim" in errors[0]


def test_enums_have_the_documented_sizes():
    assert len(CATEGORIES) == 14
    assert len(PRODUCT_LINES) == 7  # six lines of business plus unknown
    assert len(RESOLUTIONS) == 6
    assert set(METRIC_NAMES)  # glossary is non-empty
