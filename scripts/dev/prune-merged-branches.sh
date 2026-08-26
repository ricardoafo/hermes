#!/usr/bin/env bash
# Prune drift-accumulated branches — local and remote.
#
# Covers three gaps neither `delete_branch_on_merge:true` nor the
# merge-queue's explicit `git.deleteRef` addresses (see openspec change
# `branch-hygiene-prune-script`):
#
#   Phase A — local branches whose every commit is already in main
#             under a different SHA (squash-merge remnant).
#             Detected via `git cherry origin/main <branch>` — zero
#             `+` lines means every patch is already upstream.
#   Phase B — remote branches attached to CLOSED-but-not-merged PRs.
#             Detected via `gh pr list --state closed` filtered on
#             `mergedAt == null`.
#   Phase C — remote branches with no PR at all. Detected via
#             `git ls-remote --heads origin` minus every PR head.
#
# `--dry-run` (default) reports only. `--yes` actually deletes; branches
# whose remote name matches `worktree-agent-*|agent-*|*-wip` prompt Y/n
# per branch (sub-agent scratch may still be in flight).
#
# Usage:
#     bash scripts/dev/prune-merged-branches.sh              # dry run
#     bash scripts/dev/prune-merged-branches.sh --yes        # execute
#     HERMES_SELF_TEST=1 bash scripts/dev/prune-merged-branches.sh
#
# Exit codes:
#     0 — dry-run completed / --yes completed without errors
#     1 — `gh` not authenticated / repo lookup failed
#     2 — usage error

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# ────────────────────────────────────────────────────────────────
# Self-test harness (fires when HERMES_SELF_TEST=1).
#
# Bootstraps a throwaway repo with one branch of each drift category
# and asserts categorisation is correct. Never touches the real repo.
# ────────────────────────────────────────────────────────────────
if [ "${HERMES_SELF_TEST:-0}" = "1" ]; then
    set -e
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

        # ── Fixture 1: local branch patch-equivalent to main HEAD.
        git checkout -q -b feat-squash-merged
        echo "line-A" >> file.txt
        git commit -q -am "add line-A on branch"
        git checkout -q main
        # Simulate the same patch landing on main as a squash (different SHA):
        echo "line-A" >> file.txt
        git commit -q -am "add line-A via squash on main"
        git push -q origin main

        # ── Fixture 2: local branch with a truly-unmerged commit.
        git checkout -q -b feat-real-unmerged
        echo "novel-content" >> file.txt
        git commit -q -am "add novel content on branch (NOT in main)"

        git checkout -q main

        # Now run Phase A logic exactly as the script does.
        A_candidates=()
        current="$(git rev-parse --abbrev-ref HEAD)"
        for b in $(git branch --format='%(refname:short)' \
                    | grep -Ev '^(main|'"$current"')$'); do
            if [ -z "$(git cherry origin/main "$b" 2>/dev/null | grep '^+' || true)" ]; then
                A_candidates+=("$b")
            fi
        done

        # Assert: feat-squash-merged IS a candidate, feat-real-unmerged is NOT.
        if ! printf '%s\n' "${A_candidates[@]}" | grep -qx 'feat-squash-merged'; then
            echo "SELF-TEST FAIL: feat-squash-merged should be in Phase A candidates" >&2
            exit 1
        fi
        if printf '%s\n' "${A_candidates[@]}" | grep -qx 'feat-real-unmerged'; then
            echo "SELF-TEST FAIL: feat-real-unmerged should NOT be in Phase A candidates" >&2
            exit 1
        fi

        # Assert: no branches were destructively deleted.
        for b in feat-squash-merged feat-real-unmerged; do
            git rev-parse --verify "$b" >/dev/null 2>&1 || {
                echo "SELF-TEST FAIL: branch $b was deleted (self-test must be non-destructive)" >&2
                exit 1
            }
        done

        echo "SELF-TEST PASS: Phase A detection categorises correctly and does not delete."
    )
    exit 0
fi

# ────────────────────────────────────────────────────────────────
# Real invocation — arg parsing + preconditions.
# ────────────────────────────────────────────────────────────────
MODE="dry-run"
case "${1:-}" in
    ""|"--dry-run") MODE="dry-run" ;;
    "--yes")        MODE="yes" ;;
    "-h"|"--help")
        sed -n '2,30p' "$0" | sed 's/^# \?//'
        exit 0
        ;;
    *)
        echo "ERROR: unknown flag '$1' (use --dry-run or --yes)" >&2
        exit 2
        ;;
esac

cd "$REPO_ROOT"

if ! command -v gh >/dev/null 2>&1; then
    echo "ERROR: 'gh' CLI is required (Phase B + C query GitHub)" >&2
    exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
    echo "ERROR: 'gh' is not authenticated — run 'gh auth login'" >&2
    exit 1
fi

# Refresh remote-tracking refs before we compare.
echo "Fetching origin (pruning stale tracking refs)..."
git fetch origin --prune >/dev/null

# ────────────────────────────────────────────────────────────────
# Phase A — local branches patch-equivalent to something in main.
# ────────────────────────────────────────────────────────────────
echo ""
echo "━━━ Phase A: local squash-merged branches ━━━"
A_candidates=()
current="$(git rev-parse --abbrev-ref HEAD)"
for b in $(git branch --format='%(refname:short)' | grep -Ev '^(main|'"$current"')$'); do
    if [ -z "$(git cherry origin/main "$b" 2>/dev/null | grep '^+' || true)" ]; then
        A_candidates+=("$b")
    fi
done

if [ ${#A_candidates[@]} -eq 0 ]; then
    echo "  (nothing to prune)"
else
    for b in "${A_candidates[@]}"; do
        last="$(git log -1 --format='%s' "$b" 2>/dev/null | head -c 70)"
        echo "  $b  -- $last"
    done
    echo ""
    echo "  ${#A_candidates[@]} local branches every-commit-already-in-main."
fi

# ────────────────────────────────────────────────────────────────
# Phase B — remote refs attached to CLOSED-not-merged PRs.
# ────────────────────────────────────────────────────────────────
echo ""
echo "━━━ Phase B: remote refs for CLOSED-not-merged PRs ━━━"
B_candidates=()
# `gh pr list` can emit the same headRefName twice when a branch had
# multiple CLOSED PRs against it — dedupe before probing origin.
while IFS= read -r headref; do
    [ -z "$headref" ] && continue
    if git ls-remote --exit-code --heads origin "$headref" >/dev/null 2>&1; then
        B_candidates+=("$headref")
    fi
done < <(gh pr list --state closed --limit 500 \
            --json headRefName,mergedAt \
            --jq '.[] | select(.mergedAt == null) | .headRefName' | sort -u)

if [ ${#B_candidates[@]} -eq 0 ]; then
    echo "  (nothing to prune)"
else
    for b in "${B_candidates[@]}"; do
        echo "  origin/$b"
    done
    echo ""
    echo "  ${#B_candidates[@]} remote refs orphaned by CLOSED PRs."
fi

# ────────────────────────────────────────────────────────────────
# Phase C — remote refs with no PR at all (orphan pushes).
# ────────────────────────────────────────────────────────────────
echo ""
echo "━━━ Phase C: remote refs with no PR (orphan pushes) ━━━"
C_candidates=()
all_pr_heads="$(gh pr list --state all --limit 500 \
                    --json headRefName --jq '.[].headRefName' | sort -u)"
protected_names="$(printf '%s\nmain\n' "$all_pr_heads")"
while IFS= read -r remote_ref; do
    [ -z "$remote_ref" ] && continue
    branch_name="${remote_ref#refs/heads/}"
    if ! printf '%s\n' "$protected_names" | grep -qFx "$branch_name"; then
        C_candidates+=("$branch_name")
    fi
done < <(git ls-remote --heads origin | awk '{print $2}')

if [ ${#C_candidates[@]} -eq 0 ]; then
    echo "  (nothing to prune)"
else
    for b in "${C_candidates[@]}"; do
        prompt_mark=""
        case "$b" in
            worktree-agent-*|agent-*|*-wip)
                prompt_mark=" (will prompt Y/n — sub-agent scratch pattern)"
                ;;
        esac
        echo "  origin/$b$prompt_mark"
    done
    echo ""
    echo "  ${#C_candidates[@]} remote refs without any PR."
fi

# ────────────────────────────────────────────────────────────────
# Totals + action.
# ────────────────────────────────────────────────────────────────
total=$(( ${#A_candidates[@]} + ${#B_candidates[@]} + ${#C_candidates[@]} ))
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TOTAL candidates: $total  (A=${#A_candidates[@]}  B=${#B_candidates[@]}  C=${#C_candidates[@]})"

if [ "$MODE" = "dry-run" ]; then
    echo "Run with --yes to actually delete $total branches."
    exit 0
fi

# ────────────────────────────────────────────────────────────────
# --yes: execute deletions.
# ────────────────────────────────────────────────────────────────
echo ""
echo "━━━ Deleting Phase A (local) ━━━"
for b in "${A_candidates[@]}"; do
    if git branch -D "$b" >/dev/null 2>&1; then
        echo "  DEL local  $b"
    else
        echo "  SKIP local $b (git branch -D failed)"
    fi
done

echo ""
echo "━━━ Deleting Phase B (remote, CLOSED PR heads) ━━━"
for b in "${B_candidates[@]}"; do
    if git push origin --delete "$b" >/dev/null 2>&1; then
        echo "  DEL remote $b"
    else
        echo "  SKIP remote $b (push --delete failed — already gone?)"
    fi
done

echo ""
echo "━━━ Deleting Phase C (remote, orphan pushes) ━━━"
for b in "${C_candidates[@]}"; do
    case "$b" in
        worktree-agent-*|agent-*|*-wip)
            printf "  DELETE origin/%s ? [y/N] " "$b"
            read -r reply < /dev/tty
            if [ "$reply" != "y" ] && [ "$reply" != "Y" ]; then
                echo "    kept"
                continue
            fi
            ;;
    esac
    if git push origin --delete "$b" >/dev/null 2>&1; then
        echo "  DEL remote $b"
    else
        echo "  SKIP remote $b (push --delete failed)"
    fi
done

echo ""
echo "Done."
