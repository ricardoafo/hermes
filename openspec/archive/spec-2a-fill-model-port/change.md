# Change: spec-2a-fill-model-port

**SDD change**: `spec-2a-fill-model-port`
**Date**: 2026-08-25
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/spec-2a-fill-model-port/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

This change introduces `FillModelPort` — the hexagonal port that unifies
backtest, shadow, and live fill semantics under a single method signature —
and replaces the 19-LOC empty `ExchangePort` stub with a full Protocol
contract (closes Issue #199). The central architectural pick is a
**single-method protocol** (`fill_order`) so that the same-code-path
invariant (D-02 in the paper-trading engine design) is machine-checkable:
any deviation from the single path is a type error, not a runtime branch.
The main risk is that pre-existing exchange adapter code does not yet satisfy
the new `ExchangePort` shape — deliberately deferred; this PR only defines
contracts, not concrete implementations.

## Scope

### In

- `src/hermes/domain/trading/order.py` — `Order`, `OrderSide` domain types
- `src/hermes/domain/trading/fill.py` — `Fill` and `Rejection` result types
- `src/hermes/domain/trading/__init__.py` — package init
- `src/hermes/ports/fill_model.py` — `FillModelPort` `@runtime_checkable` Protocol
- `src/hermes/ports/exchange.py` — replace 19-LOC empty stub with full `ExchangePort` Protocol (closes #199)
- `tests/unit/ports/test_fill_model_port.py` — structural-conformance harness
- `tests/unit/ports/test_exchange_port.py` — structural-conformance harness
- `tests/unit/ports/__init__.py` — package init for test discovery
- `changelog.d/wave-spec-2a-fill-model-port.md` — changelog fragment

### Out

- `OrderbookWalkFillModel` concrete implementation — deferred to Spec 2b
- `SimulatedExchangeAdapter` concrete implementation — deferred to Spec 2c
- Binance/Kraken adapter migration to the new `ExchangePort` shape — follow-up
- `ClockPort` — separate Spec 4a (already in flight)
- `MarketDataPort` — separate Spec 4b

## Decisions

### D-1: Single-method vs multi-method `FillModelPort`

- **Decision**: Single-method Protocol — `fill_order(order, orderbook,
  clock, fees) -> Fill | Rejection`.
- **Alternatives**: (a) multi-method (separate `simulate_fill`, `live_fill`) —
  rejected because the whole point of the port is the same-code-path invariant;
  two methods guarantee divergence. (b) context-object pattern (inject a
  `FillContext` with all deps) — more indirection with no gain at this stage.
- **Rationale**: D-02 in the paper-trading engine design states that backtest,
  shadow, and live modes must call the IDENTICAL code path — differing only in
  which `Orderbook` source, clock, and exchange adapter are wired at the
  composition root. A single method makes that invariant a structural property:
  any impl that deviates cannot satisfy the Protocol.
- **Cost of reversal**: LOW — adding a second method to the Protocol is
  backwards-compatible at the structural level; the main cost is updating
  every conformance test.

### D-2: Backtest vs live divergence at the interface level

- **Decision**: Identical signature for all modes; injection at the
  composition root decides which `Orderbook` source feeds `fill_order`.
  In backtest: a synthetic orderbook reconstructed from historical snapshots.
  In shadow: a live orderbook fetched from the exchange. In live: a live
  orderbook fetched from the exchange (same as shadow — the difference is
  whether `ExchangePort.place_market_order` is called with real capital).
- **Alternatives**: (a) mode flag on `fill_order` — rejected; a mode
  flag inside the port is the `if dry_run:` antipattern that caused the
  failures documented in the paper-trading engine research. (b) separate
  port per mode — rejected; same-code-path invariant is violated.
- **Rationale**: Mode divergence at the interface level destroys the
  reliability guarantee. The composition root's dependency injection IS
  the mode switch; the port sees none of it.
- **Cost of reversal**: HIGH — the entire same-code-path design depends
  on this. Reversal would require architectural rework.

### D-3: `ExchangePort` scope — read + write methods vs read-only

- **Decision**: Both read and write methods in `ExchangePort`. Real-money
  guards (I-08 from ADR-003) are the composition root's responsibility,
  not the port's. The port defines the contract; the adapter enforces
  safety by checking trade mode at instantiation or per-call.
- **Alternatives**: (a) read-only port — rejected; the live `fill_order`
  impl must eventually call `place_market_order` through this port. (b)
  split into `ExchangeReadPort` + `ExchangeWritePort` — deferred;
  premature for MVP1 when there is one adapter target.
- **Rationale**: The port shape must cover the full exchange contract.
  Safety (real-money guard) is an adapter-level concern per ADR-003 §00
  invariant I-08.
- **Cost of reversal**: MEDIUM — splitting the port later requires
  updating all adapter impls and injection sites.

### D-4: `Fill | Rejection` return type — sum type

- **Decision**: `Fill | Rejection` discriminated union using Python's
  native `|` union type. No `returns.result.Result` wrapper at the port
  boundary. `Fill` and `Rejection` are domain value objects — both are
  expected outcomes, not errors, so the `Result` error-channel pattern
  does not apply here. `InfraError` is still raised for genuine
  infrastructure failures (e.g. exchange unreachable during live fill).
- **Alternatives**: (a) `Result[Fill, Rejection]` — rejected; `Rejection`
  is a named business outcome (insufficient liquidity, price drift past
  limit), not an error. Callers need to pattern-match on it, not handle
  it as a failure channel. (b) Enum with payload — more verbose without
  type-narrowing benefits.
- **Rationale**: Native union + `isinstance` narrowing is idiomatic Python
  3.10+ and aligns with how `Orderbook` and other domain types are
  handled. The `@runtime_checkable` Protocol means `isinstance(impl,
  FillModelPort)` works for conformance tests.
- **Cost of reversal**: LOW — changing the return type annotation is
  localized to the port and its conformance tests.

## Requirements

### R-1: `FillModelPort` is a runtime-checkable Protocol

- **A-1.1**: `from hermes.ports.fill_model import FillModelPort` succeeds
- **A-1.2**: `FillModelPort` is decorated with `@runtime_checkable`
- **A-1.3**: A minimal concrete class implementing `fill_order` satisfies
  `isinstance(impl, FillModelPort)` at runtime

### R-2: `fill_order` signature matches the same-code-path invariant

- **A-2.1**: Method signature is `fill_order(self, order: Order, orderbook: Orderbook, clock: datetime, fees: FeeSchedule) -> Fill | Rejection`
- **A-2.2**: All parameter types are importable from `hermes.domain`
- **A-2.3**: Return type is `Fill | Rejection` (not `Result`)

### R-3: `ExchangePort` replaces the 19-LOC empty stub with real methods

- **A-3.1**: `ExchangePort` defines `place_market_order`, `place_limit_order`,
  `cancel_order`, `get_orderbook`, `get_balance`, `get_fees`
- **A-3.2**: `ExchangePort` is `@runtime_checkable`
- **A-3.3**: A minimal concrete class implementing all methods satisfies
  `isinstance(impl, ExchangePort)` at runtime
- **A-3.4**: Closes Issue #199

### R-4: Domain types (`Order`, `Fill`, `Rejection`) are pure domain values

- **A-4.1**: No pydantic, no httpx in `hermes.domain.trading`
- **A-4.2**: `Order` has `side: OrderSide`, `pair: Pair`, `qty: Quantity`,
  and optional `limit_price: Decimal | None`
- **A-4.3**: `Fill` carries `executed_qty`, `avg_price`, `fee`, `timestamp`
- **A-4.4**: `Rejection` carries `reason: str` and `order: Order`

### R-5: Import-linter contracts still pass

- **A-5.1**: `uv run lint-imports` exits 0 after the change
- **A-5.2**: `hermes.domain` does not import from `hermes.ports` or `hermes.adapters`
- **A-5.3**: `hermes.ports.fill_model` imports only from `hermes.domain` and stdlib

### R-6: mypy --strict passes on all new modules

- **A-6.1**: `uv run mypy --strict src/hermes` exits 0

## Design

### File tree

```text
src/hermes/
  domain/
    trading/
      __init__.py      (new)
      order.py         (new) — Order, OrderSide
      fill.py          (new) — Fill, Rejection
  ports/
    fill_model.py      (new) — FillModelPort Protocol
    exchange.py        (replace) — full ExchangePort Protocol
tests/
  unit/
    ports/
      __init__.py      (new)
      test_fill_model_port.py  (new)
      test_exchange_port.py    (new)
changelog.d/
  wave-spec-2a-fill-model-port.md  (new)
openspec/changes/
  spec-2a-fill-model-port/
    change.md  (this file)
    issue.md   (sibling)
```

### `src/hermes/domain/trading/order.py`

```py
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class Order:
    """Immutable order request — the input to FillModelPort.fill_order."""

    side: OrderSide
    pair: Pair
    qty: Quantity
    limit_price: Decimal | None = None  # None → market order
```

### `src/hermes/domain/trading/fill.py`

```py
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hermes.domain.trading.order import Order
from hermes.domain.value_objects.money import Money


@dataclass(frozen=True, slots=True)
class Fill:
    """Successful fill result — ALL fields required, no None."""

    order: Order
    executed_qty: Decimal
    avg_price: Decimal
    fee: Money
    timestamp: datetime  # UTC-aware


@dataclass(frozen=True, slots=True)
class Rejection:
    """Order rejected by the fill model — expected outcome, not an error."""

    order: Order
    reason: str
```

### `src/hermes/ports/fill_model.py`

```py
from __future__ import annotations
from datetime import datetime
from typing import Protocol, runtime_checkable
from hermes.domain.fees import FeeSchedule
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.trading.fill import Fill, Rejection
from hermes.domain.trading.order import Order


@runtime_checkable
class FillModelPort(Protocol):
    def fill_order(
        self,
        order: Order,
        orderbook: Orderbook,
        clock: datetime,
        fees: FeeSchedule,
    ) -> Fill | Rejection: ...
```

### `src/hermes/ports/exchange.py`

Full Protocol with:

- `place_market_order(pair, side, qty) -> Fill`
- `place_limit_order(pair, side, qty, limit_price) -> Fill | Rejection`
- `cancel_order(order_id) -> None`
- `get_orderbook(pair) -> Orderbook`
- `get_balance(currency) -> Quantity`
- `get_fees(pair) -> FeeSchedule`

All methods raise `InfraError` on exchange failures.

## Test plan

1. Every acceptance criterion above passes.
2. `uv run pytest tests/unit/ports/` exits 0 with all tests green.
3. `bash scripts/dev/pr-preflight.sh --render-body spec-2a-fill-model-port` → all 14 checks green.
4. `uv run mypy --strict src/hermes` exits 0.
5. `uv run lint-imports` exits 0.
6. PR opened via `bash scripts/dev/create-pr.sh`.

## Risks

- **Risk 1**: Existing Binance/Kraken adapter code may reference the old
  empty `ExchangePort` via `isinstance` checks that now fail because no
  concrete adapter implements the new methods — mitigation: this PR only
  defines the port; no adapter claims conformance yet. `isinstance`
  checks with structural protocols only pass when the runtime object
  implements all protocol methods, which no adapter does yet. No
  production code calls `isinstance(adapter, ExchangePort)` today
  (import-linter verifies this is port-only code).
- **Risk 2**: The `clock: datetime` parameter in `fill_order` ties the
  port to a plain `datetime` rather than a `ClockPort` — mitigation:
  this is intentional for MVP1. `datetime` is a value; `ClockPort` is an
  abstraction for the source of that value. The composition root calls
  `ClockPort.now()` and passes the result to `fill_order`. Spec 4a
  (ClockPort) lands independently without requiring a `fill_model.py`
  change.
- **Risk 3**: `FillModelPort` is a structural Protocol — any class with a
  matching `fill_order` method satisfies it, even accidentally —
  mitigation: conformance tests use explicit `assertIsInstance` with a
  minimal implementation class, making the contract visible. The real
  guard is mypy strict + the single-method shape which is hard to match
  accidentally.

## Dependencies on other changes

- No other PRs must land first — this is a pure port PR with no concrete
  implementations.
- Spec 2b (OrderbookWalkFillModel) and Spec 2c (SimulatedExchangeAdapter)
  depend on this PR landing first.
- Spec 4a (ClockPort) and Spec 4b (MarketDataPort) are independent and
  can land in any order relative to this PR.
- Issue #199 (empty ExchangePort stub) is closed by this PR.
