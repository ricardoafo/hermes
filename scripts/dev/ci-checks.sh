#!/usr/bin/env bash
# The CI checks, each defined once. CI's workflow steps and pr-preflight.sh
# both call this script, so "passed locally" and "passes in CI" run the
# same commands and cannot drift apart.
#
# Usage:
#   bash scripts/dev/ci-checks.sh <check>
#   bash scripts/dev/ci-checks.sh unit --changed <base>
#   bash scripts/dev/ci-checks.sh integration --changed <base>
#   bash scripts/dev/ci-checks.sh --list
#   HERMES_SELF_TEST=1 bash scripts/dev/ci-checks.sh
#
# Checks:
#   format, lint, types, imports      ruff format, ruff check, mypy, import-linter
#   deps, complexity, deadcode        pip-audit, radon, vulture
#   licenses                          pip-licenses through license-check.py
#   unit                              tests/unit/ with branch coverage
#   integration                       tests/integration/
#   coverage-floor                    per-package thresholds (needs unit first)
#   lockfile                          uv.lock hash integrity
#   merge-decisions                   node tests for the merge queue and PR metadata
#   codeowners                        CODEOWNERS syntax
#   markdown                          markdownlint on the full tree
#   guides-index                      the guides index still builds (--check)
#   workflows                         actionlint + zizmor on .github/workflows
#
# `--changed <base>` (unit, integration) runs only the tests for the files
# changed since <base>: see scripts/dev/changed-tests.sh. CI always runs
# the full tiers; pr-preflight.sh uses --changed unless --full-tests.
#
# Exit codes:
#   0 — the check passed (or --changed selected no tests)
#   1 — the check failed
#   2 — usage error

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT" || exit 2

CHECKS=(format lint types imports deps complexity deadcode licenses unit
    integration coverage-floor lockfile merge-decisions codeowners markdown
    guides-index workflows)

# -n 4, not auto: auto counts the host's CPUs, not the container's 4, and
# 10+ workers overrun the CI runner's 4 GB limit (#397).
XDIST_WORKERS=4

run_tier() {
    local tier="$1"
    shift
    local -a cov=()
    if [[ "$tier" == unit ]]; then
        cov=(--cov=src/hermes --cov-branch --cov-report=xml --cov-report=json --cov-report=term)
    fi

    if [[ "${1:-}" != "--changed" ]]; then
        uv run pytest "tests/$tier/" -n "$XDIST_WORKERS" "${cov[@]}"
        return
    fi

    local base="${2:-}"
    if [[ -z "$base" ]]; then
        echo "ERROR: --changed needs a base ref" >&2
        return 2
    fi
    local selection
    selection=$(bash scripts/dev/changed-tests.sh "$base" "$tier") || return 1
    case "$selection" in
        "")
            echo "No $tier tests cover the changed files; CI runs the full tier."
            return 0
            ;;
        ALL)
            echo "Shared test setup or dependencies changed; running all $tier tests."
            uv run pytest "tests/$tier/" -n "$XDIST_WORKERS"
            ;;
        *)
            local -a files
            mapfile -t files <<< "$selection"
            echo "Running ${#files[@]} $tier test file(s) for the changed area."
            uv run pytest "${files[@]}" -n "$XDIST_WORKERS"
            ;;
    esac
}

run_one() {
    local check="$1"
    shift
    case "$check" in
        format) uv run ruff format --check . ;;
        lint) uv run ruff check . ;;
        types) uv run mypy --strict src/hermes ;;
        imports) uv run lint-imports ;;
        # --skip-editable skips our own local hermes package (not on PyPI).
        deps) uv run pip-audit --skip-editable ;;
        complexity) uv run radon cc --min B src/ ;;
        deadcode) uv run vulture src/ ;;
        # Blocks GPL/AGPL/LGPL, which create commercial-use constraints.
        licenses)
            uv tool install --quiet pip-licenses==5.5.5 \
                && uv tool run pip-licenses --format=json | python3 scripts/dev/license-check.py
            ;;
        unit) run_tier unit "$@" ;;
        integration) run_tier integration "$@" ;;
        # --bootstrap: skip EMPTY-package FAILs while the Phase 0 skeleton is
        # not yet populated (#71).
        coverage-floor) uv run python scripts/dev/coverage-check.py --bootstrap ;;
        lockfile) python3 scripts/dev/reproducibility-check.py ;;
        merge-decisions) node --test tools/merge/*.test.cjs tools/pr-metadata/*.test.cjs ;;
        codeowners) bash scripts/dev/codeowners-syntax-check.sh ;;
        markdown) markdownlint-cli2 "**/*.md" "#node_modules" "#.venv" ;;
        guides-index) bash scripts/dev/regenerate-guides-index.sh --check ;;
        # Pinned so a new release cannot turn CI red overnight. zizmor's
        # baseline of findings that predate this check lives in
        # .github/zizmor.yml and is burned down in #529.
        workflows)
            uv tool run --quiet --from "actionlint-py==1.7.12.*" actionlint \
                && uv tool run --quiet zizmor==1.30.1 --offline --config .github/zizmor.yml .github/workflows
            ;;
        *)
            echo "ERROR: unknown check '$check'. Known: ${CHECKS[*]}" >&2
            return 2
            ;;
    esac
}

if [[ "${HERMES_SELF_TEST:-0}" == "1" ]]; then
    fail=0
    for check in "${CHECKS[@]}"; do
        if ! grep -qE "^        $check\)" "$0"; then
            echo "SELF-TEST FAIL: check '$check' is listed but has no case"
            fail=1
        fi
    done
    if env -u HERMES_SELF_TEST bash "$0" no-such-check >/dev/null 2>&1; then
        echo "SELF-TEST FAIL: an unknown check was accepted"
        fail=1
    fi
    if env -u HERMES_SELF_TEST bash "$0" unit --changed >/dev/null 2>&1; then
        echo "SELF-TEST FAIL: --changed without a base was accepted"
        fail=1
    fi
    if [[ "$fail" == 1 ]]; then
        echo "SELF-TEST FAILED"
        exit 1
    fi
    echo "SELF-TEST PASS: ci-checks.sh lists ${#CHECKS[@]} checks, each with a case."
    exit 0
fi

case "${1:-}" in
    "" | -h | --help)
        sed -n '2,33p' "$0"
        [[ -n "${1:-}" ]] && exit 0
        exit 2
        ;;
    --list)
        printf '%s\n' "${CHECKS[@]}"
        exit 0
        ;;
esac

run_one "$@"
rc=$?
[[ "$rc" == 2 ]] && exit 2
[[ "$rc" == 0 ]] && exit 0
exit 1
