"""ccai_mcp/change.py: the explain_change tool core, on an in-memory semantic layer,
retriever and model (tests/change_support.py). Offline: no database, no key."""

import json
from datetime import date, timedelta

import pytest

import ccai_mcp.change as change
from change_support import (
    CHANGE,
    END,
    HOT,
    NOTICE,
    REFUND,
    START,
    FakeModel,
    FakeRetriever,
    FakeSemanticLayer,
    build_world,
    hash_order,
    in_window,
)

LOG = [{"name": "Logged alpha change", "date": CHANGE.isoformat()}]
FILTERS = {"category": [HOT]}


class Env:
    """The fakes behind one test; `explain` runs the tool against them."""

    def __init__(self, monkeypatch, *, event=True, extra_after=0, log=None, model=None):
        self.calls = build_world(event=event, extra_after=extra_after)
        self.layer = FakeSemanticLayer(self.calls)
        self.retriever = FakeRetriever(self.calls)
        self.model = model or FakeModel()
        monkeypatch.setattr(change, "query_metric_raw", self.layer)
        monkeypatch.setattr(change, "get_retriever", lambda: self.retriever)
        monkeypatch.setattr(change, "get_llm", lambda: self.model)
        monkeypatch.setattr(change, "load_change_log", lambda: list(LOG if log is None else log))

    def explain(self, **kwargs) -> dict:
        return change.explain_change(**{"metric": "csat_average_score", **kwargs})


@pytest.fixture
def env(monkeypatch):
    return Env(monkeypatch)


def mean_score(calls):
    scores = [c.score for c in calls if c.score is not None]
    return sum(scores) / len(scores)


# ── windows, numbers and SQL ─────────────────────────────────────────────────

def test_the_windows_are_half_open_around_the_change_point_and_hold_governed_numbers(env):
    result = env.explain(filters=FILTERS, change_point="2026-04-06")

    before = in_window(env.calls, CHANGE - timedelta(days=42), CHANGE, category=[HOT])
    after = in_window(env.calls, CHANGE, CHANGE + timedelta(days=42), category=[HOT])
    windows = result["windows"]
    assert (windows["before"]["start"], windows["before"]["end"]) == ("2026-02-23", "2026-04-06")
    assert (windows["after"]["start"], windows["after"]["end"]) == ("2026-04-06", "2026-05-18")
    assert windows["before"]["value"] == pytest.approx(mean_score(before))
    assert windows["after"]["value"] == pytest.approx(mean_score(after))
    assert windows["after"]["value"] < windows["before"]["value"] - 0.5
    assert windows["before"]["n"] == sum(c.score is not None for c in before)
    assert windows["after"]["n"] == sum(c.score is not None for c in after)
    assert "2026-02-23" in windows["before"]["sql"] and "2026-05-18" in windows["after"]["sql"]
    assert result["change_point"] == {"date": "2026-04-06", "source": "argument",
                                      "logged_change": "Logged alpha change", "score": None,
                                      "reason": None}
    assert result["filters"] == FILTERS and result["mode"] == "drivers" and result["version"] == 1


def test_every_number_comes_from_a_metric_query_and_the_windows_are_queried_with_the_filter(env):
    env.explain(filters={"category": [HOT], "home_branch": ["North"]}, change_point="2026-04-06")

    window_queries = [q for q in env.layer.queries if q["time_window"]]
    assert [q["time_window"] for q in window_queries] == [
        {"start": "2026-02-23", "end": "2026-04-06"}, {"start": "2026-04-06", "end": "2026-05-18"},
    ]
    for q in window_queries:
        assert q["metrics"] == ["csat_average_score", "responses"]
        assert q["where"] == [
            {"dimension": "interaction__category_code", "op": "in", "value": [HOT]},
            {"dimension": "member__home_branch", "op": "in", "value": ["North"]},
        ]


def test_call_volume_is_a_count_compared_per_week(env):
    result = env.explain(metric="call_volume", filters=FILTERS, change_point="2026-04-06")

    before = in_window(env.calls, CHANGE - timedelta(days=42), CHANGE, category=[HOT])
    after = in_window(env.calls, CHANGE, CHANGE + timedelta(days=42), category=[HOT])
    assert result["windows"]["before"]["value"] == result["windows"]["before"]["n"] == len(before)
    assert result["windows"]["after"]["value"] == result["windows"]["after"]["n"] == len(after)
    assert result["population"] == {"before": len(before), "after": len(after)}
    text = change.render_change(result)
    assert "5.0 a week before, 20.0 a week after: +15.0 calls a week (+300%)" in text


def test_ratios_and_handle_time_work_with_an_explicit_change_point(env):
    fcr = env.explain(metric="first_contact_resolution_rate", filters=FILTERS,
                      change_point="2026-04-06")
    aht = env.explain(metric="average_handle_time", filters=FILTERS, change_point="2026-04-06")

    assert fcr["windows"]["after"]["value"] < fcr["windows"]["before"]["value"]
    assert fcr["windows"]["after"]["n"] == fcr["population"]["after"]  # calls, not responses
    assert aht["windows"]["after"]["value"] > aht["windows"]["before"]["value"] + 100
    text = change.render_change(aht)
    assert " s (n=" in text and "seconds" in text
    assert "percentage points" in change.render_change(fcr)


def test_a_window_is_shortened_where_the_data_runs_out_and_says_so(env):
    result = env.explain(filters=FILTERS, change_point="2026-06-22")

    # The last call is on Friday July 3, so 12 days are left after the change point.
    assert result["windows"]["before"]["start"] == "2026-06-10"
    assert result["windows"]["after"]["end"] == "2026-07-04"
    assert any("12 days, not the 42 asked for" in item for item in result["limits"])


def test_a_change_point_with_too_little_data_beside_it_is_an_error(env):
    with pytest.raises(ValueError, match="at least 7 on each side"):
        env.explain(filters=FILTERS, change_point="2026-07-03")
    with pytest.raises(ValueError, match="at least 7"):
        env.explain(filters=FILTERS, change_point="2026-01-08")


# ── the change point ─────────────────────────────────────────────────────────

def test_the_one_logged_change_inside_the_period_is_the_change_point(env):
    result = env.explain(metric="call_volume", filters=FILTERS,
                         search_range={"start": "2026-04-01", "end": "2026-04-20"})

    assert result["change_point"]["source"] == "argument"
    assert result["change_point"]["date"] == "2026-04-06"
    assert result["change_point"]["logged_change"] == "Logged alpha change"
    assert result["series"] is None  # nothing was searched for
    assert ('from the question: the one logged change between 2026-04-01 and 2026-04-20, '
            '"Logged alpha change"') in change.render_change(result)


def test_two_logged_changes_in_the_period_mean_detection_inside_it(monkeypatch):
    log = [{"name": "One", "date": "2026-04-06"}, {"name": "Two", "date": "2026-04-13"}]
    env = Env(monkeypatch, log=log)
    result = env.explain(metric="call_volume", filters=FILTERS,
                         search_range={"start": "2026-03-30", "end": "2026-04-20"})

    assert result["change_point"]["source"] == "detected"
    assert result["change_point"]["date"] in ("2026-04-06", "2026-04-13")
    assert result["series"]["sql"]


@pytest.mark.parametrize("metric", ["call_volume", "csat_average_score"])
def test_detection_finds_the_largest_weekly_shift(monkeypatch, metric):
    env = Env(monkeypatch, log=[])
    result = env.explain(metric=metric, filters=FILTERS)

    cp = result["change_point"]
    assert cp["source"] == "detected" and cp["logged_change"] is None
    assert cp["score"] >= 2
    assert abs(date.fromisoformat(cp["date"]) - CHANGE).days <= 7
    assert "found by detection" in change.render_change(result)
    assert result["series"]["weeks"][0]["week"] == "2026-01-05"
    assert len(env.layer.queries) >= 2  # the weekly series, plus the window queries


def test_without_a_change_log_a_detected_change_says_it_could_not_be_named(monkeypatch):
    env = Env(monkeypatch, log=[])
    detected = env.explain(metric="call_volume", filters=FILTERS)
    given = env.explain(metric="call_volume", filters=FILTERS, change_point="2026-04-06")

    note = "No change log was available to this service"
    assert any(note in item for item in detected["limits"])
    assert not any(note in item for item in given["limits"])  # a date needs no log


def test_a_detected_change_snaps_to_a_logged_change_within_seven_days(monkeypatch):
    env = Env(monkeypatch, log=[{"name": "Near", "date": "2026-04-10"},
                                {"name": "Far", "date": "2026-02-02"}])
    result = env.explain(metric="call_volume", filters=FILTERS)

    assert result["change_point"]["date"] == "2026-04-10"
    assert result["change_point"]["logged_change"] == "Near"
    assert result["change_point"]["source"] == "detected"
    assert result["windows"]["after"]["start"] == "2026-04-10"


def test_a_search_range_keeps_detection_inside_it(monkeypatch):
    env = Env(monkeypatch, log=[])
    result = env.explain(metric="call_volume", filters=FILTERS,
                         search_range={"start": "2026-02-02", "end": "2026-03-16"})

    cp = result["change_point"]
    assert cp["source"] == "none" or date.fromisoformat(cp["date"]) < date(2026, 3, 16)


def test_no_clear_change_point_gives_no_windows_no_drivers_and_asks_for_a_date(monkeypatch):
    env = Env(monkeypatch, event=False, log=[])
    result = env.explain(metric="call_volume")

    assert result["change_point"]["source"] == "none"
    assert result["change_point"]["date"] is None and result["change_point"]["score"] < 2
    assert result["windows"] is None and result["drivers"] == []
    assert result["series"]["sql"] and result["series"]["weeks"]
    assert env.model.prompts == [] and env.retriever.searches == []  # no calls were read
    text = change.render_change(result)
    assert "no clear change point" in text and "Give a date" in text
    assert "-- weekly values searched" in text.split("Generated SQL:")[1]


def test_average_handle_time_will_not_search_for_a_change_point(env):
    with pytest.raises(ValueError, match="needs an explicit change point"):
        env.explain(metric="average_handle_time", filters=FILTERS)


def test_without_filters_the_call_types_whose_volume_rose_are_used(env):
    result = env.explain(metric="call_volume", change_point="2026-04-06")

    assert result["filters"] == {"category": [HOT]}
    assert any("whose volume rose across the change: alpha" in item for item in result["limits"])
    category_queries = [q for q in env.layer.queries if q["group_by"] == ["interaction__category_code"]]
    assert len(category_queries) == 2  # one per window


def test_without_filters_and_without_a_riser_all_calls_are_used(monkeypatch):
    env = Env(monkeypatch, event=False)
    result = env.explain(metric="call_volume", change_point="2026-04-06")

    assert result["filters"] == {}
    assert any("none stood out, so the analysis covers all calls" in i for i in result["limits"])


def test_a_branch_filter_is_not_replaced_by_a_category_guess(env):
    result = env.explain(metric="call_volume", filters={"home_branch": ["North"]},
                         change_point="2026-04-06")

    assert result["filters"] == {"home_branch": ["North"]}
    assert not [q for q in env.layer.queries if q["group_by"] == ["interaction__category_code"]]


# ── drivers ──────────────────────────────────────────────────────────────────

def test_drivers_are_counted_by_code_from_the_models_assignments(env):
    result = env.explain(filters=FILTERS, change_point="2026-04-06")

    after = in_window(env.calls, CHANGE, CHANGE + timedelta(days=42), category=[HOT])
    by_kind = {k: [c for c in after if c.kind == k] for k in ("notice", "refund")}
    drivers = {d["label"]: d for d in result["drivers"]}
    notice = drivers["Members were not told in advance"]
    refund = drivers["Refund requests were turned down"]
    assert notice["after"]["n"] == len(by_kind["notice"])
    assert notice["after"]["call_ids"] == [c.call_id for c in hash_order_calls(by_kind["notice"])]
    assert refund["after"]["n"] == len(by_kind["refund"])
    assert notice["before"]["n"] == refund["before"]["n"] == 0
    assert [d["after"]["n"] for d in result["drivers"]] == sorted(
        (d["after"]["n"] for d in result["drivers"]), reverse=True)
    assert result["unassigned"]["after"] == len(after) - notice["after"]["n"] - refund["after"]["n"]
    assert result["population"]["after"] == len(after) == result["coverage"]["read_after"]


def hash_order_calls(calls):
    by_id = {c.call_id: c for c in calls}
    return [by_id[i] for i in hash_order(by_id)]


def test_quotes_are_verbatim_and_at_most_three_per_driver(env):
    result = env.explain(filters=FILTERS, change_point="2026-04-06")

    by_id = {c.call_id: c.text for c in env.calls}
    for driver in result["drivers"]:
        assert 1 <= len(driver["citations"]) <= 3
        for cite in driver["citations"]:
            assert cite["quote"] in by_id[cite["call_id"]]
            assert cite["call_id"] in driver["after"]["call_ids"]
    quotes = {c["quote"] for d in result["drivers"] for c in d["citations"]}
    assert quotes == {f"MEMBER: {NOTICE}", "MEMBER: I would like a refund."}


def test_quotes_the_call_does_not_contain_are_dropped_but_the_call_is_still_counted(monkeypatch):
    for mode, why in (("invented", "not in the call"), ("agent_id", "an agent id")):
        env = Env(monkeypatch, model=FakeModel(quotes=mode))
        result = env.explain(filters=FILTERS, change_point="2026-04-06")

        assert result["drivers"] and all(d["citations"] == [] for d in result["drivers"]), why
        assert sum(d["after"]["n"] for d in result["drivers"]) > 0
        assert "AGT-" not in json.dumps(result)


def test_an_unknown_driver_number_or_a_forgotten_call_is_unassigned(monkeypatch):
    for mode in ("bad_number", "forget_half"):
        env = Env(monkeypatch, model=FakeModel(quotes=mode))
        result = env.explain(filters=FILTERS, change_point="2026-04-06")

        total = result["coverage"]["read_after"]
        assigned = sum(d["after"]["n"] for d in result["drivers"])
        assert result["unassigned"]["after"] == total - assigned
        assert assigned < total


def test_when_the_model_fails_the_numbers_stand_and_the_limit_says_why(monkeypatch):
    env = Env(monkeypatch, model=FakeModel(fail=True))
    result = env.explain(filters=FILTERS, change_point="2026-04-06")

    assert result["windows"]["after"]["n"] > 0 and result["drivers"] == []
    assert any("could not be grouped into drivers" in item and "model unavailable" in item
               for item in result["limits"])


def test_the_model_reads_one_proposal_and_batches_of_twenty(env):
    result = env.explain(filters=FILTERS, change_point="2026-04-06")

    read_after, read_before = result["coverage"]["read_after"], result["coverage"]["read_before"]
    proposals = [p for p in env.model.prompts if "Find up to" in p]
    batches = [p for p in env.model.prompts if "For each call below" in p]
    assert len(proposals) == 1
    assert len(batches) == -(-read_after // 20) + -(-read_before // 20)
    assert all(p.count("\n[CALL-") <= 20 for p in batches)
    assert proposals[0].count("\n[CALL-") == 30  # the proposal reads 30 calls, not all of them


def test_a_filter_that_matches_no_calls_reports_zero_and_skips_the_model(env):
    result = env.explain(metric="call_volume", filters={"category": ["nosuch"]},
                         change_point="2026-04-06")

    assert result["windows"]["after"]["n"] == 0 and result["population"]["after"] == 0
    assert result["drivers"] == []
    assert any("No calls were found in the after window" in item for item in result["limits"])
    assert env.model.prompts == []
    assert "0.0 a week before, 0.0 a week after" in change.render_change(result)


def test_calls_are_read_from_the_windows_with_the_same_filters(env):
    env.explain(filters={"category": [HOT], "home_branch": ["North"]}, change_point="2026-04-06",
                question="Why did it drop?")

    wheres = [s["where"] for s in env.retriever.searches]
    assert wheres == [
        {"date": {"gte": "2026-02-23", "lt": "2026-04-06"}, "category": {"in": [HOT]},
         "home_branch": {"in": ["North"]}},
        {"date": {"gte": "2026-04-06", "lt": "2026-05-18"}, "category": {"in": [HOT]},
         "home_branch": {"in": ["North"]}},
    ]
    assert all(s["query"] == "Why did it drop?" for s in env.retriever.searches)


def test_a_store_that_withholds_calls_is_reported(env, monkeypatch):
    real = env.retriever.search
    monkeypatch.setattr(env.retriever, "search",
                        lambda query, k=5, where=None, mode="vector": real(query, k=3, where=where))
    result = env.explain(filters=FILTERS, change_point="2026-04-06")

    assert any("returned 3 of the" in item and "after window" in item for item in result["limits"])
    assert result["coverage"]["read_after"] == 3 < result["population"]["after"]


def test_a_window_over_the_cap_reads_the_lowest_hashes_and_says_it_sampled(monkeypatch):
    env = Env(monkeypatch, extra_after=250)
    first = env.explain(filters=FILTERS, change_point="2026-04-06")
    second = env.explain(filters=FILTERS, change_point="2026-04-06")

    after = in_window(env.calls, CHANGE, CHANGE + timedelta(days=42), category=[HOT])
    assert first["population"]["after"] == len(after) > 200
    assert first["coverage"]["read_after"] == 200
    read = {i for d in first["drivers"] for i in d["after"]["call_ids"]}
    lowest = set(hash_order(c.call_id for c in after)[:200])
    assert read <= lowest
    assert first == second  # the same window always gives the same sample
    assert any("sampled" in item and "200" in item for item in first["limits"])
    text = change.render_change(first)
    assert "calls sampled" in text and "(200 sampled)" in text


def test_the_survey_coverage_is_a_limit_for_satisfaction_but_not_for_volume(env):
    csat = env.explain(filters=FILTERS, change_point="2026-04-06")
    volume = env.explain(metric="call_volume", filters=FILTERS, change_point="2026-04-06")

    assert any("survey responses, which cover only some of the" in i for i in csat["limits"])
    assert not any("survey responses" in i for i in volume["limits"])


# ── the answer text ──────────────────────────────────────────────────────────

def test_the_answer_states_every_required_part_without_a_model(env):
    result = env.explain(filters=FILTERS, change_point="2026-04-06")
    text = change.render_change(result)

    after = result["windows"]["after"]
    assert "CSAT Average Score" in text and "category = alpha" in text
    assert "Before [2026-02-23, 2026-04-06)" in text and "After [2026-04-06, 2026-05-18)" in text
    assert f"{after['value']:.2f} (n={after['n']} responses)" in text
    assert 'the logged change "Logged alpha change"' in text
    assert f"{result['drivers'][0]['after']['n']} of {result['population']['after']} calls in the after window" in text
    assert "in the before window" in text
    assert f'{result["drivers"][0]["citations"][0]["call_id"]}: "' in text
    assert "What the data could not determine:" in text
    assert "cannot show that the change caused the shift" in text
    sql = text.split("Generated SQL:\n")[1]
    assert "-- before window" in sql and "-- after window" in sql
    assert text.count("Generated SQL:") == 1
    assert "AGT-" not in text and "agent score" not in text.lower()


def test_split_change_reads_back_what_the_tool_returns(env):
    result = env.explain(filters=FILTERS, change_point="2026-04-06")
    joined = "\n".join([change.render_change(result), f"{change.RESULT_MARKER}\n{json.dumps(result)}"])

    text, parsed = change.split_change(joined)
    assert parsed == json.loads(json.dumps(result))
    assert text == change.render_change(result)
    with pytest.raises(ValueError):
        change.split_change("TOOL ERROR (explain_change): nope")


def test_the_wire_format_is_two_text_blocks_or_one_tool_error(env):
    blocks = change.explain_change_blocks(metric="call_volume", filters=FILTERS,
                                          change_point="2026-04-06")
    assert len(blocks) == 2 and blocks[1].startswith("CHANGE RESULT (JSON, v1):\n")
    assert blocks[0].startswith("Change analysis:")

    error = change.explain_change_blocks(metric="nps")
    assert len(error) == 1 and error[0].startswith("TOOL ERROR (explain_change):")
    for name in change.SUPPORTED_METRICS:
        assert name in error[0]


@pytest.mark.parametrize("arguments, message", [
    ({"metric": "nps"}, "cannot compare 'nps'"),
    ({"metric": "call_volume", "window_days": 13}, "14 to 91"),
    ({"metric": "call_volume", "window_days": 92}, "14 to 91"),
    ({"metric": "call_volume", "mode": "other"}, "mode must be"),
    ({"metric": "call_volume", "change_point": "April 6"}, "ISO date"),
    ({"metric": "call_volume", "search_range": {"start": "2026-04-20", "end": "2026-04-01"}}, "after its start"),
    ({"metric": "call_volume", "filters": {"colour": ["red"]}}, "only"),
    ({"metric": "call_volume", "filters": {"category": [3]}}, "list of strings"),
])
def test_bad_inputs_are_refused_with_a_message(env, arguments, message):
    with pytest.raises(ValueError, match=message):
        change.explain_change(**arguments)
    assert env.layer.queries == []  # refused before any query


# ── coaching ─────────────────────────────────────────────────────────────────

def coaching_groups(env) -> list[dict]:
    after = in_window(env.calls, CHANGE, CHANGE + timedelta(days=42), category=[HOT])
    return [{"label": "Refund requests", "call_ids": [c.call_id for c in after if c.kind == "refund"]},
            {"label": "Too small", "call_ids": [after[0].call_id]}]


def test_coaching_finds_patterns_across_the_calls_of_a_group_and_scores_no_agent(env):
    groups = coaching_groups(env)
    result = env.explain(filters=FILTERS, change_point="2026-04-06", mode="coaching",
                         groups=groups, question="What should supervisors coach on?")

    (pattern,) = result["patterns"]
    refund_ids = groups[0]["call_ids"]
    assert pattern["label"] == "Rule described differently" and pattern["driver"] == "Refund requests"
    assert pattern["n"] == len(pattern["call_ids"]) == pattern["of"] == len(refund_ids)
    assert {c["quote"] for c in pattern["citations"]} == {f"AGENT: {REFUND}"}
    assert result["mode"] == "coaching" and result["windows"] is None and result["drivers"] == []
    assert env.layer.queries == []  # coaching reads calls; it asks the semantic layer nothing
    assert env.retriever.searches[0]["where"] == {"call_id": {"in": refund_ids}}
    text = change.render_change(result)
    assert text.startswith("Coaching patterns across calls")
    assert f'{pattern["n"]} of {pattern["of"]} calls in "Refund requests"' in text
    assert "AGT-" not in text and "no per-agent scores" in text


def test_coaching_needs_groups_with_call_ids(env):
    with pytest.raises(ValueError, match="needs `groups`"):
        env.explain(filters=FILTERS, change_point="2026-04-06", mode="coaching")
    with pytest.raises(ValueError, match="needs `groups`"):
        env.explain(mode="coaching", groups=[{"label": "x", "call_ids": []}])


def test_a_pattern_in_a_single_call_is_not_reported(env):
    one = [{"label": "One call", "call_ids": [coaching_groups(env)[0]["call_ids"][0]]}]
    result = env.explain(mode="coaching", groups=one)

    assert result["patterns"] == []
    assert any("No pattern" in item for item in result["limits"])


# ── hygiene ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("quote, text, expected", [
    ("Happy to help with that.", "AGENT:  Happy to   help with that.", "Happy to help with that."),
    ("\"Happy to help with that.\"", "AGENT: Happy to help with that.", "Happy to help with that."),
    ("too short", "too short here", None),
    ("Happy to help with something else entirely.", "AGENT: Happy to help with that.", None),
    ("Member MBR-000381 said it was fine here.", "Member MBR-000381 said it was fine here.", None),
    (None, "anything", None),
    (12, "anything", None),
])
def test_only_a_long_enough_verbatim_quote_without_ids_is_kept(quote, text, expected):
    assert change._verbatim(quote, text) == expected


def test_labels_lose_ids_and_are_capped():
    assert change._clean_label("Call from AGT-044 about  fees", 80) == "Call from about fees"
    assert len(change._clean_label("x" * 500, 80)) == 80


def test_the_data_window_is_the_world(env):
    assert START.weekday() == 0 and (END - START).days == 182
