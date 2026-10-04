# Change: 630-seed-eur-at-boot

**SDD change**: `630-seed-eur-at-boot`
**Date**: 2026-10-04
**Status**: Proposed
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/630-seed-eur-at-boot/change`.

## Summary

`hermes serve` now prices pending EUR values once at start-up, after the capital seed
is written and before the bar loop starts, by calling the existing
`backfill_pending_eur` service with the pricer the composition already owns. No new
pricing logic, no new port. The main risk is boot latency during an FX outage, bounded
by the existing per-request FX timeout and the pricer's per-day cache.

## Scope

### In

- `Composition` gains the shared `eur_pricer`; `serve.run` calls `price_pending_eur_once`.
- Unit tests for priced-at-boot, FX down, ledger failure and already-priced rows.

### Out

- A periodic re-pricing schedule; a later boot or `hermes fx backfill` prices leftovers.

## Decisions

### D-1: Hook in `run`, before the task group

- **Decision**: call the backfill from `serve.run`, synchronously, before the bar loop.
- **Alternatives**: in `build_composition` (pure wiring, would add network I/O to it);
  a thread or task (the SQLite connection is single-thread); a timer (more machinery).
- **Rationale**: one layer (entrypoints) calls an existing application service; the
  hexagonal contracts hold and the bar path is untouched.
- **Cost of reversal**: LOW.

### D-2: Failure is a warning

- **Decision**: any exception is logged as `serve.eur_pricing_failed` and swallowed.
- **Rationale**: serve must never fail to start because of FX; rows stay pending.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Seed priced at boot

- **A-1.1**: with FX reachable, a fresh boot leaves the capital seed `eur_status = 'ok'`.

### R-2: FX outage is harmless

- **A-2.1**: with FX down, serve exits 0 and the seed stays pending.

### R-3: Priced rows are untouched

- **A-3.1**: a second boot makes no FX call for an already-priced seed.

## Design

`price_pending_eur_once(c)` in `src/hermes/entrypoints/serve.py` calls
`backfill_pending_eur(c.ledger, c.eur_pricer, today=fill_date_madrid(c.clock.now()))`
and logs `serve.eur_priced` with the four counts. Touches one layer; no new port.

## Test plan

1. `uv run pytest tests/unit/entrypoints/test_serve_eur_pricing.py`
2. `bash scripts/dev/pr-preflight.sh` green.

Test tiers touched: unit

## Risks

- **FX outage latency**: each distinct date costs up to one FX timeout. Boot has few
  distinct pending dates; the per-day cache bounds the rest.

## Dependencies on other changes

- None.
