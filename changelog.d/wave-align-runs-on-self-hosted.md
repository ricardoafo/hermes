### Changed

- **`runs-on:` alignment across all workflows**: two jobs (`rebase-stale-prs` in
  `ci.yml`, `semgrep` in `nightly-quality.yml`) were shipped in #188/#189 with
  plain `runs-on: ubuntu-latest`. Updated both to use the standard
  `RUNNER_LABEL` fallback pattern per the "always self-hosted" rule.
  Cron/scheduled jobs on offline runners QUEUE and pick up when a runner comes
  online — no missed executions. Applies operator preference documented in
  engram `preferences/self-hosted-runners-mandatory`.
