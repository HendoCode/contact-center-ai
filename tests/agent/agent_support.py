"""Test doubles for the agent graph: a fake LLM and a stub toolbox. No network, no DB."""

from langchain_core.runnables import RunnableLambda

from ccai_mcp.change import RESULT_MARKER, render_change

EXISTING_CALLS = {"CALL-00001", "CALL-00002", "CALL-00042"}

CHANGE_LOG = [
    {"name": "Fee schedule change", "date": "2026-04-15"},
    {"name": "Card fraud rule change", "date": "2026-06-15"},
]
BRANCHES = ["Westside", "Downtown"]
NO_FRAME = {"measure": None, "subject": None, "when": None, "logged_change": None,
            "follow_up": False}


class FakeLLM:
    """Stands in for the chat model. `classify` asks for a route and `frame` for the parts of a
    change question; each gets its scripted answer, and the prompts are kept apart."""

    def __init__(self, route: str = "retrieve", frame: dict | None = None):
        self.route = route
        self.frame = {**NO_FRAME, **(frame or {})}
        self.prompts: list[str] = []  # classify
        self.frame_prompts: list[str] = []

    def with_structured_output(self, schema):
        def respond(prompt):
            if "route" in schema.model_fields:
                self.prompts.append(prompt)
                return schema(route=self.route)
            self.frame_prompts.append(prompt)
            return schema(**self.frame)

        return RunnableLambda(respond)


SQL_BEFORE = "SELECT 'before' AS stub"
SQL_AFTER = "SELECT 'after' AS stub"


def change_result(**overrides) -> dict:
    """An explain_change result in the tool's JSON shape, with calls that exist in the stub."""
    result = {
        "version": 1, "mode": "drivers", "metric": "csat_average_score",
        "filters": {"category": ["fee_dispute"]},
        "change_point": {"date": "2026-04-15", "source": "argument",
                         "logged_change": "Fee schedule change", "score": None, "reason": None},
        "windows": {
            "before": {"start": "2026-03-04", "end": "2026-04-15", "value": 3.8, "n": 10,
                       "days": 42, "sql": SQL_BEFORE},
            "after": {"start": "2026-04-15", "end": "2026-05-27", "value": 3.13, "n": 45,
                      "days": 42, "sql": SQL_AFTER, "weekly": [], "weekly_sql": "SELECT 'weeks'"},
        },
        "population": {"before": 11, "after": 63}, "coverage": {"read_before": 11, "read_after": 63},
        "drivers": [{
            "label": "Members did not know about the change",
            "definition": "Members found a new charge they had not been told about.",
            "before": {"n": 1, "call_ids": ["CALL-00042"]},
            "after": {"n": 2, "call_ids": ["CALL-00001", "CALL-00002"]},
            "citations": [{"call_id": "CALL-00001", "quote": "Nobody told me the fee went up."}],
        }],
        "patterns": [], "unassigned": {"before": 10, "after": 61}, "series": None,
        "limits": ["The timing fits the change, but the data cannot show that the change "
                   "caused the shift."],
    }
    result.update(overrides)
    return result


def coaching_result(**overrides) -> dict:
    result = change_result(
        mode="coaching", windows=None, population=None, coverage=None, drivers=[], unassigned=None,
        patterns=[{
            "label": "Rule explained differently from call to call",
            "definition": "The agent describes the same rule in different ways.",
            "driver": "Members did not know about the change", "n": 2, "of": 2,
            "call_ids": ["CALL-00001", "CALL-00002"],
            "citations": [{"call_id": "CALL-00002", "quote": "That only happens once a year."}],
        }],
    )
    result.update(overrides)
    return result


def tool_text(result: dict) -> str:
    """The two text blocks the real tool returns, joined as MCPToolbox joins them."""
    import json

    return render_change(result) + f"\n{RESULT_MARKER}\n{json.dumps(result)}"


class StubToolbox:
    """Canned MCP tool outputs in the shapes the real server returns; records every call."""

    def __init__(self, search_output: str | None = None, change: str | dict | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.search_output = search_output or (
            "Members disputed card charges. See CALL-00001 and CALL-00002."
        )
        self.change = change  # explain_change: a result dict, or raw text such as a TOOL ERROR

    async def call(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        if name == "search_transcripts":
            return self.search_output
        if name == "get_call_summary":
            return f"Summary of {arguments['call_id']}: the member disputed a charge."
        if name == "explain_change":
            if isinstance(self.change, str):
                return self.change
            if arguments.get("mode") == "coaching":
                return tool_text(coaching_result())
            return tool_text(self.change or change_result())
        if name == "query_metric" and arguments.get("group_by") == ["metric_time__month"]:
            months = "\n".join(f"2026-{m:02d}-01 00:00:00  100" for m in range(3, 10))
            return f"MetricFlow result (1 metric(s)):\nmetric_time__month  call_volume\n{months}"
        if name == "query_metric":
            names = arguments["metrics"]
            rows = "\n".join(f"  {n}: 1.0" for n in names)
            return (
                f"MetricFlow result ({len(names)} metric(s)):\n{rows}\n\n"
                "Generated SQL:\nSELECT 1 AS stub"
            )
        if name == "ask_the_analyst":
            return (
                f'Question: "{arguments["question"]}"\n'
                "Resolved to 2 declared metric(s):\n\n"
                "- call_volume — Number of calls.\n- average_handle_time — Mean handle time.\n\n"
                "MetricFlow result (2 metric(s)):\n  call_volume: 10\n\n"
                "Generated SQL:\nSELECT 2 AS stub"
            )
        raise KeyError(name)

    def tool_calls(self, name: str) -> list[dict]:
        return [args for n, args in self.calls if n == name]


async def call_exists(call_id: str) -> bool:
    return call_id in EXISTING_CALLS


def config(thread_id: str = "t1") -> dict:
    return {"configurable": {"thread_id": thread_id}}
