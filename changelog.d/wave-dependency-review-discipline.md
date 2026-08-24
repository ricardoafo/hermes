### Added

- **Dependency-review discipline** (`.github/workflows/ci.yml`,
  `.github/dependabot.yml`, `pyproject.toml`,
  `docs/decisions/2026-08-24-dependency-upgrade-checklist.md`,
  `docs/decisions/README.md`): three coordinated moves that turn
  dependabot's PR firehose from a rubber-stamp risk back into a
  reviewable slice. Related to `Meta: MVP1 status` (#141) and
  builds on Wave 5 (PR #140, pytest-timeout as tooling precedent)
  and Wave 6 (PR #149, process tooling gate precedent). (1) CI
  `pr-body` job now carries an `if:` gate that skips it for
  `dependabot[bot]`-authored PRs — the 4-section (Summary/Related/
  Testing/Risk) template does not map to dependabot's
  auto-generated release-notes body, and failing the check on
  every dependabot PR trained reviewers to ignore red X's on human
  PRs too. Skipped is an accepted state in
  `scripts/dev/ci-success-check.sh` (allowlist `{success, skipped}`)
  so dependabot PRs unblock cleanly; every human PR still gates on
  the check. (2) `pyproject.toml [tool.pytest.ini_options]` gains
  `filterwarnings = ["error::DeprecationWarning",
  "error::PendingDeprecationWarning"]` so deprecated APIs we still
  use fail LOUDLY at PR time, not "the day the library removed the
  API". 789 unit tests pass under the new config on this PR — no
  in-tree deprecations to fix, and the escape hatch (targeted
  `ignore::DeprecationWarning:<module>` with a comment + upstream
  link) is documented in the decision doc for third-party warnings
  we cannot patch. (3) `.github/dependabot.yml` is refined (not
  clobbered — assignees, labels, reviewers, cooldown, weekly
  schedule preserved from PR #128's work): grouping now applies to
  PATCH updates only (`groups.patch-updates: patterns: ['*'],
  update-types: ['patch']`), so minor + major bumps ship as
  individual PRs that get real CHANGELOG-diff reads; PR limit
  raised from 3 to 10 to leave headroom for a busy security week
  without letting the queue explode; `commit-message.prefix-development:
  chore(deps-dev)` differentiates dev-group bumps from prod
  `chore(deps)` for cleaner conventional-commit history. Decision
  doc codifies the automated CI checks (pip-audit, deprecation-as-error,
  coverage delta, ruff UP, import-linter) + the manual checklist
  (read upstream CHANGELOG for the version range, security fast-lane,
  major-bump API-usage grep) + the dependabot-specific fast-lane
  (security same-day, patch group quick-review, minor/major
  individual review). Follow-up (p3): a GH Action that rewrites
  dependabot PR bodies into our compliant 4-section format enriched
  with major-bump warnings + upstream-CHANGELOG links (tracked in
  its own issue). Placeholder-named fragment per
  `changelog.d/README.md` — aggregator ignores non-numeric prefixes.
