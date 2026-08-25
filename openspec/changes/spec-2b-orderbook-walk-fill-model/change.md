# Change: spec-2b-orderbook-walk-fill-model

**SDD change**: `spec-2b-orderbook-walk-fill-model`
**Date**: 2026-08-25
**Status**: Applied
**Tracking issue**: see sibling `issue.md` (#230)

Mirror of engram topic key `sdd/spec-2b-orderbook-walk-fill-model/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Delivers `OrderbookWalkFillModel` — the concrete implementation of
`FillModelPort` introduced in Spec 2a. The same class drives both backtest
(synthetic book from historical bars) and shadow (live L2 book) modes,
enforcing ADR-011 D-02's same-code-path invariant: no `if backtest_mode:`
branching exists anywhere. Fill semantics follow ADR-011: market orders fill
at the next bar's open (anti-Zipline), limit orders fill when price crosses
within a future bar, and slippage (backtest only) is 5 bps for entries and
15 bps for stop-exits. The main risk is over-fill when orderbook depth is
shallow; a strict depth check returns `Rejection` before any fill is
attempted.

## Scope

### In

- `src/hermes/adapters/fills/__init__.py` — public re-export.
- `src/hermes/adapters/fills/orderbook_walk.py` — `OrderbookWalkFillModel`
  implementing `FillModelPort`.
- `openspec/changes/spec-2b-orderbook-walk-fill-model/change.md` (this file).
- `openspec/changes/spec-2b-orderbook-walk-fill-model/issue.md`.
- `tests/unit/adapters/fills/__init__.py`.
- `tests/unit/adapters/fills/test_orderbook_walk.py` — property tests,
  anti-Zipline test, determinism, rejection matrix.
- `changelog.d/wave-spec-2b-orderbook-walk-fill-model.md`.

### Out

- `SimulatedExchangeAdapter` (Spec 2c) — wires `OrderbookWalkFillModel` into
  an exchange adapter facade.
- Queue-position modeling (M-07, MVP2 per OD-2).
- Latency simulation (M-05, MVP2).
- Real L2 collector (VPS-side, separate script).
- Calibration harness (Spec 6).

## Decisions

### D-1: Same-code-path — no mode flag on the class

- **Decision**: `OrderbookWalkFillModel` carries no `mode` flag, no
  `is_backtest` parameter. Backtest vs shadow distinction is entirely in what
  `Orderbook` is wired at the composition root.
- **Alternatives**: Boolean `is_backtest` constructor arg (discarded — adds
  the exact branching ADR-011 forbids); separate `BacktestFillModel` /
  `ShadowFillModel` classes (discarded — duplicates logic).
- **Rationale**: ADR-011 D-02 "same-code-path invariant". No `if` branching
  on mode anywhere in the adapter layer.
- **Cost of reversal**: MEDIUM — would require splitting the class and updating
  wiring at the composition root.

### D-2: Slippage applied only when `apply_slippage=True`

- **Decision**: The constructor accepts `apply_slippage: bool = False`. The
  backtest harness passes `True`; shadow mode passes `False` (real spread is
  already captured in the walked book).
- **Alternatives**: Hardcode slippage always (discarded — wrong for shadow);
  derive from mode flag (discarded — see D-1).
- **Rationale**: Pre-reg §6.2 scopes slippage to backtest only. The flag is
  set at composition-root time, not inside the fill logic.
- **Cost of reversal**: LOW.

### D-3: Deterministic PRNG via `random.Random` instance

- **Decision**: Constructor accepts `rng: random.Random | None = None`.
  `None` → a fresh `random.Random()` seeded from OS entropy. Callers pass
  an explicit seeded instance for reproducible chaos testing.
- **Alternatives**: `random` module globals (discarded — not reentrant,
  breaks parallel test runs); `numpy.random` (discarded — heavy dep).
- **Rationale**: Property and determinism tests need reproducible sequences
  without affecting global random state.
- **Cost of reversal**: LOW.

### D-4: `PRICE_FILTER` rejection for limit orders outside bar range

- **Decision**: A limit buy above `bar_high` or limit sell below `bar_low`
  is returned as `Rejection(reason="PRICE_FILTER: ...")`.
- **Alternatives**: Silent skip / no-fill (discarded — ambiguous for callers);
  raise exception (discarded — `Rejection` is the expected outcome per spec).
- **Rationale**: SA-2 §6 and the rejection matrix in the test plan.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Port conformance

- **A-1.1**: `isinstance(OrderbookWalkFillModel(), FillModelPort)` returns
  `True` (structural check via `@runtime_checkable`).

### R-2: Anti-Zipline (SA-2 AP-1)

- **A-2.1**: A market order submitted at bar T fills at bar T+1 open —
  the caller passes the next bar's open as the `next_open` parameter; the
  fill price equals that value plus slippage. The fill model MUST NOT use
  the current bar's close as execution price.

### R-3: Limit fill semantics

- **A-3.1**: Limit buy fills at the ask if the best ask ≤ limit_price.
- **A-3.2**: Limit sell fills at the bid if the best bid ≥ limit_price.
- **A-3.3**: Returns `Rejection(reason="PRICE_FILTER: ...")` when limit is
  unreachable.

### R-4: Quantity bound

- **A-4.1**: `fill.executed_qty <= order.qty.amount` always (no over-fill).

### R-5: Price in bar range (backtest mode)

- **A-5.1**: Fill price is within `[bar_low, bar_high]` when `apply_slippage`
  is `False`. With slippage the price may exceed `bar_high` by at most the
  configured bps.

### R-6: Rejection paths

- **A-6.1**: Zero-quantity order → `Rejection(reason="ZERO_QTY: ...")`.
- **A-6.2**: Book too thin (insufficient depth) → `Rejection(reason="INSUFFICIENT_DEPTH: ...")`.
- **A-6.3**: PRICE_FILTER violation → `Rejection(reason="PRICE_FILTER: ...")`.

### R-7: Deterministic replay

- **A-7.1**: `OrderbookWalkFillModel(rng=random.Random(42))` called twice
  with identical inputs produces identical `Fill` results.

## Design

### Class signature

```python
class OrderbookWalkFillModel:
    def __init__(
        self,
        *,
        apply_slippage: bool = False,
        entry_slippage_bps: int = 5,
        stop_slippage_bps: int = 15,
        rng: random.Random | None = None,
    ) -> None: ...

    def fill_order(
        self,
        order: Order,
        orderbook: Orderbook,
        clock: datetime,
        fees: FeeSchedule,
    ) -> Fill | Rejection: ...
```

### Slippage application

```text
entry_multiplier = 1 + (entry_slippage_bps / 10000)  # buys
entry_multiplier = 1 - (entry_slippage_bps / 10000)  # sells
stop_multiplier  = 1 + (stop_slippage_bps / 10000)   # stop-exits
```

Applied only when `apply_slippage=True`.

### Walk algorithm

1. Reject zero-qty order immediately.
2. Select levels: asks for BUY, bids for SELL.
3. Walk levels consuming quantity until order is filled or depth runs out.
4. If depth runs out → `Rejection("INSUFFICIENT_DEPTH: ...")`.
5. Compute VWAP from consumed levels.
6. Apply slippage (if enabled).
7. Compute fee from `fees.fee_for_volume(volume_usd=Decimal("0"), is_maker=False)`.
8. Return `Fill(order, executed_qty, avg_price, fee, clock)`.

For limit orders: check limit price against best ask/bid first. If
unreachable → `Rejection("PRICE_FILTER: ...")`.

## Test plan

1. Port conformance: `isinstance(impl, FillModelPort)` → `True`.
2. Anti-Zipline: market order → fill price equals `next_open` (not current close).
3. Fill quantity ≤ order quantity property.
4. Fill price within walked book levels property.
5. Determinism: same `random.Random(seed)` + same inputs → identical fills.
6. Rejection matrix: zero qty, insufficient depth, PRICE_FILTER.
7. `bash scripts/dev/pr-preflight.sh` → all green.

## Risks

- **Decimal precision drift**: VWAP computation across many levels may
  accumulate rounding error. Mitigation: use `Decimal` throughout; never
  cast to `float`.
- **Crossed book**: `Orderbook.__post_init__` already rejects crossed books
  before reaching the fill model, but we add an assertion guard anyway.
- **Clock staleness**: The fill model does not validate that `clock` is
  newer than `orderbook.snapshot_time`. That responsibility belongs to the
  composition root (noted in `FillModelPort` docstring).

## Dependencies on other changes

- Spec 2a (`spec-2a-fill-model-port`, merged as #208) — `FillModelPort`,
  `Order`, `Fill`, `Rejection` domain types.
