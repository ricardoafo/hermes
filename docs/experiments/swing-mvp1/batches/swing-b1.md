---
batch_id: swing-b1
experiment: swing-mvp1
status: SIGNED
signed_by: ricardoafo
date_signed: 2026-10-19
content_sha256: 7ea7c4b912f9beb33c81a72c49a415279df91d7e7c07fae71d4385fad3b81211
content_sha256_scope: "git show c9c4a23c5209bb1b6ca531ebb8222a76a9dc4726:docs/experiments/swing-mvp1/batches/swing-b1.md | sha256sum"
signing_commit_precursor: c9c4a23c5209bb1b6ca531ebb8222a76a9dc4726
code_commit: c9c4a23c5209bb1b6ca531ebb8222a76a9dc4726
selection:
  eligible: full_gate_train_validation
  fdr: benjamini_hochberg
  fdr_q: 0.05
  rank: dsr
  tie_break: lowest_mean_return_correlation
backtest_funnel:
  dsr_min: 0.95
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
candidates:
  - strategy_id: swing-b1-ensemble
    idea_family: breakout-ensemble
    card: docs/experiments/swing-mvp1/cards/swing-b1-ensemble.md
    card_sha256: 8aef0d5f243df1ba47e36c38b445879a9a070b3e5480446327ead83cd0ec6fb7
    section: C1
    summary: |
      Long when 4h breakouts over several lookbacks agree and the daily close is above its EMA(200).
      Mechanism: trend returns come from several horizons, so agreement filters false single-length breakouts.
      Expected: fewer trades than v2, better t-statistic, bear stretch toward flat.
    code_paths:
      - src/hermes/domain/strategy/swing/ensemble.py
      - src/hermes/domain/strategy/swing/adapter.py
      - src/hermes/application/strategy_protocol/strategy_factory.py
      - config/strategies.yaml
    parameters:
      agree_min: {base: 2, neighbours: [1, 3]}
      chandelier_atr_mult: {base: "3.0", neighbours: ["2.5", "3.5"]}
      longest_lookback: {base: 120, neighbours: [90, 180]}
  - strategy_id: swing-b1-daily-ma
    idea_family: daily-ma-trend
    card: docs/experiments/swing-mvp1/cards/swing-b1-daily-ma.md
    card_sha256: 37105a08433aebbc3e5e0d10553563706988a8383a8277cd025946d119d69d74
    section: C2
    summary: |
      Long after a daily close crosses above its SMA(N), executed on the 00:00 UTC 4h bar; out below it.
      Mechanism: the simplest time-series momentum rule stays in persistent rallies and steps aside in declines.
      Expected: bear stretch near flat, more than half the years positive, low win rate with large winners.
    code_paths:
      - src/hermes/domain/strategy/swing/daily_ma.py
      - src/hermes/application/strategy_protocol/strategy_factory.py
      - config/strategies.yaml
    parameters:
      sma_period: {base: 100, neighbours: [50, 150]}
      stop_atr_mult: {base: "3.0", neighbours: ["2.5", "3.5"]}
  - strategy_id: swing-b1-trend-slope
    idea_family: donchian-breakout-4h
    card: docs/experiments/swing-mvp1/cards/swing-b1-trend-slope.md
    card_sha256: 0663b74fb4aa7e16ee0281deec82e5a8be5a47a9a65f812a72edba60ae516043
    section: C3
    summary: |
      v2's 4h Donchian breakout, plus a filter that the daily EMA(200) itself is rising.
      Mechanism: the v2 post-mortem infers bear-market rallies above the EMA(200) caused its failed breakouts.
      Expected: bear stretch toward flat, fewer trades than v2, bull stretches still positive.
    code_paths:
      - src/hermes/domain/strategy/swing/trend_slope.py
      - src/hermes/domain/strategy/swing/adapter.py
      - src/hermes/application/strategy_protocol/strategy_factory.py
      - config/strategies.yaml
    parameters:
      ema_slope_lookback: {base: 20, neighbours: [10, 40]}
      donchian_period: {base: 20, neighbours: [15, 25]}
      chandelier_atr_mult: {base: "3.0", neighbours: ["2.5", "3.5"]}
  - strategy_id: swing-b1-pullback
    idea_family: pullback-in-uptrend
    card: docs/experiments/swing-mvp1/cards/swing-b1-pullback.md
    card_sha256: 79efcbb0982378d6ef96af1676bef9dca4e5a561f4a596d1a7f47a70414029e7
    section: C4
    summary: |
      Inside a rising daily uptrend, long when 4h RSI(14) crosses back above T after an oversold close.
      Mechanism: short pullbacks in an established uptrend tend to be bought back, giving better entry prices.
      Expected: higher win rate and trade count than v2, smaller average win, bear stretch near flat.
    code_paths:
      - src/hermes/domain/strategy/swing/pullback.py
      - src/hermes/domain/strategy/swing/engines/rsi.py
      - src/hermes/application/strategy_protocol/strategy_factory.py
      - config/strategies.yaml
    parameters:
      rsi_reentry: {base: 35, neighbours: [30, 40]}
      stop_atr_mult: {base: "2.0", neighbours: ["1.5", "2.5"]}
      time_stop_days: {base: 20, neighbours: [10, 30]}
---

# Research batch swing-b1: swing-mvp1

> The first research batch under ADR-026. Four candidates, one per idea family, written BEFORE any of their runs.
> The Code Owner signs the whole batch by approving and merging its signing PR. Once signed it is never edited; a
> correction is a new batch. Each candidate's card (front matter `card`, `card_sha256`) is part of its
> pre-registration.
>
> **Status: DRAFT.** `code_paths` are final; `code_commit` is filled in the signing commit, after the four candidates' code merged (issues
> #1063 to #1066), before signing.

## Selection

The champion rule of ADR-026 §5, fixed by the `selection` block before any run: eligible are the candidates that pass
the full gate on Train + Validation; Benjamini-Hochberg at q = 0.05 over every tested candidate (m = 4), with 1 - DSR
as the p-value and p = 1 for a candidate with no DSR; the champion is the survivor with the highest DSR, and a tie
goes to the lowest mean daily-return correlation with the other survivors. No survivor: no champion, and one
post-mortem covers the batch within 7 days.

## Common to every candidate

Each candidate section below refers to these rules as "Common".

- **Capital** (v2 §2): 5,000 USDT, risk on the full 5,000 denominator, no change during the experiment.
- **Universe** (v2 §3): BTC/USDT, ETH/USDT, SOL/USDT on Binance spot.
- **Windows** (v2 §4), UTC, both ends inclusive: Train 2020-09-01..2024-06-30; Validation 2024-07-01..2025-08-31;
  OOS 2025-09-01..2026-12-31, touched exactly once at certification on or after 2027-01-01. No bar from 2025-09-01
  on is read before then.
- **Daily EMA(200) slope** (C3, C4): one EMA(200) series over the 300-bar daily buffer, seeded as v2's regime
  filter; "S daily closes earlier" is that same series S values back.
- **Warm-up** (v2 §4): exactly 300 closed daily bars and 45 closed 4h bars before the first decision, plus whatever
  longer 4h history a candidate's lookback needs (C1: its longest lookback). No bar before 2020-09-01 is read.
- **Train and Validation** (ADR-026 §2, §3): merging this signed batch starts every candidate's Train run on
  server-01. The pipeline starts Validation only for a candidate whose Train funnel passes. One Validation run is one
  touch of the candidate's family budget of 5 (ADR-021 §5); v1's touches count against `donchian-breakout-4h`.
- **Orders** (v2 §5.3): entry is a market order at the signal bar's close; every exit is a market order at the close
  of the 4h bar on which the exit fired. No order rests on the exchange.
- **Sizing and caps** (v2 §5.4, §5.5): 1% of capital at risk to the candidate's stop, notional = risk / (entry −
  stop) × entry, 5% cap per position, at most 3 positions (one per pair), correlation lock at 0.80 on 30-day rolling
  correlation, 15% total exposure. A bar that closes well below the stop loses more than 1%; nothing caps it.
- **Fees and slippage** (v2 §6): Binance VIP0 at each trade's timestamp from `fee-history.csv`, BNB discount off;
  entry 5 bps over the signal close, every exit 5 bps under the exit bar's close; full fills or none.
- **Lifecycle and shadow** (v2 §7, ADR-021 §5): a shadow ends at 90 days and 30 round trips, whichever is later,
  capped at 180 days; reaching the cap first counts as a fail. Drift compares each fill with the model price above.
- **Statistics** (v2 §8.1-§8.4): DSR with N from every logged trial in `trial-log.jsonl` (v1, v2 and this batch);
  block bootstrap with 10,000 resamples and 30-day blocks; walk-forward and purged CV (5 folds, 5-day purge, 3-day
  embargo); a secondary metric added after signing is Bonferroni-corrected and logged.
- **Funnel replays** (v2 §8.5): each Train or Validation run also replays and logs as trials one neighbour per value
  in the candidate's `parameters` (one parameter moved at a time) and the base at 2× fees and slippage. The CSCV
  matrix for PBO is the base plus its neighbours, in 16 blocks.
- **Deviation** (v2 §11): any change to a candidate's rules, parameters, the windows, fees, sizing, §9, §10, the
  `selection` block or `backtest_funnel` is a new batch. Engine fixes that leave strategy math alone are not.
- **Lab notebook and trials** (v2 §12): entries carry `experiment_id: swing-mvp1` and `batch: swing-b1`; every
  trial line goes to `trial-log.jsonl` with `"batch.id": "swing-b1"` in its parameters.
- **Roles** (v2 §13, ADR-023 §7): the AI sees PASS/FAIL lines only. Results reach the repo through the workflow's bot
  PR (ADR-026 §6) as trial lines, PASS/FAIL notebook entries and verdict records with no metric values.

## C1 — swing-b1-ensemble

### 1. Hypothesis (C1)

H1: the C1 rules have statistically significant positive risk-adjusted return on the universe over the OOS window,
net of costs. H0: indistinguishable from zero after costs. H1 is accepted iff DSR on OOS > 0.7 and every §9 metric
passes. Card: `cards/swing-b1-ensemble.md`.

### 5. Strategy specification (C1)

- Entry (long only), on a 4h bar close, all true: for at least `agree_min` of the lookbacks {20, 55,
  `longest_lookback`} 4h bars, the close is above the highest high of the previous L bars; the last closed daily close
  is above the daily EMA(200).
- Exit, first to fire: v2 §5.2's chandelier stop with ATR multiple `chandelier_atr_mult` (posted when the position
  opens as highest_high(22) − multiple × ATR(22) from the 4h bars closed by the signal bar, moved once per UTC daily
  close, never down, triggered by a 4h low at or below the stop in force when the bar opened); v2's 90-day time
  stop; v2's regime break (a new daily close below the daily EMA(200)).
- Orders, sizing, caps, fees: Common.

### 9. Metrics and thresholds (C1)

#### Primary

Unchanged from v1: DSR on OOS > 0.7; bootstrap 95% lower bound on OOS Sharpe > 0; at least 30 OOS trades.

#### Secondary

Unchanged from v1: max drawdown < 15%; recovery from any 8% drawdown < 90 days; profit factor > 1.3; ulcer index
< 10; fees / gross PnL < 30%; win rate ≥ 30%.

### 10. Kill criteria (C1)

On Train + Validation: DSR < `dsr_min`; any §9 secondary metric fails; or any funnel check in `backtest_funnel`
fails (v2 §10 table). Shadow and live: unchanged from v1. A killed candidate is dead for this batch.

## C2 — swing-b1-daily-ma

### 1. Hypothesis (C2)

As C1, for the C2 rules. Card: `cards/swing-b1-daily-ma.md`.

### 5. Strategy specification (C2)

- Entry (long only), on the 4h bar that closes at 00:00 UTC, when flat: the daily close just completed is above the
  daily SMA(`sma_period`), and either the previous daily close was at or below it (a fresh cross) or the position was
  closed since the last daily close at or below it (re-entry after a stop exit).
- Exit, first to fire: on the 4h bar that closes at 00:00 UTC, the daily close just completed is below the daily
  SMA(`sma_period`); or a protective stop at the signal bar's close minus `stop_atr_mult` × daily ATR(22),
  recomputed at each UTC daily close as close − multiple × ATR(22) and raised to that level if higher, never down,
  triggered by a 4h low at or below the stop in force when the bar opened.
- A decision is taken only when the daily bar closing at that instant is in the buffer; a missing daily bar means no
  decision that day. Each 4h bar is tested against the stop in force when it opened; the 00:00 UTC close raises the
  stop only after its own bar is tested. If that bar both reaches the stop and closes below the SMA, the exit is
  recorded as the protective stop; both fill at the bar close. The re-entry right is set by an exit and cleared by
  an entry or by any decision close at or below the SMA; an entry that does not fill gives none. An entry whose stop
  would be at or below zero is not taken.
- Sizing uses the protective stop distance at entry. Orders, caps, fees: Common.

### 9. Metrics and thresholds (C2)

#### Primary

Unchanged from v1: DSR on OOS > 0.7; bootstrap 95% lower bound on OOS Sharpe > 0; at least 30 OOS trades.

#### Secondary

Unchanged from v1: max drawdown < 15%; recovery from any 8% drawdown < 90 days; profit factor > 1.3; ulcer index
< 10; fees / gross PnL < 30%; win rate ≥ 30%.

### 10. Kill criteria (C2)

As C1.

## C3 — swing-b1-trend-slope

### 1. Hypothesis (C3)

As C1, for the C3 rules. Card: `cards/swing-b1-trend-slope.md`.

### 5. Strategy specification (C3)

- Entry (long only), on a 4h bar close, all true: the close is above the highest high of the previous
  `donchian_period` 4h bars; the last closed daily close is above the daily EMA(200); ADX(14) on 4h ≥ 25; and the
  daily EMA(200) at the last daily close is above its value `ema_slope_lookback` daily closes earlier.
- Exit: v2 §5.2 unchanged, with ATR multiple `chandelier_atr_mult`.
- Orders, sizing, caps, fees: Common.
- Family `donchian-breakout-4h`, shared with v1 and v2 (ADR-026 §1): its Validation touch counts against that
  family's budget of 5.

### 9. Metrics and thresholds (C3)

#### Primary

Unchanged from v1: DSR on OOS > 0.7; bootstrap 95% lower bound on OOS Sharpe > 0; at least 30 OOS trades.

#### Secondary

Unchanged from v1: max drawdown < 15%; recovery from any 8% drawdown < 90 days; profit factor > 1.3; ulcer index
< 10; fees / gross PnL < 30%; win rate ≥ 30%.

### 10. Kill criteria (C3)

As C1.

## C4 — swing-b1-pullback

### 1. Hypothesis (C4)

As C1, for the C4 rules. Card: `cards/swing-b1-pullback.md`.

### 5. Strategy specification (C4)

- Trend, at the last daily close: the daily close is above the daily EMA(200), and the daily EMA(200) is above its
  value 20 daily closes earlier.
- Entry (long only), on a 4h bar close while the trend holds: RSI(14) on 4h closes at or above `rsi_reentry` after
  the previous 4h bar's RSI(14) closed below it. RSI(14) is Wilder's, computed over the last 29 4h bars ending at
  that bar, so it fits the 45-bar warm-up.
- Exit, first to fire: 4h RSI(14) closes above 70; a protective stop at the signal bar's close minus `stop_atr_mult` ×
  4h ATR(22) on the signal bar, fixed for the trade, triggered by a 4h low at or below it; a time stop of
  `time_stop_days` calendar days from the entry bar's close, firing on the first 4h bar closing at or after that
  instant; a daily close below the daily EMA(200), firing on the 4h bar that closes at that daily close. A turn in the
  EMA slope alone does not exit. When several exits fire on one bar, all fill at its close; the exit is labelled in
  the order above. The entry bar is never tested for an exit.
- Sizing uses the protective stop distance. Orders, caps, fees: Common.

### 9. Metrics and thresholds (C4)

#### Primary

Unchanged from v1: DSR on OOS > 0.7; bootstrap 95% lower bound on OOS Sharpe > 0; at least 30 OOS trades.

#### Secondary

Unchanged from v1: max drawdown < 15%; recovery from any 8% drawdown < 90 days; profit factor > 1.3; ulcer index
< 10; fees / gross PnL < 30%; win rate ≥ 30%.

### 10. Kill criteria (C4)

As C1.

## Trial budget

One Train touch logs 8 trials for C1, C3 and C4 and 6 for C2: 30 trial lines for the batch, all added to the same
log as v1 and v2, so N grows for every candidate (inferred: a pass is harder than for a lone candidate, which the
batch-wide BH step also accounts for).

## Change log

| Version | Date | Change |
| --- | --- | --- |
| swing-b1 | 2026-10-17 | Draft (issue #1062). |

## Signatures

Filled in the signing commit: operator, timestamp, `content_sha256` of the precursor, and `code_commit`.
