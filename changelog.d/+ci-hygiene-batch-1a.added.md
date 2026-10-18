- **CI hygiene batch 1a — per-PR concurrency + post-main rebase job**
  (`.github/workflows/ci.yml`): two orthogonal improvements shipped
  together because they both edit the same workflow. (1) Concurrency
  now groups by `${{ github.workflow }}-${{ github.ref }}` with
  unconditional `cancel-in-progress: true`, so pushing a fix to an
  open PR cancels the previous in-progress run for that same ref —
  frees self-hosted runner slots that used to burn on stale commits.
  Different PRs still run in parallel (each has its own ref).
  Replaces the previous SHA-based scheme which only deduped
  push+PR-on-same-commit and never actually canceled a superseded PR
  run. Trade-off: consecutive pushes to main now also cancel each
  other; that's fine for main because the merge queue serializes
  merges anyway. Closes #174. (2) New `rebase-stale-prs` job gated on
  `needs: [ci-success]` + `push: refs/heads/main` posts
  `@dependabot rebase` on every open dependabot PR (unless
  `no-auto-rebase` label) and runs `peter-evans/rebase@v3` on
  human/sub-agent PRs (excluding drafts + `no-auto-rebase` +
  `dependencies` to avoid double-rebasing dependabot). Rebase only
  fires when main's own CI passed green, so a broken main cannot
  cascade its state into open PRs. Runs on `ubuntu-latest` (not
  `RUNNER_LABEL`) so it stays alive when self-hosted is down.
  Closes portions of #180. Placeholder-named fragment per
  `changelog.d/README.md` — aggregator ignores non-numeric prefixes;
  rename to `<PR#>-added.md` after PR opens.
