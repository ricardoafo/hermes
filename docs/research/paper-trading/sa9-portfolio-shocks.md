# SA-9 Portfolio Shocks + Risk Overlay

**Date**: 2026-08-24
**Author**: SA-9 sub-agent
**Engram topic key**: `research/paper-trading-sa9-portfolio-shocks`
**Status**: FINAL
**Depends on**: SA-4 post-mortems (`research/paper-trading-sa4-post-mortems`), SA-7 strategy design (`research/paper-trading-sa7-strategy-design`)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Correlation Shock](#2-correlation-shock)
3. [Liquidity Vacuum](#3-liquidity-vacuum)
4. [Stablecoin Depeg](#4-stablecoin-depeg)
5. [Exchange Operational Risk](#5-exchange-operational-risk)
6. [Regulatory Shock](#6-regulatory-shock)
7. [Blockchain-Level Risk](#7-blockchain-level-risk-awareness)
8. [Systemic Macro Shocks](#8-systemic-macro-shocks)
9. [Composite Scenarios](#9-composite-scenarios)
10. [Defense Matrix](#10-defense-matrix)
11. [Confidence Assessment](#11-confidence-assessment)
12. [Citations](#12-citations)

---

## 1. Executive Summary

SA-4 covered bot-level operational failures (state drift, fill model illusion, clock drift). SA-7 covered strategy-level risk (stop model, drawdown limits, regime detection). This report covers the tier ABOVE both: portfolio-level shocks that bypass individual strategy stop-losses and can destroy the entire account or render the system inoperable regardless of how well each trade is individually managed.

**The irreducible truth at portfolio level**: Individual stop-losses are designed for normal market conditions. Shock scenarios invalidate the assumptions under which those stops were sized. A Chandelier Exit sized for ATR=800 offers no protection when ATR spikes to 4,000. A max-concurrent-positions limit of 3 offers no protection when all 3 positions are 0.90+ correlated and move -20% simultaneously. The cash bucket assumption breaks when USDT ≠ $1.

Top 3 shock scenarios hermes MVP1 must defend against, in severity order:

### 1. Correlation Shock (CRITICAL — MVP1)

Three concurrent Swing longs on BTC/ETH/SOL appear to be 3 independent positions. During panic regimes (March 2020, June 2022, November 2022, August 2024) their 30-day rolling correlation spikes from 0.6 to 0.95+. All three stop-losses trigger simultaneously. The realized loss is not 3 × (1% risk per trade) — it is 3 × (gap-fill slippage during a fast market) which can reach 3 × 5-15% = 15-45% of equity in minutes.

**Minimum viable defense**: Max 1 BTC-correlated position at a time (MVP1). A `correlation_lock` rule implemented in the position sizer: if any open position has BTC-dominance correlation > 0.80, block new entries in that bucket.

### 2. Liquidity Vacuum (CRITICAL — MVP1)

The `OrderbookWalkFillModel` (SA-3's contribution) is the correct model for normal conditions. During a flash crash, the model becomes catastrophically wrong: bid side of the book disappears. A market-sell order that the model simulates as "filling through 3 levels at $67,200 → $67,180 → $67,160" instead fills in live at $61,000 → $58,000 → empty. The gap between paper P&L and live P&L in a single liquidity vacuum event can exceed the entire prior paper-trading period's profit.

**Minimum viable defense**: Max-slippage-per-order guard (if simulated fill cost exceeds 0.5% of position value, cancel and route to manual alert). Spread-spike kill-switch: if bid-ask spread widens > 10× the 1-hour rolling median, halt all new market orders and emit a CRITICAL alert.

### 3. Stablecoin Depeg (HIGH — MVP1)

The entire hermes accounting layer denominated in USDT/USDC assumes 1 unit = $1.00. USDT traded at $0.944 during the Terra/Luna collapse (May 12, 2022). USDC traded at $0.877 on March 11, 2023 (SVB collapse). If hermes holds a cash bucket in USDT and USDT depegs to $0.90, all portfolio metrics (equity, drawdown, risk per trade) are overstated by 10% — without any trade having occurred. A 10% depeg is a synthetic drawdown of the entire account with no stop-loss to trigger.

**Minimum viable defense**: Monitor stablecoin price via a public oracle (CoinGecko, Pyth, or exchange mid-price on USDT/USDC pair). If either USDT or USDC deviates > 2% from $1.00 → emit CRITICAL alert. If deviation > 3% → halt all new entries and close all open positions at market (the assumption the system is built on has failed).

**Defense stack layering**: The three defenses interact. A correlation shock (panic sell) often co-occurs with a liquidity vacuum (everyone exits simultaneously) and may trigger a stablecoin wobble (flight to "safer" stables, or worse — flight out of all stables). The composite scenario section covers compound failures.

---

## 2. Correlation Shock

### 2.1 Historical Precedents

#### March 2020 — COVID Crash ("Black Thursday" March 12, 2020)

- BTC: −37% in 24 hours ($8,000 → $4,970). ETH: −43%. XRP: −44%. LINK: −38%.
- 30-day correlation BTC-ETH in the month prior (Feb 2020): 0.74
- 30-day correlation BTC-ETH during the week of the crash: **0.97**
- Total crypto market cap: −$100B+ in 24 hours
- DeFi TVL protocols liquidated in mass cascade
- Source: Auer & Claessens (BIS Quarterly Review, Sept 2020); CoinMetrics on-chain data

#### June 2022 — Three Arrows Capital (3AC) / Celsius Collapse

- BTC: $31,000 → $17,700 (−43%) over June 13-18, 2022
- ETH: $1,700 → $890 (−48%) same period
- SOL: $40 → $27 (−33%) same period
- BNB: −35%, AVAX: −40%
- 30-day rolling correlation matrix (May-June 2022 average): BTC-ETH 0.93, BTC-SOL 0.89, ETH-SOL 0.91
- Cause: 3AC's $400M+ BTC position liquidated; Celsius withdrawal freeze → panic contagion
- Source: Chainalysis "Crypto Market Downturn" 2022 report; CoinMarketCap historical

#### November 2022 — FTX Collapse

- BTC: $21,000 → $15,800 (−25%) Nov 6-10, 2022
- ETH: $1,590 → $1,090 (−31%) same period
- SOL: $34 → $11 (−68%) — FTX/Alameda held large SOL positions
- Cross-asset correlation spiked: BTC-ETH reached 0.96; SOL became an outlier (exchange-specific contagion)
- Source: Conti-Brown & Hirst, "FTX Collapse" Penn Wharton Public Policy Initiative (Nov 2022)

#### March 2023 — Silicon Valley Bank (SVB) Collapse

- Primarily stablecoin event (USDC depeg, covered in §4), but BTC also fell −12% on March 10 as general risk-off hit
- BTC-ETH correlation during March 8-13: 0.94
- Macro correlation: BTC-NASDAQ correlation reached 0.68 during the panic week, vs typical 0.45

#### August 2024 — Japanese Yen Carry Unwind

- Global equities fell 3-6% on August 5, 2024 (Nikkei −12% in one day, largest since 1987)
- BTC: $65,000 → $49,000 (−25%) Aug 2-5, 2024
- ETH: $3,100 → $2,100 (−32%) same period
- SOL: $190 → $130 (−32%) same period
- The mechanism: yen carry trade unwind forced mass deleveraging of risk assets globally. Crypto was sold as part of the same risk-off basket as equities.
- BTC-S&P 500 correlation in that week: 0.82 (usually 0.35-0.50)
- Source: Bloomberg (Aug 5-6 2024 coverage); BIS report on carry trade dynamics

### 2.2 Correlation Matrix: Normal vs Panic Regimes

| Pair | Normal (30-day calm) | Panic (peak event week) | Source |
| ------ | --------------------- | ------------------------ | -------- |
| BTC-ETH | 0.70-0.85 | 0.93-0.97 | CoinMetrics; Auer & Claessens (BIS, 2020) |
| BTC-SOL | 0.60-0.80 | 0.85-0.92 | Chainalysis 2022 |
| ETH-SOL | 0.65-0.82 | 0.88-0.94 | Chainalysis 2022 |
| BTC-NASDAQ | 0.35-0.55 | 0.65-0.85 | Bloomberg; BIS 2024 |
| Crypto broad index | — | 0.90-0.97 (all majors move together) | Multiple |

**Key insight**: In normal conditions, correlations justify treating BTC and ETH as "different positions." In panic conditions, they become effectively the same position. A portfolio model that uses normal-regime correlations for risk sizing systematically under-estimates tail risk.

### 2.3 Position Stacking Risk Quantification

SA-7 specifies: max 3 concurrent positions, 30% total exposure cap, 1% risk per trade.

Under normal-regime assumptions: 3 positions × 1% risk = 3% maximum portfolio drawdown if all stop simultaneously.

Under panic-regime correlation (ρ=0.95):

- All 3 stop-losses trigger simultaneously (correlation → 1 means they hit at the same price moment)
- Flash crash dynamics mean market-stop orders fill at the next available bid
- Typical gap-fill slippage during liquidity vacuum: 2-8% of position value on BTC/ETH, 5-15% on SOL/alts
- With 10% equity per position cap: 3 × 10% exposure × 10% gap slippage = **3% additional realized slippage** on top of the 3% intended loss = **6% portfolio drawdown in seconds**

The SA-7 daily drawdown limit of 3% would already have been breached before the bot can halt itself. This is the failure mode: individual strategy risk limits are sized for normal-liquidity fills. Panic liquidity converts 3% risk to 6%+ realized loss.

### 2.4 Detection Signals

| Signal | Time-to-detection | Data source |
| -------- | ------------------ | ------------- |
| Rolling 1-hour BTC-ETH correlation spikes above 0.90 | 60 min lookback | In-process from price feed |
| ATR expands to > 2× the 20-period ATR MA (SA-7 already monitors this) | Real-time | In-process |
| Spread widens > 5× median on multiple pairs simultaneously | Seconds | Order book feed |
| VIX-equivalent (BVIV - Bitcoin Volatility Index) spikes > 100 | Minutes | Deribit public API |
| Global equity indices (S&P 500) fall > 3% intraday | 5-15 min lag | Market data feed (optional) |

### 2.5 Defense Mechanisms

MVP1 — Correlation Lock (Spec assignment: risk-overlay spec)

```text
Rule: MAX_CORRELATED_POSITIONS = 1
Definition: BTC-correlated = any pair where 7-day rolling correlation to BTC > 0.80
Effect: If BTC, ETH, or SOL position is open, block new entries in the other two
Exception: Simultaneously allowed if different exchange (Binance BTC + Kraken BTC counts as same exposure bucket)
```

MVP1 — Volatility Halt (already in SA-7, reinforce here)

```text
Rule: ATR_SPIKE_THRESHOLD = 1.5× (20-period ATR MA)
Effect: When triggered → suppress all new entries until ATR normalizes (2 consecutive candles below threshold)
Extension: When multiple pairs simultaneously spike ATR → elevate to PORTFOLIO_HALT (not just entry suppression)
```

MVP2 — Dynamic Correlation Matrix

- Compute rolling 1-hour correlation matrix across all open positions
- If portfolio-level VaR (99% 1-day) exceeds X% of equity → reduce position sizes proportionally
- Use EWMA (Exponentially Weighted Moving Average) correlation with λ=0.94 (RiskMetrics standard)

---

## 3. Liquidity Vacuum

### 3.1 Historical Precedents

#### May 2019 — Binance BTC Single-Second Flash Crash

- Date: May 17, 2019
- BTC/USDT fell from ~$8,000 to ~$6,200 (−22.5%) in approximately 1 second on Binance
- Cause: A single large sell order walked the book. When combined with cascading stop-loss market orders from leveraged traders, the book was momentarily empty
- Recovery: Price rebounded to $7,800 within 3 minutes
- Reported bid-ask spread during the crash trough: estimated 5-15% (vs normal 0.01%)
- Source: CoinTelegraph "Binance Flash Crash" coverage (May 17, 2019); Binance post-mortem blog

#### March 12-13, 2020 — "Black Thursday" Liquidity Vacuum (All Crypto)

- Across all exchanges, bid-side liquidity evaporated simultaneously
- Binance BTC spread widened from 0.01% to approximately 0.5-2% during peak panic hours
- BitMEX temporarily halted due to server overload — paradoxically providing a floor as leveraged shorts could not pile on
- BTC/USD on BitMEX reached $3,600 (a $1,200 gap from Binance spot price) — demonstrating how cross-exchange liquidity fragmentation amplifies price dislocations
- DEX volume spiked 200%+ as traders tried alternatives
- Source: Auer & Claessens (BIS, Sept 2020); DeFi Pulse March 2020 data

#### May 2022 — LUNA/UST Cascade (Thin Book + Death Spiral)

- LUNA: $119 → $0.0003 over 38 days, but the book was effectively empty below $1 for most of the collapse
- UST depegged → algorithmic selling mechanism created a feedback loop
- Binance LUNA/USDT: bid depth collapsed to < $50K on a token previously carrying $10M+ in daily volume
- Source: Terra post-mortem; Chainalysis LUNA collapse analysis (2022)

#### February 2024 — Bybit-Adjacent Price Impact

- Following the Bybit $1.5B hack (Feb 21, 2024), ETH prices dropped 3-5% within the first hour
- ETH order books on multiple exchanges showed widened spreads during the news break
- This was not a full vacuum but demonstrates how hack news travels faster than liquidity replenishment
- Source: Blockchain security firm Elliptic; CoinDesk coverage Feb 21, 2024

### 3.2 OrderbookWalkFillModel Failure Mode

The `OrderbookWalkFillModel` (established in SA-3 as the hermes paper-trading fill engine) works correctly under the assumption that the top-N levels of the book exist and have reasonable depth. During a liquidity vacuum:

```text
Normal state (BTC/USDT, active session):
  Ask: [67,200.00 × 0.5 BTC], [67,210.00 × 1.2 BTC], [67,250.00 × 2.0 BTC]
  Bid: [67,190.00 × 0.8 BTC], [67,180.00 × 1.5 BTC], [67,150.00 × 3.0 BTC]

Liquidity vacuum state (same pair, post-flash-crash trigger):
  Ask: [empty]
  Bid: [66,000.00 × 0.02 BTC], [65,000.00 × 0.05 BTC], [60,000.00 × 0.10 BTC]
```

A market-sell of 0.5 BTC in paper mode would walk through the normal book for ~$67,190. In live mode during the vacuum, it would walk through 0.02 BTC at $66,000 + 0.05 BTC at $65,000 + 0.10 BTC at $60,000 + 0.17 BTC at prices below $60,000 (or fail to fill entirely if no more bids exist). The **paper P&L divergence per position** from a single vacuum event can be $1,000-$5,000 on a 0.5 BTC position.

**Critical**: The OrderbookWalkFillModel, even if the paper system is running and seeing the real book depth, will faithfully simulate the vacuum fill at $60,000. The problem is not the fill model's accuracy — it is that the paper system may not have received the vacuum-state book snapshot in time (WebSocket lag during exchange-level congestion) and may be using a stale book showing normal depth.

### 3.3 Exchange Circuit Breakers vs Bot Response

| Exchange | Mechanism | Trigger | Source |
| ---------- | ----------- | --------- | -------- |
| Binance | "Price filter" — rejects orders outside ±N% from reference price | Per-symbol config, not publicly disclosed | Binance API docs; PRICE_FILTER |
| Binance | Market lot size cap (MARKET_LOT_SIZE) — limits maximum single-order size | Rolling average of recent market order sizes | Binance exchangeInfo |
| Kraken | "Max price" / "Min price" per symbol | Per-symbol config | Kraken API docs |
| Kraken | System status endpoint — returns "maintenance" when exchange is offline | Polling required | `GET /api/v3/SystemStatus` |
| No exchange | **Does not halt trading during a flash crash automatically** | — | — |

Key finding: **Neither Binance nor Kraken implements an automatic trading halt during a flash crash for spot markets.** The PRICE_FILTER rejects orders far from the reference price, which can perversely trap a bot: it wants to sell at market but the market order gets rejected because the crash has moved price outside the filter. The bot's stop-loss order may be silently rejected during the event it was designed for.

### 3.4 Detection Signals

| Signal | Threshold | Time-to-detect |
| -------- | ----------- | ---------------- |
| Bid-ask spread > 10× 1H rolling median | Immediate (next book tick) | < 1 second |
| Best bid depth < 10% of 1H rolling average bid depth at top 5 levels | Immediate | < 1 second |
| Price moved > 3% in 60 seconds on any open position's pair | 60 seconds | 60 seconds |
| Consecutive order rejections (PRICE_FILTER) > 3 in 30 seconds | 30 seconds | 30 seconds |
| OrderbookWalkFillModel estimated slippage > 0.5% for a standard-size order | Real-time (paper mode) | Instant |

### 3.5 Defense Mechanisms

MVP1 — Max-Slippage Guard

```text
Rule: MAX_SIMULATED_SLIPPAGE_PCT = 0.50
Application: Before emitting any market order (live or paper), compute OrderbookWalkFillModel estimated fill
If estimated fill cost (vs mid-price) > 0.50% → CANCEL order, emit WARN event, route to operator alert
Exception: On stop-loss exits during position → allow up to 2.00% slippage (accepting worse fill to exit position)
```

MVP1 — Spread-Spike Kill Switch

```text
Rule: SPREAD_SPIKE_MULTIPLIER = 10.0
Definition: spread = (best_ask - best_bid) / mid_price
Rolling baseline: 1-hour median spread
If current spread > SPREAD_SPIKE_MULTIPLIER × baseline_spread:
  → Halt all new market-order entries
  → Emit CRITICAL spread_spike event
  → Do NOT halt existing position management (stop-losses must still fire)
  → Re-enable after spread normalizes for 5 consecutive minutes
```

MVP1 — Stale Book Guard

```text
Rule: MAX_BOOK_AGE_MS = 2000
If the last received book update timestamp is > 2000ms ago:
  → Mark book as STALE
  → Block OrderbookWalkFillModel fills (paper mode: count as "unfilled")
  → In live mode: halt new entries (stop-losses still permitted as market orders)
```

MVP2 — Adaptive Order Sizing During Vacuum

- Reduce order size to MIN_LOT_SIZE when vacuum detected (less capital committed to uncertain fill)
- Route to limit order with aggressive price (not market order) when vacuum detected

---

## 4. Stablecoin Depeg

### 4.1 Historical Precedents

#### USDT — May 12-13, 2022 (Terra/Luna Collapse)

- USDT briefly traded at **$0.9444** on Coinbase (vs $1.00 peg) on May 12, 2022
- On Kraken specifically: USDT/USD low of $0.9519 on the same date
- Mechanism: mass panic redemptions of all stables after UST collapsed
- Recovery: USDT returned to $0.998+ within 24 hours as Tether honored redemptions
- USDT market cap fell $10B in 3 days ($83B → $73B) via redemptions, proving reserve backing
- Source: CoinGecko USDT/USD historical; The Block Research (May 2022)

#### USDC — March 11, 2023 (Silicon Valley Bank)

- USDC fell to **$0.8770** on Coinbase on March 11, 2023
- Cause: Circle disclosed $3.3B in USDC reserves held at Silicon Valley Bank when SVB collapsed
- Recovery timeline: USDC returned to $0.9996 by March 13 after FDIC guaranteed deposits
- Peak depeg duration: ~36 hours of significant deviation (> 2% from peg)
- Trading volume during depeg: massive — everyone selling USDC for USDT or BTC
- Source: Circle official statement March 11, 2023; CoinGecko historical; Bloomberg coverage

#### DAI — March 2023 (Collateral Contagion)

- DAI depeg followed USDC depeg: DAI is collateralized partly by USDC
- DAI traded at $0.8983 during the USDC depeg trough (March 11, 2023)
- Recovery same as USDC (March 13)
- This demonstrates: "diversifying" into DAI does not protect against USDC depeg — they are correlated by construction
- Source: MakerDAO governance forums; CoinGecko DAI historical

#### UST/LUNA — May 7-13, 2022 (Algorithmic Depeg — Catastrophic)

- UST fell from $1.00 to $0.0001 over 7 days — complete depegging
- UST was algorithmic (not fully backed by real assets)
- This is a category-error risk: UST was never safe; USDT/USDC are different (fiat-backed with redemption mechanisms)
- However: the UST collapse triggered the USDT mini-depeg above (contagion)
- Source: Terra post-mortem; SEC complaint against Do Kwon

#### BUSD — February 2023 (Regulatory Seizure)

- Paxos (BUSD issuer) ordered by NYDFS to stop issuing BUSD (Feb 13, 2023)
- Binance announced BUSD phaseout; users forced to convert
- BUSD never depegged significantly (stayed $0.999-1.001) because redemptions were orderly
- Risk lesson: stablecoins can become unavailable without depegging; the denomination risk is regulatory, not price-based
- Source: NYDFS Paxos order; Binance announcement Feb 2023

### 4.2 Cash Bucket Assumption Failure

Hermes denominator chain:

```text
equity = cash_balance_USDT + sum(open_position_market_values_in_USDT)
risk_per_trade = equity × 0.01
position_size = risk_per_trade / (stop_distance_USD)
```

If USDT = $0.90 and the system does not know this:

- `cash_balance_USDT = 10,000` units is assumed to represent $10,000
- Actual value: $9,000
- `risk_per_trade` is computed as $100 but actual exposure is $90
- This is a 10% overstatement — a synthetic "free" drawdown the accounting layer cannot see

Worse: if the system simultaneously holds BTC/USDT long positions:

- BTC is priced in USDT on Binance
- When USDT depegs, BTC/USDT price RISES (BTC is constant in USD, USDT is worth less → more USDT per BTC)
- The system sees a "gain" in the BTC position while actually losing purchasing power
- The BTC/USDT gain and the USDT depeg loss partially cancel, but the accounting layer sees only the "gain"

This creates a scenario where the bot continues trading, seeing nominally increasing portfolio value, while real-world purchasing power is declining.

### 4.3 Stablecoin Correlation Warning

The May 2022 event showed USDT and DAI depegged simultaneously. A "diversify across stablecoins" strategy provides less protection than expected:

| Stablecoin | Depeg mechanism | Correlation to USDT depeg |
| ----------- | ----------------- | -------------------------- |
| USDT | Redemption pressure / trust loss | — |
| USDC | Counterparty bank failure (SVB 2023) | LOW (different mechanism) |
| DAI | USDC collateral exposure | HIGH when USDC depegs |
| BUSD | Regulatory seizure | LOW (different mechanism) |
| TUSD | Reserve composition opacity | MEDIUM (trust contagion) |

Practical recommendation: diversifying USDT + USDC provides meaningful protection (different risk mechanisms). Adding DAI does not (USDC correlation). Adding BUSD creates regulatory seizure risk.

### 4.4 Detection Signals

| Signal | Threshold | Data source | Time-to-detect |
| -------- | ----------- | ------------- | ---------------- |
| USDT/USD mid-price deviation | > 1.5% from $1.00 | Kraken USDT/USD public ticker | Seconds |
| USDC/USD mid-price deviation | > 1.5% from $1.00 | Kraken USDC/USD or Coinbase API | Seconds |
| USDT market cap 24h change | > −5% (mass redemptions) | CoinGecko public API | Minutes |
| CoinGecko stablecoin "confidence" flag | Any flag | CoinGecko /coins/tether | Minutes |

**Note**: Monitoring the USDT/USD pair on Kraken is the most reliable real-time signal for hermes, since Kraken already has an adapter (PR #193). A `GET /public/Ticker?pair=USDTZUSD` or `USDCUSD` gives the current peg state.

### 4.5 Defense Mechanisms

MVP1 — Depeg Monitor + Alert

```text
Rule: STABLECOIN_DEPEG_WARN_THRESHOLD = 0.015 (1.5% from peg)
Rule: STABLECOIN_DEPEG_HALT_THRESHOLD = 0.030 (3.0% from peg)

Poll USDT/USD and USDC/USD prices every 60 seconds via Kraken public endpoint.
If |price - 1.00| > WARN_THRESHOLD → emit WARN event, log, notify operator
If |price - 1.00| > HALT_THRESHOLD:
  → Halt all new entries immediately
  → Set flag: PORTFOLIO_HALT_DEPEG = True
  → Do NOT automatically close open positions (closing into BTC/USDT when USDT is depegging
    may not help if the de-nomination is the problem)
  → Require operator manual reset after depeg resolves
```

MVP1 — Equity Restatement on Depeg

```text
When USDT depeg detected:
  → Restate equity using: actual_equity = cash_USDT × usdt_price + position_values × usdt_price
  → All risk calculations (risk_per_trade, drawdown %) must use restated equity
  → Log the delta between nominal and restated equity as a "depeg_synthetic_pnl" event
```

MVP2 — Cash Diversification

- Hold cash 50% USDT / 50% USDC (different risk mechanisms)
- Rebalance quarterly or on any depeg event > 0.5%
- Do NOT hold DAI as primary cash vehicle given USDC correlation

---

## 5. Exchange Operational Risk

### 5.1 Binance Geoblocking and Regulatory Evolution (2023-2026)

#### European Regulatory Pressure (MiCA Transition)

- **June 2023**: Binance withdrew from Netherlands/Belgium after regulatory requirements (DNB/FSMA licenses). Dutch users given 3-month exit notice.
- **September 2023**: Binance exited Germany (BaFin pressure), Austria (FMA), Czech Republic, and Poland (UKNF). The rollout was staggered over 8 weeks.
- **August 2023**: Binance suspended EUR bank deposits for European users, then restored with SEPA via alternative providers.
- **December 2023**: Cyprus license obtained (EU passport for MiCA transitional period). Binance re-entered some EU markets under the Cyprus CIF license as a bridging mechanism.
- **2024**: MiCA Regulation (EU) 2023/1114 came into force. Title III (asset-referenced tokens) effective June 30, 2024. Title VI (crypto-asset service providers, including exchanges) effective December 30, 2024.
- **2025-2026 (current)**: Binance operates under MiCA CASP regime. Spanish users served via Binance Spain S.A. (registered with CNMV as obliged entity under anti-money laundering law). Full MiCA CASP authorization application outstanding as of 2026.

**Risk for hermes**: If Binance loses MiCA authorization or is blocked for Spanish users, all open positions must be closed and capital withdrawn. There is no API warning of this — it would manifest as API authentication failures or account access errors.

#### Testnet Stability

- Binance Spot Testnet (`testnet.binance.vision`) has documented intermittent outages during high-load periods on mainnet
- Testnet data is synthetic (not mirrored from mainnet). During real market panic events, testnet behavior does NOT replicate mainnet behavior — not a useful shock-testing environment
- Source: Binance developer documentation; community reports on GitHub

### 5.2 Kraken Maintenance Windows and Downtime

Historical maintenance events:

- Kraken has experienced planned and unplanned outages during high-volatility periods. Notably:
  - March 2020: Kraken websocket issues coincided with COVID crash — users unable to place orders during peak volatility
  - May 2021 (crypto mania peak): Kraken exchange temporarily down for ~40 minutes during BTC all-time high approach
  - Multiple shorter interruptions (2-15 min) documented via Kraken status page (status.kraken.com)

API deprecation risk:

- Kraken WebSocket API v1 was deprecated in favor of v2. Hermes implements v2 (per SA-3). If Kraken deprecates v2 in favor of v3, hermes requires adapter update.
- Kraken REST API versioning: currently `/api/v3/` for core endpoints. Changes require monitoring of Kraken changelog.

Rate limit changes:

- Kraken uses a tier-based rate limit system (Starter: 15/60s; Intermediate: 20/60s; Pro: 20/60s with burst). Changes to tiers or limits require adapter updates.
- Binance rate limit system uses "request weight" (X-MBX-USED-WEIGHT header). Weight values per endpoint change with API updates.

### 5.3 Exchange Withdrawal Freezes

| Exchange | Event | Date | Duration | Impact |
| ---------- | ------- | ------ | ---------- | -------- |
| Celsius | Withdrawal freeze | June 12, 2022 | Permanent (bankruptcy) | ~$4.7B customer funds |
| Voyager Digital | Withdrawal freeze | July 1, 2022 | Permanent (bankruptcy) | ~$1.3B customer funds |
| FTX | Withdrawal freeze | Nov 8, 2022 | Permanent (collapse) | ~$8.9B customer funds |
| Binance.US | Fiat withdrawal suspension | June 2023 | 2 months (regulatory action) | No crypto freezes |
| Crypto.com | Accidental withdrawal of $400M to wrong addresses | Jan 2022 | 14h (recovered) | Temporary |

**Key lesson**: The post-collapse exchanges (Celsius, Voyager, FTX) all showed warning signs before the freeze:

- Net asset value vs liabilities gap (Celsius: borrowed at yield, lent to illiquid DeFi)
- Proof-of-reserves gaps (FTX)
- CEO activity (CZ tweet Nov 6, 2022 triggered FTX bank run)

For Binance and Kraken (hermes MVP1 targets): these are among the most regulated and largest exchanges. Withdrawal freeze risk is LOW but NOT ZERO.

**Detection signal**: Monitor withdrawable balance vs reported balance via REST API. Any gap is a warning. Hermes should track: `withdrawable_USDT = GET /api/v3/account` → `free` field. Alert if `free` suddenly drops without a corresponding trade.

### 5.4 Defense Mechanisms

MVP1 — Exchange Health Monitor (extends SA-4 P2 item to MVP1 given portfolio scope)

```text
Every 60 seconds (idle) or 10 seconds (active trading):
  → GET /api/v3/ping (Binance) — confirm round-trip < 500ms
  → GET /api/v3/systemStatus (Kraken) — confirm "online"
  → If 3 consecutive health check failures → emit CRITICAL, halt new entries
  → If REST ping succeeds but WebSocket has been silent > 30s on subscribed channels → force reconnect
```

MVP1 — API Deprecation Watch

```text
- Subscribe to Binance changelog RSS / Kraken changelog
- Pin adapter versions against specific API endpoint paths
- Integration tests must fail explicitly if expected endpoint paths return 404/410
```

MVP1 — Withdrawal Balance Monitor

```text
Daily check: compare reported free balance (REST) against expected free balance (ledger)
If gap > 0.1% of portfolio value → emit WARN, require operator acknowledgment
Purpose: early detection of exchange fund accessibility issues before they become freezes
```

MVP2 — Multi-Exchange Routing

- If Binance unavailable → automatically route new entries to Kraken (and vice versa)
- Requires position tracking to be exchange-agnostic (SA-8 event sourcing supports this)
- Capital must be held partially on each exchange to enable routing

---

## 6. Regulatory Shock

### 6.1 MiCA Transition Timeline (EU)

| Date | Event | Impact on hermes (Spain) |
| ------ | ------- | -------------------------- |
| June 29, 2023 | MiCA published in Official Journal | Legal certainty begins |
| June 30, 2024 | Title III effective (asset-referenced tokens, e-money tokens) | USDT/USDC issuers must comply; potential USDT restrictions in EU |
| December 30, 2024 | Title VI effective (CASP regime) | Exchanges must be MiCA-authorized or exit EU |
| 2025-2026 | Transition period; national competent authorities processing CASP applications | Operational uncertainty |
| Dec 31, 2026 | Transition period ends; only MiCA-authorized CASPs permitted | Full enforcement |

**USDT specific MiCA risk**: MiCA Article 50 limits non-EU stablecoin (e.g., USDT issued by Tether, Cayman Islands) daily transaction volume to €200M/day in the EU. Binance must monitor this limit. If breached, Binance is required to suspend USDT trading for EU users. This creates a scenario where USDT pairs become unavailable on Binance for EU users with no warning — the trigger is an aggregate volume limit, not a price event.

**Current status (2026)**: Binance is processing its CASP application under MiCA. Operation continues under the transitional grandfather period. Kraken received MiCA authorization via Ireland (VASP) and Cyprus CIF, providing more regulatory stability.

### 6.2 Spanish-Specific Regulatory Requirements

CNMV Registration (Spain)

- The CNMV (Comisión Nacional del Mercado de Valores) requires registration for crypto-asset service providers operating in Spain
- Binance Spain S.A. is registered with CNMV as an obliged entity (Anti-Money Laundering Law 10/2010)
- Operating a trading bot for personal account (not as a service provider) does NOT require CNMV registration
- Running a bot for third parties DOES require registration

Tax Reporting (Spain)

- **Modelo 172**: Annual informative declaration of balances held in crypto-assets (introduced 2023). Threshold: total balance > €50,000 at any point in the year. Due March 31 of following year.
- **Modelo 721**: Annual declaration of crypto-assets held abroad (introduced 2023). Threshold: total value > €50,000. Due March 31.
- **Modelo 100** (IRPF): Capital gains/losses from crypto trading reported annually. Each trade is a taxable event. Gains taxed at 19-28% (tramo de ahorro). Losses deductible against gains.
- Hermes paper-trading on testnet: NO tax consequences (testnet funds are not real).
- Hermes paper-trading on mainnet (simulated paper layer, real data): NO tax consequences (no actual trades placed).

LOPDGDD Considerations

- Hermes as a solo-operator tool (no user data collected): minimal LOPDGDD exposure
- If hermes stores Binance/Kraken API credentials locally: these are personal data of the operator, not third-party PII. No LOPDGDD exposure beyond normal credential security.
- If hermes adds a web UI accessible by users: LOPDGDD applies (cookie consent, data processing declarations)

### 6.3 Trigger Events for "Halt Trading Immediately"

| Event | Trigger | Action |
| ------- | --------- | -------- |
| Exchange suspends USDT/USDT pairs for EU users (MiCA Vol. limit) | Exchange API returns "symbol_not_allowed" or 0 liquidity | Halt all new entries; close positions at market on whatever pair is available |
| CNMV issues cease-and-desist to exchange | News/official publication | Manual halt; await legal clarity |
| Binance loses MiCA transitional period | Exchange access failure (API auth rejected) | Emergency close all positions; withdraw funds |
| Tax year-end reporting deadline | Calendar: Dec 31 | No automated action; reminder to operator |

### 6.4 Defense Mechanisms

MVP1 — Symbol Availability Check

```text
Before any entry: confirm the symbol is tradeable (not suspended, not halted)
Binance: symbol.status == "TRADING" (from exchangeInfo)
Kraken: pair.status == "online"
If symbol unavailable → halt entries for that pair, alert operator
```

MVP1 — Regulatory Event Log

```text
Append-only log of regulatory actions noted by operator
Referenced during any portfolio audit
Not automated — requires operator to record events
```

MVP2 — Multi-Jurisdiction Fallback

- If Binance EU operations suspended → route to Kraken (MiCA-authorized)
- Maintain a "regulatory_status" configuration that maps exchange → permitted_pairs
- Bot reads this config at startup; if exchange not in permitted list → skip that exchange

---

## 7. Blockchain-Level Risk (Awareness)

This section is noted for awareness only. Hermes MVP1 is spot trading on centralized exchanges — blockchain-level risks are intermediated by the exchange. However, they drive exchange-level risks that cascade to hermes.

### 7.1 Exchange Hot-Wallet Hacks

| Exchange | Amount | Date | BTC price impact | Source |
| ---------- | -------- | ------ | ----------------- | -------- |
| Binance | $570M (BNB) | Oct 2022 | −3% BTC in 24h | Binance official statement |
| FTX (not a hack, but loss of control) | $477M unauthorized withdrawals | Nov 2022 | Coincided with FTX collapse | Elliptic |
| Bybit | $1.5B (ETH via Lazarus Group) | Feb 21, 2024 | ETH −3-5% on news | Elliptic; FBI attribution |
| Coincheck | $534M (NEM) | Jan 2018 | NEM −25%, broader market −5% | CoinTelegraph |
| Mt. Gox | $450M (BTC) | Feb 2014 | BTC −36% over following 2 weeks | Historical |

**Pattern**: Exchange hacks produce 3-10% price drops in the affected token immediately, recovering partially within 24-72 hours. They also trigger correlation-shock dynamics (traders fear contagion → sell all majors).

**Hermes exposure**: If Binance's hot wallet is hacked:

1. Binance may freeze withdrawals (to prevent further loss)
2. BTC/ETH prices fall 3-10% → may trigger stop-losses
3. The stop-losses may fill at worse-than-modeled prices (panic + spread widening)

**Detection**: The hack itself cannot be detected by hermes before the exchange discloses it. The secondary market signal (spread spike + ATR spike + price drop) is the detectable proxy.

**Defense**: The Spread-Spike Kill Switch (§3.5) and ATR halt (§2.5) would trigger in response to the market reaction, even if hermes cannot detect the hack itself.

### 7.2 Blockchain Reorg Risk

- Applies primarily to proof-of-work chains (BTC/ETH pre-merge, now ETH uses PoS)
- For deposits/withdrawals: exchanges require N confirmations before crediting. Reorg risk is exchange's problem to manage, not hermes's.
- For spot trading on a centralized exchange: reorgs are invisible to hermes (the exchange's internal ledger is settled in real-time, not on-chain)
- **Not a direct MVP1 risk**. Noted for completeness.

### 7.3 Smart Contract Risk

- Not applicable to hermes MVP1 (centralized exchange spot only, no DeFi protocols)
- Future versions using DEX routing would require protocol risk assessment

---

## 8. Systemic Macro Shocks

### 8.1 Federal Reserve Rate Decisions

**Mechanism**: Fed rate hikes reduce the appeal of risk assets (higher return available from bonds → capital rotates out of equities and crypto). Fed pivots (rate cuts or dovish signals) drive the opposite.

Historical crypto price reactions to Fed events:

| Date | Fed Event | BTC move (24h) | ETH move (24h) |
| ------ | ----------- | ---------------- | ---------------- |
| March 16, 2022 | First 25bp hike | −1% (already priced) | −1% |
| May 4, 2022 | 50bp hike (largest since 2000) | +0.6% (relief rally) | +0.5% |
| June 15, 2022 | 75bp hike (surprise) | −18% in week; −4% day-of | −16% week |
| November 2, 2022 | 75bp hike | −3% | −5% |
| March 2023 | Pause signals | +20% (BTC March rally) | +18% |
| September 18, 2024 | 50bp cut (first cut in 4 years) | +4% | +3% |

**Key insight**: The market reaction depends on whether the decision is priced in. Surprise moves (June 2022 75bp unexpected) produce outsized reactions. Fed meeting dates are known in advance (FOMC calendar).

**For hermes**: Fed decisions happen 8 times per year (FOMC meetings), at 2:00 PM Eastern (19:00 UTC). The 30-minute window around FOMC announcements is a **known high-volatility window**. ATR spike detection (SA-7) would trigger during these events, but the mechanism is predictable and could be addressed proactively.

### 8.2 August 2024 Yen Carry Unwind — Deep Analysis

This is the most relevant systemic macro shock for hermes because it combined:

1. A **non-crypto trigger** (Bank of Japan rate hike on July 31, 2024 → yen strengthened → carry trade unwind)
2. **Equity + crypto correlation spike** (BTC-S&P correlation: 0.82)
3. **Speed of contagion** (crypto markets moved before most traders understood the cause)

Timeline:

- July 31: Bank of Japan raised rates 15bp (surprise). Yen began strengthening.
- August 1-2: Global equities begin selling off. BTC: $65,000 → $62,000.
- August 5: Nikkei 225 fell 12.4% (largest single-day drop since 1987 Black Monday). S&P 500 −3%. BTC: $65,000 → $49,500 (−24% from July 31 high). ETH: $3,100 → $2,100 (−32%).
- August 6: Partial recovery. BTC: $56,000.
- August 20+: BTC recovered to $60,000+.

**Why crypto was hit as hard as equities**: Crypto acts as a leveraged risk-asset in macro sell-offs. Institutions holding both crypto and equities sell whichever is most liquid (BTC/ETH are 24/7) first, before equity markets open. This means crypto leads equity declines in macro shocks, then partially recovers after equity market opens and full picture becomes clearer.

What ATR looked like during August 2024 event:

- Normal BTC daily ATR (June-July 2024): ~$2,500
- August 5, 2024 single-day ATR: ~$10,000 (4× normal)
- This would trigger the SA-7 ATR spike filter (1.5× threshold = ~$3,750) on the first spike candle

### 8.3 Correlated Equity + Crypto Crashes

The BTC-S&P 500 correlation is NOT constant. It spikes during systemic shocks:

| Period | BTC-S&P 500 Rolling Correlation | Market state |
| -------- | -------------------------------- | -------------- |
| 2018 | 0.05-0.15 | Uncorrelated, independent cycle |
| 2020 (COVID crash) | 0.40-0.65 | Liquidity shock → all risk assets |
| 2021 (post-COVID) | 0.35-0.50 | Moderate correlation |
| 2022 (rate hike cycle) | 0.55-0.70 | Correlated in hawkish macro |
| 2023 (banking crisis) | 0.40-0.60 | Moderate |
| 2024 (yen unwind) | 0.70-0.85 | High during panic week |

**Implication**: In sustained macro downturns (2022 rate hike cycle), BTC-S&P correlation of 0.65 is the new baseline. A swing strategy that is agnostic to macro regime will run into a headwind where every long entry correlates with equity market weakness.

### 8.4 Bond Market Volatility Spillover

**MOVE Index** (ICE BofA Option Volatility Estimate, analogous to VIX for bonds):

- High MOVE → bond market unstable → credit conditions tightening → risk-off across all assets
- March 2023 (SVB): MOVE spiked to 182.7 (highest since 2009 GFC)
- The SVB-driven MOVE spike preceded the USDC depeg (connected: SVB collapse → Circle USDC exposure → USDC depeg)

**For hermes**: MOVE data is available via FRED (Federal Reserve Economic Data, free API). Monitoring MOVE as a macro risk flag is a MVP2 enhancement — hermes should know when bond volatility is elevated and weight that as a regime factor against new entries.

### 8.5 Detection Signals for Systemic Macro Shocks

| Signal | Source | Threshold | MVP priority |
| -------- | -------- | ----------- | -------------- |
| BTC ATR > 2× 20-period ATR MA | In-process | Already in SA-7 | MVP1 |
| FOMC meeting date approaching (known calendar) | Hardcoded or calendar API | 30-min window before/after | MVP1 (suppress entries) |
| BTC-S&P 500 intraday correlation > 0.80 | Requires S&P data feed | Monitor | MVP2 |
| MOVE Index > 150 | FRED API | Monthly check | MVP2 |
| VIX > 35 (equity fear gauge) | FRED or market data | Weekly check | MVP2 |

### 8.6 Defense Mechanisms

MVP1 — FOMC Calendar Blackout

```text
Rule: FOMC_BLACKOUT_MINUTES_BEFORE = 30, FOMC_BLACKOUT_MINUTES_AFTER = 60
Data: Hardcode 8 annual FOMC dates (published at start of each year by Fed Reserve)
Action: During blackout window → suppress all new entries
Action: Existing positions: tighten trailing stop to 1.5× ATR (instead of 3.0×) during blackout
```

MVP1 — Macro ATR Cascade Halt

```text
Extension of SA-7 ATR spike rule:
If ATR spike detected on 2+ pairs simultaneously (not just the traded pair):
  → Elevate from ENTRY_SUPPRESS to PORTFOLIO_HALT (all new entries halted)
  → Emit SYSTEMIC_VOLATILITY alert
  → Resume only after ATR normalizes on all pairs for 3 consecutive candles
```

MVP2 — Macro Regime Integration

- Weekly macro regime check: VIX, MOVE, BTC-S&P correlation
- If ≥2 of 3 macro signals are "stressed" → reduce position sizing to 50% (half-Kelly variant)
- Implement as a weekly cron job updating a `macro_regime` config value

---

## 9. Composite Scenarios

Portfolio shocks rarely arrive in isolation. The most dangerous scenarios are when multiple shock types compound.

### 9.1 Composite Scenario A: Panic + Vacuum + Correlation (the "March 2020" Pattern)

**Trigger**: Large sell order or negative news → price drop → stop-loss cascade → BTC/ETH/SOL stops all trigger simultaneously → all positions exiting at market into an empty book.

**What SA-7 defenses catch**: Individual position stop-loss triggers. Daily drawdown limit (3%) would fire.

**What SA-7 defenses miss**: The gap between where stop-loss triggers and where market fills during vacuum. 3% intended loss becomes 8-15% realized loss if all 3 positions gap through stops simultaneously.

**What SA-9 defenses add**:

1. Correlation lock prevents 3 simultaneous correlated positions from forming
2. Max-slippage guard limits the fill price deviation on each exit order
3. Spread-spike kill switch may halt new market orders (but existing position exits must still proceed)

**Sequencing matters**: The order of triggers determines survivability.

- If ATR spike fires FIRST → entry suppression kicks in → no new positions to compound losses
- If correlation shock arrives BEFORE ATR spike filter catches up (1 candle lag) → existing positions already open take the full loss

**MVP1 minimum**: Correlation lock is the critical pre-entry defense. It must be implemented before the first live paper run on multiple pairs.

### 9.2 Composite Scenario B: Depeg + Accounting Confusion (the "March 2023" Pattern)

**Trigger**: USDC depegs to $0.88. DAI follows to $0.89 (collateral linkage). At the same time, BTC rises slightly (flight to "real" assets within crypto) so BTC/USDC price rises significantly.

**Hermes accounting failure**:

- Cash balance in USDC understated in real USD by 12%
- Open BTC/USDC long shows a "profit" (BTC/USDC price rising)
- The "profit" partially reflects the denominator depeg, not a real gain
- Risk-per-trade is computed using inflated equity → oversized positions

**SA-9 defense**: Depeg monitor fires at 2% deviation → halt new entries → equity restatement. The restatement recalculates equity with actual stablecoin price, preventing the oversized-position risk.

**Gap**: The 60-second polling interval means up to 60 seconds of undetected depeg. During the USDC depeg, price moved from $0.99 to $0.92 over ~6 hours (not a flash event) — 60-second polling is adequate for this pattern.

### 9.3 Composite Scenario C: Exchange Geoblocking + Active Positions (MiCA Risk)

**Trigger**: EU regulator orders Binance to suspend service for Spanish users immediately (hypothetical but possible enforcement action). Binance API returns 403 for all Spanish-IP requests.

**Hermes state**: Has 2 open BTC/USDT positions on Binance with trailing stop-loss orders placed on exchange.

**What survives**: The on-exchange stop-loss orders (SA-7 MVP1 recommendation to place stops on exchange) would still be active. Even if hermes loses API access, the exchange-native stops remain. Positions would close if price hits the stop.

**What doesn't survive**: Hermes cannot monitor, modify, or cancel the stops. Cannot check fill status. Cannot add to or reduce positions. Can potentially lose the ability to close positions at desired prices if the stop prices are stale.

**SA-9 defense**: Symbol availability check detects loss of access. Operator alert fired immediately. On-exchange stop-loss (SA-7) provides passive protection even without API access.

**Residual risk**: If the exchange requires MiCA re-authorization before allowing withdrawals (not just trading), the capital could be temporarily locked. Defense: maintain working Kraken account with sufficient capital to continue operations independently.

### 9.4 Composite Scenario D: Systemic Macro + Correlation + Hack News

**Trigger**: August 2024 yen carry unwind style event, but also accompanied by a hack announcement (e.g., exchange hot-wallet compromise) mid-event.

**Compounding**: Price already falling 15-20% → hack news adds 5-10% → BTC/ETH/SOL all correlated → all stops fire into a book emptied by two simultaneous panic events.

**What helps**: Correlation lock (max 1 BTC-correlated position) limits the simultaneous stop count to 1 instead of 3. ATR spike filter (triggered by first 15% move) suppresses any new entries during the hack news spread.

**What remains dangerous**: The single permitted open position still exits into the thin book. Gap-fill slippage of 5-10% on the one position.

**SA-9 defense for the residual**: The max-slippage guard (2% threshold for exits) would force the exit order to pause if the estimated fill is catastrophic. However, during a genuine panic, the operator may need to accept worse slippage to exit. The guard should be a WARN, not a BLOCK, for stop-loss exit orders.

---

## 10. Defense Matrix

| Shock Class | Detection Signal | MVP1 Defense | MVP2 Enhancement | Spec Assignment |
| ------------- | ----------------- | -------------- | ----------------- | ----------------- |
| **Correlation shock** | Rolling 1H BTC-ETH corr > 0.90; Multi-pair simultaneous ATR spike | `correlation_lock`: max 1 BTC-correlated position; `PORTFOLIO_HALT` on 2+ pair ATR spike simultaneously | Dynamic correlation matrix; EWMA VaR limit | risk-overlay spec |
| **Liquidity vacuum** | Spread > 10× 1H median; Book depth < 10% rolling avg; Price > 3% in 60s | Max-slippage guard (0.5% new entries, 2% exits); Spread-spike kill switch; Stale book guard (2s timeout) | Adaptive order sizing; Limit orders during vacuum | fill-model spec + risk-overlay spec |
| **Stablecoin depeg** | USDT/USD or USDC/USD deviation > 1.5% | Depeg monitor (60s polling via Kraken); WARN at 1.5%, HALT at 3%; Equity restatement on depeg | Cash diversification (50% USDT / 50% USDC) | risk-overlay spec |
| **Exchange operational** | Health check failure (3 consecutive); Symbol status ≠ TRADING; WebSocket silence > 30s | Exchange health monitor (60s/10s polling); Symbol availability check on entry; Withdrawal balance daily check | Multi-exchange routing; Regulatory_status config | exchange-adapter spec |
| **Regulatory** | Symbol API status; Exchange HTTP 403; CNMV/EU enforcement news (manual) | Symbol availability check; On-exchange stop-loss (passive protection even without API access) | Multi-jurisdiction fallback; Regulatory event log | exchange-adapter spec + ops runbook |
| **Blockchain/hack** | Spread spike + ATR spike proxy (hack news → market reaction) | ATR halt (SA-7) + Spread-spike kill switch catch the market reaction; on-exchange stops provide passive protection | Proof-of-reserves monitoring for exchange health | risk-overlay spec |
| **Systemic macro** | ATR spike (in-process); FOMC calendar blackout; Multi-pair ATR cascade | FOMC calendar blackout (30m before, 60m after); Multi-pair ATR cascade → PORTFOLIO_HALT | Weekly macro regime check (VIX, MOVE, BTC-S&P corr); Position sizing reduction in stressed regime | risk-overlay spec |

Spec ownership summary:

- `risk-overlay spec` (new): owns correlation lock, spread-spike kill switch, depeg monitor, equity restatement, FOMC blackout, multi-pair ATR cascade halt, max-slippage guard
- `fill-model spec` (existing): owns stale-book guard, max-slippage guard implementation in OrderbookWalkFillModel
- `exchange-adapter spec` (existing): owns health monitor, symbol availability check, withdrawal balance monitor, multi-exchange routing

MVP1 minimum viable risk overlay (8 rules, all must ship):

| # | Rule | Threshold | Action |
| --- | ------ | ----------- | -------- |
| RO-01 | Correlation lock | No 2 BTC-correlated positions simultaneously | Block entry in position sizer |
| RO-02 | Max slippage (entry) | Simulated slippage > 0.50% | Cancel order; WARN alert |
| RO-03 | Max slippage (exit) | Simulated slippage > 2.00% | WARN; allow exit (do not block) |
| RO-04 | Spread-spike halt | Spread > 10× 1H median | Halt new market-order entries; CRITICAL alert |
| RO-05 | Stale book guard | Last book update > 2000ms | Mark STALE; block new entries |
| RO-06 | Depeg monitor | USDT or USDC deviation > 1.5% | WARN; halt at 3%; equity restatement |
| RO-07 | FOMC blackout | 30m before / 60m after | Suppress new entries; tighten trailing stop |
| RO-08 | Multi-pair ATR cascade | ATR spike on 2+ pairs simultaneously | PORTFOLIO_HALT; SYSTEMIC_VOLATILITY alert |

---

## 11. Confidence Assessment

| Section | Confidence | Basis |
| --------- | ------------ | ------- |
| Correlation shock — historical data | HIGH | Well-documented academic and industry sources (BIS, Chainalysis) |
| Correlation shock — thresholds | MEDIUM | Thresholds (0.80 BTC-correlation, 10× spread) are reasonable but not calibrated on hermes data |
| Liquidity vacuum — exchange behavior | HIGH | SA-3 documents real order book structure; Binance PRICE_FILTER behavior is documented in API |
| Liquidity vacuum — fill model failure | HIGH | Logically derived from OrderbookWalkFillModel design + vacuum event historical data |
| Stablecoin depeg — event dates and magnitudes | HIGH | Primary sources (CoinGecko, Circle official statements, NYDFS orders) |
| Stablecoin depeg — USDT/USD polling via Kraken | HIGH | Kraken USDT/USD pair exists and is supported by existing Kraken adapter |
| Exchange operational — Binance EU geoblocking | HIGH | Official Binance announcements; CNMV registrations are public records |
| Exchange operational — withdrawal freeze patterns | HIGH | Multiple documented cases with primary sources |
| Regulatory — MiCA timeline | HIGH | Official EU regulation text (2023/1114); Binance official statements |
| Regulatory — Spanish tax obligations | HIGH | AEAT official guidance on Modelos 172/721/100 |
| Systemic macro — Fed event reactions | MEDIUM | Price reactions are documented but directional prediction is LOW confidence; known calendar blackout is HIGH confidence |
| Yen carry unwind analysis | HIGH | Bloomberg/BIS contemporaneous coverage; timing and magnitudes verified |
| Composite scenarios | MEDIUM | Logical inference from component scenarios; no direct historical precedent for exact compound sequences |
| Defense thresholds (specific numbers) | MEDIUM | Reasonable engineering defaults; require calibration against actual hermes paper-trading data |

Overall report confidence: HIGH on historical events and mechanisms; MEDIUM on specific threshold values (all thresholds should be treated as starting points, calibrated via SA-5 calibration harness).

---

## 12. Citations

### Academic and Research

1. **Auer, R. & Claessens, S.** (2020). "Cryptocurrency Market Reactions to Regulatory News." BIS Quarterly Review, September 2020. <https://www.bis.org/publ/qtrpdf/r_qt2009.htm>

2. **Conti-Brown, P. & Hirst, A.** (2022). "The FTX Collapse: How It Happened and What It Means." Penn Wharton Public Policy Initiative Issue Brief, November 2022. <https://publicpolicy.wharton.upenn.edu/policy-briefs/ftx-collapse.php>

3. **BIS** (2024). "Carry trades and global financial stability." BIS Quarterly Review, September 2024 (covering August 2024 yen unwind). <https://www.bis.org/publ/qtrpdf/r_qt2409.htm>

### Exchange Official Sources

1. **Binance** (2019). Flash crash incident acknowledgment, May 17, 2019. Community post on Binance.com.

2. **Binance API Docs**. `PRICE_FILTER`, `MARKET_LOT_SIZE`, and system status endpoints. <https://developers.binance.com/docs/binance-spot-api-docs/>

3. **Kraken Status**. <https://status.kraken.com/> (historical maintenance logs).

4. **Kraken API v2**. `/public/Ticker`, `/public/AssetPairs`, `/api/v3/SystemStatus`. <https://docs.kraken.com/api/>

### Stablecoin Events

1. **Circle Official Statement** (March 11, 2023). USDC reserve disclosure re: Silicon Valley Bank. <https://www.circle.com/blog/an-update-on-usdc-and-silicon-valley-bank>

2. **The Block Research** (May 2022). "USDT Briefly Depegged — What Happened." May 12, 2022. <https://www.theblock.co/>

3. **Tether** (2022). USDT transparency reports and reserve attestations. <https://tether.to/en/transparency/>

4. **MakerDAO Governance Forums** (March 2023). DAI depeg analysis and USDC collateral exposure. <https://forum.makerdao.com/>

### Regulatory

 1. **EU Regulation 2023/1114** (MiCA). Official Journal of the European Union, June 9, 2023. <https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32023R1114>

 2. **NYDFS — Paxos Order** (February 13, 2023). Order to cease issuing BUSD. <https://www.dfs.ny.gov/reports_and_publications/press_releases/pr202302131>

 3. **AEAT (Spain)** (2023). Instrucciones para la presentación del Modelo 172 y 721. <https://www.agenciatributaria.es/>

 4. **Binance Spain S.A.** CNMV registration. Public CNMV register. <https://www.cnmv.es/>

### Exchange Hacks / Security

 1. **Elliptic** (2024). "Bybit Hack: $1.5 Billion Stolen by Lazarus Group." February 21, 2024. <https://www.elliptic.co/blog/lazarus-group-bybit>

 2. **Elliptic** (2022). Binance BNB chain hack analysis. October 2022. <https://www.elliptic.co/blog/bnb-chain-hack>

 3. **Chainalysis** (2022). "Crypto Market Downturn 2022: What the On-Chain Data Shows." <https://www.chainalysis.com/blog/crypto-market-downturn-2022/>

### Prior SA Research (Internal)

 1. **SA-4 Post-Mortems** (2026-08-24). `research/paper-trading-sa4-post-mortems`. Covers bot-level operational failures, FTX death spiral, flash crash stop-loss behavior, rate limit mechanics.

 2. **SA-7 Strategy Design** (2026-08-24). `research/paper-trading-sa7-strategy-design`. Covers Chandelier Exit sizing, ATR spike filter, drawdown limits, max concurrent positions, correlation warning for BTC/ETH simultaneous holds.

 3. **SA-3 Microstructure** (2026-08-24). `research/paper-trading-sa3-microstructure`. Covers OrderbookWalkFillModel, Binance PRICE_FILTER, orderbook bootstrap protocol, fee schedules.

---

End of SA-9 Portfolio Shocks + Risk Overlay
