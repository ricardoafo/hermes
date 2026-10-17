#!/usr/bin/env bash
# Pre-commit hook enforcing openspec change spec presence for
# feat/refactor/perf branches. Fires before every commit; catches the
# earliest possible violation of the DoR/DoD discipline (#194).
#
# Predicate:
#   Current branch name starts with feat/, refactor/, or perf/ AND
#   neither the branch's diff vs origin/main nor the currently-staged
#   files include any `openspec/changes/*/change.md` addition or
#   modification → reject, exit 1.
#
# Escape hatch: SKIP_OPENSPEC_CHECK=1 env var (matches pr-preflight.sh
# and create-pr.sh). The reason SHOULD be logged in the commit body but
# is not machine-enforced here (the pr-preflight + create-pr layers
# require it at push/PR time).
#
# Two other layers catch bypasses of this hook:
#   * scripts/dev/create-pr.sh — checks conventional-commit type of the
#     latest commit at PR-open time; exit 3 if no openspec change.
#   * scripts/dev/pr-preflight.sh check #13 — same predicate as
#     create-pr.sh, runs on every push.
#
# Why branch name (not commit-message type)?
#   Pre-commit fires BEFORE the commit message is written, so the
#   conventional-commit prefix isn't available. Branch name is the
#   earliest signal available at commit time and matches this repo's
#   naming discipline (`feat/foo`, `refactor/bar`, `perf/baz`).
#
# Invocation:
#   bash scripts/dev/openspec-change-required.sh          # normal (pre-commit)
#   HERMES_SELF_TEST=1 bash scripts/dev/openspec-change-required.sh
#
# Called by: .pre-commit-config.yaml (stages: [pre-commit], always_run: true)
#
# Exit codes:
#   0 — exempt or spec present
#   1 — feat/refactor/perf branch missing openspec change spec (blocks commit)

set -uo pipefail

# ────────────────────────────────────────────────────────────────
# Self-test — validate the predicate against controlled fixtures
# ────────────────────────────────────────────────────────────────
if [[ "${HERMES_SELF_TEST:-0}" == "1" ]]; then
    fail=0
    # Absolute path to this script so tmpdir invocations work regardless
    # of CWD (git-bash on Windows: $0 is often relative).
    SCRIPT=$(cd "$(dirname "$0")" && printf '%s/%s' "$(pwd)" "$(basename "$0")")
    tmpdir=$(mktemp -d -t openspec-hook-selftest.XXXXXX)
    (
        cd "$tmpdir" || exit 1
        git init -q
        git commit -q --allow-empty -m "chore: initial"
        git update-ref refs/remotes/origin/main HEAD
    ) >/dev/null 2>&1

    # Fixture 1: branch 'main' → exempt, exit 0
    (
        cd "$tmpdir" || exit 1
        git checkout -q main 2>/dev/null || git checkout -q -b main
    )
    out=$(cd "$tmpdir" && env -u HERMES_SELF_TEST bash "$SCRIPT" 2>&1); rc=$?
    [[ $rc -eq 0 ]] || { echo "SELF-TEST FAIL 1: main branch → rc=$rc, want 0"; fail=1; }

    # Fixture 2: 'chore/*' branch → exempt, exit 0
    (
        cd "$tmpdir" || exit 1
        git checkout -q -b chore/foo
    ) 2>/dev/null
    out=$(cd "$tmpdir" && env -u HERMES_SELF_TEST bash "$SCRIPT" 2>&1); rc=$?
    [[ $rc -eq 0 ]] || { echo "SELF-TEST FAIL 2: chore/ branch → rc=$rc, want 0"; fail=1; }

    # Fixture 3: 'feat/*' branch, no openspec change → exit 1
    (
        cd "$tmpdir" || exit 1
        git checkout -q -b feat/x
    ) 2>/dev/null
    out=$(cd "$tmpdir" && env -u HERMES_SELF_TEST bash "$SCRIPT" 2>&1); rc=$?
    [[ $rc -eq 1 ]] || { echo "SELF-TEST FAIL 3: feat/ no spec → rc=$rc, want 1"; fail=1; }
    grep -qi 'openspec' <<< "$out" || { echo "SELF-TEST FAIL 3: expected 'openspec' in stderr"; fail=1; }

    # Fixture 4: 'feat/*' branch with SKIP_OPENSPEC_CHECK=1 → exit 0
    out=$(cd "$tmpdir" && SKIP_OPENSPEC_CHECK=1 env -u HERMES_SELF_TEST bash "$SCRIPT" 2>&1); rc=$?
    [[ $rc -eq 0 ]] || { echo "SELF-TEST FAIL 4: skip flag → rc=$rc, want 0"; fail=1; }
    grep -q 'openspec check skipped' <<< "$out" || { echo "SELF-TEST FAIL 4: expected skip warning"; fail=1; }

    # Fixture 5: 'feat/*' branch with staged openspec change → exit 0
    (
        cd "$tmpdir" || exit 1
        mkdir -p openspec/changes/x-change
        echo "# Change: x-change" > openspec/changes/x-change/change.md
        git add openspec/changes/x-change/change.md
    ) 2>/dev/null
    out=$(cd "$tmpdir" && env -u HERMES_SELF_TEST bash "$SCRIPT" 2>&1); rc=$?
    [[ $rc -eq 0 ]] || { echo "SELF-TEST FAIL 5: feat/ + staged spec → rc=$rc, want 0"; fail=1; }

    # Fixture 6: 'refactor/*' branch, no openspec change → exit 1
    (
        cd "$tmpdir" || exit 1
        git reset -q HEAD -- openspec/changes/x-change/change.md
        rm -rf openspec/
        git checkout -q -b refactor/y
    ) 2>/dev/null
    out=$(cd "$tmpdir" && env -u HERMES_SELF_TEST bash "$SCRIPT" 2>&1); rc=$?
    [[ $rc -eq 1 ]] || { echo "SELF-TEST FAIL 6: refactor/ no spec → rc=$rc, want 1"; fail=1; }

    # Fixture 7: 'perf/*' branch, no openspec change → exit 1
    (
        cd "$tmpdir" || exit 1
        git checkout -q -b perf/z
    ) 2>/dev/null
    out=$(cd "$tmpdir" && env -u HERMES_SELF_TEST bash "$SCRIPT" 2>&1); rc=$?
    [[ $rc -eq 1 ]] || { echo "SELF-TEST FAIL 7: perf/ no spec → rc=$rc, want 1"; fail=1; }

    rm -rf "$tmpdir"
    if [[ $fail -eq 0 ]]; then
        echo "SELF-TEST PASS: openspec-change-required.sh handles 7 fixtures correctly."
        exit 0
    fi
    exit 1
fi

# ────────────────────────────────────────────────────────────────
# Actual predicate — runs at pre-commit time
# ────────────────────────────────────────────────────────────────
if [[ "${SKIP_OPENSPEC_CHECK:-0}" == "1" ]]; then
    echo "WARN: openspec check skipped via SKIP_OPENSPEC_CHECK=1" >&2
    exit 0
fi

current_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")

# Exempt: main, HEAD-detached, non-material branch prefixes.
if [[ -z "$current_branch" || "$current_branch" == "main" || "$current_branch" == "HEAD" ]]; then
    exit 0
fi
if ! [[ "$current_branch" =~ ^(feat|refactor|perf)/ ]]; then
    exit 0
fi

# Best-effort fetch — pre-commit runs offline sometimes; that's OK, we
# fall back to the local origin/main ref.
git fetch origin main --quiet 2>/dev/null || true

# Two sources — either satisfies the check:
#   (1) any openspec/changes/*/change.md added or modified in the
#       branch's diff vs origin/main (from a prior commit on this branch)
#   (2) any openspec/changes/*/change.md currently staged for THIS commit
committed_diff=$(git diff --name-only origin/main..HEAD -- \
    'openspec/changes/*/change.md' 2>/dev/null)
staged=$(git diff --cached --name-only -- \
    'openspec/changes/*/change.md' 2>/dev/null)

if [[ -n "$committed_diff" || -n "$staged" ]]; then
    exit 0
fi

{
    echo "ERROR: pre-commit — feat/refactor/perf branch requires an openspec change spec."
    echo "  Branch: $current_branch"
    echo "  Expected: openspec/changes/<change-name>/change.md added or modified."
    echo "  Template: openspec/changes/TEMPLATE/change.md"
    echo "  Bypass (with cause): SKIP_OPENSPEC_CHECK=1 git commit ..."
    echo "  Rationale: #194 DoR/DoD hardening — openspec/changes/dor-dod-enforcement/"
} >&2
exit 1
