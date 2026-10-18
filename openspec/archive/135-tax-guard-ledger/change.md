# Change: 135-tax-guard-ledger

**SDD change**: `135-tax-guard-ledger`
**Date**: 2026-10-03
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#135)

Mirror of engram topic key `sdd/135-tax-guard-ledger/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

The recharacterization tax guard (ADR-002 §6) evaluates an in-memory list of events, so nothing connects it to the
trades the ledger records. This change adds a read-only `TradeHistoryPort` with `trades_between(start, end)`,
implements it on `SqliteLedger` over the `events` table, and adds `evaluate_ledger`, which reads exactly the rolling
window and runs the existing pure `evaluate`. No threshold, strategy or sizing changes. Main risk: none at runtime;
nothing calls the guard on a schedule yet (#136 wires the pause).

## Scope

### In

- `ports/trade_history.py`: `TradeHistoryPort`.
- `SqliteLedger.trades_between`: every strategy's `TradeExecuted` rows with `start <= occurred_at <= end`.
- `application/tax_guard/window.py`: `evaluate_ledger(history, config, *, now, correlation_id)`.
- `trade_events` and `trades_between` share one row-to-event helper.

### Out

- Running the guard periodically and pausing the engine on a breach: #136.

## Decisions

### D-1: A separate read port, not a new `LedgerPort` method

- **Decision**: `TradeHistoryPort` holds only `trades_between`.
- **Alternatives**: Add the method to `LedgerPort` (every fake that claims `LedgerPort` would have to grow it).
- **Rationale**: The guard needs one read; a narrow port keeps its fakes trivial (code-conventions §5).
- **Cost of reversal**: LOW.

### D-2: All strategies count

- **Decision**: `trades_between` does not filter by strategy.
- **Rationale**: AEAT recharacterization looks at the taxpayer's activity, not one strategy bucket (ADR-002 §6).
- **Cost of reversal**: LOW.

## Requirements

### R-1: Window read

- **A-1.1**: `evaluate_ledger` asks for `[now - recharacterization_window_days, now]` exactly.

### R-2: End to end

- **A-2.1**: `TradeExecuted` rows written to a SQLite ledger are read back across strategies and a threshold breach
  fires; rows older than the window do not count.

### R-3: Coverage

- **A-3.1**: `SqliteLedger.trades_between` is covered by `tests/unit/adapters/ledger/test_sqlite_ledger.py`.

## Design

```text
src/hermes/ports/trade_history.py              new
src/hermes/adapters/ledger/sqlite_ledger.py    + trades_between, _trades_from_rows
src/hermes/application/tax_guard/window.py     + evaluate_ledger
```

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.

## Risks

- **ISO string comparison of `occurred_at`.** Bounds are written with the same `_iso_utc` format as stored rows, so
  lexical order matches time order.

## Dependencies on other changes

- None open. Builds on `SqliteLedger.trade_events` (#392).
