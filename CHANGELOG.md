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

- **Process hardening wave 2** (`scripts/dev/create-pr.sh`, `scripts/dev/pr-preflight.sh`,
  `.pre-commit-config.yaml`, `AGENTS.md`): create-pr.sh wrapper enforces PR body lint
  before `gh pr create` (closes drift class 1); pre-flight check #9 now uses
  CI-verbatim pytest invocation + check #14 adds coverage-check.py --bootstrap (closes
  pre-flight ↔ CI parity drift); sdd-design live-verify rule added to AGENTS.md (closes
  design-miss drift class). Closes #121, #122, #123.

- **Code conventions guide** (`docs/guides/code-conventions.md`): 14
  sections codifying design patterns, error handling (hybrid Result in
  domain / exceptions in adapters), immutability, factories, async
  policy (AnyIO on asyncio), performance mindset with measured triggers,
  polyglot posture (Rust yes when profiled, Go no for solo scale),
  testing patterns, time handling (UTC internally / Madrid at edges),
  event schema versioning, feature flags, engine pause, migrations, and
  public/internal API. Research-backed (engram
  `phase-1-code-conventions-research-2026-08-23`). Closes #106.
- **6 domain value objects** in `src/hermes/domain/value_objects/`:
  Money, Price, Weight, Pair, Quantity, LotId — all frozen dataclasses
  with `from_*` factories and `Result`-based validation. Foundation for
  every Phase 1 domain feature. Closes #98.
- **HermesError hierarchy** (`src/hermes/domain/errors.py`): `DomainError`
  and `InfraError` subtrees. Enables the hybrid error-handling pattern.
- **Time helpers** (`src/hermes/infrastructure/time.py`): `now_utc`,
  `to_madrid`, `from_madrid` using `zoneinfo.ZoneInfo` (no pytz, no
  naive datetimes). Enforces §9 UTC-internally convention.
- **DomainEvent base class** (`src/hermes/domain/events/base.py`) with
  `__event_schema_version__` enforcement via `__init_subclass__`.
- **Hypothesis strategy library** in `tests/strategies/` with composable
  strategies for all 6 value objects.

- **New runtime dependencies**: `returns>=0.24,<1` (Result monad, used
  in domain error handling per code-conventions §2) + `anyio>=4,<5`
  (structured concurrency at I/O boundary per code-conventions §5).
- **Observability scaffold** (`src/hermes/infrastructure/logging.py`):
  structlog config with JSONRenderer (prod) / ConsoleRenderer (dev),
  ULID correlation-ID contextvar plumbing, starter field taxonomy in
  module docstring. First real Phase-1-shaping code — supports the
  ADR-008 observability discipline. Closes #18.
- **NotifierPort Protocol** (`src/hermes/ports/notifier.py`): contract
  for outbound notifications with `notify_urgent` + `notify_report`
  methods. Adapters land in Phase 1.
- **Symlink-swap rollback layout** in `deploy/bootstrap-vps.sh`:
  `/opt/hermes/{versions/, current -> versions/vX, previous -> ...}`.
  Enables Phase-1 rollback logic when real services + `hermes engine
  pause` CLI exist.
- **Code conventions enforcement**: 2 import-linter contracts
  (`domain-no-pydantic`, `domain-no-async-or-network` with
  `include_external_packages = true`), 2 pre-commit grep hooks
  (`no-naive-datetime`, `no-domain-mode-check`) with paired self-tests,
  and `tests/unit/test_public_api.py` asserting `__all__` on every
  `hermes.domain.*` and `hermes.ports.*` module. Adds `__all__` to
  `hermes.domain.__init__`, `hermes.ports.__init__`, `hermes.ports.exchange`,
  `hermes.ports.notifier`. Closes #109.
- **AI-authored-test enforcement** (`docs/guides/ai-authored-tests.md`):
  ruff `D103` enforced on `tests/**` (missing docstring on public test fn),
  `[tool.coverage.report] fail_under = 90` global floor, and a new
  `no-vague-test-assertions` pre-commit hook (Python AST walker at
  `scripts/dev/no-vague-test-assertions.py`) that fails a test function whose
  sole assertion is a vague null-guard (`is None`, `> 0`, bare `assert x`).
  Existing tests received 89 invariant-naming docstrings. Contributors: your
  next commit may fail if a new test lacks a docstring or has only a vague
  assertion — see the guide for the standard. Closes #116.
- Merge queue test — PR C (competing edit)
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

### Changed

- **AI-authored-tests guide** (`docs/guides/ai-authored-tests.md`): new
  test pyramid tier 2+3 definitions (integration and e2e: markers,
  fake/mock/testnet policy, hermes density guidance) and "AI development
  lifecycle × test tier" matrix mapping 7 SDD phases across 3 tiers.
  Enforcement table rewritten to reflect PR A truth (D103, `fail_under = 90`,
  `coverage-check.py`, and `no-vague-test-assertions` now `live`; mutation
  testing deferred to Phase 1 pending #118). Cross-link added from
  `ai-development-lifecycle.md` Step 5. Closes #117.
- **Notification channels** — added Slack alongside Telegram, routed
  by content class (Telegram = urgent/terse, Slack = rich/reports).
  Supersedes ADR-008 §4 "Telegram alerts — user-facing signals only"
  via decisions-journal entry
  `docs/decisions/2026-08-23-slack-alongside-telegram.md`.

### Fixed

- Removed non-functional `Add to project board` step from CI workflow;
  `GITHUB_TOKEN` cannot write to user-owned projects (only org-scope).
  Items now added manually via `gh project item-add` until a PAT-based
  restoration is set up (documented inline in `.github/workflows/ci.yml`).

### Notes

- No production runtime yet — Phase 0 lands `pyproject.toml`,
  hexagonal `src/hermes/` skeleton, CI workflows, testing scaffold.
- Version stays at `0.0.0-dev` until Phase 0 tags first pre-release.

[Unreleased]: https://github.com/ricardoafo/hermes/commits/main
