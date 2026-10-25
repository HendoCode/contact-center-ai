"""tools/demo_chat.py: the scripted metrics are declared, and the page is built from the capture."""

import json

import pytest

pytest.importorskip("fastmcp", reason="install the agent group: uv sync --extra dev --group agent")

from tools import demo_chat  # noqa: E402

from ccai_mcp.change import RESULT_MARKER, render_change  # noqa: E402
from ccai_mcp.metrics import load_metric_catalog  # noqa: E402
from retrieval.base import Hit  # noqa: E402

SQL_LCV = "SELECT relationship_revenue * 24 / active_members AS member_lifetime_value"
BALANCE_OUT = (
    "MetricFlow result (4 metric(s)):\n"
    "  banking_available_balance: 300.00\n  banking_ledger_balance: 310.00\n"
    "  credit_card_outstanding: 100.00\n  net_member_liquidity: 200.00\n\n"
    "Generated SQL:\nSELECT 1 /* balance */"
)
LCV_OUT = (
    "MetricFlow result (2 metric(s)):\n  loan_to_value: 70.0000\n"
    f"  member_lifetime_value: 1234.0000\n\nGenerated SQL:\n{SQL_LCV}"
)
CSAT_OUT = (
    "CSAT Summary (10 responses)\nAverage score: 3.50/5\n"
    "Score distribution: {1: 2, 2: 1, 3: 3, 4: 2, 5: 2}\nSample comments:\n  - ok"
)
CSAT_LOW = CSAT_OUT.replace("(10 responses)", "(3 responses)").replace("3.50", "1.33")
RATES = ["average_mortgage_note_rate", "average_deposit_apy", "average_credit_card_purchase_apr"]


CHANGE_RESULT = {
    "version": 1, "mode": "drivers", "metric": "csat_average_score",
    "filters": {"category": ["fee_dispute"]},
    "change_point": {"date": "2026-04-15", "source": "argument",
                     "logged_change": "Fee schedule change", "score": None, "reason": None},
    "windows": {
        "before": {"start": "2026-03-04", "end": "2026-04-15", "value": 3.8, "n": 10, "days": 42,
                   "sql": "SELECT 'before window'"},
        "after": {"start": "2026-04-15", "end": "2026-05-27", "value": 3.13, "n": 45, "days": 42,
                  "sql": "SELECT 'after window'", "weekly": [], "weekly_sql": "SELECT 'weeks'"},
    },
    "population": {"before": 11, "after": 63}, "coverage": {"read_before": 11, "read_after": 63},
    "drivers": [{"label": "Members did not know about the change", "definition": "A new charge.",
                 "before": {"n": 1, "call_ids": ["CALL-1"]}, "after": {"n": 2, "call_ids": ["CALL-2"]},
                 "citations": [{"call_id": "CALL-2", "quote": "Nobody told me."}]}],
    "patterns": [], "unassigned": {"before": 9, "after": 61}, "series": None,
    "limits": ["The data cannot show that the change caused the shift."],
}
CHANGE_OUT = f"{render_change(CHANGE_RESULT)}\n{RESULT_MARKER}\n{json.dumps(CHANGE_RESULT)}"


def _capture() -> dict:
    metrics = "\n".join(f"  {name}: {i}.0000" for i, name in enumerate(RATES, 1))
    return {
        "rates_call": Hit("CALL-1", "quoted 6.5% and $9.00", 1.0,
                          {"call_id": "CALL-1", "interaction_id": 7}),
        "balance_call": Hit("CALL-2", "your balance of $310.00", 1.0, {"call_id": "CALL-2"}),
        "member": {"member_id": 1, "name": "Test Member", "available": 300, "ledger": 310,
                   "principal": 9, "note_rate": 6.5, "ltv": 55.5, "csat_loan_call": 2},
        "csat_all": CSAT_OUT, "csat_low": CSAT_LOW,
        "rate_candidates": RATES,
        "analyst": f"Question: q\n\nMetricFlow result (3 metric(s)):\n{metrics}\n\n"
                   "Generated SQL:\nSELECT 2 /* rates */",
        "clarify": {
            "payload": {"kind": "clarify_metric", "version": 1},
            "result": {"answer": "MetricFlow result\n\nSQL:\nSELECT 3 /* graph */",
                       "route": "resolve_metric", "grounded": True,
                       "metric_names": demo_chat.CLARIFY_CHOICES, "sql": "SELECT 3 /* graph */"},
        },
        "fee_clarify": {
            "payload": {"kind": "clarify_metric", "version": 1},
            "result": {"answer": "MetricFlow result\n\nSQL:\nSELECT 4 /* fees */",
                       "route": "resolve_metric", "grounded": True,
                       "metric_names": demo_chat.FEE_CHOICES, "sql": "SELECT 4 /* fees */"},
        },
        "change": CHANGE_OUT,
        "balance": BALANCE_OUT, "lcv": LCV_OUT,
        "catalog": {n: {"label": n, "description": f"{n} description. More."}
                    for n in [*RATES, *demo_chat.LCV_METRICS]},
        "env": {"sha": "abc1234", "date": "2026-01-01", "python": "3.12.0", "postgres": "16",
                "versions": {"dbt-core": "1"}, "calls": 1250, "csat": 943},
    }


def test_scripted_metrics_are_declared_and_the_clarify_choices_are_offered():
    declared = set(load_metric_catalog())
    scripted = [*demo_chat.BALANCE_METRICS, *demo_chat.LCV_METRICS, *demo_chat.CLARIFY_CHOICES]
    assert not set(scripted) - declared
    assert set(demo_chat.FEE_CHOICES) <= set(demo_chat.load_terms()["fee"].candidates)
    assert demo_chat.CHANGE_ARGS["metric"] in declared
    assert set(demo_chat.CLARIFY_CHOICES) <= set(demo_chat.load_terms()["rate"].candidates)


def test_values_and_csat_numbers_read_the_tool_text():
    assert demo_chat.values(BALANCE_OUT)["net_member_liquidity"] == "200.00"
    assert demo_chat.values(LCV_OUT) == {"loan_to_value": "70.0000",
                                         "member_lifetime_value": "1234.0000"}
    assert demo_chat._csat_numbers(CSAT_OUT) == (10, "3.50", {1: 2, 2: 1, 3: 3, 4: 2, 5: 2})


def test_page_is_filled_from_the_capture_and_marks_what_is_not_captured():
    page = demo_chat.render(_capture())
    for expected in ("300.00 - 100.00", "= 200.00", "1234.0000", "SELECT 2 /* rates */",
                     "SELECT 3 /* graph */", "`abc1234`", "Test Member"):
        assert expected in page
    assert "SELECT 4 /* fees */" in page
    assert page.count("**ILLUSTRATIVE, not captured output.**") == 2
    assert "is the **ledger** balance" in page
    assert "$10.00 apart" in page


def test_the_change_turn_shows_the_tool_text_and_folds_both_windows_sql():
    page = demo_chat.render(_capture())
    assert "## Turn 8: why did the number move?" in page
    assert "Members did not know about the change" in page
    assert "-- window [2026-03-04, 2026-04-15)\nSELECT 'before window'" in page
    assert "-- window [2026-04-15, 2026-05-27)\nSELECT 'after window'" in page
    assert page.count("SELECT 'after window'") == 1  # folded under the answer, not also in it


def test_ledger_claim_follows_the_transcript():
    capture = _capture()
    capture["balance_call"] = Hit("CALL-2", "your balance of $999.00", 1.0, {"call_id": "CALL-2"})
    assert "does not match this account's ledger balance" in demo_chat.render(capture)
