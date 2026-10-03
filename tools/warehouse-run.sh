#!/usr/bin/env bash
# Run a warehouse command with its SNOWFLAKE_* / DATABRICKS_* settings resolved from
# 1Password, so no .env is ever populated. Backs `make load-snowflake`, `make
# load-databricks` and `make dbt-build WAREHOUSE=...`.
#
#   tools/warehouse-run.sh [--dry-run] [--no-check] <snowflake|databricks> -- <command...>
#
# The committed templates tools/op/<warehouse>.env hold op:// references only; `op run`
# resolves them into this process's children. For Snowflake the private key is written
# to a mode-600 temp file only for the run (SNOWFLAKE_PRIVATE_KEY_PATH) and removed on
# exit. Prerequisites are checked up front, one clear message each: op installed and
# signed in, the 1Password item present, the uv dependency groups installed, and the
# warehouse answering SELECT 1 (--no-check skips that last one). --dry-run prints what
# would run and the variable NAMES it resolved, never values, and runs nothing.

set -euo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
ROOT="$(cd "$(dirname "$SELF")/.." && pwd)"
OP_VAULT="${OP_VAULT:-CMW}"
UV="${UV:-uv}"

DRY_RUN=0 CHECK=1 INNER=0 WH="" CMD=()

# Temp files to remove on exit: one global list, one EXIT trap (never a trap that names a
# function-local variable, which breaks under set -u once the function has returned).
CLEANUP=()
cleanup() { if ((${#CLEANUP[@]})); then rm -f -- "${CLEANUP[@]}"; fi; }
trap cleanup EXIT

die() { printf 'warehouse-run: %s\n' "$*" >&2; exit 1; }
say() { printf '==> %s\n' "$*"; }

parse_args() {
  while (($#)); do
    case "$1" in
      --dry-run) DRY_RUN=1; shift ;;
      --no-check) CHECK=0; shift ;;
      --inner) INNER=1; shift ;; # internal: the stage that runs under `op run`
      -h | --help) sed -n '2,14p' "$SELF" | sed 's/^# \{0,1\}//'; exit 0 ;;
      snowflake | databricks) WH="$1"; shift ;;
      --) shift; CMD=("$@"); break ;;
      *) die "unknown argument: $1 (usage: [--dry-run] [--no-check] <snowflake|databricks> -- <command...>)" ;;
    esac
  done
  [[ -n "$WH" ]] || die "name a warehouse: snowflake or databricks"
  ((${#CMD[@]})) || die "no command after --"
}

template() { printf '%s/tools/op/%s.env\n' "$ROOT" "$WH"; }
item() { printf '%s-ccai\n' "$WH"; }

# Variable names a template sets (comments and blanks skipped).
template_names() { sed -n 's/^\([A-Z_][A-Z0-9_]*\)=.*/\1/p' "$1"; }

# The variables the command needs, after the Snowflake key is materialized.
required_names() {
  if [[ "$WH" == snowflake ]]; then
    template_names "$(template)" | grep -v '^SNOWFLAKE_PRIVATE_KEY_PEM$'
    echo SNOWFLAKE_PRIVATE_KEY_PATH
  else
    template_names "$(template)"
  fi
}

uv_import() {
  case "$WH" in snowflake) echo snowflake.connector ;; databricks) echo databricks.sql ;; esac
}

check_outer() {
  command -v op >/dev/null || die "the 1Password CLI (op) is not installed: https://developer.1password.com/docs/cli/get-started/"
  op whoami >/dev/null 2>&1 || die "op is not signed in: run 'eval \$(op signin)' and retry"
  op item get "$(item)" --vault "$OP_VAULT" >/dev/null 2>&1 \
    || die "1Password item $OP_VAULT/$(item) not found or not readable: run tools/bootstrap-$WH.sh, or check the vault"
  (cd "$ROOT" && "$UV" run -q --group dbt --group "$WH" python -c "import $(uv_import)") >/dev/null 2>&1 \
    || die "the $WH dependencies are not installed: run 'uv sync --group dbt --group $WH'"
}

# Materialize the Snowflake key for this run only; the PEM never reaches the command.
materialize_key() {
  [[ -n "${SNOWFLAKE_PRIVATE_KEY_PEM:-}" ]] || die "SNOWFLAKE_PRIVATE_KEY_PEM is empty: check the 1Password item $(item)"
  local key; key="$(umask 077 && mktemp "${TMPDIR:-/tmp}/ccai-snowflake-key.XXXXXX")"
  CLEANUP+=("$key")
  printf '%s\n' "$SNOWFLAKE_PRIVATE_KEY_PEM" > "$key"
  export SNOWFLAKE_PRIVATE_KEY_PATH="$key"
  unset SNOWFLAKE_PRIVATE_KEY_PEM
}

check_reachable() {
  local probe
  if [[ "$WH" == snowflake ]]; then
    probe='import os, snowflake.connector as s
c = s.connect(account=os.environ["SNOWFLAKE_ACCOUNT"], user=os.environ["SNOWFLAKE_USER"],
              private_key_file=os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"],
              private_key_file_pwd=os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE") or None,
              role=os.environ["SNOWFLAKE_ROLE"], warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
              login_timeout=30)
c.cursor().execute("SELECT 1"); c.close()'
  else
    probe='import os
from databricks import sql
c = sql.connect(server_hostname=os.environ["DATABRICKS_HOST"].removeprefix("https://").rstrip("/"),
                http_path=os.environ["DATABRICKS_HTTP_PATH"], access_token=os.environ["DATABRICKS_TOKEN"])
c.cursor().execute("SELECT 1"); c.close()'
  fi
  local err
  err="$(cd "$ROOT" && "$UV" run -q --group dbt --group "$WH" python -c "$probe" 2>&1 >/dev/null)" \
    || die "the $WH warehouse did not answer SELECT 1: $(tail -n 1 <<<"$err")"
}

inner() {
  [[ "$WH" == snowflake ]] && ((!DRY_RUN)) && materialize_key
  local n missing=() resolved=()
  while IFS= read -r n; do
    if [[ "$n" == SNOWFLAKE_PRIVATE_KEY_PATH ]] && ((DRY_RUN)); then
      [[ -n "${SNOWFLAKE_PRIVATE_KEY_PEM:-}" ]] && resolved+=("SNOWFLAKE_PRIVATE_KEY_PEM (written to a temp key file at run time)") || missing+=(SNOWFLAKE_PRIVATE_KEY_PEM)
    elif [[ -n "${!n:-}" ]]; then resolved+=("$n")
    else missing+=("$n")
    fi
  done < <(required_names)
  if ((DRY_RUN)); then
    say "Resolved from 1Password (names only): ${resolved[*]:-none}"
    ((${#missing[@]})) && say "Missing or empty: ${missing[*]}"
    say "Would run: ${CMD[*]}"
    return 0
  fi
  ((${#missing[@]} == 0)) || die "missing or empty after resolving 1Password: ${missing[*]}"
  ((CHECK)) && check_reachable
  say "Running: ${CMD[*]}"
  local rc=0
  (cd "$ROOT" && "${CMD[@]}") || rc=$?
  return "$rc"
}

main() {
  parse_args "$@"
  [[ -f "$(template)" ]] || die "template $(template) not found"
  if ((INNER)); then inner; return; fi

  local flags=(--inner)
  ((DRY_RUN)) && flags+=(--dry-run)
  ((CHECK)) || flags+=(--no-check)
  if ((DRY_RUN)); then
    say "Dry run for $WH: nothing connects and the command does not run"
    say "Template: tools/op/$WH.env ($(template_names "$(template)" | tr '\n' ' '))"
    if command -v op >/dev/null && op whoami >/dev/null 2>&1; then
      op run --env-file "$(template)" -- "$SELF" "${flags[@]}" "$WH" -- "${CMD[@]}"
    else
      say "Not resolved: op is not installed or not signed in"
      say "Would run: ${CMD[*]}"
    fi
    return 0
  fi
  check_outer
  op run --env-file "$(template)" -- "$SELF" "${flags[@]}" "$WH" -- "${CMD[@]}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
