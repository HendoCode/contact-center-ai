#!/usr/bin/env bash
# Run `make evals-live` with its keys resolved from 1Password, so no .env holds them.
#
#   tools/evals-live-run.sh [--dry-run] [--no-check] -- <command...>
#
# The committed template tools/op/evals.env names, for each key, the environment variable
# holding YOUR 1Password reference (LANGSMITH_KEY_REF, EVALS_OPENAI_KEY_REF); nothing in the
# repo names a vault. The rendered references go to `op run`, which resolves them into this
# process's children only. The judge runs through OpenRouter: LLM_BASE_URL defaults to
# https://openrouter.ai/api/v1, EVAL_JUDGE_PROVIDER to openai and EVAL_JUDGE_MODEL to
# anthropic/claude-opus-5.5; set any of them in the shell to override. Prerequisites are
# checked up front, one clear message each: both references set, op installed and signed
# in, each reference readable, then (python -m evals.preflight) the agent and judge models
# differ, Postgres reachable and seeded, Ollama reachable when it embeds. An estimated cost
# range is printed before the run. --no-check skips the preflight checks (not the estimate).
# --dry-run prints the variable NAMES and the plan, never values, and runs nothing.

set -euo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
ROOT="$(cd "$(dirname "$SELF")/.." && pwd)"
TEMPLATE="$ROOT/tools/op/evals.env"
UV="${UV:-uv}"

: "${LLM_BASE_URL:=https://openrouter.ai/api/v1}"
: "${EVAL_JUDGE_PROVIDER:=openai}"
: "${EVAL_JUDGE_MODEL:=anthropic/claude-opus-5.5}"
export LLM_BASE_URL EVAL_JUDGE_PROVIDER EVAL_JUDGE_MODEL

DRY_RUN=0 CHECK=1 INNER=0 CMD=()

CLEANUP=()
cleanup() { if ((${#CLEANUP[@]})); then rm -f -- "${CLEANUP[@]}"; fi; }
trap cleanup EXIT

die() { printf 'evals-live: %s\n' "$*" >&2; exit 1; }
say() { printf '==> %s\n' "$*"; }

parse_args() {
  while (($#)); do
    case "$1" in
      --dry-run) DRY_RUN=1; shift ;;
      --no-check) CHECK=0; shift ;;
      --inner) INNER=1; shift ;; # internal: the stage that runs under `op run`
      -h | --help) sed -n '2,17p' "$SELF" | sed 's/^# \{0,1\}//'; exit 0 ;;
      --) shift; CMD=("$@"); break ;;
      *) die "unknown argument: $1 (usage: [--dry-run] [--no-check] -- <command...>)" ;;
    esac
  done
  ((${#CMD[@]})) || die "no command after --"
}

# Template lines NAME=${REF_VAR}, as "NAME REF_VAR" pairs (comments and blanks skipped).
template_pairs() { sed -n 's/^\([A-Z_][A-Z0-9_]*\)=\${\([A-Z_][A-Z0-9_]*\)}[[:space:]]*$/\1 \2/p' "$1"; }

# Each reference variable that is unset or does not hold an op:// reference.
unset_refs() {
  local name ref
  while read -r name ref; do
    [[ "${!ref:-}" == op://* ]] || printf '%s (for %s)\n' "$ref" "$name"
  done < <(template_pairs "$TEMPLATE")
}

# The template with each ${REF_VAR} replaced by its reference, for `op run --env-file`.
render() {
  local name ref
  while read -r name ref; do printf '%s=%s\n' "$name" "${!ref}"; done < <(template_pairs "$TEMPLATE")
}

# The value of option $1 (e.g. --limit) the command passes to evals.run, if any. Only the
# words after `evals.run` count, so uv's own `--group agent` is never read as an eval group.
cmd_opt() {
  local i start=0
  for ((i = 0; i < ${#CMD[@]}; i++)); do [[ "${CMD[i]}" == evals.run ]] && start=$((i + 1)); done
  for ((i = start; i < ${#CMD[@]}; i++)); do
    case "${CMD[i]}" in
      "$1") printf '%s\n' "${CMD[i + 1]:-}"; return ;;
      "$1"=*) printf '%s\n' "${CMD[i]#"$1"=}"; return ;;
    esac
  done
}

preflight() {
  local opt value args=()
  for opt in --limit --dataset --group; do
    value="$(cmd_opt "$opt")"
    [[ -n "$value" ]] && args+=("$opt" "$value")
  done
  (cd "$ROOT" && "$UV" run -q --group agent python -m evals.preflight "${args[@]}" "$@")
}

check_outer() {
  local missing
  missing="$(unset_refs)"
  [[ -z "$missing" ]] || die "set your 1Password reference(s), e.g. export NAME='op://<your vault>/<item>/<field>': $(paste -sd" " <<<"$missing")"
  command -v op >/dev/null || die "the 1Password CLI (op) is not installed: https://developer.1password.com/docs/cli/get-started/"
  op whoami >/dev/null 2>&1 || die "op is not signed in: run 'eval \$(op signin)' and retry"
  local name ref
  while read -r name ref; do
    op read "${!ref}" >/dev/null 2>&1 || die "1Password could not read $ref for $name: check the vault, item and field in that reference"
  done < <(template_pairs "$TEMPLATE")
}

inner() {
  local name ref missing=()
  while read -r name ref; do [[ -n "${!name:-}" ]] || missing+=("$name"); done < <(template_pairs "$TEMPLATE")
  ((${#missing[@]} == 0)) || die "missing or empty after resolving 1Password: ${missing[*]}"
  if ((CHECK)); then preflight; else preflight --estimate-only; fi
  say "Running: ${CMD[*]}"
  local rc=0
  (cd "$ROOT" && "${CMD[@]}") || rc=$?
  return "$rc"
}

dry_run() {
  local name ref names=() refs=()
  while read -r name ref; do names+=("$name"); refs+=("$ref"); done < <(template_pairs "$TEMPLATE")
  say "Dry run: nothing is resolved or checked, and the command does not run"
  say "From 1Password (names only): ${names[*]}, via your references in ${refs[*]}"
  local missing; missing="$(unset_refs)"
  [[ -z "$missing" ]] || say "Not set yet: $(paste -sd" " <<<"$missing")"
  say "Judge: EVAL_JUDGE_PROVIDER=$EVAL_JUDGE_PROVIDER EVAL_JUDGE_MODEL=$EVAL_JUDGE_MODEL LLM_BASE_URL=$LLM_BASE_URL"
  say "Plan: check op, read each reference, then $( ((CHECK)) && echo "check models differ, Postgres seeded, Ollama if it embeds, " )estimate cost, run"
  preflight --estimate-only || true
  say "Would run: ${CMD[*]}"
}

main() {
  parse_args "$@"
  [[ -f "$TEMPLATE" ]] || die "template $TEMPLATE not found"
  if ((INNER)); then inner; return; fi
  if ((DRY_RUN)); then dry_run; return 0; fi
  check_outer
  local env_file
  env_file="$(umask 077 && mktemp "${TMPDIR:-/tmp}/ccai-evals-env.XXXXXX")"
  CLEANUP+=("$env_file")
  render > "$env_file"
  local flags=(--inner)
  ((CHECK)) || flags+=(--no-check)
  op run --env-file "$env_file" -- "$SELF" "${flags[@]}" -- "${CMD[@]}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
