### Fixed

- **Merge queue preserves `Closes #N` trailers from PR body**
  (`.github/workflows/merge-queue.yml`): passes `pr.body` as
  `commit_message` in the `pulls.merge` call so GitHub's issue
  auto-close mechanism sees `Closes #N` / `Fixes #N` / `Resolves #N`
  trailers. Previously the queue deliberately omitted
  `commit_message`, expecting per-commit trailers to survive — but
  our `render-pr-body.sh` writes `Closes #N` in the PR body (from
  openspec `issue.md`), NOT in individual commit messages. Observed
  2026-08-25: PRs #215, #219, #222 all merged with `Closes #N` in
  their bodies; none of the referenced issues (#214, #218, #220)
  auto-closed. Fix targets that specific path; empty body falls
  back to prior behavior (commit-message trailers). Fixes #224.
