#!/usr/bin/env bash
# Prune drift-accumulated worktrees and branches — local and remote.
#
# Covers the gaps neither `delete_branch_on_merge:true` nor the
# merge-queue's explicit `git.deleteRef` addresses (see openspec change
# `branch-hygiene-prune-script` and issue #472):
#
#   Phase W — local worktrees (never the primary tree) whose branch is
#             finished: its PR is MERGED/CLOSED or every commit is in
#             origin/main. Removed only when the worktree is clean and
#             has nothing unpushed, so in-flight writer work survives.
#   Phase A — local branches, not checked out anywhere, that are
#             finished by the same test. The PR-state check catches
#             multi-commit squash merges that `git cherry` cannot.
#   Phase B — remote branches whose PR was CLOSED-not-merged more than
#             HERMES_PRUNE_CLOSED_DAYS (default 3) days ago. The delay
#             leaves room to reopen a PR closed by mistake.
#   Phase C — remote branches with no PR at all whose last commit is
#             older than HERMES_PRUNE_ORPHAN_DAYS (default 14) days.
#             Writers push for hours before opening a PR, so a young
#             no-PR branch is in-flight work, not an orphan.
#
# `--dry-run` (default) reports only. `--yes` deletes. `--local-only`
# runs W + A (what launch-agent.sh runs automatically); `--remote-only`
# runs B + C (what the weekly scheduled job runs).
#
# Usage:
#     bash scripts/dev/prune-merged-branches.sh                  # dry run, all phases
#     bash scripts/dev/prune-merged-branches.sh --yes            # execute, all phases
#     bash scripts/dev/prune-merged-branches.sh --yes --local-only
#     HERMES_SELF_TEST=1 bash scripts/dev/prune-merged-branches.sh
#
# Exit codes:
#     0 — dry-run completed / --yes completed without errors
#     1 — `gh` not authenticated / repo lookup failed
#     2 — usage error

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CLOSED_DAYS="${HERMES_PRUNE_CLOSED_DAYS:-3}"
ORPHAN_DAYS="${HERMES_PRUNE_ORPHAN_DAYS:-14}"

# A branch is finished when nothing on it would be lost: its tip is
# contained in the head of one of its MERGED/CLOSED PRs, or every patch
# on it is already in origin/main. Comparing against the PR head (not
# just the name) keeps commits made after the PR closed, and keeps a
# reused branch name. $1 = branch, $2 = "<branch> <head sha>" lines for
# PRs that are MERGED or CLOSED.
branch_is_finished() {
    local tip sha
    tip="$(git rev-parse --verify -q "$1^{commit}")" || return 1
    for sha in $(printf '%s\n' "$2" | awk -v b="$1" '$1 == b { print $2 }'); do
        if git merge-base --is-ancestor "$tip" "$sha" 2>/dev/null; then
            return 0
        fi
    done
    [ -z "$(git cherry origin/main "$1" 2>/dev/null | grep '^+' || true)" ]
}

# A worktree is safe to remove only with no uncommitted or untracked files.
worktree_is_clean() {
    [ -z "$(git -C "$1" status --porcelain 2>/dev/null)" ]
}

# Prints "<path>\t<branch>" for every worktree except the primary one.
list_secondary_worktrees() {
    git worktree list --porcelain | awk '
        /^worktree / { path = substr($0, 10); n++ }
        /^branch /   { if (n > 1) { b = $2; sub("refs/heads/", "", b); print path "\t" b } }'
}

# ────────────────────────────────────────────────────────────────
# Self-test harness (fires when HERMES_SELF_TEST=1).
#
# Bootstraps a throwaway repo with one fixture per category and asserts
# categorisation is correct. Never touches the real repo.
# ────────────────────────────────────────────────────────────────
if [ "${HERMES_SELF_TEST:-0}" = "1" ]; then
    set -e
    # A caller's git environment would point the fixture's git calls at
    # the real repo (see the 2026-10-03 core.bare incident).
    unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE
    TMPDIR="$(mktemp -d)"
    trap 'rm -rf "$TMPDIR"' EXIT

    (
        cd "$TMPDIR"
        git init -q -b main upstream.git --bare
        git init -q -b main working
        cd working
        git config user.email "self-test@hermes.local"
        git config user.name "self-test"
        git remote add origin "$TMPDIR/upstream.git"

        echo "root" > file.txt
        git add file.txt
        git commit -q -m "root"
        git push -q origin main

        fail() { echo "SELF-TEST FAIL: $*" >&2; exit 1; }

        # Patch-identical squash: finished via git cherry.
        git checkout -q -b feat-squash-merged
        echo "line-A" >> file.txt
        git commit -q -am "add line-A on branch"
        git checkout -q main
        echo "line-A" >> file.txt
        git commit -q -am "add line-A via squash on main"
        git push -q origin main

        # Unmerged work with no PR: never finished.
        git checkout -q -b feat-real-unmerged
        echo "novel-content" >> file.txt
        git commit -q -am "add novel content on branch (NOT in main)"
        git checkout -q main

        # Multi-commit branch whose PR merged: cherry sees + lines, but
        # its tip is the merged PR's head, so it is finished.
        git checkout -q -b feat-multi-commit-merged
        echo "x" > other.txt; git add other.txt; git commit -q -m "one"
        echo "y" >> other.txt; git commit -q -am "two"
        merged_head="$(git rev-parse HEAD)"
        # Same PR branch name, but committed to after the PR merged.
        git checkout -q -b feat-after-merge
        echo "after-merge" >> other.txt; git commit -q -am "three, after the PR merged"
        git checkout -q main
        done_heads="$(printf '%s %s\n' feat-multi-commit-merged "$merged_head" feat-after-merge "$merged_head")"

        git fetch -q origin
        branch_is_finished feat-squash-merged "$done_heads" \
            || fail "feat-squash-merged should be finished"
        branch_is_finished feat-multi-commit-merged "$done_heads" \
            || fail "feat-multi-commit-merged (tip = merged PR head) should be finished"
        if branch_is_finished feat-real-unmerged "$done_heads"; then
            fail "feat-real-unmerged should NOT be finished"
        fi
        if branch_is_finished feat-after-merge "$done_heads"; then
            fail "a commit made after the PR merged should keep the branch"
        fi

        # Worktrees: one clean, one with an untracked file, one with a
        # staged change.
        for wt in clean dirty staged; do
            git worktree add -q "$TMPDIR/wt-$wt" -b "wt-$wt" main
            git -C "$TMPDIR/wt-$wt" push -q -u origin "wt-$wt"
        done
        echo "scratch" > "$TMPDIR/wt-dirty/untracked.txt"
        echo "z" >> "$TMPDIR/wt-staged/file.txt"
        git -C "$TMPDIR/wt-staged" add file.txt

        worktree_is_clean "$TMPDIR/wt-clean" || fail "wt-clean should be clean"
        if worktree_is_clean "$TMPDIR/wt-dirty"; then fail "wt-dirty should not be clean"; fi
        if worktree_is_clean "$TMPDIR/wt-staged"; then fail "wt-staged (staged change) should not be clean"; fi

        listed="$(list_secondary_worktrees | cut -f2 | sort | tr '\n' ' ')"
        [ "$listed" = "wt-clean wt-dirty wt-staged " ] \
            || fail "secondary worktrees should exclude the primary tree, got '$listed'"

        for b in feat-squash-merged feat-real-unmerged feat-multi-commit-merged feat-after-merge; do
            git rev-parse --verify -q "$b" >/dev/null \
                || fail "branch $b was deleted (self-test must be non-destructive)"
        done

        echo "SELF-TEST PASS: worktree and branch detection categorise correctly and do not delete."
    )
    exit 0
fi

# ────────────────────────────────────────────────────────────────
# Real invocation — arg parsing + preconditions.
# ────────────────────────────────────────────────────────────────
MODE="dry-run"
RUN_LOCAL=1
RUN_REMOTE=1
for arg in "$@"; do
    case "$arg" in
        "--dry-run")     MODE="dry-run" ;;
        "--yes")         MODE="yes" ;;
        "--local-only")  RUN_REMOTE=0 ;;
        "--remote-only") RUN_LOCAL=0 ;;
        "-h"|"--help")
            sed -n '2,34p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo "ERROR: unknown flag '$arg' (use --dry-run, --yes, --local-only, --remote-only)" >&2
            exit 2
            ;;
    esac
done
if [ "$RUN_LOCAL" = 0 ] && [ "$RUN_REMOTE" = 0 ]; then
    echo "ERROR: --local-only and --remote-only are mutually exclusive" >&2
    exit 2
fi

cd "$REPO_ROOT"

if ! command -v gh >/dev/null 2>&1; then
    echo "ERROR: 'gh' CLI is required (PR state decides what is finished)" >&2
    exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
    echo "ERROR: 'gh' is not authenticated — run 'gh auth login'" >&2
    exit 1
fi

echo "Fetching origin (pruning stale tracking refs)..."
git fetch origin --prune >/dev/null

now="$(date +%s)"
done_heads="$(gh pr list --state all --limit 1000 --json headRefName,headRefOid,state \
    --jq '.[] | select(.state != "OPEN") | "\(.headRefName) \(.headRefOid)"')"
open_heads="$(gh pr list --state open --limit 1000 --json headRefName --jq '.[].headRefName')"

W_candidates=()
A_candidates=()
B_candidates=()
C_candidates=()

if [ "$RUN_LOCAL" = 1 ]; then
    echo ""
    echo "━━━ Phase W: finished, clean worktrees ━━━"
    W_kept=0
    while IFS=$'\t' read -r path b; do
        [ -z "$path" ] && continue
        if printf '%s\n' "$open_heads" | grep -qFx "$b"; then
            W_kept=$((W_kept + 1)); continue
        fi
        if branch_is_finished "$b" "$done_heads" && worktree_is_clean "$path"; then
            W_candidates+=("$path")
            echo "  $path  ($b)"
        else
            W_kept=$((W_kept + 1))
        fi
    done < <(list_secondary_worktrees)
    [ ${#W_candidates[@]} -eq 0 ] && echo "  (nothing to prune)"
    echo "  kept $W_kept worktrees (open PR, unfinished, uncommitted or unpushed work)"

    echo ""
    echo "━━━ Phase A: finished local branches ━━━"
    checked_out="$(git worktree list --porcelain | awk '/^branch /{sub("refs/heads/","",$2); print $2}')"
    for b in $(git branch --format='%(refname:short)' | grep -v '^main$'); do
        if printf '%s\n' "$checked_out" | grep -qFx "$b"; then
            # Checked out now; becomes a candidate once Phase W removes its worktree.
            continue
        fi
        if printf '%s\n' "$open_heads" | grep -qFx "$b"; then
            continue
        fi
        if branch_is_finished "$b" "$done_heads"; then
            A_candidates+=("$b")
            echo "  $b  -- $(git log -1 --format='%s' "$b" 2>/dev/null | head -c 70)"
        fi
    done
    [ ${#A_candidates[@]} -eq 0 ] && echo "  (nothing to prune)"
fi

if [ "$RUN_REMOTE" = 1 ]; then
    echo ""
    echo "━━━ Phase B: remote heads of PRs closed >${CLOSED_DAYS}d ago, not merged ━━━"
    remote_heads="$(git ls-remote --heads origin | awk '{sub("refs/heads/","",$2); print $2}')"
    closed_cutoff=$(( now - CLOSED_DAYS * 86400 ))
    while IFS= read -r b; do
        [ -z "$b" ] && continue
        printf '%s\n' "$open_heads" | grep -qFx "$b" && continue
        printf '%s\n' "$remote_heads" | grep -qFx "$b" || continue
        B_candidates+=("$b")
        echo "  origin/$b"
    done < <(gh pr list --state closed --limit 1000 --json headRefName,mergedAt,closedAt \
                --jq ".[] | select(.mergedAt == null) | select((.closedAt | fromdateiso8601) < $closed_cutoff) | .headRefName" \
             | sort -u)
    [ ${#B_candidates[@]} -eq 0 ] && echo "  (nothing to prune)"

    echo ""
    echo "━━━ Phase C: remote branches with no PR, idle >${ORPHAN_DAYS}d ━━━"
    all_pr_heads="$(gh pr list --state all --limit 1000 --json headRefName --jq '.[].headRefName' | sort -u)"
    orphan_cutoff=$(( now - ORPHAN_DAYS * 86400 ))
    while IFS= read -r b; do
        [ -z "$b" ] || [ "$b" = "main" ] && continue
        printf '%s\n' "$all_pr_heads" | grep -qFx "$b" && continue
        last="$(git log -1 --format=%ct "origin/$b" 2>/dev/null || echo "$now")"
        if [ "$last" -lt "$orphan_cutoff" ]; then
            C_candidates+=("$b")
            echo "  origin/$b  (last commit $(( (now - last) / 86400 ))d ago)"
        fi
    done <<< "$remote_heads"
    [ ${#C_candidates[@]} -eq 0 ] && echo "  (nothing to prune)"
fi

total=$(( ${#W_candidates[@]} + ${#A_candidates[@]} + ${#B_candidates[@]} + ${#C_candidates[@]} ))
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TOTAL candidates: $total  (W=${#W_candidates[@]}  A=${#A_candidates[@]}  B=${#B_candidates[@]}  C=${#C_candidates[@]})"

if [ "$MODE" = "dry-run" ]; then
    echo "Run with --yes to delete them."
    exit 0
fi

for path in "${W_candidates[@]}"; do
    b="$(git -C "$path" rev-parse --abbrev-ref HEAD 2>/dev/null)"
    if git worktree remove "$path" >/dev/null 2>&1; then
        echo "  DEL worktree $path"
        # Its branch was skipped by Phase A while checked out.
        if [ -n "$b" ] && git branch -D "$b" >/dev/null 2>&1; then
            echo "  DEL local    $b"
        fi
    else
        echo "  SKIP worktree $path (git worktree remove refused)"
    fi
done
git worktree prune

for b in "${A_candidates[@]}"; do
    if git branch -D "$b" >/dev/null 2>&1; then
        echo "  DEL local    $b"
    else
        echo "  SKIP local   $b (git branch -D failed)"
    fi
done

for b in "${B_candidates[@]}" "${C_candidates[@]}"; do
    if git push origin --delete "$b" >/dev/null 2>&1; then
        echo "  DEL remote   $b"
    else
        echo "  SKIP remote  $b (push --delete failed — already gone?)"
    fi
done

echo ""
echo "Done."
