# SA-2 Deep Code Reading — Fill Engines + Wire-Up Patterns

**Date**: 2026-08-24
**Author**: SA-2 sub-agent
**Engram topic key**: `research/paper-trading-sa2-deep-code`
**Status**: FINAL

---

## SA-2 Deep Code Reading: Fill Engines & Wire-Up Patterns

## Hermes Paper-Trading Engine Research

---

## 1. SYSTEM-BY-SYSTEM DEEP ANALYSIS

---

### 1.1 NautilusTrader (Rust core, v2026)

**Repository state**: Fully migrated to Rust. Python backtest module is a thin stub (`python/nautilus_trader/backtest/__init__.py` — only `__init__.py`). All logic lives in `crates/`.

#### FillModel Abstraction Shape

Source: `crates/execution/src/models/fill.rs`

```rust
pub trait FillModel {
    fn is_limit_filled(&mut self) -> anyhow::Result<bool>;
    fn is_slipped(&mut self) -> anyhow::Result<bool>;
    fn fill_limit_inside_spread(&self) -> anyhow::Result<bool> { Ok(false) }
    fn get_orderbook_for_fill_simulation(
        &mut self,
        instrument: &InstrumentAny,
        order: &OrderAny,
        best_bid: Price,
        best_ask: Price,
    ) -> anyhow::Result<Option<OrderBook>>;
}
```

Concrete implementations (all in `fill.rs`):

- `DefaultFillModel` — probabilistic: `prob_fill_on_limit` + `prob_slippage`, returns `None` from `get_orderbook_for_fill_simulation` (uses matching engine default)
- `BestPriceFillModel` — fills at best bid/ask with unlimited synthetic liquidity; `fill_limit_inside_spread = true`
- `OneTickSlippageFillModel` — always slips by one tick; builds synthetic L2 book with ±1 tick at bid/ask
- `RandomFillModel` — 50/50 best price vs. one tick slippage

The `get_orderbook_for_fill_simulation` hook is the KEY architectural innovation: it lets custom fill models inject a synthetic L2 book that the matching engine uses for fill simulation. This is the post-issue-#2194 solution.

Internal state: `ProbabilisticFillState { prob_fill_on_limit: f64, prob_slippage: f64, rng: StdRng }`. Seeded RNG for determinism.

**FeeModel** (`crates/execution/src/models/fee.rs`):

```rust
pub trait FeeModel {
    fn get_commission(&self, order, fill_qty, fill_px, instrument) -> Money;
    fn get_commission_with_context(&self, ..., underlying_px: Option<Price>) -> Money;
}
```

Variants: `FixedFeeModel`, `MakerTakerFeeModel`, `PerContractFeeModel`, `ProbabilityPriceFeeModel`, `CappedOptionFeeModel`, `TieredNotionalOptionFeeModel`. MakerTaker is the relevant one for crypto.

#### Wire-Up: Backtest / Sandbox / Live

Source: `crates/backtest/src/exchange.rs` + `engine.rs`

All three modes share `NautilusKernel`. The `SimulatedExchange` struct holds `FillModelHandle`, `FeeModelHandle`, `LatencyModel` as Rc<RefCell<dyn Trait>> — injectable at construction. The `BacktestEngine` creates a `NautilusKernel` directly:

```rust
let kernel = NautilusKernel::new("BacktestEngine".to_string(), config.clone())?;
```

In Sandbox mode, the same `SimulatedExchange` is wired to a live data feed instead of historical replay. In Live mode, the exchange is replaced by a real broker adapter. The strategy code never changes — only the `exec_client` injected into the kernel differs.

`SimulatedExchange.set_fill_model()` propagates the model to all `OrderMatchingEngine` instances per instrument — showing the pattern: one fill model per venue, applied per-instrument.

`InflightCommand` with a min-heap (`BinaryHeap`) models network latency: commands are delayed by `latency_model` and processed in timestamp order. This is the latency simulation mechanism.

`bar_execution: bool` and `trade_execution: bool` flags control whether bars or trade ticks trigger matching. `bar_adaptive_high_low_ordering` controls whether within a bar the engine tries high-then-low vs. low-then-high based on open price proximity (partial fix for intrabar ambiguity problem).

`queue_position: bool` field exists on `SimulatedExchange` — signals that queue-position-aware logic is requested but the FillModel controls the actual simulation.

#### Time Model

Event-driven with nanosecond timestamps. Three data paths:

1. `process_quote_tick` — triggers matching engine per quote
2. `process_trade_tick` — triggers matching engine per trade
3. `process_bar` — triggers matching engine per bar close (bar_execution mode)

The consolidator pattern (bar-close at strategy layer) is implemented externally by the user registering a consolidator actor. The `SimulatedExchange` receives whatever data type is fed in.

#### Queue Position

Config flag `queue_position: bool` on `SimulatedVenueConfig`. No concrete queue-position simulation in built-in models — but `get_orderbook_for_fill_simulation` is the hook to implement it. Issue #2194 (now CLOSED) proposed and landed this hook.

#### Partial Fills

Not natively modeled in built-in `FillModel` implementations. The synthetic orderbook returned by `get_orderbook_for_fill_simulation` can be constructed with finite depth, enabling partial fill simulation via the matching engine's orderbook-walk logic.

#### Slippage

Via synthetic orderbook: `OneTickSlippageFillModel` builds a book with ±1 tick. Volume-based slippage requires a custom `FillModel` implementation returning a depth-limited synthetic book.

#### Open Issues (searched GitHub, Aug 2026)

Recent open issues found (not fill-specific by label — fill issues are interleaved):

1. "Repair Bybit SPOT position reports from wallet balances" (open, Aug 2026)
2. "Streaming Multiple Data streams at once" (open)
3. No open fill-specific regression issues found — the issue #2194 enhancement was closed/implemented.

---

### 1.2 QuantConnect LEAN

**Repository**: `github.com/QuantConnect/Lean`, C# core.

#### FillModel Abstraction Shape

Source: `Common/Orders/Fills/IFillModel.cs` + `FillModel.cs` + `FillModelParameters.cs`

```csharp
public interface IFillModel {
    Fill Fill(FillModelParameters parameters);
}

public class FillModelParameters {
    Security Security { get; }
    Order Order { get; }
    ISubscriptionDataConfigProvider ConfigProvider { get; }
    TimeSpan StalePriceTimeSpan { get; }
    Dictionary<Order, Security> SecuritiesForOrders { get; }
    Action<Order> OnOrderUpdated { get; }
}
```

`FillModel` base dispatches by `OrderType` to virtual methods:

- `MarketFill(Security, MarketOrder)` → `InternalMarketFill`
- `LimitFill(Security, LimitOrder)`
- `StopMarketFill(Security, StopMarketOrder)`
- `TrailingStopFill(Security, TrailingStopOrder)`
- `StopLimitFill(Security, StopLimitOrder)`
- `MarketOnOpenFill`, `MarketOnCloseFill`
- `ComboMarketFill`, `ComboLimitFill`, `ComboLegLimitFill` (multi-leg orders)

`InternalMarketFill` (line 282) fills at `GetMarketFillPrice(asset, order, prices)` + slippage from `asset.SlippageModel.GetSlippageApproximation(asset, order)`. The slippage is SEPARATE from the fill model — composed via the `Security` object.

Fill price uses `GetPrices(asset, direction)` which returns `{Open, High, Low, Close, Current, EndTime}`. Stale-price guard: if `pricesEndTime + StalePriceTimeSpan < order.Time`, waits for fresh data.

`StopMarketFill` (line 347): uses `prices.Low < order.StopPrice` for sell stops, fills at `Min(stopPrice, current - slip)` — worst-case assumption.

**Critical**: `fill.FillQuantity = quantity` — always assumes complete fill. No partial fill logic in base `FillModel`.

#### Slippage Model

Source: `Common/Orders/Slippage/VolumeShareSlippageModel.cs`

```csharp
slippage = volumeShare^2 * priceImpact * lastPrice
// where volumeShare = Min(orderQty / barVolume, volumeLimit)
// default: volumeLimit=0.025, priceImpact=0.1
```

Issue #4897 (STILL OPEN as of Aug 2026): requests `FixedSlippageModel` and `VolumeShareSlippageModel` for crypto. The VolumeShareSlippageModel exists for equities but not crypto — crypto returns zero slippage on zero-volume data (warning issued, returns 0).

#### Wire-Up: Backtest / Paper / Live

LEAN has a unified `AlgorithmManager` that calls `FillModel.Fill()` in all modes. The switch between modes is in the brokerage adapter:

- Backtest: `BacktestingBrokerage` → calls `FillModel` directly on each data event
- Paper: `PaperBrokerModel` → uses real data feed + simulated fills via same `FillModel`
- Live: real broker API — `FillModel` NOT called (real fills come from exchange)

This is the SAME-CODE-PATH pattern — strategy code is identical; only the brokerage adapter changes.

#### Consolidator Pattern

Source: `Common/Data/Consolidators/ConsolidatorBase.cs`

```csharp
public abstract class ConsolidatorBase : WindowBase<IBaseData>, IDataConsolidator {
    public abstract void Update(IBaseData data);
    public abstract void Scan(DateTime currentLocalTime);
    protected virtual void OnDataConsolidated(IBaseData consolidated) { ... }
}
```

Consolidators receive tick/quote/trade data and emit bars. Strategy subscribes to bar events from consolidators. This is the canonical consolidator pattern that hermes should emulate — strategy layer sees bar-close events; fill layer sees ticks.

#### Order Types

MARKET, LIMIT, STOP_MARKET, TRAILING_STOP, STOP_LIMIT, MARKET_ON_OPEN, MARKET_ON_CLOSE, COMBO_MARKET, COMBO_LIMIT, COMBO_LEG_LIMIT.

#### Multi-Strategy Capital

`PortfolioConstructionModel` outputs target weights; `AlphaModel` outputs signals from N strategies. Capital is managed at the portfolio level, not per-strategy.

#### Open Issues

Issue #4897 (OPEN, created 2020-10-29): "Implement FixedSlippageModel and VolumeShareSlippageModel" — still open. Crypto slippage returns zero.

---

### 1.3 Freqtrade

**Repository**: `github.com/freqtrade/freqtrade`, Python.

#### FillModel Abstraction Shape

NO formal `FillModel` abstraction. Fill logic is embedded in:

1. `freqtrade/exchange/exchange.py` — `create_dry_run_order()`, `check_dry_limit_order_filled()`, `get_dry_market_fill_price()`
2. `freqtrade/optimize/backtesting.py` — `_get_close_rate()`, `_get_order_filled()`, `_exit_trade()`

**`create_dry_run_order`** (line 1159): Builds a dict order. For market orders: `slippage = 0.05` hardcoded (5%!), `worst_rate = rate * (1 + 0.05)`, then `get_dry_market_fill_price()` walks the live orderbook to find average. For limit orders: if price crosses spread by > 1%, converts to market. Calls `check_dry_limit_order_filled` immediately.

**`check_dry_limit_order_filled`** (line 1332): Checks `_dry_is_price_crossed()` — if limit price is crossed in current orderbook, marks filled immediately at that price. No queue position, no partial fill. Binary fill/no-fill.

**CRITICAL divergence**: In backtesting, `_get_order_filled(rate, row)` checks `rate` against the OHLCV row — if rate is within candle range, it's filled. In dry-run, `check_dry_limit_order_filled` checks against LIVE orderbook at polling time. These are fundamentally different mechanisms — timing-sensitive and data-dependent.

**Backtesting fill price** (`_exit_trade`, line 932): `close_rate = price_to_precision(close_rate, ...)`. Filled at calculated `close_rate` — either stop, ROI target, or signal close price within OHLCV bounds.

#### Wire-Up: The Divergence Problem

Issues #12662 (Dec 2025) and #12675 (Dec 2025) both report: trades appearing in backtesting at 12:24/12:25 not triggering in dry-run. Root cause visible in code:

1. **Backtest**: processes complete historical OHLCV bars. Signal at bar close T, order executed at bar open T+1 (or same bar depending on `process_only_new_candles`).
2. **Dry-run**: polls every `process_throttle_secs` (default 5s). New candle data may arrive between polls — a signal that would have triggered at candle close in backtest may be missed if the next poll sees the NEXT candle already open.
3. **Limit order fill**: Backtest uses price range check; dry-run uses real orderbook crossing check.

This is NOT a bug that can be fixed easily — it's a fundamental architectural divergence. Freqtrade's `if self._config["dry_run"]:` branches in exchange.py (found at lines 255, 1464, 1615, 1716, 1790, 1914, 1953, 1987, 2009, 2426, 2490, 3802, 3853, 4024, 4059) show that dry-run has 15+ divergent code paths vs. live.

#### Time Model

OHLCV candle-driven. `process_only_new_candles: true` (default). Signal on bar close → entry at next bar open. No tick level. No orderbook for backtesting fill.

#### Slippage

Backtesting: zero slippage (fills at calculated rate within candle bounds). Dry-run market: hardcoded 5% worst-case + orderbook walk average. This is a MAJOR divergence.

#### Fee Model

`set_fee()` in `Backtesting.__init__`: reads from exchange config or uses hardcoded default. In dry-run: `add_dry_order_fee(pair, order, "taker"/"maker")` — maker for limit fills, taker for market. Reads from CCXT exchange fee structure (fetched live).

#### Queue Position / Partial Fills

None. `check_dry_limit_order_filled`: binary fill. `create_dry_run_order`: `"filled": _amount` for market, `"filled": 0` for limit until check.

#### Open Issues

Issues #12662, #12675 (both closed Dec 2025 — resolved by documentation, not code fix). Root cause remains architectural.

---

### 1.4 hftbacktest (Rust/Python)

**Repository**: `github.com/nkaz001/hftbacktest`

#### FillModel / QueueModel Abstraction

Source: `hftbacktest/src/backtest/models/queue.rs`, `fee.rs`

**QueueModel trait** (the core fill abstraction):

```rust
pub trait QueueModel<MD: MarketDepth> {
    fn new_order(&self, order: &mut Order, depth: &MD);   // called on order submission
    fn trade(&self, order: &mut Order, qty: f64, depth: &MD);  // called on trade at same price
    fn depth(&self, order: &mut Order, prev_qty: f64, new_qty: f64, depth: &MD); // on book change
    fn is_filled(&self, order: &mut Order, depth: &MD) -> f64;  // returns filled qty
}
```

Two implementations:

1. **`RiskAdverseQueueModel`**: stores `front_q_qty` = qty ahead in queue. `trade()` decrements it. `depth()` takes `min(front, new_qty)`. `is_filled()` returns `(-front_q_qty / lot_size).round()` as executable quantity. Conservative — only advances on trades.

2. **`ProbQueueModel<P: Probability>`**: stores `{front_q_qty, cum_trade_qty}`. `depth()` implements probabilistic advancement: `prob = P(front, back)`, `est_front = front - (1-prob)*chg + max(0, back - prob*chg)`. Two probability functions: `PowerProbQueueFunc(n)` and `LogProbQueueFunc`. This is the Cont-Kukanov model adaptation.

**FeeModel** (`fee.rs`):

```rust
pub trait FeeModel {
    fn amount(&self, order: &Order, amount: f64) -> f64;
}
```

`TradingValueFeeModel<CommonFees>` — maker/taker differentiation. `TradingValueFeeModel<DirectionalFees>` — adds buyer/seller directional fees. Clean and simple.

#### Exchange Models

**`PartialFillExchange`** (`proc/partialfillexchange.rs`):

- `check_if_sell_filled`: if `order.price_tick < price_tick` → full fill. If `== price_tick` → calls `queue_model.trade()` + `is_filled()` for partial quantity.
- Partial fill condition: "your order is at the front of the queue AND your order price == sell trade price"
- `exec_qty = min(filled_qty, leaves_qty)` — true partial fill support

**`NoPartialFillExchange`**: simpler, binary fill.

**Critical assumption** (from README): "orders are small enough not to make any market impact." The synthetic book is not updated when the simulated order consumes it — a 10% size order gets the same book as a 0.1% order.

#### Wire-Up

Backtest-only system. No paper/live mode. Data replay via `HftBacktest<...>` struct with a `DataIterator`. There IS no shadow-mode — the codebase is purely historical replay. Python bindings via `py-hftbacktest` crate.

#### Time Model

Tick-level, nanosecond. Full L2/L3 LOB reconstruction. Event types: `EXCH_BID_DEPTH_EVENT`, `EXCH_ASK_DEPTH_EVENT`, `EXCH_BUY_TRADE_EVENT`, `EXCH_SELL_TRADE_EVENT`, `EXCH_BID_DEPTH_SNAPSHOT_EVENT`, etc. Each event has exchange timestamp + local timestamp for latency simulation.

---

### 1.5 Zipline (Quantopian, canonical failure case)

**Repository**: `github.com/quantopian/zipline` (archived)

#### FillModel / SlippageModel Abstraction

Source: `zipline/finance/slippage.py`

```python
class SlippageModel(metaclass=FinancialModelMeta):
    def process_order(self, data, order) -> (execution_price, execution_volume):
        """
        data: BarData (gives data.current(asset, 'close'), 'volume', etc.)
        order: Order (has order.amount, order.filled, order.limit, order.direction)
        Returns: (price: float, volume: int)
        Raises: LiquidityExceeded
        """
```

`SlippageModel.simulate()` iterates orders, calls `process_order`, creates transactions. `volume_for_bar` tracks filled shares across multiple orders in same bar.

**`VolumeShareSlippage.process_order`** (the bug source):

```python
price = data.current(order.asset, "close")  # BUG: uses CLOSE of CURRENT bar
max_volume = self.volume_limit * volume
remaining_volume = max_volume - self.volume_for_bar
volume_to_transact = min(order.open_amount, remaining_volume)
price_impact = price * self.price_impact * (volume_share**2)
```

Issue #2011 (STILL OPEN, created 2017-11-10): "why do we use 'close' price for orders in VolumeShareSlippage.process_order". The call chain is: bar arrives → `process_orders()` → `VolumeShareSlippage.process_order` → uses CURRENT bar's `close` price. Since orders from bar T are processed when bar T+1 data arrives (from `tradesimulation.py`), this means fills at bar T+1 CLOSE — not T+1 OPEN. Pure look-ahead on daily bars.

**`NoSlippage.process_order`**: returns `data.current(order.asset, 'close'), order.amount` — fills at close, full quantity, zero slippage. Same timing bug.

#### Wire-Up

No live trading in original Zipline. Backtest-only. The `simulate()` method on the slippage model is called from `blotter.get_transactions()` which is called from `tradesimulation.py:L121` (processes orders BEFORE `handle_data` which places new orders at L137). So orders placed at bar T's `handle_data` are processed at bar T+1 — correct. But the fill price uses bar T+1's CLOSE, not OPEN.

#### Time Model

Event-driven bar-close. `tradesimulation.py` processes bar by bar. No tick level.

---

### 1.6 VectorBT

**Repository**: `github.com/polakowo/vectorbt`

#### FillModel / Capital Accounting

Source: `vectorbt/portfolio/nb.py`

No `FillModel` class. Fill logic is inline Numba-JIT functions:

```python
@njit
def buy_nb(exec_state, size, price, fees=0.0, fixed_fees=0.0, slippage=0.0, ...):
    adj_price = price * (1 + slippage)
    # cash accounting: req_cash = adj_size * adj_price; fees = req_cash * fees + fixed_fees
    # Returns: (new_exec_state, OrderResult)
```

`OrderResult = namedtuple(size, price, fees, side, status, status_info)`.

`ExecuteOrderState = namedtuple(cash, position, debt, free_cash)`.

**Multi-strategy capital**: achieved via array dimensions. `from_signals()` accepts 2D arrays `(n_bars, n_cols)` where each column is a strategy/asset. The Numba loop processes each column independently with its own `exec_state`. No shared capital — each column has its own cash balance.

**Slippage**: `adj_price = price * (1 ± slippage)` — constant percentage, applied to price before fee calculation.

**Partial fills**: supported for size-constrained fills (insufficient cash → fills max affordable quantity). No orderbook-walk partial fills.

**Fee**: `fees` (% of notional) + `fixed_fees` (flat per trade). No maker/taker differentiation.

#### Wire-Up

No live trading in OSS version. Array broadcasting processes ALL bars simultaneously — not event-driven. Lookahead risk is explicit — discipline required to shift signals by 1 bar.

---

## 2. COMPARISON MATRIX

| Dimension | NautilusTrader | LEAN | Freqtrade | hftbacktest | Zipline | VectorBT |
| ----------- | --------------- | ------ | ----------- | ------------- | --------- | ---------- |
| **FillModel abstraction** | `FillModel` trait (Rust) — 4 methods; `get_orderbook_for_fill_simulation` hook | `IFillModel.Fill(params)` → virtual per-type methods | No abstraction — embedded in `exchange.py` | `QueueModel` trait — `new_order/trade/depth/is_filled` | `SlippageModel.process_order(data, order)` | Numba JIT functions (`buy_nb`, `sell_nb`) |
| **Fill fn receives** | `&mut self` + instrument + order + best_bid + best_ask | `FillModelParameters{Security, Order, ConfigProvider, StalePriceTimeSpan}` | CCXT order dict + orderbook dict | `&mut Order` + `qty: f64` + `&MD` (market depth) | `BarData` + `Order` | `exec_state` + `size/price/fees/slippage` |
| **Fill fn returns** | `Option<OrderBook>` (synthetic) or probabilistic bool | `Fill{List<OrderEvent>}` with price + qty + fees | Mutated CcxtOrder dict | `f64` (executable qty) | `(execution_price, execution_volume)` | `(ExecuteOrderState, OrderResult)` |
| **Fill state held** | `ProbabilisticFillState{rng, prob_*}` per model | Stateless (slippage reads from `Security`) | `_dry_run_open_orders: dict` in Exchange | `QueuePos{front_q_qty, cum_trade_qty}` per order | `volume_for_bar: int` | Stateless (array broadcasting) |
| **Time model** | Event-driven, nanosecond; bar-close via consolidator pattern | Event-driven, tick→minute→hour→day via consolidators | OHLCV candle-driven (5m minimum) | Tick-level, nanosecond, L2 LOB replay | Bar-close event-driven | Vectorized (all bars simultaneous) |
| **Backtest/paper wire-up** | SAME kernel (`NautilusKernel`), different data source + exec_client | SAME `FillModel`, different brokerage adapter | DIVERGENT — 15+ `if dry_run:` branches | Backtest only | Backtest only | Backtest only |
| **Queue position** | Flag `queue_position: bool`; no built-in simulation — hook available | None | None | `RiskAdverseQueueModel` + `ProbQueueModel` | None | None |
| **Partial fills** | Via synthetic orderbook hook (finite depth) | None (always full fill) | None (binary) | `PartialFillExchange` — FIFO queue + trade qty | Via `LiquidityExceeded` exception (cuts volume) | Size-constrained (cash limit) only |
| **Slippage model** | Synthetic orderbook (0-tick, 1-tick, or custom) | Separate `ISlippageModel` — VolumeShare^2, Constant, MarketImpact | Backtesting: zero; dry-run: 5% worst-case + OB walk | None (queue position IS the slippage model) | VolumeShare^2 * priceImpact (at wrong price) | Constant % |
| **Fee model** | `FeeModel` trait: Fixed, MakerTaker, PerContract, TieredNotional | Separate from FillModel; applied after fill | `add_dry_order_fee` — maker/taker from CCXT | `FeeModel` trait: TradingValueFeeModel, DirectionalFees | Not modeled in SlippageModel | % of notional + fixed fee |
| **Multi-strategy capital** | Shared Portfolio across strategies; actor isolation | `PortfolioConstructionModel` — weight-based allocation | Manual per-strategy capital (`capital_per_trade`) | Single strategy focus | None (per-column independent cash) | Per-column independent |
| **Order types** | MARKET, LIMIT, STOP, STOP_LIMIT, TRAILING, GTD, GTC | MARKET, LIMIT, STOP_MARKET, TRAILING, STOP_LIMIT, MOO, MOC, COMBO | MARKET, LIMIT, STOP (exchange-native) | LIMIT only (+ IOC, FOK, GTX) | MARKET, LIMIT, STOP, STOP_LIMIT | MARKET, LIMIT (abstract), STOP (by signal) |
| **Known bugs** | Issue #2194 (resolved — hook added); no open fill bugs | Issue #4897 (OPEN — crypto zero slippage) | Issues #12662, #12675 (dry-run divergence, "resolved" by docs) | Market impact assumption (documented not fixed) | Issue #2011 (OPEN since 2017 — fill at close not open) | Lookahead if signals not shifted |

---

## 3. RECOMMENDED FillModelPort FOR HERMES

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Protocol
from decimal import Decimal


@dataclass
class OrderBookSnapshot:
    """Synthetic or real L2 snapshot for fill simulation."""

    bids: list[tuple[Decimal, Decimal]]  # [(price, qty), ...] sorted desc
    asks: list[tuple[Decimal, Decimal]]  # [(price, qty), ...] sorted asc


@dataclass
class FillResult:
    """Returned by FillModelPort.process_fill()."""

    filled_qty: Decimal  # 0 = unfilled; < order_qty = partial
    fill_price: Decimal  # VWAP if partial walk, else single price
    commission: Decimal  # in quote currency
    is_maker: bool  # determines fee tier
    slippage_ticks: int  # for diagnostics


class MarketDataContext:
    """What the fill model receives — inspired by NautilusTrader + LEAN."""

    instrument_id: str
    strategy_id: str  # hermes: bucket-aware
    order: "Order"  # order type, side, qty, limit_px
    best_bid: Decimal
    best_ask: Decimal
    orderbook: Optional[OrderBookSnapshot]  # None if only bar data available
    bar_ohlcv: Optional[tuple]  # (open, high, low, close, volume) if bar-level
    ts_event: int  # nanosecond timestamp
    ts_now: int  # wall-clock or simulated clock


class FillModelPort(ABC):
    """
    Core fill simulation abstraction for hermes.

    Inspired by:
    - NautilusTrader: get_orderbook_for_fill_simulation hook (synthetic book)
    - hftbacktest: QueueModel.is_filled() returning partial qty
    - LEAN: separate slippage model composition
    - Zipline: process_order(data, order) simplicity

    The key difference from all surveyed systems:
    - Receives strategy_id for bucket-aware capital tracking
    - Returns FillResult (not mutates order) for immutability
    - fee_model is injected, not embedded, for tier-aware crypto fees
    """

    @abstractmethod
    def process_fill(self, ctx: MarketDataContext) -> FillResult:
        """
        Simulate fill given market context.
        Called by SimulatedExchangePort for every order at each data event.
        """
        ...

    @abstractmethod
    def reset(self) -> None:
        """Reset per-bar state (e.g., volume_for_bar tracking)."""
        ...


class FeeModelPort(ABC):
    """Injected into FillModelPort at construction."""

    @abstractmethod
    def commission(
        self,
        instrument_id: str,
        fill_qty: Decimal,
        fill_px: Decimal,
        is_maker: bool,
        strategy_id: str,  # for tier-aware per-strategy fee tracking
    ) -> Decimal: ...


# --- Concrete MVP1 Implementations ---


class OrderbookWalkFillModel(FillModelPort):
    """
    Walks the synthetic or real orderbook to determine fill price + qty.
    Inspired by: NautilusTrader BestPriceFillModel + OneTickSlippageFillModel

    For MVP1: uses real orderbook snapshot from WebSocket feed (shadow mode)
    or synthetic from bar OHLCV (backtesting).
    """

    def __init__(self, fee_model: FeeModelPort, max_volume_pct: float = 0.10):
        self.fee_model = fee_model
        self.max_volume_pct = max_volume_pct  # cap at 10% of bar volume

    def process_fill(self, ctx: MarketDataContext) -> FillResult:
        if ctx.orderbook:
            return self._walk_real_book(ctx)
        else:
            return self._estimate_from_bar(ctx)  # OHLCV fallback

    def _walk_real_book(self, ctx) -> FillResult:
        # Walk asks (for buy) or bids (for sell) to find VWAP fill
        levels = ctx.orderbook.asks if ctx.order.is_buy else ctx.orderbook.bids
        remaining = ctx.order.qty
        total_cost = Decimal(0)
        filled = Decimal(0)
        for price, qty in levels:
            fill_at = min(remaining, qty)
            total_cost += fill_at * price
            filled += fill_at
            remaining -= fill_at
            if remaining <= 0:
                break
        if filled == 0:
            return FillResult(0, Decimal(0), Decimal(0), False, 0)
        vwap = total_cost / filled
        is_maker = False  # market order always taker
        commission = self.fee_model.commission(
            ctx.instrument_id, filled, vwap, is_maker, ctx.strategy_id
        )
        slippage_ticks = (
            round((vwap - ctx.best_ask) / ctx.order.tick_size) if ctx.order.is_buy else 0
        )
        return FillResult(filled, vwap, commission, is_maker, slippage_ticks)


class MakerTakerFeeModel(FeeModelPort):
    """
    Tier-aware maker/taker fees.
    Inspired by: NautilusTrader MakerTakerFeeModel, hftbacktest TradingValueFeeModel.
    """

    def __init__(self, maker_rate: Decimal, taker_rate: Decimal):
        self.maker = maker_rate
        self.taker = taker_rate

    def commission(self, instrument_id, fill_qty, fill_px, is_maker, strategy_id) -> Decimal:
        rate = self.maker if is_maker else self.taker
        return fill_qty * fill_px * rate
```

**Justification for each design choice**:

- `process_fill(ctx) -> FillResult` — inspired by LEAN's explicit parameter struct; avoids Zipline's mutable-state anti-pattern
- `MarketDataContext.orderbook: Optional[...]` — inspired by NautilusTrader's `get_orderbook_for_fill_simulation` hook; enables same code for bar-only backtest and orderbook-rich shadow mode
- `FillResult.filled_qty` can be < order qty — inspired by hftbacktest's `is_filled() -> f64`; correct partial fill semantics
- `strategy_id` on both `MarketDataContext` and `FeeModelPort.commission` — hermes-specific bucket-awareness; no surveyed system does this
- `FeeModelPort` injected separately — inspired by LEAN's composition pattern; enables tier-aware fees without touching fill logic
- `reset()` method — inspired by Zipline's `volume_for_bar` that must reset per bar

---

## 4. ANTI-PATTERNS TO AVOID

### AP-1: Fill at Wrong Bar's Price (Zipline issue #2011)

**What**: `VolumeShareSlippage.process_order` uses `data.current(asset, 'close')` — the CLOSE of the bar being processed, which is bar T+1 since orders are processed at start of T+1 loop. Fills at T+1 CLOSE not T+1 OPEN.
**Hermes**: ALWAYS fill at `bar.open` of the NEXT bar for limit/market orders triggered at bar close. Never fill at `bar.close` of the current bar.
**Code cite**: `zipline/finance/slippage.py` lines ~270-290.

### AP-2: Divergent Code Paths for Backtest vs. Shadow (Freqtrade)

**What**: 15+ `if self._config["dry_run"]:` branches in `exchange.py`. Different fill timing, different slippage (0% backtest vs 5% dry-run), different order state management.
**Hermes**: ONE fill code path. The ONLY difference is `MarketDataContext.orderbook` being `None` (backtest OHLCV) vs. a real snapshot (shadow). The `OrderbookWalkFillModel` handles both via `_walk_real_book` vs. `_estimate_from_bar`.
**Code cite**: Freqtrade `exchange.py` lines 255, 1464, 1615, 1716, 1790 etc.

### AP-3: Assume All Limit Orders Fill (Freqtrade, LEAN base)

**What**: Freqtrade `check_dry_limit_order_filled` marks limit orders as `filled: amount` the moment price crosses. LEAN `FillModel.InternalMarketFill` always sets `fill.FillQuantity = quantity`. No partial fill consideration.
**Hermes**: Shadow mode should check ACTUAL available qty at price level in orderbook. Backtest should use volume-fraction cap.
**Code cite**: LEAN `FillModel.cs` line 335; Freqtrade `exchange.py` lines 1383-1388.

### AP-4: Hardcoded Slippage Constants (Freqtrade dry-run)

**What**: `slippage = 0.05` hardcoded in `create_dry_run_order` line 1214. 5% worst-case applied to all market orders regardless of instrument or size.
**Hermes**: Slippage emerges from orderbook walk — no constant. If no orderbook, use volume-fraction estimate from bar data.
**Code cite**: Freqtrade `exchange.py` line 1214.

### AP-5: Market Impact Ignored for Large Orders (hftbacktest)

**What**: Documented assumption: "orders are small enough not to make any market impact." Synthetic book is not depleted by simulated order's own consumption.
**Hermes**: For MVP1 (swing trading, typical order < 1% of book depth), this is acceptable. For MVP2, implement book-depletion simulation.
**Source**: hftbacktest README + `partialfillexchange.rs` docstring.

### AP-6: Slippage Model Separate from Fill Model Without Coordination (LEAN #4897)

**What**: LEAN's `asset.SlippageModel.GetSlippageApproximation()` is called AFTER fill price is set, as an additive adjustment. For crypto, if volume is zero, returns 0 — silently. No coordination between fill qty and slippage.
**Hermes**: Slippage IS the orderbook walk result — not a post-hoc adjustment. Embed it in `FillResult.fill_price` directly.

### AP-7: RNG Without Seed in Probabilistic Fill Models

**What**: NautilusTrader `ProbabilisticFillState` defaults to unseeded RNG if `random_seed=None`. Non-deterministic backtests.
**Hermes**: Always require deterministic seed for probabilistic models. NautilusTrader solved this with `StdRng::seed_from_u64(seed)` — copy this pattern.
**Code cite**: NautilusTrader `fill.rs` lines 184-188.

---

## 5. WIRE-UP RECOMMENDATION: SAME-CODE-PATH GUARANTEE

The core insight from surveyed systems: **LEAN achieves best same-code-path via brokerage adapter injection; NautilusTrader achieves it via NautilusKernel injection; Freqtrade FAILS because it branches in the exchange layer**.

**Hermes pattern** (inspired by NautilusTrader + LEAN):

```text
MarketDataPort (interface)
    ↓
    ┌─── BacktestDataAdapter (reads from OHLCV DB)
    └─── ShadowDataAdapter   (reads from Binance/Kraken WS)

Both emit: MarketEvent{instrument_id, ts, orderbook?, bar_ohlcv?}

SimulatedExchangePort (interface)
    ↓
    FillModelPort.process_fill(MarketDataContext)
    ├── Uses orderbook if present (shadow mode: real L2 from WS)
    └── Uses OHLCV fallback (backtest mode: volume-fraction + bar OHLCV)

Strategy receives: BarCloseEvent (from consolidator)
Strategy emits: OrderRequest{instrument_id, strategy_id, side, type, qty, limit_px?}

OrderRequest → SimulatedExchangePort
    → FillModelPort.process_fill(ctx)
    → FillResult → position/balance update (bucket-aware via strategy_id)
```

**The guarantee**: `FillModelPort.process_fill()` is called identically in both modes. The ONLY difference is `ctx.orderbook` is `Some(real_snapshot)` in shadow and `None` in backtest. No `if shadow_mode:` branches anywhere in the fill logic.

---

## 6. MVP1 FILL MODEL SCOPE RECOMMENDATION

### Include in MVP1

1. **`OrderbookWalkFillModel`** (H confidence)
   - For shadow mode: walks real L2 snapshot from Binance/Kraken WebSocket to compute VWAP fill
   - For backtest: uses OHLCV volume fraction cap + bar open/close price range
   - Handles MARKET and LIMIT orders; no queue position
   - Inspiration: NautilusTrader `BestPriceFillModel` + hftbacktest `PartialFillExchange`

2. **`MakerTakerFeeModel`** with Binance/Kraken tier tables (H confidence)
   - Maker rate for limit orders that rest; taker rate for market orders and crossed limits
   - Strategy-ID-tagged for bucket-level fee accounting
   - Inspiration: NautilusTrader `MakerTakerFeeModel` + hftbacktest `TradingValueFeeModel`

3. **Deterministic RNG** for probabilistic elements (H confidence)
   - Seeded via `BacktestConfig.random_seed`
   - Required for reproducible backtests

4. **Partial fill support** via volume fraction (M confidence)
   - Cap fills at `min(order_qty, bar_volume * max_volume_pct)` for OHLCV backtest
   - In shadow mode, partial fill naturally from orderbook walk
   - Inspiration: Zipline `VolumeShareSlippage` (concept only, not the buggy implementation)

5. **Intrabar ordering for stops** (H confidence)
   - When stop-loss AND take-profit fall within same bar range: check which triggers first using bar-open proximity heuristic
   - Implementation: if open closer to high → assume low touched first (for long positions)
   - Inspiration: NautilusTrader `bar_adaptive_high_low_ordering` flag

6. **Next-bar-open fill semantics** (H confidence)
   - Market order triggered at bar T close → fills at bar T+1 open price
   - NEVER fill at current bar close (Zipline bug AP-1)

### Defer to MVP2

1. **Queue Position Simulator** (defer) — requires tick data + Cont-Kukanov probability model
   - hftbacktest `ProbQueueModel` is the reference; needs L2 tick feed to calibrate
   - Only matters for limit orders in high-frequency fills

2. **Statistical Calibration Model** (defer) — fitting `prob_fill_on_limit` from historical data
   - NautilusTrader `DefaultFillModel` approach; needs historical fill rate analysis

3. **Market Impact for Large Orders** (defer) — LOB depletion simulation
   - hftbacktest documented limitation; only matters if order size > 5% of book depth
   - Swing strategy orders typically < 1% of book depth

4. **Funding Rate Simulation** (defer) — perpetual futures specific
   - NautilusTrader has `pending_funding_rates` + `FundingSettlement` built in
   - Relevant for perps but deferred for MVP1

5. **Latency Simulation** (defer) — `InflightCommand` min-heap pattern from NautilusTrader
   - Shadow mode already has real network latency; backtest latency simulation is MVP2

---

## 7. CONFIDENCE SUMMARY

| Recommendation | Confidence | Basis |
| --------------- | ------------ | ------- |
| `FillModelPort` abstraction shape | H | Synthesized from 4 real trait/interface definitions |
| Same-code-path via MarketDataContext | H | Negative evidence from Freqtrade; positive from NautilusTrader |
| Orderbook-walk for shadow fill | H | Live code reading hftbacktest PartialFillExchange + NautilusTrader |
| MakerTaker fee model | H | Both NautilusTrader and hftbacktest implement this pattern |
| Defer queue position to MVP2 | H | Only hftbacktest has it; requires L2 tick data not available in OHLCV |
| Defer market impact to MVP2 | H | Documented assumption in hftbacktest; negligible for swing orders |
| Next-bar-open semantics | H | Zipline #2011 as counter-evidence; LEAN InternalMarketFill as positive |
| Intrabar ordering heuristic | M | NautilusTrader has flag but no public doc on algorithm |
| Partial fill via volume fraction | M | Concept from Zipline VolumeShareSlippage; exact parameters need calibration |

---

## 8. KEY SOURCE FILES CITED

- `crates/execution/src/models/fill.rs` — NautilusTrader FillModel trait + 4 concrete implementations
- `crates/execution/src/models/fee.rs` — NautilusTrader FeeModel trait + MakerTaker
- `crates/backtest/src/exchange.rs` — SimulatedExchange struct (fill_model, fee_model, latency_model injection)
- `crates/backtest/src/engine.rs` — BacktestEngine + NautilusKernel construction
- `crates/backtest/src/config.rs` — SimulatedVenueConfig fields (queue_position, bar_adaptive_high_low_ordering)
- `Common/Orders/Fills/IFillModel.cs` — LEAN FillModel interface
- `Common/Orders/Fills/FillModel.cs` — LEAN FillModel base (InternalMarketFill line 282)
- `Common/Orders/Fills/FillModelParameters.cs` — LEAN parameter struct
- `Common/Orders/Slippage/VolumeShareSlippageModel.cs` — LEAN slippage model
- `Common/Data/Consolidators/ConsolidatorBase.cs` — LEAN consolidator pattern
- `freqtrade/exchange/exchange.py` — Freqtrade dry_run branches (lines 255, 1159, 1332, 1464 etc.)
- `freqtrade/optimize/backtesting.py` — Freqtrade backtest fill logic (_get_order_filled,_exit_trade)
- `hftbacktest/src/backtest/models/queue.rs` — RiskAdverseQueueModel + ProbQueueModel
- `hftbacktest/src/backtest/models/fee.rs` — TradingValueFeeModel
- `hftbacktest/src/backtest/proc/partialfillexchange.rs` — PartialFillExchange (partial fill logic)
- `zipline/finance/slippage.py` — VolumeShareSlippage.process_order (canonical fill-timing bug)
- `vectorbt/portfolio/nb.py` — buy_nb/sell_nb Numba functions (multi-strategy capital accounting)
