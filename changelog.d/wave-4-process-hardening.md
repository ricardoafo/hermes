- **Process hardening wave 4** (`scripts/dev/create-pr.sh`,
  `scripts/dev/aggregate-changelog.py`, `scripts/dev/changelog-check.sh`,
  `changelog.d/`, `AGENTS.md`): closes four drifts observed in the
  PR #124-#132 queue. (1) create-pr.sh gained a lowercase-subject title
  gate that mirrors the CI job "PR title" `subjectPattern` — pre-flight
  fail replaces the amend+push cycle from PR #130's `Telegram` slip.
  (2) AGENTS.md now documents `# gitleaks:allow` inline as the required
  discipline for test-only credential-shaped literals (ruff `# noqa: S105`
  does NOT satisfy the gitleaks CI job — PR #132 cost one amend cycle
  on this). (3) AGENTS.md now mandates `git fetch origin main` from the
  primary tree BEFORE spawning worktree-isolated sub-agents; two rebase
  cascades in wave 3 traced back to stale local `main`. (4) Structural
  CHANGELOG conflict eliminated: new `changelog.d/` directory holds
  one-bullet-per-PR fragments (`<pr-number>-<type>.md`), and
  `changelog-check.sh` accepts either a direct `CHANGELOG.md` edit OR
  a fragment addition. Aggregation into `[Unreleased]` runs at
  release-cut time via `scripts/dev/aggregate-changelog.py --apply` —
  no release this cycle, so the aggregator is future-proofing plus
  immediate merge-conflict elimination. Placeholder-named fragments
  (non-numeric prefix, like this one) are skipped by the aggregator
  and exist for the chicken-and-egg case where the PR number is not
  known until `gh pr create` returns.
