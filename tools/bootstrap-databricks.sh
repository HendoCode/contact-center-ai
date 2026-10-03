#!/usr/bin/env bash
# One-command, repeatable Databricks bootstrap for dbt and the S2 loaders.
#
#   tools/bootstrap-databricks.sh --dry-run                     # print every step, touch nothing
#   tools/bootstrap-databricks.sh --from-terraform              # the envs/dev workspace (enable_databricks)
#   tools/bootstrap-databricks.sh --host dbc-1234.cloud.databricks.com   # any workspace, incl. Free Edition
#
# Steps: read a personal access token without echo (prompt, or stdin when piped; never
# argv); verify it with GET /api/2.0/preview/scim/v2/Me; find the SQL warehouse named
# --warehouse-name or create a serverless 2X-Small one, and record its HTTP path; create the
# catalog (when the workspace permits), the raw and marts schemas and the loaders' volume
# with CREATE ... IF NOT EXISTS through the SQL statements API, printing the grant needed
# when a step is denied; then store DATABRICKS_HOST, DATABRICKS_HTTP_PATH, DATABRICKS_TOKEN
# and DATABRICKS_CATALOG in the 1Password item databricks-ccai (vault CMW) through a
# mode-600 temp template, never argv. Rerunning is safe: everything is find-or-create.

# Backticks in the SQL below are Databricks identifier quotes, not command substitution.
# shellcheck disable=SC2016

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OP_VAULT="${OP_VAULT:-CMW}"
OP_ITEM="${OP_ITEM:-databricks-ccai}"
VOLUME="ccai_loader_stage" # olap/dbt/loaders/databricks_loader.py

# Temp files to remove on exit. One global list and one EXIT trap: a trap that names a
# function-local variable fails under set -u once that function has returned.
CLEANUP=()
cleanup() { if ((${#CLEANUP[@]})); then rm -f -- "${CLEANUP[@]}"; fi; }
trap cleanup EXIT

DRY_RUN=0 SKIP_OP=0 FROM_TF=0 CATALOG_GIVEN=0 WAREHOUSE_GIVEN=0
HOST="" CATALOG="ccai" RAW_SCHEMA="raw" MARTS_SCHEMA="marts" WAREHOUSE_NAME="ccai-sql"
TOKEN="" AUTH_FILE="" WAREHOUSE_ID="" HTTP_PATH="" ME=""

usage() {
  sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  cat <<'EOF'

Options:
  --host HOST            workspace host or URL (normalized to a bare hostname)
  --from-terraform       take the host from envs/dev's databricks_workspace_url output
  --catalog NAME         catalog to use or create (default ccai; Free Edition: see README)
  --raw-schema NAME      loaders' schema (default raw; DATABRICKS_RAW_SCHEMA)
  --marts-schema NAME    dbt's schema (default marts; DATABRICKS_SCHEMA)
  --warehouse-name NAME  SQL warehouse to find or create (default ccai-sql; spaces are fine)
  --warehouse-id ID      use this warehouse id, skipping the name lookup
  --skip-1password       do not write the 1Password item
  --dry-run              print each step and change nothing
  -h, --help             this help
EOF
}

say() { printf '==> %s\n' "$*"; }
die() { printf 'bootstrap-databricks: %s\n' "$*" >&2; exit 1; }

parse_args() {
  while (($#)); do
    case "$1" in
      --host) HOST="${2:?--host needs a value}"; shift 2 ;;
      --from-terraform) FROM_TF=1; shift ;;
      --catalog) CATALOG="${2:?--catalog needs a value}"; CATALOG_GIVEN=1; shift 2 ;;
      --raw-schema) RAW_SCHEMA="${2:?--raw-schema needs a value}"; shift 2 ;;
      --marts-schema) MARTS_SCHEMA="${2:?--marts-schema needs a value}"; shift 2 ;;
      --warehouse-name) WAREHOUSE_NAME="${2:?--warehouse-name needs a value}"; WAREHOUSE_GIVEN=1; shift 2 ;;
      --warehouse-id) WAREHOUSE_ID="${2:?--warehouse-id needs a value}"; WAREHOUSE_GIVEN=1; shift 2 ;;
      --skip-1password) SKIP_OP=1; shift ;;
      --dry-run) DRY_RUN=1; shift ;;
      -h | --help) usage; exit 0 ;;
      *) die "unknown option: $1 (see --help)" ;;
    esac
  done
  for n in "$CATALOG" "$RAW_SCHEMA" "$MARTS_SCHEMA"; do
    valid_ident "$n" || die "not a plain identifier: '$n' (letters, digits, underscore)"
  done
  # The name is only looked up and JSON-encoded, so any printable name (spaces included) is fine.
  [[ -n "$WAREHOUSE_NAME" && "$WAREHOUSE_NAME" != *[[:cntrl:]]* ]] || die "warehouse name must be printable text"
  [[ -z "$WAREHOUSE_ID" || "$WAREHOUSE_ID" =~ ^[A-Za-z0-9]+$ ]] || die "warehouse id: letters and digits only"
}

# Same rule as the loaders' check_ident: unquoted-safe Unity Catalog names.
valid_ident() { [[ "$1" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; }

# normalize_host <host-or-url>: bare lower-case hostname, no scheme, path or trailing slash.
normalize_host() {
  local h="${1//[[:space:]]/}"
  h="$(tr '[:upper:]' '[:lower:]' <<<"$h")"
  [[ "$h" == http://* ]] && { echo "use https, not http: $1" >&2; return 1; }
  h="${h#https://}"
  h="${h%%[/?#]*}"
  [[ "$h" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && "$h" == *.* ]] || { echo "not a workspace host: $1" >&2; return 1; }
  printf '%s\n' "$h"
}

# The statements run in order; each is idempotent.
bootstrap_sql() {
  printf 'CREATE CATALOG IF NOT EXISTS `%s`\n' "$CATALOG"
  printf 'CREATE SCHEMA IF NOT EXISTS `%s`.`%s`\n' "$CATALOG" "$RAW_SCHEMA"
  printf 'CREATE SCHEMA IF NOT EXISTS `%s`.`%s`\n' "$CATALOG" "$MARTS_SCHEMA"
  printf 'CREATE VOLUME IF NOT EXISTS `%s`.`%s`.`%s`\n' "$CATALOG" "$RAW_SCHEMA" "$VOLUME"
}

# grant_hint <statement> <principal>: what an admin must grant when that statement is denied.
grant_hint() {
  local p="\`$2\`"
  case "$1" in
    "CREATE CATALOG"*)
      printf 'A metastore admin runs: GRANT CREATE CATALOG ON METASTORE TO %s;\n' "$p"
      printf 'Or rerun with --catalog <an existing catalog you can use>; the catalogs you can see are listed above.\n' ;;
    "CREATE SCHEMA"*)
      printf 'The catalog owner runs: GRANT USE CATALOG, CREATE SCHEMA ON CATALOG `%s` TO %s;\n' "$CATALOG" "$p" ;;
    "CREATE VOLUME"*)
      printf 'The schema owner runs: GRANT USE CATALOG ON CATALOG `%s` TO %s; GRANT USE SCHEMA, CREATE VOLUME ON SCHEMA `%s`.`%s` TO %s;\n' \
        "$CATALOG" "$p" "$CATALOG" "$RAW_SCHEMA" "$p" ;;
    warehouse)
      printf 'A workspace admin creates a SQL warehouse (or grants you warehouse creation), or rerun with --warehouse-name <an existing warehouse>.\n' ;;
  esac
}

# json_get <python-expr on d> [arg...]: read JSON from stdin, print the expression ('' when
# absent). Extra args are the list `a`, so values are passed as data, never spliced into code.
json_get() {
  python3 -c 'import json,sys
d, a = json.load(sys.stdin), sys.argv[2:]
try: v = eval(sys.argv[1])
except Exception: v = None
print("" if v is None else v)' "$@"
}

# api <METHOD> <path> [json-body]: the token goes through a mode-600 header file, not argv.
api() {
  local args=(-sS -H "@$AUTH_FILE" -H 'Content-Type: application/json' -X "$1" "https://$HOST$2")
  [[ $# -ge 3 ]] && args+=(--data-binary "$3")
  curl "${args[@]}" -w '\n%{http_code}'
}

# api_ok: split api's output; body to stdout, non-2xx exits 1 with the body on stderr.
api_ok() {
  local out code
  out="$(api "$@")"; code="${out##*$'\n'}"; out="${out%$'\n'*}"
  if [[ "$code" != 2* ]]; then printf '%s\n' "$out" >&2; return 1; fi
  printf '%s\n' "$out"
}

read_token() {
  if [[ -t 0 ]]; then read -r -s -p "Databricks personal access token: " TOKEN; echo
  else IFS= read -r TOKEN || true
  fi
  [[ -n "$TOKEN" ]] || die "no token given"
  AUTH_FILE="$(umask 077 && mktemp)"
  CLEANUP+=("$AUTH_FILE")
  printf 'Authorization: Bearer %s\n' "$TOKEN" > "$AUTH_FILE"
}

verify_token() {
  local me
  me="$(api_ok GET /api/2.0/preview/scim/v2/Me 2>&1)" || die "token check failed on $HOST: $(head -c 300 <<<"$me")"
  ME="$(json_get 'd["userName"]' <<<"$me")"
  say "Token works: signed in as $ME"
}

find_or_create_warehouse() {
  local list body
  if [[ -n "$WAREHOUSE_ID" ]]; then
    say "Using SQL warehouse id $WAREHOUSE_ID (--warehouse-id; no name lookup)"
    HTTP_PATH="/sql/1.0/warehouses/$WAREHOUSE_ID"
    return
  fi
  list="$(api_ok GET /api/2.0/sql/warehouses)"
  WAREHOUSE_ID="$(json_get "next(w['id'] for w in d.get('warehouses', []) if w['name'] == a[0])" "$WAREHOUSE_NAME" <<<"$list")"
  if [[ -n "$WAREHOUSE_ID" ]]; then
    say "Using SQL warehouse $WAREHOUSE_NAME ($WAREHOUSE_ID)"
  else
    say "Creating SQL warehouse $WAREHOUSE_NAME (serverless, 2X-Small, stops after 10 idle minutes)"
    body="{\"name\":$(json_str "$WAREHOUSE_NAME"),\"cluster_size\":\"2X-Small\",\"min_num_clusters\":1,\"max_num_clusters\":1,\"auto_stop_mins\":10,\"enable_serverless_compute\":true,\"warehouse_type\":\"PRO\"}"
    local out
    if ! out="$(api_ok POST /api/2.0/sql/warehouses "$body" 2>&1)"; then
      # Free Edition allows one warehouse: when that limit is hit and exactly one exists, use it.
      local only; only="$(json_get "(lambda w: w[0]['id'] + ' ' + w[0]['name'] if len(w) == 1 else None)(d.get('warehouses', []))" <<<"$list")"
      if ((!WAREHOUSE_GIVEN)) && [[ "$out" == *RESOURCE_EXHAUSTED* && -n "$only" ]]; then
        WAREHOUSE_ID="${only%% *}" WAREHOUSE_NAME="${only#* }"
        say "Warehouse limit reached (RESOURCE_EXHAUSTED) and exactly one exists: using '$WAREHOUSE_NAME' ($WAREHOUSE_ID)"
        HTTP_PATH="/sql/1.0/warehouses/$WAREHOUSE_ID"
        return
      fi
      printf '%s\n' "$out" >&2; grant_hint warehouse "$ME" >&2
      local names; names="$(json_get "', '.join(w['name'] for w in d.get('warehouses', []))" <<<"$list")"
      [[ -n "$names" ]] && say "Existing warehouses: $names (Free Edition allows one)" >&2
      exit 1
    fi
    WAREHOUSE_ID="$(json_get 'd["id"]' <<<"$out")"
  fi
  HTTP_PATH="/sql/1.0/warehouses/$WAREHOUSE_ID"
}

# run_sql <statement>: through the SQL statements API, waiting until it finishes.
run_sql() {
  local body out state id
  body="$(python3 -c 'import json,sys; print(json.dumps({"warehouse_id": sys.argv[1], "statement": sys.argv[2], "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"}))' "$WAREHOUSE_ID" "$1")"
  out="$(api_ok POST /api/2.0/sql/statements "$body")" || return 1
  id="$(json_get 'd["statement_id"]' <<<"$out")"
  state="$(json_get 'd["status"]["state"]' <<<"$out")"
  while [[ "$state" == PENDING || "$state" == RUNNING ]]; do
    sleep 5
    out="$(api_ok GET "/api/2.0/sql/statements/$id")" || return 1
    state="$(json_get 'd["status"]["state"]' <<<"$out")"
  done
  [[ "$state" == SUCCEEDED ]] && return 0
  json_get 'd["status"]["error"]["message"]' <<<"$out" >&2
  return 1
}

# Catalogs a user can put tables in: not the built-in system, samples or legacy ones.
USABLE_CATALOGS="[c['name'] for c in d.get('catalogs', []) if c['name'] not in ('system', 'samples', 'hive_metastore') and not c['name'].startswith('__')]"

create_objects() {
  local stmt first
  first="$(bootstrap_sql | head -n 1)"
  say "$first"
  if ! run_sql "$first"; then
    local cats; cats="$(api_ok GET /api/2.1/unity-catalog/catalogs)" || cats='{}'
    local usable; usable="$(json_get "' '.join($USABLE_CATALOGS)" <<<"$cats")"
    if ((!CATALOG_GIVEN)) && [[ -n "$usable" && "$usable" != *" "* ]]; then
      say "Cannot create catalog '$CATALOG' and exactly one usable catalog exists: using '$usable'"
      CATALOG="$usable"
    else
      say "Catalogs you can see: $(json_get "', '.join(c['name'] for c in d.get('catalogs', []))" <<<"$cats")" >&2
      grant_hint "$first" "$ME" >&2
      exit 1
    fi
  fi
  while IFS= read -r stmt; do
    say "$stmt"
    if ! run_sql "$stmt"; then grant_hint "$stmt" "$ME" >&2; exit 1; fi
  done < <(bootstrap_sql | tail -n +2)
}

# json_str / op_template: the same 1Password template shape as tools/bootstrap-snowflake.sh.
json_str() {
  local s="${1//\\/\\\\}"
  s="${s//\"/\\\"}"; s="${s//$'\n'/\\n}"; s="${s//$'\r'/\\r}"; s="${s//$'\t'/\\t}"
  printf '"%s"' "$s"
}

op_template() {
  local first=1 kv k v type
  printf '{"title":%s,"category":"SECURE_NOTE","fields":[' "$(json_str "$OP_ITEM")"
  for kv in "$@"; do
    k="${kv%%=*}" v="${kv#*=}"
    case "$k" in *TOKEN*) type=CONCEALED ;; *) type=STRING ;; esac
    ((first)) || printf ','
    first=0
    printf '{"id":%s,"label":%s,"type":"%s","value":%s}' "$(json_str "$k")" "$(json_str "$k")" "$type" "$(json_str "$v")"
  done
  printf ']}\n'
}

store_in_1password() {
  ((SKIP_OP)) && { say "Skipping 1Password (--skip-1password)"; return; }
  say "Storing the connection in 1Password: $OP_VAULT/$OP_ITEM (values never printed)"
  command -v op >/dev/null || die "op (1Password CLI) not found; rerun with --skip-1password or install it"
  op whoami >/dev/null 2>&1 || die "op is not signed in: run 'eval \$(op signin)' and rerun (everything above is reused)"
  local tpl; tpl="$(umask 077 && mktemp)"
  CLEANUP+=("$tpl")
  op_template "DATABRICKS_HOST=$HOST" "DATABRICKS_HTTP_PATH=$HTTP_PATH" \
    "DATABRICKS_TOKEN=$TOKEN" "DATABRICKS_CATALOG=$CATALOG" > "$tpl"
  if op item get "$OP_ITEM" --vault "$OP_VAULT" >/dev/null 2>&1; then
    op item edit "$OP_ITEM" --vault "$OP_VAULT" --template "$tpl" >/dev/null
  else
    op item create --vault "$OP_VAULT" --template "$tpl" >/dev/null
  fi
  say "1Password item $OP_ITEM updated"
}

dry_run() {
  say "Dry run: nothing is read, created or stored"
  say "Host: ${HOST:-<from --host, --from-terraform, or a prompt>}"
  say "Read the personal access token without echo (prompt, or stdin when piped)"
  say "GET https://${HOST:-<host>}/api/2.0/preview/scim/v2/Me   (token check)"
  if [[ -n "$WAREHOUSE_ID" ]]; then say "Use SQL warehouse id $WAREHOUSE_ID (no lookup)"
  else say "Find SQL warehouse '$WAREHOUSE_NAME' (GET /api/2.0/sql/warehouses), else create it serverless 2X-Small"
  fi
  say "Run through POST /api/2.0/sql/statements:"
  bootstrap_sql | sed 's/^/    /'
  if ((SKIP_OP)); then say "Skip 1Password"
  else say "Store DATABRICKS_HOST, DATABRICKS_HTTP_PATH, DATABRICKS_TOKEN, DATABRICKS_CATALOG in $OP_VAULT/$OP_ITEM via a mode-600 template"
  fi
}

main() {
  parse_args "$@"
  if ((FROM_TF)) && [[ -z "$HOST" ]]; then
    ((DRY_RUN)) && HOST="<terraform output databricks_workspace_url>" \
      || HOST="$(terraform -chdir="$ROOT/infra/azure/envs/dev" output -raw databricks_workspace_url)"
  fi
  if [[ -n "$HOST" && "$HOST" != "<"* ]]; then HOST="$(normalize_host "$HOST")" || exit 1; fi
  ((DRY_RUN)) && { dry_run; return; }

  for tool in curl python3; do command -v "$tool" >/dev/null || die "$tool not found"; done
  if [[ -z "$HOST" ]]; then
    read -r -p "Workspace host or URL: " HOST < /dev/tty
    HOST="$(normalize_host "$HOST")" || exit 1
  fi
  read_token
  verify_token
  find_or_create_warehouse
  create_objects
  store_in_1password
  say "Done. DATABRICKS_HOST=$HOST DATABRICKS_HTTP_PATH=$HTTP_PATH DATABRICKS_CATALOG=$CATALOG (token in 1Password)"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
