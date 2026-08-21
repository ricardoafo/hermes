#!/usr/bin/env bash
# Regenerate the guides index table in docs/guides/README.md.
#
# Reads each docs/guides/*.md file (excluding README.md), extracts:
#   * H1 → filename link
#   * **Description**: <line> from the header metadata block
#   * Last updated: git log's last commit date for that file (YYYY-MM-DD)
# Writes a fresh 3-column table between the marker comments in README.md.
#
# Idempotent: identical output for identical input. Safe to run repeatedly.
# Called from: pre-commit hook + CI verification job.
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

TABLE="| Guide | What it covers | Last updated |"$'\n'"| ----- | -------------- | ------------ |"

for guide in $(find "$GUIDES_DIR" -maxdepth 1 -name '*.md' -not -name 'README.md' | sort); do
    filename="$(basename "$guide")"

    # H1: first line starting with "# "
    title="$(grep -m1 '^# ' "$guide" | sed 's/^# //')"

    # Description: line starting with "**Description**:"
    description="$(grep -m1 '^\*\*Description\*\*:' "$guide" | sed 's/^\*\*Description\*\*: *//')"

    # Last updated: last commit date for this file (author date, YYYY-MM-DD)
    # Falls back to today if the file is not yet committed.
    last_updated="$(cd "$REPO_ROOT" && git log -1 --format='%as' -- "$guide" 2>/dev/null || true)"
    if [[ -z "$last_updated" ]]; then
        last_updated="$(date +%Y-%m-%d)"
    fi

    if [[ -z "$title" ]]; then
        echo "ERROR: $filename has no H1 heading — cannot index" >&2
        exit 1
    fi
    if [[ -z "$description" ]]; then
        echo "ERROR: $filename missing '**Description**: <line>' in header — cannot index" >&2
        exit 1
    fi

    TABLE+=$'\n'"| [$filename]($filename) | $description | $last_updated |"
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

awk -v table="$TABLE" '
    /<!-- guides-index-start -->/ { print; print table; skip=1; next }
    /<!-- guides-index-end -->/ { skip=0 }
    !skip { print }
' "$README" > "$README.tmp"
mv "$README.tmp" "$README"

count=$(find "$GUIDES_DIR" -maxdepth 1 -name '*.md' -not -name 'README.md' | wc -l | tr -d ' ')
echo "$README regenerated from ${count} guide file(s)."
