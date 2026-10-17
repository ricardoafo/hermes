#!/usr/bin/env bash
# Render a PR body for an SDD change from openspec/changes/<name>/change.md.
#
# Extracts Summary / Test plan / Risks from change.md, reads the tracking
# issue from openspec/changes/<name>/issue.md, and emits a body matching
# the mandatory pull_request_template.md sections (Summary / Related /
# Testing / Risk / Follow-ups).
#
# Motivation: hand-writing PR bodies from change.md content is
# error-prone and inconsistent. This script removes that step from every
# SDD flow — pr-preflight.sh --render-body <name> renders + lints in one.
#
# ## Invocation
#
#   bash scripts/dev/render-pr-body.sh <change-name>            # to stdout
#   bash scripts/dev/render-pr-body.sh <change-name> -o body.md # to file
#   bash scripts/dev/render-pr-body.sh <change-name> --branch <ref>  # remote fallback
#   HERMES_SELF_TEST=1 bash scripts/dev/render-pr-body.sh       # sanity
#
# ## Exit codes
#   0 — body rendered successfully
#   1 — change.md missing required section OR issue.md missing/malformed
#   2 — usage error

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# ────────────────────────────────────────────────────────────────
# Pure function: extract_section <markdown> <section-name>
# Extracts the body of the first `## <section-name>` H2 in the given
# markdown text, stopping at the next H2. Prints trimmed content or
# empty if not found.
# ────────────────────────────────────────────────────────────────
extract_section() {
    local md="$1"
    local section="$2"
    printf '%s' "$md" | awk -v s="$section" '
        BEGIN { inside=0 }
        /^## / {
            if (inside) exit
            if ($0 == "## " s) { inside=1; next }
            next
        }
        inside { print }
    ' | sed -e '/./,$!d' -e :a -e '/^\s*$/{$d;N;ba' -e '}'
}

# ────────────────────────────────────────────────────────────────
# Pure function: read_change_file <local-path> [<branch-ref> <repo-relative-path>]
# Returns the file contents. Tries local path first; if missing AND a
# branch ref is provided, falls back to `git show origin/<ref>:<path>`
# after `git fetch origin <ref>` to survive slow-network races.
# Emits to stderr on fetch failure but continues (works offline if the
# ref is already in the local refs).
# ────────────────────────────────────────────────────────────────
read_change_file() {
    local local_path="$1"
    local branch_ref="${2:-}"
    local repo_rel="${3:-}"

    if [[ -f "$local_path" ]]; then
        cat "$local_path"
        return 0
    fi

    if [[ -z "$branch_ref" || -z "$repo_rel" ]]; then
        return 1
    fi

    # Best-effort fetch — silent on success, ignored on failure (the
    # subsequent `git show` will fail cleanly if ref is unreachable).
    git fetch origin "$branch_ref" --quiet 2>/dev/null || true

    if ! git show "origin/${branch_ref}:${repo_rel}" 2>/dev/null; then
        echo "ERROR: could not read '$repo_rel' from local disk OR from origin/${branch_ref}" >&2
        echo "  Tried local: $local_path" >&2
        echo "  Tried remote: origin/${branch_ref}:${repo_rel}" >&2
        return 1
    fi
}

# ────────────────────────────────────────────────────────────────
# Pure function: render_body <change-md> <issue-number> [<issue-url> [<closes-list>]]
# Composes the full PR body from change.md content + issue reference.
# <closes-list> is a newline-separated string of secondary issue numbers
# (may be empty). Primary issue number is always emitted; duplicates in
# closes-list are skipped.
# ────────────────────────────────────────────────────────────────
render_body() {
    local change_md="$1"
    local issue_num="$2"
    local issue_url="${3:-}"
    local closes_list="${4:-}"   # newline-separated secondary issue numbers

    local summary testing risks
    summary=$(extract_section "$change_md" "Summary")
    testing=$(extract_section "$change_md" "Test plan")
    risks=$(extract_section "$change_md" "Risks")

    if [[ -z "$summary" ]]; then
        echo "ERROR: change.md is missing '## Summary' section" >&2
        return 1
    fi
    if [[ -z "$testing" ]]; then
        echo "ERROR: change.md is missing '## Test plan' section" >&2
        return 1
    fi
    if [[ -z "$risks" ]]; then
        echo "ERROR: change.md is missing '## Risks' section" >&2
        return 1
    fi

    # Build the deduplicated Closes list — primary issue first, then each
    # secondary in file order, skipping any that equal the primary.
    local closes_block="Closes #$issue_num"
    local related_block="- Issue: Closes #$issue_num${issue_url:+ ($issue_url)}"
    if [[ -n "$closes_list" ]]; then
        while IFS= read -r n; do
            [[ -z "$n" ]] && continue
            [[ "$n" == "$issue_num" ]] && continue
            closes_block+=$'\n'"Closes #$n"
            related_block+=$'\n'"- Also closes: #$n"
        done <<< "$closes_list"
    fi

    cat <<BODY
## Summary

$summary

$closes_block

## Related

$related_block
- SDD change: \`openspec/changes/\` (see \`change.md\` in this PR)
- ADR references and cross-cutting citations are in \`change.md\` § Decisions and § Dependencies

## Testing

$testing

## Risk

$risks

## Follow-ups (out of scope)

See \`change.md\` § Scope § Out for the deferred items and their target phase or follow-up issue.
BODY
}

# ────────────────────────────────────────────────────────────────
# Self-test — 6 fixtures for extract_section + render_body + read_change_file
# ────────────────────────────────────────────────────────────────
if [[ "${HERMES_SELF_TEST:-}" == "1" ]]; then
    fail=0

    # Fixture 1: extract_section finds a middle section
    out=$(extract_section "## A
alpha
## B
beta
## C
gamma" "B")
    if [[ "$out" != "beta" ]]; then
        echo "SELF-TEST FAIL: extract_section middle: got '$out', want 'beta'" >&2
        fail=1
    fi

    # Fixture 2: extract_section returns empty for missing section
    out=$(extract_section "## A
alpha" "Z")
    if [[ -n "$out" ]]; then
        echo "SELF-TEST FAIL: extract_section missing: got non-empty '$out'" >&2
        fail=1
    fi

    # Fixture 3: render_body fails when Summary missing
    if render_body "## Test plan
foo
## Risks
bar" "42" >/dev/null 2>&1; then
        echo "SELF-TEST FAIL: render_body should reject missing Summary" >&2
        fail=1
    fi

    # Fixture 4: render_body succeeds with all three sections
    body=$(render_body "## Summary
Hello world.
## Test plan
- run tests
## Risks
- none" "42" "https://example/issues/42")
    if ! grep -q "^Closes #42$" <<< "$body"; then
        echo "SELF-TEST FAIL: render_body should include 'Closes #42'" >&2
        fail=1
    fi
    if ! grep -q "^Hello world.$" <<< "$body"; then
        echo "SELF-TEST FAIL: render_body should include Summary content" >&2
        fail=1
    fi

    # Fixture 5: render_body emits Closes lines for primary + each secondary,
    # deduplicated, in file order — covers #97 multi-close.
    body=$(render_body "## Summary
Ships thing.
## Test plan
- run tests
## Risks
- none" "42" "https://example/issues/42" "43
44
42")
    # Expect exactly 3 Closes lines (primary 42, secondaries 43 + 44;
    # the duplicate '42' in the closes list must be deduped).
    closes_count=$(grep -c '^Closes #' <<< "$body")
    if [[ "$closes_count" -ne 3 ]]; then
        echo "SELF-TEST FAIL: multi-close: expected 3 Closes lines, got $closes_count" >&2
        echo "--- body ---" >&2
        echo "$body" >&2
        fail=1
    fi
    for n in 42 43 44; do
        if ! grep -q "^Closes #${n}$" <<< "$body"; then
            echo "SELF-TEST FAIL: multi-close: missing 'Closes #${n}'" >&2
            fail=1
        fi
    done
    # Related block should show primary as 'Issue:' and each secondary as
    # 'Also closes:'.
    also_count=$(grep -c '^- Also closes: #' <<< "$body")
    if [[ "$also_count" -ne 2 ]]; then
        echo "SELF-TEST FAIL: multi-close: expected 2 'Also closes' rows, got $also_count" >&2
        fail=1
    fi

    # Fixture 6: read_change_file falls back cleanly when local missing and
    # no branch given — covers #93 error path. Full remote-fetch path is
    # not exercised in self-test (would require git repo state); this
    # confirms the local-first contract and the error message on missing.
    if read_change_file "/nonexistent/path/change.md" "" "" 2>/dev/null; then
        echo "SELF-TEST FAIL: read_change_file should fail when local missing and no branch" >&2
        fail=1
    fi
    # And confirm it succeeds when local IS present (writing a temp file).
    tmp_fixture=$(mktemp -t hermes-selftest.XXXXXX)
    printf 'hello\n' > "$tmp_fixture"
    out=$(read_change_file "$tmp_fixture" "" "")
    if [[ "$out" != "hello" ]]; then
        echo "SELF-TEST FAIL: read_change_file local-read got '$out', want 'hello'" >&2
        fail=1
    fi
    rm -f "$tmp_fixture"

    if [[ "$fail" -eq 0 ]]; then
        echo "SELF-TEST PASS: render-pr-body.sh handles 6 fixtures correctly."
        exit 0
    fi
    exit 1
fi

# ────────────────────────────────────────────────────────────────
# Argument parsing (production path)
# ────────────────────────────────────────────────────────────────
CHANGE_NAME="${1:-}"
if [[ -z "$CHANGE_NAME" ]]; then
    echo "Usage: bash scripts/dev/render-pr-body.sh <change-name> [-o output-file] [--branch <ref>]" >&2
    echo "  --branch <ref>: when change.md/issue.md are absent locally," >&2
    echo "                  fall back to \`git show origin/<ref>:<path>\`." >&2
    exit 2
fi

OUTPUT=""
BRANCH_REF=""
shift || true
while [[ $# -gt 0 ]]; do
    case "$1" in
        -o) OUTPUT="${2:-}"; shift 2 ;;
        --branch) BRANCH_REF="${2:-}"; shift 2 ;;
        *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
    esac
done

CHANGE_DIR="$REPO_ROOT/openspec/changes/$CHANGE_NAME"
CHANGE_FILE="$CHANGE_DIR/change.md"
ISSUE_FILE="$CHANGE_DIR/issue.md"
CHANGE_REL="openspec/changes/$CHANGE_NAME/change.md"
ISSUE_REL="openspec/changes/$CHANGE_NAME/issue.md"

# Read change.md — local first, remote fallback if --branch given.
change_content=$(read_change_file "$CHANGE_FILE" "$BRANCH_REF" "$CHANGE_REL") || {
    if [[ -z "$BRANCH_REF" ]]; then
        echo "ERROR: $CHANGE_FILE not found. Is the change name '$CHANGE_NAME' correct?" >&2
        echo "Note: 3-file legacy convention (proposal.md/spec.md/design.md) is not supported." >&2
        echo "New SDD changes use change.md per ADR-009 §6." >&2
        echo "Hint: pass --branch <ref> to read from origin/<ref> when local is absent." >&2
    fi
    exit 1
}

# Read issue.md — same fallback.
issue_content=$(read_change_file "$ISSUE_FILE" "$BRANCH_REF" "$ISSUE_REL") || {
    if [[ -z "$BRANCH_REF" ]]; then
        echo "ERROR: $ISSUE_FILE not found." >&2
        echo "Every SDD change must have a tracking issue (per ADR-009 §6)." >&2
        echo "Create it via: gh issue create ... then write the number to $ISSUE_FILE." >&2
    fi
    exit 1
}

# Parse issue.md — primary `issue:` + secondary `closes:` lines.
ISSUE_NUM=$(printf '%s\n' "$issue_content" | grep -E '^issue:' | awk '{print $2}' | head -1)
ISSUE_URL=$(printf '%s\n' "$issue_content" | grep -E '^url:' | awk '{print $2}' | head -1)
CLOSES_LIST=$(printf '%s\n' "$issue_content" | grep -E '^closes:' | awk '{print $2}')

if [[ -z "$ISSUE_NUM" ]]; then
    echo "ERROR: $ISSUE_REL missing 'issue: <N>' line" >&2
    echo "Expected format:" >&2
    echo "  issue: 84" >&2
    echo "  url: https://github.com/ricardoafo/hermes/issues/84" >&2
    echo "  closes: 85    # optional, repeatable" >&2
    echo "  closes: 86" >&2
    exit 1
fi

# ────────────────────────────────────────────────────────────────
# Render
# ────────────────────────────────────────────────────────────────
body=$(render_body "$change_content" "$ISSUE_NUM" "$ISSUE_URL" "$CLOSES_LIST")

if [[ -n "$OUTPUT" ]]; then
    printf '%s\n' "$body" > "$OUTPUT"
    echo "PR body rendered to $OUTPUT ($(wc -l < "$OUTPUT") lines)"
else
    printf '%s\n' "$body"
fi
