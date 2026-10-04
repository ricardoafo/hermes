# Change: 387-portfolio-marks

**SDD change**: `387-portfolio-marks`
**Date**: 2026-10-04
**Status**: Applied
**Tracking issue**: see sibling `issue.md` (#387)

Mirror of engram topic key `sdd/387-portfolio-marks/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

The console runs as its own process and cannot read `PortfolioTracker`'s memory. This change makes `hermes serve`
store one portfolio mark per handled bar (cash, positions, market value, equity, realized PnL, peak equity, in the
strategy's quote currency) in a new `portfolio_marks` table, and exposes a read-only `PortfolioHistoryPort` the
console's read model can use. Ledger and persistence only: no console code, strategy, sizing or signal changes. Main
risk: these rows hold equity and PnL, so AI-facing tools must keep excluding them before certification (ADR-012 D-06).

## Scope

### In

- Migration `20261004T0300_portfolio_marks.sql` (additive, timestamp-named per #483).
- Domain `PortfolioMark` (snapshot plus the bar stream and close time).
- `PortfolioHistoryPort` and its `SqliteLedger` implementation: `record_portfolio_mark`, `portfolio_marks`,
  `latest_portfolio_mark`.
- `PortfolioTracker.current` (read-only access to the snapshot).
- `_BarLoop.handle` records the mark after the strategy evaluates the bar, before `BarProcessed`.

### Out

- The console's `LedgerReadModel` and sparkline ranges reading the marks: console work (#377, console writer).
- EUR equity per mark: needs the EUR rate per bar date. It follows `hermes fx backfill` once #405 lands, as a
  follow-up, so the backfill is not edited by two PRs at once.

## Decisions

### D-1: One row per bar, first write wins

- **Decision**: `UNIQUE (strategy, pair, interval, bar_close_time)` with `INSERT OR IGNORE`.
- **Rationale**: A restart re-handles no processed bar (cursor), but if it did, the stored mark stays evidence.
- **Cost of reversal**: LOW.

### D-2: Snapshot taken after the strategy evaluates the bar

- **Decision**: The mark is the tracker after `mark(bar)` and the strategy pass. With fills (#353), fills applied while
  handling the bar are included.
- **Cost of reversal**: LOW.

## Requirements

### R-1: N bars, N marks

- **A-1.1**: After N handled bars the ledger holds N marks for the strategy, the last equal to the tracker's state.

### R-2: Restart

- **A-2.1**: A restart followed by one bar writes a mark with the same cash and equity as the one before the
  restart, at the new bar's close.

### R-3: Exact round trip

- **A-3.1**: A stored mark reads back equal, positions included.

## Design

```text
src/hermes/adapters/ledger/migrations/20261004T0300_portfolio_marks.sql   new
src/hermes/domain/portfolio/mark.py                                       new
src/hermes/ports/portfolio_history.py                                     new
src/hermes/adapters/ledger/sqlite_ledger.py                               + three methods
src/hermes/application/portfolio/tracker.py                               + current
src/hermes/entrypoints/serve.py                                           record per handled bar
```

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.

## Risks

- **PnL in the ledger.** The marks are for operator pull surfaces only; supervisor tools (#470) must not read
  `portfolio_marks` before certification.
- **Growth.** About 21 rows a day for three pairs at 4h and 1d.

## Dependencies on other changes

- None open. Coordinates with #405 (backfill) for EUR equity.
