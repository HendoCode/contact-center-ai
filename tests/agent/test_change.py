"""The explain_change route: classify, frame, resolve, analyze and ground. Offline, with a fake
LLM, a stub toolbox that returns the tool's real output shape, and an in-memory checkpointer."""

import pytest
from langgraph.types import Command

from agent_support import (
    SQL_AFTER,
    SQL_BEFORE,
    StubToolbox,
    change_result,
    config,
    tool_text,
)

pytestmark = pytest.mark.usefixtures("change_files")

NO_FRAME_FOLLOW_UP = {"measure": None, "subject": None, "when": None, "logged_change": None,
                      "follow_up": True}
FEE_FRAME = {"measure": "satisfaction", "subject": "fee calls",
             "logged_change": "Fee schedule change"}


def explain_calls(toolbox) -> list[dict]:
    return toolbox.tool_calls("explain_change")


def interrupt_payload(result: dict) -> dict:
    assert "__interrupt__" in result, f"expected an interrupt, got {result}"
    (interrupt,) = result["__interrupt__"]
    return interrupt.value


# ── the route ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_logged_change_question_calls_the_tool_once_and_answers_from_its_result(make, toolbox):
    graph, llm = make("explain_change", frame=FEE_FRAME)
    out = await graph.ainvoke({"question": "Why did satisfaction on fee calls drop?"}, config())

    assert out["route"] == "explain_change"
    assert explain_calls(toolbox) == [{
        "metric": "csat_average_score", "question": "Why did satisfaction on fee calls drop?",
        "filters": {"category": ["fee_dispute"]}, "change_point": "2026-04-15",
    }]
    assert toolbox.tool_calls("query_metric") == []  # the date came from the log: no lookup
    assert out["metric_names"] == ["csat_average_score"]
    assert out["change"]["windows"]["after"]["value"] == 3.13
    assert out["citations"] == ["CALL-00001"]
    assert out["grounded"] is True
    assert "Sources: CALL-00001" in out["answer"]
    assert "2 of 63 calls in the after window" in out["answer"]
    assert "cannot show that the change caused the shift" in out["answer"]
    assert out["sql"].count("window [") == 2 and SQL_BEFORE in out["sql"] and SQL_AFTER in out["sql"]
    assert out["answer"].endswith(f"SQL:\n{out['sql']}")
    assert out["answer"].count(SQL_AFTER) == 1  # the answer node adds the SQL once
    assert len(llm.prompts) == 1 and len(llm.frame_prompts) == 1


@pytest.mark.asyncio
async def test_frame_sees_the_change_log_and_whether_an_analysis_exists(make):
    graph, llm = make("explain_change", frame=FEE_FRAME)
    await graph.ainvoke({"question": "Why did satisfaction on fee calls drop?"}, config())

    (prompt,) = llm.frame_prompts
    assert "- Fee schedule change (2026-04-15)" in prompt
    assert "- Card fraud rule change (2026-06-15)" in prompt
    assert "already exists on this thread: no" in prompt
    assert prompt.rstrip().endswith("Question: Why did satisfaction on fee calls drop?")
    assert "already exists on this thread: no" in llm.prompts[0]


@pytest.mark.asyncio
async def test_a_branch_subject_filters_members_by_home_branch(make, toolbox):
    graph, _ = make("explain_change", frame={"measure": "satisfaction", "subject": "Westside members"})
    await graph.ainvoke({"question": "Why is satisfaction down for Westside members?"}, config())

    (call,) = explain_calls(toolbox)
    assert call["filters"] == {"home_branch": ["Westside"]}
    assert "change_point" not in call and "search_range" not in call  # the tool detects it


@pytest.mark.asyncio
async def test_a_card_subject_maps_to_both_card_categories(make, toolbox):
    graph, _ = make("explain_change", frame={"subject": "declined cards"})
    await graph.ainvoke({"question": "What are members upset about with declined cards?"}, config())

    (call,) = explain_calls(toolbox)
    assert call["filters"] == {"category": ["card_services", "fraud_dispute"]}


@pytest.mark.asyncio
async def test_no_subject_leaves_the_filters_to_the_tool(make, toolbox):
    graph, _ = make("explain_change", frame={"measure": "calls", "logged_change": "Card fraud rule change"})
    await graph.ainvoke({"question": "What changed for members after the rule change?"}, config())

    (call,) = explain_calls(toolbox)
    assert "filters" not in call and call["change_point"] == "2026-06-15"


@pytest.mark.asyncio
async def test_a_month_phrase_becomes_a_search_range_from_the_data_months(make, toolbox):
    graph, _ = make("explain_change", frame={"measure": "calls", "subject": "fee calls",
                                             "when": "late July"})
    out = await graph.ainvoke({"question": "Why did fee calls go up in late July?"}, config())

    assert toolbox.tool_calls("query_metric") == [
        {"metrics": ["call_volume"], "group_by": ["metric_time__month"]}
    ]
    (call,) = explain_calls(toolbox)
    assert call["search_range"] == {"start": "2026-07-18", "end": "2026-08-04"}
    assert "change_point" not in call
    assert out["grounded"] is True


@pytest.mark.asyncio
async def test_the_months_the_data_covers_are_looked_up_once_per_graph(make, toolbox):
    graph, _ = make("explain_change", frame={"measure": "calls", "when": "late July"})
    for thread in ("a", "b"):
        await graph.ainvoke({"question": "Why did calls go up in late July?"}, config(thread))

    assert len(toolbox.tool_calls("query_metric")) == 1
    assert len(explain_calls(toolbox)) == 2


@pytest.mark.asyncio
async def test_a_failed_months_lookup_is_refused_and_tried_again_next_time(make):
    class NoMonths(StubToolbox):
        async def call(self, name, arguments):
            if name == "query_metric":
                self.calls.append((name, arguments))
                return "TOOL ERROR (query_metric): MetricFlow query failed"
            return await super().call(name, arguments)

    tools = NoMonths()
    graph, _ = make("explain_change", frame={"when": "late July"}, tools=tools)
    first = await graph.ainvoke({"question": "Why did calls go up in late July?"}, config("a"))
    await graph.ainvoke({"question": "Why did calls go up in late July?"}, config("b"))

    assert "could not be read" in first["answer"] and tools.tool_calls("explain_change") == []
    assert len(tools.tool_calls("query_metric")) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("when, expected", [("2026-06-15", "2026-06-15"), ("June 15", "2026-06-15")])
async def test_a_date_is_the_change_point(make, toolbox, when, expected):
    graph, _ = make("explain_change", frame={"measure": "calls", "when": when})
    await graph.ainvoke({"question": f"Why did calls jump on {when}?"}, config())

    (call,) = explain_calls(toolbox)
    assert call["change_point"] == expected and "search_range" not in call
    # An ISO date carries its own year, so only "Month D" needs the data's months.
    assert len(toolbox.tool_calls("query_metric")) == (0 if when == "2026-06-15" else 1)


@pytest.mark.asyncio
async def test_with_no_measure_named_call_volume_is_used_and_said_so(make, toolbox):
    graph, _ = make("explain_change", frame={"subject": "fee calls",
                                             "logged_change": "Fee schedule change"})
    out = await graph.ainvoke({"question": "What are members saying about fees since April?"}, config())

    (call,) = explain_calls(toolbox)
    assert call["metric"] == "call_volume"
    assert out["answer"].startswith("Note: the question names no measure, so this uses call volume.")


@pytest.mark.asyncio
async def test_measure_phrases_resolve_to_the_supported_metrics(make, toolbox):
    for phrase, metric in [("handle time", "average_handle_time"), ("CSAT", "csat_average_score"),
                           ("first contact resolution", "first_contact_resolution_rate"),
                           ("the number of calls", "call_volume")]:
        toolbox.calls.clear()
        graph, _ = make("explain_change", frame={"measure": phrase, "when": "2026-06-15"})
        await graph.ainvoke({"question": f"Why did {phrase} change?"}, config(phrase))
        assert explain_calls(toolbox)[0]["metric"] == metric


# ── the no-change-point path ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_clear_change_point_is_reported_not_invented(make):
    none = change_result(
        change_point={"date": None, "source": "none", "logged_change": None, "score": 0.9,
                      "reason": "no week across all the data stands out from the weeks around it"},
        windows=None, population=None, coverage=None, drivers=[], unassigned=None,
        series={"sql": "SELECT 'series'", "weeks": [{"week": "2026-03-02", "value": 3.8, "n": 9}]},
        limits=["The timing of a change could not be fixed, so no windows or drivers are reported. "
                "Give a date, or the name of a logged change.",
                "The timing fits the change, but the data cannot show that the change caused the shift."],
    )
    graph, _ = make("explain_change", frame={"measure": "satisfaction"},
                    tools=StubToolbox(change=none))
    out = await graph.ainvoke({"question": "Why is satisfaction down?"}, config())

    assert out["change"]["change_point"]["source"] == "none"
    assert "no clear change point" in out["answer"]
    assert "Give a date" in out["answer"]
    assert out["citations"] == []
    assert out["sql"] == "-- weekly values searched\nSELECT 'series'"
    assert out["grounded"] is True  # the series it searched is the evidence


# ── the clarify interrupt ────────────────────────────────────────────────────

@pytest.fixture
def experience_term(monkeypatch):
    """A term that leaves two supported metrics. None of the shipped terms does, so the
    interrupt needs a fixture."""
    from agent import terms

    spec = terms.Term(
        name="experience", aliases=("experience",), unless=(),
        candidates={"csat_average_score": ("score",), "first_contact_resolution_rate": ("fixed",),
                    "average_mortgage_note_rate": ("mortgage",)},
    )
    monkeypatch.setattr(terms, "load_terms", lambda: {"experience": spec})


@pytest.mark.asyncio
async def test_a_term_with_several_supported_metrics_interrupts_once_and_resumes(
    make, toolbox, experience_term
):
    graph, llm = make("explain_change", frame={"measure": "member experience",
                                               "logged_change": "Fee schedule change"})
    cfg = config()
    first = await graph.ainvoke({"question": "Why did member experience drop after the fee change?"}, cfg)

    payload = interrupt_payload(first)
    assert payload["kind"] == "clarify_metric" and payload["version"] == 1
    assert payload["term"] == "experience" and payload["multi_select"] is True
    assert [o["id"] for o in payload["options"]] == [
        "csat_average_score", "first_contact_resolution_rate"  # the unsupported candidate is not offered
    ]
    assert toolbox.calls == []  # asked instead of guessing

    out = await graph.ainvoke(Command(resume={"choices": ["first_contact_resolution_rate"]}), cfg)
    assert "__interrupt__" not in out
    assert explain_calls(toolbox)[0]["metric"] == "first_contact_resolution_rate"
    assert len(llm.frame_prompts) == 1  # the frame step did not run again on resume


@pytest.mark.asyncio
async def test_an_invalid_choice_asks_again_and_two_choices_are_refused(make, toolbox, experience_term):
    graph, _ = make("explain_change", frame={"measure": "member experience"})
    cfg = config()
    await graph.ainvoke({"question": "Why did member experience drop?"}, cfg)

    again = await graph.ainvoke(Command(resume={"choices": ["nope"]}), cfg)
    assert "Answer with option ids" in interrupt_payload(again)["prompt"]

    out = await graph.ainvoke(
        Command(resume={"choices": ["csat_average_score", "first_contact_resolution_rate"]}), cfg
    )
    assert out["change"] is None and toolbox.calls == []
    assert "one measure at a time" in out["answer"]


# ── refusals: the phrase is named and no tool is called ─────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("frame, phrase", [
    ({"measure": "interest rate"}, "interest rate"),  # a term, but none of its metrics is supported
    ({"measure": "net promoter score"}, "net promoter score"),
])
async def test_a_measure_that_cannot_be_explained_is_refused(make, toolbox, frame, phrase):
    graph, _ = make("explain_change", frame=frame)
    out = await graph.ainvoke({"question": f"Why did {phrase} change?"}, config())

    assert toolbox.calls == []
    assert f'"{phrase}"' in out["answer"]
    assert "Call Volume, CSAT Average Score" in out["answer"]  # what can be explained
    assert out["change"] is None and out["citations"] == [] and out["sql"] is None
    assert out["grounded"] is True and not out["answer"].startswith("Not grounded")


@pytest.mark.asyncio
async def test_a_subject_that_matches_nothing_is_refused(make, toolbox):
    graph, _ = make("explain_change", frame={"subject": "the moon base"})
    out = await graph.ainvoke({"question": "Why did the moon base calls rise?"}, config())

    assert toolbox.calls == []
    assert '"the moon base"' in out["answer"] and "type of call" in out["answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["last week", "recently", "sometime in 2019"])
async def test_a_time_phrase_outside_the_grammar_is_refused(make, toolbox, when):
    graph, _ = make("explain_change", frame={"when": when})
    out = await graph.ainvoke({"question": f"Why did calls change {when}?"}, config())

    assert toolbox.tool_calls("explain_change") == []
    assert f'"{when}"' in out["answer"] and "I can't place" in out["answer"]
    assert "late July" in out["answer"]  # what is understood


@pytest.mark.asyncio
async def test_a_month_outside_the_data_is_refused(make, toolbox):
    graph, _ = make("explain_change", frame={"when": "December"})
    out = await graph.ainvoke({"question": "Why did calls jump in December?"}, config())

    assert toolbox.tool_calls("explain_change") == []
    assert '"December"' in out["answer"]


@pytest.mark.asyncio
async def test_a_logged_change_the_log_does_not_have_is_refused(make, toolbox):
    graph, _ = make("explain_change", frame={"logged_change": "Office relocation"})
    out = await graph.ainvoke({"question": "Why did calls change after the relocation?"}, config())

    assert toolbox.calls == []
    assert '"Office relocation" is not in the change log' in out["answer"]
    assert "Fee schedule change (2026-04-15)" in out["answer"]


@pytest.mark.asyncio
async def test_without_a_change_log_a_named_change_is_refused_with_the_fix(make, toolbox, monkeypatch):
    from agent.subgraphs import change

    monkeypatch.setattr(change, "load_change_log", lambda: [])
    graph, _ = make("explain_change", frame={"logged_change": "Fee schedule change"})
    out = await graph.ainvoke({"question": "Why did calls change after the fee change?"}, config())

    assert toolbox.calls == []
    assert "no change log is available" in out["answer"] and "date of the change" in out["answer"]


# ── tool failure ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_tool_error_is_shown_and_not_grounded(make):
    error = "TOOL ERROR (explain_change): average_handle_time needs an explicit change point"
    graph, _ = make("explain_change", frame={"measure": "handle time"},
                    tools=StubToolbox(change=error))
    out = await graph.ainvoke({"question": "Why did handle time change?"}, config())

    assert out["change"] is None and out["sql"] is None and out["citations"] == []
    assert out["grounded"] is False
    assert out["answer"].startswith("Not grounded") and "needs an explicit change point" in out["answer"]


# ── grounding ────────────────────────────────────────────────────────────────

def with_citation(call_id: str, quote: str = "A quote that is long enough to cite.") -> dict:
    result = change_result()
    result["drivers"][0]["citations"] = [{"call_id": call_id, "quote": quote}]
    return result


async def run_with(make, result) -> dict:
    graph, _ = make("explain_change", frame=FEE_FRAME, tools=StubToolbox(change=result))
    return await graph.ainvoke({"question": "Why did satisfaction on fee calls drop?"}, config())


@pytest.mark.asyncio
async def test_a_citation_outside_the_windows_is_dropped_and_not_grounded(make):
    in_a_window = with_citation("CALL-00042")
    in_a_window["drivers"][0]["before"]["call_ids"] = ["CALL-00042"]
    assert (await run_with(make, in_a_window))["grounded"] is True

    result = with_citation("CALL-00001")
    result["drivers"][0]["after"]["call_ids"] = ["CALL-00002"]  # CALL-00001 is in no window
    out = await run_with(make, result)
    assert out["citations"] == [] and out["grounded"] is False


@pytest.mark.asyncio
async def test_a_citation_that_is_not_a_real_call_is_not_grounded(make):
    result = with_citation("CALL-99999")
    result["drivers"][0]["after"]["call_ids"].append("CALL-99999")
    out = await run_with(make, result)

    assert out["citations"] == [] and out["grounded"] is False


@pytest.mark.asyncio
async def test_a_missing_window_sql_is_not_grounded(make):
    result = change_result()
    result["windows"]["before"]["sql"] = ""
    assert (await run_with(make, result))["grounded"] is False


@pytest.mark.asyncio
async def test_an_agent_id_anywhere_is_not_grounded(make):
    result = with_citation("CALL-00001", "AGT-017 told the member it only happens once a year.")
    assert (await run_with(make, result))["grounded"] is False


@pytest.mark.asyncio
async def test_an_answer_with_no_citation_is_not_grounded(make):
    result = change_result()
    result["drivers"][0]["citations"] = []
    out = await run_with(make, result)

    assert out["citations"] == [] and out["grounded"] is False


# ── a follow-up on the same thread ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_follow_up_reuses_the_stored_evidence_and_asks_for_coaching_patterns(make, toolbox):
    graph, llm = make("explain_change", frame=FEE_FRAME)
    cfg = config("thread")
    first = await graph.ainvoke({"question": "Why did satisfaction on fee calls drop?"}, cfg)
    toolbox.calls.clear()

    llm.frame = {**llm.frame, "follow_up": True, "measure": None, "subject": None,
                 "logged_change": None}
    out = await graph.ainvoke({"question": "What should supervisors coach on for fee calls?"}, cfg)

    assert "already exists on this thread: yes" in llm.prompts[-1]
    assert "already exists on this thread: yes" in llm.frame_prompts[-1]
    (call,) = toolbox.calls
    assert call[0] == "explain_change"
    assert call[1] == {
        "metric": "csat_average_score", "filters": {"category": ["fee_dispute"]},
        "change_point": "2026-04-15", "mode": "coaching",
        "question": "What should supervisors coach on for fee calls?",
        "groups": [{"label": "Members did not know about the change",
                    "call_ids": ["CALL-00001", "CALL-00002"]}],
    }
    assert out["change"]["windows"] == first["change"]["windows"]  # the same evidence
    assert out["change"]["drivers"] == first["change"]["drivers"]
    assert out["change"]["mode"] == "coaching" and len(out["change"]["patterns"]) == 1
    assert "Rule explained differently from call to call: 2 of 2 calls" in out["answer"]
    assert out["citations"] == ["CALL-00002"] and out["grounded"] is True
    assert out["sql"].count("window [") == 2  # the windows' SQL, from the stored analysis
    assert "AGT-" not in out["answer"]


@pytest.mark.asyncio
async def test_a_follow_up_with_nothing_to_build_on_is_a_new_question(make, toolbox):
    graph, _ = make("explain_change", frame={"follow_up": True, "measure": "satisfaction",
                                             "logged_change": "Fee schedule change"})
    out = await graph.ainvoke({"question": "What should supervisors coach on?"}, config())

    (call,) = explain_calls(toolbox)
    assert "mode" not in call and call["metric"] == "csat_average_score"
    assert out["change"]["mode"] == "drivers"


@pytest.mark.asyncio
async def test_a_follow_up_on_an_analysis_without_drivers_is_refused(make, toolbox):
    toolbox.change = change_result(drivers=[])
    graph, llm = make("explain_change", frame=FEE_FRAME)
    cfg = config()
    await graph.ainvoke({"question": "Why did satisfaction on fee calls drop?"}, cfg)
    toolbox.calls.clear()

    llm.frame = {**llm.frame, "follow_up": True}
    out = await graph.ainvoke({"question": "What should supervisors coach on?"}, cfg)

    assert toolbox.calls == [] and "no drivers" in out["answer"]


@pytest.mark.asyncio
async def test_change_is_only_what_this_run_produced_but_the_thread_keeps_the_latest_analysis(
    make, toolbox
):
    graph, llm = make("explain_change", frame=FEE_FRAME)
    cfg = config("kept")
    first = await graph.ainvoke({"question": "Why did satisfaction on fee calls drop?"}, cfg)
    assert first["change"]["metric"] == "csat_average_score"

    # Another route on the same thread: nothing from the analysis is in this run's output.
    llm.route = "retrieve"
    second = await graph.ainvoke({"question": "What are members saying about fraud?"}, cfg)
    assert second["route"] == "retrieve" and second["change"] is None
    assert "Members did not know" not in second["answer"]

    # A refusal, a tool error and a metric question likewise.
    llm.route, llm.frame = "explain_change", {**llm.frame, "subject": "the moon base"}
    refused = await graph.ainvoke({"question": "Why did the moon base calls rise?"}, cfg)
    assert refused["change"] is None and refused["citations"] == [] and refused["sql"] is None

    llm.frame = {**llm.frame, "subject": "fee calls"}
    toolbox.change = "TOOL ERROR (explain_change): boom"
    errored = await graph.ainvoke({"question": "Why did satisfaction on fee calls drop?"}, cfg)
    assert errored["change"] is None and errored["grounded"] is False

    llm.route = "resolve_metric"
    metric = await graph.ainvoke({"question": "How many calls did we handle?"}, cfg)
    assert metric["change"] is None

    # The latest analysis is still there for a follow-up.
    toolbox.change, toolbox.calls[:] = None, []
    llm.route, llm.frame = "explain_change", {**NO_FRAME_FOLLOW_UP}
    follow = await graph.ainvoke({"question": "What should supervisors coach on?"}, cfg)
    assert toolbox.calls[0][1]["mode"] == "coaching"
    assert follow["change"]["windows"] == first["change"]["windows"]


def test_the_rendered_answer_has_no_stray_sql_marker_before_the_end():
    text = tool_text(change_result())
    body = text.split("\nCHANGE RESULT")[0]
    assert body.count("Generated SQL:") == 1
    assert body.index("Generated SQL:") > body.index("What the data could not determine")
