# Change: spec-4b-market-data-port

**SDD change**: `spec-4b-market-data-port`
**Date**: 2026-08-25
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/spec-4b-market-data-port/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Introduces the `MarketDataPort` hexagonal port — a `@runtime_checkable` Protocol defining the
streaming contract any market-data adapter must satisfy. Ships three async stream methods
(`stream_bars`, `stream_trades`, `stream_orderbook_diffs`) and one REST-bootstrap method
(`get_snapshot`), plus three new domain value objects (`Bar`, `Trade`, `OrderbookDiff`) typed
to match. This is a pure port-introduction change: no concrete adapters land here. The port
unblocks spec-4c (Binance/Kraken adapters) and is the prerequisite for the G2 gate in the
swing-MVP1 experiment. Main risk is import-linter purity — the port references domain types,
which is correct; domain types must not reference the port.

## Scope

### In

- `src/hermes/ports/market_data.py` — `MarketDataPort` `@runtime_checkable` Protocol with four
  methods (see Design §Port API)
- `src/hermes/domain/market_data/bar.py` — `Bar` value object: closed-bar OHLCV with interval
  and close-time, aligned to `KlineInterval` for reuse
- `src/hermes/domain/market_data/trade.py` — `Trade` value object: single tick (price, quantity,
  aggressor side, UTC timestamp)
- `src/hermes/domain/market_data/orderbook_diff.py` — `OrderbookDiff` value object: L2 diff
  wrapper (bids + asks as tuples of `OrderbookLevel`, with a `delete` flag per level)
- `tests/unit/ports/test_market_data_port.py` — structural-conformance harness that any
  concrete adapter can validate against (Protocol implementability proof + value object
  invariants)

### Out

- Any concrete adapter (Binance WebSocket, Kraken WebSocket, historical file replay) — spec-4c
- `Consolidator` (bar aggregation from raw trade stream) — spec-4c
- Wiring `klines.py` consumers to the new port — separate mechanical migration, defer to
  spec-4c or a standalone refactor PR
- Streaming connection management / reconnect logic — adapter concerns

## Decisions

### D-1: `@runtime_checkable` Protocol over ABC

- **Decision**: Define `MarketDataPort` as a `typing.Protocol` with `@runtime_checkable`.
- **Alternatives**: (a) ABC with abstractmethods — requires inheritance, couples adapters to
  hermes domain at class-declaration time; (b) plain duck typing — loses mypy checking and
  `isinstance` guard capability.
- **Rationale**: Consistent with `KlinesFetcherPort` and `FeeScheduleProviderPort` (established
  idiom). Protocol + `@runtime_checkable` allows `isinstance(adapter, MarketDataPort)` checks in
  application code for injection validation, and mypy --strict verifies conformance without
  requiring adapters to import from hermes.ports.
- **Cost of reversal**: MEDIUM — application code that uses `isinstance(x, MarketDataPort)` would
  need to be rewritten if switching to ABC.

### D-2: `AsyncIterator` return type for all three stream methods

- **Decision**: Stream methods return `AsyncIterator[T]` (not `AsyncGenerator`, not
  `AsyncIterable`).
- **Alternatives**: (a) `AsyncGenerator[T, None]` — over-constrains implementation (requires
  `yield`); (b) `AsyncIterable[T]` — too loose, no `__anext__` guarantee at static type level;
  (c) `Callable[..., Coroutine]` — misses the streaming intent entirely.
- **Rationale**: `AsyncIterator[T]` is the canonical type for async streams in Python stdlib
  (PEP 525). It matches what `async for item in adapter.stream_bars(...)` expects. Adapters can
  implement with `async def ... -> AsyncIterator[T]: yield ...` or return any
  async-iterable object — maximum implementation flexibility.
- **Cost of reversal**: LOW — changing return type is a pure type annotation change; no runtime
  behavior.

### D-3: `Bar` is a new value object distinct from `Kline`

- **Decision**: Introduce `Bar` in `hermes.domain.market_data.bar` rather than reusing `Kline`.
- **Alternatives**: (a) reuse `Kline` directly — `Kline` carries `Price` (pair-aware) for all
  OHLCV fields and `Quantity` (asset-aware) for volume; forcing real-time bars through `Kline`
  would require constructing `Price` and `Quantity` objects with known pair on every bar event;
  (b) type alias `Bar = Kline` — loses semantic distinction between historical candle and
  live bar event.
- **Rationale**: `Bar` in a live stream carries raw `Decimal` OHLCV values and pair as a flat
  field — adapters do not always know the full asset/currency context at bar-event time, and
  constructing `Price(amount, pair)` per-bar is boilerplate with no domain value at the port
  boundary. `Kline` is the historical-storage shape; `Bar` is the streaming-event shape.
  Keeps domain semantics clear and avoids forcing live adapters through `Price` construction.
- **Cost of reversal**: MEDIUM — if a future decision mandates `Kline` in the stream, Bar usages
  in the application layer would need updating.

### D-4: `TradeSide` enum for aggressor direction

- **Decision**: Add a `TradeSide` enum (`BUY` / `SELL`) in `trade.py` to represent the aggressor
  side of a matched trade.
- **Alternatives**: `bool is_buyer_maker` (Binance convention) — semantically reversed from
  exchange to exchange; using a domain enum removes wire-format leakage.
- **Rationale**: Domain enums named after domain concepts are cleaner than booleans whose
  semantics invert between venues (Binance: `is_buyer_maker`; Kraken: `side` = buy/sell but
  from taker perspective). Adapters translate once at the port boundary.
- **Cost of reversal**: LOW.

### D-5: `OrderbookDiff` as a thin tuple-of-levels wrapper

- **Decision**: `OrderbookDiff` holds `(pair, bids, asks, seq)` where `bids` / `asks` are
  `tuple[OrderbookDiffLevel, ...]` and each level carries `(price, quantity, delete: bool)`.
  A `delete=True` level means the price level is removed from the book; `delete=False` means
  update/insert.
- **Alternatives**: (a) reuse `Orderbook` directly — `Orderbook` is a full snapshot with
  invariants (non-empty, crossed-book check); diff events can legitimately be empty or contain
  deletions, violating those invariants; (b) raw `list[tuple[Decimal, Decimal]]` — loses the
  delete-vs-update semantic.
- **Rationale**: L2 diff events from both Binance (`depthUpdate`) and Kraken (`subscribe book`)
  signal deletion by sending a zero-quantity level. A `delete` flag makes this explicit at the
  domain boundary without leaking zero-quantity sentinel values into the domain.
- **Cost of reversal**: LOW — only adapter code reads this shape.

### D-6: `get_snapshot` as a synchronous-style coroutine in the Protocol

- **Decision**: `get_snapshot` is declared as `async def get_snapshot(self, pair: Pair) ->
  Orderbook` — i.e. a coroutine method in the Protocol.
- **Alternatives**: Synchronous `def get_snapshot(...) -> Orderbook` — forces adapters to block
  or use `anyio.from_thread`, conflicting with async-first adapter design.
- **Rationale**: REST snapshots for L2 bootstrap require an HTTP round-trip. All adapters in
  hermes use async I/O (anyio-on-asyncio policy per existing codebase). Async coroutine aligns
  with the rest of the port.
- **Cost of reversal**: LOW — type annotation change plus adapter updates.

## Requirements

### R-1: Port is importable and mypy-clean

- **A-1.1**: `uv run python -c "from hermes.ports.market_data import MarketDataPort"` exits 0
- **A-1.2**: `uv run mypy --strict src/hermes` exits 0 with 0 errors

### R-2: Domain value objects are importable and mypy-clean

- **A-2.1**: `uv run python -c "from hermes.domain.market_data.bar import Bar, BarInterval"` exits 0
- **A-2.2**: `uv run python -c "from hermes.domain.market_data.trade import Trade, TradeSide"` exits 0
- **A-2.3**: `uv run python -c "from hermes.domain.market_data.orderbook_diff import OrderbookDiff, OrderbookDiffLevel"` exits 0

### R-3: Import-linter domain-is-pure contract holds

- **A-3.1**: `uv run lint-imports` exits 0 (no domain→ports import violation introduced)

### R-4: Structural-conformance harness proves the Protocol is implementable

- **A-4.1**: `uv run pytest tests/unit/ports/test_market_data_port.py` exits 0
- **A-4.2**: Test count ≥ 8 (Protocol implementability + value object invariants)

### R-5: Pre-push gate passes

- **A-5.1**: `bash scripts/dev/pr-preflight.sh` exits 0 — all checks green

## Design

### File tree

```text
src/hermes/domain/market_data/
    __init__.py          (existing — no change needed)
    bar.py               (NEW)
    orderbook.py         (existing — unchanged)
    orderbook_diff.py    (NEW)
    trade.py             (NEW)
    kline.py             (existing — unchanged)
    ticker.py            (existing — unchanged)

src/hermes/ports/
    market_data.py       (NEW)
    klines.py            (existing — unchanged)
    exchange.py          (existing — unchanged)
    fees.py              (existing — unchanged)

tests/unit/ports/
    __init__.py          (NEW — empty)
    test_market_data_port.py  (NEW)
```

### `Bar` value object (`src/hermes/domain/market_data/bar.py`)

```python
from __future__ import annotations
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair


@dataclass(frozen=True, slots=True)
class Bar:
    pair: Pair
    interval: KlineInterval
    open_time: datetime  # UTC-aware, start of bar window
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal  # base-asset quantity
    close_time: datetime  # UTC-aware, end of bar window (exclusive)
```

Invariants in `__post_init__`: UTC-aware `open_time` / `close_time`; `close_time > open_time`;
`high >= max(open, close) >= low > 0`; `volume >= 0`.

### `Trade` value object (`src/hermes/domain/market_data/trade.py`)

```python
from __future__ import annotations
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from hermes.domain.value_objects.pair import Pair


class TradeSide(StrEnum):
    BUY = "buy"  # aggressor is buyer (taker buy)
    SELL = "sell"  # aggressor is seller (taker sell)


@dataclass(frozen=True, slots=True)
class Trade:
    pair: Pair
    price: Decimal  # execution price, > 0
    quantity: Decimal  # base-asset quantity, > 0
    side: TradeSide  # aggressor direction
    trade_time: datetime  # UTC-aware
    trade_id: str  # exchange-assigned unique ID (opaque string)
```

Invariants: UTC-aware `trade_time`; `price > 0`; `quantity > 0`.

### `OrderbookDiff` value object (`src/hermes/domain/market_data/orderbook_diff.py`)

```python
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from hermes.domain.value_objects.pair import Pair


@dataclass(frozen=True, slots=True)
class OrderbookDiffLevel:
    price: Decimal
    quantity: Decimal
    delete: bool  # True = remove this price level from the local book


@dataclass(frozen=True, slots=True)
class OrderbookDiff:
    pair: Pair
    bids: tuple[OrderbookDiffLevel, ...]  # bid-side updates (any order, adapter decides)
    asks: tuple[OrderbookDiffLevel, ...]  # ask-side updates (any order, adapter decides)
    first_update_id: int  # exchange sequence number (Binance: U; Kraken: first seq)
    final_update_id: int  # exchange sequence number (Binance: u; Kraken: last seq)
```

Invariants in `__post_init__`: `price > 0` for all levels (zero-price is never a valid
L2 price; deletion is carried by `delete=True`, not by zero price); `quantity >= 0` (adapter
sets `delete=True` when `quantity=0` from wire, but the level still holds the price);
`final_update_id >= first_update_id`.

### `MarketDataPort` Protocol (`src/hermes/ports/market_data.py`)

```python
from __future__ import annotations
from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.orderbook import Orderbook
from hermes.domain.market_data.orderbook_diff import OrderbookDiff
from hermes.domain.market_data.trade import Trade
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair


@runtime_checkable
class MarketDataPort(Protocol):
    async def stream_bars(self, pair: Pair, interval: KlineInterval) -> AsyncIterator[Bar]: ...
    async def stream_trades(self, pair: Pair) -> AsyncIterator[Trade]: ...
    async def stream_orderbook_diffs(self, pair: Pair) -> AsyncIterator[OrderbookDiff]: ...
    async def get_snapshot(self, pair: Pair) -> Orderbook: ...
```

### Conformance test stub (`tests/unit/ports/test_market_data_port.py`)

A concrete in-memory `FakeMarketDataAdapter` implements the Protocol with async generators
yielding pre-seeded data. Tests verify:

1. `isinstance(fake, MarketDataPort)` is True (Protocol implementability).
2. `isinstance(object(), MarketDataPort)` is False (non-conforming object rejected).
3. `stream_bars` yields `Bar` instances with correct fields.
4. `stream_trades` yields `Trade` instances with correct fields.
5. `stream_orderbook_diffs` yields `OrderbookDiff` instances with correct fields.
6. `get_snapshot` returns an `Orderbook` instance.
7. `Bar.__post_init__` rejects naive datetimes (UTC invariant).
8. `Trade.__post_init__` rejects non-positive price and quantity.
9. `OrderbookDiffLevel.__post_init__` rejects non-positive price.
10. `OrderbookDiff.__post_init__` rejects `final_update_id < first_update_id`.

## Test plan

1. Every acceptance criterion in Requirements passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.
3. All CI jobs green on the scaffolding PR.
4. Code Owner review: API surface matches SA-2/SA-3 streaming semantics.

## Risks

- **Vulture false positive on port Protocol methods**: `vulture` is configured with
  `exclude = ["src/hermes/ports/"]` in `pyproject.toml` — this existing exclusion covers
  the new `market_data.py` file without any extra config change.
- **`AsyncIterator` Protocol structural check at runtime**: Python's `runtime_checkable`
  only checks for `__aiter__` / `__anext__` attribute presence; it does NOT check
  type parameters. This means `isinstance(x, MarketDataPort)` confirms the object has
  the four methods but not their signatures. This is a known Python limitation —
  mypy --strict at static analysis time provides the full check.
- **Sequence number semantics differ between venues**: `OrderbookDiff.first_update_id` /
  `final_update_id` map to Binance's `U` / `u` fields and Kraken's sequence numbers.
  The invariant `final >= first` is safe across both; adapters are responsible for
  the venue-specific gap-detection logic. This is an adapter concern deferred to spec-4c.

## Dependencies on other changes

- `src/hermes/domain/market_data/orderbook.py` (existing, validated) — reused by
  `get_snapshot` return type and referenced in `OrderbookDiff` design.
- `src/hermes/domain/market_data/kline.py` `KlineInterval` — reused by `Bar.interval`
  (avoids duplicating the interval enum).
- No new external dependencies required.

## Tasks

- [x] T-1: Create `openspec/archive/spec-4b-market-data-port/change.md` + `issue.md`
- [x] T-2: Implement `Bar` + `TradeSide` + `Trade` value objects in `src/hermes/domain/market_data/`
- [x] T-3: Implement `OrderbookDiff` + `OrderbookDiffLevel` in `src/hermes/domain/market_data/orderbook_diff.py`
- [x] T-4: Implement `MarketDataPort` Protocol in `src/hermes/ports/market_data.py`
- [x] T-5: Create `tests/unit/ports/__init__.py` + `tests/unit/ports/test_market_data_port.py`
- [x] T-6: Create `changelog.d/wave-spec-4b-market-data-port.md` fragment
- [x] T-7: Run `bash scripts/dev/pr-preflight.sh` → all green; open PR
