#!/usr/bin/env bash
# CHANGELOG update enforcement (scope-aware).
#
# For PRs typed feat/fix/refactor/perf (or with breaking marker `!:`),
# require that CHANGELOG.md was modified OR a changelog.d/*.md fragment was
# added in the PR — UNLESS the scope is one of the infra-only scopes (ci,
# build, deps, tooling) which don't affect user-visible runtime behavior.
#
# The fragment path is the preferred way per AGENTS.md (introduced by
# process hardening wave 4). Direct CHANGELOG.md edits remain valid for
# small doc-only PRs — either path satisfies this check.
#
# Called by:
#   - .github/workflows/ci.yml changelog job (production)
#   - Pre-commit hook self-test when this script changes
#   - Manually: PR_TITLE=... BASE_SHA=... HEAD_SHA=... bash scripts/dev/changelog-check.sh
#
# Env vars:
#   PR_TITLE          — the PR title (required in normal use)
#   BASE_SHA          — merge-base SHA (required to detect CHANGELOG diff)
#   HEAD_SHA          — head SHA (required to detect CHANGELOG diff)
#   HERMES_SELF_TEST  — set to 1 for built-in pass/fail cases
#
# Exit codes:
#   0 — check passed or was skipped
#   1 — check failed (missing CHANGELOG entry or fragment)
#   2 — usage error

set -euo pipefail

# ────────────────────────────────────────────────────────────────
# classify <title>
# prints one of: skip / require / require-breaking
# ────────────────────────────────────────────────────────────────
classify() {
    local title="$1"
    local type scope breaking

    type=$(echo "$title" | grep -oE '^(feat|fix|refactor|perf)(\([a-z0-9-]+\))?(!)?:' | grep -oE '^(feat|fix|refactor|perf)' || true)
    scope=$(echo "$title" | grep -oE '^(feat|fix|refactor|perf)\(([a-z0-9-]+)\)' | sed -E 's/^[a-z]+\(([a-z0-9-]+)\)$/\1/' || true)
    breaking=$(echo "$title" | grep -oE '!:' || true)

    if [[ -z "$type" && -z "$breaking" ]]; then
        echo "skip"
        return
    fi

    if [[ -z "$breaking" ]]; then
        case "$scope" in
            ci|build|deps|tooling)
                echo "skip"
                return
                ;;
        esac
    fi

    if [[ -n "$breaking" ]]; then
        echo "require-breaking"
    else
        echo "require"
    fi
}

# ────────────────────────────────────────────────────────────────
# Self-test
# ────────────────────────────────────────────────────────────────
if [[ "${HERMES_SELF_TEST:-0}" == "1" ]]; then
    failed=0
    assert_classify() {
        local title="$1" expected="$2"
        local actual
        actual=$(classify "$title")
        if [[ "$actual" == "$expected" ]]; then
            echo "PASS: '$title' → $actual"
        else
            echo "FAIL: '$title' → got '$actual', expected '$expected'"
            failed=1
        fi
    }

    assert_classify "feat(fifo): add lot-consumption event log" "require"
    assert_classify "fix(exchange): retry Binance 5xx" "require"
    assert_classify "refactor(domain): extract port interface" "require"
    assert_classify "perf(cli): batch price fetches" "require"
    assert_classify "docs(guides): clarify squash-merge policy" "skip"
    assert_classify "chore(deps): bump httpx" "skip"
    assert_classify "test(fifo): add hypothesis strategies" "skip"
    assert_classify "fix(ci): pr-body linter grep flag" "skip"
    assert_classify "fix(build): pyproject constraint" "skip"
    assert_classify "fix(deps): pin torch" "skip"
    assert_classify "fix(tooling): script permission" "skip"
    assert_classify "fix(ci)!: breaking CI change" "require-breaking"
    assert_classify "feat(domain)!: rename port method" "require-breaking"
    assert_classify "revert: broken thing" "skip"

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
: "${PR_TITLE:?PR_TITLE env var required (or set HERMES_SELF_TEST=1 for tests)}"
: "${BASE_SHA:?BASE_SHA env var required}"
: "${HEAD_SHA:?HEAD_SHA env var required}"

decision=$(classify "$PR_TITLE")

case "$decision" in
    skip)
        echo "Type or scope does not require a CHANGELOG entry — skipped."
        exit 0
        ;;
    require|require-breaking)
        changed=$(git diff --name-only "$BASE_SHA" "$HEAD_SHA")
        if echo "$changed" | grep -qE '^CHANGELOG\.md$'; then
            echo "CHANGELOG.md updated — ok."
            exit 0
        fi
        # Fragment path (wave 4): a new file under changelog.d/ also
        # satisfies the check. Aggregation happens at release-cut time.
        if echo "$changed" | grep -qE '^changelog\.d/[^/]+\.md$'; then
            echo "changelog.d/*.md fragment added — ok."
            exit 0
        fi
        echo "::error::PR requires a CHANGELOG.md [Unreleased] update OR a changelog.d/*.md fragment."
        echo "Preferred: add changelog.d/<pr-number>-<type>.md (see changelog.d/README.md)."
        echo "Direct edits to CHANGELOG.md are still accepted."
        echo "Infra-only scopes (ci, build, deps, tooling) skip this check — non-breaking only."
        exit 1
        ;;
    *)
        echo "ERROR: classify returned unexpected value: $decision" >&2
        exit 2
        ;;
esac
