### Added

- **Auto-update PR branches workflow**
  (`.github/workflows/auto-update-pr-branches.yml`): on every push to `main`,
  every open non-draft PR whose head is BEHIND main gets a fresh merge commit
  via GitHub's `pulls/{n}/update-branch` endpoint. Complements the
  merge-queue's own reconciliation (which only touches APPROVED candidates)
  by closing the UX gap where non-approved PRs (still in review) linger in
  "BEHIND main" state until they eventually reach the queue. This workflow
  does NOT merge anything, does NOT run on draft PRs, does NOT bypass any
  status/review gates — it only owns branch freshness. Cost: paginated
  `pulls.list` + one `pulls.get` per PR + at most one `updateBranch` call
  per BEHIND PR (sub-second for any realistic PR count). 422 responses
  (already up-to-date or race conditions) are logged and swallowed;
  workflow never fails, only reports.
