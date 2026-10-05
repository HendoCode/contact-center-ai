"""
A teacher that runs each label request through the Claude Code CLI (`claude -p`).

For labeling on a Claude subscription instead of an API key: `LLM_PROVIDER=claude-code`.
The call is kept lean (our system prompt replaces Claude Code's, no tools, no settings,
no MCP servers, run from an empty directory) so each request carries little more than
the label prompt. The model is configuration: CLAUDE_CODE_MODEL, default `sonnet`.

It implements only what `data_gen.label_one` uses: `invoke(messages)` and `model`.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Any


class ClaudeCodeError(RuntimeError):
    """The CLI exited non-zero or reported an error; `label_one` retries it like an API error."""


def _render(messages: list[Any]) -> tuple[str, str]:
    """Split LangChain messages into (system prompt, one prompt text).

    `claude -p` takes a single prompt, so a retry's earlier turns (the rejected reply and
    the correction) are rendered into it in order.
    """
    system, turns = "", []
    for m in messages:
        kind = getattr(m, "type", "human")
        if kind == "system":
            system = str(m.content)
        elif kind == "ai":
            turns.append(f"Your previous reply was:\n{m.content}")
        else:
            turns.append(str(m.content))
    return system, "\n\n".join(turns)


class ClaudeCodeTeacher:
    def __init__(self, model: str | None = None, *, binary: str = "claude", timeout: float = 300,
                 run=subprocess.run):
        self.alias = model or os.getenv("CLAUDE_CODE_MODEL", "sonnet")
        self.model = f"claude-code/{self.alias}"
        self.binary, self.timeout, self._run = binary, timeout, run

    def invoke(self, messages: list[Any]) -> str:
        system, prompt = _render(messages)
        cmd = [self.binary, "-p", "--model", self.alias, "--output-format", "json",
               "--system-prompt", system, "--tools", "", "--setting-sources", "",
               "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"]
        with tempfile.TemporaryDirectory(prefix="ccai-teacher-") as cwd:
            proc = self._run(cmd, input=prompt, capture_output=True, text=True,
                             timeout=self.timeout, cwd=cwd)
        if proc.returncode != 0:
            raise ClaudeCodeError(f"claude exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:300]}")
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise ClaudeCodeError(f"claude printed non-JSON output: {proc.stdout[:300]}") from exc
        if out.get("is_error"):
            raise ClaudeCodeError(f"claude reported an error: {str(out.get('result'))[:300]}")
        return str(out.get("result", ""))
