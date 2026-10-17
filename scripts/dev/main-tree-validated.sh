#!/usr/bin/env bash
# Did CI already pass on the exact tree this push to main landed?
#
# Usage: main-tree-validated.sh <pushed_sha>
#   Prints `yes` when the commit's tree is byte-identical to the head of
#   the PR it came from AND ci.yml passed on that head. Prints `no`
#   otherwise, including whenever anything cannot be checked. Always
#   exits 0 on a verdict; the caller decides what to skip.
#
# Why: the merge queue squash-merges a head that already contains main
# (its fast path), so the tree on main is the tree PR CI just passed.
# Running the whole suite again on main costs a runner for nothing
# (#414). "Cannot check" is never "validated": any missing piece is `no`.
#
# Env vars:
#   GITHUB_REPOSITORY  owner/repo (required unless self-testing)
#   GH_TOKEN           needs contents:read + pull-requests:read
#   HERMES_SELF_TEST=1 run the decision table and exit
#
# Called by: .github/workflows/ci.yml (Changeset step, push to main).

set -uo pipefail

# decide <pushed_tree> <head_tree> <ci_passed: true|false>
decide() {
    local pushed=$1 head=$2 passed=$3
    if [[ -z "$pushed" || -z "$head" ]]; then
        echo no
    elif [[ "$pushed" != "$head" ]]; then
        echo no
    elif [[ "$passed" != "true" ]]; then
        echo no
    else
        echo yes
    fi
}

if [[ "${HERMES_SELF_TEST:-0}" == "1" ]]; then
    fail=0
    check() {
        local want=$1 got
        shift
        got=$(decide "$@")
        if [[ "$got" != "$want" ]]; then
            echo "SELF-TEST FAIL: decide $* -> $got, want $want" >&2
            fail=1
        fi
    }
    check yes abc abc true
    check no abc def true
    check no abc abc false
    check no "" abc true
    check no abc "" true
    [[ "$fail" == 0 ]] && echo "SELF-TEST PASS: main-tree-validated.sh handles 5 fixtures correctly."
    exit "$fail"
fi

sha=${1:?usage: main-tree-validated.sh <pushed_sha>}
repo=${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}

# The self-hosted runner has no gh or jq; curl + python3 it does have.
api() {
    curl -fsSL \
        -H "Authorization: Bearer ${GH_TOKEN:-}" \
        -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/$repo/$1"
}

pushed_tree=$(git rev-parse "$sha^{tree}" 2>/dev/null || true)

pr=$(api "commits/$sha/pulls" 2>/dev/null \
    | python3 -c 'import json, sys
prs = [p for p in json.load(sys.stdin) if p.get("merged_at")]
print(prs[0]["number"], prs[0]["head"]["sha"]) if prs else None' 2>/dev/null || true)
if [[ -z "$pr" ]]; then
    echo "No merged PR for $sha; running the suite." >&2
    echo no
    exit 0
fi
read -r number head_sha <<< "$pr"

head_tree=""
if git fetch --no-tags --quiet origin "refs/pull/$number/head" 2>/dev/null; then
    head_tree=$(git rev-parse "$head_sha^{tree}" 2>/dev/null || true)
fi

passed=$(api "actions/workflows/ci.yml/runs?head_sha=$head_sha&status=success&per_page=1" 2>/dev/null \
    | python3 -c 'import json, sys; print("true" if json.load(sys.stdin).get("total_count", 0) > 0 else "false")' 2>/dev/null \
    || echo false)

verdict=$(decide "$pushed_tree" "$head_tree" "$passed")
echo "#$number head $head_sha: pushed tree ${pushed_tree:-?}, head tree ${head_tree:-?}, CI passed $passed -> $verdict" >&2
echo "$verdict"
