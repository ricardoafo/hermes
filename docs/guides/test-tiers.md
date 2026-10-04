# Test Tiers — What Runs Where

**Type**: Guide (living document — PR any change)
**Owners**: Ricardo Afonso
**Applies to**: every test in this repo and every spec test-plan section.
**Description**: The single table of test tiers: what each one is, which workflow or command runs it, how often, and whether it is live or planned; plus coverage floors and layer-isolation contracts.

This guide is the single source of truth for test tiers. The decision behind it is
[ADR-017](../architecture/adr-017-test-tiers-as-enforced.md), which supersedes the tier parts of
[ADR-007](../architecture/adr-007-testing-strategy.md). Other docs link here and do not restate cadences. If
this table and a workflow disagree, the workflow is right and this guide has a bug.

## Tiers

Tiers are chosen by directory, not by marker. Status is `live` (a workflow or script runs it today) or
`planned` with an issue number.

| Tier | Directory | What | Where it runs | Cadence | Status |
| --- | --- | --- | --- | --- | --- |
| Unit | `tests/unit/` | Hermetic: no network, no real clock. Includes hypothesis and syrupy tests | `ci.yml` job `ci-success`: `pytest tests/unit/ -n 4 --cov=src/hermes --cov-branch`, then `scripts/dev/coverage-check.py --bootstrap`. Merge queue: `scripts/dev/merge-gates.sh` gate `pytest` | Every PR; every merge-queue batch | live |
| Integration | `tests/integration/` | Real adapters against local fakes; no network, no secrets | `ci.yml`: `pytest tests/integration/ -n 4`. Merge queue: same `pytest` gate as unit | Every PR; every merge-queue batch | live |
| Contract | `tests/contract/` | Real HTTPS to third-party sandboxes; each test skips when its secret is absent | `scheduled.yml` job `contract`: `pytest tests/contract -m contract` | Daily 11:30 UTC | live |
| Smoke | `tests/smoke/` | Read-only checks on hermes-01 over SSH | `cd.yml` step "Smoke tests": `pytest tests/smoke -m smoke`. Exit 5 (nothing collected) fails the deploy | After each deploy | live |
| Mutation | `tests/unit/domain/value_objects` (rerun by cosmic-ray) | cosmic-ray over `src/hermes/domain/value_objects`; scope is `module-path` in `cosmic-ray.toml` | `scheduled.yml` job `mutation` | Sundays 11:45 UTC; manual dispatch with `mutation` ticked | live; kill-rate floor (80%) advisory until #406 |
| End-to-end | `tests/e2e/` | Full stack against a testnet | none; the directory holds only `__init__.py` | planned inside `scheduled.yml` (no `e2e.yml`) | planned, #408 |

A push to `main` skips the suite when the tree already passed on the PR head
(`scripts/dev/main-tree-validated.sh`). Unit and integration therefore run on the merged tree in the merge queue,
not again after the merge.

## Other gates

These are not test tiers but they run next to them:

| Gate | Where it runs |
| --- | --- |
| ruff format and check, `mypy --strict src/hermes`, `lint-imports`, pip-audit, radon, vulture, licenses, gitleaks, markdownlint, test-deletion guard | `ci.yml` (job `ci-success`, the only required check) |
| ruff, mypy, `lint-imports`, markdownlint, gitleaks on the merged batch | merge queue (`scripts/dev/merge-gates.sh`) |
| Semgrep over the whole tree | `scheduled.yml` job `semgrep`, daily 11:30 UTC |

## Markers

`pyproject.toml` registers markers and runs pytest with `--strict-markers`, so a marker must be registered before
a test uses it. Registered markers are labels; the directory decides what runs, with two exceptions:

- `contract`: `addopts` has `-m 'not contract'`, so contract tests only run with `-m contract` (the scheduled job).
- `smoke`: the CD job selects it with `-m smoke`.

`unit`, `property` and `integration` are used by a few tests and are not used for selection. `chaos`, `e2e`,
`mutation` and `slow` are registered and used by no test today. Do not invent a new marker without registering it in
`pyproject.toml`.

## Rules per tier

- **Unit**: no network, no filesystem writes outside `tmp_path`, no sleeping. Every test inherits the 60 s
  `pytest-timeout` default; see [ai-authored-tests.md](ai-authored-tests.md) for overriding it.
- **Integration**: fakes and SQLite `:memory:`. No real money and no production endpoints.
- **Contract**: read-only or informational calls only. See
  [the contract tier decision](../decisions/2026-08-23-contract-test-tier.md).
- **Smoke**: read-only; nothing here writes to hermes-01 or calls an exchange (AGENTS.md rule 7).
- **Mutation**: the rationale for cosmic-ray is in
  [the mutation tool decision](../decisions/2026-08-22-mutation-testing-cosmic-ray.md).

## Coverage floors

Enforced in `ci.yml` by `scripts/dev/coverage-check.py` on the unit run's `coverage.json`:

| Package | Floor |
| --- | --- |
| `src/hermes/domain` | 95% |
| `src/hermes/application` | 90% |
| `src/hermes/adapters/ledger` | 90% |
| `src/hermes/adapters/exchanges`, `fx`, `healthchecks`, `market_data`, `notifier` | 85% |
| `src/hermes/infrastructure/sentry_integration` | 85% |
| `src/hermes/entrypoints` | 70% |

The overall `fail_under = 90` in `pyproject.toml` `[tool.coverage.report]` also applies. Adapter packages not listed
have no per-package floor. `--bootstrap` makes a package with zero executable lines pass instead of fail; every
listed package has code today, so the flag is a no-op that CI still passes.

## Layer-isolation contracts

`[tool.importlinter]` enforces forbidden-import contracts. CI and the merge queue run `lint-imports`.

| Contract name | What it guards |
| --- | --- |
| `domain-is-pure` | Domain cannot import application/adapters/infra/entrypoints |
| `domain-no-pydantic` | Domain cannot import pydantic or pydantic_settings |
| `domain-no-async-or-network` | Domain cannot import asyncio/anyio/httpx/socket/... |
| `application-imports-only-domain-and-ports` | Application cannot import adapters/infra/entrypoints |
| `adapters-never-import-entrypoints` | Adapters cannot import entrypoints |

## Running locally

```bash
# Unit tier with the CI coverage settings, then the per-package floors
uv run pytest tests/unit/ -n 4 --cov=src/hermes --cov-branch --cov-report=json --cov-report=term
uv run python scripts/dev/coverage-check.py --bootstrap

# Integration tier
uv run pytest tests/integration/ -n 4

# Contract tier (needs secrets in the environment; each test skips without its own)
uv run pytest tests/contract -m contract

# Layer contracts
uv run lint-imports

# Mutation (see cosmic-ray.toml for the full sequence)
uv run cosmic-ray init cosmic-ray.toml session.sqlite
uv run cosmic-ray exec cosmic-ray.toml session.sqlite
uv run cr-report session.sqlite
```

Smoke tests need the SSH path that only the CD runner has; without `HERMES_SMOKE_SSH` they skip.

## Local gates

- pre-commit hooks run on every commit.
- The pre-push hook runs `scripts/dev/pr-preflight.sh --pre-push`, a fast subset: pytest and coverage are skipped.
- `scripts/dev/create-pr.sh` refuses to open a PR unless the full `scripts/dev/pr-preflight.sh` passed on the current
  HEAD (`scripts/dev/pr-ready-check.sh`). The full preflight runs `pytest tests/unit/` with branch coverage.

## References

- [ADR-017](../architecture/adr-017-test-tiers-as-enforced.md) — the decision this guide implements
- [ADR-007](../architecture/adr-007-testing-strategy.md) — original strategy, superseded in part
- [ai-authored-tests.md](ai-authored-tests.md) — how to write a test that catches bugs
- `pyproject.toml` `[tool.pytest.ini_options]` — marker registrations
- `cosmic-ray.toml` — mutation scope
