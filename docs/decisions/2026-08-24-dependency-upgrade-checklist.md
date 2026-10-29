# Dependency-upgrade review checklist

**Date**: 2026-08-24
**Author**: Maintainer
**Related**: [Dependabot over Renovate](2026-08-21-dependabot-over-renovate.md),
[Drop `safety`](2026-08-22-drop-safety.md), ADR-010 (Security policy),
GH #141 (MVP1 status), PR #140 (Wave 5 process cadence), PR #149 (Wave 6
process tooling).

## Decision

Every dependency-bump PR (dependabot-authored or human-authored) MUST
clear the checklist below before merge. CI enforces the automated legs;
the manual legs are the human bar we refuse to lower.

## Context

Dependabot lands ~5-10 PRs per week. Two failure modes have appeared:

1. **Rubber-stamp risk**: a firehose of green PRs trains reviewers to
   merge on green CI alone. That reduces "dependency upgrade" to
   "trust the vendor" — which is exactly the drift class ADR-010
   §CVE-scanning was written to prevent.
2. **Silent API rot**: the library ships a deprecation on version N;
   we don't remove our usage; version N+1 removes the API; the next
   dependabot bump breaks the build and blocks release.

Both classes are cheap to prevent at PR time and expensive to fix
in-flight. The checklist codifies the bar.

## Automated checks (in CI)

Every dependency PR must clear the CI matrix — same gates every other
PR clears, plus the ones specifically tuned for dependency review:

1. **`pip-audit --skip-editable`** — known CVE scan. Runs on every
   PR via the quality matrix leg. A CVE in a bumped package fails
   the bump. Also see [Drop `safety`](2026-08-22-drop-safety.md) for
   why we consolidated on pip-audit + Dependabot alerts + semgrep.
2. **`pytest -W error::DeprecationWarning`** — surfaces deprecated APIs
   we still use, so a bump that formalises a removal fails LOUDLY at
   PR time. Enforced via `pyproject.toml [tool.pytest.ini_options]
   filterwarnings` from this PR onwards; every unit run treats
   deprecation warnings as errors.
3. **Coverage delta vs main** — the per-package coverage check
   (`scripts/dev/coverage-check.py`) enforces per-slice thresholds
   already; a bump that quietly disables a code path shows up as a
   coverage drop we'd have to justify.
4. **`ruff UP` rules** — `pyupgrade`-family auto-suggests newer
   patterns after a bump makes them available. Turns "we should
   modernise" into a fix at PR time, not a follow-up ticket.
5. **Import-linter contracts** — architectural boundaries
   (`domain-is-pure`, `domain-no-async-or-network`, etc.) still
   hold post-bump. A dependency that starts to leak into the domain
   layer (e.g. a new transitive `httpx` import) fails the contract.

## Manual checklist (before merging any bump beyond patch)

1. **Is CI green?** Necessary, not sufficient. Read the specific check
   panel — a skipped job is not the same as a passed job.
2. **Read the upstream CHANGELOG for the version range.** Look for:
   removed APIs we use, security patches, new defaults, config-key
   renames. Dependabot's auto-generated PR body has the release-notes
   diff inline — read it, don't scroll past it.
3. **Is it a security patch?** Merge fast, skip discussion. A CVE with
   an upstream fix is not a design decision — it is an obligation.
4. **Major bump?** Check for API deprecations we currently use. Grep
   the source for imports from the bumped package and the changed
   symbols. If the diff is non-trivial, migrate in a separate PR
   ahead of the bump — do not couple migration + bump in one review.
5. **New patterns worth adopting?** File a follow-up issue with the
   `p3` label; do NOT block the bump. Adoption is optional; the bump
   is the priority.
6. **Coverage / mutation-rate delta from main?** If a bump moves the
   number, understand why before merge. The mutation workflow
   (`.github/workflows/mutation.yml`) is weekly — for a suspicious
   bump, run `uv run cosmic-ray init` on the affected package
   locally before merge.

## For dependabot PRs specifically

- The bot's auto-generated body already contains the diff + release
  notes + compatibility score — READ it. The CI "PR body" check is
  now SKIPPED for dependabot (see `.github/workflows/ci.yml pr-body
  job`); the trade-off is that reviewers must open the auto-generated
  body themselves, not rely on the 4-section template to force it.
- **Security patch** (Dependabot marks the PR with a security label):
  merge same-day.
- **Patch bump** (grouped under `patch-updates` per
  `.github/dependabot.yml`): quick sanity check + merge. Grouping is
  intentional — a single PR per week, not a per-package firehose.
- **Minor or major bump**: individual PR per package. Manual
  CHANGELOG review REQUIRED per the checklist above. Grouped
  minors are explicitly disabled in `.github/dependabot.yml` so
  every minor gets its own review.

## Consequences

**What this locks in**:

- The 4-section PR body template is skipped for dependabot ONLY —
  every human PR still gates on it. Skipped jobs are accepted by
  `scripts/dev/ci-success-check.sh` (allowlist `{success, skipped}`),
  so dependabot PRs unblock cleanly.
- `pytest` treats deprecation warnings as errors from this PR on.
  Third-party warnings we cannot fix must be silenced with a targeted
  `ignore::DeprecationWarning:<module>` line PLUS a comment explaining
  WHY and a link to the upstream tracking issue. No bare
  `default::DeprecationWarning`.
- The dependency-review discipline is documented, not folklore — this
  file is the reference the checklist links to.

**What this forecloses**:

- "Merge on green CI alone" for major bumps. If the reviewer cannot
  point to the CHANGELOG entry that justifies a major bump, the bump
  waits.
- Grouping minor bumps into one PR. The `groups:` config in
  `.github/dependabot.yml` scopes to `update-types: [patch]` only.

**What we'll revisit**:

- If the `filterwarnings = ["error::DeprecationWarning"]` config
  turns into a maintenance burden (frequent third-party escapes),
  weaken to `always` on the CI matrix leg only and drop from
  `pyproject.toml`. Not before we have evidence.
- If dependabot's auto-generated body becomes shallow enough that
  reviewers actually miss upstream migration notes, ship the
  auto-rewriter follow-up (p3 issue linked from the CHANGELOG
  fragment) that rewrites dependabot bodies into our 4-section
  format enriched with major-bump warnings + CHANGELOG links.
