#!/usr/bin/env bash
# Regenerate the guides index table in docs/guides/README.md.
#
# Reads each docs/guides/*.md file (excluding README.md), extracts:
#   * H1 → filename link
#   * **Description**: <line> from the header metadata block
# Writes a fresh 2-column table between the marker comments in README.md.
#
# Idempotent: identical output for identical input. Safe to run repeatedly.
# The table depends on file content only, never on git history: a commit
# date changes when the merge queue squashes a PR.
#
# The index is a derived file, rebuilt on main after each merge. PRs never
# commit it: two PRs that each regenerate it conflict, and a committed copy
# goes stale the moment another PR lands.
#
# Usage:
#   regenerate-guides-index.sh           rewrite the table in README.md
#   regenerate-guides-index.sh --check   validate every guide and build the
#                                        table, write nothing (pre-commit,
#                                        pr-preflight, CI)
#
# Expected guide file shape:
#   # <Title>
#
#   **Type**: Guide (living document — PR any change)
#   **Owners**: Ricardo Afonso
#   **Applies to**: ...
#   **Description**: One-line summary that shows up in the index cell.
#
#   <body>

set -euo pipefail

check_only=0
case "${1:-}" in
    "") ;;
    --check) check_only=1 ;;
    *)
        echo "usage: $0 [--check]" >&2
        exit 2
        ;;
esac

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GUIDES_DIR="$REPO_ROOT/docs/guides"
README="$GUIDES_DIR/README.md"

if [[ ! -d "$GUIDES_DIR" ]]; then
    echo "ERROR: $GUIDES_DIR does not exist" >&2
    exit 1
fi

if [[ ! -f "$README" ]]; then
    echo "ERROR: $README does not exist" >&2
    exit 1
fi

TABLE="| Guide | What it covers |"$'\n'"| ----- | -------------- |"

for guide in $(find "$GUIDES_DIR" -maxdepth 1 -name '*.md' -not -name 'README.md' | sort); do
    filename="$(basename "$guide")"

    # H1: first line starting with "# "
    title="$(grep -m1 '^# ' "$guide" | sed 's/^# //')"

    # Description: line starting with "**Description**:"
    description="$(grep -m1 '^\*\*Description\*\*:' "$guide" | sed 's/^\*\*Description\*\*: *//')"

    if [[ -z "$title" ]]; then
        echo "ERROR: $filename has no H1 heading — cannot index" >&2
        exit 1
    fi
    if [[ -z "$description" ]]; then
        echo "ERROR: $filename missing '**Description**: <line>' in header — cannot index" >&2
        exit 1
    fi

    TABLE+=$'\n'"| [$filename]($filename) | $description |"
done

# Verify markers exist before writing
if ! grep -q '<!-- guides-index-start -->' "$README"; then
    echo "ERROR: $README missing '<!-- guides-index-start -->' marker" >&2
    exit 1
fi
if ! grep -q '<!-- guides-index-end -->' "$README"; then
    echo "ERROR: $README missing '<!-- guides-index-end -->' marker" >&2
    exit 1
fi

count=$(find "$GUIDES_DIR" -maxdepth 1 -name '*.md' -not -name 'README.md' | wc -l | tr -d ' ')

if [[ "$check_only" == 1 ]]; then
    echo "guides index builds from ${count} guide file(s); README.md not written (--check)."
    exit 0
fi

awk -v table="$TABLE" '
    /<!-- guides-index-start -->/ { print; print table; skip=1; next }
    /<!-- guides-index-end -->/ { skip=0 }
    !skip { print }
' "$README" > "$README.tmp"
mv "$README.tmp" "$README"

echo "$README regenerated from ${count} guide file(s)."
