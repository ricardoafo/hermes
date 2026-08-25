# Paper-Trading Engine Research — SYNTHESIS

**Date**: 2026-08-24
**Author**: Synthesis agent (opus)
**Engram topic key**: `research/paper-trading-synthesis`
**Status**: DRAFT — awaiting operator review
**Feeds**: umbrella ADR + 7 focused specs
**Time-boxed**: 90-120 min

---

## 0. Reading Map

This document is the single input to the paper-trading engine ADR and the 7 focused
specs that will implement it. It reconciles nine sub-agent reports (SA-0 through
SA-8) against locked operator decisions, resolves cross-report tensions, proposes
a spec breakdown with landing sequence, flags open questions requiring operator
input, and maps existing GH issues to the new spec structure.

Source engram topic keys:

- `research/paper-trading-sa0-landscape` — 25+ systems landscape + failure inventory
- `research/paper-trading-sa1-academic` — theory (impact, slippage, bias, allocation)
- `research/paper-trading-sa2-deep-code` — deep code reading + `FillModelPort` shape
- `research/paper-trading-sa3-microstructure` — Binance/Kraken spot facts + storage
- `research/paper-trading-sa4-post-mortems` — 15 failure classes, 6 P0 defenses
- `research/paper-trading-sa5-calibration` — 15 metrics, thresholds, cert lifecycle
- `research/paper-trading-sa6-multi-strategy` — MVP2-ready domain aggregate design
- `research/paper-trading-sa7-strategy-design` — Donchian + Chandelier + ATR sizing
- `research/paper-trading-sa8-reproducibility` — event sourcing + `ClockPort` + replay

Locked decisions cited throughout:

- `decision/paper-trading-foundational` — engine is THE subsystem
- `decision/mvp1-swing-only-balancer-mvp2` — Swing only, Balancer deferred
- `architecture/time-model-hybrid` — event-driven fills + bar-close signals
- `architecture/portfolio-multi-balance` — partitioned balances, `strategy_id` day 1
- `decision/kline-storage-superseded` — orderbook diff storage (issue #203)

---

## 1. Executive Summary

**The recommended architecture is a strict hexagonal engine built around one
canonical fill code path**, exercised by three modes that differ only in which
adapters get injected into a shared kernel:

- **Backtest**: `HistoricalMarketDataAdapter` + `SimulatedClock` + `FillModelPort`
- **Shadow**: `LiveWebSocketMarketDataAdapter` + `WallClockClock` + same `FillModelPort`
- **Live** (MVP2): `LiveWebSocketMarketDataAdapter` + `WallClockClock` + `RealExchangeAdapter`
  bypassing `FillModelPort` for order writes

This is the NautilusTrader + LEAN pattern (SA-2 §2 comparison matrix) — the only
two systems in the landscape that achieve genuine backtest-to-live parity. Every
system that branches the fill logic (`if dry_run:`) has been shown to diverge in
production (Freqtrade issues #12662, #12675 — SA-0 §3, SA-2 §1.3).

**The engine's foundational contract** is a bucket-aware domain model where every
`Order`, `Fill`, `Position`, `Balance`, and `Portfolio` aggregate carries a
`strategy_id` from day 1. MVP1 exercises exactly one bucket (Swing); MVP2's
Portfolio Balancer plugs in as a second `StrategyAdapter` without any breaking
change. This is confirmed by NautilusTrader's `Position.strategy_id: StrategyId`
first-class field (SA-6 finding 1); LEAN's insight-level attribution is measurably
weaker (SA-6 finding 4).

**Time model is hybrid** (locked: `architecture/time-model-hybrid`):
event-driven at the fill layer (consumes L2 orderbook diffs and trade prints in
timestamp order), bar-close at the strategy layer (Swing wakes on 4H candle
close, per SA-7 recommended stack). The two channels are exposed by a single
`MarketDataPort` with a `raw_events` stream + a `consolidated_bars` stream.

**Fill model** is `OrderbookWalkFillModel` (SA-1 §3, SA-2 §3, SA-3 §2 P0.3) —
walks the L2 book (real snapshot in shadow, synthetic OHLCV-derived book in
backtest) to compute VWAP fill, dispatches maker/taker classification, and
applies tier-aware fees via an injected `FeeModelPort`. Slippage EMERGES from the
walk — no separate slippage constant (SA-2 anti-pattern AP-4, AP-6).

**Signal stack for MVP1** (SA-7 recommended stack, HIGH confidence): Donchian(20)
breakout on 4H, ADX(14) > 25 + EMA(200) daily + OBV + ATR-spike filters, entry
via limit at `high + 0.1×ATR`, Chandelier(22, 3.0) trailing stop, ATR-based sizing
at 1% risk-per-trade with 10% equity cap per position.

**Calibration gates certification** (SA-5): 6 P0 metrics (M-02 fill-price bps,
M-04 bias direction, M-09 PnL correlation, M-10 Sharpe match, M-13 lookahead
integrity, M-15 fee accuracy), with thresholds anchored in real-world evidence
(Alpaca IEX/SIP 5-50 bps gap, Binance slippage benchmarks 10-50 bps). Shadow-mode
status is a state machine (`PENDING_DATA → CERTIFIED → UNTRUSTED`) that gates
live deployment at the CI level.

**Reproducibility is non-negotiable** (SA-8): every domain event carries
`event_seq` (SQLite `rowid`) + `payload_sha256`. `ClockPort` is injected
everywhere — domain never calls `now_utc()` directly. `RecordedExchangePort`
logs every real API response as an `ExternalCallRecorded` event, enabling
deterministic replay via `hermes replay --from <seq> --to <seq>`.

**Decisions locked by this synthesis**:

1. Umbrella ADR + 7 focused specs (not a single mega-spec)
2. `FillModelPort` shape (SA-2 §3) with `MarketDataContext.orderbook: Optional[…]`
3. Domain aggregates bucket-aware from day 1 (`strategy_id` propagation)
4. Hybrid time model (fills=event-driven, signals=bar-close consolidator)
5. Calibration harness is a first-class CI gate, not a monitoring dashboard
6. Same-code-path invariant enforced via import-linter contract

**Decisions still open** (see §6): historical L2 data source, Kelly vs ATR
sizing wiring, #196 shadow-mode PR relationship, spec landing sequence
(gate-first vs engine-first).

Confidence: HIGH on architecture (three independent reports converge on hexagonal
same-code-path). MEDIUM on some threshold numbers (SA-5 §14 acknowledges that
30 bps p50 block threshold is derived from Alpaca data, not from same-exchange
Binance testnet baseline — needs empirical adjustment).

---

## 2. Tension Reconciliation

Every conflict found between reports, with resolution.

### T-1: SA-1 Fractional Kelly (0.25×) vs SA-7 ATR 1% risk-per-trade

**Tension**: SA-1 §6 recommends *fractional Kelly (0.25×) per trade, 5% hard cap
of the swing bucket*. SA-7 recommends `qty = min((equity × 0.01) / (2.0 ×
ATR(14)), equity × 0.10 / entry_price)` — a 1% risk-per-trade ATR-sized model
with a 10% equity cap.

**Resolution**: These are two DIFFERENT layers of the sizing pipeline, not
alternatives. They compose:

- **ATR-sizing (SA-7)** is the per-trade *stop-distance-aware* baseline. It answers
  "given my ATR and 1% risk budget, how many units do I buy so a stop-out costs
  exactly 1% of equity?"
- **Fractional Kelly (SA-1)** is the *edge-adjusted upper bound*. It caps the
  per-trade risk fraction based on estimated edge from OOS walk-forward trades.
  At full Kelly f*it maximises E[log W]; at 0.25× f* it sacrifices ~25% growth
  for ~75% drawdown reduction.

**Wired composition**:

```text
raw_qty     = (equity × risk_pct_target) / (stop_atr_multiple × ATR)   # SA-7
kelly_qty   = (equity × min(0.25 × f_star_estimate, 0.05)) / entry_px  # SA-1
notional    = min(raw_qty × entry_px, kelly_qty × entry_px, 0.05 × equity)  # pre-reg v1 §5.4 binding cap
qty         = notional / entry_px
```

For **MVP1**, `f_star_estimate` is unavailable (no OOS trades yet), so the Kelly
cap degenerates to the SA-1 5% hard cap. Effective sizing is SA-7's rule with a
5% notional ceiling. After 30 days of shadow-mode data with ≥30 trades, the
Kelly estimator activates and can further reduce sizing if edge is weak.

**Spec ownership**: `Spec 3 (OrderRouter)` implements the composition. `Spec 5
(StrategyContract)` exposes `f_star_estimate` as an optional field on the
signal — allows SA-7's baseline in isolation while making the SA-1 hook available.

Confidence: HIGH — the two mechanisms are mathematically composable and address
orthogonal concerns (stop-distance normalisation vs edge-adjusted growth).

### T-2: SA-0 "queue position irrelevant for MVP1" vs SA-5 M-07 partial fill KS test

**Tension**: SA-0 §5 and SA-3 §1.6 both conclude queue position modeling is
irrelevant for MVP1 (swing orders placed away from touch, retail sizes on liquid
pairs). SA-5 lists M-07 (partial fill KS test) as a required calibration metric.

**Resolution**: SA-5 §8 already resolves this — M-07 is classified **P2** (not
P0, not P1) precisely because "rare for retail swing size; needs sample
accumulation." No conflict. The synthesis carries M-07 forward as future work,
not MVP1 gating.

Confidence: HIGH — internally consistent within SA-5's own tiering.

### T-3: SA-2 "next-bar-open fill" vs SA-7 "entry limit at high + 0.1×ATR"

**Tension**: SA-2 §4 AP-1 mandates "ALWAYS fill at bar.open of the NEXT bar" as
the anti-Zipline defense. SA-7 specifies a LIMIT entry at `20-day high + 0.1 ×
ATR` — this is a limit order, not a market order at next bar open.

**Resolution**: SA-2's rule targets MARKET orders (Zipline's bug was a
market-timing bug). SA-7's limit order has a different fill mechanism: it rests
in the book and fills when price reaches it (potentially inside the next bar,
not at next bar open). Both are correct in their own context:

- Market orders (used for exits, kill-switch closes): next-bar-open semantics
  (SA-2 AP-1)
- Limit orders (used for SA-7 entries): fill when price crosses limit within a
  future bar, using bar high/low and configurable intrabar ordering (SA-2 §6
  intrabar ordering for stops)

**Spec ownership**: `Spec 2 (FillModelPort)` implements both semantics; `Spec 5
(StrategyContract)` documents which order type each signal must produce.

Confidence: HIGH.

### T-4: SA-2 "no `if shadow_mode` branches" vs #175 `ShadowGate` interceptor

**Tension**: SA-2 §4 AP-2 forbids divergent code paths for backtest vs shadow.
GH issue #175 (which #196 PR implements) proposes a `ShadowGate` decorator on
adapter write methods that intercepts orders before hitting the wire — this
looks like the exact anti-pattern SA-2 warns against.

**Resolution**: They resolve differently at different layers of the hexagon:

- SA-2 AP-2's rule is about the FILL SIMULATION code path — the code that
  computes fill price/qty from market state MUST be a single function. A
  `if dry_run: return zero_slippage()` in the fill computation itself is the
  banned anti-pattern.
- `ShadowGate` operates at the ADAPTER layer — it chooses whether to route the
  order to a `RealExchangeAdapter` (which hits the wire) or a
  `SimulatedExchangeAdapter` (which runs the fill model). The DECISION to
  simulate is at the port boundary; the SIMULATION itself is a single code
  path.

The correct implementation of #175 uses the port-swap pattern (LEAN's
`BacktestingBrokerage` vs `PaperBrokerModel` vs live broker — SA-2 §1.2) rather
than injecting `if shadow:` branches inside `create_dry_run_order` as Freqtrade
does. The `ShadowGate` becomes trivial: it just picks the adapter to bind at
composition-root time based on `HERMES_SHADOW`.

**Spec ownership**: `Spec 2 (FillModelPort)` + `Spec 4 (MarketDataPort +
ClockPort)` define the ports; the actual `ShadowGate` shrinks to a
composition-root wiring choice. This means **#196 must be amended** to adopt
the port-swap pattern rather than the decorator-interceptor pattern currently
in its draft. See §6 open decision on #196.

Confidence: HIGH.

### T-5: SA-5 "30 bps p50 BLOCK" vs SA-3 "testnet has separate orderbook"

**Tension**: SA-5 §2 threshold table sets M-02 p50 BLOCK at > 30 bps. SA-5 §13
also flags that Binance testnet has a SEPARATE orderbook from mainnet, so some
bps divergence between sim (testnet data) and real (testnet fills) is
STRUCTURAL, not calibration failure. SA-3 §1.9 confirms testnet-mainnet
divergence.

**Resolution**: SA-5 acknowledges this as OPEN QUESTION #2 (§13). The synthesis
adopts the resolution: **before MVP1 launch, run a 7-day sim-off baseline
measurement** where the ONLY comparison is testnet mainnet↔testnet REST prices
vs testnet WebSocket prices (no sim involved). This measures the STRUCTURAL
testnet divergence; the M-02 threshold is applied to `bps_divergence -
baseline_bps_divergence`. If baseline is 8 bps p50, the effective sim-quality
threshold is `sim < baseline + 30 = 38 bps p50 BLOCK`.

**Spec ownership**: `Spec 6 (Calibration Harness)` includes the baseline
measurement as a P0 pre-run step.

Confidence: MEDIUM — the mechanism is right; the exact baseline value requires
empirical measurement.

### T-6: SA-8 "SQLite `rowid` as sequencer" vs high write throughput

**Tension**: SA-8 §Architecture #3 recommends `event_seq = SQLite rowid` with
single-writer WAL mode. Under high shadow-mode event volume (orderbook @100ms,
aggTrades + fills + calibration pairs), single-writer SQLite could bottleneck.

**Resolution**: SA-3 §3 storage budget shows BTC/USDT + SOL/USDT orderbook diffs
are ~10 msgs/second per pair (Binance @depth@100ms cadence). At 20 events/sec
sustained with WAL mode, SQLite handles > 10,000 writes/sec on modern SSDs.
Well below the ceiling. Additionally, the ARCHITECTURAL separation is:

- **Domain events** (Order/Fill/Position/Balance updates) go through the
  single-writer domain ledger (`hermes.db`) — these are LOW volume (10s per day
  at swing frequency)
- **Market data events** (orderbook diffs, trade prints) go to a SEPARATE
  storage (issue #203) — high volume, but NOT part of the domain event
  sequence. Sequencer contention is a non-issue.

**Spec ownership**: `Spec 4 (MarketDataPort + ClockPort)` handles market-data
storage; `Spec 1 (Domain Aggregates)` handles domain event storage. They share
the SAME `ClockPort` but have DIFFERENT storage backends.

Confidence: HIGH.

### T-7: SA-7 "on-exchange stop-market" vs SA-4 "flash crash 99% slippage"

**Tension**: SA-7 recommends on-exchange stop-market orders ("accept slippage,
do not accept non-fill risk"). SA-4 documents Kraken BTC/CAD flash crash of
99% (May 2019) where stop-loss orders filled at pennies — a stop-market during
a flash crash produces catastrophic fills.

**Resolution**: Both hold simultaneously. On-exchange stop-market is the correct
default for the modal case (avoids the bot-managed stop single-point-of-failure
that SA-4 explicitly warns against). Flash crash protection is an ORTHOGONAL
layer:

1. **Bucket-level drawdown circuit-breaker** (SA-7 §Drawdown Limits): peak
   drawdown -20% → close all + halt. This runs on aggregated PnL, not per-order.
2. **Stop-order price validation**: reject stop-market with stop price > N ×
   ATR from current price (implies severe adverse move already in progress —
   likely flash crash mid-event).
3. **Kill-switch on unusual volatility**: SA-4 recommends halting on ATR spike
   > 3σ from rolling window.

**Spec ownership**: `Spec 3 (OrderRouter)` implements the stop-order price
validation. `Spec 7 (Test Pyramid Manifest)` requires a chaos test that injects
a synthetic flash crash and asserts the circuit-breaker fires.

Confidence: HIGH.

### T-8: SA-6 "MVP2 Balancer bucket-aware from day 1" vs SA-1 "single-bucket Kelly MVP1"

**Tension**: SA-6 requires `strategy_id` on every aggregate + `capital_budget`
per strategy from MVP1. SA-1 recommends "no cross-strategy optimization for
MVP1" — single-bucket Kelly.

**Resolution**: No conflict. SA-6's `strategy_id` is a DOMAIN carrier field, not
a strategic algorithm. SA-1's advice is that MVP1 shouldn't run an HRP/MVO
allocator across buckets — because there's only one bucket to allocate to. Both
agree: MVP1 has one bucket exercised; the domain model is prepared for N buckets.

Confidence: HIGH — trivially resolved by re-reading the locked decision
`architecture/portfolio-multi-balance`.

### T-9: SA-2 recommendation vs SA-5 M-05 latency metric

**Tension**: SA-2 §6 defers latency simulation to MVP2. SA-5 M-05 tracks fill
latency divergence.

**Resolution**: SA-5 explicitly classifies M-05 as **P2** (§8): "swing strategy
is latency-insensitive." Not conflict — deferred consistently.

Confidence: HIGH.

### Summary Table

| ID  | Reports    | Nature                           | Resolution | Owner spec        |
|-----|------------|----------------------------------|------------|-------------------|
| T-1 | SA-1×SA-7  | Kelly cap vs ATR sizing          | Compose    | Spec 3            |
| T-2 | SA-0×SA-5  | Queue position necessity         | M-07 = P2  | Spec 6            |
| T-3 | SA-2×SA-7  | Fill semantics                   | Per-type   | Spec 2 + 5        |
| T-4 | SA-2×#175  | `ShadowGate` pattern             | Port-swap  | Spec 2 + 4        |
| T-5 | SA-5×SA-3  | Testnet baseline                 | Subtract   | Spec 6            |
| T-6 | SA-8×SA-3  | Sequencer scale                  | Separate   | Spec 1 + 4        |
| T-7 | SA-7×SA-4  | Stop-market vs flash crash       | Orthogonal | Spec 3 + 7        |
| T-8 | SA-6×SA-1  | Bucket-aware vs single-bucket    | Domain/algo| Spec 1            |
| T-9 | SA-2×SA-5  | Latency modeling                 | P2 defer   | Spec 6            |

---

## 3. Proposed ADR Structure

**Filename**: `docs/decisions/2026-08-XX-paper-trading-engine-architecture.md`
(ADR-00N — number allocated when ADR is written).

**Title**: "Paper-Trading Engine Architecture: Hexagonal Fill Kernel with Same-
Code-Path Backtest/Shadow/Live Parity"

**One-line summary**: A single hexagonal engine where backtest, shadow, and live
share the same `FillModelPort`, differing only in injected `MarketDataPort` +
`ClockPort` + `ExchangePort` adapters; domain aggregates are bucket-aware from
day 1; calibration is an automated CI gate, not a monitoring dashboard.

### Section-by-section outline

**1. Problem Statement** (~250 words)

- Why the paper-trading engine is THE foundational subsystem (locked:
  `decision/paper-trading-foundational`) — "no product without this"
- What breaks if we get it wrong: Quantopian died despite 200k users because
  fills weren't calibrated (SSRN 2745220); Knight Capital lost $460M in 45min
  from a deployment invariant failure (SA-4); Freqtrade users lose money live
  after profitable backtests because the fill code path diverges (SA-0 issues
  #12662, #12675)
- What we're building: an engine that provably matches live execution physics,
  gated by a calibration harness that prevents deployment when divergence
  exceeds evidence-based thresholds

**2. Alternatives Considered** (~500 words)

For each: name, one-para description, why rejected, evidence citation.

- **Alt 1 — Adopt NautilusTrader directly**. Rust core, kernel-based
  backtest-live parity, best-in-class. Rejected because: (a) Rust core adds
  cross-language friction in a Python-first codebase; (b) `strategy_id`
  attribution on PnL is missing (SA-6 finding 2 — keyed by instrument only);
  (c) `RiskEngine.max_notional_per_order` is global not per-strategy (SA-6
  finding 3); (d) issue #2194 acknowledges the fill model is basic (no queue,
  no partial fills natively); (e) integration cost > cost of building the
  targeted hexagonal engine.
- **Alt 2 — Adopt QuantConnect LEAN directly**. Most mature portfolio
  construction. Rejected because: (a) C# core; (b) `PortfolioConstructionModel`
  attribution at insight level not position level (SA-6 finding 4); (c) issue
  #4897 shows crypto zero-slippage bug open since 2020; (d) cloud-first design.
- **Alt 3 — Adopt Freqtrade directly**. Most battle-tested crypto stack.
  Rejected because: (a) 15+ `if dry_run:` branches in `exchange.py` (SA-2 §1.3)
  make backtest-shadow divergence architectural not fixable; (b) issues #12662,
  #12675 remain "resolved by documentation, not code fix"; (c) hardcoded 5%
  slippage constant (SA-2 AP-4); (d) no formal `FillModelPort` abstraction.
- **Alt 4 — Adopt VectorBT for backtest + custom shadow**. Fastest for
  parameter sweeps. Rejected because: (a) vectorized paradigm cannot model
  event-driven fills — fundamental architectural mismatch with the locked
  hybrid time model; (b) no live trading in OSS; (c) two-engine strategy
  reintroduces the exact divergence problem we're trying to solve.
- **Alt 5 — Build a single mega-spec + PR**. Rejected because: (a) too many
  concerns coupled in review; (b) test pyramid cannot be validated
  incrementally; (c) violates the locked decision to split by concern
  (`decision/paper-trading-foundational` §3).
- **Chosen — Build hexagonal engine, umbrella ADR + 7 focused specs** (see §3).

**3. Chosen Approach** (~800 words)

- The hexagonal skeleton: `StrategyPort` → `OrderRouter` → `RiskEngine` →
  `FillModelPort` / `ExchangePort` → `Portfolio` (aggregate) → `EventLedger`
- The three modes = three adapter compositions at the composition root
- The bucket-aware domain: `strategy_id` propagation contract diagram
- The hybrid time model realisation: `MarketDataPort` emits `raw_events` +
  `consolidated_bars`; `StrategyPort` consumes `consolidated_bars`;
  `FillModelPort` consumes `raw_events`
- The `FillModelPort` shape (from SA-2 §3): `process_fill(ctx:
  MarketDataContext) -> FillResult`; concrete MVP1 impl is
  `OrderbookWalkFillModel` with `MakerTakerFeeModel` injection
- Reproducibility contract: `ClockPort` injected everywhere, `event_seq =
  SQLite rowid`, `payload_sha256` on every domain event, replay CLI
- Calibration as a CI gate: 6 P0 metrics with evidence-based thresholds,
  `CERTIFIED / UNTRUSTED` state machine gates live deployment

**4. Decisions Locked by This ADR** (bullet list, ~20 items)

- D-01: Single `FillModelPort` code path across all modes (no `if mode:`
  branches in fill logic)
- D-02: Adapter composition at the root — mode selection swaps ports, not
  branches logic
- D-03: `strategy_id` field on Order/Fill/Position/Balance/Portfolio
- D-04: `capital_budget: Money` per strategy in RiskEngine
- D-05: `OmsType` per strategy (NETTING for Swing MVP1)
- D-06: Hybrid time model — `raw_events` for fills, `consolidated_bars` for
  signals
- D-07: `MarketDataContext.orderbook: Optional[OrderBookSnapshot]` — same code
  path for real L2 (shadow) and synthetic (backtest)
- D-08: `OrderbookWalkFillModel` is the MVP1 fill model
- D-09: `MakerTakerFeeModel` with tier-aware Binance (0.10%/0.10% default) +
  Kraken (0.25%/0.40% default) fee rates
- D-10: Next-bar-open semantics for market orders (SA-2 AP-1)
- D-11: Limit orders fill when price crosses within a future bar; intrabar
  ordering via bar-open proximity heuristic (SA-2 §6)
- D-12: MVP1 signal stack = Donchian(20) 4H + Chandelier(22, 3.0) + ATR sizing
  1% risk / 5% cap (pre-reg v1 §5.4 binding; supersedes SA-7's original 10%)
- D-13: Position sizing = ATR-baseline capped by fractional Kelly (0.25× f*) or
  5% hard cap, whichever is smaller (see T-1)
- D-14: On-exchange stop-market (not bot-managed) for stop-loss; drawdown
  circuit-breaker as flash-crash defense (see T-7)
- D-15: `ClockPort` injected in every domain service (SA-8 S-01)
- D-16: `event_seq = SQLite rowid`, `payload_sha256 = SHA-256(sort_keys=True
  JSON)`, append-only triggers
- D-17: `RecordedExchangePort` logs `ExternalCallRecorded` events for replay
- D-18: Calibration harness gates live deployment via `CERTIFIED /
  UNTRUSTED` state in SQLite; CI script checks status before deploy
- D-19: 6 P0 calibration metrics (M-02, M-04, M-09, M-10, M-13, M-15) with
  SA-5 thresholds; testnet baseline subtracted from M-02
- D-20: Test pyramid manifest (Spec 7) is the acceptance gate every other spec
  must pass; includes 6 P0 operational defenses from SA-4

**5. Consequences** (~400 words)

*Enables:*

- MVP2 Portfolio Balancer plugs in as a second `StrategyAdapter` (no domain
  breaking change)
- Backtest becomes trivially a subset of shadow-mode calibration (same fill
  engine, replay recorded events)
- Compliance-ready audit trail from day 1 (Spanish AEAT 4-year retention
  satisfied via annual JSONL export)
- Chaos testing possible (inject flash crash, WebSocket drop, clock skew) at
  the port boundary

*Forbids:*

- Direct `datetime.now()` calls in domain code (import-linter contract)
- `if shadow_mode:` branches in fill logic (import-linter contract)
- Order aggregates without `strategy_id` (schema constraint)
- Deploying to live when `shadow_mode.status = UNTRUSTED` (CI gate)
- Skipping calibration baseline before MVP1 launch (Spec 6 P0)

*Requires:*

- Historical L2 data plan (OPEN DECISION §6) — either 30-day shadow bootstrap,
  Tardis.dev purchase, or OHLCV-only backtest
- Orderbook diff storage (issue #203) implemented before Spec 4 lands
- #196 shadow-mode PR amended to port-swap pattern (see T-4, §6)

*Deferred to MVP2:*

- Queue position simulator (M-07, SA-1 §2.5 Cont-Kukanov)
- HRP capital allocation across buckets (SA-1 §6)
- Latency simulation (SA-5 M-05)
- Multi-venue smart order routing (Cont-Kukanov)

**6. Follow-up Specs** (list of the 7 specs from §4 below with 1-line summaries)

**7. References** (all engram topic keys + primary papers + GH issues)

---

## 4. Proposed 7 Focused Specs

For each spec: Purpose, Scope, API Surface, Dependencies, Tests, PR sizing.

### Spec 1 — Domain Aggregates (bucket-aware)

**Purpose**: Establish the core domain vocabulary — Order, Fill, Position,
Balance, Portfolio — as bucket-aware value objects and aggregates. Every
aggregate carries `strategy_id` from day 1 so MVP2 Portfolio Balancer plugs
in without a schema change. This spec ships the ubiquitous language that
every other spec references. Confirmed by SA-6 finding 1 (NautilusTrader
`Position.strategy_id: StrategyId`) and locked by
`architecture/portfolio-multi-balance`.

**Scope**:

- **IN**: `StrategyId` value type, `OrderId`, `FillId`, `PositionId` ULIDs;
  `Order` (immutable with `strategy_id`, `opening_order_id`, `oms_type`,
  `time_in_force`); `Fill` (immutable, `strategy_id`, `commission`,
  `is_maker`); `Position` (mutable aggregate, `strategy_id`,
  `opening_order_id`); `Balance` (per-currency, per-strategy);
  `Portfolio` (root aggregate holding balances + positions across strategies,
  with `strategy_pnl: dict[StrategyId, dict[InstrumentId, Money]]`); domain
  events (`OrderPlaced`, `OrderFilled`, `PositionOpened`, `PositionClosed`,
  `BalanceUpdated`) with `event_seq` + `payload_sha256` (SA-8)
- **OUT**: OrderRouter (Spec 3), FillModelPort (Spec 2), persistence details
  (application layer)

**API surface** (Python-flavoured pseudocode):

```python
@dataclass(frozen=True)
class StrategyId:
    value: str  # e.g. "swing-donchian-4h"


@dataclass(frozen=True)
class Order:
    id: OrderId
    strategy_id: StrategyId
    instrument_id: InstrumentId
    side: OrderSide  # BUY | SELL
    order_type: OrderType  # MARKET | LIMIT | STOP_MARKET
    qty: Decimal
    limit_px: Decimal | None
    stop_px: Decimal | None
    time_in_force: TimeInForce  # GTC | IOC | FOK
    oms_type: OmsType  # NETTING | HEDGING (SA-6)
    submitted_at: datetime  # via ClockPort
    correlation_id: ULID


@dataclass(frozen=True)
class Fill:
    id: FillId
    order_id: OrderId
    strategy_id: StrategyId  # copied from Order at fill time
    instrument_id: InstrumentId
    fill_qty: Decimal
    fill_px: Decimal
    commission: Money
    is_maker: bool
    filled_at: datetime


class Position:
    id: PositionId
    strategy_id: StrategyId
    opening_order_id: OrderId
    instrument_id: InstrumentId
    qty: Decimal  # signed
    avg_entry_px: Decimal
    realised_pnl: Money
    unrealised_pnl: Money  # marked-to-market via portfolio update
    opened_at: datetime
    closed_at: datetime | None


class Portfolio:
    balances: dict[tuple[StrategyId, Currency], Balance]
    positions: dict[PositionId, Position]

    def strategy_pnl(self, strategy_id: StrategyId) -> dict[InstrumentId, Money]: ...
    def strategy_exposure(self, strategy_id: StrategyId) -> Money: ...
    def total_pnl(self) -> Money: ...
```

**Dependencies**: None (foundation).

**Tests**:

- Unit: constructor validation, immutability, `strategy_id` propagation from
  Order→Fill→Position
- Property (Hypothesis): PnL invariants (sum of realised + unrealised =
  total; per-strategy sums equal portfolio total)
- Contract: schema-versioned event serialisation deterministic across two
  invocations (SA-8 `payload_sha256` test)
- Mutation: kill rate ≥ 85% on domain services (SA-4 SEV-1 rubric)

**PR sizing**: **M** (~600 LoC domain + ~800 LoC tests). Mechanical but must
be complete before any other spec starts.

**Absorbs P0 defenses from SA-4**: P0-6 (all timestamps UTC via `ClockPort`),
part of P0-2 (state drift — via `payload_sha256` reconciliation possible).

---

### Spec 2 — FillModelPort + MVP1 Implementation

**Purpose**: Ship the SINGLE FILL CODE PATH used across backtest, shadow, and
live simulation modes. Implements `OrderbookWalkFillModel` (SA-1 §3, SA-2 §3)
and `MakerTakerFeeModel` with Binance + Kraken fee tier awareness (SA-3 §1.2).

**Scope**:

- **IN**: `FillModelPort` (Protocol/ABC); `MarketDataContext` value object;
  `FillResult` value object; `OrderbookSnapshot` value object;
  `OrderbookWalkFillModel` concrete impl (walks L2 for VWAP fill, computes
  maker/taker, applies fees); `MakerTakerFeeModel` concrete impl with
  Binance/Kraken tier tables; `SimulatedExchangeAdapter` that composes the
  above (implements `ExchangePort` for shadow/backtest); tick + lot
  quantization (SA-3 P0.1); MIN_NOTIONAL validation (SA-3 P0.6)
- **OUT**: MarketDataPort (Spec 4), OrderRouter capital enforcement (Spec 3),
  probabilistic queue models (P2 defer)

**API surface**: SA-2 §3 (already spelled out — full `FillModelPort`,
`FeeModelPort`, `MarketDataContext`, `FillResult`, `OrderbookWalkFillModel`,
`MakerTakerFeeModel`).

Key additions from synthesis:

```python
class FillModelPort(ABC):
    @abstractmethod
    def process_fill(self, ctx: MarketDataContext) -> FillResult: ...

    @abstractmethod
    def reset(self) -> None: ...


class SimulatedExchangeAdapter(ExchangePort):
    """Composes FillModelPort + local order state.

    Implements ExchangePort so it's a drop-in replacement for
    RealExchangeAdapter. Used in shadow + backtest.
    """

    def __init__(
        self,
        fill_model: FillModelPort,
        market_data: MarketDataPort,
        clock: ClockPort,
        ledger: EventLedger,
    ): ...

    def place_market_order(self, order: Order) -> OrderAck: ...
    def place_limit_order(self, order: Order) -> OrderAck: ...
    def cancel_order(self, order_id: OrderId) -> CancelAck: ...
```

**Dependencies**: Spec 1 (domain aggregates).

**Tests**:

- Unit: orderbook walk correctness for known input books (fixture-driven);
  maker/taker classification for crossing limits (SA-2 AP-3); tick/lot
  quantization; MIN_NOTIONAL rejection
- Property (Hypothesis): fill VWAP ≥ best price (never impossibly good);
  commission proportional to fill notional; `filled_qty ≤ order.qty` always;
  determinism under seed (SA-8 replay)
- Integration: `SimulatedExchangeAdapter` end-to-end against a canned
  orderbook + order sequence produces identical event log across two runs
- Calibration harness (from Spec 6): sim fills vs real testnet fills within
  M-02 thresholds
- Mutation: kill rate ≥ 85% on `OrderbookWalkFillModel`
- **Anti-Zipline test** (SA-2 AP-1): assert market orders NEVER fill at the
  current bar's close price when signal was generated on that bar
- **Anti-Freqtrade test** (SA-2 AP-2): grep-based import-linter contract —
  no `if.*shadow` or `if.*dry_run` in `fill.py` module

**PR sizing**: **L** (~1200 LoC + ~2000 LoC tests + calibration harness
smoke tests). Central piece.

**Absorbs P0 defenses from SA-4**: P0-3 (cost model unit tests with known
fixtures), P0-4 (silent order rejection — SimulatedExchange returns explicit
`OrderReject` events).

**Note on issue #198** (SimulatedExchangeAdapter): This spec IS #198's
implementation. #198 stays open as the tracking issue and gets a scope
comment pointing to Spec 2.

---

### Spec 3 — OrderRouter (bucket-aware, strategy-agnostic)

**Purpose**: Route orders from strategies to the exchange adapter, enforcing
per-strategy `capital_budget`, correlation limits, and risk gates before
dispatch. Implements the SA-1 × SA-7 composed sizing pipeline (T-1) and
SA-4's operational defenses (rate limits, orphan position guard).

**Scope**:

- **IN**: `OrderRouter` domain service; `RiskEngine` with
  `capital_budget: dict[StrategyId, Money]` (SA-6 finding 3);
  `SizingCalculator` implementing ATR-baseline + Kelly cap composition (T-1);
  drawdown circuit-breaker (SA-7 kill switches); correlation limit (BTC+ETH
  same-direction lockout, SA-7); orphan position invariant (SA-4 Knight
  Capital lesson); flash-crash guard on stop price validity (T-7)
- **OUT**: fill execution (Spec 2), signal generation (Spec 5), calibration
  metrics (Spec 6)

**API surface**:

```python
class OrderRouter:
    def __init__(
        self,
        risk: RiskEngine,
        sizing: SizingCalculator,
        exchange: ExchangePort,
        clock: ClockPort,
    ): ...

    def submit(self, signal: StrategySignal) -> RouteDecision:
        """Returns Accepted | RejectedByRisk | RejectedByBudget | Halted."""


class RiskEngine:
    capital_budget: dict[StrategyId, Money]
    max_concurrent_positions_per_strategy: int  # SA-7: 3
    max_exposure_pct_per_strategy: Decimal  # SA-7: 30%
    daily_drawdown_halt_pct: Decimal  # SA-7: 3%
    weekly_drawdown_halt_pct: Decimal  # SA-7: 8%
    peak_drawdown_halt_pct: Decimal  # SA-7: 20%

    def check(
        self,
        order: Order,
        portfolio: Portfolio,
    ) -> RiskDecision: ...


class SizingCalculator:
    """T-1 resolution: ATR baseline capped by fractional Kelly (or 5% floor)."""

    risk_pct_target: Decimal = Decimal("0.01")  # SA-7 1%
    stop_atr_multiple: Decimal = Decimal("2.0")  # SA-7
    equity_cap_pct: Decimal = Decimal("0.05")  # pre-reg v1 §5.4 binding
    kelly_fraction: Decimal = Decimal("0.25")  # SA-1 quarter Kelly
    kelly_hard_cap_pct: Decimal = Decimal("0.05")  # SA-1 5%

    def size(
        self,
        equity: Money,
        entry_px: Decimal,
        atr: Decimal,
        f_star_estimate: Decimal | None,
    ) -> Decimal: ...
```

**Dependencies**: Spec 1 (domain), Spec 2 (ExchangePort).

**Tests**:

- Unit: sizing composition edge cases (ATR=0, Kelly f*=None, equity below
  min notional); risk decisions matrix
- Property: sizing never exceeds any of ATR-cap, Kelly-cap, equity-cap;
  capital_budget invariant (sum of open orders per strategy ≤ budget)
- Integration: signal → sizing → risk → exchange happy-path + rejection paths
- **Orphan position test** (SA-4 P0): every position in `Portfolio` traces
  to an OrderId + StrategyId; raise if not
- **Rate-limit weight tracker test** (SA-4 P0): 80% ceiling; exponential
  backoff on 429
- Chaos: inject flash-crash (price gap 20% in 100ms) → circuit-breaker fires;
  inject WebSocket disconnect → state reconciliation before resume
- Mutation ≥ 85%

**PR sizing**: **L** (~800 LoC + ~1500 LoC tests + chaos scenarios).

**Absorbs P0 defenses from SA-4**: P0-1 (REST reconciliation on WS reconnect
— trigger via router), P0-4 (silent order rejection — explicit REST
confirmation timeout), P0-5 (rate limit weight tracker), part of P0-2
(state drift — orphan position invariant).

---

### Spec 4 — MarketDataPort + ClockPort

**Purpose**: Establish the two ports that inject market data and time into
the engine. Realises the locked hybrid time model
(`architecture/time-model-hybrid`) with a `raw_events` channel (orderbook
diffs + trade prints for the fill layer) and a `consolidated_bars` channel
(bar-close events for the strategy layer). Ships orderbook diff storage
(supersedes #139 per `decision/kline-storage-superseded`, tracked in #203).

**Scope**:

- **IN**: `MarketDataPort` protocol; `ClockPort` protocol (SA-8 S-01);
  `SimulatedClock` + `WallClockClock` concrete impls; `Consolidator`
  domain service (LEAN-inspired, SA-2 §1.2); `LiveWebSocketMarketDataAdapter`
  (Binance + Kraken WS with bootstrap protocols, SA-3 §1.3); orderbook diff
  storage schema + append-only writer (SQLite or Parquet — decide in spec
  design); Kraken CRC32 checksum validation (SA-3 P1.4); Binance
  buffer+snapshot+updateId reconciliation dance (SA-3 §1.3 bootstrap
  protocol); WebSocket 1006 gap detection with REST reconciliation (SA-4
  P0-1); startup clock offset validation (SA-4 P0-2)
- **OUT**: fill logic (Spec 2), strategy consumption (Spec 5), historical
  backtest replay (Spec 6 owns replay CLI; Spec 4 provides
  `HistoricalMarketDataAdapter` for its use)

**API surface**:

```python
class ClockPort(Protocol):
    def now(self) -> datetime: ...  # UTC always
    def now_ns(self) -> int: ...  # nanosecond precision for event_ts


class MarketDataPort(Protocol):
    def subscribe_raw_events(self, instrument_id: InstrumentId) -> AsyncIterator[MarketEvent]:
        """L2 diffs + trade prints in strict timestamp order."""

    def subscribe_bars(
        self, instrument_id: InstrumentId, timeframe: Timeframe
    ) -> AsyncIterator[Bar]:
        """Bar-close events (via consolidator)."""

    def get_orderbook_snapshot(self, instrument_id: InstrumentId) -> OrderbookSnapshot: ...


@dataclass(frozen=True)
class MarketEvent:
    instrument_id: InstrumentId
    ts_event: int  # exchange timestamp (ns)
    ts_local: int  # local receive timestamp (ns) for latency measurement
    payload: OrderbookDiff | TradePrint


class Consolidator:
    """Aggregates raw events into bars. LEAN pattern (SA-2 §1.2)."""

    def update(self, event: MarketEvent) -> Bar | None: ...
```

**Dependencies**: Spec 1 (domain events).

**Tests**:

- Unit: consolidator correctness (open/high/low/close/volume from tick
  stream); tick/lot quantization; Kraken CRC32 validation on known book
- Property: consolidated bar high ≥ every trade price during window; consolidated
  bar volume = sum of trade volumes
- Integration: replay a recorded WebSocket capture through the adapter → same
  event sequence emitted; replay through Consolidator → deterministic bars
- **Clock drift test** (SA-4 P0-2): mock `WallClockClock` returning drift > 5s
  → adapter halts and alerts
- **Bootstrap reconciliation test**: buffered events with gap in updateId →
  reconnect+reboot; Binance official protocol conformance
- **WebSocket 1006 test** (SA-4 P0-1): simulate abnormal close → REST
  reconciliation before resume; no missed fills
- Mutation ≥ 85%

**PR sizing**: **L** (~1000 LoC + ~1800 LoC tests + WS chaos scenarios).
This is the biggest spec after Spec 2.

**Absorbs P0 defenses from SA-4**: P0-1 (REST reconciliation on WS reconnect),
P0-2 (clock offset validation), P0-6 (UTC everywhere).

---

### Spec 5 — StrategyContract (SwingStrategyAdapter for MVP1)

**Purpose**: Define the port interface that all strategies (Swing MVP1,
Portfolio Balancer MVP2, future arbitrage MVP3+) implement. Ship
`SwingStrategyAdapter` for MVP1 using SA-7's recommended stack (Donchian +
Chandelier + ATR sizing + ADX/EMA/OBV/ATR-spike filters).

**Scope**:

- **IN**: `StrategyPort` protocol; `StrategyConfig` (SA-6:
  `capital_budget`, `oms_type`, `status`); `StrategyStatus` state machine
  (`ACTIVE | DRAINING | DISABLED`, SA-6); `SwingStrategyAdapter` impl with
  Donchian(20) 4H breakout + regime filters (ADX(14)>25, EMA(200) daily
  above, OBV monotone, ATR<1.5×20MA); Chandelier(22, 3.0) trailing stop
  emission; entry limit at `high + 0.1 × ATR`; System 1 fail-safe (skip
  after prior winning breakout, SA-7)
- **OUT**: Sizing (Spec 3), stop execution (Spec 3), Portfolio Balancer
  (MVP2)

**API surface**:

```python
class StrategyPort(Protocol):
    strategy_id: StrategyId
    config: StrategyConfig

    def on_bar(self, bar: Bar, portfolio: Portfolio) -> list[StrategySignal]:
        """Called by StrategyRunner on every consolidated bar close.
        Returns 0+ signals; router handles sizing + risk."""

    def on_fill(self, fill: Fill) -> None:
        """State update after fill (e.g., trailing stop level)."""


@dataclass(frozen=True)
class StrategyConfig:
    strategy_id: StrategyId
    capital_budget: Money  # SA-6
    oms_type: OmsType  # NETTING for Swing
    status: StrategyStatus  # ACTIVE | DRAINING | DISABLED
    timeframe: Timeframe


@dataclass(frozen=True)
class StrategySignal:
    strategy_id: StrategyId
    instrument_id: InstrumentId
    intent: SignalIntent  # ENTER | EXIT | ADJUST_STOP
    order_type: OrderType  # LIMIT | MARKET | STOP_MARKET
    limit_px: Decimal | None
    stop_px: Decimal | None
    stop_atr: Decimal  # for sizing calc — SA-7's stop distance
    atr_context: Decimal  # for M-06 regime tagging in calibration
    f_star_estimate: Decimal | None  # T-1: enables Kelly cap; None → 5% floor
    metadata: dict  # signal source, confidence, etc.


class SwingStrategyAdapter(StrategyPort):
    donchian_period: int = 20
    atr_period: int = 14
    adx_period: int = 14
    adx_threshold: Decimal = Decimal("25")
    ema_daily_period: int = 200
    chandelier_period: int = 22
    chandelier_multiple: Decimal = Decimal("3.0")
    entry_atr_buffer: Decimal = Decimal("0.1")
    stop_atr_multiple: Decimal = Decimal("2.0")
```

**Dependencies**: Spec 1 (domain), Spec 4 (bars from `MarketDataPort`).

**Tests**:

- Unit: indicator correctness (Donchian, ATR, ADX, Chandelier — cross-check
  vs TA-Lib reference values); regime filter logic; System 1 fail-safe
- Property: Chandelier only moves up (monotone); no signal when ADX<25;
  entry only when price>EMA(200)
- Integration: replay canned bar sequence → deterministic signal sequence
- **DST timezone test** (SA-4 P0-6): strategy uses UTC only, never local time
- **Multi-timeframe closed-bar test** (SA-4 P1): informative-pair signals
  compute on closed bar only; no lookahead
- Mutation ≥ 85%

**PR sizing**: **M** (~700 LoC + ~1200 LoC tests + backtest fixtures).

**Absorbs P0 defenses from SA-4**: part of P0-6 (UTC only).

---

### Spec 6 — Calibration Harness

**Purpose**: Implement SA-5's calibration protocol as an automated CI gate.
6 P0 metrics with evidence-based thresholds; testnet baseline subtraction
(T-5); `CERTIFIED / UNTRUSTED` state machine; replay CLI (SA-8 §5) for
divergence debugging. This spec is what makes the engine defensible: without
it, shadow-mode is fantasy generation (SA-5 §0).

**Scope**:

- **IN**: `CalibrationService` domain service (pure — no I/O); metric
  computation modules (M-02 KS+Wasserstein+bps distribution, M-04 binomial
  bias, M-09 Pearson r + bootstrap CI, M-10 Sharpe permutation, M-13
  lookahead audit, M-15 fee delta); `CalibrationReport` value object with
  JSON schema (SA-5 §9); certification lifecycle state machine; sim↔real
  fill-pair matcher (5s window default, SA-5 §12.1); testnet baseline
  measurement job; replay CLI (`hermes replay --from --to --compare`);
  `RecordedExchangePort` + `ExternalCallRecorded` event (SA-8)
- **OUT**: P1 metrics (M-06, M-11, M-14 — deferred); P2 metrics (M-05,
  M-07, M-08, M-12 — deferred)

**API surface**: SA-5 §12.3 already spelled out.

Additions from synthesis:

```python
class CalibrationService:
    def record_pair(self, sim: SimulatedFill, real: RealFill) -> None: ...
    def run_rolling(self, window: int = 1000) -> CalibrationReport: ...
    def check_lookahead(self, event_log: list[FillEvent]) -> list[LookaheadViolation]: ...
    def certify(self, report: CalibrationReport, human_approved: bool) -> ShadowModeStatus: ...


class ShadowModeGate:
    """CI-invoked. Reads shadow_mode.status from SQLite.
    Exits non-zero when deploy-to-live is attempted with UNTRUSTED status."""

    def check_deploy_allowed(self, target_env: Env) -> None: ...
```

**Dependencies**: Spec 1 (domain events), Spec 2 (SimulatedExchangeAdapter
for sim fills), Spec 4 (MarketDataPort for real fills feed).

**Tests**:

- Unit: KS test wrapper, Wasserstein, Pearson r bootstrap CI, lookahead
  detector, bias binomial test (SA-5 §11.1)
- Property: percentile monotonicity, bias ratio symmetry, threshold
  boundary correctness (SA-5 §11.2)
- Integration: end-to-end with fake sim + fake real ports where sim is
  known-biased → BLOCK after 1000 fills; known-matching → CERTIFIED after
  200 fills (SA-5 §11.3)
- **Regression golden files** (SA-5 §11.5): scenario fixtures with
  pre-computed expected reports
- **State machine test**: UNTRUSTED cannot auto-restore; requires human
  approval CLI call (SA-5 §10)
- **Baseline measurement test**: 7-day testnet-vs-testnet baseline job
  produces expected bps distribution
- Mutation ≥ 85% (SA-5 §11.4 — critical mutations to kill listed)

**PR sizing**: **L** (~900 LoC + ~1500 LoC tests + baseline measurement
job + replay CLI).

**Absorbs P0 defenses from SA-4**: reinforces P0-3 (cost model tests via
M-15), P0-4 (silent rejection detection via M-06 in P1).

**Note on issue #201**: This spec IS #201's implementation. Update #201's
scope to reference Spec 6.

---

### Spec 7 — Test Pyramid Manifest

**Purpose**: Define the acceptance gate every other spec must pass. Codifies
the test tiers from `decision/paper-trading-foundational` §4 (unit +
property + integration + calibration + mutation) plus SA-4's 6 P0
operational defenses as MANDATORY chaos tests. Ships the `pytest` markers,
CI job matrix, and coverage thresholds per tier.

**Scope**:

- **IN**: `pytest.ini` markers (`unit`, `property`, `integration`,
  `calibration`, `chaos`, `mutation`); CI job matrix (which markers run
  in PR CI vs nightly vs weekly); coverage thresholds per module tier
  (domain ≥ 95%, adapter ≥ 90%, application ≥ 90% per hermes ADR-007);
  mutation testing config (`mutmut` targets + kill rate ≥ 85%); import-linter
  contracts enforcing (a) no `datetime.now()` in domain, (b) no `if
  shadow_mode` in fill logic, (c) no cross-strategy imports; 6 P0 chaos
  test manifest (WebSocket 1006, clock drift, rate limit, silent rejection,
  DST, partial fill miscount); SEV-1 annotation lint (SA-4 rubric)
- **OUT**: individual test files (each spec ships its own); dashboards
  (out of MVP1)

**API surface**: config files + CI YAML, no runtime API.

Key deliverables:

- `pyproject.toml` — pytest markers + coverage thresholds per package
- `.github/workflows/tests.yml` — matrix: unit+property (every PR),
  integration (every PR), calibration (nightly against testnet), chaos
  (nightly), mutation (weekly)
- `importlinter.toml` — three contracts above
- `docs/testing/manifest.md` — the pyramid diagram + tier definitions

**Dependencies**: None (foundation) — SHOULD LAND FIRST alongside Spec 1.

**Tests**:

- Meta: assert markers registered; assert CI matrix contains all required
  jobs; assert import-linter contracts execute
- **Self-test**: the manifest itself has a `test_manifest.py` that runs the
  6 P0 chaos tests as an integration smoke — proves the gate can fail

**PR sizing**: **S** (~200 LoC config + ~300 LoC meta-tests + docs). Small
but critical.

**Absorbs P0 defenses from SA-4**: ALL 6 P0 defenses appear as MANDATORY
chaos tests in this manifest (as tests OWNED BY OTHER SPECS but LISTED
here as gate criteria).

---

## 5. Spec Landing Sequence

### Dependency DAG

```text
Spec 7 (Test Pyramid Manifest) ─────┐
                                     ├──> gate for all other specs
Spec 1 (Domain Aggregates) ─────────┤
                                     │
                Spec 4 (MarketData + Clock) ──┐
                                              │
Spec 2 (FillModelPort) <──────────────────────┤
                                              │
                                Spec 3 (OrderRouter) <── depends on Spec 2 + Spec 1
                                              │
                                Spec 5 (StrategyContract) <── depends on Spec 4 + Spec 1
                                              │
                                Spec 6 (Calibration) <── depends on Spec 2 + Spec 4
```

### Recommended sequence (gate-FIRST)

1. **Spec 7 (Test Pyramid Manifest)** — **S** — establishes the acceptance
   gate FIRST so every subsequent spec is validated against a stable target
2. **Spec 1 (Domain Aggregates)** — **M** — the ubiquitous language; nothing
   else can compile without this
3. **Spec 4 (MarketDataPort + ClockPort)** — **L** — enables both real data
   ingestion (needed for Spec 2 shadow tests) and consolidator (needed for
   Spec 5)
4. **Spec 2 (FillModelPort + SimulatedExchangeAdapter)** — **L** — the
   central fill code path
5. **Spec 3 (OrderRouter)** — **L** — wires signals to fills with risk gates
6. **Spec 5 (StrategyContract + SwingStrategyAdapter)** — **M** — the first
   real signal source
7. **Spec 6 (Calibration Harness)** — **L** — the final gate; verifies the
   whole stack

### Rationale for gate-FIRST vs engine-FIRST

The operator asked whether to do gate-first or engine-first. The synthesis
recommends **gate-FIRST for exactly one reason**: the gate is a SMALL
deliverable (~500 LoC total) that makes every subsequent spec's PR review
faster. Reviewers can point at the manifest, say "does this pass tier X?",
and merge in minutes rather than negotiating tier definitions in every PR.

Engine-first would inflate every subsequent PR because tier expectations
get renegotiated. The failure mode is well-documented: SA-4's Knight Capital
case study is FUNDAMENTALLY a test-manifest-missing story — no automated
verification of the deployment invariant. That's exactly what Spec 7 prevents.

**Trade-off**: Spec 7 has no runnable engine code — feels like slow start.
Mitigation: Spec 1 lands in parallel (they don't depend on each other);
week 1 delivers both. First real engine code (Spec 4) starts week 2.

### Timeline estimate (rough, 1 engineer, no distractions)

- Week 1: Spec 7 + Spec 1 (parallel) — 1S + 1M
- Week 2-3: Spec 4 — 1L
- Week 4-5: Spec 2 — 1L
- Week 6-7: Spec 3 — 1L
- Week 8: Spec 5 — 1M
- Week 9-10: Spec 6 — 1L
- Week 11-12: baseline measurement + first CERTIFIED shadow mode run

**Total: ~12 weeks to first CERTIFIED shadow-mode**. This is the point at
which #175 (shadow-mode flag) is safely activatable in production.

---

## 6. Open Decisions for Operator

### OD-1: Historical L2 data source

**OPERATOR DECISION REQUIRED.**

The synthesis identifies that neither Binance nor Kraken provides historical
L2 orderbook data publicly (SA-3 §1.9). This blocks tick-level backtest before
shadow-mode has accumulated 30-60 days of data.

Options:

| Option | Cost | Time-to-first-tick-backtest | Fidelity |
| -------- | ------ | ---------------------------- | ---------- |
| **A. Wait 30-60d for shadow-mode capture** | $0 | 30-60 days | HIGH — real data from our own capture |
| **B. Buy Tardis.dev historical WebSocket replay** | $50-200/mo | Immediate | HIGH — full L2 diffs |
| **C. Buy Kaiko L2 snapshots** | $200-500/mo | Immediate | MEDIUM — 1s granularity snapshots |
| **D. OHLCV-only backtest, defer tick backtest to post-shadow** | $0 | Immediate (OHLCV only) | LOW — bar-derived fills, ±10% accuracy |

**Recommendation**: Option D for MVP1 launch + Option A (shadow capture) as
the natural fidelity-improvement path. If budget allows Option B, it
accelerates parameter tuning by 30-60 days.

**Blocker?** No — Spec 2's `OrderbookWalkFillModel` handles both
`ctx.orderbook` present (real L2) and absent (OHLCV-derived synthetic book).

### OD-2: `#196` shadow-mode PR relationship

**OPERATOR DECISION REQUIRED.**

Per §2 T-4, the #196 PR (docs+openspec for #175 shadow-mode flag) currently
proposes a `ShadowGate` DECORATOR that wraps adapter write methods. The
synthesis recommends the PORT-SWAP pattern instead (mode selection swaps
adapters at composition root; no decorators, no `if shadow:` in write paths).

Options:

- **A. Amend #196** to adopt port-swap pattern; keep the same 3-gate
  invariant (`SHADOW + ENV + ALLOW_MAINNET`) but implement via composition
  root, not decorator. New sub-PR to update the openspec change.
- **B. Close #196**; reopen a new PR after Spec 2 lands (SimulatedExchangeAdapter).
  The 3-gate invariant becomes a Spec 3 concern (OrderRouter refuses to
  submit when guards fail).
- **C. Merge #196 as-is** with the decorator; refactor to port-swap in a
  follow-up PR after Spec 2. Risky — introduces the anti-pattern first.

**Recommendation**: **Option A** — amend #196. The 3-gate invariant is
valuable prior art; only the implementation mechanism changes. Preserves
work-in-progress and issue history.

**Blocker?** No — #196 is on hold pending this synthesis; the amendment can
happen after ADR lands.

### OD-3: `#175` scope after ADR

**OPERATOR DECISION REQUIRED.**

Issue #175 is titled "shadow-mode flag + adapter-layer order interception."
After the ADR + Spec 2, the interception mechanism CHANGES from
decorator-based to port-swap-based. Options:

- **A. Rewrite #175** description to reflect port-swap pattern; retitle to
  "shadow-mode as composition-root port selection"
- **B. Close #175** as superseded by Spec 2 + Spec 3; reference in the
  closure that the intent lives on but the mechanism changed
- **C. Split #175** into a smaller "3-gate config invariant" issue + close
  the interception part as superseded

**Recommendation**: **Option A** — rewrite. #175 is a good tracking issue
for the operator-facing UX (the 3-gate config, the `hermes health` mode
display, the SHADOW tag on notifications). Only the internal implementation
mechanism changes.

### OD-4: Kelly f* estimator trigger

**Recommend adopt without operator input.**

Per T-1, the Kelly f* estimator activates only when ≥30 OOS walk-forward
trades are available. Before that, sizing degenerates to ATR-baseline capped
by the 5% hard cap. This is safe and requires no operator sign-off.

### OD-5: Testnet baseline measurement duration

**Recommend adopt without operator input.**

Per T-5, run a 7-day sim-off baseline before applying M-02 thresholds. This
is a Spec 6 concern, hard-coded in the harness.

### OD-6: MVP1 exchange coverage

**OPERATOR DECISION REQUIRED (minor).**

SA-3 documents Binance + Kraken. SA-7 assumes Binance-first based on fee
economics (Kraken retail 4× Binance taker cost). Options:

- **A. MVP1 = Binance-only shadow mode; Kraken adapter deferred**
- **B. MVP1 = Binance + Kraken shadow mode from day 1**

**Recommendation**: **Option A**. Reduces MVP1 scope by ~30% (single fee
tier, single WS bootstrap protocol, single set of instrument filters).
Kraken adapter is already partially built (PR #193 public adapter merged);
integration is straightforward once Binance is CERTIFIED.

### OD-7: Storage backend for orderbook diffs

**OPERATOR DECISION REQUIRED.**

SA-3 §3 estimates 145 GB/year compressed for BTC+SOL at 100ms depth diffs.
Options:

- **A. SQLite (append-only table)** — simplest; hermes already uses SQLite;
  performance OK up to ~10K writes/sec (well above our need); query slower
  than columnar
- **B. Parquet files (rolling daily)** — columnar; better read perf for
  backtest replay; adds new dependency; more operational surface
- **C. TimescaleDB / DuckDB** — best analytics; adds a service

**Recommendation**: **Option A** for MVP1 (consistency with existing
storage; hermes AEAT retention already uses SQLite pattern per SA-8).
Revisit Option B if backtest replay perf becomes a bottleneck.

**Blocker?** Yes for Spec 4. Requires answer before spec design.

### OD-8: Execution mode for spec landing

Standard SDD execution-mode question — automatic (run all 7 specs
back-to-back via sub-agents) vs interactive (review after each spec).

**Recommendation**: **Interactive** for the first 3 specs (Spec 7 + Spec 1 +
Spec 4) to establish rhythm; switch to **Automatic** for Spec 2-6 once the
patterns are stable.

---

## 7. GH Issues Impact

Which existing issues change scope, are superseded, or become sub-issues.

| Issue | Current title | Impact | Action |
| ------- | --------------- | -------- | -------- |
| **#25** | Phase 1 · Paper-trading engine | Becomes PARENT for the 7 specs | Update body to reference umbrella ADR + link 7 spec issues (to be filed) |
| **#26** | (Phase 1 backtest) | Absorbed into Spec 2 + Spec 6 | Close as superseded; reference Spec 2 + 6 in closure |
| **#27** | (Phase 1 signal engines) | Absorbed into Spec 5 | Close as superseded; reference Spec 5 |
| **#175** | Shadow-mode flag + adapter interception | Rewritten per OD-3 | Rewrite scope; keep as UX/config tracking issue |
| **#196** | ADR + openspec for #175 (docs) | Amended per OD-2 | Wait for ADR; amend PR to port-swap pattern |
| **#197** | Rebalancer + DonchianEngine domain classes | Split: DonchianEngine → Spec 5; Rebalancer → MVP2 | Split into two issues; deprioritize Rebalancer to MVP2 milestone |
| **#198** | SimulatedExchangeAdapter | IS Spec 2's implementation | Add scope comment: "See Spec 2 in synthesis; this issue tracks Spec 2 delivery" |
| **#199** | ExchangePort method contract | Merged INTO Spec 2 (port defined alongside first impl) | Close as absorbed; reference Spec 2 |
| **#200** | RebalanceUC + SwingSignalUC use cases | Split: SwingSignalUC → Spec 5 application layer; RebalanceUC → MVP2 | Split; MVP2 for Rebalance |
| **#201** | Backtest calibration harness | IS Spec 6's implementation | Add scope comment: "See Spec 6 in synthesis" |
| **#203** | Orderbook diff stream storage (supersedes #139) | Absorbed INTO Spec 4 | Add scope comment: "Delivered as part of Spec 4" |
| **#139** | Kline storage (already superseded by #203) | Already handled | No action |

**New issues to file** (one per spec):

- feat(specs): Spec 1 — Domain Aggregates (bucket-aware)
- feat(specs): Spec 2 — FillModelPort + SimulatedExchangeAdapter
- feat(specs): Spec 3 — OrderRouter (bucket-aware capital enforcement)
- feat(specs): Spec 4 — MarketDataPort + ClockPort + orderbook diff storage
- feat(specs): Spec 5 — StrategyContract + SwingStrategyAdapter
- feat(specs): Spec 6 — Calibration harness (SA-5 protocol)
- feat(specs): Spec 7 — Test pyramid manifest

All 7 filed as sub-issues of #25 (parent epic).

---

## 8. P0 Defense Mapping (SA-4 × Specs)

SA-4's 6 P0 operational defenses, mapped to spec ownership. None float
separately — every P0 lives inside a spec's test requirements.

| P0 ID | Defense | Owner spec(s) | Test type |
| ------- | --------- | --------------- | ----------- |
| **P0-1** | REST reconciliation on every WebSocket reconnect | Spec 4 (MarketDataPort) + Spec 3 (OrderRouter triggers reconciliation) | Chaos test in Spec 4; state invariant in Spec 3 |
| **P0-2** | Startup clock offset validation gate | Spec 4 (ClockPort validation on startup) | Unit test simulating drift; integration test with mock ntp source |
| **P0-3** | Cost model unit tests with known fixtures | Spec 2 (FeeModelPort fixture tests) | Fixture-driven unit tests |
| **P0-4** | Silent order rejection: explicit REST confirmation with timeout | Spec 3 (OrderRouter dispatches with timeout) + Spec 2 (SimulatedExchangeAdapter returns explicit reject events) | Integration test; timeout injection chaos |
| **P0-5** | Rate limit weight tracker with 80% ceiling + exponential backoff | Spec 3 (OrderRouter enforcement) + Spec 4 (MarketDataPort read-side tracking) | Chaos test with rate-limit injection |
| **P0-6** | All timestamps in UTC; no local time | Spec 1 (domain), Spec 4 (ClockPort), Spec 5 (strategy indicators) | Import-linter contract in Spec 7; unit tests in each spec |

**Additionally, non-P0 SA-4 defenses folded in:**

- P1 partial fill fill-event-driven position tracking → Spec 1 (Position
  aggregate) + Spec 2 (Fill emission on every level walked)
- P1 lookahead invariant assertion → Spec 6 M-13 (continuous check) + Spec 7
  import-linter contract (structural prohibition)
- P1 orphan position invariant → Spec 3 (RiskEngine startup check)
- P1 multi-timeframe closed-bar mode → Spec 5 (strategy tests)

**Not in scope for MVP1 (per SA-4 P2)**: Exchange health monitor (ACK
timeout, REST vs WS cross-validation), Deflated Sharpe Ratio reporting,
withdrawable balance monitoring. Defer to MVP1.5 or MVP2.

---

## 9. Confidence

Per-section confidence assessment.

| Section | Confidence | Notes |
| --------- | ----------- | ------- |
| 1. Executive summary | HIGH | Consolidates well-supported findings |
| 2. Tension reconciliation T-1 to T-9 | HIGH on T-1..T-4, T-6..T-9; MEDIUM on T-5 | T-5 baseline value is empirical |
| 3. ADR structure | HIGH | Standard ADR sections; alternatives citations verified |
| 4. Spec 1 (Domain) | HIGH | SA-6 provides confirmed field list |
| 4. Spec 2 (FillModelPort) | HIGH | SA-2 §3 provides full API shape |
| 4. Spec 3 (OrderRouter) | HIGH on structure; MEDIUM on sizing coefficients | SA-7 defaults — needs empirical tuning |
| 4. Spec 4 (MarketData+Clock) | HIGH | SA-3 provides exchange protocols; SA-8 provides ClockPort |
| 4. Spec 5 (StrategyContract) | HIGH on port shape; MEDIUM-HIGH on SA-7 stack | Backtest is 25-trade sample only |
| 4. Spec 6 (Calibration) | HIGH on structure; MEDIUM on threshold values | SA-5 acknowledges empirical dependency |
| 4. Spec 7 (Test manifest) | HIGH | Standard test pyramid + hermes conventions |
| 5. Spec sequence | HIGH | DAG is unambiguous; gate-first rationale strong |
| 6. Open decisions | HIGH | All clearly bracketed for operator |
| 7. GH issues impact | HIGH | Verified against gh CLI |
| 8. P0 mapping | HIGH | Every P0 has an unambiguous owner |

Overall synthesis confidence: **HIGH**. Where MEDIUM, the exact numeric
values are the reason (thresholds, coefficients) — the mechanisms and
structure are HIGH.

---

## 10. Cross-References

Every claim in this synthesis traces back to a sub-agent report or locked
decision. This section is the audit index.

**Executive summary claims**:

- Hexagonal engine pattern → SA-2 §2 (comparison matrix), §5 (wire-up)
- Bucket-aware domain → SA-6 finding 1; `architecture/portfolio-multi-balance`
- Hybrid time model → `architecture/time-model-hybrid`
- OrderbookWalkFillModel → SA-1 §3, SA-2 §3, SA-3 §2 P0.3
- MVP1 signal stack → SA-7 recommended stack
- Calibration gates → SA-5 §10
- Reproducibility → SA-8 architecture recommendations 1-6

**Tension T-1 to T-9**: cited inline with SA-N §M throughout §2.

**ADR alternatives**:

- Alt 1 (NautilusTrader) → SA-2 §1.1, SA-6 findings 2-3
- Alt 2 (LEAN) → SA-2 §1.2, SA-6 finding 4
- Alt 3 (Freqtrade) → SA-2 §1.3, SA-0 issues #12662 #12675
- Alt 4 (VectorBT) → SA-2 §1.6, SA-0 landscape
- Alt 5 (mega-spec) → `decision/paper-trading-foundational` §3

**ADR consequences**:

- Enables MVP2 balancer → SA-6 §MVP1 required fields
- Enables replay debugging → SA-8 architecture #5
- Forbids `if shadow_mode:` → SA-2 AP-2
- Forbids direct `datetime.now()` → SA-8 S-01
- Requires historical L2 plan → SA-3 §4

**Spec 1 details**:

- `strategy_id` propagation → SA-6 finding 1
- `payload_sha256` → SA-8 architecture #1
- `event_seq = SQLite rowid` → SA-8 architecture #3

**Spec 2 details**:

- `FillModelPort` shape → SA-2 §3
- Anti-patterns AP-1..AP-7 → SA-2 §4
- `OrderbookWalkFillModel` → SA-2 §3, SA-1 §3
- `MakerTakerFeeModel` fee tables → SA-3 §1.2

**Spec 3 details**:

- Sizing composition → §2 T-1 (SA-1 §6 × SA-7 sizing rule)
- Capital budget per strategy → SA-6 finding 3
- Drawdown circuit-breaker → SA-7 kill switches
- Correlation limit → SA-7 correlation warning
- Orphan position invariant → SA-4 Knight Capital lesson

**Spec 4 details**:

- Hybrid time model → `architecture/time-model-hybrid`
- Binance bootstrap protocol → SA-3 §1.3 bootstrap protocol
- Kraken CRC32 → SA-3 §1.3
- WS 1006 handling → SA-4 P0-1
- Storage budget → SA-3 §3
- Orderbook diff storage → `decision/kline-storage-superseded`, issue #203

**Spec 5 details**:

- SwingStrategyAdapter stack → SA-7 §MVP1 recommended stack
- Regime filters → SA-7 §regime detection thresholds
- StrategyStatus state machine → SA-6 hot-swap recommendation

**Spec 6 details**:

- 6 P0 metrics → SA-5 §8
- Certification lifecycle → SA-5 §10
- Statistical tests → SA-5 §3
- JSON schema → SA-5 §9
- Test pyramid → SA-5 §11
- Testnet baseline subtraction → §2 T-5, SA-5 §13 open Q#2

**Spec 7 details**:

- Test tiers → `decision/paper-trading-foundational` §4
- Import-linter contracts → SA-2 §4 AP-2 (anti-branching), SA-8 S-01
  (no `datetime.now()` in domain)
- 6 P0 chaos tests → SA-4 P0 items

**P0 defense mapping** (§8): each row cites SA-4's operational failures section
directly.

---

## Appendix A — Documents produced by this synthesis effort

- Umbrella ADR (to be written from §3 outline)
- Spec 1 through Spec 7 documents (to be written from §4)
- 7 new GH issues under #25 parent (to be filed per §7)
- #196 amendment PR (per OD-2)
- #175 rewrite (per OD-3)
- Scope comments on #197, #198, #199, #200, #201, #203 (per §7)

## Appendix B — Full report file paths

- `docs/research/paper-trading/sa0-landscape.md`
- `docs/research/paper-trading/sa1-academic.md`
- `docs/research/paper-trading/sa2-deep-code.md`
- `docs/research/paper-trading/sa3-microstructure.md`
- `docs/research/paper-trading/sa4-post-mortems.md`
- `docs/research/paper-trading/sa5-calibration.md`
- `docs/research/paper-trading/sa6-multi-strategy.md`
- `docs/research/paper-trading/sa7-strategy-design.md`
- `docs/research/paper-trading/sa8-reproducibility.md`
- `docs/research/paper-trading/SYNTHESIS.md` (this document)

Engram topic key for this synthesis: `research/paper-trading-synthesis`.
