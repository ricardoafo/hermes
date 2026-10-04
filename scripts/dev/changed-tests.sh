#!/usr/bin/env bash
# Print the test files that cover the files changed since <base>, one per
# line, for one tier (unit or integration). pr-preflight.sh uses it so a
# local run tests the changed area in seconds; CI and the merge queue's
# merged-tree gate still run every test.
#
# Usage:
#   bash scripts/dev/changed-tests.sh <base> <tier>
#   HERMES_SELF_TEST=1 bash scripts/dev/changed-tests.sh
#
# Output:
#   (nothing)  no test in the tier covers a changed file
#   ALL        shared test setup or dependencies changed: run the whole tier
#   <paths>    the selected test files
#
# Selection, for the files changed between merge-base(<base>, HEAD) and
# the working tree (so uncommitted edits count):
#   * pyproject.toml, uv.lock, any conftest.py, tests/support/,
#     tests/plugins/, tests/fixtures/ or a top-level src/hermes module  → ALL
#   * Markdown and docs/                                                → nothing
#   * a changed test file in the tier                                   → itself
#   * any other changed file: every test file in the tier that names its
#     dotted module (src/hermes/a/b.py → hermes.a.b) or, for files outside
#     src/, its file stem (scripts/dev/foo-bar.py → foo_bar or foo-bar)
#
# This catches direct importers, not code reached only transitively. That
# trade is deliberate: the full tiers run in CI on every head.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT" || exit 2

shared_setup() {
    [[ "$1" =~ ^(pyproject\.toml|uv\.lock)$ ]] \
        || [[ "$1" =~ (^|/)conftest\.py$ ]] \
        || [[ "$1" =~ ^tests/(support|plugins|fixtures)/ ]] \
        || [[ "$1" =~ ^src/hermes/[^/]+\.py$ ]]
}

# Reads changed paths on stdin, prints the selection for <tier>.
select_tests() {
    local tier="$1" path stem module
    local -a patterns=()
    local -A picked=()
    while IFS= read -r path; do
        [[ -z "$path" ]] && continue
        if shared_setup "$path"; then
            echo ALL
            return 0
        fi
        if [[ "$path" == *.md || "$path" == docs/* ]]; then
            continue
        fi
        if [[ "$path" == tests/* ]]; then
            if [[ "$path" == "tests/$tier/"* && "$path" == *.py && -f "$path" ]]; then
                picked["$path"]=1
            fi
            continue
        fi
        if [[ "$path" == src/hermes/*.py ]]; then
            module="${path#src/}"
            module="${module%.py}"
            module="${module%/__init__}"
            patterns+=("${module//\//.}")
        else
            stem="$(basename "$path")"
            stem="${stem%.*}"
            [[ ${#stem} -lt 4 ]] && continue
            patterns+=("$stem" "${stem//-/_}")
        fi
    done

    local pattern file
    for pattern in "${patterns[@]}"; do
        while IFS= read -r file; do
            [[ -n "$file" ]] && picked["$file"]=1
        done < <(grep -rlF --include='*.py' -e "$pattern" "tests/$tier" 2>/dev/null)
    done
    if [[ ${#picked[@]} -gt 0 ]]; then
        printf '%s\n' "${!picked[@]}" | sort
    fi
}

if [[ "${HERMES_SELF_TEST:-0}" == "1" ]]; then
    fail=0
    expect() {
        local name="$1" want="$2" got
        got=$(printf '%s\n' "${@:3}" | select_tests unit | head -1)
        if [[ "$got" == "$want" ]]; then
            echo "PASS: $name"
        else
            echo "SELF-TEST FAIL: $name (want '$want', got '$got')"
            fail=1
        fi
    }
    expect "uv.lock selects the whole tier" ALL uv.lock
    expect "a conftest selects the whole tier" ALL tests/unit/domain/conftest.py
    expect "a top-level src module selects the whole tier" ALL src/hermes/__main__.py
    expect "docs alone select nothing" "" docs/guides/README.md
    expect "a changed unit test selects itself" tests/unit/test_sev1_guard.py tests/unit/test_sev1_guard.py
    expect "an integration test is not a unit test" "" tests/integration/test_simulated_exchange_round_trip.py
    got=$(printf 'src/hermes/domain/value_objects/money.py\n' | select_tests unit)
    if grep -q 'tests/unit/domain/value_objects/test_money.py' <<< "$got"; then
        echo "PASS: a src module selects the tests that import it"
    else
        echo "SELF-TEST FAIL: money.py did not select test_money.py"
        fail=1
    fi
    if [[ "$fail" == 1 ]]; then
        echo "SELF-TEST FAILED"
        exit 1
    fi
    echo "SELF-TEST PASS: changed-tests.sh handles 7 fixtures."
    exit 0
fi

if [[ $# -ne 2 || ! "$2" =~ ^(unit|integration)$ ]]; then
    echo "usage: $0 <base> <unit|integration>" >&2
    exit 2
fi

merge_base=$(git merge-base "$1" HEAD) || {
    echo "ERROR: no merge base between $1 and HEAD" >&2
    exit 2
}
{
    git diff --name-only "$merge_base"
    git ls-files --others --exclude-standard
} | sort -u | select_tests "$2"
