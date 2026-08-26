# Change: spec-2c-simulated-exchange-adapter

**SDD change**: `spec-2c-simulated-exchange-adapter`
**Date**: 2026-08-25
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/spec-2c-simulated-exchange-adapter/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Implements `SimulatedExchangeAdapter` — a composition-root-swappable
`ExchangePort` that wires `FillModelPort` (Spec 2a/2b), `MarketDataPort`
(Spec 4b), and `ClockPort` (Spec 4a) into a single concrete exchange
adapter. The central architectural pick is **port injection over
mode branching**: backtest and shadow modes share the SAME adapter code;
only the injected ports differ at the composition root (backtest =
synthetic bar-derived orderbook + SimulatedClock; shadow = real live
L2 stream + WallClock). This eliminates the divergence failure mode
where separate backtest vs live code paths accumulate subtle semantic
differences.

## Scope

### In

- `openspec/changes/spec-2c-simulated-exchange-adapter/change.md` — this spec.
- `openspec/changes/spec-2c-simulated-exchange-adapter/issue.md` — tracking issue #238.
- `src/hermes/adapters/exchanges/simulated.py` — `SimulatedExchangeAdapter`
  implementing `ExchangePort` via port injection.
- `tests/unit/adapters/exchanges/test_simulated.py` — unit tests (≥25).
- `tests/unit/adapters/exchanges/test_simulated_property.py` — property tests (≥5).
- `tests/integration/test_simulated_exchange_router.py` — integration tests (≥3).
- `changelog.d/wave-spec-2c-simulated-exchange-adapter.md` — changelog fragment.

### Out

- Live exchange adapter (Binance signed / Kraken signed — MVP2 or separate spec).
- Queue position modeling (M-07, MVP2 per OD-2).
- WebSocket state reconciliation (Spec 4c for the market data side).
- Actual persistence of adapter state across restarts (event sourcing via
  Spec 1 hash chain covers this; adapter is stateless-per-run).

## Design Decisions

### D-1 — Port injection, no mode flag

`SimulatedExchangeAdapter` accepts a `FillModelPort`, a callable that
returns an `Orderbook` for a given `Pair`, a `ClockPort`, and a
`FeeSchedule`. It has NO `mode: Literal["backtest", "shadow"]` field.
The composition root decides what to wire.

### D-2 — Open orders queue + committed balances

The adapter maintains an in-memory open-order queue (dict keyed by
`order_id: str`) and a committed-balance ledger (`dict[Currency, Decimal]`).
Market orders fill immediately; limit orders are IOC — fill or reject
immediately, never rest in the queue beyond the call.

### D-3 — Lot tracking on fills

On every BUY fill the adapter opens a `Lot` via `Lot.open()` and
appends it to an internal `lot_ledger: list[Lot]`. SELL fills are not
lot-tracked here (FIFO consumption is delegated to the tax layer in
Spec 1 domain). The lot_ledger provides observability for integration
tests.

### D-4 — Deterministic under seed

`SimulatedExchangeAdapter` accepts an optional `random.Random` instance
for future queue-position modeling. Currently unused but wired so that
property tests can pass a seeded RNG and assert reproducibility.

### D-5 — `get_orderbook` delegates to injected callable

`get_orderbook(pair)` calls `self._orderbook_fn(pair)`. In backtest
mode the composition root passes a closure over the synthetic bar-derived
book; in shadow mode it passes a closure that calls a live market-data
adapter.

### D-6 — `get_balance` reads committed-balance ledger

Balances are initialised from a `dict[Currency, Decimal]` passed at
construction. `place_market_order` / `place_limit_order` on BUY deduct
executed_qty × avg_price from quote balance; on SELL deduct executed_qty
from base balance. `get_balance` returns the free (uncommitted) balance.

### D-7 — `get_fees` delegates to injected FeeSchedule provider

`get_fees(pair)` calls `self._fee_schedule_fn(pair)` — a callable that
returns a `FeeSchedule`. Callers in tests pass a lambda over a hardcoded
schedule; in production it wraps the exchange fee endpoint.
