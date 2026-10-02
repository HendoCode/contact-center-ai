"""
`summary_v1`: the task schema for the fine-tune-or-retrieve experiment (W1).

A call transcript goes in; one strict JSON object comes out. Every key is
required and no extra keys are allowed. Gold labels, teacher labels and every
experiment arm are validated against this one schema, so "valid" means the
same thing everywhere.

Beyond the JSON Schema, `validate_summary()` asserts that every
`metric_mentioned` value occurs verbatim in the transcript text. The schema
alone cannot check that, and a value the model invented is the failure this
task is meant to expose.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_VERSION = "summary_v1"

# The 14 generator categories (data/synthetic/generate_data.py CATEGORY_WEIGHTS);
# tests/finetune/test_gold.py asserts this list matches the generator.
CATEGORIES = [
    "account_balance",
    "online_banking_support",
    "card_services",
    "fraud_dispute",
    "payment_assistance",
    "rate_inquiry",
    "loan_inquiry",
    "mortgage_inquiry",
    "insurance_service",
    "investment_review",
    "account_opening",
    "fee_dispute",
    "escrow_analysis",
    "rate_lock_status",
]

# The generator's six lines of business, plus "unknown" for calls whose text
# carries no cue for the line of business.
PRODUCT_LINES = ["mortgage", "home", "car_insurance", "banking", "cards", "investments", "unknown"]

# value -> definition (the definitions are also shown to the labeling model).
RESOLUTIONS = {
    "information_provided": "the agent only read out or explained account information",
    "action_completed": "the agent states an action is already done (a reset link sent, a fee refunded)",
    "dispute_opened": "the agent opened a dispute or investigation for the member",
    "request_submitted": (
        "the agent submitted or started a request that is still processing "
        "(a hardship deferral, a new account being set up)"
    ),
    "offer_pending": "the agent offered something (a quote, a pre-qualification) the member has not accepted",
    "review_needed": "the agent found something that still needs a closer review",
}

# name -> definition. Names are keys of the generator's dialogue facts, so a
# value is always spoken in the transcript and gold labels can be built by code.
METRIC_NAMES = {
    "balance": (
        "the main balance the agent states for the account in question, whatever the product "
        "(ledger, outstanding, principal, drawn, market value, annual premium)"
    ),
    "secondary_balance": (
        "the second balance the agent states right after the main one "
        "(available balance, available credit, escrow, settled cash, deductible)"
    ),
    "ledger_balance": "the ledger balance read out while helping with online banking access",
    "credit_limit": "a card's credit limit",
    "outstanding_balance": "a card's outstanding balance",
    "available_credit": "a card's available credit",
    "purchase_apr": "a card's purchase APR",
    "payment_amount": "the payment amount the agent states (minimum, scheduled or estimated payment)",
    "rate": "the interest rate or yield the agent states for the account (APY, APR, note rate, return)",
    "note_rate": "a mortgage's note rate",
    "current_principal": "a mortgage's current principal balance",
    "ltv": "a mortgage's loan-to-value ratio",
    "escrow_balance": "an escrow balance named as such",
    "monthly_pi": "a mortgage's monthly principal-and-interest payment",
    "annual_premium": "an insurance policy's annual premium",
    "monthly_premium": "an insurance policy's monthly premium",
    "deductible": "an insurance policy's deductible, named as such",
    "market_value": "an investment account's market value",
    "cash_value": "an investment account's settled cash",
    "ytd_return": "an investment account's year-to-date return",
    "new_account_apy": "the APY offered on a new account the member is opening",
    "monthly_fee": "an account's monthly fee",
    "waiver_min_balance": "the minimum balance that waives the monthly fee",
    "locked_rate": "a locked mortgage rate",
    "expires_at": "the date a rate lock runs through",
    "loan_amount": "the loan amount on an application",
}

SUMMARY_V1_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": SCHEMA_VERSION,
    "type": "object",
    "additionalProperties": False,
    "required": ["reason_for_call", "product_line", "metric_mentioned", "resolution", "follow_up"],
    "properties": {
        "reason_for_call": {"enum": CATEGORIES},
        "product_line": {"enum": PRODUCT_LINES},
        "metric_mentioned": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "value"],
                "properties": {
                    "name": {"enum": list(METRIC_NAMES)},
                    "value": {"type": "string", "minLength": 1},
                },
            },
        },
        "resolution": {"enum": list(RESOLUTIONS)},
        "follow_up": {
            "type": "object",
            "additionalProperties": False,
            "required": ["needed", "action"],
            "properties": {
                "needed": {"type": "boolean"},
                "action": {"type": ["string", "null"]},
            },
            # `action` is a non-empty sentence exactly when a follow-up is needed.
            "if": {"properties": {"needed": {"const": True}}, "required": ["needed"]},
            "then": {"properties": {"action": {"type": "string", "minLength": 1}}},
            "else": {"properties": {"action": {"type": "null"}}},
        },
    },
}

_VALIDATOR = Draft202012Validator(SUMMARY_V1_SCHEMA)


def schema_sha256() -> str:
    """Hash of the canonical schema JSON, recorded in the dataset manifest."""
    canonical = json.dumps(SUMMARY_V1_SCHEMA, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def validate_summary(label: Any, full_text: str) -> list[str]:
    """Return every reason `label` is not a valid summary_v1 for `full_text` (empty = valid)."""
    errors = [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in sorted(_VALIDATOR.iter_errors(label), key=lambda e: list(map(str, e.absolute_path)))
    ]
    if errors:
        return errors
    for i, metric in enumerate(label["metric_mentioned"]):
        if metric["value"] not in full_text:
            errors.append(f"metric_mentioned/{i}/value: {metric['value']!r} does not occur verbatim in the transcript")
    return errors
