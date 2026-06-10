#!/usr/bin/env bash
# build.sh — Generate HTML from Markdown for the Anchoring AI blog series.
#
# Usage:
#   ./blogs/build.sh          # Build all posts
#   ./blogs/build.sh 01       # Build only post 01
#   ./blogs/build.sh index    # Build only the series index page
#
# Requires: pandoc >= 2.11  (brew install pandoc)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/template.html"
FILTER="${1:-}"
COUNT=0

# ── Pre-flight checks ──────────────────────────────────────────────────────────

if ! command -v pandoc &>/dev/null; then
  echo ""
  echo "  Error: pandoc is not installed."
  echo "  Install it with:  brew install pandoc"
  echo ""
  exit 1
fi

if [[ ! -f "$TEMPLATE" ]]; then
  echo "  Error: template not found at $TEMPLATE"
  exit 1
fi

PANDOC_OPTS=(
  --template="$TEMPLATE"
  --syntax-highlighting=breezedark
  --standalone
  -f "markdown+yaml_metadata_block+smart"
  -t html5
)

echo ""
echo "  Anchoring AI — Blog Builder"
echo "  Template: $TEMPLATE"
echo "  Pandoc:   $(pandoc --version | head -1)"
echo ""

# ── Series index page ──────────────────────────────────────────────────────────

build_index() {
  local input="$SCRIPT_DIR/index.md"
  local output="$SCRIPT_DIR/index.html"
  if [[ -f "$input" ]]; then
    pandoc "${PANDOC_OPTS[@]}" -o "$output" "$input"
    echo "  Built: $output"
    (( COUNT++ )) || true
  fi
}

# ── Individual post pages ──────────────────────────────────────────────────────

build_posts() {
  for post_dir in "$SCRIPT_DIR"/[0-9][0-9]-*/; do
    local slug
    slug="$(basename "$post_dir")"
    local num="${slug:0:2}"
    local input="$SCRIPT_DIR/$slug/index.md"
    local output="$SCRIPT_DIR/$slug/index.html"

    [[ -f "$input" ]] || continue

    if [[ -n "$FILTER" && "$FILTER" != "all" && "$num" != "$FILTER" ]]; then
      continue
    fi
    pandoc "${PANDOC_OPTS[@]}" -o "$output" "$input"
    echo "  Built: $output"
    (( COUNT++ )) || true
  done
}

# ── Run ────────────────────────────────────────────────────────────────────────

if [[ "$FILTER" == "index" ]]; then
  build_index
elif [[ -z "$FILTER" || "$FILTER" == "all" ]]; then
  build_index
  build_posts
else
  build_posts
fi

echo ""
echo "  Done. $COUNT file(s) built."
echo ""
