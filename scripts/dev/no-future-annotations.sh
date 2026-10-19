#!/usr/bin/env bash
# No new `from __future__ import annotations` (#906).
#
# Python 3.14 evaluates annotations lazily (PEP 649), so the import does
# nothing and only marks code as written for an older Python. This guard
# rejects any ADDED line that imports it in a .py file. Ruff's TID251 ban
# (pyproject.toml) covers every file except src/hermes/**/__init__.py,
# which ruff skips for that rule, so this guard stays for those.
#
# Called by:
#   - .pre-commit-config.yaml `no-future-annotations` hook (staged mode)
#   - Pre-commit hook when THIS script changes (self-test)
#   - CI `guards` job and manually: bash scripts/dev/no-future-annotations.sh --base origin/main
#
# Env vars:
#   HERMES_SELF_TEST  — set to 1 to run built-in pass/fail cases and exit
#
# Exit codes:
#   0 — no added line imports it (or self-test passed)
#   1 — one or more added lines import it
#   2 — usage error

set -euo pipefail

# ────────────────────────────────────────────────────────────────
# added_lines [base-ref] — "path:line" for every added import line.
# Staged changes when no base is given; otherwise this branch's
# changes since its merge-base with <base-ref>. While a merge is in
# progress the index is compared with MERGE_HEAD, not HEAD: lines the
# merged branch brings in are not this branch's additions, which is
# what CI's --base mode checks too.
# ────────────────────────────────────────────────────────────────
added_lines() {
    local range=(--cached)
    if [[ -n "${1:-}" ]]; then
        range=("$1...HEAD")
    elif git rev-parse -q --verify MERGE_HEAD >/dev/null; then
        range=(--cached MERGE_HEAD)
    fi
    git diff --unified=0 --no-color "${range[@]}" -- '*.py' | awk '
        /^\+\+\+ / { path = substr($0, 7); next }
        /^@@ / { split($3, a, ","); line = substr(a[1], 2) + 0; next }
        /^\+/ {
            if ($0 ~ /^\+[ \t]*from[ \t]+__future__[ \t]+import[ \t].*annotations/) print path ":" line
            line++
        }
    '
}

check() {
    local found
    found=$(added_lines "${1:-}")
    if [[ -n "$found" ]]; then
        echo "no-future-annotations: drop 'from __future__ import annotations'; Python 3.14 evaluates annotations lazily (PEP 649):" >&2
        printf '  %s\n' $found >&2
        return 1
    fi
    return 0
}

# ────────────────────────────────────────────────────────────────
# Self-test
# ────────────────────────────────────────────────────────────────
if [[ "${HERMES_SELF_TEST:-0}" == "1" ]]; then
    # Under a pre-commit hook git exports these for the real repo.
    unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE
    script="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    failed=0

    new_repo() {
        rm -rf "$tmp/repo"
        mkdir -p "$tmp/repo/src"
        git -C "$tmp/repo" init -q -b main
        git -C "$tmp/repo" config user.name self-test
        git -C "$tmp/repo" config user.email self-test@example.invalid
        git -C "$tmp/repo" config commit.gpgsign false
        printf 'from __future__ import annotations\n\nx = 1\n' > "$tmp/repo/src/old.py"
        git -C "$tmp/repo" add -A
        git -C "$tmp/repo" commit -q -m "chore: seed"
    }

    stage() {
        mkdir -p "$tmp/repo/$(dirname "$1")"
        printf '%b' "$2" > "$tmp/repo/$1"
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
    stage src/new.py 'from __future__ import annotations\n\ny = 2\n'
    expect 1 "a new file with the import is rejected"

    new_repo
    stage src/new.py '"""Doc."""\n\nimport os\n'
    expect 0 "a new file without the import passes"

    new_repo
    stage src/old.py 'from __future__ import annotations\n\nx = 3\n'
    expect 0 "editing a file that already has the import passes"

    new_repo
    stage src/other.py 'from __future__ import annotations, division\n'
    expect 1 "the import inside a longer __future__ line is rejected"

    new_repo
    stage docs/note.md 'from __future__ import annotations\n'
    expect 0 "non-Python files are not checked"

    new_repo
    git -C "$tmp/repo" checkout -q -b feature
    stage src/feat.py 'from __future__ import annotations\n'
    git -C "$tmp/repo" commit -q -m "feat: feat"
    expect 1 "--base mode catches a committed import" main

    new_repo
    git -C "$tmp/repo" checkout -q -b feature
    stage src/feat.py 'z = 1\n'
    git -C "$tmp/repo" commit -q -m "feat: feat"
    expect 0 "--base mode ignores the import already on the base" main

    new_repo
    git -C "$tmp/repo" checkout -q -b feature
    stage src/feat.py 'z = 1\n'
    git -C "$tmp/repo" commit -q -m "feat: feat"
    git -C "$tmp/repo" checkout -q main
    stage src/main_only.py 'from __future__ import annotations\n'
    git -C "$tmp/repo" commit -q -m "feat: main"
    git -C "$tmp/repo" checkout -q feature
    git -C "$tmp/repo" merge -q --no-commit --no-ff main >/dev/null 2>&1
    expect 0 "merging main ignores the imports main brings in"

    stage src/fix.py 'from __future__ import annotations\n'
    expect 1 "a merge still rejects an import this branch adds"

    if [[ "$failed" == "0" ]]; then
        echo "no-future-annotations self-test: all fixtures passed"
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
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) echo "ERROR: unknown argument '$1'" >&2; exit 2 ;;
    esac
done

check "$BASE"
