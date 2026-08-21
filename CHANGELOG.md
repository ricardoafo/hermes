# Changelog

All notable changes to this project will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html)
as defined in [ADR-006](docs/architecture/adr-006-versioning-strategy.md).

Every PR that changes production behavior must update the `[Unreleased]`
section under Added / Changed / Deprecated / Removed / Fixed / Security.
PRs that are pure docs, chore, or CI may skip. The CHANGELOG update check
lands in Phase 0 CI (per ADR-006).

## [Unreleased]

### Added

- Architecture Decision Records 001-010 (accepted 2026-08-21)
- Version-control discipline (`docs/guides/dev-workflow.md`) — trunk-based,
  Conventional Commits, squash-merge only, Ricardo-only approval
- AI collaboration protocol (`AGENTS.md`) — role separation, 10 absolute
  prohibitions, precedence order
- Pre-commit hook chain: markdownlint-cli2, conventional-pre-commit
  (--strict), pre-commit-hooks v5.0.0 (trailing-whitespace,
  end-of-file-fixer, check-yaml/json, check-merge-conflict,
  mixed-line-ending=LF)
- GitHub scaffolding: 4 issue templates (bug with severity rubric,
  feature, chore, config), CODEOWNERS assigning @ricardoafo, PR template
  with 7 mandatory sections, `.gitattributes` for LF normalization
- GitHub repo config: 27 labels (`type:*`, `sev-*`, `p*`, `area:*`,
  `size:*`), 4 milestones (Phase 0-3), branch protection on `main`
  (1 review + code-owner + linear history + no force-push + no admin
  bypass), squash-merge-only, auto-delete branches
- Idempotent bootstrap scripts (`scripts/dev/setup-labels.sh`,
  `setup-milestones.sh`, `setup-branch-protection.sh`)
- Doc surface scaffolding: `docs/guides/`, `docs/decisions/`,
  `docs/runbook/`, `docs/research/` with README indexes
- Local dev environment: `.venv/` (Python 3.14.7 via uv 0.12.5),
  pre-commit 4.6.2, markdownlint-cli2 0.23.2

### Notes

- No production runtime yet — Phase 0 lands `pyproject.toml`,
  hexagonal `src/hermes/` skeleton, CI workflows, testing scaffold.
- Version stays at `0.0.0-dev` until Phase 0 tags first pre-release.

[Unreleased]: https://github.com/ricardoafo/hermes/commits/main
