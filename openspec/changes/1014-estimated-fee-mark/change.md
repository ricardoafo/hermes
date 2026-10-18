# Change: 1014-estimated-fee-mark

**SDD change**: `1014-estimated-fee-mark`
**Date**: 2026-10-18
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1014-estimated-fee-mark/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

When Binance charges a fee in a third asset (BNB) and that asset's ticker cannot be read, the
spot adapter charges the pre-registered 10 bps taker rate (PR #1009). Nothing downstream could
tell that fee from a real one. This change carries a `fee_estimated` mark from the fill through
the trade record into the ledger. The ledger keeps the mark in a new additive table, written in
the trade's own transaction, and lists every marked trade for correction. The main risk is
schema: one new table, which an older release ignores.

## Scope

### In

- Domain: `Fill.fee_estimated` and `TradeRecord.fee_estimated`, both default `False`;
  `TradeRecord.from_fill` copies the mark.
- Adapter: the Binance spot adapter sets the mark when any part of a fill's fee was priced at
  the taker rate.
- Ledger: migration `20261018T0100_estimated_fees.sql` (table `estimated_fees`, one row per
  marked trade), written by `record_trade`; `list_estimated_fees()` on the new read port
  `EstimatedFeeSourcePort`, returning `EstimatedFee` rows oldest first.

### Out

- A command or console view that prints the list. The read port is what a tax, P&L or
  reconciliation surface calls; the adapter is not wired into serve or backtest yet, so no
  ledger holds a marked trade before the live phase.
- Correcting a fee. A correction from the exchange's trade history is a later change; it will
  delete the mark in the same transaction as the fee update.

## Decisions

### D-1: The mark is a side table, not a column on `trades`

- **Decision**: a new table `estimated_fees (trade_id PRIMARY KEY REFERENCES trades(id))`.
- **Alternatives**: a `fee_estimated` column on `trades` (an `ALTER TABLE` on the busiest
  table, and every trade row carries a field that is almost always false); a flag in the
  `TradeExecuted` event payload (changes every new event's payload, which replay and parity
  evidence hash).
- **Rationale**: the side table is additive, so an older release ignores it and rollback is
  the usual one (ledger-migrations guide). It also makes the correction list a plain join.
- **Cost of reversal**: LOW. Drop the table in a destructive migration; nothing else reads it.

### D-2: The mark is a boolean, not a method name

- **Decision**: `Fill.fee_estimated: bool`.
- **Alternatives**: an enum naming how the fee was estimated.
- **Rationale**: there is one estimate today (the pre-registered taker rate). The correction
  reads the exchange's own figure, whatever the estimate was.
- **Cost of reversal**: LOW. Widen to an enum with `True` mapping to the taker rate.

### D-3: Fees are listed through a new read port

- **Decision**: `EstimatedFeeSourcePort.list_estimated_fees()`, not a method on
  `TradeLedgerPort`.
- **Alternatives**: extend `TradeLedgerPort`, which every in-memory fake would then implement.
- **Rationale**: readers ask for only what they read (interface segregation), as
  `TaxTradeSourcePort` already does.
- **Cost of reversal**: LOW.

## Requirements

### R-1: An estimated fee is marked on the fill

- **A-1.1**: `uv run pytest -q tests/unit/adapters/exchanges/test_binance_spot.py -k estimated`
  passes: a taker-rate fee is marked, a ticker-priced or quote fee is not.

### R-2: The mark reaches the ledger with the trade, atomically

- **A-2.1**: `uv run pytest -q tests/unit/adapters/ledger/test_sqlite_estimated_fees.py` passes:
  the marked trade is listed with its stored fee, the exact one is not, the list is oldest
  first, and a refused trade leaves no mark.
- **A-2.2**: `uv run pytest -q tests/unit/application/ledger/test_trade_recorder.py` passes:
  `TradeRecorder` carries the mark from a fill to the listing.

### R-3: The migration is additive

- **A-3.1**: the migration test applies it after the previous file and finds only the new
  table added, with every trade kept.

## Design

```sql
CREATE TABLE IF NOT EXISTS estimated_fees (
    trade_id     INTEGER PRIMARY KEY REFERENCES trades(id)
);
```

```python
@dataclass(frozen=True, slots=True)
class EstimatedFee:
    trade_id: int
    pair: Pair
    side: OrderSide
    executed_at: datetime
    fee: Money


class EstimatedFeeSourcePort(Protocol):
    def list_estimated_fees(self) -> tuple[EstimatedFee, ...]: ...
```

`SqliteLedger.record_trade` inserts the `estimated_fees` row right after the trade row, inside
the same `_write` transaction. `BinanceSpotAdapter._quote_fee` returns the fee and whether it
is the taker-rate estimate; `_fill` ORs the per-part flags into `Fill.fee_estimated`.

## Test plan

1. Every acceptance criterion above passes.
2. `uv run pytest -q` passes, with the ledger reader fuzz and the risk-inputs migration test
   unchanged in what they claim.

## Risks

- A new migration adds a row to `schema_migrations`. The ledger reader fuzz test could draw
  that bookkeeping table, and a corrupt filename re-runs an old migration, which raises
  `sqlite3.Error` by design. The fuzz test now leaves `schema_migrations` out; the runner's
  own tests cover that path.
- The risk-inputs migration test assumed its file was the last one. It now applies files up
  to its own, so a later migration no longer breaks it.
