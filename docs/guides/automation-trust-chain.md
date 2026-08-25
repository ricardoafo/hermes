---
name: Automation trust chain
description: How PRs #209 + #210 + #213 combine to give a hands-off "idea → merge to main" flow
---

# Automation trust chain

**Description**: How PRs #209 + #210 + #213 combine to give a hands-off "idea → merge to main" flow. Cross-repo reproduction reference.

**Audience**: operators reproducing this pattern in another repo, or
debugging why a specific PR isn't advancing through the pipeline.

This document is the map to the three-workflow architecture that makes
the hermes PR pipeline fully hands-off after human review. It ties
together three PRs that landed on 2026-08-25 as one coherent trust chain.

---

## Problem

Under `strict_required_status_checks_policy=true` (branch protection
setting on `main`), GitHub's `pulls.merge` API refuses any PR whose
head SHA is not up-to-date with `main` even when the required
`CI success` check is green. Combine that with GitHub's built-in
loop-prevention rule (workflows triggered by `github-actions[bot]`
commits are held in `action_required` until manually approved), and
a naive automated merge queue stalls in two independent ways:

1. **BEHIND-branch stall** — the queue's local revalidation proves the
   merged tree is safe, but the PR head is still BEHIND, so
   `pulls.merge` returns 405 forever.
2. **Bot-authored CI approval friction** — every automated merge commit
   authored by `github-actions[bot]` triggers a CI run held in
   `action_required` status until a human clicks Approve. That defeats
   the point of hands-off automation.

Before the three-PR sequence below, one full "idea → merge to main"
cycle needed 3-5 manual clicks: approve PR, click "Update branch",
approve CI on the new head, wait, then merge. Each click added
minutes of latency and cognitive load.

---

## The three workflows

### 1. `merge-queue.yml` — revalidate-then-merge (PR #209)

**Design**: the queue selects APPROVED candidates, checks out `main` at
a resolved SHA, fetches the PR head at the approved SHA, builds the
merge locally with `git merge --no-commit --no-ff`, runs gates against
that tree, and only then calls `pulls.merge`. **No push to the PR
branch, ever** — the approval survives.

**BEHIND-branch reconciliation (added by #209)**: after the freshness
re-assertion passes but before `pulls.merge`, the queue checks
`pr.mergeable_state`. If `BEHIND`, it calls `pulls/{n}/update-branch`
— GitHub creates a merge commit on the PR head, moving it forward to
include main. That triggers fresh CI on the new head; when CI
completes, `workflow_run:CI:completed` re-fires this workflow. On
the next pass the PR is no longer BEHIND and `pulls.merge` succeeds
because the required `CI success` check is now reported on the
up-to-date head.

**Decisions module**: `tools/merge/decisions.cjs` — pure functions
covered by `tools/merge/decisions.test.cjs`. `isCandidate`,
`shouldMerge`, `gateSkip`, `ciRestart`. YAML holds only the I/O.

### 2. `auto-update-pr-branches.yml` — push:main freshness (PR #210)

**Scope**: complements the queue, which only reconciles APPROVED
candidates. On every push to `main`, this workflow calls
`pulls/{n}/update-branch` for every open non-draft PR whose head is
BEHIND. Non-approved PRs (still in review) no longer linger in the
"BEHIND main" state until they eventually reach the queue.

**Non-goals**: does NOT merge anything (`pulls.merge` is exclusively
the queue's job); does NOT bypass any status/review gate; does NOT
run on draft PRs.

**Safety**: `updateBranch` returns 422 if the head already contains
main (no-op) or if the branch is not mergeable (conflict). Both are
logged and swallowed — this workflow never fails, only reports.

### 3. GitHub App authentication (PR #213)

**Problem it solves**: workflows using `GITHUB_TOKEN` for writes
authenticate as `github-actions[bot]`, triggering the loop-prevention
`action_required` gate on downstream CI. Every automated merge commit
therefore required a manual approval click before CI would run —
defeating the queue's hands-off intent.

**Fix**: both `merge-queue.yml` and `auto-update-pr-branches.yml` now
mint short-lived installation tokens from a dedicated GitHub App
(`hermes-automation-bot`) via `actions/create-github-app-token@v2`,
and pass those tokens to `pulls/{n}/update-branch` and `pulls/{n}/merge`
calls. App-authored commits are a distinct actor and bypass the
loop-prevention rule entirely.

**Attribution**: merges now show as `hermes-automation-bot[bot]` in the
git log rather than `github-actions[bot]`. Clearer intent + traceable
to the App installation.

**Fallback**: `${{ steps.app-token.outputs.token || github.token }}` —
workflows continue to function (with the approval friction) if the
App is not installed (e.g., in forks).

---

## Cross-repo reproduction checklist

To reproduce this pattern in another repository:

### 1. Create the GitHub App

At <https://github.com/settings/apps/new>:

- **Name**: `<repo>-automation-bot` (or similar)
- **Homepage URL**: repo URL
- **Webhook**: uncheck "Active" (no webhooks needed)
- **Repository permissions**:
  - Contents: **Read and write**
  - Pull requests: **Read and write**
- **Where can this GitHub App be installed?**: **Only on this account**
- Click **Create GitHub App**

### 2. Install on the target repo

Left nav → **Install App** → **Install** next to your account → **Only
select repositories** → check the target repo → **Install**.

### 3. Generate + store the private key

- On the App's settings page, scroll to **Private keys** → **Generate a
  private key** (downloads a `.pem` file).
- Store as repo secret:

  ```bash
  gh secret set HERMES_AUTOMATION_APP_KEY --repo <owner>/<repo> \
      < path/to/downloaded.pem
  ```

- Store the App ID as a repo variable (visible on the App's settings
  page under "About"):

  ```bash
  gh variable set HERMES_AUTOMATION_APP_ID --repo <owner>/<repo> \
      --body "<app-id>"
  ```

### 4. Copy the three workflows

- `.github/workflows/merge-queue.yml` (with #209 reconciliation
  branch — search for `mergeable_state === 'behind'`)
- `.github/workflows/auto-update-pr-branches.yml` (with #213 App-token
  mint at the top of the job)
- `tools/merge/decisions.cjs` + `tools/merge/decisions.test.cjs`

Update any repo-name references. The App-token mint step is guarded
by `if: vars.HERMES_AUTOMATION_APP_ID != ''` so the workflows are
safe to copy before the App is provisioned.

### 5. Verify

- Open a test PR. Approve. Confirm the queue merges it without
  requiring you to click "Update branch" or "Approve CI".
- On a separate open PR, wait for the next push to main. Confirm the
  auto-update workflow keeps it fresh without needing approval.

---

## Failure modes

| Symptom | Root cause | Recovery |
| --------- | ----------- | ---------- |
| Queue selects PR but 405 "CI success expected" | App not installed on repo, `updateBranch` falls back to `GITHUB_TOKEN`, loop-prevention held CI. | Install App; re-trigger queue via `gh workflow run merge-queue.yml`. |
| Auto-update workflow errors "Resource not accessible" | App permissions insufficient (missing Contents R/W or Pull requests R/W). | Update App permissions at <https://github.com/settings/apps/{name}/permissions>; accept the resulting installation-permission-request in the target repo. |
| Downstream CI still shows `action_required` after App install | Variable `HERMES_AUTOMATION_APP_ID` unset — fallback to GITHUB_TOKEN. | Verify `gh variable list --repo <owner>/<repo>` shows `HERMES_AUTOMATION_APP_ID`. |
| Merge queue merges but new commit not signed by bot | Legacy `HERMES_QUEUE_PAT` secret still set + old PAT-based path in the workflow. | Grep workflows for `HERMES_QUEUE_PAT`; remove references; delete the secret. |
| App private key compromised (accidentally leaked) | Generate new key at App settings → **Private keys** → Generate new; delete the old key; update the repo secret. | Rotation is O(seconds). |

---

## Related

- **PR #209** — `fix(ci): merge queue handles BEHIND PRs under strict branch protection`
- **PR #210** — `chore(ci): auto-update PR branches on push to main`
- **PR #213** — `fix(ci): use github app for automation writes to bypass loop-prevention`
- [`docs/decisions/2026-08-21-revalidate-then-merge.md`](../decisions/2026-08-21-revalidate-then-merge.md) — original merge-queue redesign rationale
- [`docs/decisions/2026-08-21-pr-flow-hardening.md`](../decisions/2026-08-21-pr-flow-hardening.md) — earlier PR-flow hardening waves
- [`docs/decisions/2026-08-23-fetch-before-launch-tooling.md`](../decisions/2026-08-23-fetch-before-launch-tooling.md) — sub-agent launch discipline (fetch main before spawn)
- [`AGENTS.md` rules 12-13](../../AGENTS.md#12-always-open-prs-as-the-ai-bot-never-as-ricardoafo) — actor separation source of truth
- [`openspec/changes/sdd-process-hardening/change.md`](../../openspec/changes/sdd-process-hardening/change.md) — the SDD change that landed this guide and the local pre-flight gates that complement it
