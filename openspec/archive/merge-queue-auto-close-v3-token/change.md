# Change: merge-queue-auto-close-v3-token

**SDD change**: `merge-queue-auto-close-v3-token`
**Date**: 2026-08-26
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#261)

Mirror of engram topic key `sdd/merge-queue-auto-close-v3-token/change`.

## Summary

Third attempt at auto-close on PR merge. v2 (PR #248) added a body-regex fallback that correctly identified the linked issues after every merge — but the `issues.update` call still failed with `Resource not accessible by integration`. Root cause: the GitHub App token used to bypass branch protection during `pulls.merge` does NOT have `Issues: Read & Write` at App-install time, and the workflow-declared `permissions: issues: write` grants that scope only to the default `GITHUB_TOKEN`, not to the App token. Fix: use a second Octokit instance authed with `github.token` for the issue-close calls only. The merge itself keeps the App token.

## Scope

### In

- **MOD**: `.github/workflows/merge-queue.yml` — inside the `Merge` step, instantiate `workflowOctokit = require('@actions/github').getOctokit(process.env.WORKFLOW_TOKEN)` and route `issues.get`, `issues.update`, `issues.createComment` through it. Add `WORKFLOW_TOKEN: ${{ github.token }}` to the step's `env`. Update the block header from "v2" to "v3" and record all three failure modes for the next debugger.
- **NEW**: `changelog.d/wave-merge-queue-auto-close-v3-token.md`.

### Out

- Granting `Issues: Read & Write` to the hermes-automation-bot App at the install level. Would work equally well but is silent when it lapses (permission grants have to be re-accepted every time they change). The two-token code fix is durable.
- Splitting the merge step into two separate steps (merge, then close). Would work but adds another workflow step + output plumbing. The dual-Octokit approach fits inside the existing step with a 3-line delta at the point of use.
- Retro-close of the 4 issues silently missed by v2 (#249, #251, #253, #255) — closed manually as part of the diagnostic that surfaced this bug.

## Decisions

### D-1: Dual Octokit in the same step, not a separate step

- **Decision**: instantiate a second Octokit inside the existing `Merge` step, authed with `WORKFLOW_TOKEN`. Use it only for the belt-and-suspenders block.
- **Alternatives**: (a) split into a new `Close linked issues` step with `github-token: ${{ github.token }}`; (b) grant Issues:Write to the App at install time.
- **Rationale**: Splitting the step requires plumbing outputs (PR number, body, closableIssues) between steps and duplicating the "did the merge succeed?" guard. Dual Octokit in one step is a 3-line delta at the point of use. App permission grant is fragile — silent when it lapses; code fix is self-documenting.
- **Cost of reversal**: LOW — collapse back to single Octokit if the App ever gains Issues:Write.

### D-2: `WORKFLOW_TOKEN` env, not inline `secrets.GITHUB_TOKEN`

- **Decision**: pass `github.token` to the step via env var `WORKFLOW_TOKEN`.
- **Alternatives**: reference `secrets.GITHUB_TOKEN` inside the script.
- **Rationale**: `secrets.GITHUB_TOKEN` is a workflow-level context reference that github-script's inline JS cannot resolve directly (it's evaluated at YAML-substitution time). Passing via env is the standard pattern in actions/github-script v9.
- **Cost of reversal**: LOW.

### D-3: Preserve v2's body-regex + GraphQL union — do NOT drop either source

- **Decision**: v3 is a token-permission fix; both v2 sources stay.
- **Alternatives**: replace GraphQL query with body-regex only, since v2's diagnostic proved GraphQL returns empty at merge time.
- **Rationale**: GraphQL's `closingIssuesReferences` may catch cross-repo linkages that body-regex ignores (e.g. `ricardoafo/other-repo#42`). Belt-and-suspenders means running both.
- **Cost of reversal**: LOW.

## Requirements

### R-1: v3 closes linked issues on merge

- **A-1.1**: After this PR merges, `gh issue view 261 --json state --jq .state` returns `"CLOSED"`.
- **A-1.2**: Merge-queue log for the next PR that merges after this contains a line matching `closed linked issue #NN` (rather than the v2 `failed to close linked issue`).

### R-2: `WORKFLOW_TOKEN` env is set

- **A-2.1**: `grep -F 'WORKFLOW_TOKEN' .github/workflows/merge-queue.yml` returns at least one line.

### R-3: Idempotency preserved

- **A-3.1**: The `issues.get` → `if (existing.state === 'closed') continue` guard from v2 is unchanged. Re-runs do not spam comments.

## Design

Change diff — three touch points in `.github/workflows/merge-queue.yml`:

```yaml
- name: Merge
  env:
    ...
    WORKFLOW_TOKEN: ${{ github.token }}   # NEW
  with:
    github-token: ${{ steps.app-token.outputs.token || github.token }}
    script: |
      ...
      // NEW: second Octokit with the workflow's default token
      // (has issues:write via job permissions).
      const workflowOctokit = require('@actions/github')
        .getOctokit(process.env.WORKFLOW_TOKEN);
      ...
      // In the close loop, swap github.rest.issues.* for
      // workflowOctokit.rest.issues.*:
      const { data: existing } = await workflowOctokit.rest.issues.get(...);
      await workflowOctokit.rest.issues.update(...);
      await workflowOctokit.rest.issues.createComment(...);
```

The merge step keeps the App token because `pulls.merge` needs to bypass branch protection (only the App has that grant). Only the belt-and-suspenders block swaps.

## Test plan

1. Land this PR. Watch the merge-queue log for THIS PR — should show `#XX: closed linked issue #261`.
2. Any subsequent PR with `Closes #N` in body: its linked issue auto-closes without manual intervention.
3. Idempotency spot-check: manual `workflow_dispatch` re-run of the merge queue against this PR — no duplicate close comment posts.
4. `bash scripts/dev/pr-preflight.sh --render-body merge-queue-auto-close-v3-token` — all checks green.

## Risks

- **R1** — Future App migration grants Issues:Write to the App token, and the operator forgets that this code exists. Mitigation: the block header names all three failure modes; the code is idempotent (falls back cleanly if `workflowOctokit === github`).
- **R2** — `@actions/github` package structure changes across actions/github-script versions. Mitigation: `getOctokit` is a stable API since v3.x; we pin `actions/github-script@v9`.
- **R3** — `WORKFLOW_TOKEN` env var is empty in some fork-PR-triggered runs (GitHub restricts secrets). Mitigation: the queue doesn't run against fork PRs (workflow only triggers on `pull_request_review` for the base repo). Not a real path here.

## Dependencies on other changes

- Predecessor: PR #248 (v2 body-regex fallback) — this change amends the block v2 introduced.
- Cross-references: manually-closed stragglers #249, #251, #253, #255 (silently missed by v2 due to the token issue this fix addresses).
