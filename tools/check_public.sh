#!/usr/bin/env bash
# check-public.sh — public-safety scan for a public repo.
#
# Greps the repo (by default every git-tracked file) for a hard deny-list of
# client/vendor identifiers, plus any additional patterns listed in the
# gitignored `.forbidden_strings.local` file kept privately by the repo owner.
#
# `.forbidden_strings.local` is gitignored and must NEVER be committed or have
# its contents echoed into a PR.
#
# Usage:
#   tools/check_public.sh
#   tools/check_public.sh <path...>   # scan specific paths instead of all tracked files

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Hard deny-list — identifiers that must never appear in a public repo.
DENY_LIST=(
  lyzr
  gitagent
  open-gitagent
)

# Optional locally-held patterns (one per line). Gitignored — never committed.
EXTRA_FILE=".forbidden_strings.local"
EXTRA=()
if [[ -f "$EXTRA_FILE" ]]; then
  # shellcheck disable=SC2207
  mapfile -t EXTRA < "$EXTRA_FILE"
fi

patterns=("${DENY_LIST[@]}" "${EXTRA[@]}")

# Files to scan: argument paths, or every git-tracked file by default.
if [[ $# -gt 0 ]]; then
  mapfile -t files < <(printf '%s\n' "$@")
else
  mapfile -t files < <(git ls-files)
fi

# shellcheck disable=SC2207
nonempty=($(printf '%s\n' "${patterns[@]}" | grep -v '^[[:space:]]*$' || true))

# Files that legitimately quote the deny-list itself and must be skipped:
#   - tools/check_public.sh  (defines DENY_LIST)
#   - docs/AGENT_HANDOFF.md  (§3 Guardrails quotes these exact identifiers)
# Skip them by basename so the scan still covers their other contents.
EXCLUDE_FILES=(check_public.sh AGENT_HANDOFF.md)
exclude_args=()
for f in "${EXCLUDE_FILES[@]}"; do
  exclude_args+=(--exclude="$f")
done

status=0
for pat in "${nonempty[@]}"; do
  hits="$(grep -rInI --exclude-dir=.git "${exclude_args[@]}" -- "$pat" "${files[@]}" 2>/dev/null || true)"
  if [[ -n "$hits" ]]; then
    if (( ${#EXTRA[@]} > 0 )) && printf '%s\n' "${EXTRA[@]}" | grep -Fqx -- "$pat"; then
      src=".forbidden_strings.local"
    else
      src="public deny-list"
    fi
    echo "blocked: '$pat' found (from $src)"
    echo "$hits"
    status=1
  fi
done

if [[ $status -eq 0 ]]; then
  echo "check-public: clean"
fi
exit $status