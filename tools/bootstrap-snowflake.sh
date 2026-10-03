#!/usr/bin/env bash
# One-command, repeatable Snowflake bootstrap for infra/azure/envs/snowflake.
#
#   tools/bootstrap-snowflake.sh --dry-run            # print every step, touch nothing
#   tools/bootstrap-snowflake.sh --org MYORG --account MYACCOUNT --admin-user TERRAFORM_ADMIN
#
# Steps: key pairs for the Terraform admin user and the dbt user (PKCS#8, unencrypted
# unless --passphrase, mode 600 under ~/.snowflake, never overwritten without
# --force-new-keys); the ALTER USER / DESC USER SQL to paste in Snowsight; the gitignored
# terraform.tfvars and backend.hcl; terraform init + plan, apply after an explicit y (or
# --yes); then the dbt connection values into the 1Password item snowflake-ccai (vault
# CMW), passed to `op` through a mode-600 temp template, never argv. Secrets are exported
# only inside this process. Rerunning is safe: existing keys and files are reused.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="$ROOT/infra/azure/envs/snowflake"
KEY_DIR="${SNOWFLAKE_KEY_DIR:-$HOME/.snowflake}"
OP_VAULT="${OP_VAULT:-CMW}"
OP_ITEM="${OP_ITEM:-snowflake-ccai}"

DRY_RUN=0 YES=0 FORCE_KEYS=0 USE_PASSPHRASE=0 SKIP_OP=0
ORG="" ACCOUNT="" ADMIN_USER=""

usage() {
  sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  cat <<'EOF'

Options:
  --org NAME            organization name (the part before the dash in org-account)
  --account NAME        account name (the part after the dash)
  --admin-user NAME     existing user Terraform signs in as (default TERRAFORM_ADMIN)
  --passphrase          encrypt both private keys; prompts once, stored in 1Password
  --force-new-keys      replace existing key files (the old keys stop working)
  --yes                 apply without the y/N prompt
  --skip-1password      do not write the 1Password item
  --dry-run             print each step and change nothing
  -h, --help            this help
EOF
}

say() { printf '==> %s\n' "$*"; }
die() { printf 'bootstrap-snowflake: %s\n' "$*" >&2; exit 1; }

# Run a command, or only show it under --dry-run.
run() {
  if ((DRY_RUN)); then printf '    [dry-run] %s\n' "$*"; else "$@"; fi
}

parse_args() {
  while (($#)); do
    case "$1" in
      --org) ORG="${2:?--org needs a value}"; shift 2 ;;
      --account) ACCOUNT="${2:?--account needs a value}"; shift 2 ;;
      --admin-user) ADMIN_USER="${2:?--admin-user needs a value}"; shift 2 ;;
      --passphrase) USE_PASSPHRASE=1; shift ;;
      --force-new-keys) FORCE_KEYS=1; shift ;;
      --yes) YES=1; shift ;;
      --skip-1password) SKIP_OP=1; shift ;;
      --dry-run) DRY_RUN=1; shift ;;
      -h | --help) usage; exit 0 ;;
      *) die "unknown option: $1 (see --help)" ;;
    esac
  done
  ADMIN_USER="${ADMIN_USER:-TERRAFORM_ADMIN}"
}

# Snowflake identifiers here are plain names; reject anything that would need quoting.
valid_name() { [[ "$1" =~ ^[A-Za-z_][A-Za-z0-9_$]*$ ]]; }

ask_names() {
  if [[ -z "$ORG" || -z "$ACCOUNT" ]]; then
    say "Organization and account names: run this in Snowsight if you do not know them"
    printf '    SELECT CURRENT_ORGANIZATION_NAME(), CURRENT_ACCOUNT_NAME();\n'
    if ((DRY_RUN)); then
      ORG="${ORG:-EXAMPLEORG}" ACCOUNT="${ACCOUNT:-EXAMPLEACCOUNT}"
      printf '    [dry-run] using %s-%s\n' "$ORG" "$ACCOUNT"
    else
      [[ -n "$ORG" ]] || read -r -p "    organization name: " ORG
      [[ -n "$ACCOUNT" ]] || read -r -p "    account name: " ACCOUNT
    fi
  fi
  for n in "$ORG" "$ACCOUNT" "$ADMIN_USER"; do
    valid_name "$n" || die "not a plain Snowflake name: '$n'"
  done
}

# gen_key <name>: <name>.p8 (PKCS#8 private) and <name>.pub, reused when present.
gen_key() {
  local p8="$KEY_DIR/$1.p8" pub="$KEY_DIR/$1.pub"
  if [[ -e "$p8" && $FORCE_KEYS -eq 0 ]]; then
    say "Reusing $p8 (pass --force-new-keys to replace it)"
    return
  fi
  say "Generating $p8 ($([[ $USE_PASSPHRASE -eq 1 ]] && echo encrypted || echo no passphrase))"
  ((DRY_RUN)) && { printf '    [dry-run] openssl genrsa 2048 | openssl pkcs8 -topk8 ... > %s; chmod 600\n' "$p8"; return; }
  mkdir -p "$KEY_DIR" && chmod 700 "$KEY_DIR"
  local tmp; tmp="$(mktemp "$KEY_DIR/.$1.XXXXXX")"
  if ((USE_PASSPHRASE)); then
    openssl genrsa 2048 2>/dev/null | openssl pkcs8 -topk8 -v2 aes-256-cbc -passout env:CCAI_KEY_PASSPHRASE -out "$tmp"
    openssl rsa -in "$tmp" -passin env:CCAI_KEY_PASSPHRASE -pubout -out "$pub" 2>/dev/null
  else
    openssl genrsa 2048 2>/dev/null | openssl pkcs8 -topk8 -nocrypt -out "$tmp"
    openssl rsa -in "$tmp" -pubout -out "$pub" 2>/dev/null
  fi
  chmod 600 "$tmp" && mv -f "$tmp" "$p8"
}

# One line of base64, no BEGIN/END lines: what RSA_PUBLIC_KEY and dbt_rsa_public_key take.
public_key_line() { grep -v -- '-----' "$1" | tr -d '\n'; }

# SHA256:<base64>, the form DESC USER shows as RSA_PUBLIC_KEY_FP.
public_key_fp() {
  printf 'SHA256:%s' "$(openssl rsa -pubin -in "$1" -outform DER 2>/dev/null | openssl dgst -sha256 -binary | openssl enc -base64)"
}

# alter_user_sql <user> <pubfile>: the block to paste in Snowsight as ACCOUNTADMIN.
alter_user_sql() {
  printf "ALTER USER %s SET RSA_PUBLIC_KEY='%s';\n" "$1" "$(public_key_line "$2")"
  printf 'DESC USER %s;  -- RSA_PUBLIC_KEY_FP must read %s\n' "$1" "$(public_key_fp "$2")"
}

render_tfvars() {
  cat <<EOF
# Written by tools/bootstrap-snowflake.sh. Gitignored. The dbt public key is passed as
# TF_VAR_dbt_rsa_public_key and the admin private key as SNOWFLAKE_PRIVATE_KEY, so
# neither is in this file.
organization_name = "$ORG"
account_name      = "$ACCOUNT"
admin_user        = "$ADMIN_USER"
EOF
}

# write_if_absent <path> <content>: never clobber a file the captain may have edited.
write_if_absent() {
  if [[ -e "$1" ]]; then say "Keeping existing $1"; return; fi
  say "Writing $1"
  ((DRY_RUN)) && { printf '%s\n' "$2" | sed 's/^/    [dry-run] | /'; return; }
  (umask 077 && printf '%s\n' "$2" > "$1")
}

backend_hcl() {
  local dev="$ENV_DIR/../dev/backend.hcl"
  if [[ -e "$dev" ]]; then sed 's/dev\.tfstate/snowflake.tfstate/' "$dev"
  else
    say "No envs/dev/backend.hcl; writing the example, fill it in before init" >&2
    cat "$ENV_DIR/backend.hcl.example"
  fi
}

# json_str <value>: a JSON string literal (backslash, quote, newline, CR, tab escaped).
json_str() {
  local s="${1//\\/\\\\}"
  s="${s//\"/\\\"}"; s="${s//$'\n'/\\n}"; s="${s//$'\r'/\\r}"; s="${s//$'\t'/\\t}"
  printf '"%s"' "$s"
}

# op_template <field=value>...: a 1Password item template; *KEY* / *PASSPHRASE* concealed.
op_template() {
  local first=1 kv k v type
  printf '{"title":%s,"category":"SECURE_NOTE","fields":[' "$(json_str "$OP_ITEM")"
  for kv in "$@"; do
    k="${kv%%=*}" v="${kv#*=}"
    case "$k" in *KEY* | *PASSPHRASE*) type=CONCEALED ;; *) type=STRING ;; esac
    ((first)) || printf ','
    first=0
    printf '{"id":%s,"label":%s,"type":"%s","value":%s}' "$(json_str "$k")" "$(json_str "$k")" "$type" "$(json_str "$v")"
  done
  printf ']}\n'
}

store_in_1password() {
  ((SKIP_OP)) && { say "Skipping 1Password (--skip-1password)"; return; }
  say "Storing the dbt connection in 1Password: $OP_VAULT/$OP_ITEM (values never printed)"
  ((DRY_RUN)) && { printf '    [dry-run] op item create|edit %s --vault %s --template <mode-600 temp file>\n' "$OP_ITEM" "$OP_VAULT"; return; }
  command -v op >/dev/null || die "op (1Password CLI) not found; rerun with --skip-1password or install it"
  op whoami >/dev/null 2>&1 || die "op is not signed in: run 'eval \$(op signin)' and rerun (keys and Terraform are reused)"
  local out; out="$(cd "$ENV_DIR" && terraform output -json dbt_env)"
  field() { sed -n "s/.*\"$1\": *\"\([^\"]*\)\".*/\1/p" <<<"$out"; }
  local fields=(
    "SNOWFLAKE_ACCOUNT=$(field SNOWFLAKE_ACCOUNT)" "SNOWFLAKE_USER=$(field SNOWFLAKE_USER)"
    "SNOWFLAKE_ROLE=$(field SNOWFLAKE_ROLE)" "SNOWFLAKE_WAREHOUSE=$(field SNOWFLAKE_WAREHOUSE)"
    "SNOWFLAKE_DATABASE=$(field SNOWFLAKE_DATABASE)"
    "SNOWFLAKE_PRIVATE_KEY_PEM=$(cat "$KEY_DIR/ccai_dbt_key.p8")"
  )
  ((USE_PASSPHRASE)) && fields+=("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE=$CCAI_KEY_PASSPHRASE")
  local tpl; tpl="$(umask 077 && mktemp)"
  trap 'rm -f "$tpl"' RETURN
  op_template "${fields[@]}" > "$tpl"
  if op item get "$OP_ITEM" --vault "$OP_VAULT" >/dev/null 2>&1; then
    op item edit "$OP_ITEM" --vault "$OP_VAULT" --template "$tpl" >/dev/null
  else
    op item create --vault "$OP_VAULT" --template "$tpl" >/dev/null
  fi
  say "1Password item $OP_ITEM updated"
}

terraform_steps() {
  say "terraform init / plan in $ENV_DIR"
  export TF_VAR_dbt_rsa_public_key SNOWFLAKE_PRIVATE_KEY
  if ((DRY_RUN)); then
    printf '    [dry-run] export TF_VAR_dbt_rsa_public_key=<dbt public key> SNOWFLAKE_PRIVATE_KEY=<admin private key>\n'
    run terraform -chdir="$ENV_DIR" init -input=false -backend-config=backend.hcl
    run terraform -chdir="$ENV_DIR" plan -input=false -out=tfplan
    printf '    [dry-run] apply tfplan after an explicit y (or --yes)\n'
    return
  fi
  TF_VAR_dbt_rsa_public_key="$(public_key_line "$KEY_DIR/ccai_dbt_key.pub")"
  SNOWFLAKE_PRIVATE_KEY="$(cat "$KEY_DIR/ccai_tf_admin_key.p8")"
  ((USE_PASSPHRASE)) && export SNOWFLAKE_PRIVATE_KEY_PASSPHRASE="$CCAI_KEY_PASSPHRASE"
  trap 'rm -f "$ENV_DIR/tfplan"' EXIT
  terraform -chdir="$ENV_DIR" init -input=false -backend-config=backend.hcl >/dev/null
  terraform -chdir="$ENV_DIR" plan -input=false -out=tfplan >/dev/null
  terraform -chdir="$ENV_DIR" show -no-color tfplan | grep -E '^(Plan:|No changes)|^  # ' || true
  if ((!YES)); then
    local reply; read -r -p "Apply this plan? [y/N] " reply
    [[ "$reply" == y || "$reply" == Y ]] || { say "Not applied. Rerun when ready."; exit 0; }
  fi
  terraform -chdir="$ENV_DIR" apply -input=false tfplan
}

main() {
  parse_args "$@"
  ((DRY_RUN)) && say "Dry run: nothing is created, written or applied"
  for tool in openssl terraform; do
    command -v "$tool" >/dev/null || ((DRY_RUN)) || die "$tool not found"
  done
  ask_names
  if ((USE_PASSPHRASE && !DRY_RUN)); then
    read -r -s -p "Key passphrase (stored in 1Password): " CCAI_KEY_PASSPHRASE; echo
    [[ -n "$CCAI_KEY_PASSPHRASE" ]] || die "empty passphrase; drop --passphrase for an unencrypted key"
    export CCAI_KEY_PASSPHRASE
  fi

  gen_key ccai_tf_admin_key
  gen_key ccai_dbt_key

  say "Paste in Snowsight as ACCOUNTADMIN (sets the Terraform admin user's public key):"
  if ((DRY_RUN)) && [[ ! -e "$KEY_DIR/ccai_tf_admin_key.pub" ]]; then
    printf "    ALTER USER %s SET RSA_PUBLIC_KEY='<one line of base64>';\n    DESC USER %s;\n" "$ADMIN_USER" "$ADMIN_USER"
  else
    alter_user_sql "$ADMIN_USER" "$KEY_DIR/ccai_tf_admin_key.pub" | sed 's/^/    /'
  fi
  printf '    %s also needs ACCOUNTADMIN, SYSADMIN and SECURITYADMIN granted. No network rule or policy is needed.\n' "$ADMIN_USER"
  if ((!DRY_RUN && !YES)); then read -r -p "Press Enter once that SQL has run... " _; fi

  write_if_absent "$ENV_DIR/terraform.tfvars" "$(render_tfvars)"
  write_if_absent "$ENV_DIR/backend.hcl" "$(backend_hcl)"
  if ((!DRY_RUN)) && grep -q 'xxxxxx' "$ENV_DIR/backend.hcl"; then
    die "fill in the placeholders in $ENV_DIR/backend.hcl (bootstrap outputs), then rerun"
  fi
  terraform_steps
  store_in_1password
  say "Done. dbt: SNOWFLAKE_PRIVATE_KEY_PATH=$KEY_DIR/ccai_dbt_key.p8"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
