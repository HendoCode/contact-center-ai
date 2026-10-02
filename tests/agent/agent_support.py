"""Test doubles for the agent graph: a fake LLM and a stub toolbox. No network, no DB."""

from langchain_core.runnables import RunnableLambda

EXISTING_CALLS = {"CALL-00001", "CALL-00002", "CALL-00042"}


class FakeLLM:
    """Stands in for the chat model; classify is its only caller."""

    def __init__(self, route: str = "retrieve"):
        self.route = route
        self.prompts: list[str] = []

    def with_structured_output(self, schema):
        def respond(prompt):
            self.prompts.append(prompt)
            return schema(route=self.route)

        return RunnableLambda(respond)


class StubToolbox:
    """Canned MCP tool outputs in the shapes the real server returns; records every call."""

    def __init__(self, search_output: str | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.search_output = search_output or (
            "Members disputed card charges. See CALL-00001 and CALL-00002."
        )

    async def call(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        if name == "search_transcripts":
            return self.search_output
        if name == "get_call_summary":
            return f"Summary of {arguments['call_id']}: the member disputed a charge."
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
