---
name: Pre-Registration — Swing MVP1 v2
version: 2
supersedes: pre-registration-v1.md
author: Claude (drafted 2026-10-17 from v1, the #735 audit and the Code Owner's decisions of 2026-10-17)
date_drafted: 2026-10-17
date_signed: null
experiment_id: swing-mvp1-v2
strategy_id: swing-mvp1-v2
status: DRAFT
signed_by: null
content_sha256: null
backtest_funnel:
  train_end: 2024-06-30
  oos_start: 2025-09-01
  cscv_blocks: 16
  pbo_max: 0.05
  t_stat_above: 3.0
  trade_count_min: 100
  cost_stress_multiplier: 2.0
  positive_year_share_above: 0.5
  validation_to_train_return_min: 0.5
  neighbour_sharpe_ratio_min: 0.5
  return_correlation_below: 0.7
  regimes:
    - {kind: bull, start: 2020-10-01, end: 2021-11-10}
    - {kind: bear, start: 2021-11-11, end: 2022-11-21}
    - {kind: bull, start: 2022-11-22, end: 2024-03-14}
---

# Pre-Registration Document — Swing Strategy MVP1, v2

> v2 is v1 with its known engine defects fixed and its exit made executable by the shadow engine. It is a new
> experiment: v1's results do not carry over, and v1's pre-registration stays frozen as signed. This document is
> written BEFORE any v2 backtest. Once signed and hashed, any deviation is a new experiment (§11).
>
> **Status: DRAFT.** The Code Owner signs it by approving its PR; the signing commit records the content hash (§16).

---

## 0. What changes from v1

| # | Section | v1 (signed 2026-08-25) | v2 | Why |
| --- | --- | --- | --- | --- |
| 1 | §5.2 stop | Chandelier level recomputed on every 4h bar, including the bar it is tested on | Posted at the entry fill, moved once per UTC daily close, never down; the bar being tested never moves it | Issue #785: 27 of 154 Train exits fired only because a bar raised the stop with its own high and then reached it with its own low |
| 2 | §5.3 / §6.2 exit | On-exchange stop-market; backtest fill 15 bps under the stop | Market sale at the close of the 4h bar whose low reached the stop; fill 5 bps under that close, in the backtest and in shadow | Paper shadow cannot rest a stop order, so it sells at the close; pricing exits at the stop made exit drift 70-390 bps (#796) |
| 3 | §5.1 warm-up | Daily EMA(200) seeded from a 200- to 300-bar buffer | Exactly 300 closed daily bars of warm-up, everywhere | Issue #880: 200 and 300 bars could decide differently |
| 4 | §9 / §10 backtest gate | DSR > 0.7 and the §9 secondary metrics | DSR ≥ 0.95 plus the ADR-021 §5 funnel (front matter `backtest_funnel`) | ADR-021 §5 binds every strategy from v2 on |
| 5 | §7 shadow drift | Fill drift against the §6.2 model price | Same rule; with change 2 the exit model price is the close-based price shadow actually trades at | Makes the 30 bps p50 rule meaningful for exits |

Everything else (universe, windows, entry rules, sizing, caps, fees, OOS rules, roles) is unchanged from v1.

---

## 1. Hypothesis

### Primary (H1)

The strategy defined in §5 has statistically significant positive risk-adjusted return on the asset universe (§3),
over the OOS test window (§4), NET of realistic fees and slippage (§6), at the position sizing (§5.4).

### Null (H0)

Its risk-adjusted return is indistinguishable from zero (or from buy-and-hold) after transaction costs, over the same
OOS window.

### Decision rule

- **H1 accepted** iff DSR on OOS > 0.7 AND every §9 metric passes.
- **H0 not rejected** otherwise → UNTRUSTED → no live capital.

The stricter DSR ≥ 0.95 and the funnel apply to the backtest gate (§10), not to the OOS decision.

### What this experiment CANNOT answer

- Whether the strategy works in regimes the OOS window does not contain.
- Whether it is optimal or the best strategy, only whether it is viable.
- Whether v1's failure was caused by the defects fixed here. v2 passing would be consistent with that, not proof.

---

## 2. Experimental capital

Unchanged from v1: **5,000 USDT**, risk percentages on the full 5,000 denominator, no capital change during the
experiment.

---

## 3. Asset universe (FIXED)

Unchanged from v1: BTC/USDT, ETH/USDT, SOL/USDT on Binance spot. No additions during the experiment.

---

## 4. Data windows (FROZEN)

Unchanged from v1, UTC, both ends inclusive.

| Window | Start | End | Access rule |
| --- | --- | --- | --- |
| **Train** | 2020-09-01 | 2024-06-30 | Full use for development |
| **Validation** | 2024-07-01 | 2025-08-31 | One logged touch per Code Owner-started workflow run (ADR-023 §5) |
| **OOS Test** | 2025-09-01 | 2026-12-31 | Touched exactly once, at certification on or after 2027-01-01 |

### Validation budget

v2 belongs to the v1 idea family (Donchian breakout with a chandelier stop). ADR-021 §5 gives a family 5 Validation
touches. The v1 record counts up to 2 as used (the 2026-10-08 gate run, and possibly the 2026-08-26 per-pair runs).
**v2 has at most 3 Validation touches.** One touch is one Validation workflow run, including the neighbour and cost
stress replays §8.5 pre-registers for it. After the third, Validation counts as in-sample for this family.

### Warm-up

Every run, backtest or serve, warms up with exactly **300 closed daily bars** and 45 closed 4h bars before its first
decision. Before 2020-09-01 no bars are read; the first Train decisions wait for the warm-up to fill, as in v1.

---

## 5. Strategy specification (FROZEN)

### 5.1 Entry (long only)

Unchanged from v1:

- **Donchian breakout**: 4h close > highest high of the previous 20 4h bars.
- **Regime filter**: last closed 1D close > EMA(200) on 1D.
- **Momentum gate**: ADX(14) ≥ 25 on 4h.
- All three true on the same 4h bar close. Entry is a market order at that bar's close.

### 5.2 Exit (first condition to fire wins)

- **Chandelier stop** (changed): the stop is posted at the entry fill as highest_high(22) − 3 × ATR(22), computed
  from the 4h bars closed by then, entry bar included. At each UTC daily close while the position is held, the level
  is recomputed from the 4h bars closed by then and the stop becomes the higher of the stop in force and that level.
  The stop never moves down, and the bar it is tested on never moves it. **Trigger**: a 4h bar whose low is at or
  below the stop in force when that bar opened.
- **Time stop**: 90 calendar days from the entry bar's close.
- **Regime break**: a new 1D close below EMA(200) on 1D.

### 5.3 Order type (changed)

- Entry: market order at the signal bar's close.
- Every exit, stop exits included: **market order at the close of the 4h bar on which the exit condition fired.**
  No order rests on the exchange. In live trading a protective exchange stop may be added as an operational safety
  net, but it is not part of the strategy and its fills are reported separately.

### 5.4 Position sizing

Unchanged from v1: 1% of capital at risk per trade, notional = risk / (entry − stop) × entry, 5% cap per position.

### 5.5 Concurrency and correlation

Unchanged from v1: at most 3 positions (one per pair), correlation lock at 0.80 on 30-day rolling correlation,
15% total exposure cap.

---

## 6. Fee and execution model

### 6.1 Fees

Unchanged from v1: Binance VIP0 at each trade's timestamp, BNB discount off, from `fee-history.csv`.

### 6.2 Slippage model (changed)

- Entry: 5 bps over the signal bar's close.
- Every exit: **5 bps under the close of the bar the exit fired on.**
- Cost stress (§8.5): every fee and slippage above × 2.

### 6.3 Fill assumptions

Unchanged from v1: full fills or none, no re-quotes; a trade the exchange filters would reject is skipped.

---

## 7. Certification lifecycle

Unchanged from v1 (UNTRUSTED → BACKTEST_CERTIFIED → SHADOW_CERTIFIED → OOS_CERTIFIED → LIVE_APPROVED → CERTIFIED),
with two v2 rules:

- `BACKTEST_CERTIFIED` needs §10's backtest gate, which includes the funnel.
- Shadow follows ADR-021 §5: it ends at 90 days and 30 round trips, whichever is later, capped at 180 days; reaching
  the cap first is inconclusive and counts as a fail. Shadow drift (SA-5) compares each fill with the §6.2 model
  price for that fill, so exits are compared with the bar close less 5 bps.

---

## 8. Statistical framework

### 8.1 Primary test

Deflated Sharpe Ratio (López de Prado 2014). N is the implied number of independent trials across every logged trial
on this dataset (ADR-012 D-11), v1's trials included.

### 8.2 Confidence intervals

Unchanged from v1: block bootstrap, 10,000 resamples, 30-day blocks; H1 needs a 95% lower bound above 0.

### 8.3 Cross-validation (Train and Validation only, never OOS)

Unchanged from v1: walk-forward and purged combinatorial CV (5 folds, 5-day purge, 3-day embargo).

### 8.4 Multiple testing

Unchanged from v1: a secondary metric added after signing is Bonferroni-corrected and logged.

### 8.5 Funnel replays (new)

Each backtest run of v2, Train or Validation, also replays and logs as trials:

- **Neighbours**, one parameter moved at a time: Donchian 15 and 25; ADX gate 20 and 30; chandelier multiple 2.5
  and 3.5. Six neighbours.
- **Cost stress**: the base parameters with fees and slippage × 2.

The CSCV trial matrix for PBO is the base run plus its six neighbours, in 16 blocks.

---

## 9. Metrics and thresholds

### Primary (must pass for LIVE_APPROVED)

Unchanged from v1: DSR on OOS > 0.7; bootstrap 95% lower bound on OOS Sharpe > 0; at least 30 OOS trades.

### Secondary (all must pass)

Unchanged from v1: max drawdown < 15%; recovery from any 8% drawdown < 90 days; profit factor > 1.3; ulcer index
< 10; fees / gross PnL < 30%; win rate ≥ 30%.

---

## 10. Kill criteria

### Backtest phase (do not proceed to shadow)

On Train + Validation, any of:

- DSR < 0.95;
- any §9 secondary metric fails, or max drawdown ≥ 15%;
- any funnel check fails, with the thresholds in the front matter `backtest_funnel` block, printed by `hermes funnel`:

| Check | Requirement |
| --- | --- |
| PBO (CSCV, 16 blocks) | ≤ 0.05 |
| t-statistic of daily returns | > 3 |
| Trades, Train + Validation | ≥ 100 |
| Cost stress (fees and slippage × 2) | Total return still positive |
| Calendar years with a positive return | More than half |
| Bull stretches 2020-10-01..2021-11-10 and 2022-11-22..2024-03-14 | Positive compounded return |
| Bear stretch 2021-11-11..2022-11-21 | Positive compounded return |
| Validation annualised return | At least half of Train's, Train positive |
| Each §8.5 neighbour's Sharpe | At least half the base Sharpe |
| Daily return correlation with strategies already running | Below 0.7 (none run once v1 is retired) |

### Shadow phase and live phase

Unchanged from v1, with the shadow length of §7.

### Post-kill protocol

Unchanged from v1: dead for this pre-registration, post-mortem within 7 days, a variant needs a new
pre-registration.

---

## 11. Deviation protocol

Unchanged from v1. Counts as deviation: any change to §3, §4, §5, §6, §9, §10 or the `backtest_funnel` block.
Does not count: an added secondary metric (with its penalty logged), engine fixes that do not change strategy math,
new bars accruing.

---

## 12. Lab notebook

`docs/experiments/swing-mvp1/lab-notebook.md`, append-only and hash-verified. v2 entries carry phase and
`experiment_id: swing-mvp1-v2`. Every v2 trial goes to `trial-log.jsonl`, the same chained log as v1's.

---

## 13. Roles

Unchanged from v1. The AI sees PASS/FAIL lines only, never observed metrics (ADR-012 D-06).

---

## 14. Engineering needed before the first v2 run

Not part of the protocol; listed so signing does not wait on it.

- Strategy configuration `swing-mvp1-v2` with the §5 parameters and the close-based exit of §5.3 / §6.2 in both
  the backtest driver and serve (#785 / PR #955 prices stop exits at min(stop, open); v2 needs its own exit price).
- Backtest driver and `backtest.yml`: the §8.5 replays, the funnel evidence file, and a `hermes funnel` step
  (PR #964, Design §5).
- `hermes certify` reading v2's DSR ≥ 0.95 (#361).

---

## 15. Change log

| Version | Date | Change |
| --- | --- | --- |
| v1 | 2026-08-25 | Signed. Gate failed on 2026-10-08 (issue #735). |
| v2 | 2026-10-17 | Draft: the changes in §0. |

---

## 16. Signatures

**Unsigned draft.** The Code Owner signs by approving the PR that sets `status: SIGNED`, `date_signed`, `signed_by`
and the content hash, computed as for v1: `git show <pre-signing commit>:docs/experiments/swing-mvp1/pre-registration-v2.md | sha256sum`.
