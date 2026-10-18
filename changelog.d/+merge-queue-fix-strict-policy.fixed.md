- **Merge queue no longer stalls on BEHIND PRs under strict branch protection**
  (`.github/workflows/merge-queue.yml`): the repository ruleset for `main` sets
  `strict_required_status_checks_policy: true`, meaning GitHub refuses
  `pulls.merge` on a PR whose head SHA is not up-to-date with `main` even when
  the required `CI success` check is green on that head. The queue's `Build
  merge tree` step validated a synthetic merged tree locally, but never
  pushed — so the PR head stayed BEHIND, GitHub refused every merge attempt
  with 405, and the queue retriggered forever. Reproduced on PRs #204 + #206
  after PRs #205 + #207 merged and moved main forward. The fix adds a
  reconciliation branch in the `Merge` step: when the freshness re-assertion
  passes but `mergeable_state === 'behind'`, the queue calls
  `pulls/{n}/update-branch` (creating a GitHub-native merge commit on the PR
  head) and returns. That triggers a fresh CI run; when CI completes,
  `workflow_run:CI:completed` re-fires this workflow, the PR is no longer
  BEHIND, and the merge succeeds on the next pass. `DRY_RUN` respected. Both
  the update-branch call and the merge attempt still race-check via
  `shouldMerge` — no silent bypass of any existing safety property.
