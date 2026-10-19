# Change: branch-hygiene-prune-script

**SDD change**: `branch-hygiene-prune-script`
**Date**: 2026-08-26
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#249)

Mirror of engram topic key `sdd/branch-hygiene-prune-script/change`.

## Summary

Adds `scripts/dev/prune-merged-branches.sh` to cover the three drift gaps neither `delete_branch_on_merge:true` nor the merge-queue's explicit `git.deleteRef` catch: CLOSED-but-not-merged remote refs, never-a-PR remote refs, and all local branches (squash-merge rewrites the SHA so `git branch --merged` refuses to clean them). `--dry-run` default, `--yes` to actually delete. Runs locally by an operator; not automated in CI (deleting branches from a scheduled workflow is too risky given the "never-a-PR" gap includes legitimate in-flight sub-agent scratch).

## Scope

### In

- **NEW**: `scripts/dev/prune-merged-branches.sh` — three-phase pruner with `--dry-run` (default) and `--yes` (execute):
  - **Phase A (local squash-merged)**: iterate every local branch (excluding `main` and the current branch); for each, run `git cherry origin/main <branch>` — if it produces zero `+` lines every commit is already in main under a different SHA, safe to `git branch -D`.
  - **Phase B (remote CLOSED-not-merged)**: `gh pr list --state closed --json headRefName,mergedAt --jq '.[] | select(.mergedAt == null) | .headRefName'` — for each such head that still exists on origin, `git push origin --delete <ref>`.
  - **Phase C (remote never-a-PR)**: `git ls-remote --heads origin` minus `main` minus all PR heads (open + closed) = orphaned pushes. Delete with `--yes`. Interactive prompt per branch even with `--yes` when the branch matches `worktree-agent-*|agent-*|.*-wip$` (paranoid — sub-agent scratch may still be in use).
- **NEW**: `HERMES_SELF_TEST=1` fixture matrix — fake repo scenario with one branch of each drift type, asserts the script categorises correctly and takes no destructive action in dry-run mode.
- **MOD**: `AGENTS.md` — one paragraph under §"On branch discipline" (or added as a new section if none exists) telling operators + agents that this is the recommended cleanup step to run monthly.
- **NEW**: `changelog.d/wave-branch-hygiene-prune-script.md`.

### Out

- Automated scheduled deletion in a workflow. Too risky given Phase C includes the "never-a-PR" gap — sub-agent worktree branches from an in-flight session would look identical to orphaned scratch. Manual `--yes` invocation only.
- Extending the merge-queue to also delete on CLOSED — separate change if operators want it. Rationale: closing a PR often precedes re-opening from a rebased branch (Path B pattern used for #242 → #244); auto-delete on close would race the re-open.
- Retro-clean of the current backlog (103 local, 40 remote). The very first `--yes` run of this script IS the retro-clean.

## Decisions

### D-1: Three phases, one script, one `--yes` flag

- **Decision**: single script covering all three drift gaps. `--yes` applies to all phases.
- **Alternatives**: (a) three separate scripts `prune-local-merged.sh` / `prune-remote-closed.sh` / `prune-remote-orphaned.sh`; (b) subcommands `... --phase local|remote-closed|remote-orphaned`.
- **Rationale**: operators cleaning branches want one command, not a checklist of three. The three phases are cheap enough to run together (all read-only in dry-run mode). Subcommands add ceremony without reducing risk.
- **Cost of reversal**: LOW — splitting is a mechanical refactor if the combined script ever becomes unwieldy.

### D-2: `git cherry origin/main <branch>` is the local-merged detector

- **Decision**: local branch is "safely deletable" when `git cherry origin/main <branch>` outputs zero lines starting with `+`.
- **Alternatives**: (a) `git branch --merged main` — misses squash-merge because the tip SHA is not literally in `main`'s ancestry. (b) `git log main..<branch> --oneline` — same failure mode. (c) diff the trees at `main` vs `<branch>` — misses branches with legitimate diverging content, false positive on truly-unmerged branches.
- **Rationale**: `git cherry` compares patch-IDs (same textual change, different SHA) — precisely the case squash-merge creates. If every commit on the branch has a patch-equivalent already in `main`, the branch adds nothing.
- **Cost of reversal**: LOW — swapping the detection command is one line.

### D-3: Interactive per-branch confirm for suspicious remote names, even with `--yes`

- **Decision**: patterns `worktree-agent-*`, `agent-*`, `*-wip` prompt Y/n per branch in `--yes` mode.
- **Alternatives**: (a) blanket delete on `--yes`; (b) always exclude those patterns from Phase C.
- **Rationale**: sub-agent worktree branches from a currently-running session are indistinguishable from abandoned scratch. Losing an in-flight sub-agent's checkpoint destroys ~30 min of recovery-material per the `sub-agent-prompt-checklist.md` item 2 discipline. One extra `y` per branch is cheap insurance.
- **Cost of reversal**: LOW — remove the pattern list to make it non-interactive.

### D-4: Script is manual — no scheduled workflow

- **Decision**: no `.github/workflows/branch-hygiene.yml`. Operator runs it.
- **Alternatives**: weekly cron that runs Phases A+B automatically (skip Phase C).
- **Rationale**: sub-agent worktree branches (Phase C category) are legitimate in-flight state. A scheduled Phase-B-only workflow WOULD be safe (CLOSED PRs are terminal), but the incremental value is low and it adds one more workflow to maintain. Revisit if the same operator has to run the script by hand every week for a month.
- **Cost of reversal**: LOW — add a workflow later, unchanged script.

## Requirements

### R-1: Three drift categories are correctly detected

- **A-1.1**: `HERMES_SELF_TEST=1 bash scripts/dev/prune-merged-branches.sh` — fixture matrix asserts each of the three categories is enumerated in the dry-run report and none is deleted.
- **A-1.2**: The script's dry-run output distinguishes the three categories with a labelled section header per phase.

### R-2: Dry-run is the default

- **A-2.1**: Invocation with no flags produces a report but performs no `git branch -D`, `git push --delete`, or destructive git action. Verified by post-run `git rev-parse <branch>` succeeding for every listed candidate.
- **A-2.2**: The report ends with a line matching `Run with --yes to actually delete N branches`.

### R-3: `--yes` respects the interactive confirm for suspicious names

- **A-3.1**: With `--yes` and stdin containing `n` for each prompted branch, no `worktree-agent-*` / `agent-*` / `*-wip` branch is deleted.
- **A-3.2**: With `--yes` and stdin containing `y`, every prompted branch IS deleted.

### R-4: Idempotent

- **A-4.1**: Running the script twice in a row with `--yes` — the second run's Phase A/B/C candidate lists are empty (no residual drift from the first pass).

### R-5: Script self-test hooks into `pr-preflight.sh`

- **A-5.1**: `HERMES_SELF_TEST=1 bash scripts/dev/prune-merged-branches.sh` exits 0 in a clean checkout (no fixture setup needed — self-test bootstraps its own tmp repo).

## Design

Script skeleton (~180 LOC):

```bash
#!/usr/bin/env bash
# Prune drift-accumulated branches — local and remote.
#
# Covers three gaps neither `delete_branch_on_merge:true` nor the
# merge-queue's explicit `git.deleteRef` addresses:
#
#   Phase A — local branches whose every commit is already in main
#             under a different SHA (squash-merge remnant).
#   Phase B — remote branches attached to CLOSED-but-not-merged PRs.
#   Phase C — remote branches with no PR at all.
#
# --dry-run (default) reports only; --yes actually deletes.

set -uo pipefail

MODE="${1:-dry-run}"
: "${HERMES_SELF_TEST:=0}"

# ...
# Phase A: local squash-merged
# ...
git fetch origin --prune >/dev/null 2>&1
local_prune_candidates=()
current="$(git rev-parse --abbrev-ref HEAD)"
for b in $(git branch --format='%(refname:short)' | grep -Ev '^(main|'"$current"')$'); do
    if [ -z "$(git cherry origin/main "$b" 2>/dev/null | grep '^\+')" ]; then
        local_prune_candidates+=("$b")
    fi
done

# ...
# Phase B: remote CLOSED-not-merged
# ...
gh pr list --state closed --limit 500 --json headRefName,mergedAt \
    --jq '.[] | select(.mergedAt == null) | .headRefName' \
    | while read -r headref; do
    if git ls-remote --exit-code --heads origin "$headref" >/dev/null 2>&1; then
        remote_prune_closed+=("$headref")
    fi
done

# ...
# Phase C: remote never-a-PR
# ...
all_pr_heads="$(gh pr list --state all --limit 500 --json headRefName \
    --jq '.[].headRefName' | sort -u)"
git ls-remote --heads origin | awk '{print $2}' | sed 's|refs/heads/||' \
    | grep -vFf <(printf '%s\nmain\n' "$all_pr_heads") \
    | while read -r orphan; do
    remote_prune_orphaned+=("$orphan")
done

# Report all three lists.
# If --yes:
#   For each Phase A candidate: git branch -D <b>
#   For each Phase B candidate: git push origin --delete <b>
#   For each Phase C candidate: prompt if worktree-agent-*|agent-*|*-wip,
#                                else git push origin --delete <b>
```

Fixture matrix for `HERMES_SELF_TEST=1`:

```text
tmp-repo/
├── main
├── feat-squash-merged      # commit patch-equivalent to main HEAD
├── feat-real-unmerged      # commit with content not in main
└── (remote refs simulated via `git remote add origin <local bare repo>`)
```

Assert: Phase A puts `feat-squash-merged` in the delete list, `feat-real-unmerged` NOT in it.

## Test plan

1. `HERMES_SELF_TEST=1 bash scripts/dev/prune-merged-branches.sh` — self-test exit 0.
2. `bash scripts/dev/prune-merged-branches.sh` (in the real repo) — dry-run produces categorised report with expected counts (~90 local, ~2 remote-closed, ~5 remote-orphaned including `worktree-agent-*`).
3. `bash scripts/dev/prune-merged-branches.sh --yes < answers.txt` — with `n` for every prompt, no worktree branches deleted.
4. `bash scripts/dev/prune-merged-branches.sh --yes` (second run, immediately after a successful first `--yes` run) — empty candidate lists (idempotency check).
5. `bash scripts/dev/pr-preflight.sh` — all 14 checks green.

## Risks

- **R1** — `git cherry` is patch-ID based; if a branch was rebased AND squash-merged, the patch IDs no longer match and the branch looks "unmerged". Mitigation: false negative is the safe direction (won't delete). Operator notices unmerged branch, decides manually.
- **R2** — Phase C over-collects: any push to origin that never had a PR (e.g. archival branches like `preservation/pre-refactor-snapshot`) will be flagged. Mitigation: manual review of the dry-run report before `--yes`; interactive prompt catches the sub-agent-worktree pattern which is the most common false positive.
- **R3** — `gh pr list --limit 500` won't paginate beyond 500 PRs. Mitigation: fine for years; add `--all` pagination if the repo ever exceeds it.
- **R4** — Race condition: an operator merges a PR between dry-run and `--yes`. Mitigation: `--yes` re-computes the candidate lists (does NOT trust a stale dry-run).

## Dependencies on other changes

- No hard dependency. Complements PR #248 (auto-close v2) — with issues auto-closing on merge, the PR→issue→branch chain is finally self-cleaning end-to-end (except for the gaps this script fills).
- Cite in `AGENTS.md`: recommend agents run this at end-of-session as part of the "session close" discipline.
