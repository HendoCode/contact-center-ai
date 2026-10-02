"""Graph state and the JSON output contract (docs/design/agent-graph.md)."""

from typing import Literal, TypedDict

Route = Literal["retrieve", "resolve_metric", "summarize_call"]


class AgentInput(TypedDict):
    question: str


class AgentOutput(TypedDict):
    """What a caller reads from a finished run; all JSON, so evals read fields."""

    answer: str
    route: Route
    metric_names: list[str]
    sql: str | None
    citations: list[str]
    grounded: bool


class AgentState(TypedDict, total=False):
    question: str
    route: Route
    call_id: str | None
    term: str | None  # ambiguous term awaiting an answer, e.g. "rate"
    candidates: list[str]  # metric names offered for `term`
    metric_names: list[str]
    resolved_terms: list[str]  # terms already clarified (several may appear in one question)
    clarify_attempts: int
    tool_output: str
    sql: str | None
    citations: list[str]
    grounded: bool
    answer: str
