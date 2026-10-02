"""answer: format tool output, citations and SQL. Adds no facts."""

from agent.state import AgentState

SQL_MARKER = "\nGenerated SQL:"


def split_sql(tool_output: str) -> tuple[str, str | None]:
    """Split a metric tool's text into (body, sql); sql is None when absent."""
    body, marker, sql = tool_output.partition(SQL_MARKER)
    return body.rstrip(), (sql.strip() or None) if marker else None


async def answer(state: AgentState) -> dict:
    body, _ = split_sql(state.get("tool_output", ""))
    parts = [] if state.get("grounded") else ["Not grounded: treat this answer as unverified."]
    parts.append(body)
    if state.get("citations"):
        parts.append("Sources: " + ", ".join(state["citations"]))
    if state.get("sql"):
        parts.append(f"SQL:\n{state['sql']}")
    return {"answer": "\n\n".join(parts)}
