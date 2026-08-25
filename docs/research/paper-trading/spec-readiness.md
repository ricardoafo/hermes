# Spec-Readiness Audit — Paper-Trading Engine

**Date**: 2026-08-25
**Author**: spec-readiness sub-agent
**Status**: FINAL — for operator review 2026-08-26
**Scope**: 9 specs (SYNTHESIS.md §4 = Specs 1-7; SA-9 = Spec 8; pre-registration-v1.md shape = Spec 9)

---

## Summary Table

| Spec | Name | Risk | LOC estimate (net add) | Depends on | Ready-to-start-when |
| ------ | ------ | ------ | ------------------------ | ------------ | --------------------- |
| 1 | Domain Aggregates | LOW | ~1 400 | None | Now (OD decisions optional) |
| 2 | FillModelPort + MVP1 impl | HIGH | ~3 200 | Spec 1, Spec 4 (port only) | Spec 1 merged |
| 3 | OrderRouter | HIGH | ~2 300 | Spec 1, Spec 2 | Spec 2 merged |
| 4 | MarketDataPort + ClockPort | HIGH | ~2 800 | Spec 1 | Spec 1 merged; OD-7 decided |
| 5 | StrategyContract (SwingStrategyAdapter) | MEDIUM | ~1 900 | Spec 1, Spec 4 | Spec 4 merged |
| 6 | Calibration Harness | HIGH | ~2 400 | Spec 1, Spec 2, Spec 4 | Spec 2 + 4 merged |
| 7 | Test Pyramid Manifest | LOW | ~500 | None | Now (land alongside Spec 1) |
| 8 | Risk Overlay | HIGH | ~2 100 | Spec 2, Spec 3, Spec 4 | Spec 3 merged |
| 9 | Strategy Development Protocol | MEDIUM | ~1 600 | Spec 2, Spec 6, Spec 5 | Spec 5 + 6 merged |

---

## Spec 1: Domain Aggregates (bucket-aware)

- **Purpose**: Core domain vocabulary — Order, Fill, Position, Balance, Portfolio — as `strategy_id`-bearing value objects and aggregates, with `event_seq` + `payload_sha256` on every domain event.
- **Files that already exist and touch this**:
  - `src/hermes/domain/events/base.py` (78 LOC — `DomainEvent` base, `event_seq` not yet present)
  - `src/hermes/domain/events/trade_executed.py` (60 LOC — existing `TradeExecuted` event, no `strategy_id`)
  - `src/hermes/domain/events/trade_rejected.py`
  - `src/hermes/domain/events/lot_closed.py`
  - `src/hermes/domain/events/anomaly_detected.py`
  - `src/hermes/domain/events/balance_low.py`
  - `src/hermes/domain/events/daily_trade_digest.py`
  - `src/hermes/domain/events/rebalance_triggered.py`
  - `src/hermes/domain/events/strategy_paused.py`
  - `src/hermes/domain/events/strategy_resumed.py`
  - `src/hermes/domain/events/event_bus.py` (162 LOC — in-process event bus)
  - `src/hermes/domain/lots/lot.py` (174 LOC — existing Lot aggregate, already FIFO-aware)
  - `src/hermes/domain/lots/lot_consumption.py` (124 LOC — LotConsumption value object)
  - `src/hermes/domain/lots/selection.py` (146 LOC — FIFO selection logic)
  - `src/hermes/domain/value_objects/money.py` (158 LOC — Money value object, solid)
  - `src/hermes/domain/value_objects/pair.py` (93 LOC)
  - `src/hermes/domain/value_objects/currency.py` (54 LOC)
  - `src/hermes/domain/value_objects/price.py`
  - `src/hermes/domain/value_objects/quantity.py`
  - `src/hermes/domain/value_objects/lot_id.py`
  - `src/hermes/domain/value_objects/weight.py`
  - `src/hermes/domain/errors.py` (254 LOC — InfraError, LedgerError hierarchy, solid)
  - `src/hermes/adapters/ledger/sqlite_ledger.py` (303 LOC — existing ledger, FIFO-only, no `strategy_id`)
  - `src/hermes/ports/ledger.py` (118 LOC — LedgerPort, fully substantive, no `strategy_id` on Lot)
- **Empty stubs found**:
  - `src/hermes/adapters/clock/__init__.py` — 0 bytes. No ClockPort implementation exists anywhere in the codebase yet.
- **Files to create new**:
  - `src/hermes/domain/aggregates/__init__.py`
  - `src/hermes/domain/aggregates/order.py` — `Order`, `OrderId`, `OrderSide`, `OrderType`, `TimeInForce`, `OmsType`
  - `src/hermes/domain/aggregates/fill.py` — `Fill`, `FillId`
  - `src/hermes/domain/aggregates/position.py` — `Position`, `PositionId` (mutable aggregate)
  - `src/hermes/domain/aggregates/portfolio.py` — `Portfolio` root aggregate
  - `src/hermes/domain/aggregates/balance.py` — `Balance` per-strategy per-currency
  - `src/hermes/domain/aggregates/strategy_id.py` — `StrategyId`, `InstrumentId` value types
  - `src/hermes/domain/events/order_placed.py` — new event with `event_seq`, `payload_sha256`
  - `src/hermes/domain/events/order_filled.py`
  - `src/hermes/domain/events/position_opened.py`
  - `src/hermes/domain/events/position_closed.py`
  - `src/hermes/domain/events/balance_updated.py`
  - `tests/unit/domain/aggregates/__init__.py`
  - `tests/unit/domain/aggregates/test_order.py`
  - `tests/unit/domain/aggregates/test_fill.py`
  - `tests/unit/domain/aggregates/test_position.py`
  - `tests/unit/domain/aggregates/test_portfolio.py`
  - `tests/unit/domain/aggregates/test_strategy_id.py`
  - `tests/property/domain/__init__.py`
  - `tests/property/domain/test_portfolio_pnl_invariants.py`
  - `tests/property/domain/test_event_serialisation.py`
- **Depends on specs**: None.
- **Blocks specs**: 2, 3, 4, 5, 6, 7 (indirectly), 8, 9.
- **Estimated LOC delta (net add)**: ~1 400 (domain ~600 + tests ~800). Existing lot/event files need `strategy_id` backfill — AMENDMENTS, not new files; amendment cost is ~100 LOC net.
- **Risk**: LOW — almost entirely additive new files; existing lots/events need `strategy_id` threading but no behavioral change.
- **Notes**:
  - IMPORTANT: existing `DomainEvent.base.py` does NOT have `event_seq` or `payload_sha256`. Both must be added. This is a backward-compat break for existing event tests — all existing event test files will need updating (about 10 files in `tests/unit/domain/events/`).
  - Existing `Lot` aggregate (FIFO tax layer) does NOT carry `strategy_id`. It needs a field addition. The `LedgerPort` and `SqliteLedger` will also need amendment when Spec 1 lands.
  - `src/hermes/infrastructure/time.py` exposes `now_utc()` — this is the function the import-linter contract in Spec 7 will forbid from being called in domain code. The domain must call `ClockPort.now()` instead. Current domain code has no direct `now_utc()` calls (confirmed by importlinter contracts already blocking `hermes.infrastructure` from `hermes.domain`), so this is safe.
  - ULID is the specified ID type in SYNTHESIS. Hermes currently uses `lot_id` (custom value object). Check whether ULID library is a dependency — it likely is not yet.

---

## Spec 2: FillModelPort + MVP1 Implementation

- **Purpose**: Single fill code path — `FillModelPort` protocol, `OrderbookWalkFillModel`, `MakerTakerFeeModel`, and `SimulatedExchangeAdapter` — shared across backtest, shadow, and live simulation. Closes GH #198.
- **Files that already exist and touch this**:
  - `src/hermes/domain/market_data/orderbook.py` (108 LOC — `Orderbook`, `OrderbookLevel` value objects, SOLID. Already validated, immutable, correct bid-descending/ask-ascending invariants. This is the `OrderbookSnapshot` the fill model needs.)
  - `src/hermes/ports/fees.py` (65 LOC — `FeeScheduleProviderPort` fully substantive)
  - `src/hermes/domain/fees/fee_schedule.py` (182 LOC — `FeeSchedule` with volume tiers, solid)
  - `src/hermes/adapters/exchanges/kraken_public.py` (430+ LOC — Kraken public adapter, implements `FeeScheduleProviderPort`. Fee schedule retrieval for Kraken is done. Binance fee tier table must still be hardcoded in `MakerTakerFeeModel`.)
  - `src/hermes/adapters/exchanges/binance_klines.py` (430 LOC — existing Binance klines adapter. No fill model, no order placement.)
  - `src/hermes/ports/exchange.py` (20 LOC — **CONFIRMED EMPTY STUB**: `ExchangePort` Protocol with zero methods, as reported in #199. This is the primary blocker for wiring any adapter.)
  - `src/hermes/adapters/exchanges/rate_limiter.py` (exists — shared rate limiter, reusable)
  - `tests/unit/adapters/exchanges/test_kraken_public.py` (exists — tests for public adapter)
  - `tests/unit/domain/market_data/test_orderbook.py` (exists — covers `Orderbook` value object)
- **Empty stubs found**:
  - `src/hermes/ports/exchange.py` — 20 LOC total, 0 methods. The body is a docstring and a `pass`-equivalent Protocol with no method definitions. This IS issue #199. Must be filled as the first act of Spec 2.
- **Files to create new**:
  - `src/hermes/ports/fill_model.py` — `FillModelPort` (ABC/Protocol), `MarketDataContext`, `FillResult`
  - `src/hermes/domain/engine/__init__.py`
  - `src/hermes/domain/engine/fill_model.py` — `OrderbookWalkFillModel` concrete impl
  - `src/hermes/domain/engine/fee_model.py` — `MakerTakerFeeModel` with Binance + Kraken tier tables
  - `src/hermes/adapters/exchanges/simulated_exchange.py` — `SimulatedExchangeAdapter` (implements `ExchangePort`)
  - `tests/unit/domain/engine/__init__.py`
  - `tests/unit/domain/engine/test_fill_model.py` — orderbook walk correctness, quantization, MIN_NOTIONAL
  - `tests/unit/domain/engine/test_fee_model.py` — maker/taker classification, tier lookup
  - `tests/unit/adapters/exchanges/test_simulated_exchange.py` — end-to-end sim adapter
  - `tests/property/engine/__init__.py`
  - `tests/property/engine/test_fill_model_props.py` — VWAP ≥ best price, determinism, commission proportionality
  - `tests/integration/engine/__init__.py`
  - `tests/integration/engine/test_simulated_exchange_e2e.py` — canned orderbook → deterministic event log
- **Depends on specs**: 1 (Order, Fill, Portfolio aggregates), 4 (MarketDataPort type only — can be stubbed for Spec 2 tests).
- **Blocks specs**: 3, 6, 8.
- **Estimated LOC delta (net add)**: ~3 200 (domain+adapters ~1 200, tests ~2 000). SYNTHESIS estimates "~1 200 LoC + ~2 000 LoC tests" = 3 200 net.
- **Risk**: HIGH — almost 100% greenfield new code in a new `domain/engine/` subtree. Central piece: every downstream spec depends on this. The `ExchangePort` stub issue (#199) means even the interface contract does not exist yet. The `MakerTakerFeeModel` requires hardcoding Binance fee tiers (not in any existing file) — requires research verification against Binance docs.
- **Notes**:
  - The anti-Zipline and anti-Freqtrade tests (SA-2 AP-1, AP-2) must be written here. The import-linter contract "no `if shadow` or `if dry_run` in fill.py" does not yet exist in `importlinter.toml` — Spec 7 adds it but Spec 2 must be written to comply from day one.
  - `domain/engine/` does not exist at all. Naming should follow `domain/lots/`, `domain/fees/` patterns (one concern per subdirectory).
  - SA-3 mandates tick-size and lot-size quantization (P0.1) and MIN_NOTIONAL validation (P0.6). These are Binance-specific constraints. The Binance exchange info endpoint is not yet implemented in any adapter — the quantization constants will need to be hard-coded or fetched. Recommend hard-code for MVP1.

---

## Spec 3: OrderRouter (bucket-aware, strategy-agnostic)

- **Purpose**: Route signals from strategies to the exchange adapter, enforcing per-strategy `capital_budget`, correlation limits, ATR-based sizing with Kelly cap, and drawdown circuit-breakers.
- **Files that already exist and touch this**:
  - `src/hermes/domain/errors.py` (254 LOC — error hierarchy reusable)
  - `src/hermes/ports/exchange.py` (stub — will be filled by Spec 2)
  - `src/hermes/domain/fees/fee_schedule.py` (referenced for commission-aware risk calc)
  - `src/hermes/adapters/exchanges/rate_limiter.py` (rate limiter pattern reusable for P0-5)
- **Empty stubs found**: None additional beyond what Spec 2 resolves.
- **Files to create new**:
  - `src/hermes/domain/engine/order_router.py` — `OrderRouter`, `RouteDecision`
  - `src/hermes/domain/engine/risk_engine.py` — `RiskEngine` with `capital_budget`, drawdown limits, correlation lock (RO-01 from SA-9, which becomes Spec 8 — but correlation lock is also in Spec 3 per SYNTHESIS)
  - `src/hermes/domain/engine/sizing.py` — `SizingCalculator` (ATR baseline × Kelly cap composition)
  - `src/hermes/domain/engine/rate_limit_tracker.py` — P0-5 weight tracker (80% ceiling + exponential backoff)
  - `tests/unit/domain/engine/test_order_router.py`
  - `tests/unit/domain/engine/test_risk_engine.py`
  - `tests/unit/domain/engine/test_sizing.py`
  - `tests/property/engine/test_sizing_props.py` — sizing never exceeds any of ATR/Kelly/equity caps
  - `tests/integration/engine/test_order_router_e2e.py` — signal → sizing → risk → exchange paths
  - `tests/chaos/engine/__init__.py`
  - `tests/chaos/engine/test_flash_crash.py` — inject 20% price gap → circuit-breaker fires
  - `tests/chaos/engine/test_rate_limit.py` — 429 injection → exponential backoff
- **Depends on specs**: 1 (Order, Portfolio aggregates), 2 (ExchangePort fulfilled, FillResult).
- **Blocks specs**: 5 (indirectly — strategy signals need a router to be testable end-to-end), 8 (RiskEngine provides correlation lock foundation that Spec 8 extends).
- **Estimated LOC delta (net add)**: ~2 300 (engine code ~800 + tests ~1 500).
- **Risk**: HIGH — entirely greenfield (`OrderRouter`, `RiskEngine`, `SizingCalculator` are all new). The Knight Capital / orphan-position invariant and rate-limit tracker are subtle concurrent-state concerns requiring careful property testing.
- **Notes**:
  - Tension T-1 (Kelly vs ATR sizing composition) is resolved here. The `SizingCalculator` is the most mathematically complex piece in this spec — it must handle `f_star_estimate=None` (degenerate to 5% cap) gracefully.
  - The `RiskEngine.capital_budget` is a per-strategy map. For MVP1 there is one strategy, but the data structure must support N from day one.
  - Chaos marker (`chaos`) does not yet exist in `pyproject.toml` markers. Spec 7 adds it, but if Spec 3 ships before Spec 7 (it shouldn't per SYNTHESIS landing sequence), coordination is needed.
  - The correlation lock (`correlation_lock`: max 1 BTC-correlated position) appears in BOTH Spec 3 (SYNTHESIS §3 Spec 3 scope) AND Spec 8 (SA-9 defense matrix). The boundary: Spec 3 implements the blocking logic in `RiskEngine`; Spec 8 adds the DETECTION signals (rolling correlation computation, spread-spike, depeg monitor). Do not duplicate code.

---

## Spec 4: MarketDataPort + ClockPort

- **Purpose**: The two infrastructure injection ports. Realises the hybrid time model: `raw_events` channel for fills (L2 diffs + trade prints) and `consolidated_bars` channel for strategy signals. Ships orderbook diff storage (GH #203), live WebSocket adapters for Binance + Kraken (bootstrap protocols per SA-3), and `ClockPort` with `SimulatedClock` + `WallClockClock`.
- **Files that already exist and touch this**:
  - `src/hermes/domain/market_data/orderbook.py` (108 LOC — `Orderbook` value object reusable as snapshot type)
  - `src/hermes/domain/market_data/kline.py` (136 LOC — `Kline`, `KlineInterval` value objects, reusable as `Bar`)
  - `src/hermes/domain/market_data/ticker.py` (74 LOC — `Ticker` value object)
  - `src/hermes/ports/klines.py` (72 LOC — `KlinesFetcherPort`, fully substantive. The `Kline` value object is the proto-`Bar`.)
  - `src/hermes/adapters/exchanges/binance_klines.py` (430 LOC — existing REST klines adapter. This is the HISTORICAL data path but NOT the WebSocket streaming path. The Consolidator builds on top of klines but the live WebSocket adapter is new.)
  - `src/hermes/adapters/exchanges/kraken_public.py` (430+ LOC — Kraken klines, ticker, orderbook. Public REST only — no WebSocket streaming.)
  - `src/hermes/adapters/exchanges/rate_limiter.py` (reusable for rate limit tracking P0-5)
  - `src/hermes/infrastructure/time.py` (67 LOC — `now_utc()` is the pre-`ClockPort` pattern. `WallClockClock` wraps this.)
  - `src/hermes/adapters/clock/__init__.py` — **EMPTY STUB**: 0 bytes. No clock implementations exist.
  - `src/hermes/adapters/ledger/sqlite_ledger.py` (303 LOC — SQLite WAL mode already established; orderbook diff storage will use same SQLite approach per OD-7 recommendation)
  - `src/hermes/adapters/ledger/migration_runner.py` (exists — migration runner reusable for new orderbook diff schema)
- **Empty stubs found**:
  - `src/hermes/adapters/clock/__init__.py` — 0 bytes (confirmed). Both `SimulatedClock` and `WallClockClock` must be created from scratch.
- **Files to create new**:
  - `src/hermes/ports/market_data.py` — `MarketDataPort` Protocol, `ClockPort` Protocol, `MarketEvent`, `Bar`, `Timeframe`, `OrderbookDiff`, `TradePrint`
  - `src/hermes/adapters/clock/simulated.py` — `SimulatedClock`
  - `src/hermes/adapters/clock/wall_clock.py` — `WallClockClock` (wraps `now_utc()`)
  - `src/hermes/domain/engine/consolidator.py` — `Consolidator` (LEAN-inspired bar aggregator)
  - `src/hermes/adapters/exchanges/binance_ws.py` — `LiveWebSocketMarketDataAdapter` for Binance (buffer+snapshot+updateId protocol, SA-3 §1.3)
  - `src/hermes/adapters/exchanges/kraken_ws.py` — `LiveWebSocketMarketDataAdapter` for Kraken (CRC32 checksum validation, SA-3 P1.4)
  - `src/hermes/adapters/exchanges/historical_market_data.py` — `HistoricalMarketDataAdapter` (replay adapter for backtest)
  - `src/hermes/adapters/ledger/migrations/003_orderbook_diffs.py` — orderbook diff storage schema migration
  - `src/hermes/adapters/ledger/orderbook_diff_writer.py` — append-only diff writer
  - `tests/unit/domain/engine/test_consolidator.py` — bar aggregation correctness
  - `tests/unit/adapters/clock/test_simulated_clock.py`
  - `tests/unit/adapters/clock/test_wall_clock.py`
  - `tests/unit/adapters/exchanges/test_binance_ws.py` — bootstrap protocol unit tests
  - `tests/unit/adapters/exchanges/test_kraken_ws.py` — CRC32 checksum tests
  - `tests/property/engine/test_consolidator_props.py` — bar high ≥ all trade prices, volume sum
  - `tests/integration/adapters/exchanges/test_binance_ws_replay.py` — recorded WS capture replay
  - `tests/chaos/adapters/test_ws_reconnect.py` — WebSocket 1006 → REST reconciliation (P0-1)
  - `tests/chaos/adapters/test_clock_drift.py` — P0-2 clock drift > 5s → halt + alert
- **Depends on specs**: 1 (domain events, `InstrumentId`).
- **Blocks specs**: 2 (shadow tests need `MarketDataPort`), 5 (strategy needs `consolidated_bars`), 6 (calibration needs real fills feed).
- **Estimated LOC delta (net add)**: ~2 800 (ports+adapters ~1 000 + engine consolidator ~200 + tests ~1 800 + storage migration ~100). SYNTHESIS estimates "~1 000 LoC + ~1 800 LoC tests" = 2 800 net.
- **Risk**: HIGH — largest surface area of any spec. The Binance WebSocket bootstrap protocol (buffer+snapshot+updateId reconciliation) is well-documented in SA-3 but finicky to implement correctly. The `kraken_ws.py` CRC32 validation is an additional correctness requirement not present in any existing file. Two entirely new WebSocket adapters. P0-1 (REST reconciliation on WS reconnect) is a chaos test requiring precise state machine behavior.
- **Notes**:
  - OD-7 (SQLite vs Parquet for orderbook diff storage) MUST be decided before Spec 4 begins detailed design. The SYNTHESIS recommends SQLite (Option A). If Parquet is chosen, the migration runner approach cannot be reused and a new write path must be designed.
  - The existing `KlinesFetcherPort` + `BinanceKlinesAdapter` cover historical bar retrieval. The new `MarketDataPort` supersedes `KlinesFetcherPort` for the engine's internal use, but `KlinesFetcherPort` remains valid for external historical queries (e.g., preview CLI). Do NOT delete `KlinesFetcherPort`.
  - `src/hermes/infrastructure/time.py`'s `now_utc()` becomes an internal detail wrapped by `WallClockClock`. The import-linter contract in Spec 7 will forbid domain code from calling `now_utc()` directly — this is consistent because `hermes.infrastructure` is already forbidden from `hermes.domain`.
  - `HistoricalMarketDataAdapter` for OHLCV-only backtest (OD-1 Option D) should synthesize a fake orderbook from bar OHLCV using the documented LEAN/SA-2 approach. This is the MVP1 path; tick-level replay from captured diffs is MVP2.

---

## Spec 5: StrategyContract (SwingStrategyAdapter for MVP1)

- **Purpose**: Define `StrategyPort` interface that all strategies implement. Ship `SwingStrategyAdapter` (Donchian(20) 4H + Chandelier(22, 3.0) + ADX(14) + EMA(200) + OBV + ATR-spike filters). Closes/scopes GH #197 DonchianEngine part + #200 SwingSignalUC part.
- **Files that already exist and touch this**:
  - `src/hermes/domain/market_data/kline.py` (136 LOC — `Kline`, `KlineInterval` — the bar input type)
  - `src/hermes/domain/market_data/orderbook.py` (reusable value object)
  - `src/hermes/domain/events/strategy_paused.py` (exists — `DRAINING` state machine partially anticipated)
  - `src/hermes/domain/events/strategy_resumed.py` (exists)
  - `src/hermes/domain/fees/fee_schedule.py` (reusable)
  - `src/hermes/infrastructure/config/models.py` (383 LOC — existing config system, `StrategyConfig` will extend this)
- **Empty stubs found**: None directly related. But GH #197 (`Rebalancer + DonchianEngine domain classes`) is tracked as an issue implying some work was anticipated — SYNTHESIS says split it and scope DonchianEngine to Spec 5. No actual code for it found in codebase.
- **Files to create new**:
  - `src/hermes/ports/strategy.py` — `StrategyPort` Protocol, `StrategyConfig`, `StrategyStatus`, `StrategySignal`, `SignalIntent`
  - `src/hermes/domain/strategies/__init__.py`
  - `src/hermes/domain/strategies/swing/__init__.py`
  - `src/hermes/domain/strategies/swing/indicators.py` — Donchian, ATR, ADX, EMA, OBV, Chandelier indicator computations
  - `src/hermes/domain/strategies/swing/swing_strategy.py` — `SwingStrategyAdapter` concrete impl
  - `src/hermes/domain/strategies/swing/config.py` — `SwingStrategyConfig` (extends `StrategyConfig`)
  - `tests/unit/domain/strategies/__init__.py`
  - `tests/unit/domain/strategies/swing/__init__.py`
  - `tests/unit/domain/strategies/swing/test_indicators.py` — cross-check vs TA-Lib reference values
  - `tests/unit/domain/strategies/swing/test_swing_strategy.py` — regime filter logic, System 1 fail-safe
  - `tests/property/strategies/__init__.py`
  - `tests/property/strategies/test_swing_props.py` — Chandelier monotone, no signal when ADX<25
  - `tests/integration/strategies/__init__.py`
  - `tests/integration/strategies/test_swing_e2e.py` — canned bar sequence → deterministic signal sequence
- **Depends on specs**: 1 (domain aggregates: `StrategyId`, `Order`, `Fill`, `Portfolio`), 4 (`Bar` from `MarketDataPort`, `ClockPort` injection for UTC).
- **Blocks specs**: 6 (calibration needs real strategy signals), 9 (pre-registration protocol consumes strategy definition).
- **Estimated LOC delta (net add)**: ~1 900 (indicators + strategy ~700 + tests ~1 200).
- **Risk**: MEDIUM — indicators are greenfield but well-specified (SA-7). The risk is indicator implementation errors (off-by-one in rolling windows, incorrect ATR formula). SA-7 specifies cross-check vs TA-Lib — TA-Lib is not in hermes dependencies today. Recommend `pandas-ta` or `ta-lib` as test-only dependency, or compute reference values offline.
- **Notes**:
  - `src/hermes/domain/strategies/` does not exist. Create it (analogous to `domain/lots/`, `domain/fees/`).
  - The `SwingStrategyAdapter` consumes `Bar` from Spec 4's consolidator AND signals from `InstrumentId`. In the backtest path, bars come from `HistoricalMarketDataAdapter`. Test fixtures can use canned bar sequences — no live data needed for unit/integration tests.
  - `StrategyStatus` state machine (`ACTIVE → DRAINING → DISABLED`) has proto-events already in `domain/events/strategy_paused.py` and `strategy_resumed.py`. These events should be wired to the state machine here.
  - DST timezone test (SA-4 P0-6): the strategy must only emit UTC timestamps via `ClockPort`. The existing `infrastructure/time.py` DST helpers must not be called from strategy code. The import-linter already blocks `hermes.domain` → `hermes.infrastructure`.

---

## Spec 6: Calibration Harness

- **Purpose**: SA-5's calibration protocol as an automated CI gate. 6 P0 metrics (M-02, M-04, M-09, M-10, M-13, M-15), `CERTIFIED / UNTRUSTED` state machine, testnet baseline subtraction (T-5), replay CLI. Closes GH #201.
- **Files that already exist and touch this**:
  - `src/hermes/adapters/ledger/sqlite_ledger.py` (303 LOC — calibration state machine stored in same SQLite DB; CERTIFIED/UNTRUSTED status is a new table)
  - `src/hermes/adapters/ledger/migration_runner.py` (reusable for new calibration schema migration)
  - `src/hermes/domain/errors.py` (error hierarchy reusable)
  - `src/hermes/entrypoints/cli.py` (replay CLI extends this entrypoint)
  - `src/hermes/infrastructure/config/models.py` (383 LOC — calibration thresholds will be config-driven)
- **Empty stubs found**: None directly.
- **Files to create new**:
  - `src/hermes/domain/calibration/__init__.py`
  - `src/hermes/domain/calibration/service.py` — `CalibrationService` (pure domain, no I/O)
  - `src/hermes/domain/calibration/metrics.py` — M-02 KS+Wasserstein, M-04 binomial bias, M-09 Pearson r, M-10 Sharpe permutation, M-13 lookahead audit, M-15 fee delta
  - `src/hermes/domain/calibration/report.py` — `CalibrationReport` value object with JSON schema
  - `src/hermes/domain/calibration/state_machine.py` — `ShadowModeStatus` (`PENDING_DATA → CERTIFIED → UNTRUSTED`)
  - `src/hermes/ports/calibration.py` — `RecordedExchangePort` Protocol, `ExternalCallRecorded` event (SA-8)
  - `src/hermes/adapters/calibration/__init__.py`
  - `src/hermes/adapters/calibration/fill_pair_matcher.py` — sim↔real fill-pair matcher (5s window)
  - `src/hermes/adapters/ledger/migrations/004_calibration.py` — CERTIFIED/UNTRUSTED state schema
  - `src/hermes/adapters/calibration/shadow_mode_gate.py` — `ShadowModeGate` CI gate
  - `src/hermes/adapters/calibration/baseline_job.py` — 7-day testnet baseline measurement job
  - `src/hermes/entrypoints/replay.py` — `hermes replay --from --to --compare` CLI
  - `tests/unit/domain/calibration/__init__.py`
  - `tests/unit/domain/calibration/test_metrics.py` — KS wrapper, Wasserstein, Pearson r bootstrap CI, bias binomial
  - `tests/unit/domain/calibration/test_state_machine.py` — UNTRUSTED cannot auto-restore
  - `tests/unit/domain/calibration/test_report.py`
  - `tests/property/calibration/__init__.py`
  - `tests/property/calibration/test_metrics_props.py` — percentile monotonicity, threshold boundary
  - `tests/integration/calibration/__init__.py`
  - `tests/integration/calibration/test_calibration_e2e.py` — fake sim (known biased) → BLOCK; fake matching → CERTIFIED
  - `tests/fixtures/calibration/` — golden file regression scenarios
- **Depends on specs**: 1 (domain events, `FillId`), 2 (`SimulatedExchangeAdapter` for sim fills), 4 (`MarketDataPort` for real fills feed).
- **Blocks specs**: 9 (pre-registration certification lifecycle depends on calibration harness).
- **Estimated LOC delta (net add)**: ~2 400 (domain+adapters+CLI ~900 + tests ~1 500). SYNTHESIS estimates "~900 LoC + ~1 500 LoC tests" = 2 400 net.
- **Risk**: HIGH — the statistical metric implementations (KS test, Wasserstein, Pearson r bootstrap CI, Sharpe permutation) are moderately complex. `scipy.stats` is not a current dependency of hermes — needs to be added. The fill-pair matcher's 5-second window logic requires careful handling of out-of-order fills. The replay CLI requires the `RecordedExchangePort` pattern from SA-8 to be wired end-to-end.
- **Notes**:
  - `scipy` is almost certainly not in `pyproject.toml` yet. Check before spec design. Alternative: implement the statistical tests from first principles using `numpy` only (already likely present via `pandas`).
  - The `ShadowModeGate` is invoked by CI scripts (`scripts/dev/` pattern already established per git status). It must be a standalone executable that exits non-zero on UNTRUSTED — wire into the existing CI workflow structure.
  - T-5 (testnet baseline subtraction): the 7-day baseline measurement job runs against real Binance testnet. This is a CALIBRATION-tier test that must not run in PR CI (too slow, requires external connectivity). The existing `contract` marker pattern applies.
  - Golden file regression tests use `syrupy` (already a test dependency per `pyproject.toml`). This is a natural fit.

---

## Spec 7: Test Pyramid Manifest

- **Purpose**: Acceptance gate for all other specs. Pytest markers, CI job matrix, coverage thresholds, import-linter contracts, mutation testing config, 6 P0 chaos test manifest. Ships alongside Spec 1.
- **Files that already exist and touch this**:
  - `pyproject.toml` — already has `[tool.pytest.ini_options]` with markers (`smoke`, `integration`, `e2e`, `slow`, `contract`), import-linter contracts (5 existing contracts), coverage config, `cosmic-ray` mutation testing config.
  - `.github/workflows/` — existing CI workflows (build, CI, nightly-quality, mutation). Structure must be extended.
  - `tests/plugins/sev1_guard.py` — existing SEV-1 annotation lint plugin.
  - `tests/conftest.py` — existing conftest.
- **Empty stubs found**: None.
- **Files to create new**:
  - `docs/testing/manifest.md` — the pyramid diagram + tier definitions (documentation only)
  - `tests/chaos/__init__.py` — chaos tier package (chaos tests land in Specs 3, 4, 8)
  - `tests/property/__init__.py` — property tier package (if not already present)
  - `tests/unit/test_manifest.py` — meta-test asserting markers registered, CI matrix contains required jobs, import-linter contracts execute
- **Existing files to amend**:
  - `pyproject.toml` — ADD markers: `property`, `chaos`, `mutation`, `calibration`. ADD import-linter contracts: (a) no `datetime.now()` in domain, (b) no `if shadow` / `if dry_run` in fill modules, (c) no cross-strategy imports. ADD coverage thresholds: domain ≥ 95%, adapter ≥ 90%, application ≥ 90% (per ADR-007 — thresholds exist in spirit but not in `pyproject.toml` yet).
  - `.github/workflows/tests.yml` (to be created or extended) — matrix: unit+property (every PR), integration (every PR), calibration (nightly against testnet), chaos (nightly), mutation (weekly).
- **Depends on specs**: None.
- **Blocks specs**: All others (acceptance gate).
- **Estimated LOC delta (net add)**: ~500 (config amendments ~100 + meta-tests ~300 + docs ~100).
- **Risk**: LOW — mostly configuration changes and documentation. The `chaos` and `property` markers are new but additive. Main risk: CI matrix changes must not break the existing PR gate.
- **Notes**:
  - IMPORTANT: `pyproject.toml` currently has `property` NOT in the markers list. Hypothesis-based property tests exist in `tests/strategies/` (e.g., `lot_id.py`, `money.py`) but they are not marked as `property` — they use Hypothesis `@given` directly without a custom marker. This is a discrepancy that Spec 7 must resolve.
  - The mutation testing tool is `cosmic-ray` (per `pyproject.toml` decisions), NOT `mutmut` as mentioned in SYNTHESIS. Spec 7 must reference the correct tool.
  - 6 P0 chaos tests are OWNED by Specs 3 and 4 but LISTED in the manifest. The manifest must enumerate them by name so the gate is machine-checkable.
  - The `sev1_guard.py` plugin already enforces `# SEV-1: <rubric row>` annotations. This is the existing implementation of the "SEV-1 annotation lint" from the SYNTHESIS. Spec 7 documents it as part of the pyramid, not duplicate it.

---

## Spec 8: Risk Overlay

- **Purpose**: Portfolio-level shock defenses: 8 MVP1 rules (RO-01 correlation lock, RO-02/03 max-slippage guards, RO-04 spread-spike halt, RO-05 stale book guard, RO-06 depeg monitor + equity restatement, RO-07 FOMC blackout, RO-08 multi-pair ATR cascade halt). Defined in SA-9.
- **Files that already exist and touch this**:
  - `src/hermes/domain/market_data/orderbook.py` (108 LOC — spread computation uses `Orderbook.bids[0].price` and `asks[0].price` — directly usable)
  - `src/hermes/domain/market_data/ticker.py` (74 LOC — `Ticker` value object usable for depeg monitor USDT/USD price)
  - `src/hermes/adapters/exchanges/kraken_public.py` (430+ LOC — `get_ticker()` can already fetch USDT/USD price from Kraken for depeg monitoring. SA-9 §4.4 calls this out explicitly as the recommended data source.)
  - `src/hermes/domain/errors.py` (InfraError hierarchy for alert events)
  - `src/hermes/domain/events/anomaly_detected.py` (exists — reusable for CRITICAL/WARN alert emission)
  - `src/hermes/domain/events/event_bus.py` (162 LOC — in-process event bus reusable for alert dispatch)
  - `src/hermes/adapters/notifier/slack_adapter.py` + `telegram_adapter.py` (CRITICAL alerts pipeline)
  - `src/hermes/application/notification_router.py` (reusable for alert routing)
  - `src/hermes/infrastructure/config/models.py` (383 LOC — RO thresholds will be config-driven)
- **Empty stubs found**: None. But the depeg monitor polls Kraken public ticker — the polling infrastructure does not exist yet (only one-shot REST calls exist in `kraken_public.py`).
- **Files to create new**:
  - `src/hermes/domain/risk_overlay/__init__.py`
  - `src/hermes/domain/risk_overlay/rules.py` — RO-01 through RO-08 as pure domain rule objects
  - `src/hermes/domain/risk_overlay/correlation.py` — rolling correlation computation (7-day window, BTC dominance bucket logic)
  - `src/hermes/domain/risk_overlay/spread_monitor.py` — spread-spike detection (1H rolling median, 10× multiplier)
  - `src/hermes/domain/risk_overlay/depeg_monitor.py` — USDT/USDC deviation monitor (1.5% warn, 3% halt)
  - `src/hermes/domain/risk_overlay/fomc_calendar.py` — hardcoded FOMC dates 2026-2027, blackout window logic
  - `src/hermes/domain/risk_overlay/equity_restater.py` — equity restatement given stablecoin price
  - `src/hermes/domain/risk_overlay/overlay_service.py` — `RiskOverlayService` orchestrating all 8 rules
  - `src/hermes/adapters/risk_overlay/__init__.py`
  - `src/hermes/adapters/risk_overlay/depeg_poller.py` — async polling loop (60s interval) using `KrakenPublicAdapter`
  - `src/hermes/adapters/risk_overlay/spread_tracker.py` — stateful rolling spread tracker fed by orderbook events
  - `src/hermes/adapters/risk_overlay/correlation_tracker.py` — stateful rolling correlation tracker fed by price events
  - `tests/unit/domain/risk_overlay/__init__.py`
  - `tests/unit/domain/risk_overlay/test_rules.py` — each RO-N rule fires at threshold
  - `tests/unit/domain/risk_overlay/test_fomc_calendar.py` — blackout window edge cases (UTC midnight, DST)
  - `tests/unit/domain/risk_overlay/test_equity_restater.py` — restatement math correctness
  - `tests/property/risk_overlay/__init__.py`
  - `tests/property/risk_overlay/test_spread_monitor_props.py` — rolling median monotonicity
  - `tests/integration/risk_overlay/__init__.py`
  - `tests/integration/risk_overlay/test_overlay_e2e.py` — composite scenario A/B/C from SA-9 §9
  - `tests/chaos/risk_overlay/__init__.py`
  - `tests/chaos/risk_overlay/test_depeg_composite.py` — depeg + correlation shock compound scenario
- **Depends on specs**: 2 (fill model's slippage estimation for RO-02/03, stale book guard RO-05), 3 (`RiskEngine` provides the blocking integration point for RO-01/04/07/08), 4 (`MarketDataPort` for spread and price events feed).
- **Blocks specs**: 9 (pre-registration validation requires SA-9 RO-01 to be live and tested).
- **Estimated LOC delta (net add)**: ~2 100 (domain+adapters ~900 + tests ~1 200).
- **Risk**: HIGH — largely greenfield domain subdirectory. The rolling correlation computation (7-day window over sparse price events) is non-trivial to implement correctly without an existing framework. The FOMC calendar must cover 2026 and 2027 dates and handle UTC edge cases around the 2:00 PM Eastern announcement time (7:00 PM UTC, or 8:00 PM UTC during EDT). The depeg poller is the first polling loop in hermes — a new async infrastructure pattern.
- **Notes**:
  - RO-01 (correlation lock) is already named in Spec 3's scope as a `RiskEngine` rule. The architecture: `RiskEngine` (Spec 3) checks whether a new entry is BLOCKED by correlation state; `CorrelationTracker` (Spec 8) maintains the rolling state that `RiskEngine` queries. This is the correct separation — do not move the correlation state into Spec 3.
  - The depeg monitor uses `kraken_public.py`'s `get_ticker()` for real USDT/USD prices. The `Ticker` value object from `domain/market_data/ticker.py` already models this. The poller wraps the existing adapter — minimal new code on the adapter side.
  - RO-07 (FOMC blackout): FOMC meeting dates for 2026 are not hardcoded anywhere. The 2026 FOMC calendar must be sourced from the Federal Reserve's published schedule and hardcoded into `fomc_calendar.py`. Reminder: meeting times are 2:00 PM Eastern (14:00 ET = 19:00 UTC in EST, 18:00 UTC in EDT). This DST sensitivity is a gotcha.
  - RO-05 (stale book guard) straddles Spec 2 (fill model) and Spec 8. The book staleness flag must be managed in the fill model's `MarketDataContext` (Spec 2) and checked by the overlay (Spec 8). Design the interface in Spec 2, the enforcement in Spec 8.
  - SA-9 §10 "Defense Matrix" shows `exchange-adapter spec` ownership for health monitor, symbol availability check, and withdrawal balance monitor. These are NOT in Spec 8 — they belong to Spec 4 (MarketDataPort health) and Spec 3 (OrderRouter pre-entry checks). Spec 8 focuses exclusively on portfolio-level shock rules.

---

## Spec 9: Strategy Development Protocol

- **Purpose**: Operationalize the pre-registration framework from `pre-registration-v1.md` as a machine-enforced engine feature. Data-split wall (OOS window physically blocked until certification date), lab notebook format, `experiment_id` tagging through the calibration harness, DSR certification lifecycle state (`UNTRUSTED → BACKTEST_CERTIFIED → SHADOW_CERTIFIED → OOS_CERTIFIED → LIVE_APPROVED → CERTIFIED`).
- **Files that already exist and touch this**:
  - `docs/experiments/swing-mvp1/pre-registration-v1.md` — the specification source (PENDING_SIGNATURE status, not yet committed/signed). This document IS the spec shape — the engine must enforce it.
  - `src/hermes/adapters/ledger/sqlite_ledger.py` (calibration state machine from Spec 6 extends here)
  - `src/hermes/domain/calibration/state_machine.py` (NEW — from Spec 6; Spec 9 adds the pre-registration lifecycle on top)
  - `src/hermes/infrastructure/config/models.py` (experiment config will be a new config section)
  - `src/hermes/entrypoints/cli.py` (new CLI commands: `hermes experiment sign`, `hermes experiment certify`, `hermes experiment status`)
- **Empty stubs found**: None — `docs/experiments/swing-mvp1/` directory exists with `pre-registration-v1.md` but no companion files: `data-manifest.json`, `lab-notebook.md`, `fee-history.csv`, `post-mortem-template.md` are all listed as "pending" in §14 of the pre-registration. These are documentation artifacts, not code.
- **Files to create new**:
  - `src/hermes/domain/experiment/__init__.py`
  - `src/hermes/domain/experiment/pre_registration.py` — `PreRegistration` value object (immutable once signed), `ExperimentId`, data window freeze, strategy spec freeze
  - `src/hermes/domain/experiment/lifecycle.py` — `ExperimentStatus` state machine (UNTRUSTED → BACKTEST_CERTIFIED → SHADOW_CERTIFIED → OOS_CERTIFIED → LIVE_APPROVED → CERTIFIED), kill trigger evaluation
  - `src/hermes/domain/experiment/data_wall.py` — OOS date-wall enforcement (raises if OOS window accessed outside certification mode before `certification_gate_date`)
  - `src/hermes/adapters/experiment/__init__.py`
  - `src/hermes/adapters/experiment/lab_notebook.py` — append-only lab notebook writer (event-sourced per SA-8 discipline)
  - `src/hermes/adapters/experiment/sha256_signer.py` — SHA256 hash of pre-registration document for signature
  - `src/hermes/adapters/ledger/migrations/005_experiment.py` — `experiment_status` table
  - `src/hermes/entrypoints/experiment.py` — CLI sub-commands: `sign`, `certify`, `status`, `kill`
  - `tests/unit/domain/experiment/__init__.py`
  - `tests/unit/domain/experiment/test_pre_registration.py` — immutability after signing, data window validation
  - `tests/unit/domain/experiment/test_lifecycle.py` — state machine transitions, kill trigger evaluation
  - `tests/unit/domain/experiment/test_data_wall.py` — OOS window blocked before certification date
  - `tests/property/experiment/__init__.py`
  - `tests/property/experiment/test_lifecycle_props.py` — no transition skips, certification idempotence
  - `tests/integration/experiment/__init__.py`
  - `tests/integration/experiment/test_experiment_e2e.py` — sign → backtest_certify → shadow_certify flow
- **Depends on specs**: 2 (fill engine for backtest execution), 5 (SwingStrategyAdapter is the strategy under protocol), 6 (CalibrationService + ShadowModeStatus as the internal gate).
- **Blocks specs**: Nothing (this is the outermost shell, no downstream engine specs).
- **Estimated LOC delta (net add)**: ~1 600 (domain+adapters+CLI ~700 + tests ~900).
- **Risk**: MEDIUM — the domain model is mostly new but draws heavily on the pre-registration document's explicit structure. The riskiest piece is the OOS data wall: it must physically prevent the engine from reading OOS data, which requires the `HistoricalMarketDataAdapter` (Spec 4) to honor a date cutoff at the port boundary. If Spec 4 doesn't reserve a hook for this, Spec 9 must retrofit. Recommend: add a `oos_cutoff: datetime | None` parameter to `HistoricalMarketDataAdapter` in Spec 4's design.
- **Notes**:
  - The pre-registration document (`pre-registration-v1.md`) is PENDING_SIGNATURE. Spec 9 implementation should NOT begin until the document is signed (SHA256 hash committed). The signed document IS the spec input.
  - The `data-manifest.json` (SHA256 fingerprints of Parquet/SQLite data files) must be created when data ingest begins. This is operator responsibility, not an engine deliverable — but the lab notebook writer must reference the manifest path.
  - Companion documents listed in pre-registration §14 (`lab-notebook.md`, `fee-history.csv`, `post-mortem-template.md`) are documentation/process artifacts. The engine enforces the protocol around them but does not generate them — except `lab-notebook.md` (the lab notebook writer creates the file, operator populates entries).
  - Deflated Sharpe Ratio (DSR) implementation for OOS certification: `scipy.stats` or a custom implementation. If Spec 6 already pulls in `scipy`, reuse it here. The DSR formula from López de Prado (2014) requires: sample Sharpe, autocorrelation correction, skewness/kurtosis correction, number-of-trials penalty. This is non-trivial — ensure statistical expertise is applied.

---

## Appendix: Known Stubs and Blockers

| File | Issue | Status |
| ------ | ------- | -------- |
| `src/hermes/ports/exchange.py` | #199 — ExchangePort has zero methods | Confirmed empty stub; filled in Spec 2 |
| `src/hermes/adapters/clock/__init__.py` | No ClockPort implementations exist | 0-byte file; SimulatedClock + WallClockClock created in Spec 4 |
| `src/hermes/domain/aggregates/` | Entire directory missing | Created in Spec 1 |
| `src/hermes/domain/engine/` | Entire directory missing | Created across Specs 2, 3, 4 |
| `src/hermes/domain/strategies/` | Entire directory missing | Created in Spec 5 |
| `src/hermes/domain/calibration/` | Entire directory missing | Created in Spec 6 |
| `src/hermes/domain/risk_overlay/` | Entire directory missing | Created in Spec 8 |
| `src/hermes/domain/experiment/` | Entire directory missing | Created in Spec 9 |

## Appendix: Dependency-check on `pyproject.toml`

Probable missing dependencies that specs will require (verify before each spec begins design):

| Dependency | Required by | Already present? |
| ------------ | ------------- | ----------------- |
| `ulid-py` or `python-ulid` | Spec 1 (`OrderId`, `FillId` as ULIDs) | Likely NO |
| `scipy` | Spec 6 (KS test, Wasserstein) + Spec 9 (DSR) | Likely NO |
| `numpy` | Spec 6 (statistical metrics) | Likely NO (check; may come via other dep) |
| `websockets` or `aiohttp` | Spec 4 (Binance/Kraken WebSocket adapters) | Likely NO |
| `ta-lib` / `pandas-ta` | Spec 5 (indicator reference values in tests) | Likely NO — test-only |
