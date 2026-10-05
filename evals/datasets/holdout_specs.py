"""Held-out questions for the agent evals: written once on 2026-10-05, before any score on
them was seen, from the synthetic data and the semantic layer only (metrics.yml labels,
agent/data/ambiguous_terms.yml, the transcript categories and outcomes), never from the
golden set's results or any run output.

Do not edit a question after its scores are seen. A factual error in a question may be
fixed; record each fix here with its date and reason.

Same spec format as SPECS in build_golden.py, whose code derives every expectation.
`python -m evals.datasets.build_golden --dataset holdout` writes agent_holdout.jsonl.

Fixes: none.
"""

HOLDOUT_SPECS: list[dict] = [
    # ambiguous (4)
    {"kind": "ambiguous", "question": "What rate are we charging on credit cards?",
     "clarify_with": ["average_credit_card_purchase_apr"]},
    {"kind": "ambiguous", "question": "What is our investment balance?",
     "clarify_with": ["investment_market_value"]},
    {"kind": "ambiguous", "question": "What's the LTV across our members?",
     "clarify_with": ["member_lifetime_value"]},
    {"kind": "ambiguous", "question": "How much balance do members keep in checking and savings?",
     "clarify_with": ["checking_available_balance", "savings_balance"]},

    # metric (5)
    {"kind": "metric", "question": "What is our total fee revenue?"},
    {"kind": "metric", "question": "How many promoters do we have?"},
    {"kind": "metric", "question": "What is the average credit card purchase APR?"},
    {"kind": "metric", "question": "What is our total interest income?"},
    {"kind": "metric", "question": "What are the HELOC credit limit and the credit card credit limit?"},

    # call_lookup (3)
    {"kind": "call_lookup", "question": "Summarize {call_id}.",
     "pick": {"category": "card_services", "outcome": "callback_scheduled"}},
    {"kind": "call_lookup", "question": "What did the member want on {call_id}?",
     "pick": {"category": "mortgage_inquiry", "outcome": "unresolved"}},
    {"kind": "call_lookup", "question": "Was {call_id} escalated?",
     "pick": {"category": "insurance_service", "outcome": "escalated"}},

    # open (4)
    {"kind": "open", "question": "Why do members call about their mortgages?",
     "categories": ["mortgage_inquiry"]},
    {"kind": "open", "question": "What do members ask when they apply for a loan?",
     "categories": ["loan_inquiry"]},
    {"kind": "open", "question": "Why do members get transferred when they call about their balance?",
     "categories": ["account_balance"]},
    {"kind": "open", "question": "What makes members need a callback about a payment problem?",
     "categories": ["payment_assistance"]},
]
