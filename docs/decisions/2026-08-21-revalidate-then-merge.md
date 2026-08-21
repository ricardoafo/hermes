# Revalidate-then-merge queue — no branch pushes, decisions in code

**Date**: 2026-08-21
**Author**: claude on behalf of Ricardo
**Related**: dev-workflow.md §Merge queue, ADR-003 §09 Deployment,
`.github/workflows/pr-revalidate.yml`, `tools/merge/decisions.cjs`,
`docs/decisions/2026-08-21-pr-flow-hardening.md` (partially superseded)

## Decision

Replace `merge-queue.yml` + `pr-auto-update.yml` with a single
`pr-revalidate.yml` workflow that builds the merge tree locally, runs
tree-verifying gates on it, and merges from the workflow — never
pushing to the PR branch. Turn OFF
`strict_required_status_checks_policy` on `main`, because freshness is
now enforced by the workflow. Extract every non-trivial merge decision
into `tools/merge/decisions.cjs`, covered by
`tools/merge/decisions.test.cjs` under Node's built-in test runner.

## Context

The prior design had two coupled problems that the initial bug-triage
mis-diagnosed as separate:

1. `merge-queue.yml` trusted GitHub's `mergeable_state` field as
   final. That field is lazy (returns `unknown` on `pulls.list`), and
   even when settled it returns `unstable` when a non-required check
   was skipped or failed. The queue refused `unstable`, stranding
   otherwise-mergeable PRs. Fix attempt: expand the allowlist to
   include `unstable`.

2. Branch protection required `strict_required_status_checks_policy`
   (PR must include current `main` before merging), which forced a
   push to keep the branch current. Under
   `require_last_push_approval: true` +
   `dismiss_stale_reviews_on_push: true`, any push invalidated the
   reviewer's approval. Fix attempt: `pr-auto-update.yml` pushing as
   `github-actions[bot]` (a system actor that supposedly does not
   invalidate approvals). This worked, sometimes, and required a
   second workflow to exist forever.

Both fixes were papering over the wrong problem. The real problem is
that verifying a merge tree does not require pushing that tree back
to the PR branch — it only requires building it somewhere. Once the
merge queue builds it in the runner and runs gates there, `strict`
becomes redundant, `pr-auto-update` becomes unnecessary, and the
`unstable`-state ambiguity is bypassed entirely (the queue's own
tree-gates decide, not GitHub's cached state field).

## What changes

- **New**: `.github/workflows/pr-revalidate.yml`
- **New**: `tools/merge/decisions.cjs` — pure functions:
  `isApproved`, `isCandidate`, `shouldMerge`, `gateSkip`, `ciRestart`
- **New**: `tools/merge/decisions.test.cjs` — Node `node:test`,
  zero deps, 34 test cases covering every refusal branch
- **New**: `merge-decisions` job in `ci.yml` (runs the unit tests)
- **Deleted**: `.github/workflows/merge-queue.yml`
- **Deleted**: `.github/workflows/pr-auto-update.yml`
- **Branch protection**: `strict_required_status_checks_policy: false`
  on `main`

## The five decisions (in `decisions.cjs`)

Each is a pure function taking plain objects (fixture-shaped like the
GitHub REST response), returning `{ verdict, reason }`. The `reason`
is always populated, even on the yes, so log lines say WHY.

- **`isApproved(reviews)`** — the last opinionated review per author
  wins. `COMMENTED` reviews ignored so a note after approval does
  not withdraw it.

- **`isCandidate({ pr, reviews })`** — allowlist:
  `MERGEABLE_STATES = {clean, unstable, behind, has_hooks}`. `blocked`
  is CI still running or approval missing (retry next tick). `dirty`
  is human-resolvable conflicts. Unknown states refuse (safe direction
  to be wrong).

- **`shouldMerge({...})`** — the re-assertion IMMEDIATELY before
  `pulls.merge`. Refuses if base moved, head moved, PR closed/drafted,
  conflicts developed, or approval withdrawn during the gate run.

- **`gateSkip({ mainIsAncestor, ciConclusion })`** — the dangerous
  one. Skips the tree gates only when the head already contains the
  main tip AND CI on that head concluded `success`. Both required,
  neither inferred. A wrong TRUE here merges a tree nothing checked.

- **`ciRestart({ mergedTree, validatedTree })`** — after merge,
  decides whether to hand-dispatch CI on main. Compares TREES (not
  SHAs, since squash always makes a new commit). Fail-open: unsure
  means restart.

## Why decisions live in a .cjs module

`actions/github-script` runs Node and uses `require()`. It can load a
`.cjs` file straight from a sparse checkout of `tools/merge`, with no
`npm ci` and no build step. The `select` job runs ~50 times a day —
every CI completion, every approval, every merge, hourly cron —
adding a full Node setup to each pass would consume most of the
self-hosted runner's headroom for a step that is otherwise API calls
and nothing else.

Types come from JSDoc. Real discipline is the test file.

## Alternatives considered

- **Adopt GitHub's native merge queue**: not available on private
  repos on the current plan tier.
- **Keep `pr-auto-update.yml`, just fix the `unstable` bug in
  `merge-queue.yml`**: the fix I actually shipped first. Solves the
  immediate symptom, keeps the bandaid workflow alive, keeps the
  `require_last_push_approval` treadmill in place, keeps
  bash-in-YAML untested.
- **Move decisions to Python (matches the project's stack)**:
  rejected because `github-script` is Node — every call site would
  need to spawn a Python subprocess, negating the "runs in ~seconds"
  property of the `select` job.
- **Move decisions to TypeScript with a build step**: rejected
  because the ~40s `npm ci` per select-job run is expensive at 50
  runs/day. CJS + JSDoc gives the same type-checking discipline
  without the install cost.

## What we accept

- Changes to `pr-revalidate.yml` itself are hard to test in the PR
  that introduces them: `workflow_run` triggers use the workflow file
  from `main`, so a fix to the workflow can't self-apply. Mitigated
  by:
  - Unit-testing the decisions module (decoupled from workflow YAML)
  - `dry_run: true` dispatch input (runs gates, skips `pulls.merge`)
  - Explicit doc entry so future us doesn't forget

- Tree gates today are markdown-lint + secrets scan only. When Phase
  0 adds pytest/mypy, those get added to the revalidate step too.
  The framework is in place; the gate list grows with the codebase.

## What we'll revisit

- Add a log-hygiene inspector (adapted from PDP `log-hygiene.cjs`)
  that refuses to merge when a CI log contains new warnings —
  deferred until we have warnings worth policing.
- Automatic label-based CI selection (e.g., `perf/*` PRs run
  benchmarks) — deferred until benchmarks exist.
