"""Accuracy of the change route on the planted events, scored offline: the generator's answer
key against what the route reports for the fee-schedule question (G1), its coaching follow-up
(G3), the online-banking held-out question (H1) and the Westside satisfaction question (H3);
and that a question about nothing planted stops at "not enough evidence".

The driver model is the answer-key double (`AnswerKeyModel`), so these tests check the counting,
windowing, ranking, quoting and grounding around the model, not a model's reading of calls."""

import re

import pytest

pytest.importorskip("fastmcp", reason="install the agent group: uv sync --extra dev --group agent")

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from agent.graph import build_graph  # noqa: E402
from evals import run as eval_run  # noqa: E402
from evals.change_corpus import default_corpus  # noqa: E402
from evals.datasets import build_golden  # noqa: E402
from evals.offline import (  # noqa: E402
    NO_FRAME,
    OfflineToolbox,
    ScriptedClassifier,
    corpus_call_date,
    corpus_call_exists,
)


@pytest.fixture(scope="module")
def corpus():
    eval_run.ensure_corpus()
    return default_corpus()


@pytest.fixture(scope="module")
def rows(corpus) -> dict[str, dict]:
    found = [*eval_run.run_change_offline("change"), *eval_run.run_change_offline("change-holdout")]
    return {r["id"]: r for r in found}


def planted_window(corpus, day: str) -> dict:
    """The answer key's default window for the event dated `day`."""
    return next(e for e in corpus.truth["events"] if e["date"] == day)["default_window"]


@pytest.mark.parametrize("row_id", ["G1", "G3", "H1", "H3"])
def test_the_deterministic_evaluators_pass_on_the_named_questions(rows, row_id):
    scores = {k: r["score"] for k, r in rows[row_id]["results"].items() if r["score"] is not None}
    assert scores and all(scores.values()), (row_id, rows[row_id]["results"])


def test_the_fee_question_reports_the_planted_before_and_after_values_exactly(rows, corpus):
    window = planted_window(corpus, "2026-04-15")
    reported = rows["G1"]["outputs"]["change"]["windows"]
    want = window["metrics"]["csat_average_score"]
    assert round(reported["before"]["value"], 4) == want["before"]
    assert round(reported["after"]["value"], 4) == want["after"]
    assert (reported["before"]["n"], reported["after"]["n"]) == (
        window["metrics"]["responses"]["before"], window["metrics"]["responses"]["after"])
    answer = rows["G1"]["outputs"]["answer"]
    assert f"{want['before']:.2f}" in answer and f"{want['after']:.2f}" in answer


def test_the_fee_drivers_are_ranked_by_calls_with_n_of_m_counts_and_verbatim_quotes(rows, corpus):
    change = rows["G1"]["outputs"]["change"]
    window = planted_window(corpus, "2026-04-15")
    planted = sorted((len(d["after"]) for d in window["drivers"]), reverse=True)
    reported = [d["after"]["n"] for d in change["drivers"]]
    assert reported == planted and reported == sorted(reported, reverse=True)
    answer = rows["G1"]["outputs"]["answer"]
    population = change["population"]["after"]
    for n in reported:
        assert f"{n} of {population} calls in the after window" in answer
    quotes = [c for d in change["drivers"] for c in d["citations"]]
    assert quotes
    for cited in quotes:
        assert cited["quote"] in corpus.calls[cited["call_id"]].text


def test_the_coaching_follow_up_surfaces_the_waiver_pattern_across_calls(rows, corpus):
    change = rows["G3"]["outputs"]["change"]
    (pattern,) = change["patterns"]
    assert pattern["n"] >= 2 and pattern["n"] < pattern["of"]  # several calls, not all of them
    assert len(set(pattern["call_ids"])) == pattern["n"]
    assert not re.search(r"\bAGT-\d+\b", rows["G3"]["outputs"]["answer"])
    assert rows["G3"]["results"]["pattern_recall"]["score"] is True


@pytest.mark.parametrize("row_id, source", [("H1", "argument"), ("H3", "detected")])
def test_the_held_out_questions_find_their_event_and_every_planted_driver(rows, row_id, source):
    change = rows[row_id]["outputs"]["change"]
    assert change["change_point"]["source"] == source and change["drivers"]
    assert rows[row_id]["outputs"]["grounded"] is True
    assert rows[row_id]["results"]["driver_recall"]["score"] is True


def test_the_westside_question_is_detected_in_the_direction_the_data_moved(rows):
    cp = rows["H3"]["outputs"]["change"]["change_point"]
    assert cp["date"] == "2026-08-07" and cp["logged_change"] == "Westside branch closure"
    assert cp["direction"] == "fall"
    assert "a fall," in rows["H3"]["outputs"]["answer"]


# ── nothing planted ───────────────────────────────────────────────────────────

def ask(question: str, frame: dict) -> dict:
    import asyncio

    calls = build_golden.load_calls()
    graph = build_graph(
        get_llm=lambda: ScriptedClassifier({question: "explain_change"}, {question: {**NO_FRAME, **frame}}),
        toolbox=OfflineToolbox([], calls),
        call_exists=corpus_call_exists({c["call_id"] for c in calls}),
        call_date=corpus_call_date(calls),
        checkpointer=InMemorySaver(),
    )
    return asyncio.run(eval_run.run_question(graph, question))


def test_an_unplanted_subject_the_route_cannot_place_is_refused_without_a_tool_call(corpus):
    question = "Why did rate shopping calls go up after a competitor CD promotion?"
    out = ask(question, {"measure": "calls", "subject": "rate shopping calls"})

    assert out["change"] is None and out["sql"] is None and out["citations"] == []
    assert "can't tell which calls" in out["answer"]


@pytest.mark.parametrize("category_phrase, metric", [("loan calls", "calls"), ("insurance calls", "calls")])
def test_an_unplanted_call_type_with_no_shift_says_no_clear_change_point(corpus, category_phrase, metric):
    question = f"Why did {category_phrase} go up after a competitor promotion?"
    out = ask(question, {"measure": metric, "subject": category_phrase})

    change = out["change"]
    assert change["change_point"]["source"] == "none"
    assert change["windows"] is None and change["drivers"] == [] and change["patterns"] == []
    assert "no clear change point" in out["answer"]
    assert out["citations"] == [] and out["grounded"] is True


def test_a_shift_with_no_driver_in_the_calls_is_reported_as_not_enough_evidence(corpus):
    # Rate-inquiry call volume has no planted event, but the weekly series wobbles enough to
    # pass the detection threshold; the calls then hold no reason for it, and the answer says so.
    question = "Why did rate calls go up after a competitor CD promotion?"
    out = ask(question, {"measure": "calls", "subject": "rate calls"})

    change = out["change"]
    assert change["drivers"] == [] and change["patterns"] == []
    assert change["change_point"]["source"] == "detected"
    assert "Not enough evidence" in out["answer"]
    assert out["citations"] == [] and out["grounded"] is True
    assert "Sources:" not in out["answer"]
