#!/usr/bin/env bash
# check-public.sh — public-safety scan for a public repo.
#
# Scans the repo (by default every git-tracked file) for a hard deny-list of
# client/vendor identifiers, plus any additional patterns listed in the
# gitignored `.forbidden_strings.local` file kept privately by the repo owner.
#
# The built-in deny-list is stored as SHA-256 hashes of lowercase words, so the
# list itself carries no readable names. Each file is split into lowercase
# alphanumeric words and every word is hashed and compared.
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

# Hard deny-list — SHA-256 of identifiers that must never appear in a public repo.
DENY_HASHES=(
  0e2bc07791b716ceaa28b826ac2e4395993d92a56eced6a4db0e69c16b0524bc
  3163c9e743c68d4d4a8d39fdd802aa90c60f49418da959cae764d9f92e98b896
)

# Optional locally-held patterns (one per line). Gitignored — never committed.
EXTRA_FILE=".forbidden_strings.local"
EXTRA=()
if [[ -f "$EXTRA_FILE" ]]; then
  # shellcheck disable=SC2207
  mapfile -t EXTRA < "$EXTRA_FILE"
fi

# Files to scan: argument paths, or every git-tracked file by default.
if [[ $# -gt 0 ]]; then
  mapfile -t files < <(printf '%s\n' "$@")
else
  mapfile -t files < <(git ls-files)
fi

# Files that legitimately quote the deny-list itself and must be skipped (by basename).
EXCLUDE_FILES=(check_public.sh AGENT_HANDOFF.md)

status=0

# Built-in list: hash every word of every text file.
hash_hits="$(printf '%s\n' "${files[@]}" | DENY_HASHES="${DENY_HASHES[*]}" EXCLUDE_FILES="${EXCLUDE_FILES[*]}" python3 -I -c '
import hashlib, os, re, sys
deny = set(os.environ["DENY_HASHES"].split())
skip = set(os.environ["EXCLUDE_FILES"].split())
for path in (l.rstrip("\n") for l in sys.stdin):
    if not path or os.path.basename(path) in skip or not os.path.isfile(path):
        continue
    try:
        data = open(path, "rb").read()
    except OSError:
        continue
    if b"\0" in data[:8192]:
        continue
    for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
        if any(hashlib.sha256(w.encode()).hexdigest() in deny for w in re.findall(r"[a-z0-9]+", line.lower())):
            print(f"{path}:{n}: <blocked word>")
')"
if [[ -n "$hash_hits" ]]; then
  echo "blocked: a word from the public deny-list was found"
  echo "$hash_hits"
  status=1
fi

# Local list: plain patterns.
nonempty=()
for p in "${EXTRA[@]}"; do [[ -n "${p//[[:space:]]/}" ]] && nonempty+=("$p"); done
exclude_args=()
for f in "${EXCLUDE_FILES[@]}"; do exclude_args+=(--exclude="$f"); done
for pat in "${nonempty[@]}"; do
  hits="$(grep -rInI --exclude-dir=.git "${exclude_args[@]}" -- "$pat" "${files[@]}" 2>/dev/null || true)"
  if [[ -n "$hits" ]]; then
    echo "blocked: '$pat' found (from .forbidden_strings.local)"
    echo "$hits"
    status=1
  fi
done

if [[ $status -eq 0 ]]; then
  echo "check-public: clean"
fi
exit $status
