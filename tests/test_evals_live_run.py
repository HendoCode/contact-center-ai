"""Offline tests for tools/evals-live-run.sh: no 1Password, OpenRouter, LangSmith or stack.

A fake `op` resolves the references `op run --env-file` receives from FAKE_OP_VALUES
(JSON keyed by op:// reference) and runs the command; a fake `uv` stands in for
`python -m evals.preflight` and records its arguments.
"""

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "evals-live-run.sh"

LS_REF = "op://your-vault/langsmith/credential"
OR_REF = "op://your-vault/openrouter/credential"
VALUES = {LS_REF: "lsv2-test-key", OR_REF: "sk-or-test-key"}

FAKE_OP = r"""#!/usr/bin/env python3
import json, os, re, sys
a = sys.argv[1:]
values = json.loads(os.environ.get("FAKE_OP_VALUES", "{}"))
if a[0] == "whoami":
    sys.exit(0 if os.environ.get("OP_SIGNED_IN") else 1)
if a[0] == "read":
    if a[1] in values:
        print(values[a[1]]); sys.exit(0)
    sys.exit(1)
if a[0] == "run":
    path = a[a.index("--env-file") + 1]
    open(os.environ["ENV_FILE_COPY"], "w").write(path + "\n" + open(path).read())
    env = dict(os.environ)
    for line in open(path):
        m = re.match(r"([A-Z_][A-Z0-9_]*)=(op://.+)$", line.strip())
        if m:
            env[m.group(1)] = values.get(m.group(2), "")
    cmd = a[a.index("--") + 1:]
    os.execvpe(cmd[0], cmd, env)
sys.exit(2)
"""

FAKE_UV = """#!/usr/bin/env bash
# uv stand-in for `uv run ... python -m evals.preflight`: records its args; PREFLIGHT_FAIL=1 fails.
printf '%s\\n' "$*" >> "$UV_LOG"
[[ "${PREFLIGHT_FAIL:-}" == 1 && "$*" != *--estimate-only* ]] && { echo "evals-live: Postgres is not reachable" >&2; exit 1; }
exit 0
"""


def run(args, tmp_path, refs=True, values=VALUES, **env):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name, body in (("op", FAKE_OP), ("uv", FAKE_UV)):
        (bindir / name).write_text(body)
        (bindir / name).chmod(0o755)
    full = {k: v for k, v in os.environ.items()
            if k not in ("LANGSMITH_KEY_REF", "EVALS_OPENAI_KEY_REF", "EVAL_JUDGE_MODEL",
                         "EVAL_JUDGE_PROVIDER", "LLM_BASE_URL", "LANGSMITH_API_KEY", "OPENAI_API_KEY")}
    full.update({"PATH": f"{bindir}:{os.environ['PATH']}", "UV": str(bindir / "uv"),
                 "TMPDIR": str(tmp_path), "OP_SIGNED_IN": "1", "FAKE_OP_VALUES": json.dumps(values),
                 "UV_LOG": str(tmp_path / "uv.log"), "ENV_FILE_COPY": str(tmp_path / "envfile")})
    if refs:
        full.update({"LANGSMITH_KEY_REF": LS_REF, "EVALS_OPENAI_KEY_REF": OR_REF})
    full.update(env)
    return subprocess.run([str(SCRIPT), *args], capture_output=True, text=True, env=full)


def dump_env_cmd(tmp_path):
    out = tmp_path / "seen.json"
    names = ["LANGSMITH_API_KEY", "OPENAI_API_KEY", "EVAL_JUDGE_PROVIDER", "EVAL_JUDGE_MODEL", "LLM_BASE_URL"]
    return out, ["python3", "-c", f"import json,os; json.dump({{n: os.environ.get(n) for n in {names!r}}},"
                                  f" open({str(out)!r}, 'w'))"]


def test_template_renders_your_references_and_keys_reach_only_the_command(tmp_path):
    out, cmd = dump_env_cmd(tmp_path)
    res = run(["--", *cmd], tmp_path)
    assert res.returncode == 0, res.stderr
    seen = json.loads(out.read_text())
    assert seen == {"LANGSMITH_API_KEY": "lsv2-test-key", "OPENAI_API_KEY": "sk-or-test-key",
                    "EVAL_JUDGE_PROVIDER": "openai", "EVAL_JUDGE_MODEL": "anthropic/claude-opus-5.5",
                    "LLM_BASE_URL": "https://openrouter.ai/api/v1"}
    path, *lines = (tmp_path / "envfile").read_text().splitlines()
    assert lines == [f"LANGSMITH_API_KEY={LS_REF}", f"OPENAI_API_KEY={OR_REF}"]
    assert not Path(path).exists(), "rendered env file must be removed after the run"
    assert "sk-or-test-key" not in res.stdout + res.stderr


def test_committed_template_names_no_vault():
    body = (ROOT / "tools" / "op" / "evals.env").read_text()
    settings = [line for line in body.splitlines() if line and not line.startswith("#")]
    assert settings == ["LANGSMITH_API_KEY=${LANGSMITH_KEY_REF}", "OPENAI_API_KEY=${EVALS_OPENAI_KEY_REF}"]
    assert "op://" not in "\n".join(settings)


def test_judge_settings_are_overridable(tmp_path):
    out, cmd = dump_env_cmd(tmp_path)
    res = run(["--", *cmd], tmp_path, EVAL_JUDGE_MODEL="openai/gpt-5.5", LLM_BASE_URL="http://gw/v1")
    assert res.returncode == 0, res.stderr
    seen = json.loads(out.read_text())
    assert seen["EVAL_JUDGE_MODEL"] == "openai/gpt-5.5"
    assert seen["LLM_BASE_URL"] == "http://gw/v1"


def test_missing_references_are_named(tmp_path):
    res = run(["--", "true"], tmp_path, refs=False)
    assert res.returncode == 1
    assert "LANGSMITH_KEY_REF (for LANGSMITH_API_KEY)" in res.stderr
    assert "EVALS_OPENAI_KEY_REF (for OPENAI_API_KEY)" in res.stderr


def test_a_reference_that_is_not_op_is_refused(tmp_path):
    res = run(["--", "true"], tmp_path, EVALS_OPENAI_KEY_REF="sk-or-pasted-key")
    assert res.returncode == 1
    assert "EVALS_OPENAI_KEY_REF" in res.stderr
    assert "sk-or-pasted-key" not in res.stderr


def test_op_not_signed_in(tmp_path):
    res = run(["--", "true"], tmp_path, OP_SIGNED_IN="")
    assert res.returncode == 1
    assert "op is not signed in" in res.stderr


def test_unreadable_reference_names_the_variable(tmp_path):
    res = run(["--", "true"], tmp_path, values={LS_REF: "lsv2-test-key"})
    assert res.returncode == 1
    assert "could not read EVALS_OPENAI_KEY_REF for OPENAI_API_KEY" in res.stderr


def test_preflight_failure_stops_before_the_command(tmp_path):
    marker = tmp_path / "ran"
    res = run(["--", "touch", str(marker)], tmp_path, PREFLIGHT_FAIL="1")
    assert res.returncode == 1
    assert "Postgres is not reachable" in res.stderr
    assert not marker.exists()


def test_limit_reaches_the_preflight_estimate(tmp_path):
    res = run(["--", "true", "--live", "--limit", "5"], tmp_path)
    assert res.returncode == 0, res.stderr
    assert "-m evals.preflight --limit 5" in (tmp_path / "uv.log").read_text()


def test_no_check_still_estimates(tmp_path):
    res = run(["--no-check", "--", "true", "--limit=3"], tmp_path)
    assert res.returncode == 0, res.stderr
    assert "-m evals.preflight --limit 3 --estimate-only" in (tmp_path / "uv.log").read_text()


def test_dry_run_prints_names_and_plan_never_values(tmp_path):
    res = run(["--dry-run", "--", "true", "--limit", "5"], tmp_path)
    assert res.returncode == 0, res.stderr
    assert "LANGSMITH_API_KEY OPENAI_API_KEY" in res.stdout
    assert "Would run: true --limit 5" in res.stdout
    for secret in ("lsv2-test-key", "sk-or-test-key", LS_REF, OR_REF):
        assert secret not in res.stdout + res.stderr
    assert not (tmp_path / "envfile").exists(), "dry run must not call op run"
    assert "--estimate-only" in (tmp_path / "uv.log").read_text()
