#!/usr/bin/env bash
# CI success aggregator — the required status check that branch protection
# gates on. Passes iff every needs-job's result is `success` or `skipped`.
#
# ## The bug this replaces
#
# The previous inline shell used:
#   grep -qE '"result":"(failure|cancelled)"'
# which failed to match GH-Actions' pretty-printed JSON output:
#   "result": "failure"       (with space after `:`)
# The regex expected `"result":"failure"` (no space) and never matched.
# Result: ci-success was reporting SUCCESS on every run regardless of
# actual failures. PRs #72 and #73 both merged with red checks under
# this false-pass.
#
# ## Design
#
# Uses Python for proper JSON parsing (baked in both the runner image and
# the dev machine via uv-managed 3.14). Positive-list allowlist
# ({success, skipped}) means any future GH-Actions result state
# ("action_required", "startup_failure", …) is caught by default — safer
# than a negative denylist that must chase new failure states.
#
# ## Invocation
#
#   NEEDS_JSON='...' bash scripts/dev/ci-success-check.sh    # production
#   HERMES_SELF_TEST=1 bash scripts/dev/ci-success-check.sh  # sanity check
#
# ## Exit codes
#   0 — every needs-job result in {success, skipped}
#   1 — at least one needs-job result outside the allowlist
#   2 — usage error (missing NEEDS_JSON in non-self-test mode)

set -uo pipefail

# ────────────────────────────────────────────────────────────────
# Pure function: check_results <json>
# Prints newline-separated "job: result" pairs for any result NOT
# in the allowed set. Empty output = all-pass.
# ────────────────────────────────────────────────────────────────
check_results() {
    local json="$1"
    printf '%s\n' "$json" | python3 -c '
import json, sys
data = json.load(sys.stdin)
allowed = {"success", "skipped"}
for job, info in data.items():
    result = info.get("result", "unknown")
    if result not in allowed:
        print(f"{job}: {result}")
'
}

# ────────────────────────────────────────────────────────────────
# Self-test — run 5 canonical fixtures, exit 0 iff every one matches
# the expected outcome. Prevents this script from silently rotting.
# ────────────────────────────────────────────────────────────────
if [[ "${HERMES_SELF_TEST:-}" == "1" ]]; then
    if ! command -v python3 >/dev/null 2>&1; then
        echo "SELF-TEST FAIL: python3 is required (baked in runner image + dev machine)" >&2
        exit 1
    fi

    fail=0

    # Fixture 1: everything success → no non-passing jobs
    out=$(check_results '{"a":{"result":"success"},"b":{"result":"success"}}')
    if [[ -n "$out" ]]; then
        echo "SELF-TEST FAIL: all-success expected empty, got: $out" >&2
        fail=1
    fi

    # Fixture 2: one failure → surfaces exactly that failure
    out=$(check_results '{"a":{"result":"success"},"b":{"result":"failure"}}')
    if [[ "$out" != "b: failure" ]]; then
        echo "SELF-TEST FAIL: one-failure expected 'b: failure', got: $out" >&2
        fail=1
    fi

    # Fixture 3: THE ORIGINAL BUG — space after colon in JSON
    # This was the exact shape that made grep miss the failure.
    out=$(check_results '{"markdown-lint": {"result": "failure"}, "quality": {"result": "success"}}')
    if [[ "$out" != "markdown-lint: failure" ]]; then
        echo "SELF-TEST FAIL: pretty-printed JSON regression, got: $out" >&2
        fail=1
    fi

    # Fixture 4: skipped is legit (jobs behind if-conditionals) → pass
    out=$(check_results '{"a":{"result":"success"},"b":{"result":"skipped"}}')
    if [[ -n "$out" ]]; then
        echo "SELF-TEST FAIL: skipped-is-ok expected empty, got: $out" >&2
        fail=1
    fi

    # Fixture 5: cancelled → surfaces as failure
    out=$(check_results '{"a":{"result":"success"},"b":{"result":"cancelled"}}')
    if [[ "$out" != "b: cancelled" ]]; then
        echo "SELF-TEST FAIL: cancelled-detected expected 'b: cancelled', got: $out" >&2
        fail=1
    fi

    if [[ "$fail" -eq 0 ]]; then
        echo "SELF-TEST PASS: ci-success-check.sh handles 5 fixtures correctly."
        exit 0
    fi
    exit 1
fi

# ────────────────────────────────────────────────────────────────
# Production path
# ────────────────────────────────────────────────────────────────
: "${NEEDS_JSON:?NEEDS_JSON is required. Pass via env from '\${{ toJSON(needs) }}'.}"

echo "Aggregating needs context..."
echo "$NEEDS_JSON"
echo ""

failures=$(check_results "$NEEDS_JSON")

if [[ -z "$failures" ]]; then
    echo "All required CI gates passed."
    exit 0
fi

echo "::error::CI gates failed or in unexpected state:"
printf '  - %s\n' $(echo "$failures" | sed 's/: /\t/') | column -t -s $'\t'
exit 1
