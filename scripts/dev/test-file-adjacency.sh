#!/usr/bin/env bash
# Test-file adjacency guard (#160).
#
# Every NEW module under src/hermes/ must arrive with a matching unit
# test file somewhere under tests/unit/: adding src/hermes/a/b/foo.py
# needs a tests/unit/**/test_foo.py in the same commit (or already in
# the index). Forces test-file-adjacent thinking at commit time instead
# of at review time.
#
# Only ADDED files count. Editing an existing module never trips the
# guard, so the 30-odd legacy modules without a same-name test file are
# grandfathered.
#
# Exempt:
#   - __init__.py, __main__.py and any _private.py module (leading
#     underscore)
#   - src/hermes/ports/** — ports are Protocol definitions with no
#     behaviour of their own; adapter contract tests cover them
#
# Called by:
#   - .pre-commit-config.yaml `test-file-adjacency` hook (staged mode)
#   - Pre-commit hook when THIS script changes (self-test)
#   - Manually against a branch: bash scripts/dev/test-file-adjacency.sh --base origin/main
#
# Env vars:
#   HERMES_SELF_TEST  — set to 1 to run built-in pass/fail cases and exit
#
# Exit codes:
#   0 — every new module has a matching test file (or self-test passed)
#   1 — one or more new modules lack a test file
#   2 — usage error

set -euo pipefail

# ────────────────────────────────────────────────────────────────
# added_files [base-ref]
# Staged additions when no base is given; otherwise files added on
# this branch since its merge-base with <base-ref>.
# ────────────────────────────────────────────────────────────────
added_files() {
    if [[ -n "${1:-}" ]]; then
        git diff --name-only --diff-filter=A "$1...HEAD"
    else
        git diff --cached --name-only --diff-filter=A
    fi
}

needs_test() {
    local path="$1" name
    [[ "$path" =~ ^src/hermes/.*\.py$ ]] || return 1
    [[ "$path" == src/hermes/ports/* ]] && return 1
    name=$(basename "$path")
    [[ "$name" == _* ]] && return 1
    return 0
}

# ────────────────────────────────────────────────────────────────
# check [base-ref] — prints offenders, returns 1 if any
# ────────────────────────────────────────────────────────────────
check() {
    local base="${1:-}" path stem known missing=()
    known=$(git ls-files 'tests/unit/*' | sed 's|.*/||' | sort -u)
    while IFS= read -r path; do
        [[ -z "$path" ]] && continue
        needs_test "$path" || continue
        stem=$(basename "$path" .py)
        grep -qxF "test_${stem}.py" <<< "$known" || missing+=("$path")
    done < <(added_files "$base")

    if (( ${#missing[@]} > 0 )); then
        echo "test-file-adjacency: new modules without a matching tests/unit/**/test_<name>.py:" >&2
        printf '  %s\n' "${missing[@]}" >&2
        echo "Add the test file in the same commit (see docs/guides/ai-authored-tests.md)." >&2
        return 1
    fi
    return 0
}

# ────────────────────────────────────────────────────────────────
# Self-test
# ────────────────────────────────────────────────────────────────
if [[ "${HERMES_SELF_TEST:-0}" == "1" ]]; then
    # Under a pre-commit hook git exports these for the real repo; an
    # inherited GIT_DIR once flipped the real repo to core.bare=true.
    unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE
    script="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    failed=0

    new_repo() {
        rm -rf "$tmp/repo"
        mkdir -p "$tmp/repo"
        git -C "$tmp/repo" init -q -b main
        git -C "$tmp/repo" config user.name self-test
        git -C "$tmp/repo" config user.email self-test@example.invalid
        git -C "$tmp/repo" config commit.gpgsign false
        mkdir -p "$tmp/repo/src/hermes/domain" "$tmp/repo/tests/unit/domain"
        echo "x = 1" > "$tmp/repo/src/hermes/domain/old.py"
        git -C "$tmp/repo" add -A
        git -C "$tmp/repo" commit -q -m "chore: seed"
    }

    stage() {
        mkdir -p "$tmp/repo/$(dirname "$1")"
        echo "y = 2" > "$tmp/repo/$1"
        git -C "$tmp/repo" add "$1"
    }

    expect() {
        local want="$1" label="$2" got=0
        (cd "$tmp/repo" && HERMES_SELF_TEST=0 bash "$script" ${3:+--base "$3"}) >/dev/null 2>&1 || got=$?
        if [[ "$got" != "$want" ]]; then
            echo "SELF-TEST FAIL: $label (exit $got, want $want)" >&2
            failed=1
        fi
    }

    new_repo
    stage src/hermes/domain/foo.py
    expect 1 "new module without a test file is rejected"

    new_repo
    stage src/hermes/domain/foo.py
    stage tests/unit/domain/test_foo.py
    expect 0 "new module with a staged test file passes"

    new_repo
    stage src/hermes/domain/foo.py
    stage tests/unit/somewhere/else/test_foo.py
    expect 0 "test file anywhere under tests/unit counts"

    new_repo
    stage src/hermes/domain/foo.py
    stage tests/integration/test_foo.py
    expect 1 "a test outside tests/unit does not count"

    new_repo
    stage src/hermes/domain/sub/__init__.py
    stage src/hermes/domain/_helpers.py
    stage src/hermes/ports/thing.py
    expect 0 "__init__, private and port modules are exempt"

    new_repo
    echo "x = 3" > "$tmp/repo/src/hermes/domain/old.py"
    git -C "$tmp/repo" add src/hermes/domain/old.py
    expect 0 "modifying an existing module is not checked"

    new_repo
    git -C "$tmp/repo" checkout -q -b feature
    stage src/hermes/application/bar.py
    git -C "$tmp/repo" commit -q -m "feat: bar"
    expect 1 "--base mode catches a committed module without a test" main

    if [[ "$failed" == "0" ]]; then
        echo "test-file-adjacency self-test: all fixtures passed"
    fi
    exit "$failed"
fi

# ────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────
BASE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --base) BASE="${2:?--base needs a ref}"; shift 2 ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        *) echo "ERROR: unknown argument '$1'" >&2; exit 2 ;;
    esac
done

check "$BASE"
