---
document: Regime Detection Notes — Swing MVP1
version: DRAFT v1
status: DRAFT — research notes, not a specification
created: 2026-08-25
relates_to: pre-registration-v1.md §4 (regime coverage), §9 (ulcer index per-regime)
---

# Regime Detection Notes — Swing MVP1

> §4 of the pre-registration names five regimes that must be covered by Train/Val/OOS: bull, bear, sideways, flash crash, and ETF launch. This document answers a question the pre-registration leaves open: **how do we detect those regimes programmatically?**
>
> Scope of this document:
>
> - MVP1: retrospective classification only (assign a regime label to every bar in the historical dataset)
> - MVP2 note: regime-aware strategy switching (deferred)
>
> This is a research/design note, NOT a frozen spec. It can change before the dataset is fingerprinted.

---

## 1. Why This Matters

Three concrete uses in this experiment:

1. **Regime coverage audit (§4)** — Before locking the Train/Val/OOS split, verify that each window contains representative samples of all five regimes. If OOS is 100% bull-quiet, the certification result tells us nothing about bear regimes.

2. **Per-regime performance breakdown (§9)** — Ulcer index, drawdown, and Sharpe decomposed by regime. A strategy with edge in bull-quiet but none in bear-vol is fragile. The pre-registration requires knowing *where* the edge lives.

3. **Post-mortem forensics (§10)** — When a kill trigger fires, §§4–6 of the post-mortem ask "what regime did the kill happen in?" We need a label to answer that.

---

## 2. Standard Regime Detection Methods

### 2.1 Markov Switching Models (Hamilton 1989)

**What it is:** A time-series model that assumes the data-generating process switches between K hidden states (regimes) governed by a Markov chain. Each state has its own mean and variance. The EM algorithm infers state probabilities.

**Strengths:**

- Principled probabilistic output (posterior probability of each regime at each timestep)
- Well-studied; statsmodels `MarkovAutoregression` implements it
- Naturally captures vol clustering (bear regimes = high-variance state)

**Weaknesses:**

- K must be chosen a priori; 2-state (bull/bear) is common but 4+ states are noisy
- Assumes stationarity within each regime; crypto regimes are non-stationary even within state
- Look-ahead bias risk: model is fit on full data, then applied retrospectively — need to refit on expanding window to avoid leaking future info into Train

**Reference:** Hamilton, J.D. (1989). *A New Approach to the Economic Analysis of Nonstationary Time Series and the Business Cycle.* Econometrica 57(2): 357–384.

**Verdict for MVP1:** Overkill for retrospective labeling. Useful if MVP2 needs probabilistic regime input. Skip for now.

---

### 2.2 Hidden Markov Models (HMM)

**What it is:** Generalization of Markov switching to multivariate observation sequences (e.g., return + volatility + volume simultaneously). `hmmlearn` in Python is the standard library.

**Strengths:**

- Can jointly model return, realized vol, and volume regime
- N-state flexible; 3-4 states reasonable for crypto

**Weaknesses:**

- Sensitive to initialization; results vary across runs without seeding
- Computationally heavier than heuristics; not justified for MVP1 retrospective classification
- Same look-ahead bias risk as MSM if fit on full history

**Verdict for MVP1:** Interesting for research, not justified for MVP1 operational use. Flag for MVP2 if regime-aware Balancer is implemented.

---

### 2.3 Bayesian Changepoint Detection

**What it is:** Detects structural breaks in a time series (moments where the data-generating process changes). `ruptures` library (Python) implements several algorithms (PELT, BinSeg, Window). Online variant: `bayesian_changepoint_detection`.

**Strengths:**

- Finds regime boundaries without assuming K
- Interpretable output: "the regime changed at date X"

**Weaknesses:**

- Detects *changes*, not *labels* — you still need to classify each segment
- Prone to over-segmentation on noisy crypto data
- Not well-suited for overlapping/fuzzy regime boundaries

**Verdict for MVP1:** Useful as a secondary check ("did a structural break occur near the ETF launch date?"), not as primary labeler.

---

### 2.4 Simple Heuristics (Rolling Correlation, Rolling Volatility, Trend)

**What it is:** Rule-based classification using one or more indicators:

- **Trend**: asset price relative to a moving average (e.g., close vs EMA-200)
- **Volatility**: realized vol (e.g., 20-day annualized std of log returns) above/below threshold
- **Correlation**: cross-asset correlation as a stress indicator
- **Drawdown from ATH**: depth of drawdown signals bear severity

**Strengths:**

- Transparent, reproducible, no model fitting
- No look-ahead bias if indicators use only past data
- Computationally trivial; can be applied bar-by-bar in the backtester

**Weaknesses:**

- Threshold choices are arbitrary (why 200D not 150D?)
- Binary classifications miss continuous transitions
- Can lag — a vol spike becomes "high vol" only after the realized measure catches up

**Verdict for MVP1:** PRIMARY METHOD. See §4 for the recommended approach.

---

## 3. Crypto-Specific Regime Detection — Published Frameworks

### 3.1 Coin Metrics Regime Classification

Coin Metrics publishes quarterly state-of-the-market reports that informally label BTC regimes based on on-chain metrics (SOPR, MVRV-Z, entity-adjusted transfer volume). They do not publish a reproducible algorithmic classifier.

**Availability:** Free-tier data via `coinmetrics` Python library. SOPR and MVRV-Z available. Useful for post-hoc annotation ("MVRV-Z < 0 = bear capitulation") but not suitable as a primary regime engine for MVP1 without additional R&D.

### 3.2 Kaiko Regime Data

Kaiko does not publish a regime classification product as of 2026. Their research papers occasionally reference regime-conditioned liquidity analysis, but no downloadable regime labels exist.

### 3.3 Glassnode Bull/Bear Framework

Glassnode's "Market Pulse" uses MVRV Momentum (a z-score of MVRV against its 365-day moving average) to classify BTC into four quadrants: Recovery, Bullish, Distribution, Bearish. This is the closest thing to a published, repeatable crypto regime framework.

**Reproducibility:** MVRV is available on Glassnode's free tier (limited). Full MVRV-Z requires paid access. For MVP1, this is an enrichment, not a dependency — we can classify regimes without on-chain data and cross-check afterward.

### 3.4 Academic Work

- **Liu, Tsyvinski, Wu (2022)** — "Common Risk Factors in Cryptocurrency" (Journal of Finance). Identifies momentum and size as crypto factors; implicitly uses drawdown-from-ATH to separate bear from bull. Downloadable factor data available.
- **Caporin et al. (2021)** — "The Dynamics of Cryptocurrency Returns" uses DCC-GARCH for correlation-based regime switching, but requires institutional data access.

**Verdict:** No off-the-shelf crypto regime API suitable for MVP1. Heuristic approach is appropriate and avoids data dependency.

---

## 4. Recommended Approach for MVP1 — Lightweight Heuristic

### 4.1 Rationale

MVP1 needs regime labels for three purposes (§1 above). All three are retrospective — we are labeling bars that already exist in the dataset. We do not need real-time regime detection. We do not need probabilistic outputs. We need reproducible, transparent labels that a human can audit.

The heuristic below uses only OHLCV data that is already required by the strategy (§5). No additional data sources, no model fitting, no look-ahead bias.

### 4.2 Primary Regime Taxonomy (4 base regimes + 1 binary overlay)

```text
Base regime = f(trend, realized_vol)

trend      = sign(close - EMA(200, 1D))     # 1D timeframe, same as strategy §5.1 regime filter
realized_vol = annualized std of log_return over 20 rolling trading days (1D bars)

vol_threshold = 60% annualized                # empirically separates "normal" from "turbulent"
                                              # BTC historical: quiet ≈ 30-50%, vol ≈ 60-120%

Regime labels:
  bull-quiet  : trend = UP   AND realized_vol < 60%
  bull-vol    : trend = UP   AND realized_vol ≥ 60%
  bear-quiet  : trend = DOWN AND realized_vol < 60%
  bear-vol    : trend = DOWN AND realized_vol ≥ 60%

Flash-crash overlay (binary, orthogonal to base regime):
  flash_crash = True iff any 1D bar has log_return < −10%
  Flags a single bar (or the surrounding 3-day window) regardless of base regime.
  Source: BTC/USDT Binance spot daily return.
```

**Why BTC EMA-200 as the trend signal for all three pairs?**
BTC dominates crypto market regime. ETH and SOL trend is co-determined by BTC macro trend ~80% of the time (correlation > 0.80 in bear markets per §5.5 rationale). Using BTC trend as the cross-asset regime signal is consistent with the strategy's own EMA filter (§5.1), which uses per-asset EMA-200 — the per-asset filter handles individual trend breaks; the regime classifier uses BTC as the market-wide anchor.

### 4.3 Implementation Notes

```python
# Pseudocode — not production code
def classify_regime(btc_daily: pd.DataFrame) -> pd.Series:
    ema200 = btc_daily['close'].ewm(span=200, adjust=False).mean()
    log_ret = np.log(btc_daily['close'] / btc_daily['close'].shift(1))
    realized_vol = log_ret.rolling(20).std() * np.sqrt(252)

    trend = np.where(btc_daily['close'] > ema200, 'up', 'down')
    vol_state = np.where(realized_vol >= 0.60, 'vol', 'quiet')

    base = [f"{t}-{v}" for t, v in zip(trend, vol_state)]
    # rename: up-quiet → bull-quiet, up-vol → bull-vol, etc.

    flash = (log_ret < -0.10).rolling(3).max().fillna(0).astype(bool)
    return pd.DataFrame({'regime': base, 'flash_crash': flash})
```

- **No look-ahead bias**: EMA and rolling vol use only past data (standard pandas `.ewm` and `.rolling` with `min_periods`).
- **Alignment**: apply to 1D bars; join to 4H strategy bars by date. Each 4H bar inherits the regime of its calendar date.
- **ETF-launch regime**: NOT a separate base regime in this heuristic. ETF launch (2024 Jan) is a point event; in the dataset it appears as a transition from bear-quiet → bull-quiet. Label it separately in the lab notebook as an annotated event, not a regime category. The post-mortem §10 can call it out specifically if a kill fires near an ETF event.

### 4.4 Vol Threshold Calibration

The 60% annualized threshold is a starting point. Before locking it:

1. Plot `realized_vol` over the full Train window (2020-01-01 → 2024-06-30).
2. Identify natural breaks in the distribution (bimodal distribution expected: quiet ≈ 30-50%, turbulent ≈ 70-120%).
3. Adjust threshold to the valley between modes if it differs significantly from 60%.
4. Document the chosen threshold in `lab-notebook.md` with a timestamp.

This calibration uses only Train data. Do NOT inspect Val or OOS for threshold selection.

### 4.5 Sideways Regime

The pre-registration §4 mentions "sideways" as a regime. In the 4-regime heuristic above, sideways is approximated by:

- `bear-quiet` when BTC has been consolidating below EMA-200 with low vol
- OR: add a separate sideways detection: `|log(close / EMA(200))| < 5%` (price hugging EMA tightly)

Recommendation: add sideways as a sub-label if post-mortem forensics need it, but do NOT complicate MVP1 regime coverage audit with a 5th category. Flag for MVP2 refinement.

---

## 5. Where Regime Detection Fits in the Experiment Lifecycle

| Phase | Use | Who runs it |
| ------- | ----- | ------------- |
| **Train/Val build** | Retrospective classification of all bars; coverage audit; per-regime Ulcer + Sharpe | Backtester — runs `classify_regime` and produces regime-breakdown table |
| **Backtest certification (§7)** | Per-regime metric decomposition included in BACKTEST_CERTIFIED report | Backtester |
| **Shadow phase** | Classify live bars in real time for forensics; log regime label per trade in trade log | Engine live feed |
| **Live phase** | Same as Shadow; regime label logged at entry and exit of each trade | Engine live feed |
| **Post-mortem (§10)** | Identify regime at kill trigger; decompose which regime caused the breach | Operator + post-mortem doc §1 Header |
| **OOS certification (§7)** | Per-regime Sharpe decomposition reported alongside aggregate DSR | OOS certification run |

---

## 6. Note for MVP2 — Regime as Strategy Input

In MVP1, regime classification is a **retrospective analytical tool**. The strategy (§5) does not use a regime label as input — it uses EMA-200 directly as its trend filter, which implicitly captures bull vs bear.

In MVP2, if a **Balancer** or regime-aware allocation layer is introduced, regime detection becomes a **real-time strategy input**. The implications:

1. **Look-ahead bias is now a live risk** — the regime classifier must be validated to use only past data at the point of signal generation. Rolling EMA and realized vol already satisfy this, but any model-based approach (HMM, MSM) must be validated with online inference, not batch fitting.

2. **Regime detection becomes a tunable parameter** — changing the vol threshold or trend window changes the strategy's behavior. A new pre-registration is required for any change to regime detection logic that affects entry/exit decisions.

3. **Flash-crash overlay may become a live circuit breaker** — if a 1D return < −10% is detected intraday (via the L2 collector), positions could be closed preemptively. This is a strategy change (§5) and requires a new pre-registration.

4. **Candidate models for MVP2 regime-awareness:**
   - HMM (hmmlearn, 3-state) trained on rolling 2-year window, retrained monthly
   - Glassnode MVRV-Z as external signal layer (on-chain regime confirmation)
   - BTC funding rate (Binance perpetuals) as bull-exhaustion signal — not available in MVP1 universe (spot-only)

**Bottom line for now:** Do not over-engineer MVP1 regime detection. The 4-regime heuristic in §4 is sufficient. Keep a `TODO: MVP2` comment in code where regime logic lives.

---

## 7. Regime Coverage Verification Checklist

Before signing pre-registration v1, verify that Train + Val + OOS together contain:

| Regime | Expected dates | Window it falls in | Bars expected |
| -------- | ---------------- | ------------------- | --------------- |
| COVID crash (flash crash + bear-vol) | 2020-02-20 → 2020-03-23 | Train | ~30 days |
| 2020 recovery (bull-quiet → bull-vol) | 2020-04 → 2020-12 | Train | ~200 days |
| 2021 bull peak (bull-vol) | 2020-Q4 → 2021-Q2 | Train | ~200 days |
| 2022 bear (bear-vol, multiple flash crashes) | 2022-01 → 2022-12 | Train | ~365 days |
| Terra/Luna flash crash | 2022-05-08 → 2022-05-12 | Train | 5 days |
| FTX collapse (flash crash + bear-vol) | 2022-11-06 → 2022-11-14 | Train | ~10 days |
| 2023 SVB/USDC depeg + recovery | 2023-03 → 2023-06 | Train | ~90 days |
| 2024 ETF launch (bear-quiet → bull-quiet) | 2024-01 → 2024-03 | Train | ~60 days |
| 2024 Aug 5 volatility (flash crash overlay) | 2024-08-05 | Val | 1 day |
| 2025-2026 OOS regime (TBD) | 2025-09-01 → 2026-12-31 | OOS | ~480 days |

Run `classify_regime` on the full dataset and produce a bar chart of regime distribution per window. If any base regime represents < 5% of a window, flag it as underrepresented in the lab notebook.

---

*End of regime detection notes — DRAFT.*
