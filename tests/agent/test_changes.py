"""agent/changes.py: periods, points and the change log. Pure functions, no graph."""

import json
from datetime import date

import pytest

from agent.changes import (
    contains_phrase,
    is_iso_date,
    load_branches,
    load_change_log,
    match_logged_change,
    parse_period,
    parse_point,
)

DATA = {"start": "2026-03-03", "end": "2026-09-02"}


@pytest.mark.parametrize("phrase, start, end", [
    ("July", "2026-06-28", "2026-08-04"),            # July 1-31, padded by 3 days each side
    ("in June", "2026-05-29", "2026-07-04"),
    ("during April", "2026-03-29", "2026-05-04"),
    ("since April", "2026-03-29", "2026-05-04"),
    ("after March", "2026-02-26", "2026-04-04"),
    ("early July", "2026-06-28", "2026-07-14"),     # days 1-10
    ("mid July", "2026-07-08", "2026-07-24"),       # days 11-20
    ("late July", "2026-07-18", "2026-08-04"),      # day 21 to the end
    ("in late July", "2026-07-18", "2026-08-04"),
    ("Late july", "2026-07-18", "2026-08-04"),
    ("mid-June", "2026-06-08", "2026-06-24"),
    ("Aug", "2026-07-29", "2026-09-04"),
    ("Sept", "2026-08-29", "2026-10-04"),
    ("June 15", "2026-06-12", "2026-06-19"),        # a point: one day, padded
    ("2026-06-15", "2026-06-12", "2026-06-19"),
    ("on June 15th", "2026-06-12", "2026-06-19"),
])
def test_periods_are_padded_by_three_days_and_end_exclusive(phrase, start, end):
    assert parse_period(phrase, DATA) == {"start": start, "end": end}


@pytest.mark.parametrize("phrase", [
    "last week", "recently", "around the holidays", "Q2", "2019", "June 31", "Smarch",
    "late", "", None, "December", "January",   # the last two are outside the data
])
def test_anything_else_is_refused(phrase):
    assert parse_period(phrase, DATA) is None


def test_the_year_is_the_one_where_the_month_falls_inside_the_data():
    across = {"start": "2025-11-10", "end": "2026-02-20"}
    assert parse_period("December", across)["start"] == "2025-11-28"
    assert parse_period("late January", across) == {"start": "2026-01-18", "end": "2026-02-04"}
    assert parse_point("Jan 5", across) == date(2026, 1, 5)
    assert parse_point("Nov 12", across) == date(2025, 11, 12)
    assert parse_period("June", across) is None


def test_points_are_exact_dates():
    assert parse_point("June 15", DATA) == date(2026, 6, 15)
    assert parse_point("2026-06-15", DATA) == date(2026, 6, 15)
    assert parse_point("since April 15", DATA) == date(2026, 4, 15)
    assert parse_point("2026-06-15", {"start": "0001-01-01", "end": "9999-12-31"}) == date(2026, 6, 15)
    for not_a_point in ("June", "late July", "2026-02-30", "Feb 30", "next Tuesday", None):
        assert parse_point(not_a_point, DATA) is None


def test_is_iso_date_only_for_a_full_date():
    assert is_iso_date("2026-06-15") and not is_iso_date("June 15") and not is_iso_date(None)


def test_phrases_match_whole_words_ignoring_case():
    assert contains_phrase("Why are Fee Calls up?", "fee calls")
    assert not contains_phrase("coinbase", "coin")
    assert not contains_phrase(None, "fee")


def test_the_change_log_and_branches_read_the_generated_files(tmp_path):
    (tmp_path / "change_log.json").write_text(json.dumps([
        {"name": "Later", "date": "2026-06-15"}, {"name": "Earlier", "date": "2026-04-15"},
        {"name": "", "date": "2026-05-01"}, {"name": "No date"}, "junk",
    ]))
    (tmp_path / "branches.json").write_text(json.dumps([{"name": "Westside"}, {"nope": 1}]))

    assert load_change_log(tmp_path) == [
        {"name": "Earlier", "date": "2026-04-15"}, {"name": "Later", "date": "2026-06-15"},
    ]
    assert load_branches(tmp_path) == ["Westside"]


def test_a_missing_or_broken_file_reads_as_empty(tmp_path):
    assert load_change_log(tmp_path) == [] and load_branches(tmp_path) == []
    (tmp_path / "change_log.json").write_text("{not json")
    (tmp_path / "branches.json").write_text('{"name": "Westside"}')
    assert load_change_log(tmp_path) == [] and load_branches(tmp_path) == []


def test_a_logged_change_matches_by_exact_name_only():
    log = [{"name": "Fee schedule change", "date": "2026-04-15"}]
    assert match_logged_change("  fee SCHEDULE change ", log) == log[0]
    assert match_logged_change("fee change", log) is None
    assert match_logged_change(None, log) is None
