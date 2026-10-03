# ADR-011: Paper-trading engine — hexagonal fill kernel with same-code-path backtest / shadow / live parity, bucket-aware domain, calibration as CI gate, risk overlay, and pre-registered Strategy Development Protocol

**Date**: 2026-08-25
**Status**: Accepted — confirmed by Ricardo on 2026-10-03; text merged unchanged via #231 on 2026-08-26
**Deciders**: Ricardo Afonso (product owner, theorist), Claude (proposer, synthesizer)
**Supersedes**: —

---

## Context

### Why this ADR is THE foundational subsystem

The paper-trading engine is the load-bearing wall of hermes. Every other subsystem — signal engines, notifications, LLM supervisor, deployment, observability — presumes an engine that can (a) simulate fills accurately enough to model real execution, (b) run identical code across backtest, shadow, and live modes so behavior observed in one mode is evidence for behavior in the others, and (c) leave an auditable trail dense enough to reconstruct any historical decision. Without this foundation, everything above it is speculation.

The synthesis phrase is direct: **no product without this**. `decision/paper-trading-foundational` locks this in.

### What breaks if we get it wrong

Three classes of failure are documented in the research vault, each with primary sources:

1. **Fill divergence in production** — Quantopian ran a live simulator for ~200k users and shut down partly because paper fills systematically diverged from realized fills; SSRN 2745220 documents the sample-selection bias in the backtest-live gap. Freqtrade issues #12662 and #12675 (open, resolved-by-documentation-not-code, SA-0 §3, SA-2 §1.3) show 5-15% loss discrepancies from `if dry_run:` branches in the fill path. This is the modal failure mode: the engine looks accurate in backtest, then bleeds real money live.
2. **Deployment invariant failure** — Knight Capital lost $460M in 45 minutes (SA-4) because a deployment left one server running a decommissioned code path with no automated verification. The lesson is not "test more"; it is that any code path that runs in production without an automated gate is a Knight Capital candidate.
3. **Silent state drift** — FTX collapsed partly because withdrawable balance and internal ledger diverged and nobody had a reconciliation invariant that noticed (SA-4). For a solo-operator bot, the analogous failure is the balance-vs-open-lots invariant silently drifting until a live fill produces a nonsense position.

### The question this ADR answers

Given ten sub-agent reports (SA-0 landscape, SA-1 academic, SA-2 deep code, SA-3 microstructure, SA-4 post-mortems, SA-5 calibration, SA-6 multi-strategy, SA-7 strategy design, SA-8 reproducibility, SA-9 portfolio shocks, SA-10 Python perf + testing), a locked decision framework, and a signed-and-pending pre-registration for the first live experiment: **what architecture do we commit to for the engine, and what does it lock in versus leave flexible?**

The answer is a single hexagonal engine where backtest, shadow, and live share ONE `FillModelPort` code path and differ only in which adapters get injected at the composition root; where every domain aggregate carries `strategy_id` from day 1 so MVP2's Portfolio Balancer plugs in as a second `StrategyAdapter` without a schema change; where calibration is an automated CI gate that BLOCKS live deployment on drift; where the risk overlay from SA-9 catches portfolio-level shocks that bypass individual stop-losses; and where the Strategy Development Protocol from the pre-registration is the immutable scientific contract for what "the strategy works" means.

### Why an ADR, not a decisions-journal entry

Per `docs/decisions/README.md`, an ADR is required when the choice is:

- architectural, affecting multiple modules (this touches domain, ports, adapters, application, infrastructure, testing, and CI)
- one-way trade-off with high blast radius (rewriting after the fact means throwing away the shadow-capture calibration data)
- reversal requires a migration or breaking change (bucket-aware aggregates cannot be retrofitted without a full event-log replay)
- cross-references multiple existing ADRs (this touches ADR-003, 004, 007, 008, 009, 010)

The 7 focused specs that implement this ADR are per-spec deliverables and will each ship with either an openspec change or a decisions-journal entry as appropriate. This ADR is the umbrella.

---

## Decision

Hermes commits to a **hexagonal paper-trading engine** organized around a single canonical fill kernel, exposed via three modes that differ only in which adapters get injected at composition root:

| Mode | `MarketDataPort` | `ClockPort` | `ExchangePort` | Fill logic |
| ------ | ----------------- | ------------- | --------------- | ------------ |
| **Backtest** | `HistoricalMarketDataAdapter` | `SimulatedClock` | `SimulatedExchangeAdapter` | `FillModelPort` (single impl) |
| **Shadow** | `LiveWebSocketMarketDataAdapter` | `WallClockClock` | `SimulatedExchangeAdapter` | Same `FillModelPort` |
| **Live** (MVP2) | `LiveWebSocketMarketDataAdapter` | `WallClockClock` | `RealExchangeAdapter` (Binance/Kraken) | Bypasses `FillModelPort` — real broker |

The engine is bucket-aware from day 1 (`strategy_id` propagated across `Order → Fill → Position → Balance → Portfolio`), realizes the hybrid time model locked in `architecture/time-model-hybrid` (event-driven fills, bar-close signals), stores every domain event with `event_seq` + `payload_sha256` for deterministic replay (SA-8), gates live deployment on a `CERTIFIED / UNTRUSTED` state machine derived from six P0 calibration metrics (SA-5), and layers a risk overlay on top (SA-9) that catches portfolio-level shocks individual stop-losses cannot see.

Delivery is umbrella ADR + seven focused specs (SYNTHESIS §4): Spec 1 Domain Aggregates, Spec 2 FillModelPort + SimulatedExchangeAdapter, Spec 3 OrderRouter, Spec 4 MarketDataPort + ClockPort, Spec 5 StrategyContract + SwingStrategyAdapter, Spec 6 Calibration Harness, Spec 7 Test Pyramid Manifest. An **eighth spec** (Spec 8 Risk Overlay) is added to this ADR to own SA-9's eight MVP1 shock defenses. Landing sequence and dependencies are per SYNTHESIS §5.

Twenty-two decisions are locked below. Each has a **choice**, **alternatives considered**, **evidence citation** (SA-N §M or research topic key), and **consequences**.

---

### D-01: Single `FillModelPort` code path across all modes

**Choice**: The fill computation is exactly one function, `FillModelPort.process_fill(ctx: MarketDataContext) -> FillResult`. There is no `if mode == "backtest":` or `if dry_run:` branch anywhere inside the fill logic. Mode selection happens ONLY at the composition root by picking which `MarketDataPort` + `ExchangePort` + `ClockPort` triple to inject.

**Alternatives considered**:

- **Duplicate fill implementations per mode** (Freqtrade pattern): rejected — SA-2 §1.3 catalogues 15+ `if dry_run:` branches in Freqtrade's `exchange.py` that produce the exact backtest-live divergence issues #12662 and #12675 document. Divergence becomes architectural, not fixable.
- **`if mode:` branches inside a single fill function**: rejected — the branches accumulate over time; every new mode adds a branch to every existing path; the "single function" claim becomes cosmetic. SA-2 AP-2 forbids this explicitly.

**Evidence**: SA-2 §2 (comparison matrix: NautilusTrader + LEAN achieve genuine parity via port-swap; Freqtrade + Zipline diverge), SA-2 §4 AP-2 (anti-pattern taxonomy), SA-0 §3 (Freqtrade issues #12662, #12675).

**Consequences**: Enables MVP2 live trading to reuse 100% of shadow-mode fill logic. Enforces a single locus of change when fill semantics evolve. Requires import-linter contract (D-20 / Spec 7) to structurally prevent regressions. Forbids the "quick hack" of a mode-specific override — every fill-semantics change lands in one place.

---

### D-02: Adapter composition at the root; mode selection swaps ports, not branches logic

**Choice**: The composition root (a `compose_engine(mode: EngineMode, config: HermesConfig) -> Engine` factory in the application layer) picks concrete adapters based on `HERMES_ENV` + `HERMES_SHADOW` + `HERMES_ALLOW_MAINNET`. Downstream code sees only ports; the mode is invisible below the composition root.

**Alternatives considered**:

- **`ShadowGate` decorator wrapping write methods** (the pattern currently drafted in issue #196): rejected per SYNTHESIS T-4 — this is the anti-pattern SA-2 AP-2 forbids in disguise. The decorator injects an `if shadow:` decision INSIDE the write path.
- **Runtime feature flag checked in domain services**: rejected — domain must not know about deployment modes.

**Evidence**: SA-2 §2 (LEAN `BacktestingBrokerage` vs `PaperBrokerModel` vs live broker pattern), SYNTHESIS §2 T-4 resolution.

**Consequences**: Issue #196 must be amended to adopt port-swap (OD-2 in SYNTHESIS). Issue #175 is retitled to reflect composition-root selection (OD-3). The 3-gate config invariant (`HERMES_ENV + HERMES_SHADOW + HERMES_ALLOW_MAINNET`) survives as a startup validator in `HermesConfig`, not as a per-call decorator. Enables trivial addition of new modes (e.g., a future `paper_trading_with_real_fills` diagnostic mode) by wiring a new adapter triple.

---

### D-03: `strategy_id` field on Order / Fill / Position / Balance / Portfolio from day 1

**Choice**: Every domain aggregate and every domain event carries a `strategy_id: StrategyId` (e.g., `StrategyId("swing-donchian-4h")`). `Order.strategy_id` is set at submission; `Fill.strategy_id` is copied from `Order` at fill time; `Position.strategy_id` is set at opening; `Balance` is partitioned per `(StrategyId, Currency)` tuple; `Portfolio.strategy_pnl()` returns per-strategy PnL from the ledger.

**Alternatives considered**:

- **Add `strategy_id` in MVP2 when the second bucket lands**: rejected — SA-6 finding 1 shows NautilusTrader made `Position.strategy_id: StrategyId` first-class from the start; LEAN's insight-level-only attribution (finding 4) is measurably weaker downstream. Retrofitting requires a full event-log replay to backfill the field; at MVP1 shadow scale (~30 fills/day) the cost is trivial, but at MVP2 scale it becomes a migration cliff.
- **Global `strategy_id` context via contextvar or middleware**: rejected — implicit context breaks reproducibility (SA-8) and audit-trail clarity.

**Evidence**: SA-6 finding 1 (NautilusTrader `Position.strategy_id`), SA-6 finding 4 (LEAN insight-only attribution weakness), `architecture/portfolio-multi-balance`.

**Consequences**: MVP2 Portfolio Balancer plugs in as a second `StrategyAdapter` with no domain-schema change. Every SQL constraint has a `strategy_id NOT NULL` clause. Audit exports (Modelo 172 / 721 per SA-9 §6.2) can be filtered by strategy trivially. Requires migration discipline: no `strategy_id`-less events may ever enter the ledger (schema-enforced).

---

### D-04: `capital_budget: dict[StrategyId, Money]` in `RiskEngine`

**Choice**: The `RiskEngine` holds a per-strategy capital budget. A strategy cannot open positions whose aggregate notional exceeds `capital_budget[strategy_id]`. For MVP1, there is one entry: `{StrategyId("swing-donchian-4h"): Money("5000", "USDT")}` matching the pre-registration §2 experimental capital.

**Alternatives considered**:

- **Global `max_notional_per_order`** (NautilusTrader pattern, SA-6 finding 3): rejected — global limits do not distinguish "the Swing bucket is full" from "the Balancer bucket is full"; a runaway strategy consumes the entire portfolio.
- **No budget enforcement, rely on operator discipline**: rejected — the pre-registration §5.4 hard-codes 5% notional cap per position; enforcement belongs in the engine, not in the operator's head.

**Evidence**: SA-6 finding 3 (NautilusTrader gap), pre-registration §2 + §5.4, `architecture/portfolio-multi-balance`.

**Consequences**: Capital allocation across buckets is an explicit config surface. MVP1 = one bucket; MVP2 adds the Balancer bucket with its own budget line. Enforcement is a `RiskDecision` returned by `RiskEngine.check()` before dispatch to the exchange — no post-fill enforcement (which would leave a position open).

---

### D-05: `OmsType` per strategy (NETTING for Swing MVP1)

**Choice**: Every `StrategyConfig` declares an `oms_type: OmsType ∈ {NETTING, HEDGING}`. Swing MVP1 uses `NETTING` (one net position per instrument per strategy — buying more BTC while already long adds to the position; selling more than open closes and shorts). MVP2 may introduce `HEDGING` for the Balancer if simultaneous long+short exposure is required.

**Alternatives considered**:

- **Implicit HEDGING everywhere**: rejected — introduces MTF-style position accounting complexity we do not need for a long-only spot swing strategy.
- **Global config, single OMS for all strategies**: rejected — MVP2 balancer may need HEDGING while Swing stays NETTING; per-strategy is the correct grain.

**Evidence**: SA-6 (NautilusTrader `OmsType` per-strategy pattern).

**Consequences**: `Position` aggregate has a signed `qty` field. NETTING invariant: at most one open `Position` per `(strategy_id, instrument_id)` tuple. Enforced structurally in the `Portfolio` aggregate.

---

### D-06: Hybrid time model — `raw_events` for fills, `consolidated_bars` for signals

**Choice**: `MarketDataPort` exposes two channels: `subscribe_raw_events(instrument)` yields L2 orderbook diffs + trade prints in strict timestamp order (consumed by `FillModelPort`); `subscribe_bars(instrument, timeframe)` yields bar-close events (consumed by `StrategyPort`). A `Consolidator` domain service aggregates raw events into bars. Fills are event-driven; signals are bar-close-driven.

**Alternatives considered**:

- **Pure event-driven** (every fill AND every signal reacts to each tick): rejected — Swing signals fire at most twice per day per pair; running Donchian(20) on every tick is wasteful and introduces intra-bar look-ahead bugs (SA-2 AP-1).
- **Pure bar-driven** (fills also simulated at bar close): rejected — bar-based fills cannot model orderbook walk, cannot compute VWAP realistically, and break the same-code-path guarantee (a live fill on a real exchange is event-driven no matter what).

**Evidence**: `architecture/time-model-hybrid` (locked), SA-2 §1.2 (LEAN Consolidator pattern), SA-7 (Swing operates on 4H bars).

**Consequences**: Two channels through one port. Strategies never see raw events; fill logic never sees consolidated bars. The `Consolidator` is domain, not adapter — it must be deterministic (SA-8).

---

### D-07: `MarketDataContext.orderbook: Optional[OrderbookSnapshot]` — same code path for real L2 and synthetic

**Choice**: The `FillModelPort` accepts a `MarketDataContext` whose `orderbook` field may be `None` (backtest OHLCV-only path) or a real `OrderbookSnapshot` (shadow / historical L2). When `orderbook is None`, the `OrderbookWalkFillModel` synthesizes a book from bar OHLCV using a documented reconstruction (bid = close - spread/2; ask = close + spread/2; depth = a fraction of bar volume distributed across N levels). When `orderbook` is present, the model walks it directly.

**Alternatives considered**:

- **Two separate fill models, one per data type**: rejected — violates D-01 (single code path). Two models drift over time.
- **Refuse to backtest without real L2**: rejected — OD-1 open decision in SYNTHESIS acknowledges neither Binance nor Kraken provides historical L2 publicly. Blocking backtest until 30-60 days of shadow-capture data accumulates delays MVP1 by 2 months. Pre-registration §6.2 explicitly picks OHLCV backtest + concurrent L2 collector.

**Evidence**: SA-2 §3 (`FillModelPort` shape), SA-3 §1.9 (no public historical L2), pre-registration §6.2 (OD-1 resolution Option C + Option B).

**Consequences**: Backtest fidelity is LOW-to-MEDIUM (bar-derived synthetic book, ±10% accuracy per SYNTHESIS OD-1). Shadow fidelity is HIGH (real L2 walked). Live path bypasses the model entirely. The synthetic-book reconstruction is documented in Spec 2 and MUST be deterministic under seed.

---

### D-08: `OrderbookWalkFillModel` is the MVP1 fill model

**Choice**: The concrete `FillModelPort` implementation for MVP1 is `OrderbookWalkFillModel` — walks the L2 book (real or synthetic) accumulating fill quantity at each level's price until the order quantity is exhausted or the book runs out. Returns VWAP fill price, filled quantity, per-level fill breakdown, and maker/taker classification (limit orders crossing the touch at post time are taker; limit orders resting at or better than the touch are maker).

**Alternatives considered**:

- **Constant-slippage model** (Freqtrade-style hardcoded 5% slippage): rejected — SA-2 AP-4 catalogues this as a documented failure mode; slippage varies from 0.5 bps (BTC $100 order) to 500 bps (SOL $50k order into thin book).
- **Bar-close fill at close price with fixed spread adjustment**: rejected — cannot model partial fills, cannot model liquidity-vacuum blowouts (SA-9 §3.2), does not converge to the live-execution physics we are trying to match.
- **Probabilistic queue model** (SA-1 §2.5 Cont-Kukanov): deferred to MVP2 — SA-0 §5, SA-3 §1.6, SA-5 §8 all agree queue position is irrelevant for retail swing size on liquid pairs; classified P2.

**Evidence**: SA-1 §3, SA-2 §3, SA-3 §2 P0.3.

**Consequences**: Slippage EMERGES from the walk — there is no separate "slippage" constant to calibrate. This is the correct abstraction. Requires the fee model to be a separate injectable port (D-09) so fill and fee logic evolve independently.

---

### D-09: `MakerTakerFeeModel` with tier-aware Binance (VIP0) and Kraken defaults, BNB discount OFF

**Choice**: `FeeModelPort` is a separate injectable port. The MVP1 implementation is `MakerTakerFeeModel` with per-exchange tier tables. Binance defaults to VIP0 tier (0.10% maker / 0.10% taker), BNB discount OFF (conservative, matches pre-registration §6.1). Kraken defaults to Intermediate tier (0.25% maker / 0.40% taker, SA-3 §1.2). Fees are computed as `notional × rate` per fill, rounded per exchange's per-symbol rounding rules.

**Alternatives considered**:

- **Point-in-time historical fee schedule** (reconstruct fees from Wayback Machine per pre-registration §6.1): the pre-registration explicitly requires this for the backtest fee history. This is IN scope for the fee model but requires an external `fee-history.csv` data file. The model must accept a `fee_schedule_at(timestamp) -> FeeSchedule` callable so backtest replay uses the historical rate for each trade, not today's rate.
- **Flat fee constant**: rejected — pre-registration §9 secondary metric "total fees / gross PnL < 30%" fails immediately if fees are miscalibrated; a stale rate silently biases the strategy toward or against certification.
- **BNB discount ON**: rejected — the pre-registration explicitly picks "BNB OFF, conservative". A live-mode operator can re-enable it via config; the backtest MUST match the pre-registration.

**Evidence**: SA-3 §1.2 (Kraken tier tables), SA-3 §1.2 (Binance VIP0 rates), pre-registration §6.1 (VIP0 + BNB OFF), Kraken public adapter journal entry (`docs/decisions/2026-08-24-kraken-public-adapter.md`).

**Consequences**: `KrakenPublicAdapter.get_fee_schedule()` (already shipped, PR #193) feeds the Kraken side. Binance requires a fee-schedule endpoint or a reconstructed CSV. Fee rate is per-fill (not per-order) — a limit order that fills half maker + half taker gets two commissions.

---

### D-10: Next-bar-open semantics for market orders (SA-2 AP-1 defense)

**Choice**: A market order submitted at bar-close time T fills at the OPEN of bar T+1, never at the close of bar T. This is the anti-Zipline rule from SA-2 AP-1. Enforced structurally by the `FillModelPort` refusing a `MarketDataContext` whose `bar.ts == order.submitted_at.floor_to_bar()`.

**Alternatives considered**:

- **Fill at bar close of the signal bar**: rejected — this is the Zipline bug that inflated backtest returns by 15-30% (SA-2 §1.4). The signal is generated ON the bar, so the strategy has seen the close price — filling at that close is lookahead bias.
- **Fill at bar close plus a fixed spread**: rejected — still lookahead; the strategy already knows the close.

**Evidence**: SA-2 §4 AP-1 (anti-Zipline), Zipline post-mortem (SA-0 §3).

**Consequences**: The `Consolidator` MUST emit a `bar_closed` event AFTER the bar's timestamp has fully elapsed. The `StrategyPort.on_bar()` receives a bar whose `ts_close < now()`. The `OrderRouter.submit()` records `submitted_at = clock.now()`. The `FillModelPort` refuses fills where `bar.ts_open ≤ order.submitted_at`. Structural rejection means backtest and shadow cannot accidentally produce identical lookahead bugs.

---

### D-11: Limit orders fill when price crosses the limit within a future bar; intrabar ordering via bar-open proximity heuristic

**Choice**: A limit order submitted at bar-close time T rests in the book from bar T+1 forward. It fills when a subsequent bar's price range crosses the limit price. For each bar considered, the intrabar order of events is deterministic: if the bar's `open` is closer to the limit than the `close`, price is assumed to have touched the limit early in the bar; otherwise late. Stops use the opposite convention (assume the worst-case intrabar sequence).

**Alternatives considered**:

- **Random intrabar ordering**: rejected — breaks reproducibility (SA-8).
- **Always fill at bar open**: rejected — a limit far from open never fills; a limit close to open always fills; loses precision.
- **Fill at limit price exactly regardless of bar range**: rejected — implies zero slippage on a bar that gapped past the limit; produces the Zipline-class inflation SA-2 documents.

**Evidence**: SA-2 §6 (intrabar ordering discipline).

**Consequences**: The heuristic is documented, deterministic, and testable. Golden-replay fixtures (SA-10 §10) pin the expected intrabar decisions per test. Any change to the heuristic is a breaking change and requires re-running calibration.

---

### D-12: MVP1 signal stack = Donchian(20) 4H + Chandelier(22, 3.0) + ATR sizing 1% risk / 5% cap (aligned with pre-registration §5)

**Choice**: The Swing strategy for MVP1 is:

- **Entry** (long-only): close > Donchian(20) high on 4H timeframe AND close > EMA(200) daily AND ADX(14) ≥ 25 on 4H
- **Exit**: on-exchange stop-market at Chandelier(22, 3.0) trailing stop level, OR time-stop at 90 days if not stopped out, OR regime break (close < EMA(200) daily)
- **Order types**: entry = market at bar close of signal bar (fills at next bar open per D-10); exit = on-exchange stop-market posted immediately after entry fill and moved daily
- **Sizing**: `risk_per_trade = 0.01 × experiment_capital = $50`; `qty = risk_per_trade / (entry_px - chandelier_stop_px)`; hard cap at 5% of experiment capital per position ($250 notional)
- **Concurrency**: max 3 concurrent positions (one per pair BTC/ETH/SOL); correlation lock at 0.80 (D-21 / RO-01); total exposure cap 15% of capital

Note: 5% supersedes SA-7's original 10% cap. Both SYNTHESIS D-12 and this ADR now align with the pre-registration §5.4 binding value.

**Alternatives considered**:

- **SA-7's original 10% cap + Chandelier(22, 3.0) with entry limit at high + 0.1×ATR**: superseded by pre-registration §5.4 (5% cap) and §5.3 (market entry at bar close, not limit). The pre-registration is the binding scientific contract.
- **Larger Donchian period (50, 100)**: not tested in the pre-registration; would require a v2 pre-registration to change.
- **Include short entries**: excluded by pre-registration §5.1 ("long-only for MVP1").

**Evidence**: SA-7 recommended stack (Donchian + ADX/EMA/ATR filters), pre-registration §5 (frozen strategy spec), SYNTHESIS §2 T-3 (fill semantics per order type).

**Consequences**: The `SwingStrategyAdapter` implementation is a mechanical translation of pre-registration §5. Any deviation from these parameters at implementation time triggers pre-registration §11 "counts as deviation → invalidates". The engine must be able to produce identical signals on identical bars for a given parameter set (Hypothesis test).

---

### D-13: Position sizing = ATR-baseline capped by fractional Kelly (0.25× f*) or 5% hard cap, whichever is smaller — Kelly deferred until ≥30 OOS trades

**Choice**: The `SizingCalculator` composes SA-7's ATR sizing with SA-1's fractional Kelly cap per SYNTHESIS §2 T-1:

```text
raw_qty     = (equity × 0.01) / (2.0 × ATR(14))             # SA-7 ATR baseline
kelly_qty   = (equity × min(0.25 × f_star, 0.05)) / entry   # SA-1 fractional Kelly cap
notional    = min(raw_qty × entry, kelly_qty × entry, 0.05 × equity)  # PRE-REG 5% cap
qty         = notional / entry
```

For MVP1, `f_star` is `None` (no OOS trades yet), so the Kelly term degenerates to the 5% hard cap. Effective MVP1 sizing = ATR baseline capped at 5% of experiment capital. Kelly activates only after ≥30 OOS trades exist (SYNTHESIS OD-4, pre-registration §5.4).

**Alternatives considered**:

- **Fixed fractional (2% risk-per-trade regardless of ATR)**: rejected — does not adapt to volatility; a high-ATR pair takes the same nominal risk but wildly different equity risk after slippage.
- **Full Kelly**: rejected — full Kelly at f* maximizes E[log W] but produces 50%+ drawdowns routinely; 0.25× Kelly sacrifices ~25% growth for ~75% drawdown reduction (SA-1 §6).
- **Volatility-targeting (target portfolio vol = X%)**: deferred to MVP2 — requires cross-pair correlation matrix and is not in the pre-registration.

**Evidence**: SYNTHESIS §2 T-1 resolution, SA-1 §6 (fractional Kelly), SA-7 (ATR sizing), pre-registration §5.4 (5% cap, Kelly deferred).

**Consequences**: MVP1 sizing is fully deterministic given equity + ATR + entry price. Kelly hook is present but inert. The `f_star_estimate` field on `StrategySignal` (D-14) exists so a future Kelly estimator can flip the mechanism on without a domain change.

---

### D-14: On-exchange stop-market as default stop mechanism; drawdown circuit-breaker as flash-crash defense

**Choice**: Stop-loss orders are posted on-exchange as stop-market orders (not managed by the bot). Rationale: bot-managed stops are a single point of failure — if hermes crashes, positions run naked (SA-7 explicit warning). On-exchange stops survive bot outages.

The flash-crash risk (SA-4: Kraken BTC/CAD 99% slippage in May 2019) is addressed ORTHOGONALLY by (a) bucket-level drawdown circuit-breaker (peak drawdown -20% → close all + halt), (b) stop-price validation (reject stop-market with stop price > N × ATR from current — implies severe adverse move already in progress), (c) ATR-spike kill switch (SA-7 already, reinforced by SA-9 §2.5 / RO-08).

**Alternatives considered**:

- **Bot-managed stops only**: rejected — SA-7 SEV-1 warning. Bot crash = no protection.
- **On-exchange stop-limit** (limit price below stop): rejected — during flash crash, price gaps through the limit and the stop never fills. Accepting slippage on a stop-market is better than a hung stop-limit.

**Evidence**: SA-7 (on-exchange preference, non-fill risk), SA-4 (Kraken May 2019 flash crash), SYNTHESIS §2 T-7 (orthogonal resolution), pre-registration §5.2 (stop-market at Chandelier level, posted immediately after entry).

**Consequences**: Stop-market fills at prevailing bid during flash crashes — accepted trade-off. Composite protection layers (circuit-breaker + ATR filter + stop-price validation) catch the systemic events. The `RiskEngine.check()` rejects a stop-market order whose stop price is more than a configured multiple of ATR away from current (default 5×).

---

### D-15: `ClockPort` injected in every domain service — no direct `datetime.now()` in domain code

**Choice**: A `ClockPort` protocol (`now() -> datetime`, `now_ns() -> int`, both UTC) is injected into every domain service that needs a timestamp. Two concrete impls: `WallClockClock` (production) and `SimulatedClock` (backtest, advances deterministically along a bar sequence). Domain code never calls `datetime.now()`, `time.time()`, `time.time_ns()`, or any wall-clock function directly. An import-linter contract (Spec 7) enforces this structurally.

**Alternatives considered**:

- **Use `freezegun` in tests only**: rejected — leaves direct `datetime.now()` calls in production code, which then work in tests but are non-deterministic in production replay.
- **Contextvar-based clock**: rejected — implicit context is fragile; explicit dependency injection is the SA-8 discipline.

**Evidence**: SA-8 recommendation S-01 (ClockPort injection).

**Consequences**: Every backtest produces bit-identical event sequences under the same seed. Shadow-mode divergence has ONE possible root cause (wall-clock jitter) — not "we forgot to inject the clock somewhere". Replay debugging is deterministic. Requires the import-linter contract to catch regressions at PR time.

---

### D-16: `event_seq = SQLite rowid`, `payload_sha256 = SHA-256(sort_keys=True JSON)`, append-only triggers

**Choice**: Every domain event stored in the ledger has:

- `event_seq: int` = the SQLite `rowid` of the event's row (monotonic per-session, may reset across sessions but is unique-within-session; a `session_id` field disambiguates)
- `payload_sha256: str` = SHA-256 of `json.dumps(payload, sort_keys=True, default=<Decimal→str, datetime→isoformat>)`
- Append-only enforcement via SQLite triggers that raise on UPDATE or DELETE against the events table
- Batched WAL writes via `PRAGMA synchronous=NORMAL` + `PRAGMA journal_mode=WAL` + `PRAGMA wal_autocheckpoint=1000` (SA-10 §6.1)

**Alternatives considered**:

- **UUID-based event_seq**: rejected — non-monotonic; cannot order events without a separate timestamp field that itself has determinism problems.
- **`msgpack` for payload serialization**: rejected — SA-10 §5.1 shows canonical `msgpack` is non-trivial; `json` + `sort_keys=True` gives deterministic SHA-256 out of the box.
- **`pickle`**: rejected — non-deterministic across Python versions, security risk on load, cannot be human-audited.

**Evidence**: SA-8 architecture recommendations 1, 3, SA-10 §5.2 (deterministic serialization), SA-10 §6.1 (WAL pragmas).

**Consequences**: Two replays of the same session produce identical `payload_sha256` for each event — a mismatch is a bug. `event_seq` is unique-per-session; cross-session ordering uses `(session_id, event_seq)`. The append-only trigger prevents even a bug from mutating history. Batched commits handle the shadow-mode event rate (~10 events/sec per pair per SA-3 §3, well under SQLite's 10k writes/sec ceiling).

---

### D-17: `RecordedExchangePort` logs `ExternalCallRecorded` events for deterministic replay

**Choice**: Every call from a live exchange adapter to a remote endpoint is wrapped by a `RecordedExchangePort` decorator that logs an `ExternalCallRecorded` domain event containing (endpoint, request payload, response payload, latency, timestamp, correlation_id). Replay reads these events instead of hitting the wire, enabling deterministic debugging via `hermes replay --from <seq> --to <seq>`.

**Alternatives considered**:

- **VCR-style HTTP cassettes in tests only**: rejected — leaves production runs unrecorded; a bug reproduces only in shadow, not in a test.
- **Full production HTTP capture via mitmproxy**: rejected — adds operational surface; the app-layer decorator is enough.

**Evidence**: SA-8 architecture recommendation 5 (recorded ports for replay).

**Consequences**: Every production shadow-mode run is replayable offline. The event volume from `ExternalCallRecorded` is bounded (a few hundred external calls per day in shadow mode). Sensitive data (API keys in request headers) MUST be redacted before serialization; the decorator strips known-secret fields.

---

### D-18: Calibration harness is a first-class CI gate, not a monitoring dashboard

**Choice**: Six P0 calibration metrics (M-02 fill-price bps divergence, M-04 bias direction, M-09 PnL correlation, M-10 Sharpe match, M-13 lookahead integrity, M-15 fee accuracy) are computed continuously from shadow-mode data. A state machine in SQLite tracks `shadow_mode.status ∈ {PENDING_DATA, CERTIFIED, UNTRUSTED}`. A CI script (`ShadowModeGate.check_deploy_allowed()`) runs before every deploy-to-live and exits non-zero when `status == UNTRUSTED`. Restoration from UNTRUSTED to CERTIFIED requires human approval via an explicit CLI call.

**Alternatives considered**:

- **Grafana dashboard, human eyeball**: rejected — this is a Knight-Capital pattern (SA-4). Manual verification is skipped under time pressure. Automated gate that BLOCKS is the only reliable mechanism.
- **Nightly report, alert on divergence**: rejected — reporting is post-hoc; a blocking gate is pre-hoc.

**Evidence**: SA-5 §10 (certification lifecycle state machine), SA-4 (Knight Capital: no automated invariant checks), SYNTHESIS locked decision 5.

**Consequences**: A shadow-mode run that has not accumulated enough data starts `PENDING_DATA` and blocks deployment automatically. A drift event flips to `UNTRUSTED` and blocks until a human approves the restore. CI must fail loudly (exit non-zero, block merge to `main` for tags that trigger deploy). This is a step-change in operational discipline versus "we'll notice if fills look weird".

---

### D-19: Six P0 calibration metrics with SA-5 thresholds; testnet baseline subtracted from M-02

**Choice**: The six P0 metrics have concrete thresholds (SA-5 §2):

- **M-02** fill-price bps divergence: p50 < 30 bps BLOCK, p95 < 100 bps BLOCK
- **M-04** bias direction: binomial test p-value < 0.01 → BLOCK if sim systematically better than real
- **M-09** PnL correlation: Pearson r > 0.85, bootstrap 95% CI lower bound > 0.70
- **M-10** Sharpe match: |sim_sharpe - real_sharpe| < 0.3 over rolling 30-day window
- **M-13** lookahead integrity: zero violations (any lookahead event → BLOCK immediately, no threshold)
- **M-15** fee accuracy: |sim_fee - real_fee| / real_fee < 5% per fill

Per SYNTHESIS §2 T-5: the M-02 threshold is applied to `bps_divergence - baseline_bps_divergence`, where `baseline` is measured over a 7-day sim-off run at MVP1 launch (testnet mainnet-vs-testnet WebSocket, no fill sim in the loop). If baseline is 8 bps, the effective sim-quality threshold is `sim < baseline + 30 = 38 bps p50 BLOCK`.

**Alternatives considered**:

- **Absolute thresholds without baseline subtraction**: rejected — SA-5 §13 open Q#2 acknowledges testnet has a separate orderbook from mainnet; some divergence is STRUCTURAL, not calibration failure. Absolute thresholds would produce false BLOCKS from a correctly-calibrated model.
- **Fewer metrics** (only M-02): rejected — a fill-price-accurate model with a lookahead bug (missing M-13) or wrong fees (missing M-15) still bleeds real money.
- **All 15 SA-5 metrics as P0**: rejected — M-05 latency and M-07 partial-fill KS test are P2 (SYNTHESIS §2 T-2, T-9); computing them wastes reviewer attention.

**Evidence**: SA-5 §2 (threshold table), SA-5 §8 (P0 tiering), SYNTHESIS §2 T-5 (baseline subtraction resolution).

**Consequences**: The pre-registration §10 shadow phase kill trigger "sim-vs-live PnL divergence > 30 bps p50 sustained 7 rolling days" is EXACTLY M-02 with the baseline-subtracted threshold. Baseline measurement job is a Spec 6 P0 pre-run step (runs once before MVP1 launch, cached; re-runs quarterly to catch testnet drift).

---

### D-20: Test pyramid manifest as acceptance gate every other spec must pass

**Choice**: A `pytest.ini`-defined test tier system with markers (`unit`, `property`, `integration`, `calibration`, `chaos`, `mutation`, `replay`, `contract`) and per-tier CI schedules:

- **Every PR**: `unit` + `property` (<60s per SA-10 §13.2)
- **Nightly**: `chaos` + `replay` (small fixtures) + `contract` (real Binance/Kraken public API) + `property` with `max_examples=10_000`
- **Weekly**: full `mutation` (cosmic-ray on domain modules, kill-rate targets per SA-10 §11.3), calibration harness with recorded shadow data, `atheris` fuzz campaigns

Import-linter contracts (Spec 7) enforce:

1. No `datetime.now()` / `time.time()` in `hermes.domain.*` (structural D-15 enforcement)
2. No `if.*shadow` or `if.*dry_run` grep hits in fill logic modules (structural D-01 enforcement)
3. No cross-strategy imports (structural D-03 enforcement — each strategy is a self-contained module)

Mutation kill-rate targets per SA-10 §11.3: `domain/lots/`, `domain/fees/`, future `domain/fill_model.py` all ≥ 85%; adapters ≥ 80%; infrastructure ≥ 70%. Coverage thresholds per ADR-007: domain ≥ 95%, adapter ≥ 90%, application ≥ 90%.

**Alternatives considered**:

- **Ad-hoc test discipline per spec**: rejected — SYNTHESIS §5 gate-first rationale is precisely to avoid this. Tier definitions renegotiated per PR is Knight Capital risk.
- **Single tier (everything runs everywhere)**: rejected — mutation testing takes hours; blocking every PR on it is unworkable.

**Evidence**: SA-10 §13 (CI pyramid economics), SA-4 (Knight Capital: no automated verification), ADR-007 (existing coverage thresholds), SYNTHESIS §4 Spec 7.

**Consequences**: Spec 7 lands FIRST (with Spec 1 in parallel per SYNTHESIS §5). Every subsequent spec knows its acceptance criteria before starting implementation. Import-linter runs on every PR; a `datetime.now()` in domain fails CI, not code review. Weekly mutation run requires a self-hosted runner (already provisioned per commit 729b959).

---

### D-21: Risk overlay — SA-9's eight MVP1 shock defenses (Spec 8, added to SYNTHESIS §4)

**Choice**: An eighth focused spec, **Spec 8 — Risk Overlay**, owns SA-9's eight MVP1 shock defenses as a distinct concern from `RiskEngine` (which owns capital-budget + per-strategy risk gates). The risk overlay operates at portfolio scope, above individual strategies:

| # | Rule | Threshold | Action | Owner |
| --- | ------ | ----------- | -------- | ------- |
| **RO-01** | Correlation lock | No 2 BTC-correlated positions simultaneously (7-day rolling ρ > 0.80) | Block entry in `SizingCalculator` | Spec 8 |
| **RO-02** | Max slippage (entry) | Simulated slippage > 0.50% | Cancel order; WARN alert | Spec 8 + Spec 2 |
| **RO-03** | Max slippage (exit) | Simulated slippage > 2.00% | WARN; allow exit | Spec 8 + Spec 2 |
| **RO-04** | Spread-spike halt | Spread > 10× 1H rolling median | Halt new market-order entries; CRITICAL alert | Spec 8 + Spec 4 |
| **RO-05** | Stale book guard | Last book update > 2000ms | Mark STALE; block new entries | Spec 8 + Spec 4 |
| **RO-06** | Depeg monitor | USDT or USDC deviation > 1.5% (WARN); > 3% (HALT) | WARN → halt → equity restatement | Spec 8 + Kraken adapter |
| **RO-07** | FOMC blackout | 30 min before / 60 min after known FOMC dates | Suppress new entries; tighten trailing stop to 1.5× ATR | Spec 8 |
| **RO-08** | Multi-pair ATR cascade | ATR spike on 2+ pairs simultaneously | PORTFOLIO_HALT; SYSTEMIC_VOLATILITY alert | Spec 8 |

Each rule is a pure function `evaluate(portfolio_state, market_state, event) -> RiskDecision`. Composed via a `RiskOverlay` domain service that runs BEFORE `RiskEngine.check()` in the `OrderRouter` submit path.

**Alternatives considered**:

- **Fold into `RiskEngine`**: rejected — SA-9's rules are portfolio-scope (correlation lock crosses strategies; depeg monitor affects the entire denomination; FOMC blackout is global). Mixing them with per-strategy capital enforcement muddles two orthogonal concerns.
- **Defer to MVP2**: rejected — the pre-registration §10 shadow-phase kill triggers explicitly reference "Any SA-9 shock overlay rule fires 3+ times in 30 days" and "Any SA-9 P0 rule violation" as HALT conditions. Without the overlay implemented, the kill triggers cannot fire. Also: RO-01 (correlation lock) is the critical pre-entry defense per SA-9 §2.5; skipping it means MVP1 shadow mode is provably unsafe against a March-2020-class event.
- **Only implement RO-01 (correlation lock)**: rejected — the depeg monitor (RO-06) protects the denominator assumption the entire accounting layer depends on (SA-9 §4.2). Cannot ship a system that assumes USDT = $1.00 without a check.

**Evidence**: SA-9 §10 (defense matrix), pre-registration §10 (kill triggers reference SA-9 rules), SA-9 §2 (correlation shock precedents: March 2020, June 2022, Nov 2022, Aug 2024), SA-9 §4 (stablecoin depeg precedents: USDT May 2022, USDC March 2023).

**Consequences**: Adds Spec 8 to the SYNTHESIS §4 seven-spec plan (total = eight). Landing sequence: Spec 8 lands AFTER Spec 3 (OrderRouter) and Spec 4 (MarketDataPort — for spread + book-age signals) but BEFORE MVP1 shadow-mode launch (pre-registration §10 dependency). Uses the Kraken public adapter (PR #193) for USDT/USD and USDC/USD price polling. FOMC calendar is hard-coded at year start (8 dates published by the Federal Reserve). Restoration from `PORTFOLIO_HALT` requires operator manual reset via CLI (no auto-restore).

---

### D-22: Strategy Development Protocol from `pre-registration-v1.md` is the immutable scientific contract (Spec 9)

**Choice**: A ninth focused spec, **Spec 9 — Strategy Development Protocol**, codifies the pre-registration discipline as an engine-enforceable protocol. Specifically:

- **Data window enforcement**: the engine physically refuses to read OOS data (`2025-09-01 → 2026-12-31`) outside an explicit `CERTIFICATION_MODE=true` gate, and refuses certification before `2027-01-01` (pre-registration §4 discipline gate). Read attempts are logged as violations and raise `PreRegistrationViolation`.
- **Universe enforcement**: `STRATEGY_UNIVERSE = frozenset({"BTC/USDT@binance", "ETH/USDT@binance", "SOL/USDT@binance"})`. Any signal referencing an instrument outside this set is rejected at `StrategyPort.on_bar()` return time.
- **Certification lifecycle**: state machine per pre-registration §7 — `UNTRUSTED → BACKTEST_CERTIFIED → SHADOW_CERTIFIED → OOS_CERTIFIED → LIVE_APPROVED → CERTIFIED`. Transitions require explicit CLI calls with justifications logged to the lab notebook.
- **Deviation detection**: a schema-hash of the strategy config (asset universe, data windows, strategy parameters, fee model, sizing, kill triggers) is computed at session start and compared against the pre-registration's committed hash. Mismatch → session refuses to start.
- **Lab notebook**: append-only event-sourced log per pre-registration §12; every parameter tune, metric read on Val window, and deviation proposal is recorded with timestamp + phase + rationale.
- **Metric thresholds**: primary (DSR > 0.7, bootstrap 95% CI Sharpe lower bound > 0, ≥30 OOS trades) and secondary (max drawdown < 15%, ulcer < 10, profit factor > 1.3, win rate ≥ 30%, total fees / gross PnL < 30%, time-to-recovery from ≥8% DD < 90d) evaluated by the calibration harness (D-19).

**Alternatives considered**:

- **Fold into Spec 5 (StrategyContract)**: rejected — the Protocol is meta to any strategy; it enforces HOW strategies are developed, tested, and promoted, not what a specific strategy does. Mixing loses clarity.
- **Documentation-only, no engine enforcement**: rejected — the pre-registration §11 "counts as deviation" clause is meaningless if the engine allows the deviation. Structural enforcement is the anti-p-hacking mechanism.
- **Enforce only universe + windows, defer certification lifecycle**: rejected — the certification lifecycle IS the kill-trigger mechanism (pre-registration §10). Without it, the engine cannot know it should HALT.

**Evidence**: `docs/experiments/swing-mvp1/pre-registration-v1.md` §1–§16 (in full), pre-registration §13 (roles: engine is experimentalist, Claude is integrity checker), SA-5 §10 (certification lifecycle).

**Consequences**: The pre-registration document becomes an engine artifact, not just a lab record. Its SHA-256 hash (once signed) is included in every session's startup log. Every attempt to change a locked field (universe, windows, strategy params, kill triggers) requires signing pre-registration v2 (which INVALIDATES v1 per §11 — a new experiment begins). This is deliberately painful: it is the p-hacking prevention mechanism. Spec 9 lands after Spec 6 (calibration harness — provides the metric machinery) and before OOS certification (2027-01-01). MVP1 launch does not require Spec 9 complete, only the universe + window enforcement pieces; the certification lifecycle can land during shadow phase.

---

## Rejected alternatives (5 explicit, from SYNTHESIS §3)

### R-1: Adopt NautilusTrader wholesale

**Rejected**. NautilusTrader is best-in-class for hexagonal architecture, kernel-based backtest-live parity, and has a mature Rust core. The temptation to adopt it directly is real. Rejected because: (a) the Rust core adds cross-language friction in a Python-first codebase (ADR-003 §04), doubling the maintenance surface for a solo operator; (b) SA-6 finding 2 documents that `strategy_id` attribution on PnL is missing — Nautilus keys by instrument only, weakening MVP2 Balancer semantics; (c) SA-6 finding 3 shows `RiskEngine.max_notional_per_order` is global, not per-strategy — bakes in the exact anti-pattern D-04 rejects; (d) NautilusTrader issue #2194 acknowledges the built-in fill model is basic (no queue simulation, no native partial fills), so we would end up subclassing the fill layer anyway; (e) integration cost (learning the extension points, wiring the composition root, mapping our domain to theirs) exceeds the cost of building a targeted hexagonal engine that fits hermes's specific shape. Evidence: SA-2 §1.1, SA-2 §2, SA-6 findings 1–3, NautilusTrader GitHub issue #2194.

### R-2: Adopt QuantConnect LEAN wholesale

**Rejected**. LEAN has the most mature portfolio construction framework in OSS. Rejected because: (a) C# core — same cross-language friction argument as R-1, worse because C# tooling has less overlap with our Python/uv stack; (b) SA-6 finding 4 shows `PortfolioConstructionModel` performs attribution at INSIGHT level, not POSITION level — weaker than NautilusTrader for MVP2 Balancer semantics and much weaker than the day-1 `strategy_id` on aggregates that D-03 commits to; (c) LEAN issue #4897 documents a crypto zero-slippage bug OPEN since 2020 — a five-year-old known correctness bug on the exact use case we care most about is a red flag about maintenance priorities; (d) cloud-first design — hermes runs on a solo-operator VPS (ADR-003 §09), not a cloud fleet; running LEAN self-hosted is possible but off the well-trodden path. Evidence: SA-2 §1.2, SA-6 finding 4, LEAN GitHub issue #4897.

### R-3: Adopt Freqtrade wholesale

**Rejected**. Freqtrade is the most battle-tested crypto trading framework in OSS with thousands of live users. Rejected because: (a) SA-2 §1.3 catalogues 15+ `if dry_run:` branches in `exchange.py` — this is D-01's forbidden anti-pattern baked into the architecture, not fixable without a rewrite; (b) issues #12662 and #12675 (open, 2023 and 2024 respectively) document real users losing 5-15% live after profitable backtests, and both are marked "resolved by documentation, not code fix" — the maintainers have accepted the divergence as by-design; (c) hardcoded 5% slippage constant (SA-2 AP-4) rather than orderbook-walk; (d) no formal `FillModelPort` abstraction — fill logic is inlined in the exchange adapter, blocking the port-swap pattern D-02 depends on. Adopting Freqtrade means importing an architectural liability that we would spend six months carving out. Evidence: SA-2 §1.3, SA-0 §3 (Freqtrade issues #12662 #12675), SA-2 §4 AP-2 and AP-4.

### R-4: Adopt VectorBT for backtest + custom shadow

**Rejected**. VectorBT is dramatically faster than any event-driven engine for parameter sweeps — it vectorizes fills across thousands of parameter combinations at NumPy speed. Rejected because: (a) the vectorized paradigm fundamentally CANNOT model event-driven fills — it processes bar arrays, not tick streams — and this is a fundamental architectural mismatch with the locked hybrid time model (`architecture/time-model-hybrid`) that D-06 commits to; (b) no live trading in OSS VectorBT (paid VectorBT Pro has limited live) — so we would end up with a two-engine strategy: VectorBT for backtest, custom engine for shadow/live; (c) a two-engine strategy REINTRODUCES the exact divergence problem D-01 is designed to prevent — backtest signals generated by VectorBT would need to be re-validated against the custom engine before shipping, and any drift is a Freqtrade-class problem. VectorBT is the right tool for parameter sweeps as a research adjunct (SA-10 §3.4 notes it uses Numba for exactly this reason), not as the production backtest engine. Evidence: SA-2 §1.6, SA-0 landscape.

### R-5: Build a single mega-spec + one giant PR

**Rejected**. Every claim in this ADR could be delivered as one enormous specification and one enormous PR. Rejected because: (a) too many concerns coupled in review — a reviewer cannot meaningfully approve the domain aggregates, the fill model, the risk overlay, the calibration harness, and the strategy protocol in one sitting; the review becomes rubber-stamping or infinite; (b) the test pyramid (D-20 / Spec 7) cannot be validated incrementally against a mega-PR — the acceptance gate can only fire once, at the end, after all the code exists, and any failure blocks EVERYTHING; (c) violates the locked decision `decision/paper-trading-foundational` §3 which explicitly picks the "split by concern" approach; (d) SYNTHESIS §5 documents an explicit dependency DAG (Spec 7 + Spec 1 first; Spec 4 next; Spec 2, 3, 5, 6, 8, 9 after) — this parallelism is impossible in a mega-spec approach. Every focused spec gets its own openspec change, its own reviewable PR (or series), and its own acceptance gate against the test pyramid manifest. Evidence: `decision/paper-trading-foundational` §3, SYNTHESIS §5 dependency DAG.

---

## Consistency with pre-registration v1

This ADR is designed to be structurally compatible with the pre-registration document at `docs/experiments/swing-mvp1/pre-registration-v1.md` (v1, pending signature). Key consistency checks:

**Data windows** (pre-registration §4 vs D-22):

- Train: 2020-09-01 → 2024-06-30 — enforced by Spec 9 file-permission gate (uniform start across BTC/ETH/SOL per pre-reg v1 §4)
- Validation: 2024-07-01 → 2025-08-31 — enforced by Spec 9 (every read logged to lab notebook)
- OOS Test: 2025-09-01 → 2026-12-31 — enforced by Spec 9 (physical read refusal outside `CERTIFICATION_MODE`)
- Certification cannot begin before 2027-01-01 — enforced by Spec 9 clock check (uses `ClockPort` per D-15)

**Universe** (pre-registration §3 vs D-22):

- BTC/USDT, ETH/USDT, SOL/USDT on Binance spot — hard-coded `STRATEGY_UNIVERSE` frozenset in Spec 9
- Binance-only for MVP1 (OD-6 in SYNTHESIS → Kraken deferred to MVP2) — enforced at `StrategyPort.on_bar()` return time

**Sizing** (pre-registration §5.4 vs D-13):

- 1% ATR-baseline risk — matches SA-7 formula in D-13
- 5% hard cap per position — binding per pre-registration §5.4; SYNTHESIS §3 D-12 and ADR-011 D-13 both aligned to 5%
- Kelly deferred until ≥30 OOS trades — matches SYNTHESIS OD-4 and D-13 in this ADR

**Fee model** (pre-registration §6.1 vs D-09):

- Binance retail VIP0 at timestamp of each historical trade — requires `fee_schedule_at(timestamp)` callable in `MakerTakerFeeModel` (D-09)
- BNB discount OFF — hard-coded default in `MakerTakerFeeModel`; live-mode operator can override via config; backtest MUST match pre-registration
- Fee history reconstructed to `docs/experiments/swing-mvp1/fee-history.csv` — Spec 2 (or a Spec 9 sub-task) owns the reconstruction

**Concurrency + correlation** (pre-registration §5.5 vs D-21):

- Max 3 concurrent positions — enforced by `RiskEngine.max_concurrent_positions_per_strategy` (D-04 machinery)
- Correlation lock at 0.80 (7-day rolling ρ) — enforced by RO-01 in Spec 8; the pre-registration explicitly picked 0.80 (was 0.85 in v0)
- Total exposure cap 15% of capital — 3 × 5% cap, derived; enforced by `RiskEngine`

**Kill triggers** (pre-registration §10 vs D-19, D-21):

- "Sim-vs-live PnL divergence > 30 bps p50 sustained 7 rolling days" — matches M-02 in D-19 (with testnet baseline subtraction per T-5)
- "Any SA-9 shock overlay rule fires 3+ times in 30 days" — requires Spec 8 (D-21) to instrument every RO-N rule with a 30-day rolling counter
- "Max drawdown 15% peak-to-trough" — requires `RiskEngine` to track peak-to-trough drawdown; wired in Spec 3
- "Any SA-9 P0 rule violation" — requires Spec 8 (D-21) rules to emit `RiskDecision` events consumed by the certification lifecycle in Spec 9

If the pre-registration is signed and hashed, the hash is included in every session startup log per D-22. Any drift between this ADR and a signed pre-registration is a bug in this ADR — the pre-registration is the binding scientific contract; the ADR is architecture in service of it.

---

## Consequences

### What this locks in for MVP1

- **Single fill kernel** — one `FillModelPort` code path, one `OrderbookWalkFillModel` MVP1 implementation, one `MakerTakerFeeModel` MVP1 implementation. Import-linter-enforced (D-01, D-20).
- **Composition-root mode selection** — `HERMES_ENV + HERMES_SHADOW + HERMES_ALLOW_MAINNET` triple determines injected adapters. Issue #196 must be amended (D-02, SYNTHESIS OD-2).
- **Bucket-aware domain from day 1** — every event has `strategy_id`; every aggregate is partitioned. No retrofit path (D-03).
- **`ClockPort` everywhere** — no direct `datetime.now()` in domain. Import-linter-enforced (D-15, D-20).
- **Reproducibility contract** — `event_seq` + `payload_sha256` on every event; append-only SQLite triggers; `RecordedExchangePort` for external calls (D-16, D-17).
- **Calibration as CI gate** — `CERTIFIED / UNTRUSTED` state machine; six P0 metrics with SA-5 thresholds and testnet baseline subtraction; blocks live deploy (D-18, D-19).
- **Risk overlay from SA-9** — eight portfolio-scope rules layered above `RiskEngine`; correlation lock, spread-spike halt, depeg monitor, FOMC blackout, multi-pair ATR cascade all shipping in MVP1 (D-21).
- **Strategy Development Protocol** — pre-registration v1's data window / universe / sizing / kill-trigger enforcement wired into the engine; deviation-detection via config schema hash (D-22).
- **Test pyramid manifest** — pytest markers, tier schedules, coverage thresholds, mutation kill rates, import-linter contracts all locked before Spec 1 lands (D-20).
- **Nine focused specs, not one mega-spec** — Spec 1 Domain, Spec 2 FillModel + SimulatedExchangeAdapter, Spec 3 OrderRouter, Spec 4 MarketDataPort + ClockPort, Spec 5 StrategyContract + SwingStrategyAdapter, Spec 6 Calibration Harness, Spec 7 Test Pyramid Manifest, Spec 8 Risk Overlay, Spec 9 Strategy Development Protocol. Landing sequence per SYNTHESIS §5 (gate-first) with Spec 8 landing after Spec 3 + Spec 4 but before shadow-mode launch, and Spec 9 landing after Spec 6 (with universe + window enforcement pieces early).
- **Storage backend for orderbook diffs** — SQLite append-only for MVP1 per SYNTHESIS OD-7 (consistency with existing storage; hermes AEAT retention already uses SQLite per SA-8); Parquet-via-pyarrow deferred to MVP2 per SA-10 §6.3 if backtest replay perf becomes a bottleneck.

### What remains flexible for MVP2

- **Second `StrategyAdapter` (Portfolio Balancer)** — plugs in via `strategy_id` machinery, no domain schema change (D-03, D-04).
- **Live trading path** — `RealExchangeAdapter` (Binance signed + Kraken signed) plugs in via composition-root port swap (D-02). Kraken public adapter is already shipped (PR #193); adding signed endpoints is a documented ~300 LoC delta (`docs/decisions/2026-08-24-kraken-public-adapter.md`).
- **Kelly f\* estimator activation** — the field is present on `StrategySignal` (D-13); an estimator that computes `f_star` from ≥30 OOS trades activates without domain change.
- **Historical L2 storage upgrade** — SQLite → Parquet (SA-10 §6.3) is a decoration-pattern swap in the adapter layer if profiling justifies it.
- **Queue position simulation** (M-07): P2 defer per SYNTHESIS §2 T-2; adds a probabilistic queue model to `OrderbookWalkFillModel` without changing the port contract.
- **Latency simulation** (M-05): P2 defer per SYNTHESIS §2 T-9; adds a network-latency simulator between `MarketDataPort` and `FillModelPort`.
- **Multi-venue smart order routing** (Cont-Kukanov): MVP2+; requires cross-venue L2 fusion in `MarketDataPort` and cross-venue capital allocation in `RiskEngine`.
- **Cross-exchange failover** (SA-9 §5.4 MVP2 enhancement): "if Binance unavailable → route to Kraken" requires exchange-agnostic position tracking, which the event-sourcing model (SA-8) already supports; the missing piece is a router in Spec 3 evolution.
- **Cash diversification** (SA-9 §4.5 MVP2 enhancement): 50/50 USDT/USDC held as cash, rebalanced quarterly, requires MVP2 balancer.
- **Macro regime integration** (SA-9 §8.6 MVP2 enhancement): VIX + MOVE + BTC-S&P correlation as a weekly cron job producing a `macro_regime` config that reduces sizing in stressed regimes.
- **Free-threaded Python 3.13+ (PEP 703)**: deferred per SA-10 §3.2 — ecosystem gaps + 5-10% single-threaded overhead not justified at 10 msg/sec.
- **Sub-interpreters (PEP 684, 734)**: not applicable at MVP1/MVP2 scale per SA-10 §3.3.
- **Cython / Rust / Numba on the hot path**: deferred per SA-10 §3.4 — YAGNI at 10 msg/sec; pure Python is 1000× below the ceiling.

### What this forbids (structural, non-negotiable)

- `datetime.now()` in `hermes.domain.*` — import-linter fails CI (D-15, D-20).
- `if.*shadow` or `if.*dry_run` grep hit in fill logic modules — import-linter fails CI (D-01, D-20).
- Cross-strategy imports — import-linter fails CI (D-03, D-20).
- `Order` / `Fill` / `Position` / `Balance` without `strategy_id` — schema constraint (`NOT NULL`), event-serialization rejects (D-03).
- UPDATE or DELETE on the events table — SQLite trigger raises (D-16).
- Deploy to live when `shadow_mode.status = UNTRUSTED` — CI script exits non-zero (D-18).
- Skipping the testnet baseline measurement before MVP1 launch — Spec 6 P0 pre-run (D-19).
- Reading OOS data (2025-09-01 → 2026-12-31) outside `CERTIFICATION_MODE=true` — Spec 9 raises `PreRegistrationViolation` (D-22).
- Starting certification before 2027-01-01 — Spec 9 clock check refuses (D-22).
- Trading any instrument outside `STRATEGY_UNIVERSE` frozenset — `StrategyPort.on_bar()` return validation (D-22).
- Deviating from the pre-registration's committed config hash — session refuses to start (D-22).

### What this requires (before MVP1 shadow-mode launch)

- **Historical L2 data plan resolved** — pre-registration §6.2 picked "OHLCV-only backtest + concurrent L2 collector" (OD-1 resolution). No blocker.
- **Orderbook diff storage** (issue #203) implemented — absorbed into Spec 4.
- **#196 shadow-mode PR amended** to port-swap pattern per D-02 / SYNTHESIS OD-2.
- **#175 issue rewritten** to reflect composition-root selection per SYNTHESIS OD-3.
- **Fee-history CSV reconstructed** — pre-registration §6.1 requires Wayback-Machine reconstruction of Binance VIP0 fees; Spec 2 or Spec 9 sub-task.
- **Testnet baseline measurement** (7-day sim-off run) — Spec 6 P0 pre-run, hard-coded in the harness (SYNTHESIS OD-5).
- **Pre-registration signed** — pre-registration v1 is `PENDING_SIGNATURE`; the signed SHA-256 hash is required for Spec 9 config-schema-hash enforcement (D-22).

### What this defers to post-MVP1 review

- **Exchange coverage** — Binance-only for MVP1 (SYNTHESIS OD-6 recommendation; pre-registration §3). Kraken as a second exchange lands after Binance is CERTIFIED.
- **Storage backend** — SQLite for MVP1 (SYNTHESIS OD-7); Parquet upgrade if profiling justifies.
- **Spec landing execution mode** — Interactive for Spec 7 + Spec 1 + Spec 4 to establish rhythm, Automatic for Spec 2–6, 8, 9 once patterns are stable (SYNTHESIS OD-8).

---

## Cross-references

### Locked decisions (research vault)

- `decision/paper-trading-foundational` — engine is THE foundational subsystem; umbrella ADR + focused specs (not mega-spec)
- `decision/mvp1-swing-only-balancer-mvp2` — Swing-only MVP1, Portfolio Balancer deferred to MVP2
- `architecture/time-model-hybrid` — event-driven fills + bar-close signals (D-06)
- `architecture/portfolio-multi-balance` — partitioned balances, `strategy_id` day 1 (D-03, D-04)
- `decision/kline-storage-superseded` — orderbook diff storage supersedes kline storage (issue #203, absorbed into Spec 4)

### Sub-agent research reports

- `docs/research/paper-trading/README.md` — vault index
- `docs/research/paper-trading/sa0-landscape.md` — 25+ systems landscape + failure inventory (evidence for R-3, R-4)
- `docs/research/paper-trading/sa1-academic.md` — theory: impact, slippage, bias, allocation (evidence for D-13, D-08)
- `docs/research/paper-trading/sa2-deep-code.md` — deep code reading + `FillModelPort` shape (evidence for D-01, D-02, D-08, D-10, D-11; R-1, R-2, R-3, R-4)
- `docs/research/paper-trading/sa3-microstructure.md` — Binance/Kraken spot facts + storage (evidence for D-09, D-19, Spec 4)
- `docs/research/paper-trading/sa4-post-mortems.md` — 15 failure classes, 6 P0 defenses (evidence for D-14, D-18, Spec 3)
- `docs/research/paper-trading/sa5-calibration.md` — 15 metrics, thresholds, cert lifecycle (evidence for D-18, D-19)
- `docs/research/paper-trading/sa6-multi-strategy.md` — MVP2-ready domain aggregate design (evidence for D-03, D-04, D-05; R-1, R-2)
- `docs/research/paper-trading/sa7-strategy-design.md` — Donchian + Chandelier + ATR sizing (evidence for D-12, D-13, D-14)
- `docs/research/paper-trading/sa8-reproducibility.md` — event sourcing + `ClockPort` + replay (evidence for D-15, D-16, D-17)
- `docs/research/paper-trading/sa9-portfolio-shocks.md` — portfolio-level shocks + risk overlay (evidence for D-21)
- `docs/research/paper-trading/sa10-python-perf-testing.md` — Python perf + testing philosophy (evidence for D-16, D-20; MVP2 deferrals)
- `docs/research/paper-trading/SYNTHESIS.md` — reconciles the ten SA reports; §3 20-decision outline expanded here

### Pre-registration

- `docs/experiments/swing-mvp1/pre-registration-v1.md` (v1, PENDING_SIGNATURE) — binding scientific contract for the first live experiment; source of D-22 and consistency-check constraints
- `docs/experiments/swing-mvp1/pre-registration-v0.md` (superseded)
- Companion (pending): `docs/experiments/swing-mvp1/data-manifest.json`, `docs/experiments/swing-mvp1/fee-history.csv`, `docs/experiments/swing-mvp1/lab-notebook.md`, `docs/experiments/swing-mvp1/success-criteria.md`

### Existing ADRs

- [ADR-001](./adr-001-llm-as-supervisor.md) — LLM supervisor sits above the engine; consumes calibration reports for anomaly detection
- [ADR-002](./adr-002-tax-posture.md) — EUR-first accounting, ECB rates; the depeg-restatement path (RO-06) uses this
- [ADR-003](./adr-003-solution-architecture/README.md) — solution architecture; §01 code organization, §02 data flow, §03 persistence schema, §04 exchange adapters, §05 signal engines, §09 deployment, §10 failure-recovery all touched by this ADR
- [ADR-004](./adr-004-exchange-decision.md) — Binance primary, Kraken hot backup; D-09 fee tables reference both
- [ADR-005](./adr-005-ai-ml-support-boundary.md) — AI/ML support boundary; Spec 9's integrity-checker function (from pre-registration §13) is analogous
- [ADR-006](./adr-006-versioning-strategy.md) — versioning strategy; schema version on `DomainEvent` (SA-10 §5.3) uses this
- [ADR-007](./adr-007-testing-strategy.md) — testing strategy; D-20 test pyramid tier definitions extend ADR-007's coverage thresholds
- [ADR-008](./adr-008-observability-strategy.md) — observability; calibration-harness alerts (D-18) and RO-N CRITICAL events (D-21) fire via the notification path
- [ADR-009](./adr-009-documentation-strategy.md) — documentation strategy; per-spec decisions land as decisions-journal entries or openspec changes
- [ADR-010](./adr-010-dependency-management-and-supply-chain.md) — dependency management; `pyarrow` (SA-10 §6.3, MVP2), `cosmic-ray` (SA-10 §11), `atheris` (SA-10 §12.1) all flow through the Renovate + CVE-scanning pipeline

### Decisions-journal entries

- [2026-08-23 Credential storage layered by env](../decisions/2026-08-23-credential-storage-layered-by-env.md) — `HERMES_ENV + HERMES_ALLOW_MAINNET` are the mode-selection gates D-02 uses
- [2026-08-23 Binance testnet klines + SQLite kline store](../decisions/2026-08-23-binance-testnet-klines.md) — supersedes; issue #203 orderbook diff storage takes its place
- [2026-08-23 FX rate source — Frankfurter](../decisions/2026-08-23-fx-rate-source-frankfurter.md) — EUR-first FX; the depeg-restatement path (RO-06) uses ECB rates via Frankfurter
- [2026-08-23 Slack alongside Telegram](../decisions/2026-08-23-slack-alongside-telegram.md) — CRITICAL alerts from Spec 8 RO-N rules and Spec 6 UNTRUSTED transitions fire via both channels
- [2026-08-24 Kraken public-endpoints adapter](../decisions/2026-08-24-kraken-public-adapter.md) — the USDT/USD and USDC/USD polling for RO-06 depeg monitor uses this adapter's ticker method

### GitHub issues

Parent epic:

- **#25** — Phase 1 · Paper-trading engine (deterministic rule engine + Strategy pattern) — becomes PARENT for the 9 specs; body should be updated to reference this ADR + the 9 sub-issues to be filed

Absorbed / superseded:

- **#26** — Phase 1 · Backtest harness — absorbed into Spec 2 + Spec 6; close as superseded
- **#27** — Phase 1 · Signal engines library — absorbed into Spec 5; close as superseded
- **#99** (CLOSED) — Domain events + in-process EventBus — foundation for D-16 event ledger
- **#98** (CLOSED) — Domain value objects (Money, Price, Weight, Pair, Quantity, LotId) — foundation for D-03 aggregates
- **#139** — Kline storage — already superseded by #203, no action
- **#199** — ExchangePort method contract — merged INTO Spec 2 (port defined alongside first impl); close as absorbed

Amended / rewritten:

- **#175** — Shadow-mode flag + adapter-layer order interception — rewrite scope to reflect port-swap pattern (SYNTHESIS OD-3, D-02); keep as UX/config tracking issue
- **#196** — ADR + openspec change spec for #175 (design phase) — amend to adopt port-swap pattern (SYNTHESIS OD-2, D-02) instead of decorator; wait for THIS ADR to land first

Split:

- **#197** — Rebalancer + DonchianEngine domain classes — split: DonchianEngine → Spec 5; Rebalancer → MVP2 (Portfolio Balancer)
- **#200** — RebalanceUC + SwingSignalUC use cases — split: SwingSignalUC → Spec 5 application layer; RebalanceUC → MVP2

Tracked-by-spec (implementation issues remain open, add scope comment):

- **#198** — SimulatedExchangeAdapter — IS Spec 2's implementation; add scope comment: "See Spec 2 in SYNTHESIS + this ADR D-01/D-02/D-08"
- **#201** — Backtest calibration harness — IS Spec 6's implementation; add scope comment: "See Spec 6 in SYNTHESIS + this ADR D-18/D-19"
- **#203** — Orderbook diff stream storage — absorbed INTO Spec 4; add scope comment: "Delivered as part of Spec 4"

Process context (not sub-issues of this ADR, but relevant):

- **#141** — MVP1 status meta — status board for MVP1 shadow-mode product on VPS
- **#142** — MVP2 status meta — status board for MVP2 (Portfolio Balancer, real-money flip, UI, resilience)
- **#194 / #195** — OpenSpec change-required gate (dor/dod openspec-gate at create-pr.sh + pr-preflight.sh, merged) — every focused spec's PR flows through this gate; each spec authors its own `openspec/changes/<name>/change.md`

New issues to file (one per spec, all as sub-issues of #25):

- feat(specs): Spec 1 — Domain Aggregates (bucket-aware)
- feat(specs): Spec 2 — FillModelPort + SimulatedExchangeAdapter
- feat(specs): Spec 3 — OrderRouter (bucket-aware capital enforcement)
- feat(specs): Spec 4 — MarketDataPort + ClockPort + orderbook diff storage
- feat(specs): Spec 5 — StrategyContract + SwingStrategyAdapter
- feat(specs): Spec 6 — Calibration harness (SA-5 protocol)
- feat(specs): Spec 7 — Test pyramid manifest
- feat(specs): Spec 8 — Risk overlay (SA-9 eight-rule MVP1 defense stack)
- feat(specs): Spec 9 — Strategy Development Protocol (pre-registration enforcement)

---

## What would change this decision

- **Pre-registration v1 signed with material changes to §3, §4, §5, §6, §9, or §10** — D-22 consistency check breaks; ADR must be revised to match the signed protocol.
- **NautilusTrader ships `Position.strategy_id: StrategyId` first-class + per-strategy `RiskEngine.max_notional_per_order`** — R-1 rejection weakens; may become adoptable for MVP3+ if Python-Rust friction remains acceptable and MVP1 has already exercised the hexagonal port shape.
- **Binance shuts down for Spanish users under MiCA enforcement** — D-09 default flips to Kraken; SYNTHESIS OD-6 recommendation to defer Kraken to MVP2 becomes MVP1 critical path; Spec 4 wires the Kraken WS bootstrap in addition to Binance's.
- **A production shadow-mode run shows the OHLCV-derived synthetic book (D-07) produces M-02 divergence > 100 bps p95** — Option C from SYNTHESIS OD-1 becomes insufficient; escalate to Option A (30-60 day shadow capture) or Option B (Tardis.dev purchase) for MVP2 backtest fidelity.
- **SQLite write throughput saturates at the shadow-mode event rate** — SA-10 §6.2 thresholds (5k rows/sec sustained) suggest this is highly unlikely at 10 events/sec per pair, but if MVP2 adds many pairs and calibration events, migrate to Postgres per SA-10 §6.2 or split market-data events into Parquet per SA-10 §6.3.
- **A calibration threshold BLOCK produces sustained false positives** — investigate testnet baseline drift first (SYNTHESIS §2 T-5); if the baseline itself is unstable, re-measure quarterly (already Spec 6 P0); if the threshold is genuinely wrong for hermes's real-world distribution, adjust SA-5 M-02 threshold with evidence in a decisions-journal entry (SA-5 §14 explicitly acknowledges thresholds are starting values).
- **A regulatory event blocks USDT trading in EU (MiCA Article 50, SA-9 §6.1)** — Spec 8 RO-06 depeg mechanism repurposed for "denominator unavailable" halt; RiskEngine reads a `regulatory_status` config that hermes updates when the operator observes a MiCA event (manual for MVP1, automated feed for MVP2).

---

## Pending

- **Nine spec sub-issues filed** under #25 with acceptance criteria pointing at this ADR + SYNTHESIS §4
- **#196 amendment PR** landed with port-swap pattern (D-02)
- **#175 rewrite** to reflect composition-root selection (D-02)
- **Pre-registration v1 signed** with hash committed to Spec 9 for config-schema-hash enforcement (D-22)
- **Fee-history CSV reconstructed** (pre-registration §6.1 Wayback Machine) — Spec 2 or Spec 9 sub-task
- **First testnet baseline measurement job run** (7-day sim-off, Spec 6 P0 pre-run, D-19)
- **Import-linter contracts landed** (Spec 7) — three contracts per D-15, D-01, D-03
- **First CERTIFIED shadow-mode run** (SYNTHESIS §5 timeline: ~12 weeks after Spec 1 lands)
- **Operator signature on this ADR** — confirmed 2026-10-03, see [decisions journal](../decisions/2026-10-03-adr-011-accepted.md)
