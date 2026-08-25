---
name: Pre-Registration — Swing MVP1
version: 0 (DRAFT — not yet signed or hashed)
author: Claude (drafted from research vault) — pending operator review
date_drafted: 2026-08-25
experiment_id: swing-mvp1-v0
status: DRAFT
---

# Pre-Registration Document — Swing Strategy MVP1

> This document is the SCIENTIFIC PROTOCOL for the first live-capital experiment of the hermes trading system. It is written BEFORE any parameter tuning, backtest optimization, or data inspection. Once signed and hashed (v1), any deviation from this document constitutes a **new experiment** with a new pre-registration. This is the anti-p-hacking mechanism.
>
> Analogy: this is the IRB-registered protocol of a clinical trial. The engine is the apparatus. The trader is the theorist. This document is the falsifiable claim.

---

## 1. Hypothesis

### Primary hypothesis (H1)

The Swing strategy defined in §5 has statistically significant positive risk-adjusted return on the asset universe defined in §3, over the OOS test window defined in §4, NET of realistic fees and slippage modeled per §6, at the position sizing defined in §5.4.

### Null hypothesis (H0)

Swing strategy risk-adjusted return is indistinguishable from zero (or from a buy-and-hold benchmark) after transaction costs, over the same OOS window.

### Decision rule

- **H1 accepted** iff Deflated Sharpe Ratio (DSR) on OOS window > 0.7 AND all secondary metrics (§9) pass their thresholds.
- **H0 not rejected** otherwise. Strategy is declared UNTRUSTED and does not receive live capital.

### What this experiment CANNOT answer

- Whether the strategy will work in future regimes not represented in the OOS window (non-stationarity risk).
- Whether the strategy is optimal — only whether it is positive.
- Whether it is the BEST strategy — only whether it is a viable one.

---

## 2. Experimental capital

| Parameter | Value | Rationale |
| ----------- | ------- | ----------- |
| Total experiment capital | **$5,000 USDT** | Smallest amount where fees do not dominate signal; below this, the experiment tests fee friction rather than strategy edge. |
| Denomination | USDT | Base of asset universe; SA-9 depeg overlay applies. |
| Funding schedule | — | Full $5k at experiment start OR tranches — operator's discretion — but pre-registered risk % refers to full $5k denominator regardless of actual deployed balance |
| Scaling post-certification | — | Post-CERTIFICATION scaling is a portfolio decision, NOT part of this experiment. Any capital change during the experiment invalidates results. |

---

## 3. Asset universe (FIXED)

| Symbol | Exchange | Rationale |
| -------- | ---------- | ----------- |
| BTC/USDT | Binance spot | Highest liquidity crypto; base benchmark. |
| ETH/USDT | Binance spot | Second-largest; correlated to BTC but with independent flow (staking, DeFi). |
| SOL/USDT | Binance spot | Higher-vol third leg; captures alt-season regime. |

**No additions permitted during the experiment.** If a pair delists mid-experiment, the pair is closed and excluded from remaining OOS window; results annotated.

**Binance-only for MVP1** per Open Decision OD-6 (Kraken deferred to MVP2).

---

## 4. Data windows

All windows are **UTC**, inclusive of both endpoints unless noted.

| Window | Start | End | Duration | Purpose | Access rule |
| -------- | ------- | ----- | ---------- | --------- | ------------- |
| **Train** | 2020-01-01 | 2024-06-30 | ~54 months | Parameter tuning, signal exploration | Full read/write access during v0-v1 tuning |
| **Validation** | 2024-07-01 | 2025-08-31 | ~14 months | Model selection between competing versions | Touch sparingly; every touch logged in experiment lab notebook |
| **OOS Test** | 2025-09-01 | 2026-08-25 | ~12 months | Certification — H1 vs H0 | **Touched EXACTLY ONCE at certification.** Code physically refuses to read this window outside certification mode. |

### Regime coverage confirmation

Train + Validation + OOS spans:

- 2020 Q1: COVID crash + recovery
- 2020 Q4–2021: bull peak
- 2022: bear + Terra/Luna + FTX collapse
- 2023: SVB/USDC depeg + recovery
- 2024: ETF launch, August 5 volatility
- 2025–2026: current regime

If any of these regimes is later found to be underrepresented, the pre-registration is invalidated — not extended.

### Dataset fingerprints

Pending Week 1-2 data ingest. Every Parquet/SQLite file will receive a SHA256 fingerprint stored in `docs/experiments/swing-mvp1/data-manifest.json`. That manifest is hashed and its hash included in this document's v1 signature.

---

## 5. Strategy specification (FROZEN)

Source: SA-7 (Donchian + Chandelier + ADX + EMA regime filter).

### 5.1 Signal — Entry (long-only for MVP1)

- **Donchian breakout**: close > highest_high(20) on 4h timeframe
- **Regime filter**: close > EMA(200) on 1D timeframe (uptrend confirmation)
- **Momentum gate**: ADX(14) ≥ 25 on 4h timeframe (trend strength)
- All three conditions must be TRUE simultaneously on bar close.

### 5.2 Signal — Exit

- **Chandelier Exit**: trailing stop at highest_high(22) - 3 × ATR(22) on 4h timeframe
- **Time stop**: close position if not stopped out within 30 days (prevents dead-money positions)
- **Regime break**: close position if close < EMA(200) on 1D (regime shift)

### 5.3 Order type (execution)

- Entry: **market order** at bar close of signal bar (accept slippage; simplicity > sophistication for MVP1)
- Exit: **on-exchange stop-market** at chandelier level (posted immediately after entry fill); moved daily as trailing stop advances

### 5.4 Position sizing

- **Risk per trade**: 1% of experiment capital ($5,000 × 0.01 = $50)
- **Position notional**: $50 / (entry_price − chandelier_stop_price) × entry_price
- **Hard cap**: 5% of experiment capital per position ($250 notional max)
- **Kelly deferred**: Fractional Kelly (SA-1) NOT applied for MVP1 — activates only after ≥30 OOS trades exist to estimate edge (per SYNTHESIS T-1)

### 5.5 Concurrency + correlation

- **Max concurrent positions**: 3 (one per pair)
- **Correlation lock (SA-9 RO-01, softened)**: if 30-day rolling correlation between two open pairs exceeds 0.85, block new entry in the third correlated pair (existing positions unaffected)
- **Total exposure cap**: 15% of capital notional (3 × 5%)

---

## 6. Fee + execution model

### 6.1 Fees (point-in-time)

- Binance retail tier (VIP0) at the timestamp of each historical trade
- **BNB discount OFF** (conservative; recovered edge if operator enables it in production)
- Fee history reconstructed from Wayback Machine snapshots into `docs/experiments/swing-mvp1/fee-history.csv`

### 6.2 Slippage model (backtest)

- OHLCV-only backtest (per Open Decision OD-1 resolution: Option C)
- Entry slippage: 5 bps over bar close (conservative retail assumption)
- Exit slippage on stop: 15 bps over stop trigger price (accounts for stop-hunt + gap risk)
- Shadow phase will replace this with `OrderbookWalkFillModel` fed by live L2 collector

### 6.3 Fill assumptions

- No partial fills modeled in backtest (order fills fully at slippage-adjusted price OR is rejected)
- No re-quote logic; if PRICE_FILTER would reject, trade is skipped (SA-3)

---

## 7. Certification lifecycle

| State | Entry criteria | Exit criteria |
| ------- | ---------------- | --------------- |
| `UNTRUSTED` | Default | See below |
| `BACKTEST_CERTIFIED` | Backtest metrics (§9) pass on Train+Val windows | Any parameter change → back to UNTRUSTED |
| `SHADOW_CERTIFIED` | 90-day live shadow run: sim-vs-live drift < 30 bps p50 (SA-5) | Any drift threshold breach → back to UNTRUSTED |
| `OOS_CERTIFIED` | H1 accepted on OOS window (§1) — one-shot test | Cannot revert; result is permanent for this experiment |
| `LIVE_APPROVED` | OOS_CERTIFIED + operator sign-off | Any kill trigger (§10) → HALT |
| `CERTIFIED` | 90-day live capital run with no kill trigger fired | Ongoing monitoring per §10 |

---

## 8. Statistical framework

### 8.1 Primary test

- **Deflated Sharpe Ratio (DSR)** — López de Prado (2014). Adjusts for:
  - Sample length (annualized Sharpe from limited trades)
  - Skewness + kurtosis of returns (fat tails penalized)
  - **Number of trials** — every backtest run during train + val counts as a trial; total trial count logged in experiment notebook

### 8.2 Confidence intervals

- **Bootstrap CI** on OOS Sharpe, 10,000 resamples, block bootstrap with block length = 30 days (preserves autocorrelation)
- H1 accepted only if lower bound of 95% CI > 0

### 8.3 Cross-validation (train + val only — NEVER OOS)

- **Walk-forward analysis** — expanding window, retrain every 6 months, test on next 3 months
- **Purged combinatorial cross-validation** (López de Prado) — 5-fold with 5-day purge + 3-day embargo around fold boundaries (prevents leakage from overlapping labels)

### 8.4 Multiple-testing correction

- If any secondary metric is added after v1 signing, a Bonferroni correction is applied to significance thresholds
- Every added test is logged with penalty in experiment notebook

---

## 9. Metrics + thresholds

### Primary (must pass for LIVE_APPROVED)

| Metric | Threshold | Rationale |
| -------- | ----------- | ----------- |
| DSR on OOS | > 0.7 | SA-5 default; López de Prado suggests > 0.95 for institutional; 0.7 is retail-viable floor |
| Bootstrap 95% CI lower bound on OOS Sharpe | > 0 | Ensures Sharpe > 0 is not statistical noise |
| OOS trade count | ≥ 30 | Minimum sample for CI meaningfulness (López de Prado) |

### Secondary (all must pass; failures kill the strategy)

| Metric | Threshold | Rationale |
| -------- | ----------- | ----------- |
| Max drawdown (OOS) | < 15% | Kill trigger threshold; if backtest already breaches, live will breach worse |
| Time-to-recovery from any drawdown ≥ 8% | < 90 days | Path-dependency guard; psychological survivability |
| Profit factor | > 1.3 | Winners / losers ratio; > 1.3 provides margin above break-even |
| Ulcer index | < 8 | Peter Martin's drawdown-weighted risk metric; below 8 is retail-acceptable |
| Total fees / gross PnL | < 30% | If fees eat > 30% of gross, the strategy is fragile to fee-tier changes |
| Win rate | ≥ 30% | Below 30% requires unrealistically asymmetric R:R (typically > 4:1) |

---

## 10. Kill criteria

### Backtest phase kill (do not proceed to shadow)

- DSR on Train+Val < 0.7
- Any secondary metric fails
- Max drawdown ≥ 15% during train+val

### Shadow phase kill (do not proceed to live)

- Sim-vs-live PnL divergence > 30 bps p50 sustained 7 rolling days (SA-5)
- Any SA-9 shock overlay rule fires 3+ times in 30 days (indicates model brittleness)
- Shadow-mode Sharpe < 0.3 for 60 consecutive days

### Live phase kill (HALT strategy — declare dead)

- **Max drawdown 15% peak-to-trough** on experiment capital
- Time-to-recovery > 90 days from any drawdown ≥ 8%
- 30 rolling trades OOS Sharpe < 0.3
- Any SA-9 P0 rule violation (correlation lock breach, spread-spike, depeg, stale-book, exchange downtime > 5 min during open position)
- Two consecutive weeks of live fills diverging > 50 bps from same-strategy shadow

### Post-kill protocol

- Kill = strategy declared DEAD for this pre-registration
- Post-mortem written within 7 days of kill
- New pre-registration required to attempt a variant

---

## 11. Deviation protocol

### What counts as a deviation (invalidates this pre-registration)

- Any change to §3 asset universe
- Any change to §4 data windows
- Any change to §5 strategy spec (signals, filters, sizing)
- Any change to §6 fee model or slippage assumptions
- Any change to §9 primary metric or threshold
- Any change to §10 kill criteria

### What does NOT count as a deviation

- Adding a secondary metric — permitted with Bonferroni penalty logged
- Bug fixes in the engine that do not change strategy math
- Data ingest updates (new bars accrue in real time; historical never modified)

---

## 12. Experiment lab notebook

Location: `docs/experiments/swing-mvp1/lab-notebook.md`
Format: append-only, event-sourced (extends SA-8's event log discipline)
Every entry: timestamp (UTC ISO 8601), phase (train/val/OOS/shadow/live), action, observed metrics, decision, rationale.

**This is the anti-p-hacking mechanism.** If a metric result triggers a rules deviation, the notebook records it and the pre-registration is invalidated.

---

## 13. Roles

| Role                | Person          | Responsibility                                                                                                                             |
|---------------------|-----------------|--------------------------------------------------------------------------------------------------------------------------------------------|
| **Theorist**        | Operator        | Owns strategy hypothesis, sets thresholds, signs pre-registration, makes kill-trigger calls                                                |
| **Experimentalist** | Claude + engine | Owns instrumentation, runs backtest/shadow/live, enforces data-split walls, monitors kill triggers, reports results without interpretation |

The theorist does not touch the OOS data before certification.
The experimentalist does not adjust thresholds or kill criteria mid-experiment.

---

## 14. Signatures

To be added at v1 freeze:

- Operator signature + timestamp: _______________
- SHA256 hash of this document: _______________
- SHA256 hash of `data-manifest.json`: _______________
- Git commit SHA of pre-registration-v1.md: _______________

Once signed, this document is IMMUTABLE. Any change means: create v2, kill v1's experiment, start over.

---

## 15. Open items requiring operator sign-off before v1 freeze

1. Confirm data windows (§4) — especially OOS end date, since we're currently AT the OOS end.
2. Confirm 3-pair universe (§3) — do you want to add or remove any?
3. Confirm correlation lock threshold at 0.85 (§5.5) — SA-9 suggested 0.80, I softened to 0.85 to permit some diversification.
4. Confirm ulcer index threshold of 8 (§9) — this is a subjective psychological tolerance number.
5. Confirm 30-day time-stop (§5.2) — arbitrary but honest. Data can inform this in v2.
6. Confirm role assignment (§13) — is the "theorist" you alone, or you + advisors?

---

**End of pre-registration v0.**

Next: operator reviews §15 items, we produce v1, hash it, git-commit it, and the experiment begins.
