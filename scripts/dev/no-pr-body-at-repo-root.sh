#!/usr/bin/env bash
# Reject PR body scratchpad files staged at the repo root.
#
# PR body scratchpads (`pr-body-*.md`, `*.pr-body.md`) written to the repo
# root trip markdownlint MD041: the first line must be H1, but PR bodies
# open with `## Summary`. Wave 3 (drift-eval 2026-08-22) taught
# `create-pr.sh` to copy repo-relative bodies to /tmp/ before invoking
# `gh pr create` and before running the lint. That patch only helps when
# the PR body file goes through `create-pr.sh`. A file committed to the
# index earlier (by hand, by a sub-agent, by a stray `git add .`) still
# trips MD041 in `pre-commit run markdownlint-cli2` and in CI. Wave 7
# closes the door at the index level.
#
# What counts as a PR body file (at the REPO ROOT only):
#   * pr-body-*.md   — e.g. pr-body-wave7.md, pr-body-slack.md
#   * *.pr-body.md   — e.g. wave7.pr-body.md, sentry.pr-body.md
#   * .pr-body-*.md  — dotfile variant sub-agents sometimes emit
#
# What does NOT count (allowed):
#   * Anything nested (e.g. docs/decisions/pr-body-note.md) — MD041 still
#     applies but the pattern is intentional and rarer; not this hook's
#     job to police.
#   * Files with those names inside changelog.d/ or scripts/ — the check
#     is repo-root-only.
#
# Invocation:
#   bash scripts/dev/no-pr-body-at-repo-root.sh          # normal (pre-commit)
#   HERMES_SELF_TEST=1 bash scripts/dev/no-pr-body-at-repo-root.sh
#
# Called by:
#   * `.pre-commit-config.yaml` — `no-pr-body-at-repo-root` hook, always_run
#   * Self-test hook (`no-pr-body-at-repo-root-self-test`) exercises the
#     matcher against inline fixtures, no filesystem writes.
#
# Exit codes:
#   0 — no PR body files at repo root
#   1 — at least one offending file found in the staged tree
#   2 — usage error (unexpected — this script has no args)

set -uo pipefail

# ────────────────────────────────────────────────────────────────
# matches_pr_body_pattern <path>
#
# Returns 0 (match) if <path> is a PR body file AT THE REPO ROOT.
# Returns 1 (no match) otherwise. Pure string test — does not touch
# the filesystem, so it is safe for self-test fixtures.
# ────────────────────────────────────────────────────────────────
matches_pr_body_pattern() {
    local path="$1"
    # Only care about files directly at the repo root — no nested paths.
    if [[ "$path" == */* ]]; then
        return 1
    fi
    case "$path" in
        pr-body-*.md)  return 0 ;;
        .pr-body-*.md) return 0 ;;
        *.pr-body.md)  return 0 ;;
        *) return 1 ;;
    esac
}

# ────────────────────────────────────────────────────────────────
# Self-test — validates the matcher against representative fixtures
# without touching the git index or the filesystem.
# ────────────────────────────────────────────────────────────────
if [[ "${HERMES_SELF_TEST:-0}" == "1" ]]; then
    fail=0

    assert_match() {
        local path="$1" want="$2"
        if matches_pr_body_pattern "$path"; then
            actual="match"
        else
            actual="no-match"
        fi
        if [[ "$actual" == "$want" ]]; then
            echo "PASS: '$path' → $actual"
        else
            echo "FAIL: '$path' → got '$actual', wanted '$want'"
            fail=1
        fi
    }

    # Should MATCH — the class this hook exists to reject.
    assert_match "pr-body-wave7.md"     match
    assert_match "pr-body-slack.md"     match
    assert_match "pr-body-abc.md"       match
    assert_match ".pr-body-wave7.md"    match
    assert_match "wave7.pr-body.md"     match
    assert_match "sentry.pr-body.md"    match

    # Should NOT match — nested paths, CHANGELOG entries, unrelated files.
    assert_match "docs/decisions/pr-body-note.md" no-match
    assert_match "changelog.d/pr-body-foo.md"     no-match
    assert_match "scripts/dev/pr-body-lint.sh"    no-match
    assert_match "CHANGELOG.md"                   no-match
    assert_match "README.md"                      no-match
    assert_match "pr_body.md"                     no-match
    assert_match "prbody.md"                      no-match
    assert_match "pr-body.md"                     no-match

    if [[ "$fail" == 1 ]]; then
        echo ""
        echo "SELF-TEST FAILED"
        exit 1
    fi
    echo ""
    echo "SELF-TEST PASS: no-pr-body-at-repo-root.sh matcher handles 14 fixtures."
    exit 0
fi

# ────────────────────────────────────────────────────────────────
# Production path — inspect staged (index) files and reject offenders.
#
# `git diff --cached --name-only` returns ALL staged paths, added or
# modified. Filtering to `--diff-filter=A` (added-only) is narrower but
# would let a rename onto a PR-body-shaped path slip through. We check
# every staged path — the matcher is cheap.
# ────────────────────────────────────────────────────────────────
offenders=()
while IFS= read -r path; do
    [[ -z "$path" ]] && continue
    if matches_pr_body_pattern "$path"; then
        offenders+=("$path")
    fi
done < <(git diff --cached --name-only 2>/dev/null || true)

if [[ ${#offenders[@]} -gt 0 ]]; then
    {
        echo "ERROR: PR body files must not be committed at the repo root —"
        echo "       markdownlint MD041 (first line must be H1) fails on them,"
        echo "       and every open-PR flow hits the failure at push time."
        echo ""
        echo "  Offending staged path(s):"
        for offender in "${offenders[@]}"; do
            echo "    - $offender"
        done
        echo ""
        echo "  Fix: move the file outside the repo (git-bash on Windows:"
        echo "       mv '$offender' /tmp/), then re-run create-pr.sh with"
        echo "       --body-file pointing at the moved path."
        echo ""
        echo "  Rationale: docs/guides/dev-workflow.md § Opening a PR + the"
        echo "  header comment in scripts/dev/create-pr.sh explain the"
        echo "  markdownlint MD041 collision this hook prevents."
    } >&2
    exit 1
fi

echo "No PR body files at repo root — ok."
exit 0
