"""Offline tests for the Claude Code teacher: no `claude` process is started."""

import json
import subprocess

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from models.finetune import data_gen
from models.finetune.claude_code import ClaudeCodeError, ClaudeCodeTeacher
from tests.finetune.conftest import no_sleep


class FakeRun:
    def __init__(self, *results):
        self.results, self.calls = list(results), []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        code, stdout = self.results.pop(0)
        return subprocess.CompletedProcess(cmd, code, stdout=stdout, stderr="boom")


def ok(text):
    return 0, json.dumps({"type": "result", "is_error": False, "result": text})


def test_invoke_runs_a_lean_claude_call_with_the_configured_model(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_MODEL", "opus")
    run = FakeRun(ok('{"x": 1}'))
    teacher = ClaudeCodeTeacher(run=run)
    assert teacher.invoke([SystemMessage("SYS"), HumanMessage("label this")]) == '{"x": 1}'
    assert teacher.model == "claude-code/opus"
    cmd, kw = run.calls[0]
    assert cmd[cmd.index("--model") + 1] == "opus"
    assert cmd[cmd.index("--system-prompt") + 1] == "SYS"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert kw["input"] == "label this"


def test_retry_turns_are_rendered_into_one_prompt():
    run = FakeRun(ok("{}"))
    ClaudeCodeTeacher(run=run).invoke(
        [SystemMessage("SYS"), HumanMessage("label this"), AIMessage("bad"), HumanMessage("fix it")])
    assert run.calls[0][1]["input"] == "label this\n\nYour previous reply was:\nbad\n\nfix it"


@pytest.mark.parametrize("result", [(1, ""), (0, "not json"),
                                    (0, json.dumps({"is_error": True, "result": "limit"}))])
def test_cli_failures_raise(result):
    with pytest.raises(ClaudeCodeError):
        ClaudeCodeTeacher(run=FakeRun(result)).invoke([HumanMessage("x")])


def test_label_one_retries_a_cli_failure_like_an_api_error():
    text = "Member called about a card."
    teacher = ClaudeCodeTeacher(run=FakeRun((1, ""), ok("no json here")))
    with pytest.raises(data_gen.LabelError):
        data_gen.label_one(teacher, text, max_attempts=2, sleep=no_sleep)


def test_provider_claude_code_selects_the_cli_teacher(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "claude-code")
    assert isinstance(data_gen.teacher_llm(), ClaudeCodeTeacher)
