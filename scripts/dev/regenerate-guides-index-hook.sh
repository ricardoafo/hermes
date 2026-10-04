#!/usr/bin/env bash
# Pre-commit check for docs/guides/*.md changes.
#
# Runs regenerate-guides-index.sh --check: every guide must have an H1 and
# a `**Description**:` line, or the index cannot be built. It never writes
# docs/guides/README.md. The index is a derived file rebuilt on main after
# each merge, so a commit in a PR must not carry it.
#
# Called by:
#   `.pre-commit-config.yaml` — `regenerate-guides-index` hook, triggered
#   when any docs/guides/*.md file is staged.
#
# Invocation (production):
#   bash scripts/dev/regenerate-guides-index-hook.sh
#
# Self-test invocation:
#   HERMES_SELF_TEST=1 bash scripts/dev/regenerate-guides-index-hook.sh
#
# Exit codes:
#   0 — every guide can be indexed
#   1 — a guide is missing its H1 or Description line

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
REGEN="$REPO_ROOT/scripts/dev/regenerate-guides-index.sh"
README="$REPO_ROOT/docs/guides/README.md"

if [[ "${HERMES_SELF_TEST:-0}" == "1" ]]; then
    fail=0

    if [[ ! -x "$REGEN" ]]; then
        echo "SELF-TEST FAIL: regenerate-guides-index.sh not found or not executable"
        fail=1
    else
        echo "PASS: regenerate-guides-index.sh is executable"
    fi

    before=$(git hash-object "$README")
    if bash "$REGEN" --check >/dev/null 2>&1; then
        echo "PASS: --check succeeds on the current guides"
    else
        echo "SELF-TEST FAIL: --check fails on the current guides"
        fail=1
    fi
    if [[ "$(git hash-object "$README")" == "$before" ]]; then
        echo "PASS: --check leaves README.md untouched"
    else
        echo "SELF-TEST FAIL: --check modified README.md"
        fail=1
    fi

    if bash "$REGEN" --bogus >/dev/null 2>&1; then
        echo "SELF-TEST FAIL: an unknown flag was accepted"
        fail=1
    else
        echo "PASS: an unknown flag is rejected"
    fi

    if [[ "$fail" == 1 ]]; then
        echo ""
        echo "SELF-TEST FAILED"
        exit 1
    fi
    echo ""
    echo "SELF-TEST PASS: regenerate-guides-index-hook.sh handles 4 fixtures."
    exit 0
fi

if ! bash "$REGEN" --check; then
    echo "ERROR: a guide cannot be indexed; see the message above." >&2
    exit 1
fi
