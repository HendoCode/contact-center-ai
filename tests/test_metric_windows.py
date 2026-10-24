"""
query_metric time windows and where filters, query_csat date windows, per-fee-type metrics.

The argument-building tests need nothing. The ones marked with `_require_prereqs` run the
real `mf` CLI against local Postgres and skip cleanly without it.
"""

import re
from pathlib import Path

import pytest

from ccai_mcp import metrics
from ccai_mcp.metrics import _where_clause, _window_args, query_metric_raw
from ccai_mcp.tools import query_csat
from tests.test_metrics import _require_prereqs


def _responses(text: str) -> int:
    match = re.search(r"\((\d+) responses\)", text)
    return int(match.group(1)) if match else 0


def _value(result: dict, column: str) -> float:
    return float(result["rows"][0][result["columns"].index(column)])


# ── argument building ────────────────────────────────────────────────────────

def test_window_end_is_exclusive_so_mf_gets_the_day_before():
    assert _window_args({"start": "2026-04-15", "end": "2026-05-27"}) == [
        "--start-time", "2026-04-15", "--end-time", "2026-05-26",
    ]
    assert _window_args(None) == []
    assert _window_args({"start": "2026-04-15"}) == ["--start-time", "2026-04-15"]


@pytest.mark.parametrize("window", [
    {"start": "April 15", "end": "2026-05-27"},
    {"start": "2026-05-27", "end": "2026-05-27"},
    {"start": "2026-05-27", "end": "2026-04-15"},
    {"begin": "2026-04-15"},
])
def test_bad_windows_are_rejected(window):
    with pytest.raises(ValueError):
        _window_args(window)


def test_where_renders_eq_and_in_and_quotes_values():
    clause = _where_clause([
        {"dimension": "interaction__category_code", "op": "in", "value": ["fee_dispute", "card_services"]},
        {"dimension": "member__home_branch", "op": "eq", "value": "O'Hare"},
    ])
    assert clause == (
        "{{ Dimension('interaction__category_code') }} IN ('fee_dispute', 'card_services')"
        " AND {{ Dimension('member__home_branch') }} = 'O''Hare'"
    )
    assert _where_clause(None) is None
    assert _where_clause([]) is None


@pytest.mark.parametrize("where", [
    [{"dimension": "product__lob", "op": "eq", "value": "banking"}],
    [{"dimension": "member__home_branch", "op": "like", "value": "W%"}],
    [{"dimension": "member__home_branch", "op": "eq"}],
    [{"dimension": "member__home_branch", "op": "in", "value": []}],
    [{"dimension": "member__home_branch", "op": "eq", "value": {"x": 1}}],
])
def test_bad_where_entries_are_rejected(where):
    with pytest.raises(ValueError):
        _where_clause(where)


@pytest.mark.parametrize("value", [
    "{{ 7*7 }}",
    "{% for i in range(3) %}z{% endfor %}",
    "{# note #}",
    "a{{b",
    "line\nbreak",
    "tab\there",
    "nul\x00byte",
    "x" * 101,
    float("nan"),
    float("inf"),
])
def test_where_values_with_template_syntax_or_control_characters_are_refused(value):
    with pytest.raises(ValueError):
        _where_clause([{"dimension": "member__home_branch", "op": "eq", "value": value}])
    with pytest.raises(ValueError):
        _where_clause([{"dimension": "member__home_branch", "op": "in", "value": ["ok", value]}])


def test_ordinary_where_values_with_quotes_and_backslashes_still_work():
    clause = _where_clause([
        {"dimension": "member__home_branch", "op": "eq", "value": "it's a\\b"},
        {"dimension": "interaction__category_code", "op": "in", "value": ["fee_dispute", "x" * 100]},
    ])
    assert "= 'it''s a\\b'" in clause
    assert "'" + "x" * 100 + "'" in clause


def test_a_call_without_window_or_where_passes_the_same_arguments_as_before(monkeypatch):
    seen = []
    monkeypatch.setattr(metrics, "manifest_problem", lambda: None)

    def fake_mf(args):
        seen.append(args)
        if "--csv" in args:
            Path(args[args.index("--csv") + 1]).write_text("call_volume\n1\n")
        return ""

    monkeypatch.setattr(metrics, "run_metricflow", fake_mf)
    query_metric_raw(["call_volume"])
    assert seen[0][:3] == ["query", "--metrics", "call_volume"]
    assert not {"--start-time", "--end-time", "--where"} & set(seen[0])

    seen.clear()
    query_metric_raw(
        ["call_volume"],
        time_window={"start": "2026-04-15", "end": "2026-05-27"},
        where=[{"dimension": "interaction__category_code", "op": "eq", "value": "fee_dispute"}],
    )
    assert all("--where" in args and "--end-time" in args for args in seen)


def test_query_csat_rejects_a_bad_or_inverted_date():
    assert query_csat(start_date="April 15").startswith("TOOL ERROR (query_csat)")
    inverted = query_csat(start_date="2026-05-27", end_date="2026-04-15")
    assert inverted.startswith("TOOL ERROR (query_csat)")


def test_an_empty_time_window_part_is_an_error_not_an_open_window():
    with pytest.raises(ValueError):
        _window_args({"start": "", "end": "2026-05-27"})


# ── against the semantic layer ───────────────────────────────────────────────

WINDOWS = [("2026-03-03", "2026-04-15"), ("2026-04-15", "2026-05-27")]


def test_one_metric_over_two_windows_returns_two_numbers_with_sql():
    _require_prereqs()
    where = [{"dimension": "interaction__category_code", "op": "eq", "value": "fee_dispute"}]
    values = []
    for start, end in WINDOWS:
        result = query_metric_raw(
            ["call_volume"], time_window={"start": start, "end": end}, where=where
        )
        assert f"BETWEEN '{start}' AND" in result["sql"]
        assert "fee_dispute" in result["sql"]
        values.append(_value(result, "call_volume"))
    whole = _value(query_metric_raw(["call_volume"], where=where), "call_volume")
    assert 0 < sum(values) <= whole


@pytest.mark.parametrize("start,end", WINDOWS)
def test_query_csat_matches_the_responses_metric_over_the_same_window(start, end):
    _require_prereqs()
    category = "fee_dispute"
    result = query_metric_raw(
        ["responses"],
        time_window={"start": start, "end": end},
        where=[{"dimension": "interaction__category_code", "op": "eq", "value": category}],
    )
    expected = int(_value(result, "responses")) if result["rows"] else 0
    assert _responses(query_csat(category=category, start_date=start, end_date=end)) == expected


def test_csat_average_score_filters_by_category_and_home_branch():
    _require_prereqs()
    by_category = query_metric_raw(
        ["csat_average_score"], group_by=["interaction__category_code"]
    )
    assert len(by_category["rows"]) > 1
    by_branch = query_metric_raw(["csat_average_score"], group_by=["member__home_branch"])
    assert by_branch["rows"]


def test_fee_types_split_total_fee_revenue():
    _require_prereqs()
    names = [
        "overdraft_fee_revenue", "nsf_fee_revenue",
        "monthly_maintenance_fee_revenue", "atm_fee_revenue",
    ]
    assert set(names) <= set(metrics.load_metric_catalog())
    result = query_metric_raw(names + ["total_fee_revenue"])
    # a fee type with no postings comes back empty
    row = dict(zip(result["columns"], (float(v or 0) for v in result["rows"][0])))
    assert row["overdraft_fee_revenue"] > 0
    assert sum(row[n] for n in names) <= row["total_fee_revenue"] + 0.01


def test_every_fee_type_bucket_adds_up_to_total_fee_revenue():
    _require_prereqs()
    total = _value(query_metric_raw(["total_fee_revenue"]), "total_fee_revenue")
    by_type = query_metric_raw(["total_fee_revenue"], group_by=["transaction__fee_type"])
    col = by_type["columns"].index("total_fee_revenue")
    typed = [float(r[col]) for r in by_type["rows"] if r[by_type["columns"].index("transaction__fee_type")]]
    assert typed
    assert sum(typed) == pytest.approx(total, abs=0.01)
