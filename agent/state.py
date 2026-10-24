"""Graph state and the JSON output contract (docs/design/agent-graph.md)."""

from typing import Literal, TypedDict

Route = Literal["retrieve", "resolve_metric", "summarize_call", "explain_change"]


class ChangeFrame(TypedDict):
    """What a "why did X change" question names, as read by the `frame` node."""

    measure: str | None  # "satisfaction"
    subject: str | None  # "fee calls"
    when: str | None  # "late July", "since April", "2026-06-15"
    logged_change: str | None  # a change_log name, chosen by frame
    follow_up: bool


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
    change: dict | None  # the explain_change evidence this run produced; None otherwise


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
    # explain_change (agent/subgraphs/change.py). `prior_change` outlives the run: classify keeps
    # the latest analysis there, so a follow-up on the thread reuses it; `change` is per run.
    change_frame: ChangeFrame | None
    change_filters: dict  # {"category": [...], "home_branch": [...]}; {} lets the tool choose
    change_point: str | None  # ISO date, when the question gives one
    change_search: dict | None  # {"start", "end"} to look in when it gives a period
    change: dict | None
    prior_change: dict | None
    refusal: str | None  # why the question was not run, in words; no tool was called
