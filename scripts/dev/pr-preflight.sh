#!/usr/bin/env bash
# PR pre-flight — mirror the exact suite CI runs, locally, before push.
#
# Prevents "pushed, then CI red" round-trips. Every check here is a check
# CI runs on the PR; if any fails locally, CI will fail too.
#
# Invocation:
#   bash scripts/dev/pr-preflight.sh                          # every CI check, changed-area tests
#   bash scripts/dev/pr-preflight.sh --full-tests             # ... and every test + coverage floor
#   bash scripts/dev/pr-preflight.sh --pre-push               # fast subset (git hook)
#   bash scripts/dev/pr-preflight.sh --body FILE              # + PR body lint
#   bash scripts/dev/pr-preflight.sh --render-body <change>   # render + lint
#   HERMES_SELF_TEST=1 bash scripts/dev/pr-preflight.sh       # sanity check
#
# Wired to git-push via `.pre-commit-config.yaml` `stages: [pre-push]` hook,
# which passes --pre-push: the slow checks CI runs on every head anyway
# (tests, pip-audit, radon, vulture, licenses) are skipped (#416).
#
# The CI checks themselves live in scripts/dev/ci-checks.sh, which CI's
# workflow steps also call (#489).
# One-time install:
#   pre-commit install --hook-type pre-push
#
# Exit codes:
#   0 — every check passed
#   1 — at least one check failed
#   2 — usage error

set -uo pipefail

# ────────────────────────────────────────────────────────────────
# Argument parsing
# ────────────────────────────────────────────────────────────────
BODY_FILE=""
RENDER_CHANGE=""
PRE_PUSH=0
FULL_TESTS=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --body)
            BODY_FILE="${2:-}"
            if [[ -z "$BODY_FILE" || ! -f "$BODY_FILE" ]]; then
                echo "ERROR: --body requires a readable file path" >&2
                exit 2
            fi
            shift 2
            ;;
        --render-body)
            RENDER_CHANGE="${2:-}"
            if [[ -z "$RENDER_CHANGE" ]]; then
                echo "ERROR: --render-body requires a change name" >&2
                exit 2
            fi
            shift 2
            ;;
        --pre-push)
            PRE_PUSH=1
            shift
            ;;
        --full-tests)
            FULL_TESTS=1
            shift
            ;;
        -h|--help)
            sed -n '2,28p' "$0"
            exit 0
            ;;
        *)
            echo "ERROR: unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

# Render mode: generate body from openspec/changes/<name>/change.md +
# issue.md, then treat as --body. Fails early if change.md or issue.md
# is missing. Auto-derives --branch from the current git branch (unless
# on main) so render-pr-body.sh can fall back to `git show origin/<ref>`
# if the local file was removed by the "delete-local-after-open" step
# of the Code Owner's PR discipline.
if [[ -n "$RENDER_CHANGE" ]]; then
    BODY_FILE=$(mktemp -t pr-body.XXXXXX.md)
    echo "▶ Rendering PR body for change '$RENDER_CHANGE'"

    # Determine branch for the remote-fallback arg. Skip on main.
    current_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
    render_args=("$RENDER_CHANGE" -o "$BODY_FILE")
    if [[ -n "$current_branch" && "$current_branch" != "main" ]]; then
        render_args+=(--branch "$current_branch")
    fi

    if ! bash scripts/dev/render-pr-body.sh "${render_args[@]}"; then
        echo "ERROR: render-pr-body.sh failed for '$RENDER_CHANGE'" >&2
        exit 1
    fi
    echo "  ✓ body rendered to $BODY_FILE"
fi

# ────────────────────────────────────────────────────────────────
# Self-test — validate the script's own logic without side-effects
# ────────────────────────────────────────────────────────────────
if [[ "${HERMES_SELF_TEST:-}" == "1" ]]; then
    # Every CI check name this script calls must exist in ci-checks.sh,
    # and every check ci-checks.sh defines must be called here.
    known=$(env -u HERMES_SELF_TEST bash scripts/dev/ci-checks.sh --list)
    for check in $known; do
        if ! grep -qE "ci_check ${check}\$|test_tier ${check}\$" "$0"; then
            echo "SELF-TEST FAIL: CI check '${check}' is never run here" >&2
            exit 1
        fi
    done
    echo "SELF-TEST PASS: pr-preflight.sh runs every CI check in ci-checks.sh."
    exit 0
fi

# ────────────────────────────────────────────────────────────────
# Result tracking
#
# Total check count is CONDITIONAL, by design:
#   - the CI checks from ci-checks.sh, the guides index and the
#     changelog, openspec, project and new-module checks always run.
#   - the per-package coverage floor runs only with --full-tests.
#   - the PR body lint runs ONLY when --body FILE or --render-body
#     <change> is given.
# Not drift — different modes.
# See summary line for the exact per-invocation breakdown.
# ────────────────────────────────────────────────────────────────
FAILED=()
CHECK_COUNT=0

run_check() {
    local name="$1"
    shift
    CHECK_COUNT=$((CHECK_COUNT + 1))
    printf "▶ %s\n" "$name"
    if "$@"; then
        printf "  ✓ pass\n"
    else
        printf "  ✗ FAIL\n"
        FAILED+=("$name")
    fi
}

# Checks CI runs on every head; skipped in --pre-push mode so a push costs
# seconds, not minutes (#416).
SKIPPED=()
run_slow_check() {
    if [[ "$PRE_PUSH" == "1" ]]; then
        printf "▶ %s
  - skipped in pre-push (CI runs it on every head)
" "$1"
        SKIPPED+=("$1")
        return 0
    fi
    run_check "$@"
}

# ────────────────────────────────────────────────────────────────
# Preconditions — the environment must be usable
#
# uv may not be on PATH inside subshells (pre-commit spawns without
# env inheritance on Windows). Probe common install locations before
# giving up.
# ────────────────────────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
    for candidate in \
        "$HOME/AppData/Roaming/Python/Scripts/uv.exe" \
        "$HOME/.local/bin/uv" \
        "$HOME/.cargo/bin/uv" \
        "/usr/local/bin/uv"
    do
        if [[ -x "$candidate" ]]; then
            export PATH="$(dirname "$candidate"):$PATH"
            break
        fi
    done
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv not found on PATH or in common install locations." >&2
    echo "  Windows: uv typically lives at ~/AppData/Roaming/Python/Scripts/uv.exe" >&2
    echo "  Linux/macOS: ~/.local/bin/uv or ~/.cargo/bin/uv" >&2
    echo "  Install: https://github.com/astral-sh/uv" >&2
    exit 2
fi

# ────────────────────────────────────────────────────────────────
# Checks — same commands CI runs, no more no less
# ────────────────────────────────────────────────────────────────

# MUST MATCH create-pr.sh:77 — same TITLE_REGEX; single source of truth.
# If you change one, change both AND update this comment's line number.
TITLE_REGEX='^[a-z]+(\(.+\))?!?:[[:space:]]+[a-z]'

# Check: PR title semantic (conventional-commit + lowercase subject).
# Mirrors amannn/action-semantic-pull-request@v6 subjectPattern. A rebase
# or amend can leave HEAD as a fixup / uppercase subject that create-pr.sh
# accepted at open time but CI rejects, forcing a close+reopen cycle
# (drift observed 2026-08-25 on 'PAT-authenticate' — see #215 Tier-2 defer).
# Bypass with SKIP_TITLE_CHECK=1 (documented for rebase-in-progress paths).
check_pr_title_semantic() {
    if [[ "${SKIP_TITLE_CHECK:-0}" == "1" ]]; then
        echo "  WARN: title check skipped via SKIP_TITLE_CHECK=1" >&2
        return 0
    fi
    local title
    title=$(git log -1 --format=%s HEAD 2>/dev/null)
    if [[ -z "$title" ]]; then
        return 0
    fi
    if [[ "$title" =~ $TITLE_REGEX ]]; then
        return 0
    fi
    {
        echo "ERROR: HEAD commit subject fails the semantic-PR-title rule."
        echo "  title:  $title"
        echo "  regex:  $TITLE_REGEX"
        echo ""
        echo "  CI would reject this via amannn/action-semantic-pull-request@v6:"
        echo "    'Subject may not start with an uppercase character.'"
        echo ""
        echo "  Fix: lowercase the first char after the colon (yes, even proper nouns)."
        echo "  Bypass (rebase in progress): SKIP_TITLE_CHECK=1 bash scripts/dev/pr-preflight.sh"
    } >&2
    return 1
}
run_check "PR title semantic (conventional-commit + lowercase)" check_pr_title_semantic

# Refresh origin/main for the diff-based checks below (changelog, openspec).
# No "base up-to-date" gate: the merge queue owns branch freshness, so a
# stale base no longer blocks a push (#416).
git fetch origin main --quiet || true

# 2-11. The CI checks, run through scripts/dev/ci-checks.sh: the same
# script CI's steps call, so this list and CI cannot drift apart.
#
# Locally the unit and integration tiers run only the tests for the
# changed area (scripts/dev/changed-tests.sh). CI and the merge queue's
# merged-tree gate run every test on every head, so a full local run buys
# little and cost five minutes. --full-tests runs every test here too,
# plus the per-package coverage floor, which needs the full unit run.
#
# SKIP_PYTEST=1 skips the test tiers and the coverage floor. Escape hatch
# for a known flake in an unrelated area; log a reason when using it:
# `SKIP_PYTEST=1  # tracking bug #NNN`.
ci_check() {
    bash scripts/dev/ci-checks.sh "$@"
}
test_tier() {
    if [[ "${SKIP_PYTEST:-0}" == "1" ]]; then
        echo "  WARN: tests skipped via SKIP_PYTEST=1 — known-flake bypass" >&2
        return 0
    fi
    if [[ "$FULL_TESTS" == "1" ]]; then
        ci_check "$1"
    else
        ci_check "$1" --changed origin/main
    fi
}

run_check "format (ruff format --check)"     ci_check format
run_check "lint (ruff check)"                 ci_check lint
run_check "types (mypy --strict)"             ci_check types
run_check "imports (lint-imports)"            ci_check imports
run_check "lockfile hash integrity"           ci_check lockfile
run_check "CODEOWNERS syntax"                 ci_check codeowners
run_check "merge-decisions tests (node)"      ci_check merge-decisions
run_check "markdownlint (full tree)"          ci_check markdown
run_slow_check "deps (pip-audit)"             ci_check deps
run_slow_check "complexity (radon)"           ci_check complexity
run_slow_check "deadcode (vulture)"           ci_check deadcode
run_slow_check "licenses (pip-licenses)"      ci_check licenses
run_slow_check "unit tests"                   test_tier unit
run_slow_check "integration tests"            test_tier integration
if [[ "$FULL_TESTS" == "1" && "${SKIP_PYTEST:-0}" != "1" ]]; then
    run_slow_check "coverage floor (per-package)" ci_check coverage-floor
fi

# 11. Guides index up-to-date (CI regenerates and diffs)
check_guides_index() {
    bash scripts/dev/regenerate-guides-index.sh >/dev/null 2>&1 || return 1
    git diff --exit-code -- docs/guides/README.md >/dev/null 2>&1
}
run_check "Guides index current" check_guides_index

# 12. CHANGELOG check — infers PR title's conventional-commit type from
#     the LATEST commit on the branch (matches what the eventual squash
#     commit will inherit — squash-merge uses the PR title, which is
#     conventionally the last-committed message).
#     Skipped when the branch has no commits ahead of origin/main, or
#     when the inferred type is docs/chore/ci/build/test/revert (which
#     changelog-check.sh itself skips anyway).
check_changelog_if_applicable() {
    # Latest commit's subject line — the type prefix drives the check.
    local latest_subject
    latest_subject=$(git log -1 --format=%s HEAD 2>/dev/null)
    if [[ -z "$latest_subject" ]]; then
        return 0  # empty branch — nothing to check
    fi

    # Only feat/fix/refactor/perf require a CHANGELOG entry.
    if ! [[ "$latest_subject" =~ ^(feat|fix|refactor|perf)(\(.+\))?(!)?: ]]; then
        return 0  # docs/chore/ci/build/test/revert — skip
    fi

    # Base SHA for the diff — origin/main HEAD as of last fetch.
    # (origin/main was refreshed above.)
    local base_sha head_sha
    base_sha=$(git rev-parse origin/main 2>/dev/null)
    head_sha=$(git rev-parse HEAD)
    if [[ "$base_sha" == "$head_sha" ]]; then
        return 0  # branch at main — no diff to check
    fi

    PR_TITLE="$latest_subject" BASE_SHA="$base_sha" HEAD_SHA="$head_sha" \
        bash scripts/dev/changelog-check.sh >/dev/null 2>&1
}
run_check "CHANGELOG check (inferred from latest commit)" check_changelog_if_applicable

# 13. Openspec change spec present when the PR's conventional-commit type
#     implies a material change (feat|refactor|perf). Coarse heuristic
#     — false positives (small feat) bypass via SKIP_OPENSPEC_CHECK=1
#     with a logged reason. False negatives (large fix) caught in code
#     review. See #194 for the DoR/DoD rationale that motivates this.
# Check: tracking-issue project assignment (opt-in).
#
# For every openspec change modified on this branch, resolve its
# tracking issue via issue.md and verify the issue is attached to at
# least one GitHub Project. Ship-validation gap surfaced on #242 —
# operator caught PR #242 slipping through without a project attached.
#
# OPT-IN via HERMES_ENFORCE_PROJECT_CHECK=1. Default OFF because
# until every open tracking issue is attached to a project AND local
# operators have `read:project` scope, this check would fail-hard on
# every current tracking issue (`projectItems` returns `[]` both when
# no project is attached AND when the scope is missing — the two
# cases are indistinguishable to the caller). Flip the env var on
# once open issues are bulk-attached and the scope is granted.
#
# Bypass (when opt-in is on): SKIP_PROJECT_CHECK=1.
check_pr_project_assigned() {
    if [[ "${HERMES_ENFORCE_PROJECT_CHECK:-0}" != "1" ]]; then
        echo "  SKIP: enforcement OFF (set HERMES_ENFORCE_PROJECT_CHECK=1 to enable)" >&2
        return 0
    fi
    if [[ "${SKIP_PROJECT_CHECK:-0}" == "1" ]]; then
        echo "  WARN: project check skipped via SKIP_PROJECT_CHECK=1" >&2
        return 0
    fi
    local changes
    changes=$(git diff --name-only origin/main..HEAD -- 'openspec/changes/*/change.md' 2>/dev/null)
    if [[ -z "$changes" ]]; then
        # No openspec change on this branch — nothing to check.
        return 0
    fi

    local any_fail=0
    while IFS= read -r cf; do
        [[ -z "$cf" ]] && continue
        local change_dir issue_md issue_num count
        change_dir=$(dirname "$cf")
        issue_md="$change_dir/issue.md"
        [[ -f "$issue_md" ]] || continue
        issue_num=$(grep -oE '^issue:[[:space:]]*[0-9]+' "$issue_md" \
                    | grep -oE '[0-9]+' | head -1)
        [[ -z "$issue_num" ]] && continue

        count=$(gh issue view "$issue_num" --json projectItems \
                    --jq '.projectItems | length' 2>&1)
        if [[ "$count" == *INSUFFICIENT_SCOPES* ]] || [[ "$count" == *"required scopes"* ]]; then
            echo "  WARN: read:project scope missing — cannot verify project attachment for #$issue_num" >&2
            continue
        fi
        if ! [[ "$count" =~ ^[0-9]+$ ]]; then
            echo "  WARN: unexpected gh output for #$issue_num: ${count:0:120}" >&2
            continue
        fi
        if [[ "$count" -eq 0 ]]; then
            {
                echo "ERROR: tracking issue #$issue_num for change '$(basename "$change_dir")' has no project attached."
                echo "  Attach via the GitHub web UI (Projects tab on the issue) or:"
                echo "    gh api graphql -f query='mutation { addProjectV2ItemById(input: {projectId: \"...\", contentId: \"...\"}) { item { id } } }'"
                echo "  Bypass (with cause): SKIP_PROJECT_CHECK=1 bash scripts/dev/pr-preflight.sh"
            } >&2
            any_fail=1
        fi
    done <<< "$changes"

    return $any_fail
}
run_check "Tracking-issue project assignment (opt-in)" check_pr_project_assigned

check_openspec_change_required() {
    if [[ "${SKIP_OPENSPEC_CHECK:-0}" == "1" ]]; then
        echo "  WARN: openspec check skipped via SKIP_OPENSPEC_CHECK=1" >&2
        return 0
    fi
    local latest_subject
    latest_subject=$(git log -1 --format=%s HEAD 2>/dev/null)
    if [[ -z "$latest_subject" ]]; then
        return 0  # empty branch — nothing to check
    fi
    if ! [[ "$latest_subject" =~ ^(feat|refactor|perf)(\(.+\))?(!)?: ]]; then
        return 0  # docs/chore/ci/build/test/fix/revert — exempt
    fi
    local added_or_modified
    added_or_modified=$(git diff --name-only origin/main..HEAD -- \
        'openspec/changes/*/change.md' 2>/dev/null)
    if [[ -z "$added_or_modified" ]]; then
        {
            echo "ERROR: feat/refactor/perf PR requires an openspec change spec."
            echo "  Latest commit: $latest_subject"
            echo "  Expected: openspec/changes/<change-name>/change.md added or modified in this branch."
            echo "  Template: openspec/changes/TEMPLATE/change.md"
            echo "  Bypass (with cause): SKIP_OPENSPEC_CHECK=1 bash scripts/dev/pr-preflight.sh"
            echo "  Rationale: #194 DoR/DoD hardening."
        } >&2
        return 1
    fi
    return 0
}
run_check "Openspec change spec present (DoR)" check_openspec_change_required

# 15. New src modules carry a matching tests/unit test file — the
#     branch-wide twin of the test-file-adjacency pre-commit hook (#160).
run_check "New modules have test files (vs origin/main)" \
    bash scripts/dev/test-file-adjacency.sh --base origin/main

# 13. Optional — PR body lint + test-tier line (only when --body FILE given)
if [[ -n "$BODY_FILE" ]]; then
    check_pr_body() {
        PR_BODY="$(cat "$BODY_FILE")" bash scripts/dev/pr-body-lint.sh &&
            PR_BODY="$(cat "$BODY_FILE")" bash scripts/dev/test-tiers-line-check.sh
    }
    run_check "PR body lint (--body $BODY_FILE)" check_pr_body
fi

# ────────────────────────────────────────────────────────────────
# Summary
#
# The check count depends on invocation flags (see "Result tracking" comment
# above). The mode string makes the count discoverable so operators do not
# mistake it for silent drift between runs (drift-eval 2026-08-22, wave 3).
# ────────────────────────────────────────────────────────────────
if [[ -n "$BODY_FILE" ]]; then
    MODE_DESC="with PR body lint"
else
    MODE_DESC="without PR body lint — pass --body FILE or --render-body <change> to add it"
fi

if [[ "$PRE_PUSH" == "1" ]]; then
    MODE_DESC="pre-push, ${#SKIPPED[@]} slow checks left to CI; ${MODE_DESC}"
elif [[ "$FULL_TESTS" == "1" ]]; then
    MODE_DESC="every test; ${MODE_DESC}"
else
    MODE_DESC="changed-area tests, CI runs the rest; ${MODE_DESC}"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [[ ${#FAILED[@]} -eq 0 ]]; then
    echo "✓ ALL ${CHECK_COUNT} CHECKS PASS (${MODE_DESC}) — safe to push."
    # The stamp scripts/dev/pr-ready-check.sh requires before a PR opens:
    # only a full run on a clean tree proves this exact HEAD.
    if [[ "$PRE_PUSH" != "1" && -z "$(git status --porcelain)" ]]; then
        git rev-parse HEAD > "$(git rev-parse --git-dir)/hermes-preflight-pass"
    fi
    exit 0
else
    echo "✗ ${#FAILED[@]} of ${CHECK_COUNT} CHECKS FAILED (${MODE_DESC}):"
    printf '   - %s\n' "${FAILED[@]}"
    echo ""
    echo "Push blocked. Fix the failures above, then re-run:"
    echo "  bash scripts/dev/pr-preflight.sh"
    exit 1
fi
