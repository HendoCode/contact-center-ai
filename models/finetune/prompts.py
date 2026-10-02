"""
The summary_v1 prompt. One system prompt serves the teacher labeler here and,
later, the prompted and fine-tuned arms (F2, F6), so every model is asked the
same question. Definitions come from `schema.py`, so prompt and schema cannot
drift apart.
"""

from __future__ import annotations

from models.finetune.schema import CATEGORIES, METRIC_NAMES, PRODUCT_LINES, RESOLUTIONS


def _bullets(items: dict[str, str]) -> str:
    return "\n".join(f"- {name}: {meaning}" for name, meaning in items.items())


SYSTEM_PROMPT = f"""You summarize credit union call transcripts as one JSON object.

Reply with the JSON object only: no prose, no code fence. Use exactly these keys.

reason_for_call: one of {", ".join(CATEGORIES)}.
product_line: the member's line of business for the call, one of {", ".join(PRODUCT_LINES)}. Use "unknown" when the transcript gives no cue.
metric_mentioned: a list of {{"name": ..., "value": ...}}, one entry for every account figure the agent says aloud (balances, rates, limits, payments, premiums, dates), in the order spoken. Copy each value character for character from the transcript, including "$", "," and "%". Never list the member number, the member's name or the account's last four digits. "name" must be one of:
{_bullets(METRIC_NAMES)}
resolution: how the call ended, one of:
{_bullets(RESOLUTIONS)}
follow_up: {{"needed": true or false, "action": ...}}. needed is true when something is still pending when the call ends (a confirmation to send, an investigation, a quote or review to do). action is then one short sentence naming it; otherwise action is null.
"""


def user_prompt(text: str) -> str:
    """The per-call user message: the transcript text, nothing else."""
    return f"Transcript:\n{text}"
