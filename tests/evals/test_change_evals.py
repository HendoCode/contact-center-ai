"""The change-analysis eval sets (`make evals` scores them offline): the datasets derive from
the ground truth and keep golden and held-out events apart, the recomputation agrees with the
generator's own answer key, and every change evaluator fails a wrong answer."""

import copy
import json
from datetime import date

import pytest
from langchain_core.runnables import RunnableLambda

pytest.importorskip("fastmcp", reason="install the agent group: uv sync --extra dev --group agent")

from evals import evaluators as ev  # noqa: E402
from evals import run as eval_run  # noqa: E402
from evals.change_corpus import default_corpus  # noqa: E402
from evals.datasets import build_change  # noqa: E402
from evals.offline import AnswerKeyModel  # noqa: E402
from evals.run import (  # noqa: E402
    CHANGE_GROUPS, KNOWN_FAILURES_PATH, failures, gate, load_dataset, run_change_offline, split,
    summarize,
)


@pytest.fixture(scope="module")
def corpus():
    eval_run.ensure_corpus()
    return default_corpus()


@pytest.fixture(scope="module")
def offline_rows(corpus) -> dict[str, dict]:
    rows = [*run_change_offline("change"), *run_change_offline("change-holdout")]
    return {r["id"]: r for r in rows}


def scored(row_id: str, outputs: dict, key: str):
    item = next(i for name in CHANGE_GROUPS for i in load_dataset(name) if i["id"] == row_id)
    inputs, reference = split(item)
    return getattr(ev, key)(inputs, outputs, reference)


# ── datasets ──────────────────────────────────────────────────────────────────

def test_the_committed_change_sets_are_a_fresh_derivation(corpus):
    assert build_change.main(["--check"]) == 0


def test_golden_rows_are_golden_events_and_held_out_rows_held_out_ones(corpus):
    for name, split_name in (("change", "golden"), ("change-holdout", "heldout")):
        items = load_dataset(name)
        assert items and {corpus.event(i["event"])["split"] for i in items} == {split_name}
    assert [i["id"] for i in load_dataset("change")] == ["G1", "G2", "G3", "G4", "G5", "G6"]
    assert [i["id"] for i in load_dataset("change-holdout")] == ["H1", "H2", "H3", "H4"]


def test_a_golden_spec_on_a_held_out_event_is_refused(corpus, monkeypatch):
    spec = {**build_change.CHANGE_SPECS[0], "event": "Westside branch closure"}
    monkeypatch.setitem(build_change.SETS, "golden", ([spec], build_change.CHANGE_GOLDEN_PATH))
    with pytest.raises(SystemExit, match="golden spec on a heldout event"):
        build_change.build("golden")


def test_rows_follow_the_contract_shape_and_a_follow_up_takes_its_contract_from_its_base():
    shape = {"id", "kind", "event", "question", "follow_up_of", "route", "metric_names",
             "filters", "change_point", "change_point_source", "expect_interrupt", "evaluators",
             "reference", "derivation"}
    for _, path in build_change.SETS.values():
        for line in path.read_text().splitlines():
            assert set(json.loads(line)) == shape
    golden = {i["id"]: i for i in load_dataset("change")}
    assert golden["G3"]["prior_question"] == golden["G1"]["question"]
    for key in ("metric_names", "filters", "change_point", "change_point_source"):
        assert golden["G3"][key] == golden["G1"][key]
    assert all(i["group"] == "change_golden" for i in golden.values())


def test_the_recomputation_matches_the_generators_answer_key_on_every_default_window(corpus):
    for event in corpus.truth["events"]:
        dw = event["default_window"]
        for side in ("before", "after"):
            span = (date.fromisoformat(dw[side]["start"]), date.fromisoformat(dw[side]["end"]))
            calls, n = corpus.window("call_volume", dw["filters"], *span)
            score, responses = corpus.window("csat_average_score", dw["filters"], *span)
            assert calls == n == dw["metrics"]["call_volume"][side]
            assert responses == dw["metrics"]["responses"][side]
            assert round(score, 4) == dw["metrics"]["csat_average_score"][side]
            ids = set(corpus.call_ids(dw["filters"], *span))
            for driver in dw["drivers"]:
                assert set(driver[side]) <= ids


# ── the offline run ───────────────────────────────────────────────────────────

def test_the_offline_change_runs_match_the_known_failures(offline_rows):
    known = json.loads(KNOWN_FAILURES_PATH.read_text())["failures"]
    found = {k: v for k, v in failures(list(offline_rows.values())).items()
             if k in offline_rows}
    assert gate(found, {k: v for k, v in known.items() if k in offline_rows}) == []


def test_every_listed_evaluator_scores_its_row_and_the_others_do_not(offline_rows):
    for name in CHANGE_GROUPS:
        for item in load_dataset(name):
            results = offline_rows[item["id"]]["results"]
            applied = {k for k, r in results.items() if r["score"] is not None}
            assert applied == set(item["evaluators"]), item["id"]


def test_golden_and_held_out_change_rows_report_in_separate_groups(offline_rows):
    table = summarize(list(offline_rows.values()), ev.CHANGE_KEYS)
    assert table["change_golden"]["n"] == 6 and table["change_heldout"]["n"] == 4


def test_a_driver_model_that_finds_nothing_fails_driver_recall_everywhere(corpus, monkeypatch):
    def nothing(self, schema):
        empty = {"reasons": []} if "reasons" in schema.model_fields else {"assignments": []}
        return RunnableLambda(lambda prompt: schema(**empty))

    monkeypatch.setattr(AnswerKeyModel, "with_structured_output", nothing)
    rows = run_change_offline("change")
    recall = {r["id"]: r["results"]["driver_recall"]["score"] for r in rows}
    assert recall == {"G1": False, "G2": False, "G3": None, "G4": False, "G5": False, "G6": False}


# ── each evaluator bites ──────────────────────────────────────────────────────

@pytest.fixture
def g1(offline_rows) -> dict:
    return copy.deepcopy(offline_rows["G1"]["outputs"])


def test_merging_every_driver_into_one_fails_recall_on_precision(g1):
    drivers = g1["change"]["drivers"]
    everything = [c for d in drivers for c in d["after"]["call_ids"]]
    drivers[:] = [{**drivers[0], "after": {"n": len(everything), "call_ids": everything}}]
    result = scored("G1", g1, "driver_recall")
    assert result["score"] is False and "0 of 4 planted drivers found" in result["comment"]


def test_a_driver_holding_under_half_its_calls_is_missed(g1):
    top = g1["change"]["drivers"][0]
    kept = top["after"]["call_ids"][: len(top["after"]["call_ids"]) // 2 - 1]
    top["after"] = {"n": len(kept), "call_ids": kept}
    assert scored("G1", g1, "driver_recall")["score"] is False


def test_an_inflated_driver_count_fails_driver_share(g1):
    g1["change"]["drivers"][0]["after"]["n"] += 10
    assert scored("G1", g1, "driver_share")["score"] is False


def test_a_value_off_at_two_decimals_or_a_wrong_n_fails_before_after(g1):
    good = copy.deepcopy(g1)
    g1["change"]["windows"]["after"]["value"] += 0.01
    assert scored("G1", g1, "before_after")["score"] is False
    good["change"]["windows"]["before"]["n"] += 1
    assert scored("G1", good, "before_after")["score"] is False


def test_the_wrong_filters_fail_before_after_even_with_matching_numbers(offline_rows):
    out = copy.deepcopy(offline_rows["G4"]["outputs"])
    out["change"]["filters"] = {"category": ["card_services"]}
    assert scored("G4", out, "before_after")["score"] is False


def test_a_logged_change_point_must_be_exact(g1):
    g1["change"]["change_point"]["date"] = "2026-04-16"
    assert scored("G1", g1, "change_point")["score"] is False


@pytest.mark.parametrize("days, ok", [(4, True), (-4, True), (5, False)])
def test_a_detected_change_point_may_be_four_days_off(offline_rows, days, ok):
    out = copy.deepcopy(offline_rows["H3"]["outputs"])
    out["change"]["change_point"]["date"] = date.fromordinal(
        date(2026, 8, 7).toordinal() + days).isoformat()
    assert scored("H3", out, "change_point")["score"] is ok


def test_a_wrong_weekly_value_fails_weekly(offline_rows):
    out = copy.deepcopy(offline_rows["G6"]["outputs"])
    assert scored("G6", out, "weekly")["score"] is True
    out["change"]["windows"]["after"]["weekly"][1]["value"] += 0.5
    assert scored("G6", out, "weekly")["score"] is False


def test_a_coaching_answer_without_the_planted_pattern_fails_pattern_recall(offline_rows):
    out = copy.deepcopy(offline_rows["G3"]["outputs"])
    assert out["change"]["patterns"] and scored("G3", out, "pattern_recall")["score"] is True
    out["change"]["patterns"] = []
    assert scored("G3", out, "pattern_recall")["score"] is False


def test_an_agent_id_anywhere_in_a_change_answer_fails(g1):
    assert scored("G1", g1, "no_agent_ids")["score"] is True
    g1["answer"] += "\nHandled mostly by AGT-012."
    assert scored("G1", g1, "no_agent_ids")["score"] is False


def test_the_live_preflight_estimates_the_change_sets_with_their_own_assumptions(monkeypatch, capsys):
    from evals import preflight

    monkeypatch.setattr(preflight, "fetch_prices", lambda: {"m": (1e-6, 1e-6), "j": (1e-6, 1e-6)})
    monkeypatch.setattr(preflight, "priced_models", lambda: {"agent": "m", "judge": "j"})
    assert preflight.main(["--estimate-only", "--dataset", "change"]) == 0
    out = capsys.readouterr().out
    assert "for 7 question(s)" in out  # six questions, and G1 again before its follow-up G3
    assert preflight.assumption_text(preflight.CHANGE_ASSUMPTIONS) in out
    assert preflight.main(["--estimate-only", "--dataset", "change-holdout"]) == 0
    assert "for 4 question(s)" in capsys.readouterr().out
