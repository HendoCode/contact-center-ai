"""A cited call, laid out to be read on a shared screen.

Shows the call id, the date, the cited turn with a little context either side, and every
account number masked to its last four digits. Long lines are wrapped.
"""

import re
import textwrap
from datetime import datetime

from ccai_mcp.masking import mask_accounts

WIDTH = 72
CONTEXT_TURNS = 1  # turns shown either side of the cited one
_TURN = re.compile(r"(?:^|\s)(MEMBER|AGENT):\s*")


def split_turns(text: str) -> list[str]:
    """`MEMBER: ... AGENT: ...` as one string per turn, speaker label kept."""
    marks = list(_TURN.finditer(text.strip()))
    body = text.strip()
    return [f"{m.group(1)}: {body[m.end():(marks[i + 1].start() if i + 1 < len(marks) else None)].strip()}"
            for i, m in enumerate(marks)] or [body]


def _wrap(text: str, first: str, rest: str, width: int) -> str:
    return textwrap.fill(text, width=width, initial_indent=first, subsequent_indent=rest,
                         break_long_words=True, break_on_hyphens=False)


def _day(raw: str) -> str:
    try:
        return datetime.fromisoformat(raw).strftime("%Y-%m-%d (%a) %H:%M")
    except (TypeError, ValueError):
        return str(raw or "?")


def render_citation(call_id: str, quote: str, metadata: dict, text: str,
                    width: int = WIDTH) -> str:
    """The citation view. Everything printed has been through `mask_accounts`."""
    turns = [mask_accounts(t) for t in split_turns(text)]
    quote = mask_accounts(quote)
    cited = next((i for i, t in enumerate(turns) if quote in t), None)
    lines = [f"citation  {call_id}",
             f"  date:      {_day(metadata.get('date'))}",
             f"  call:      {metadata.get('category', '?')}, outcome {metadata.get('outcome', '?')}",
             _wrap(f'quote:     "{quote}"', "  ", "             ", width)]
    if cited is None:
        lines.append("  the quote was not found in the stored transcript")
        return "\n".join(lines)
    first, last = max(0, cited - CONTEXT_TURNS), min(len(turns), cited + CONTEXT_TURNS + 1)
    lines.append(f"  turn {cited + 1} of {len(turns)}, with {cited - first} before and "
                 f"{last - cited - 1} after:")
    for i in range(first, last):
        mark = ">> " if i == cited else "   "
        lines.append(_wrap(turns[i], f"    {mark}", "       " + " " * len(mark), width))
    lines.append("  accounts:  masked to the last four digits")
    return "\n".join(lines)
