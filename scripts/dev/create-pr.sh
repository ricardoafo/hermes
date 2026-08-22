#!/usr/bin/env bash
# Thin wrapper around `gh pr create` that enforces PR body content.
#
# Refuses to open the PR if the body file fails pr-body-lint.sh. This
# closes the "opened PR with placeholder body" drift class (drift-eval
# #459, drift class 1). AGENTS.md § When SDD applies mandates using
# this wrapper — do NOT bypass with `gh pr create` directly.
#
# Orchestrator applier prompts must invoke this wrapper, not `gh pr create`
# directly.
#
# Invocation:
#   bash scripts/dev/create-pr.sh --title <t> --body-file <path> [passthrough]
#   HERMES_SELF_TEST=1 bash scripts/dev/create-pr.sh
#
# Passthrough allowlist: --head, --base, --draft, --reviewer
# Anything else → exit 2 (usage error, unknown flag).
#
# Env vars:
#   HERMES_SELF_TEST  — set to 1 to run built-in fixtures + exit
#   GH_CMD            — override for `gh` binary; self-test sets to `echo`
#                       so tests never call real GitHub.
#
# Exit codes:
#   0 — PR created successfully (or self-test passed)
#   1 — PR body failed lint (gh NOT invoked)
#   2 — usage / arg error

set -uo pipefail

TITLE=""
BODY_FILE=""
PASSTHROUGH=()
ALLOWED_PASS=(--head --base --draft --reviewer)

is_allowed_flag() {
    local flag="$1"
    for a in "${ALLOWED_PASS[@]}"; do [[ "$a" == "$flag" ]] && return 0; done
    return 1
}

# ────────────────────────────────────────────────────────────────
# Self-test — validate the script's own logic without side-effects
# ────────────────────────────────────────────────────────────────
if [[ "${HERMES_SELF_TEST:-0}" == "1" ]]; then
    fail=0
    tmp_ok=$(mktemp -t create-pr-ok.XXXXXX.md)
    tmp_bad=$(mktemp -t create-pr-bad.XXXXXX.md)

    # Fixture body that passes pr-body-lint (4 mandatory sections filled)
    cat > "$tmp_ok" <<'EOF'
## Summary
Real summary content.
## Related
- Issue: Closes #1
## Testing
Real testing steps.
## Risk
Low.
EOF

    # Fixture body that fails pr-body-lint (placeholder-only)
    cat > "$tmp_bad" <<'EOF'
## Summary
<!-- placeholder -->
## Related
- ADR: <!-- ph -->
## Testing
<!-- ph -->
## Risk
<!-- ph -->
EOF

    # 1. PASS: valid body, gh stubbed → exit 0, echo captures gh args
    out=$(GH_CMD=echo HERMES_SELF_TEST=0 bash "$0" --title "T1" --body-file "$tmp_ok" 2>&1); rc=$?
    if [[ $rc -ne 0 ]]; then echo "SELF-TEST FAIL: valid body → rc=$rc, want 0"; fail=1; fi
    grep -q 'pr create' <<< "$out" || { echo "SELF-TEST FAIL: valid body should invoke stubbed gh"; fail=1; }
    grep -q -- '--title T1' <<< "$out" || { echo "SELF-TEST FAIL: --title passthrough missing"; fail=1; }

    # 2. PASS: passthrough --draft + --reviewer
    out=$(GH_CMD=echo HERMES_SELF_TEST=0 bash "$0" --title "T2" --body-file "$tmp_ok" --draft --reviewer alice 2>&1); rc=$?
    if [[ $rc -ne 0 ]]; then echo "SELF-TEST FAIL: passthrough → rc=$rc"; fail=1; fi
    grep -q -- '--draft' <<< "$out" || { echo "SELF-TEST FAIL: --draft not forwarded"; fail=1; }
    grep -q -- '--reviewer alice' <<< "$out" || { echo "SELF-TEST FAIL: --reviewer not forwarded"; fail=1; }

    # 3. PASS: --head + --base passthrough
    out=$(GH_CMD=echo HERMES_SELF_TEST=0 bash "$0" --title "T3" --body-file "$tmp_ok" --head feat --base main 2>&1); rc=$?
    [[ $rc -eq 0 ]] || { echo "SELF-TEST FAIL: --head/--base rc=$rc"; fail=1; }

    # 4. FAIL: bad body → exit 1, gh NOT invoked
    out=$(GH_CMD=echo HERMES_SELF_TEST=0 bash "$0" --title "T4" --body-file "$tmp_bad" 2>&1); rc=$?
    [[ $rc -eq 1 ]] || { echo "SELF-TEST FAIL: bad body → rc=$rc, want 1"; fail=1; }
    grep -q 'pr create' <<< "$out" && { echo "SELF-TEST FAIL: gh MUST NOT be invoked on lint fail"; fail=1; }

    # 5. FAIL: missing --body-file arg → exit 2
    out=$(HERMES_SELF_TEST=0 bash "$0" --title "T5" 2>&1); rc=$?
    [[ $rc -eq 2 ]] || { echo "SELF-TEST FAIL: missing --body-file → rc=$rc, want 2"; fail=1; }

    # 6. FAIL: nonexistent body file → exit 2 (before lint runs)
    out=$(HERMES_SELF_TEST=0 bash "$0" --title "T6" --body-file "/nonexistent/xyz.md" 2>&1); rc=$?
    [[ $rc -eq 2 ]] || { echo "SELF-TEST FAIL: nonexistent body-file → rc=$rc, want 2"; fail=1; }

    # 7. FAIL: unknown flag → exit 2
    out=$(HERMES_SELF_TEST=0 bash "$0" --title "T7" --body-file "$tmp_ok" --project 99 2>&1); rc=$?
    [[ $rc -eq 2 ]] || { echo "SELF-TEST FAIL: unknown flag → rc=$rc, want 2"; fail=1; }
    grep -q "unknown flag" <<< "$out" || { echo "SELF-TEST FAIL: expected 'unknown flag' in stderr"; fail=1; }

    # 8. FAIL: missing --title → exit 2
    out=$(HERMES_SELF_TEST=0 bash "$0" --body-file "$tmp_ok" 2>&1); rc=$?
    [[ $rc -eq 2 ]] || { echo "SELF-TEST FAIL: missing --title → rc=$rc, want 2"; fail=1; }

    rm -f "$tmp_ok" "$tmp_bad"

    if [[ $fail -eq 0 ]]; then
        echo "SELF-TEST PASS: create-pr.sh handles 8 fixtures correctly."
        exit 0
    fi
    exit 1
fi

# ────────────────────────────────────────────────────────────────
# Argument parsing
# ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --title)
            TITLE="${2:-}"
            [[ -z "$TITLE" ]] && { echo "ERROR: --title requires a value" >&2; exit 2; }
            shift 2 ;;
        --body-file)
            BODY_FILE="${2:-}"
            [[ -z "$BODY_FILE" ]] && { echo "ERROR: --body-file requires a value" >&2; exit 2; }
            shift 2 ;;
        --draft)
            PASSTHROUGH+=("$1"); shift ;;
        --head|--base|--reviewer)
            PASSTHROUGH+=("$1" "${2:-}"); shift 2 ;;
        -h|--help)
            sed -n '2,26p' "$0"; exit 0 ;;
        *)
            echo "ERROR: unknown flag '$1' (allowlist: --title, --body-file, ${ALLOWED_PASS[*]})" >&2
            exit 2 ;;
    esac
done

# ────────────────────────────────────────────────────────────────
# Required-args check
# ────────────────────────────────────────────────────────────────
[[ -z "$TITLE" ]] && { echo "ERROR: --title is required" >&2; exit 2; }
[[ -z "$BODY_FILE" ]] && { echo "ERROR: --body-file is required" >&2; exit 2; }
[[ -f "$BODY_FILE" ]] || { echo "ERROR: body file not found: $BODY_FILE" >&2; exit 2; }

# ────────────────────────────────────────────────────────────────
# Lint gate — refuse to open the PR if body fails pr-body-lint.sh
# ────────────────────────────────────────────────────────────────
if ! PR_BODY="$(cat "$BODY_FILE")" bash scripts/dev/pr-body-lint.sh; then
    echo "ERROR: pr-body-lint.sh rejected $BODY_FILE — PR NOT opened." >&2
    exit 1
fi

# ────────────────────────────────────────────────────────────────
# Lint passed — invoke gh (or GH_CMD stub for self-test)
# ────────────────────────────────────────────────────────────────
GH="${GH_CMD:-gh}"
$GH pr create --title "$TITLE" --body "$(cat "$BODY_FILE")" "${PASSTHROUGH[@]}"
