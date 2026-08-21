#!/usr/bin/env bash
# PR body content linter.
#
# Verifies that mandatory sections of a PR body (Summary, Related,
# Testing, Risk) contain real content — not just HTML placeholder
# comments or a lone bullet with no value ("- ADR: ", "- Issue: ").
#
# Called by:
#   - .github/workflows/ci.yml pr-body job (production)
#   - Pre-commit hook when this script changes (self-test)
#   - Manually: PR_BODY="..." bash scripts/dev/pr-body-lint.sh
#
# Env vars:
#   PR_BODY           — the PR body text to lint (required in normal use)
#   HERMES_SELF_TEST  — set to 1 to run built-in pass/fail cases and exit
#
# Exit codes:
#   0 — all mandatory sections have content (or self-test passed)
#   1 — one or more mandatory sections are empty
#   2 — usage error (missing PR_BODY when not self-testing)

set -euo pipefail

MANDATORY_SECTIONS=(Summary Related Testing Risk)

# ────────────────────────────────────────────────────────────────
# lint_body <body-text>
# echoes space-separated names of missing sections (empty on pass)
# ────────────────────────────────────────────────────────────────
lint_body() {
    local body="$1"
    local missing=()
    local section content stripped

    for section in "${MANDATORY_SECTIONS[@]}"; do
        content=$(printf '%s\n' "$body" | awk -v s="$section" '
            $0 ~ "^## " s "$" {inside=1; next}
            inside && /^## / {inside=0}
            inside { print }
        ')
        # Strip HTML comments (single-line + multi-line spans) then filter
        # empty / placeholder-only bullet rows.
        stripped=$(printf '%s\n' "$content" | awk '
            {
                line = $0
                while (match(line, /<!--.*?-->/)) {
                    line = substr(line, 1, RSTART - 1) substr(line, RSTART + RLENGTH)
                }
                if (line ~ /<!--/) { sub(/<!--.*/, "", line); in_comment=1; print line; next }
                if (in_comment) { if (line ~ /-->/) { sub(/.*-->/, "", line); in_comment=0; print line } next }
                print line
            }
        ' | grep -vE '^[[:space:]]*$|^[[:space:]]*-[[:space:]]+(ADR|Issue):[[:space:]]*$' || true)

        if [[ -z "$stripped" ]]; then
            missing+=("$section")
        fi
    done

    printf '%s' "${missing[*]}"
}

# ────────────────────────────────────────────────────────────────
# Self-test — exercise pass + fail without needing a real PR
# ────────────────────────────────────────────────────────────────
if [[ "${HERMES_SELF_TEST:-0}" == "1" ]]; then
    failed=0

    # PASS: all sections filled with real content
    body_ok='# PR

## Summary

Some real content here.

## Related

- ADR: ADR-010
- Issue: Closes #42

## Testing

- Ran the thing.

## Risk

Low.'
    result=$(lint_body "$body_ok")
    if [[ -z "$result" ]]; then
        echo "PASS: fully-filled body returns no missing sections"
    else
        echo "FAIL: fully-filled body reported missing [$result]"
        failed=1
    fi

    # FAIL: placeholder-only sections
    body_placeholders='# PR

## Summary

<!-- 1-3 sentences describing what changes -->

## Related

- ADR: <!-- e.g. ADR-007 or N/A -->
- Issue: <!-- e.g. Closes #42 -->

## Testing

<!-- what was verified -->

## Risk

<!-- what could break -->'
    result=$(lint_body "$body_placeholders")
    if [[ "$result" == "Summary Related Testing Risk" ]]; then
        echo "PASS: placeholder-only body reports all sections missing"
    else
        echo "FAIL: placeholder-only body reported missing [$result], expected all four"
        failed=1
    fi

    # FAIL: partial fill (only Summary + Related)
    body_partial='# PR

## Summary

Some content.

## Related

- ADR: ADR-006

## Testing

## Risk

<!-- placeholder -->'
    result=$(lint_body "$body_partial")
    if [[ "$result" == "Testing Risk" ]]; then
        echo "PASS: partial-fill reports [Testing Risk] missing"
    else
        echo "FAIL: partial-fill reported [$result], expected [Testing Risk]"
        failed=1
    fi

    # PASS: multi-line comment stripped correctly
    body_multiline_comment='# PR

## Summary

<!--
line one of comment
line two
-->

Real content survives the multi-line strip.

## Related

- ADR: X

## Testing

Tested.

## Risk

None.'
    result=$(lint_body "$body_multiline_comment")
    if [[ -z "$result" ]]; then
        echo "PASS: multi-line comment stripped, real content preserved"
    else
        echo "FAIL: multi-line comment case reported [$result], expected pass"
        failed=1
    fi

    if [[ "$failed" == 1 ]]; then
        echo ""
        echo "SELF-TEST FAILED"
        exit 1
    fi
    echo ""
    echo "All self-tests passed."
    exit 0
fi

# ────────────────────────────────────────────────────────────────
# Production path
# ────────────────────────────────────────────────────────────────
if [[ -z "${PR_BODY:-}" ]]; then
    echo "ERROR: PR_BODY env var required (or set HERMES_SELF_TEST=1 for tests)" >&2
    exit 2
fi

if [[ -z "$(printf '%s' "$PR_BODY" | tr -d '[:space:]')" ]]; then
    echo "::error::PR body is empty — the template must be filled in."
    exit 1
fi

missing=$(lint_body "$PR_BODY")
if [[ -n "$missing" ]]; then
    echo "::error::PR body missing content in mandatory sections: $missing"
    echo "Fill each ## section with real content (not the placeholder <!-- ... --> comment)."
    exit 1
fi

echo "All mandatory sections have content."
