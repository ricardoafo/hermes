# Change: 421-live-bars

**SDD change**: `421-live-bars`
**Date**: 2026-10-03
**Status**: Applied
**Tracking issue**: see sibling `issue.md` (#421)

Mirror of engram topic key `sdd/421-live-bars/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`hermes serve` keeps only `BarProcessed` (pair, interval, time) for each handled bar, so no live bar values exist
for the Binance archive check (#359) or the nightly parity replay (#358). This change records each handled bar's
OHLCV in an append-only `live_bars` table in the ledger database. It only records data: the strategy, sizing,
signals, fees, metrics and the `BarProcessed` schema are unchanged, as pre-registration rule 1 requires during
certification. Main risk: a write failure now stops the loop, the same as any other ledger write failure.

## Scope

### In

- Migration `0003_live_bars.sql`: table keyed by (pair, interval, open_time), with triggers that refuse UPDATE and
  DELETE.
- `ports/live_bars.py`: `LiveBarStorePort` (`record_live_bar`, `live_bars`).
- `SqliteLedger.record_live_bar` and `SqliteLedger.live_bars`.
- `_BarLoop.handle` records the bar before publishing `BarProcessed`, and logs
  `serve.live_bar.differs_from_stored` if a replayed bar's values differ from the stored row.

### Out

- Warm-up bars used only to seed the first-boot cursor are not handled, so they get no row.
- The archive check (#359) and the parity replay (#358) that read this table.

## Decisions

### D-1: A table in the ledger database, not a schema change to `BarProcessed`

- **Decision**: New `live_bars` table through a numbered migration (ADR-006).
- **Alternatives**: `BarProcessed` schema v2 with OHLCV (changes an event the cursor logic reads, and needs an
  upcaster). A separate `klines.db` (a second database for backup and health checks to cover).
- **Rationale**: Additive, in the database the backup already covers, and invisible to the strategy.
- **Cost of reversal**: LOW.

### D-2: First record wins

- **Decision**: `INSERT OR IGNORE`; a later bar with the same key and different values is reported and not stored.
- **Rationale**: Stored bars are evidence for #359 and #358; rewriting them would hide a data problem.
- **Cost of reversal**: LOW.

## Requirements

### R-1: One row per handled bar, exact values

- **A-1.1**: After a serve run, `live_bars` holds exactly the handled bars with their exact OHLCV.

### R-2: Idempotent across restarts

- **A-2.1**: A restart over the same history adds no row.

### R-3: Append-only

- **A-3.1**: `UPDATE` and `DELETE` on `live_bars` raise `sqlite3.IntegrityError`.

### R-4: Data only

- **A-4.1**: Existing serve, strategy and ledger tests pass unchanged.

## Design

```text
src/hermes/adapters/ledger/migrations/0003_live_bars.sql   new
src/hermes/ports/live_bars.py                              new
src/hermes/adapters/ledger/sqlite_ledger.py                + record_live_bar, live_bars
src/hermes/entrypoints/serve.py                            record before BarProcessed
```

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.

## Risks

- **Ledger write failure stops the loop.** Same behaviour as a failed `BarProcessed` write today; systemd restarts it.
- **Table growth.** About 21 rows a day for three pairs at 4h and 1d, which is negligible.

## Dependencies on other changes

- #359 and #358 read this table.
