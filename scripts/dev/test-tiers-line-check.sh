#!/usr/bin/env bash
# "Test tiers touched" PR-body gate (#160).
#
# The `## Testing` section of every PR body must carry one line naming
# the pyramid tiers the PR adds or changes tests in:
#
#   Test tiers touched: unit, property
#   Test tiers touched: none (docs only)
#
# Allowed tiers: unit, property, contract, integration, e2e, mutation, none.
# The line forces the author (human or sub-agent) to say which tiers the
# change exercises; reviewers check the claim against the diff.
#
# Called by:
#   - scripts/dev/create-pr.sh, after pr-body-lint.sh (exit 6 on failure)
#   - Pre-commit hook when THIS script changes (self-test)
#   - Manually: PR_BODY="$(cat body.md)" bash scripts/dev/test-tiers-line-check.sh
#
# Not wired into CI's pr-body job on purpose: PRs opened before this
# gate existed would turn red on their next push.
#
# Env vars:
#   PR_BODY           — the PR body text to check (required in normal use)
#   HERMES_SELF_TEST  — set to 1 to run built-in pass/fail cases and exit
#
# Exit codes:
#   0 — line present with at least one known tier (or self-test passed)
#   1 — line missing, empty, or naming an unknown tier
#   2 — usage error (missing PR_BODY when not self-testing)

set -euo pipefail

KNOWN_TIERS="unit property contract integration e2e mutation none"

# ────────────────────────────────────────────────────────────────
# check_body <body-text> — echoes the problem, returns 1 on failure
# ────────────────────────────────────────────────────────────────
check_body() {
    local body="${1//$'\r'/}" testing line value tier found=0

    testing=$(printf '%s\n' "$body" | awk '
        /^## Testing[[:space:]]*$/ {inside=1; next}
        inside && /^## / {inside=0}
        inside { print }
    ')
    line=$(printf '%s\n' "$testing" | grep -iE '^[[:space:]*-]*Test tiers touched[*]*:' | head -n1 || true)
    if [[ -z "$line" ]]; then
        echo "missing 'Test tiers touched: <tiers>' line in ## Testing"
        return 1
    fi

    # Drop the label, HTML comments, and parentheses: "none (docs only)".
    value=$(printf '%s' "$line" | sed -E 's/^[^:]*:[*]*//; s/<!--.*-->//g; s/\([^)]*\)//g' | tr 'A-Z,' 'a-z ')
    for tier in $value; do
        if [[ " $KNOWN_TIERS " != *" $tier "* ]]; then
            echo "unknown tier '$tier' (allowed: ${KNOWN_TIERS// /, })"
            return 1
        fi
        found=1
    done
    if [[ "$found" == "0" ]]; then
        echo "'Test tiers touched:' names no tier (allowed: ${KNOWN_TIERS// /, })"
        return 1
    fi
    return 0
}

# ────────────────────────────────────────────────────────────────
# Self-test
# ────────────────────────────────────────────────────────────────
if [[ "${HERMES_SELF_TEST:-0}" == "1" ]]; then
    failed=0

    expect() {
        local want="$1" label="$2" body="$3" got=0
        check_body "$body" >/dev/null || got=$?
        if [[ "$got" != "$want" ]]; then
            echo "SELF-TEST FAIL: $label (exit $got, want $want)" >&2
            failed=1
        fi
    }

    expect 0 "single tier" '## Testing
Test tiers touched: unit
## Risk
Low.'

    expect 0 "several tiers, bold label, bullet" '## Testing
- **Test tiers touched:** unit, property, contract
## Risk'

    expect 0 "none with a reason, CRLF" $'## Testing\r\nTest tiers touched: none (docs only)\r\n## Risk\r\n'

    expect 1 "line missing" '## Testing
Ran the suite.
## Risk'

    expect 1 "line outside Testing does not count" '## Summary
Test tiers touched: unit
## Testing
Ran the suite.'

    expect 1 "empty value" '## Testing
Test tiers touched:
## Risk'

    expect 1 "template placeholder left in" '## Testing
Test tiers touched: <unit, property, contract, integration, e2e, mutation or none>
## Risk'

    expect 1 "unfilled PR-template comment" '## Testing
Test tiers touched: <!-- e.g. unit, property -->
## Risk'

    expect 1 "unknown tier" '## Testing
Test tiers touched: unit, acceptance
## Risk'

    if [[ "$failed" == "0" ]]; then
        echo "test-tiers-line-check self-test: all fixtures passed"
    fi
    exit "$failed"
fi

# ────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────
if [[ -z "${PR_BODY:-}" ]]; then
    echo "ERROR: PR_BODY env var is empty" >&2
    exit 2
fi

if ! problem=$(check_body "$PR_BODY"); then
    echo "test-tiers-line-check: $problem" >&2
    echo "Add e.g. 'Test tiers touched: unit, property' under ## Testing (docs/guides/ai-authored-tests.md)." >&2
    exit 1
fi
