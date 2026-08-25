# SA-5 Calibration Harness Protocol

**Date**: 2026-08-24
**Author**: SA-5 sub-agent (general-purpose, sonnet)
**Engram topic key**: `research/paper-trading-sa5-calibration`
**Status**: FINAL

---

## SA-5: Calibration Harness Protocol Design

## Hermes Paper-Trading Engine — Spec 6 Candidate

**Date**: 2026-08-24
**Role**: SA-5 (calibration methodology)
**Feeds**: hermes Spec 6 (shadow-mode certification)
**Depends on**: SA-0 landscape scouting (`research/paper-trading-sa0-landscape`)
**Time-box**: ~90 min

---

## 0. MOTIVATION

The Wiecki/Quantopian post-mortem (SSRN 2745220) showed that Sharpe ratio predicts
out-of-sample performance with R² < 0.025 when fill models are not calibrated to
reality. hermes runs in shadow-mode (real-time simulator against live orderbook)
alongside Binance testnet. Without a rigorous calibration harness, shadow-mode is
a fantasy generator, not a product.

**Core invariant**: every simulated fill produced by `FillModelPort` must be
continuously compared against real fills from Binance testnet (and eventually
Kraken). The calibration harness quantifies the divergence, enforces thresholds,
and controls the shadow-mode CERTIFIED/UNTRUSTED lifecycle.

---

## 1. METRICS CATALOG

### 1.1 Fill-Price Metrics

M-01 — Fill-Price Delta (absolute)

- Formula: `Δ_price = sim_fill_price - real_fill_price`
- Unit: quote currency (USDT / EUR)
- Distribution: maintain full empirical distribution (not just mean)
- Why: mean can hide systematic bias; distribution reveals skew, fat tails

M-02 — Fill-Price Divergence (bps)

- Formula: `bps = (sim_fill_price - real_fill_price) / real_fill_price × 10_000`
- Unit: basis points
- Why: exchange-agnostic, asset-agnostic normalization. Industry standard.
- Sign convention: positive = sim bought at HIGHER price than live (pessimistic for buys); negative = sim bought cheaper (optimistic — dangerous)
- Swing-strategy context: BTC/ETH market orders on Binance show 0.1–0.5%
  (10–50 bps) slippage on normal sizes; a fill model that is off by >50% of
  observed live slippage introduces meaningful PnL bias over a swing holding
  period of days-to-weeks.

M-03 — Fill-Price Divergence Percentiles

- Track: p50 (median), p90, p95, p99, p99.9
- Why: p50 catches systematic bias; p95/p99 catch tail blow-ups that kill
  drawdown modeling. A calibrated model looks right at p50 but may wildly
  mis-model tail fills.

M-04 — Fill-Price Bias Direction

- Formula: `bias_ratio = count(bps > 0) / N` (fraction of trades where sim is more expensive than real for buys)
- Why: a well-calibrated model should be symmetric (bias_ratio ~0.5) for market orders.
  Consistent positive bias means sim assumes worse fills than real → backtest
  over-penalizes → live looks better than backtest (acceptable but deceptive).
  Consistent negative bias means sim assumes BETTER fills than real → backtest
  over-optimistic → real-money underperforms → HARD BLOCK trigger.

### 1.2 Fill-Timing Metrics

M-05 — Fill Latency Divergence

- Formula: `Δ_latency = sim_fill_timestamp - real_fill_timestamp` (in ms)
- Unit: milliseconds
- Context: swing strategy places market orders at bar-close signal. Real round-trip
  on Binance testnet ≈ 50–200 ms. sim assumes near-instant or configurable latency.
- Why: timing divergence matters for stop-loss tracking, not price — a 200ms
  delay is irrelevant for a multi-day swing hold but WOULD matter for intraday.

M-06 — Trade Count Divergence

- Formula: `count_delta = sim_trade_count - real_trade_count` over rolling window
- Unit: integer; report as percentage
- Why: Freqtrade issues #12662/#12675 show that signal → fill timing skew causes
  sim to fire more/fewer trades than live over same period. Count divergence >10%
  over 100 trades indicates a structural timing bug.

### 1.3 Partial Fill Metrics

M-07 — Partial Fill Rate Divergence

- Formula: KS test statistic between sim partial-fill-size distribution and real
- Unit: KS D-statistic (0 = identical; 1 = maximally different)
- Why: NautilusTrader issue #2194 shows the canonical partial-fill gap. Swing orders
  on Binance at normal retail sizes (<$5k notional) rarely partially fill for BTC/ETH,
  but modeling this correctly matters for drawdown edge cases.
- MVP1 note: for small retail notional, partial fills are rare enough that this metric
  can be P1 (not P0) if sample size is insufficient.

M-08 — Partial Fill Size Distribution Match

- Metric: Wasserstein-1 distance between sim and real partial-fill qty distributions
- Why: KS is sensitive to distributional shape but not magnitude; Wasserstein
  captures "earth-mover distance" — how much the distributions need to be shifted
  to match, in fill-quantity units. Useful complement to KS for this use case.

### 1.4 PnL-Level Metrics

M-09 — Cumulative PnL Correlation

- Formula: Pearson r between sim_cumulative_pnl and real_cumulative_pnl over N trades
- Minimum N: 30 trades (below this, correlation estimate is noise-dominated)
- Why: individual fill metrics can all look acceptable while PnL diverges due to
  compounding effects. PnL correlation is the final integration test.
- Threshold: r ≥ 0.95 for CERTIFIED; < 0.90 BLOCKS; 0.90–0.95 = WARN

M-10 — Sharpe Ratio Match

- Formula: `|sharpe_sim - sharpe_real| / max(|sharpe_real|, 0.01)`
- Unit: relative divergence (fraction)
- Window: rolling 30-trade window (recalculated daily for swing strategy)
- Why: SSRN 2745220 is the entire reason this spec exists. Sharpe divergence is
  THE metric that Quantopian failed to catch.
- Threshold: relative divergence ≤ 20% → OK; 20–40% → WARN; > 40% → BLOCK

M-11 — Sortino Ratio Match

- Same formula as M-10 but using Sortino
- Why: Swing strategy cares about downside — Sortino captures this better than
  Sharpe for directional strategies with asymmetric returns.

M-12 — Maximum Drawdown Divergence

- Formula: `|maxdd_sim - maxdd_real| / max(|maxdd_real|, 0.0001)`
- Unit: relative fraction
- Why: risk model depends on drawdown accuracy. sim that consistently under-models
  drawdown creates false confidence in position sizing.

### 1.5 Lookahead / Timing Integrity Metrics

M-13 — Execution Timing Integrity

- Test: for every sim fill at time T, assert that all data used to generate the
  signal at T-ε was available before T. No fill can use data from T or later.
- Implementation: event log audit comparing signal_generation_timestamp to
  data_observation_timestamp to fill_timestamp. Must have:
  `data_observation_ts < signal_generation_ts < fill_ts`
- Why: catches Zipline #2011 style bugs where fill price equals the bar that
  generated the signal.

M-14 — Clock Jitter (sim vs real)

- Formula: σ of `sim_fill_ts - real_fill_ts` distribution (in ms)
- Acceptable: σ < 500ms for swing strategy (bar-close signals, daily-to-weekly holds)
- Why: hybrid event-driven + bar-close model has a sync boundary. Jitter in the
  consolidator → bar-close event → exchange dispatch pipeline should stay bounded.

### 1.6 Fee / Cost Metrics

M-15 — Fee Model Accuracy

- Formula: `(sim_fee - real_fee) / real_fee` per trade
- Why: Binance spot taker fee = 0.1% (BNB discount: 0.075%). If sim uses wrong
  fee tier, cumulative PnL error compounds.
- Threshold: |relative fee delta| median ≤ 5% → OK; > 10% → WARN

---

## 2. ACCEPTANCE THRESHOLDS TABLE

Legend for Action column:

- OK: within tolerance, no action
- WARN: log warning, notify via Telegram, increment warn counter; no block
- BLOCK: set shadow_mode.status = UNTRUSTED, notify, PREVENT any live deployment
  certification until resolved

| ID   | Metric                                 | Window      | OK        | WARN                   | BLOCK            | Block action                                  |
|------|----------------------------------------|-------------|-----------|------------------------|------------------|-----------------------------------------------|
| M-02 | Fill-price divergence p50 (bps)        | 1000 trades | ≤ 15 bps  | 15–30 bps              | > 30 bps         | Mark UNTRUSTED; alert; stop shadow cert       |
| M-02 | Fill-price divergence p95 (bps)        | 1000 trades | ≤ 50 bps  | 50–100 bps             | > 100 bps        | Mark UNTRUSTED; alert; stop shadow cert       |
| M-02 | Fill-price divergence p99 (bps)        | 1000 trades | ≤ 100 bps | 100–200 bps            | > 200 bps        | Mark UNTRUSTED; alert                         |
| M-04 | Bias direction (fill quality)          | 1000 trades | 0.40–0.60 | 0.35–0.40 or 0.60–0.65 | < 0.35 or > 0.65 | Mark UNTRUSTED; sim systematically optimistic |
| M-06 | Trade count divergence                 | 100 trades  | ≤ 5%      | 5–10%                  | > 10%            | Flag timing bug; halt shadow cert             |
| M-09 | Cumulative PnL correlation (Pearson r) | 30 trades   | r ≥ 0.95  | 0.90–0.95              | r < 0.90         | Mark UNTRUSTED; alert; investigate fill model |
| M-10 | Sharpe ratio relative divergence       | 30 trades   | ≤ 20%     | 20–40%                 | > 40%            | Mark UNTRUSTED; halt shadow cert              |
| M-12 | MaxDrawdown relative divergence        | 30 trades   | ≤ 25%     | 25–50%                 | > 50%            | WARN only; flag risk model review             |
| M-13 | Lookahead integrity                    | Continuous  | PASS      | —                      | ANY FAIL         | IMMEDIATE BLOCK; P0 bug; halt all modes       |
| M-14 | Clock jitter σ                         | 1000 trades | ≤ 500ms   | 500ms–2s               | > 2s             | Alert; investigate consolidator pipeline      |
| M-15 | Fee model accuracy (median)            | 100 trades  | ≤ 5%      | 5–10%                  | > 10%            | Correct fee tier config; re-run after fix     |

**Threshold rationale**:

- 30 bps p50 block: Alpaca IEX vs SIP creates 5–50 bps divergence (SA-0). For a
  calibrated same-exchange comparison (sim vs Binance testnet), 15 bps median is
  already generous — it allows for sim's simplified order-book model vs real book.
  At >30 bps median, the sim is off by >half a round-trip Binance taker fee, which
  will materially distort edge estimation for swing strategies with 0.5–3% avg
  moves per trade.
- 100 bps p95 block: p95 captures tail fills (large candle wicks, slippage events).
  Beyond 100 bps at p95, the tail model is dangerously wrong for drawdown purposes.
- Bias direction 0.65 block: a sim that fills 65% of trades MORE cheaply than real
  is systematically optimistic. That's the Quantopian failure mode.
- PnL correlation r < 0.90 block: correlations below 0.90 over 30 trades indicate
  the sim PnL trajectory is structurally decoupled from reality. This is UNTRUSTED.
- Lookahead ANY FAIL: zero tolerance. One lookahead catch = immediate halt across
  all modes (backtest + shadow + paper). The entire purpose of calibration is to
  detect these before live money.

---

## 3. STATISTICAL RIGOR

### 3.1 Minimum Sample Size for Statistical Validity

For fill-price divergence metrics (M-01/M-02):

- Detecting a 10 bps shift in the median at α=0.05, power=0.80 requires ≈ 380 trades
- Detecting a 5 bps shift at the same power: ≈ 1,500 trades
- **MVP1 minimum**: 200 trades before any metric is "valid" (below this, all thresholds
  auto-WARN, not BLOCK, since the estimate is noise-dominated)
- **Operational target**: 1,000 trades rolling window for p50/p95 metrics

Derivation (Wilcoxon rank-sum power formula for two-sample location shift):

```text
n = 2 × (z_α/2 + z_β)² × σ² / δ²
With σ ≈ 30 bps (typical fill-price std on BTC/USDT), δ = 15 bps:
n ≈ 2 × (1.96 + 0.84)² × 30² / 15² ≈ 315 → round up to 380 with continuity correction
```

For PnL correlation (M-09) and Sharpe match (M-10):

- Minimum 30 trades per Pearson correlation stability
- Below 30 trades: metrics computed but labeled "(insufficient sample — informational only)"
- Above 100 trades: metrics are operationally reliable

### 3.2 Statistical Test Selection per Metric

| Metric class            | Test                   | Why                                                                   |
|-------------------------|------------------------|-----------------------------------------------------------------------|
| Fill-price distribution | Two-sample KS test     | Non-parametric; sensitive to distributional shape. α=0.05.            |
| Fill-price distribution | Wasserstein-1 distance | Complements KS; captures magnitude of shift not just shape change.    |
| Fill-price bias         | Binomial test          | Count-based (M-04 bias_ratio vs H₀: ratio=0.50).                      |
| Trade count             | Poisson test           | Count data; H₀: λ_sim = λ_real over same time window.                 |
| PnL correlation         | Pearson r + CI         | Bootstrap 95% CI over 10,000 resamples to avoid normality assumption. |
| Sharpe/Sortino match    | Bootstrap permutation  | Sharpe is not normally distributed; permutation test is exact.        |
| Latency/jitter          | One-sample t-test on Δ | H₀: mean(sim_ts - real_ts) = 0 after latency offset subtracted.       |

**KS test for fill-price distribution calibration**:

```python
from scipy.stats import ks_2samp

stat, p_value = ks_2samp(sim_fill_prices_bps, real_fill_prices_bps)
# Alert if stat > 0.15 (i.e., CDFs differ by >15% at their maximum gap)
# AND p_value < 0.05
```

**Wasserstein as secondary**:

```python
from scipy.stats import wasserstein_distance

w1 = wasserstein_distance(sim_fill_prices_bps, real_fill_prices_bps)
# Alert if w1 > 20 bps (CDFs need >20 bps "mass shift" to match)
```

Why both: KS is sensitive at distributional shape but ignores magnitude of
separation. Wasserstein (earth-mover distance) captures HOW FAR apart the
distributions are in bps units — directly interpretable to traders.

### 3.3 Regime Awareness

Calibration MUST run separately per regime bucket:

1. **Low-volatility regime**: 24h rolling ATR < 1% of price
2. **Normal regime**: 1–3% ATR
3. **High-volatility regime**: > 3% ATR (also: around macro events, exchange anomalies)

Why: slippage, fill rates, and spread all change dramatically across volatility
regimes. A model calibrated only on normal-regime data will fail in stressed markets.
Hummingbot's recurring paper-mode failures (SA-0 issues #7370 etc.) correlate with
high-volatility periods when the simplified fill model is furthest from reality.

Implementation:

- Tag each fill pair (sim, real) with its regime label at the time of execution
- Report threshold compliance SEPARATELY per regime
- Overall CERTIFIED requires PASS in all three regimes with sufficient sample per regime

Minimum N per regime: 100 fills (below this: informational only, no blocking)

### 3.4 Calibration Re-run Cadence

| Cadence | Trigger | Scope |
| --------- | --------- | ------- |
| **Continuous** (every fill) | Every sim+real fill pair is ingested | M-13 (lookahead) — zero latency |
| **Rolling** (per 100 fills) | 100 new fill pairs accumulated | M-06 (trade count), M-15 (fee) |
| **Rolling** (per 1000 fills) | 1000 new fill pairs accumulated | M-02 (fill-price distribution), M-04 (bias) |
| **Daily** (at midnight UTC) | Clock-based | M-10/M-11 (Sharpe/Sortino match), M-12 (drawdown), M-14 (jitter σ) |
| **Minimum 30 fills** | After minimum N reached | M-09 (PnL correlation) |
| **On-demand** | Human triggers `hermes calibrate --full` | Full suite |

Why not fully continuous: KS and Wasserstein tests have O(n log n) cost.
Running them per-fill is unnecessary; per-1000-fills is both timely and computationally cheap.

---

## 4. TIME-MODEL CALIBRATION

### 4.1 Hybrid Event-Driven / Bar-Close Alignment

Hermes uses: bar-close signals → event-driven fills.

The alignment problem:

1. Bar closes at T
2. DonchianEngine sees the close, generates signal at T + ε_processing
3. ExchangePort receives order at T + ε_processing + ε_dispatch
4. Exchange fills at T + ε_processing + ε_dispatch + ε_latency (real round-trip ~50–200ms)
5. Sim FillModelPort fills at T + ε_processing + ε_sim_latency (configured latency model)

**Calibration check**: compare (ε_dispatch + ε_latency) measured in live vs
ε_sim_latency in sim. If sim assumes 10ms and real is consistently 150ms,
M-14 clock jitter will catch it.

**Consolidator boundary calibration**:
LEAN's consolidators use a "fire-on-period-close" model where the bar-close event
fires strictly after the bar closes, never before. hermes must enforce:

- `signal_timestamp > bar_close_timestamp` (never same)
- `fill_timestamp > signal_timestamp` (fills happen after signals)
Both enforced as assertions in M-13 (lookahead integrity check).

### 4.2 Acceptable Jitter for Swing Strategy

For a swing strategy with holds of minutes-to-days:

- 500ms clock jitter has ZERO trading impact — it cannot affect a bar-close signal
- 2s jitter is still acceptable (within same minute bar)
- > 2s jitter on a 1-minute bar strategy means fills are attributed to the WRONG bar
  in edge cases → timing bug, block

For intraday (not MVP1 but future): 50ms jitter would be the threshold.

---

## 5. FAILURE MODES CALIBRATION PREVENTS

### 5.1 Lookahead Bugs (Zipline #2011 Class)

**Symptom**: sim achieves systematically better fills than live because signal
uses data that was not available at execution time.

**Calibration catch**: M-13 (lookahead integrity) + M-04 (bias direction).
A sim that consistently fills at better-than-real prices (bias_ratio < 0.35 for
buys) is a signal of lookahead. M-13 provides the definitive assertion.

**Detection timeline**: M-04 catches it in distribution after 100 trades.
M-13 catches the FIRST occurrence immediately (continuous check).

### 5.2 Slippage Under-modeling (LEAN #4897 Class)

**Symptom**: sim assumes zero or small slippage; real fills at worse prices.
Backtest shows higher PnL than live.

**Calibration catch**: M-02 p50 shows negative bps (sim fills CHEAPER than real).
Bias direction (M-04) shows bias_ratio > 0.65 (sim fills more cheaply on majority
of trades).

**Block trigger**: M-02 p50 < −30 bps OR bias_ratio > 0.65 → BLOCK.

### 5.3 Fill Timing Skew (Freqtrade #12662/#12675 Class)

**Symptom**: sim fires more or fewer trades than live over same period because
signals arrive at slightly different bar boundaries.

**Calibration catch**: M-06 (trade count divergence). A 10% count gap over 100
trades flags the structural timing difference.

### 5.4 Partial Fill Mis-modeling

**Symptom**: sim always fills full size; live frequently partially fills.
Causes simulated position sizes to be systematically larger than real.

**Calibration catch**: M-07 (KS test on partial fill size distribution).

### 5.5 Fee-Tier Mis-configuration

**Symptom**: sim uses 0.1% taker fee; actual account earns BNB discount (0.075%).
Cumulative 25 bps per trade error.

**Calibration catch**: M-15 (fee model accuracy). Catches misconfiguration fast.

---

## 6. BACKTEST CALIBRATION

Since backtest = paper + historical data replay through same FillModelPort,
calibration applies identically.

**What "backtest is trustworthy" looks like**:

- Shadow mode is CERTIFIED (all M-02/M-04/M-09/M-10 thresholds are OK/WARN)
- The same FillModelPort and fee model used in shadow mode is used for backtest
- The backtest uses HISTORICAL data that does NOT overlap with LLM training data
  (hermes ADR-005 ML unlock rule: use post-January-2026 data for live-state testing)
- Backtest result should be compared against shadow-mode result on the same historical
  period; divergence flags a code-path bug in the historical replay

**Shadow-mode as backtest ground truth**:
The most powerful calibration pattern: run shadow mode live for 30–60 days, then
replay the SAME market data through the backtest engine and compare trade-by-trade.
Any divergence between live-shadow and historical-replay over the same data = bug in
the historical replay path.

---

## 7. REFERENCE IMPLEMENTATIONS

### 7.1 NautilusTrader FillModel

**What it does**: probability-based fill simulation with two knobs.

```python
# From NautilusTrader documentation (2026)
FillModel(
    prob_fill_on_limit=1.0,  # P(limit fills | price touches level)
    prob_slippage=0.0,  # P(one-tick adverse slippage on L1 book)
    random_seed=42,
)
```

**Calibration guidance (from docs)**:

1. Analyze historical fills against touched price levels → set `prob_fill_on_limit`
2. Quantify average slippage in ticks → derive `prob_slippage`
3. L2/L3 books: `prob_slippage` ignored; actual book data governs

**Gap acknowledged in issue #2194** (Jan 2025): FillModel lacks queue position,
competition for liquidity, varying session liquidity, price impact for large orders,
and multi-level partial fills. The enhancement request (`process_market_fills`,
`process_limit_fills` hooks) is not yet shipped as of 2026-08.

**hermes lesson**: NautilusTrader's calibration is parameter-tuning, not
distribution-comparison. hermes' calibration harness is more rigorous — it compares
EMPIRICAL DISTRIBUTIONS of sim vs real fills, not just point estimates of parameters.

### 7.2 QuantConnect LEAN Reconciliation

**What it does**: runs an out-of-sample backtest in parallel to live trading and
overlays equity curves visually.

**Gap**: no automated thresholds. No blocking. Visual comparison only (per official
reconciliation docs, 2026). The human must notice and decide.

**hermes lesson**: LEAN's reconciliation is a visual diffing tool, not a continuous
certification gate. hermes' harness is automated, quantitative, and binding
(BLOCK = deploy blocked).

### 7.3 QuantConnect Slippage Models

Pluggable models available (verified from official docs):

- `ConstantSlippageModel(slippage_pct)` — fixed bps
- `VolumeShareSlippageModel` — market impact proportional to ADV participation
- `FixedSlippageModel` — fixed tick offset

**hermes lesson**: LEAN externalizes slippage as a model parameter.
hermes' FillModelPort should similarly be pluggable (strategy pattern, already
planned per ADR-003). The calibration harness picks the model that minimizes
divergence across M-02 and M-09.

### 7.4 Code reference — calibration-adjacent patterns

NautilusTrader calibration loop (approximate, from docs):

```python
# Step 1: collect (sim_fill, real_fill) pairs for the same signal
fill_pairs = [(sim_price, real_price) for sim, real in matched_fills]

# Step 2: compute bps divergence per pair
bps_series = [(s - r) / r * 10_000 for s, r in fill_pairs]

# Step 3: run KS test and update FillModel parameters
from scipy.stats import ks_2samp

stat, p = ks_2samp(bps_series_sim, bps_series_real)
if p < 0.05 and stat > 0.15:
    # distributions differ significantly; retune prob_slippage
    ...
```

hermes will implement this as a domain service: `CalibrationService.run(fill_pairs)`.

---

## 8. MVP1 CALIBRATION SCOPE (P0 Metrics)

Minimal but complete protocol for Swing strategy on Binance testnet, MVP1.

### P0 — Must have for shadow-mode certification

| ID   | Metric                          | Why P0                                                    |
|------|---------------------------------|-----------------------------------------------------------|
| M-02 | Fill-price divergence p50 (bps) | Core accuracy measure; catches slippage under-modeling    |
| M-04 | Bias direction                  | Catches systematic optimism (lookahead proxy)             |
| M-09 | PnL correlation                 | Integration test — catches compounding error              |
| M-10 | Sharpe ratio match              | SSRN 2745220 lesson; primary certification signal         |
| M-13 | Lookahead integrity             | Zero-tolerance; must run continuous                       |
| M-15 | Fee model accuracy              | Configuration correctness; cheap to verify                |

### P1 — Implement within 30 days of first shadow-mode run

| ID   | Metric                          | Why P1                                                   |
|------|---------------------------------|----------------------------------------------------------|
| M-02 | p95/p99 percentiles             | Tail risk; need more trades to estimate reliably         |
| M-03 | Percentile distributions        | Visualization for debugging                              |
| M-06 | Trade count divergence          | Timing bug detection; needs 100 trade min                |
| M-11 | Sortino ratio match             | Complements Sharpe; P1 since P0 Sharpe is more urgent    |
| M-14 | Clock jitter σ                  | Consolidator validation; P1 since timing is secondary    |

### P2 — Future / after HFT mode or high-frequency data

| ID   | Metric                          | Why P2                                                   |
|------|---------------------------------|----------------------------------------------------------|
| M-07 | Partial fill KS test            | Rare for retail swing size; needs sample accumulation    |
| M-08 | Partial fill Wasserstein        | Complement to M-07                                       |
| M-12 | MaxDrawdown divergence          | Informational; no block action; P2                       |
| M-05 | Fill latency divergence         | Timing analysis; swing strategy is latency-insensitive   |

---

## 9. REPORTING FORMAT

### 9.1 JSON Schema (calibration report)

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-08-24T22:00:00Z",
  "shadow_mode_status": "CERTIFIED | UNTRUSTED | INSUFFICIENT_DATA",
  "strategy": "swing_donchian",
  "exchange": "binance_testnet",
  "window": {
    "start": "2026-07-25T00:00:00Z",
    "end": "2026-08-24T00:00:00Z",
    "fill_pairs_total": 847,
    "fill_pairs_by_regime": {
      "low_volatility": 312,
      "normal": 495,
      "high_volatility": 40
    }
  },
  "metrics": {
    "M02_fill_price_bps": {
      "p50": 8.3,
      "p90": 24.1,
      "p95": 38.7,
      "p99": 82.4,
      "status": "OK",
      "ks_stat": 0.09,
      "ks_pvalue": 0.23,
      "wasserstein_bps": 11.2
    },
    "M04_bias_direction": {
      "ratio": 0.52,
      "status": "OK"
    },
    "M09_pnl_correlation": {
      "pearson_r": 0.971,
      "bootstrap_ci_95": [0.958, 0.983],
      "status": "OK"
    },
    "M10_sharpe_divergence": {
      "sharpe_sim": 1.34,
      "sharpe_real": 1.27,
      "relative_divergence": 0.055,
      "status": "OK"
    },
    "M13_lookahead_integrity": {
      "violations": 0,
      "status": "OK"
    },
    "M15_fee_accuracy": {
      "median_relative_delta": 0.018,
      "status": "OK"
    }
  },
  "blocks": [],
  "warnings": [],
  "certification": {
    "status": "CERTIFIED",
    "granted_at": "2026-08-24T22:00:00Z",
    "valid_until": "2026-09-24T22:00:00Z",
    "min_trades_required_for_renewal": 200
  }
}
```

### 9.2 Alerting

- **BLOCK event**: Telegram alert (Notifier domain event) + calibration report JSON
  emitted to `logs/calibration/YYYY-MM-DD.json`. shadow_mode.status written to SQLite.
- **WARN event**: Telegram alert (lower priority). No block. Counter incremented.
- **Daily digest**: summary pushed to Telegram at midnight UTC with all metric statuses.
- **Grafana** (if added in Phase 3): report JSON is structured to be importable into
  a Grafana dashboard. Not P0 — ADR-003 rejected Prometheus/Grafana for MVP1.

---

## 10. SHADOW-MODE CERTIFICATION LIFECYCLE

```text
States: PENDING_DATA → CERTIFIED → UNTRUSTED → CERTIFIED (after fix)
                    ↘ INSUFFICIENT_DATA (< 200 fill pairs)
```

**PENDING_DATA**: Initial state. < 200 fill pairs collected. No thresholds enforced.
All metrics collected as informational. No deployment blocked.

**CERTIFIED**: ≥ 200 fill pairs. ALL P0 metrics are OK or WARN (no BLOCK condition
on any P0 metric in the current rolling window). Shadow-mode is production-grade.
Certification is valid for 30 days (renewable automatically on continued data accumulation).

**UNTRUSTED**: Any P0 metric hits BLOCK threshold. shadow_mode.status = UNTRUSTED.

- Telegram alert with specific metric(s) that failed
- Deploy to live trading is BLOCKED at the CI level (check script reads shadow_mode.status from SQLite before any live deployment step)
- M-13 violation immediately → UNTRUSTED regardless of other metrics

**Restoration**: UNTRUSTED → CERTIFIED requires:

1. Fix the root cause (fill model parameter, fee config, timing bug, etc.)
2. Accumulate 200 fresh fill pairs (post-fix)
3. All P0 metrics back to OK/WARN over those 200 pairs
4. Human approval (`hermes shadow-mode certify --confirm`) — cannot be auto-restored

**Why human approval for restoration**: this prevents an automatic loop where a
transient market anomaly causes UNTRUSTED → auto-CERTIFIED without investigation.
The human reviews the calibration report and signs off.

---

## 11. TEST PYRAMID FOR THE CALIBRATION HARNESS ITSELF

The calibration harness is safety-critical code. It must be tested at multiple levels.

### 11.1 Unit Tests (pure function tests)

```python
# Test: KS test wrapper returns correct status
def test_ks_test_catches_biased_distribution():
    # Sim fills 30 bps higher than real (optimistic sim)
    real_bps = np.random.normal(5, 15, 500)   # ~5 bps mean
    sim_bps  = np.random.normal(-25, 15, 500)  # ~-25 bps mean (sim fills cheaper)
    result = compute_ks_metric(sim_bps, real_bps)
    assert result.status == "BLOCK"
    assert result.bias_ratio > 0.65

# Test: lookahead detector catches signal-before-data bug
def test_lookahead_detector_fires_on_violation():
    fill = FillRecord(
        signal_ts=T,
        data_observation_ts=T + 10ms,  # data AFTER signal — lookahead bug
        fill_ts=T + 100ms,
    )
    assert detect_lookahead(fill) == LookaheadViolation

# Test: certification lifecycle state machine
def test_untrusted_requires_human_confirmation_to_restore():
    harness = CalibrationHarness(status=UNTRUSTED)
    harness.ingest_fill_pairs(good_fills_200)  # all OK metrics
    assert harness.status == UNTRUSTED  # still untrusted — needs human
    harness.human_approve()
    assert harness.status == CERTIFIED

# Test: insufficient sample blocks all BLOCK actions
def test_insufficient_sample_degrades_to_warn():
    harness = CalibrationHarness()
    # Feed 50 fills where p50 = 40 bps (would be BLOCK with N=1000)
    harness.ingest_fill_pairs(biased_fills_50)
    assert harness.metrics["M02"].status == "WARN"  # not BLOCK, insufficient N
    assert harness.shadow_mode_status == "INSUFFICIENT_DATA"
```

### 11.2 Property-Based Tests (Hypothesis)

```python
from hypothesis import given, strategies as st


@given(st.lists(st.floats(-500, 500), min_size=200, max_size=2000))
def test_bps_percentiles_are_monotone(bps_series):
    result = compute_percentiles(bps_series)
    assert result.p50 <= result.p90 <= result.p95 <= result.p99


@given(st.floats(0.0, 1.0))
def test_bias_ratio_thresholds_are_symmetric(ratio):
    status = classify_bias_ratio(ratio)
    # OK region is symmetric around 0.5
    mirrored = classify_bias_ratio(1.0 - ratio)
    assert status == mirrored
```

### 11.3 Integration Tests (fake FillModelPort + fake ExchangePort)

```python
def test_end_to_end_calibration_catches_slippage_undermodel():
    """Sim with zero slippage vs real with 20 bps slippage → BLOCK after 1000 fills."""
    fake_sim_port = ZeroSlippageFillModel()
    fake_real_port = FixedSlippageFillModel(slippage_bps=20)
    harness = CalibrationHarness(sim=fake_sim_port, real=fake_real_port)

    # Run 1000 fill pairs
    for _ in range(1000):
        harness.record_fill_pair(generate_random_order())

    assert harness.metrics["M02_p50"].value < -15  # sim fills ~20 bps cheaper
    assert harness.shadow_mode_status == "UNTRUSTED"


def test_calibration_passes_with_realistic_model():
    """Sim with matching slippage model → CERTIFIED after 200 fills."""
    fake_sim_port = FixedSlippageFillModel(slippage_bps=20)
    fake_real_port = FixedSlippageFillModel(slippage_bps=20)
    harness = CalibrationHarness(sim=fake_sim_port, real=fake_real_port)

    for _ in range(200):
        harness.record_fill_pair(generate_random_order())

    assert harness.shadow_mode_status == "CERTIFIED"
```

### 11.4 Mutation Tests (Hypothesis + mutmut)

Apply mutmut to `calibration_service.py`. Required mutant-kill rate: ≥ 85%.
Critical mutations to specifically kill:

- Flipping `>` to `>=` in BLOCK thresholds
- Inverting the lookahead `<` comparison in M-13
- Changing `BLOCK` to `WARN` in status assignment

### 11.5 Regression Tests (golden files)

Store known fill-pair datasets with pre-computed expected outputs.
On any change to calibration logic, re-run against golden files.
Golden file format: `tests/calibration/fixtures/scenario_{name}.json` with
`input_fill_pairs` + `expected_report`.

---

## 12. IMPLEMENTATION NOTES

### 12.1 Data Collection Architecture

Fill-pair ingestion is a domain event in hermes:

```text
SimFillExecuted(sim_fill: Fill)  ←→  RealFillReceived(real_fill: Fill)
→ Matcher: match by (asset, direction, approximate_time_window ≤ 5s)
→ CalibrationService.record_pair(sim_fill, real_fill)
→ CalibrationRepository.append(fill_pair)  # SQLite, append-only
```

Matching policy: sim fill at T matches real fill if |real_ts - sim_ts| ≤ 5s for the
same asset+direction. If no real fill within 5s, the sim fill is unmatched
(logged as M-06 count divergence candidate).

### 12.2 FillModelPort Interface Extension

```python
class FillModelPort(Protocol):
    """Simulates fills without touching real exchange."""

    def simulate_fill(
        self,
        order: Order,
        orderbook_snapshot: OrderbookSnapshot,
        ts: datetime,
    ) -> SimulatedFill: ...

    # Calibration support
    def calibration_metadata(self) -> dict[str, Any]:
        """Return model parameters for drift detection."""
        ...
```

### 12.3 CalibrationService Domain Service

```python
class CalibrationService:
    """Pure domain service — no I/O. Receives fill pairs, emits calibration reports."""

    def record_pair(self, sim: SimulatedFill, real: RealFill) -> None: ...
    def run_rolling(self, window: int = 1000) -> CalibrationReport: ...
    def check_lookahead(self, event_log: list[FillEvent]) -> list[LookaheadViolation]: ...
    def certify(self, report: CalibrationReport, human_approved: bool) -> ShadowModeStatus: ...
```

This is a pure domain service — no database reads, no HTTP. Data arrives as function
arguments. The application layer injects data from SQLite and calls this service.

---

## 13. OPEN QUESTIONS FOR PHASE 1 DESIGN

1. **Matching tolerance**: 5s window for sim-to-real fill matching seems right for
   swing strategy market orders, but needs empirical validation. What if a Binance
   testnet order takes 8s to fill during testnet congestion?

2. **Binance testnet price divergence baseline**: the testnet has a SEPARATE orderbook
   from mainnet (SA-0). Some bps divergence between testnet prices and mainnet prices
   is STRUCTURAL, not a calibration failure. We need to baseline the testnet-mainnet
   divergence separately and subtract it from M-02 measurements when comparing
   sim (which uses testnet data) vs real testnet fills.

3. **Regime labeling**: ATR-based regime bucket is proposed (low/normal/high vol).
   Should this also include time-of-day bucketing (Asian session vs US session)?
   Crypto liquidity is lower in Asian hours; fill model may diverge more then.

4. **Certification validity period**: 30-day validity proposed. What if 30 days pass
   without 200 fresh fills (low-activity periods)? Clock-based expiry vs
   trade-count-based expiry?

5. **Kraken adapter**: once Kraken is added, calibration must run per-exchange
   separately. The FillModelPort and CalibrationService are already parameterized
   by exchange, but the threshold table may need per-exchange tuning.

---

## 14. CONFIDENCE LEVELS

| Section | Confidence | Notes |
| --------- | ------------ | ------- |
| Metric definitions (M-01 to M-15) | HIGH | Standard industry metrics; internally consistent |
| Threshold values (p50=30bps, p95=100bps) | MEDIUM-HIGH | Derived from Alpaca IEX/SIP data (SA-0), Binance slippage benchmarks (0.1–0.5%), and LEAN/NautilusTrader practice; no single authoritative source gives exact numbers for this configuration |
| Statistical test selection (KS + Wasserstein) | HIGH | Well-established; KS is the standard in LOB simulation papers (arxiv 2409.06514 confirms); Wasserstein is complementary and interpretable |
| Sample size (200 minimum, 1000 operational) | MEDIUM-HIGH | Power analysis derivation shown; assumes σ≈30 bps which needs empirical confirmation |
| Regime conditioning (3 buckets) | HIGH | Well-established practice; consistent with regime-switching literature |
| Certification lifecycle (human approval for restoration) | HIGH | Conservative choice; justified by Quantopian lesson |
| Test pyramid coverage | HIGH | Follows hermes testing philosophy (ADR-007 pending) |
| Reference implementations (LEAN, NautilusTrader) | HIGH | Verified from official docs 2026 |

---

## KEY SOURCES

- SSRN 2745220 (Wiecki et al.): Sharpe R² < 0.025 — core motivation
- SA-0 (engram `research/paper-trading-sa0-landscape`): Alpaca IEX/SIP 5-50 bps, failure inventory
- NautilusTrader FillModel docs (2026): `prob_fill_on_limit`, `prob_slippage` parameters
- NautilusTrader issue #2194: fill model inadequacy for realistic simulation
- QuantConnect reconciliation docs (2026): visual-only, no automated thresholds
- arxiv 2603.20319 (Yin et al.): implementation risk — transaction cost is THE divergence source
- arxiv 2409.06514: KS test for LOB simulation validation — confirms KS as standard tool
- arxiv 2512.12924 (walk-forward framework): 5 bps slippage in published swing strategy research
- QuestDB power analysis: n = 2(z_α/2 + z_β)² / δ² formula
- Binance slippage benchmarks: 0.1–0.5% (10–50 bps) for major pairs, normal sizes
- EvidentlyAI drift detection: KS vs Wasserstein comparison for data drift
