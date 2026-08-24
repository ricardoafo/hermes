### Changed

- **CI: move heavy jobs to nightly cron** (`.github/workflows/ci.yml`,
  `.github/workflows/nightly-quality.yml`, `.github/workflows/mutation.yml`):
  extracted the Semgrep (SAST) job out of the per-PR `ci.yml` pipeline
  into a new dedicated workflow `nightly-quality.yml` that runs at
  03:00 UTC daily on `ubuntu-latest`. Rationale: semgrep findings are
  codebase-wide patterns that rarely change per-PR, and its ~30s+
  per-run cost was pinning scarce self-hosted runner slots (8 ephemeral)
  that are better spent on per-commit signal (unit tests, ruff, mypy,
  import-linter, pip-audit, licenses). Running on `ubuntu-latest`
  ensures the cron fires even when the operator's self-hosted PC is
  off overnight. Removed `semgrep` from `ci.yml`'s `ci-success`
  aggregator `needs:` list so PR gate no longer waits on it — the
  remaining 14 per-PR jobs (`changeset`, `markdown-lint`, `pr-title`,
  `pr-body`, `changelog`, `auto-label`, `merge-decisions`, `quality`,
  `unit`, `guards`, `licenses`, `reproducible`, `codeowners`, `secrets`)
  are still required. `mutation.yml` (weekly Sunday cosmic-ray run)
  was left untouched — it was already correctly scheduled — and gained
  a header comment cross-referencing `nightly-quality.yml` as its
  sibling in the "nightly heavy jobs" tier. Placeholder-named fragment
  per `changelog.d/README.md` — aggregator ignores non-numeric
  prefixes. Related: #182.
