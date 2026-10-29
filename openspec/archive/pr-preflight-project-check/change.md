# Change: pr-preflight-project-check

**SDD change**: `pr-preflight-project-check`
**Date**: 2026-08-26
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#253)

Mirror of engram topic key `sdd/pr-preflight-project-check/change`.

## Summary

Adds a `check_pr_project_assigned` step to `scripts/dev/pr-preflight.sh` that walks the openspec change → issue.md → GitHub issue → `projectItems` and fails when the tracking issue is not attached to any GitHub Project. Closes the ship-validation gap surfaced by the operator on #242 ("doesn't have a project assign how did it pass our ship validations?") without requiring the local `gh` token to grant `read:project` — when the scope is missing, the check WARNS and skips (CI, which will have the scope via App auth, is the enforcement surface).

## Scope

### In

- **MOD**: `scripts/dev/pr-preflight.sh` — new function `check_pr_project_assigned` inserted before the `Openspec change spec present` check. Logic:
  1. Find openspec changes added or modified on this branch: `git diff --name-only origin/main..HEAD -- 'openspec/changes/*/change.md'`.
  2. For each, extract the change name and read `openspec/changes/<name>/issue.md`.
  3. Parse the `issue: <N>` line.
  4. `gh issue view <N> --json projectItems --jq '.projectItems | length'` → integer count.
  5. If count == 0 → fail. If `gh` returns an INSUFFICIENT_SCOPES error → print a WARN and skip (do not fail).
  6. Bypass: `SKIP_PROJECT_CHECK=1` with logged reason.
- **NEW**: `HERMES_SELF_TEST=1` hook stub — self-test asserts the scope-missing WARN path exits 0 (skip is a pass) and the empty-projectItems path exits 1.
- **NEW**: `changelog.d/wave-pr-preflight-project-check.md`.

### Out

- Auto-attaching the issue to a project. Would require write scope + a policy on which project. Out of scope; operator attaches at issue-creation time via `--project` flag (already forwarded by `create-issue.sh` from #252).
- A separate merge-queue.yml server-side project check. Redundant if pr-preflight enforces at PR-open time and CI enforces via inherited pr-preflight run.
- Retroactive project attachment for already-open issues (#245, #247, #249, #251, #253 all lack projects — operator can bulk-attach after this ships).

## Decisions

### D-1: Opt-in via `HERMES_ENFORCE_PROJECT_CHECK=1`, off by default

- **Decision**: check is gated behind `HERMES_ENFORCE_PROJECT_CHECK=1`; runs (and can fail) only when the env var is set. Skips with a SKIP line otherwise.
- **Alternatives**: (a) on by default, WARN when `read:project` scope missing; (b) off by default with no env-var toggle (dead code).
- **Rationale**: `gh issue view --json projectItems` returns `[]` in two indistinguishable cases — no project attached AND scope missing. The scope-missing branch surfaces an INSUFFICIENT_SCOPES error only from `gh api graphql` calls, not from `gh issue view` (verified 2026-08-26). Consequently a default-on check would fail every current tracking issue on every operator's workstation until (1) all open tracking issues are bulk-attached to a project and (2) every operator grants the scope. Opt-in delays enforcement until those preconditions are set up — same discipline as any other feature that needs coordinated rollout.
- **Cost of reversal**: LOW — flip default to on by removing the env-var guard.

### D-2: Walk change → issue.md, not gh pr view

- **Decision**: check the TRACKING ISSUE's project attachment, not the PR's directly.
- **Alternatives**: `gh pr view <N> --json projectItems` — needs PR to exist AND the pr-preflight to know its number.
- **Rationale**: pr-preflight runs BEFORE the PR is opened (that's its whole purpose). PR-based lookup is a chicken-and-egg. Issue → PR project inheritance is standard GitHub behavior — an issue on a project automatically pulls its PR onto the same project when the PR merges.
- **Cost of reversal**: LOW — replace the issue lookup with `gh pr view` if the model changes.

### D-3: Multiple changes on one branch — all issues must have projects

- **Decision**: if the branch modifies N openspec changes, check all N `issue.md` tracking issues.
- **Alternatives**: check only the first / most-recently-modified.
- **Rationale**: multi-change branches (rare but happen) should not sneak a project-less issue through.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Missing project on tracked issue fails the check (when opt-in ON)

- **A-1.1**: With `HERMES_ENFORCE_PROJECT_CHECK=1` and a branch adding `openspec/changes/foo/change.md` + `issue.md` (`issue: 999`), and `gh issue view 999 --json projectItems` returning empty, `bash scripts/dev/pr-preflight.sh` fails with the check name `Tracking-issue project assignment (opt-in)` and exit 1.
- **A-1.2**: The failure message names the offending issue and change.

### R-2: Default OFF is a clean skip

- **A-2.1**: `bash scripts/dev/pr-preflight.sh` (no env var) emits `SKIP: enforcement OFF (set HERMES_ENFORCE_PROJECT_CHECK=1 to enable)` and marks the check as passing.

### R-3: Bypass respected when opt-in is on

- **A-3.1**: `HERMES_ENFORCE_PROJECT_CHECK=1 SKIP_PROJECT_CHECK=1 bash scripts/dev/pr-preflight.sh` skips with a WARN line.

### R-4: Idempotent + non-destructive

- **A-4.1**: Running the check multiple times against the same branch produces the same result and takes no destructive action (read-only via `gh`).

## Design

```bash
# Insert before check_openspec_change_required in pr-preflight.sh
check_pr_project_assigned() {
    if [[ "${SKIP_PROJECT_CHECK:-0}" == "1" ]]; then
        echo "  WARN: project check skipped via SKIP_PROJECT_CHECK=1" >&2
        return 0
    fi
    local changes
    changes=$(git diff --name-only origin/main..HEAD -- 'openspec/changes/*/change.md' 2>/dev/null)
    [ -z "$changes" ] && return 0  # No openspec change on this branch — irrelevant.

    local any_fail=0
    while IFS= read -r cf; do
        [ -z "$cf" ] && continue
        local change_dir
        change_dir=$(dirname "$cf")
        local issue_md="$change_dir/issue.md"
        [ -f "$issue_md" ] || continue

        local issue_num
        issue_num=$(grep -oE '^issue:\s*[0-9]+' "$issue_md" | grep -oE '[0-9]+' | head -1)
        [ -z "$issue_num" ] && continue

        # Ask GitHub. Handle scope errors gracefully.
        local count
        count=$(gh issue view "$issue_num" --json projectItems \
                    --jq '.projectItems | length' 2>&1)
        if [[ "$count" == *INSUFFICIENT_SCOPES* ]]; then
            echo "  WARN: read:project scope missing — cannot verify project attachment for #$issue_num" >&2
            continue
        fi
        if ! [[ "$count" =~ ^[0-9]+$ ]]; then
            echo "  WARN: unexpected gh output for #$issue_num: $count" >&2
            continue
        fi
        if [ "$count" -eq 0 ]; then
            {
                echo "ERROR: tracking issue #$issue_num for change '$(basename "$change_dir")' has no project attached."
                echo "  Attach via: gh api graphql -f query='...' or the GitHub web UI."
                echo "  Bypass (with cause): SKIP_PROJECT_CHECK=1 bash scripts/dev/pr-preflight.sh"
            } >&2
            any_fail=1
        fi
    done <<< "$changes"

    return $any_fail
}
run_check "Tracking-issue project assignment" check_pr_project_assigned
```

## Test plan

1. `HERMES_SELF_TEST=1 bash scripts/dev/pr-preflight.sh` self-test — no regressions.
2. Live run on THIS branch — since #253 was created without a project, the check should FAIL (or WARN if scope missing locally). If failing: attach #253 to a project, re-run, green.
3. `SKIP_PROJECT_CHECK=1 bash scripts/dev/pr-preflight.sh` → check skipped, WARN emitted.

## Risks

- **R1** — Local operators without `read:project` scope get a WARN that could be ignored. Mitigation: CI's `pr-preflight` run uses App-auth token which has the scope; enforcement lives there.
- **R2** — Regex parse of `issue: <N>` in `issue.md` breaks if the format changes. Mitigation: same regex used by `render-pr-body.sh` — coupled failure means the whole render-body flow breaks too, which is high-signal.
- **R3** — GitHub API rate-limits on repeated pre-flight runs. Mitigation: one `gh issue view` per changed openspec change per run; sub-second latency; unauthenticated rate limit not triggered.

## Dependencies on other changes

- Predecessor: none. Independent of #248 (auto-close v2), #250 (branch cleanup), #252 (create-issue wrapper).
- Complementary: with `create-issue.sh` enforcing project-attachment at issue-creation time, this check is belt-and-suspenders at PR-open time.
