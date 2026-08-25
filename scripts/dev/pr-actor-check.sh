#!/usr/bin/env bash
# PR author-vs-reviewer separation gate — enforces AGENTS.md rules 12-13.
#
# Refuses to open a PR when the gh-authenticated user matches the
# intended reviewer. The two-account discipline is:
#   * `rafo-claude-bot` opens PRs (the bot / AI identity)
#   * `ricardoafo` reviews and approves (the human operator)
#
# Author self-review is a silent deadlock — GitHub does not report a
# clear error at PR-open time; the deadlock only surfaces when the
# author tries to click Approve and gets refused. This gate catches
# it up-front.
#
# Author read: `gh api user --jq .login` (primary), `gh auth status
# --active` parser (fallback). Both are covered by the self-test.
#
# Reviewer resolution order:
#   1. `--reviewer <handle>` command-line arg
#   2. `HERMES_DEFAULT_REVIEWER` env var
#   3. Hardcoded fallback: `ricardoafo`
#
# Called by:
#   * scripts/dev/create-pr.sh — after the openspec-change gate,
#     before ensure_body_outside_repo
#   * manually: bash scripts/dev/pr-actor-check.sh --dry-run \
#       --author <login> --reviewer <login>
#
# Env vars:
#   HERMES_DEFAULT_REVIEWER  — fallback reviewer login (default: ricardoafo)
#   SKIP_ACTOR_CHECK         — set to 1 to bypass with a warning
#   HERMES_SELF_TEST         — set to 1 to run fixture matrix + exit
#   HERMES_ACTOR_OVERRIDE    — debugging override for the author read
#                              (bypasses gh calls entirely — useful when
#                              PowerShell's credential-manager caches a
#                              different account than the active gh CLI)
#
# Exit codes:
#   0 — actor and reviewer differ (ok)
#   1 — actor == reviewer (self-review conflict, blocks PR-open)
#   2 — usage error (bad flag combo)
#   3 — gh auth read failed (network or auth missing)

set -uo pipefail

# Read at call time (not init) so the self-test can flip
# HERMES_DEFAULT_REVIEWER per fixture without re-sourcing.
DEFAULT_REVIEWER_FALLBACK="ricardoafo"

# ────────────────────────────────────────────────────────────────
# Author read — primary via `gh api user`, fallback via `gh auth status`
# ────────────────────────────────────────────────────────────────
read_author() {
    if [[ -n "${HERMES_ACTOR_OVERRIDE:-}" ]]; then
        printf '%s' "$HERMES_ACTOR_OVERRIDE"
        return 0
    fi
    # Primary: `gh api user --jq .login` — stable output format, one line.
    local login
    login=$(gh api user --jq .login 2>/dev/null)
    if [[ -n "$login" ]]; then
        printf '%s' "$login"
        return 0
    fi
    # Fallback: parse `gh auth status --active`. Format:
    #   ✓ Logged in to github.com account ricardoafo (keyring)
    login=$(gh auth status --active 2>&1 | \
        awk '/Logged in to github.com account/ {print $6; exit}')
    if [[ -n "$login" ]]; then
        printf '%s' "$login"
        return 0
    fi
    return 1
}

# ────────────────────────────────────────────────────────────────
# Reviewer resolution — arg > env > hardcoded fallback
# ────────────────────────────────────────────────────────────────
resolve_reviewer() {
    local from_arg="$1"
    if [[ -n "$from_arg" ]]; then
        printf '%s' "$from_arg"
        return 0
    fi
    printf '%s' "${HERMES_DEFAULT_REVIEWER:-$DEFAULT_REVIEWER_FALLBACK}"
}

# ────────────────────────────────────────────────────────────────
# Main check — refuses when author == reviewer
# ────────────────────────────────────────────────────────────────
check_actor() {
    local author="$1"
    local reviewer="$2"
    if [[ "$author" == "$reviewer" ]]; then
        {
            echo "ERROR: PR author cannot self-review."
            echo "  Author (gh active): $author"
            echo "  Reviewer:           $reviewer"
            echo ""
            echo "  This repo enforces two-account discipline (AGENTS.md 12-13):"
            echo "    * rafo-claude-bot opens PRs"
            echo "    * ricardoafo reviews and approves"
            echo ""
            echo "  Fix (typical): gh auth switch --user rafo-claude-bot"
            echo "  Bypass (with cause): SKIP_ACTOR_CHECK=1 bash scripts/dev/create-pr.sh ..."
        } >&2
        return 1
    fi
    return 0
}

# ────────────────────────────────────────────────────────────────
# Self-test — fixture matrix, exit 0 on pass, 1 on any fail
# ────────────────────────────────────────────────────────────────
if [[ "${HERMES_SELF_TEST:-0}" == "1" ]]; then
    fail=0

    # Fixture 1: author == reviewer → exit 1
    HERMES_ACTOR_OVERRIDE=ricardoafo out=$(check_actor "ricardoafo" "ricardoafo" 2>&1); rc=$?
    [[ $rc -eq 1 ]] || { echo "SELF-TEST FAIL 1: self-review → rc=$rc, want 1"; fail=1; }
    grep -q "cannot self-review" <<< "$out" || { echo "SELF-TEST FAIL 1: expected 'cannot self-review'"; fail=1; }

    # Fixture 2: author != reviewer → exit 0
    out=$(check_actor "rafo-claude-bot" "ricardoafo" 2>&1); rc=$?
    [[ $rc -eq 0 ]] || { echo "SELF-TEST FAIL 2: bot vs human → rc=$rc, want 0"; fail=1; }

    # Fixture 3: HERMES_ACTOR_OVERRIDE bypasses gh calls
    out=$(HERMES_ACTOR_OVERRIDE=alice read_author 2>&1); rc=$?
    [[ "$out" == "alice" ]] || { echo "SELF-TEST FAIL 3: override read → got '$out', want 'alice'"; fail=1; }

    # Fixture 4: reviewer resolution — arg wins over env
    HERMES_DEFAULT_REVIEWER=bob out=$(resolve_reviewer "carol"); \
        [[ "$out" == "carol" ]] || { echo "SELF-TEST FAIL 4: arg > env → got '$out', want 'carol'"; fail=1; }

    # Fixture 5: reviewer resolution — env wins over fallback
    HERMES_DEFAULT_REVIEWER=bob out=$(resolve_reviewer ""); \
        [[ "$out" == "bob" ]] || { echo "SELF-TEST FAIL 5: env > fallback → got '$out', want 'bob'"; fail=1; }

    # Fixture 6: reviewer resolution — hardcoded fallback
    unset HERMES_DEFAULT_REVIEWER 2>/dev/null || true
    out=$(resolve_reviewer ""); \
        [[ "$out" == "ricardoafo" ]] || { echo "SELF-TEST FAIL 6: fallback → got '$out', want 'ricardoafo'"; fail=1; }

    # Fixture 7: SKIP_ACTOR_CHECK=1 short-circuits at top of script.
    # `env -u HERMES_SELF_TEST` prevents infinite recursion — otherwise
    # the inherited HERMES_SELF_TEST=1 re-enters this block.
    out=$(env -u HERMES_SELF_TEST SKIP_ACTOR_CHECK=1 \
        bash "$0" --dry-run --author ricardoafo --reviewer ricardoafo 2>&1); rc=$?
    [[ $rc -eq 0 ]] || { echo "SELF-TEST FAIL 7: skip flag → rc=$rc, want 0"; fail=1; }
    grep -q "actor check skipped" <<< "$out" || { echo "SELF-TEST FAIL 7: expected skip warning"; fail=1; }

    if [[ $fail -eq 0 ]]; then
        echo "SELF-TEST PASS: pr-actor-check.sh handles 7 fixtures correctly."
        exit 0
    fi
    exit 1
fi

# ────────────────────────────────────────────────────────────────
# Argument parsing
# ────────────────────────────────────────────────────────────────
DRY_RUN=0
CLI_AUTHOR=""
CLI_REVIEWER=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --author)  CLI_AUTHOR="$2"; shift 2 ;;
        --reviewer) CLI_REVIEWER="$2"; shift 2 ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -50
            exit 0 ;;
        *)
            echo "ERROR: unknown flag: $1" >&2
            echo "Usage: $0 [--dry-run] [--author <login>] [--reviewer <login>]" >&2
            exit 2 ;;
    esac
done

# ────────────────────────────────────────────────────────────────
# Escape hatch
# ────────────────────────────────────────────────────────────────
if [[ "${SKIP_ACTOR_CHECK:-0}" == "1" ]]; then
    echo "WARN: actor check skipped via SKIP_ACTOR_CHECK=1" >&2
    exit 0
fi

# ────────────────────────────────────────────────────────────────
# Author read (dry-run uses --author arg; production calls gh)
# ────────────────────────────────────────────────────────────────
if [[ $DRY_RUN -eq 1 && -n "$CLI_AUTHOR" ]]; then
    AUTHOR="$CLI_AUTHOR"
else
    AUTHOR=$(read_author)
    if [[ -z "$AUTHOR" ]]; then
        echo "ERROR: could not read gh-authenticated user (gh api user + gh auth status both failed)" >&2
        echo "  Try: gh auth login" >&2
        echo "  Or debug: HERMES_ACTOR_OVERRIDE=<login> $0 ..." >&2
        exit 3
    fi
fi

REVIEWER=$(resolve_reviewer "$CLI_REVIEWER")

check_actor "$AUTHOR" "$REVIEWER"
