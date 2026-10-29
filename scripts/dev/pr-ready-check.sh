#!/usr/bin/env bash
# Ready-to-merge gate: a PR opens only when it could merge right now.
#
# The maintainer reviews ready PRs only, approves what they agree with, and the
# queue merges approved PRs. A PR that is behind main with a conflict,
# has commits still on the laptop, or never passed the full local suite
# wastes that review: it gets approved, then needs another push, which
# dismisses the approval. This gate refuses to open such a PR.
#
# Checks, in order (each names its own fix):
#   1. The working tree is clean (nothing uncommitted would be left out).
#   2. HEAD is pushed: the branch's upstream points at HEAD.
#   3. HEAD contains the current origin/main tip (fetched here), so the
#      PR is not behind main at all.
#   4. `git merge-tree` of HEAD and origin/main reports no conflict.
#      Implied by 3; kept so a bypass of 3 still catches a conflict.
#   5. The full `pr-preflight.sh` suite passed on this exact HEAD. The
#      preflight writes its stamp only on a full (non --pre-push) pass
#      with a clean tree.
#
# Called by: scripts/dev/create-pr.sh, after the actor gate.
#
# Env vars:
#   SKIP_READY_CHECK   — a reason; bypasses the gate and logs the reason.
#   HERMES_SELF_TEST   — set to 1 to run the fixture matrix and exit.
#   READY_SKIP_FETCH   — set to 1 to use the local origin/main as is
#                        (self-test only; a real run always fetches).
#
# Exit codes:
#   0 — ready (or bypassed with a reason)
#   5 — not ready; the failing check says what to do

set -uo pipefail

STAMP_NAME=hermes-preflight-pass

stamp_path() {
    echo "$(git rev-parse --git-dir)/$STAMP_NAME"
}

refuse() {
    echo "NOT READY: $1" >&2
    echo "  fix: $2" >&2
    exit 5
}

check_ready() {
    local head upstream main_tip
    head=$(git rev-parse HEAD) || refuse "not a git checkout" "run from the repo"

    if [[ -n "$(git status --porcelain)" ]]; then
        refuse "uncommitted changes in the working tree" "commit them (or drop them), then re-run"
    fi

    upstream=$(git rev-parse --verify -q '@{upstream}') \
        || refuse "branch has no upstream" "git push -u origin $(git branch --show-current)"
    if [[ "$upstream" != "$head" ]]; then
        refuse "HEAD ${head:0:7} is not pushed (upstream is ${upstream:0:7})" "git push"
    fi

    if [[ "${READY_SKIP_FETCH:-0}" != "1" ]]; then
        git fetch -q origin main || refuse "could not fetch origin/main" "check the network and retry"
    fi
    main_tip=$(git rev-parse origin/main) || refuse "no origin/main" "git fetch origin main"

    if ! git merge-base --is-ancestor "$main_tip" "$head"; then
        if ! git merge-tree --write-tree "$main_tip" "$head" >/dev/null 2>&1; then
            refuse "HEAD conflicts with origin/main ${main_tip:0:7}" \
                "git merge origin/main, resolve the conflicts, run pr-preflight.sh, push"
        fi
        refuse "HEAD is behind origin/main ${main_tip:0:7}" \
            "git merge origin/main, run pr-preflight.sh, push"
    fi

    local stamp
    stamp=$(cat "$(stamp_path)" 2>/dev/null || true)
    if [[ "$stamp" != "$head" ]]; then
        refuse "the full preflight has not passed on HEAD ${head:0:7}" \
            "bash scripts/dev/pr-preflight.sh (without --pre-push)"
    fi

    echo "ready: HEAD ${head:0:7} is pushed, contains origin/main ${main_tip:0:7}, and passed the full preflight."
}

if [[ "${HERMES_SELF_TEST:-0}" == "1" ]]; then
    fail=0
    script="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
    tmp=$(mktemp -d -t pr-ready-selftest.XXXXXX)
    trap 'rm -rf "$tmp"' EXIT
    # Hooks (pre-commit) export GIT_DIR, GIT_WORK_TREE and GIT_INDEX_FILE
    # for the real repo. Inherited, they would point every scratch git
    # command below at the real repository. Drop them all first.
    while IFS= read -r var; do
        unset "$var"
    done < <(env | sed -n 's/^\(GIT_[A-Z_]*\)=.*/\1/p')
    remote="$tmp/remote.git"
    work="$tmp/work"
    git init -q --bare "$remote"
    git init -q "$work"
    # Belt and braces: refuse to go on unless git really resolves to the
    # scratch repo.
    scratch_git=$(cd "$work" && git rev-parse --git-dir)
    if [[ "$scratch_git" != ".git" ]]; then
        echo "SELF-TEST ABORT: git resolves to $scratch_git, not the scratch repo" >&2
        exit 1
    fi
    g() { git -C "$work" -c user.name=t -c user.email=t@invalid -c core.autocrlf=false "$@"; }
    g commit -q --allow-empty -m "chore: base"
    g branch -M main
    g remote add origin "$remote"
    g push -q origin main
    g checkout -q -b feature
    echo a > "$work/a.txt"
    g add a.txt
    g commit -q -m "feat: a"
    g push -q -u origin feature

    run() {
        (cd "$work" && env -u HERMES_SELF_TEST -u SKIP_READY_CHECK READY_SKIP_FETCH=1 bash "$script") >/dev/null 2>&1
    }
    expect() {
        local desc=$1 want=$2 got=0
        run || got=$?
        if [[ "$got" != "$want" ]]; then
            echo "SELF-TEST FAIL: $desc: rc=$got, want $want" >&2
            fail=1
        fi
    }
    stamp() { git -C "$work" rev-parse HEAD > "$(git -C "$work" rev-parse --absolute-git-dir)/$STAMP_NAME"; }

    expect "no preflight stamp" 5
    stamp
    expect "pushed, up to date, stamped" 0

    echo b > "$work/b.txt"
    expect "untracked file in the tree" 5
    g add b.txt
    g commit -q -m "feat: b"
    stamp
    expect "commit not pushed" 5
    g push -q

    g checkout -q main
    echo m > "$work/m.txt"
    g add m.txt
    g commit -q -m "chore: main moves"
    g push -q origin main
    g fetch -q origin
    g checkout -q feature
    expect "behind origin/main" 5

    g checkout -q main
    echo conflict > "$work/a.txt"
    g add a.txt
    g commit -q -m "chore: conflict"
    g push -q origin main
    g fetch -q origin
    g checkout -q feature
    expect "conflicts with origin/main" 5

    g merge -q -X ours --no-edit origin/main
    g push -q
    expect "merged main, but stamp is for an older head" 5
    stamp
    expect "merged main, pushed, stamped" 0

    [[ "$fail" == 0 ]] && echo "SELF-TEST PASS: pr-ready-check.sh handles 8 fixtures correctly."
    exit "$fail"
fi

if [[ -n "${SKIP_READY_CHECK:-}" ]]; then
    echo "WARNING: ready-to-merge gate bypassed: ${SKIP_READY_CHECK}" >&2
    exit 0
fi

check_ready
