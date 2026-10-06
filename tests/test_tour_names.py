"""Offline tests for tools/tour_names.py: a stub `terraform` on PATH, no Azure."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "tour_names.py"

# Fake names, built from placeholders; not a real deployment.
FAKE_DEV = {
    "postgres_fqdn": {"value": "psql-ccai-dev-zzzzzz.postgres.database.azure.com"},
    "adls_account_name": {"value": "stccaidevzzzzzz"},
    "acr_name": {"value": "acrccaidevyyyyyy"},
}
FAKE_BOOTSTRAP = {
    "state_storage_account_name": {"value": "stccaitfxxxxxx"},
    "key_vault_name": {"value": "kv-ccai-xxxxxx"},
}


def _run(tmp_path: Path, terraform_body: str | None) -> str:
    env = {**os.environ, "PATH": f"{tmp_path}:/usr/bin:/bin" if terraform_body else "/nonexistent"}
    if terraform_body:
        tf = tmp_path / "terraform"
        tf.write_text(f"#!/bin/bash\n{terraform_body}\n")
        tf.chmod(tf.stat().st_mode | stat.S_IEXEC)
    done = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, check=True)
    return done.stdout


def test_falls_back_to_masked_pattern_without_terraform(tmp_path):
    out = _run(tmp_path, None)
    assert "`psql-ccai-dev-<suffix>`" in out
    assert "`kv-ccai-<suffix>`" in out
    assert "masked pattern kept" in out


def test_falls_back_when_terraform_has_no_state(tmp_path):
    out = _run(tmp_path, "exit 1")
    assert "`stccaitf<suffix>`" in out
    assert out.count("masked pattern kept") == 2


def test_fills_live_names_from_terraform_output(tmp_path):
    body = (
        'case "$1" in *bootstrap) cat <<\'J\'\n' + json.dumps(FAKE_BOOTSTRAP) + "\nJ\n;;\n"
        "*) cat <<'J'\n" + json.dumps(FAKE_DEV) + "\nJ\n;; esac"
    )
    out = _run(tmp_path, body)
    for live in ("psql-ccai-dev-zzzzzz", "stccaidevzzzzzz", "acrccaidevyyyyyy", "stccaitfxxxxxx", "kv-ccai-xxxxxx"):
        assert f"`{live}`" in out
    assert "-<suffix>`" not in out and "dev<suffix>`" not in out and "tf<suffix>`" not in out
    assert "`log-ccai-dev`" in out  # fixed names pass through
