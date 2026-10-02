"""The evaluators, the LLM-as-judge (with a fake judge model), and the regression gate."""

import pytest
from langchain_core.runnables import RunnableLambda

from evals import evaluators as ev
from evals.run import check_judge_differs, gate, summarize

METRIC_REF = {
    "route": "resolve_metric", "expect_interrupt": False, "interrupts": [],
    "metric_names": ["average_handle_time"], "expect_sql": True, "call_id": None,
    "expect_citations": False, "reference": "AHT with its SQL.",
}
AMBIGUOUS_REF = {
    **METRIC_REF, "expect_interrupt": True, "metric_names": ["average_deposit_apy"],
    "interrupts": [{"term": "rate", "options": ["average_deposit_apy", "average_heloc_current_rate"]}],
}
LOOKUP_REF = {
    **METRIC_REF, "route": "summarize_call", "metric_names": [], "expect_sql": False,
    "call_id": "CALL-00042", "expect_citations": True,
}
MISSING_REF = {**LOOKUP_REF, "call_id": "CALL-01251", "expect_citations": False}
OPEN_REF = {**LOOKUP_REF, "route": "retrieve", "call_id": None}

SQL = "SELECT 1"
GOOD_METRIC = {
    "route": "resolve_metric", "metric_names": ["average_handle_time"], "sql": SQL,
    "answer": f"average_handle_time: 300\n\nSQL:\n{SQL}", "citations": [], "interrupts": [],
}


def score(fn, outputs, ref, inputs=None):
    return fn(inputs or {"question": "q"}, outputs, ref)["score"]


def test_route():
    assert score(ev.route, GOOD_METRIC, METRIC_REF) is True
    assert score(ev.route, {**GOOD_METRIC, "route": "retrieve"}, METRIC_REF) is False


def test_interrupt_fires_when_it_should_and_not_otherwise():
    asked = {**GOOD_METRIC, "interrupts": AMBIGUOUS_REF["interrupts"]}
    assert score(ev.interrupt, GOOD_METRIC, METRIC_REF) is True
    assert score(ev.interrupt, asked, METRIC_REF) is False  # fired, should not have
    assert score(ev.interrupt, asked, AMBIGUOUS_REF) is True
    assert score(ev.interrupt, GOOD_METRIC, AMBIGUOUS_REF) is False  # should have fired
    reordered = {**GOOD_METRIC, "interrupts": [
        {"term": "rate", "options": ["average_heloc_current_rate", "average_deposit_apy"]}]}
    assert score(ev.interrupt, reordered, AMBIGUOUS_REF) is True  # option order is not scored
    wrong = {**GOOD_METRIC, "interrupts": [{"term": "rate", "options": ["average_deposit_apy"]}]}
    assert score(ev.interrupt, wrong, AMBIGUOUS_REF) is False


def test_metric():
    assert score(ev.metric, GOOD_METRIC, METRIC_REF) is True
    assert score(ev.metric, {**GOOD_METRIC, "metric_names": ["call_volume"]}, METRIC_REF) is False
    assert score(ev.metric, {**GOOD_METRIC, "metric_names": []}, METRIC_REF) is False
    assert score(ev.metric, GOOD_METRIC, LOOKUP_REF) is None


def test_sql_present_and_attached_to_the_answer():
    assert score(ev.sql, GOOD_METRIC, METRIC_REF) is True
    assert score(ev.sql, {**GOOD_METRIC, "sql": None}, METRIC_REF) is False
    assert score(ev.sql, {**GOOD_METRIC, "answer": "300"}, METRIC_REF) is False
    assert score(ev.sql, GOOD_METRIC, LOOKUP_REF) is None


def test_citations_present_and_real():
    good = {"route": "retrieve", "citations": ["CALL-00001"], "answer": "Sources: CALL-00001"}
    assert score(ev.citations, good, OPEN_REF) is True
    assert score(ev.citations, {**good, "citations": []}, OPEN_REF) is False
    invented = {**good, "answer": "See CALL-00001 and CALL-99999."}
    assert score(ev.citations, invented, OPEN_REF) is False
    assert score(ev.citations, GOOD_METRIC, METRIC_REF) is None


def test_citations_for_a_call_that_does_not_exist():
    empty = {"route": "summarize_call", "citations": [],
             "answer": "No transcript found for call ID: CALL-01251"}
    assert score(ev.citations, empty, MISSING_REF) is True
    assert score(ev.citations, {**empty, "citations": ["CALL-01251"]}, MISSING_REF) is False


def test_call_id():
    hit = {"route": "summarize_call", "citations": ["CALL-00042"], "answer": "CALL-00042 ..."}
    assert score(ev.call_id, hit, LOOKUP_REF) is True
    assert score(ev.call_id, {**hit, "citations": ["CALL-00001"]}, LOOKUP_REF) is False
    missing = {"route": "summarize_call", "citations": [],
               "answer": "No transcript found for call ID: CALL-01251"}
    assert score(ev.call_id, missing, MISSING_REF) is True
    assert score(ev.call_id, {**missing, "answer": "nothing"}, MISSING_REF) is False
    assert score(ev.call_id, GOOD_METRIC, METRIC_REF) is None


class FakeJudge:
    def __init__(self, score: int):
        self.score = score
        self.prompts: list[str] = []

    def with_structured_output(self, schema):
        def respond(prompt):
            self.prompts.append(prompt)
            return schema(reasoning="faithful", score=self.score)

        return RunnableLambda(respond)


def test_judge_formats_the_versioned_prompt_and_scales_the_score():
    llm = FakeJudge(5)
    judge = ev.make_judge(llm)
    outputs = {**GOOD_METRIC, "interrupts": AMBIGUOUS_REF["interrupts"]}
    result = judge({"question": "What is our average rate?", "clarify_with": ["average_deposit_apy"]},
                   outputs, AMBIGUOUS_REF)

    assert result["key"] == "judge" and result["score"] == 1.0
    assert result["comment"].startswith(f"5/5 (prompt {ev.JUDGE_PROMPT_VERSION})")
    (prompt,) = llm.prompts
    assert "Question: What is our average rate?" in prompt
    assert 'asked what "rate" meant; the user answered average_deposit_apy' in prompt
    assert "Reference: AHT with its SQL." in prompt
    assert GOOD_METRIC["answer"] in prompt
    assert "{" not in prompt.replace(GOOD_METRIC["answer"], "")  # every placeholder filled
    assert ev.make_judge(FakeJudge(1))({"question": "q"}, GOOD_METRIC, METRIC_REF)["score"] == 0.0


def test_judge_model_must_differ_from_the_agent_model():
    check_judge_differs("z-ai/glm-5.3-flash", "claude-opus-5-5")
    with pytest.raises(SystemExit, match="grading itself"):
        check_judge_differs("claude-sonnet-4-5", "Claude-Sonnet-4-5")


def test_gate_flags_new_failures_and_fixed_ones():
    known = {"g01": {"route": "why"}}
    assert gate({"g01": {"route": "x"}}, known) == []
    new = gate({"g01": {"route": "x"}, "g02": {"sql": "no SQL"}}, known)
    assert new == ["NEW FAILURE g02 sql: no SQL"]
    fixed = gate({}, known)
    assert len(fixed) == 1 and fixed[0].startswith("NOW PASSES g01 route")


def test_summarize_counts_applicable_items_only():
    rows = [
        {"kind": "metric", "results": {"sql": {"score": True}, "citations": {"score": None}}},
        {"kind": "metric", "results": {"sql": {"score": False}, "citations": {"score": None}}},
    ]
    table = summarize(rows, ["sql", "citations"])
    assert table["all"] == {"n": 2, "sql": 0.5, "citations": None}
    assert set(table) == {"all", "metric"}
