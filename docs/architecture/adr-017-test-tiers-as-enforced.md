# ADR-017: Test tiers as enforced — pick tiers by path, mutation with cosmic-ray, one table of where each tier runs

**Date**: 2026-10-03
**Status**: Accepted (merged in PR #471)
**Deciders**: Ricardo Afonso (product owner), Claude (proposer)
**Supersedes**: in part, [ADR-007](adr-007-testing-strategy.md): its tier cadences (§Test categorization), the
mutation tool and scope (§Tool stack, §Mutation testing), the per-package coverage table (§Coverage) and the test-data
rules (§Test data). Everything else in ADR-007 stands, including the SEV-1 annotation, the property-test and snapshot
rationale, and the TDD policy.

---

## Context

ADR-007 promised a tier layout. Three guides then described it three different ways, and none matched what CI runs
(#409):

- Integration tests: ADR-007 said on every push, `test-tiers.md` said nightly, `ai-authored-tests.md` said on merge in
  the unit job. Today `.github/workflows/ci.yml` runs `tests/integration/` on every PR.
- Tools and paths that do not exist: `mutmut` (the repo uses cosmic-ray), `e2e.yml`, `tests/data/`, `tests/snapshots/`.
  `freezegun` is a dev dependency (`pyproject.toml`) that no test or source file imports. Snapshots do exist, but as
  syrupy `__snapshots__/` folders next to the tests that use them.
- Markers: the docs selected unit tests with `pytest -m "unit or property"`. Only six files carry `unit` or
  `property`. CI selects unit tests by path.

ADR-007 is Accepted, so the fix is a new ADR (ADR-009 lifecycle, AGENTS.md rule 10).

## Decision

1. **Tiers are chosen by directory, not by marker.** `tests/unit/`, `tests/integration/`, `tests/contract/`,
   `tests/smoke/` and `tests/e2e/` are the tiers. CI and the merge queue run `pytest tests/unit/` and
   `pytest tests/integration/`. The docs name those commands and no `-m "unit or property"` selector.
2. **Marker registrations stay.** `pyproject.toml` runs pytest with `--strict-markers`, and some tests use `unit`,
   `property` and `integration`. Removing a registration would fail those tests. The registrations are labels only; the
   path decides what runs. The exceptions are `contract` (`addopts` has `-m 'not contract'`, so the scheduled job
   passes `-m contract`) and `smoke` (the CD job passes `-m smoke`).
3. **Mutation testing uses cosmic-ray, not mutmut.** `docs/decisions/2026-08-22-mutation-testing-cosmic-ray.md`
   records why. The scope is the `module-path` in `cosmic-ray.toml`: `src/hermes/domain/value_objects` today. The
   80% kill-rate floor is advisory (`KILL_RATE_ADVISORY: "true"` in `scheduled.yml`) until #406.
4. **Per-package coverage floors are the ones in `scripts/dev/coverage-check.py`**, not the ADR-007 table. See the
   table below. `--bootstrap` makes a package with zero executable lines pass instead of fail; every listed package has
   code today, so the flag changes nothing now. CI still passes it. Dropping it is a follow-up.
5. **`docs/guides/test-tiers.md` is the single source of truth** for what runs where. Each tier has a status: live,
   planned with an issue number, or not planned. Other docs link to it and do not restate cadences.
6. **A tier is "live" only if a workflow or script runs it.** A doc that names a command or workflow that does not
   exist is a bug in the doc.

### Tier table

| Tier | What | Where it runs | Cadence | Status |
| --- | --- | --- | --- | --- |
| Unit | `tests/unit/`, hermetic, includes hypothesis and syrupy tests | `ci.yml` job `ci-success`: `pytest tests/unit/ -n 4 --cov=src/hermes --cov-branch`, then `scripts/dev/coverage-check.py --bootstrap`. Merge queue: `scripts/dev/merge-gates.sh` gate `pytest` | Every PR; every merge-queue batch | live |
| Integration | `tests/integration/`, real adapters against local fakes, no network | `ci.yml` (`pytest tests/integration/ -n 4`); merge queue (same gate as unit) | Every PR; every merge-queue batch | live |
| Contract | `tests/contract/`, real HTTPS to third-party sandboxes | `scheduled.yml` job `contract`: `pytest tests/contract -m contract` | Daily 11:30 UTC | live |
| Smoke | `tests/smoke/`, read-only checks on hermes-01 over SSH | `cd.yml` step "Smoke tests": `pytest tests/smoke -m smoke`; exit 5 (nothing collected) fails the deploy | After each deploy | live |
| Mutation | cosmic-ray over `src/hermes/domain/value_objects` and `src/hermes/domain/lots` | `scheduled.yml` job `mutation` | Sundays 11:45 UTC; manual dispatch with `mutation` ticked | live, 80% kill-rate floor blocking since #406 |
| End-to-end | `tests/e2e/` | `scheduled.yml` job `e2e`: `pytest tests/e2e -m e2e` | Sundays 11:45 UTC; every manual dispatch | live since #408 |

The merge queue and `ci.yml` on a push to `main` do not add a tier. A push to `main` skips the suite when the tree
already passed on the PR head (`scripts/dev/main-tree-validated.sh`).

### Coverage floors (enforced by `scripts/dev/coverage-check.py`)

| Package | Floor |
| --- | --- |
| `src/hermes/domain` | 95% |
| `src/hermes/application` | 90% |
| `src/hermes/adapters/ledger` | 90% |
| `src/hermes/adapters/exchanges`, `fx`, `healthchecks`, `market_data`, `notifier` | 85% |
| `src/hermes/infrastructure/sentry_integration` | 85% |
| `src/hermes/entrypoints` | 70% |

The overall `fail_under = 90` in `pyproject.toml` `[tool.coverage.report]` also applies. Adapter packages not listed
(for example `fills`, `console`, `journal`) have no per-package floor.

## Why

- The docs drifted because each one restated the cadences. One table, one owner, removes the drift.
- Path selection is what CI already does. Moving CI to markers would mean marking every test; moving the docs to paths
  costs nothing.
- cosmic-ray is already wired and running; mutmut was never installed.

## Consequences

### Positive

- Every command and workflow named in the test docs exists and runs.
- A new tier needs one row in `test-tiers.md`, and one workflow or script that runs it.

### Negative

- ADR-007 now says "superseded in part", so a reader needs both documents for the full picture.
- Marker registrations for `chaos`, `e2e`, `mutation` and `slow` stay in `pyproject.toml` although no test uses them.

### Neutral

- The coverage floors, mutation scope and cadences are unchanged. This ADR records them; it does not move them.

## What would change this decision

- #406 flips the kill-rate floor from advisory to blocking, or widens the scope: update the table, no new ADR.
- #408 lands e2e: flip its row to live, no new ADR.
- A new tier, or a move between workflows: edit `test-tiers.md`; a new ADR only if the rationale changes.

## Cross-references

- [ADR-007](adr-007-testing-strategy.md) — superseded in part
- [`docs/guides/test-tiers.md`](../guides/test-tiers.md) — the operational table
- [`docs/guides/ai-authored-tests.md`](../guides/ai-authored-tests.md) — test-writing discipline
- [`docs/decisions/2026-08-22-mutation-testing-cosmic-ray.md`](../decisions/2026-08-22-mutation-testing-cosmic-ray.md)
- [`docs/decisions/2026-08-23-contract-test-tier.md`](../decisions/2026-08-23-contract-test-tier.md)
- Issues: #409 (this reconciliation), #406 (kill-rate floor), #408 (e2e)

## Pending

- Dropping `--bootstrap` from the `ci.yml` coverage step. Not in this change.
