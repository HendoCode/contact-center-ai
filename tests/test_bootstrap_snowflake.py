"""Offline tests for tools/bootstrap-snowflake.sh: no Snowflake, Azure, Terraform or 1Password.

Functions are exercised by sourcing the script (its `main` runs only when executed).
"""

import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "bootstrap-snowflake.sh"
ENV_DIR = SCRIPT.parents[1] / "infra" / "azure" / "envs" / "snowflake"

# PEM markers built at runtime so no key-header literal sits in the source (secret scanners).
_PEM = "-----{} {}KEY-----"
BEGIN_PKCS8 = _PEM.format("BEGIN", "PRIVATE ")
BEGIN_ENCRYPTED = _PEM.format("BEGIN", "ENCRYPTED PRIVATE ")
END_PKCS8 = _PEM.format("END", "PRIVATE ")

pytestmark = pytest.mark.skipif(not shutil.which("openssl"), reason="needs openssl")


def sh(body: str, tmp_path: Path, **env) -> subprocess.CompletedProcess:
    """Run `body` in bash after sourcing the script, with keys under tmp_path."""
    full_env = {**os.environ, "SNOWFLAKE_KEY_DIR": str(tmp_path / "keys"), **env}
    return subprocess.run(["bash", "-c", f'source "{SCRIPT}"; {body}'], capture_output=True,
                          text=True, env=full_env)


def test_dry_run_prints_every_step_and_touches_nothing(tmp_path):
    before = sorted(p.name for p in ENV_DIR.iterdir())
    out = subprocess.run([str(SCRIPT), "--dry-run"], capture_output=True, text=True,
                         env={**os.environ, "SNOWFLAKE_KEY_DIR": str(tmp_path / "keys")})
    assert out.returncode == 0, out.stderr
    for step in ("SELECT CURRENT_ORGANIZATION_NAME(), CURRENT_ACCOUNT_NAME();",
                 "ccai_tf_admin_key.p8 (no passphrase)", "ccai_dbt_key.p8 (no passphrase)",
                 "ALTER USER TERRAFORM_ADMIN SET RSA_PUBLIC_KEY=", "DESC USER TERRAFORM_ADMIN;",
                 'organization_name = "EXAMPLEORG"', "init -input=false", "plan -input=false",
                 "after an explicit y", "op item create|edit snowflake-ccai --vault CMW"):
        assert step in out.stdout, step
    assert not (tmp_path / "keys").exists()
    assert sorted(p.name for p in ENV_DIR.iterdir()) == before


def test_bad_arguments_and_names_are_refused(tmp_path):
    assert subprocess.run([str(SCRIPT), "--nope"], capture_output=True).returncode != 0
    res = sh("parse_args --org 'bad-org' --account ACCT; ask_names", tmp_path)
    assert res.returncode != 0 and "not a plain Snowflake name" in res.stderr


def test_keys_are_pkcs8_mode_600_and_never_overwritten(tmp_path):
    res = sh("gen_key k; gen_key k", tmp_path)
    assert res.returncode == 0, res.stderr
    p8 = tmp_path / "keys" / "k.p8"
    assert p8.read_text().startswith(BEGIN_PKCS8)  # PKCS#8, unencrypted
    assert stat.S_IMODE(p8.stat().st_mode) == 0o600
    assert "Reusing" in res.stdout
    first = p8.read_text()
    sh("gen_key k", tmp_path)
    assert p8.read_text() == first
    sh("FORCE_KEYS=1; gen_key k", tmp_path)
    assert p8.read_text() != first


def test_passphrase_keys_are_encrypted(tmp_path):
    res = sh("USE_PASSPHRASE=1; gen_key e", tmp_path, CCAI_KEY_PASSPHRASE="s3cret-test")
    assert res.returncode == 0, res.stderr
    assert (tmp_path / "keys" / "e.p8").read_text().startswith(BEGIN_ENCRYPTED)
    assert "s3cret-test" not in res.stdout + res.stderr


def test_alter_user_sql_is_one_line_with_the_matching_fingerprint(tmp_path):
    res = sh("gen_key a >/dev/null; alter_user_sql TERRAFORM_ADMIN \"$KEY_DIR/a.pub\"", tmp_path)
    alter, desc = res.stdout.splitlines()
    pub = (tmp_path / "keys" / "a.pub").read_text()
    body = "".join(line for line in pub.splitlines() if "-----" not in line)
    assert alter == f"ALTER USER TERRAFORM_ADMIN SET RSA_PUBLIC_KEY='{body}';"
    der = subprocess.run(["openssl", "rsa", "-pubin", "-in", str(tmp_path / "keys" / "a.pub"),
                          "-outform", "DER"], capture_output=True).stdout
    fp = "SHA256:" + base64.b64encode(hashlib.sha256(der).digest()).decode()
    assert desc == f"DESC USER TERRAFORM_ADMIN;  -- RSA_PUBLIC_KEY_FP must read {fp}"


def test_tfvars_holds_names_only(tmp_path):
    res = sh("ORG=MYORG ACCOUNT=MYACCT ADMIN_USER=TF_ADMIN; render_tfvars", tmp_path)
    lines = [ln for ln in res.stdout.splitlines() if not ln.startswith("#")]
    assert lines == ['organization_name = "MYORG"', 'account_name      = "MYACCT"',
                     'admin_user        = "TF_ADMIN"']


def test_op_template_is_valid_json_and_conceals_secrets(tmp_path):
    pem = f"{BEGIN_PKCS8}\nAB\"C\\D\n{END_PKCS8}"
    res = sh('op_template "SNOWFLAKE_USER=CCAI_DBT" "SNOWFLAKE_PRIVATE_KEY_PEM=$PEM"', tmp_path, PEM=pem)
    item = json.loads(res.stdout)
    assert item["title"] == "snowflake-ccai"
    fields = {f["label"]: f for f in item["fields"]}
    assert fields["SNOWFLAKE_USER"] == {"id": "SNOWFLAKE_USER", "label": "SNOWFLAKE_USER",
                                        "type": "STRING", "value": "CCAI_DBT"}
    assert fields["SNOWFLAKE_PRIVATE_KEY_PEM"]["type"] == "CONCEALED"
    assert fields["SNOWFLAKE_PRIVATE_KEY_PEM"]["value"] == pem
