---
card_id: swing-b1-daily-ma
idea_family: daily-ma-trend
batch: first research batch (issue #1062)
status: DRAFT
---

# Card: daily price against its moving average, 4h for execution

## Mechanism

A daily close above its moving average is the simplest time-series momentum rule, and on crypto it is documented to
stay in during persistent rallies and step aside in long declines (lab scan of 2026-10-06, family 3). Deciding on the
daily close removes the 4h breakout noise; the 4h bar is used only to execute.

## Rules

- Entry (long only): at the 4h bar that closes at 00:00 UTC, when the daily close is above the daily SMA(N) and was at
  or below it on the previous daily close (a fresh cross), or on the first daily close above it after an exit.
- Exit: the 4h bar that closes at 00:00 UTC after a daily close below the daily SMA(N); or a protective stop at the
  entry fill minus M × daily ATR(22), moved once per daily close, never down, triggered by a 4h low as in v2 §5.2.
- Sizing uses the protective stop distance, as in v2 §5.4.

## Free parameters (2)

| Parameter | Base | Neighbours |
| --- | --- | --- |
| N, SMA length in daily closes | 100 | 50, 150 |
| M, protective stop in daily ATRs | 3.0 | 2.5, 3.5 |

Trials per Train touch: base, 4 neighbours, 2× costs = 6.

## Expected sign

- Bear stretch: near flat, because the rule is mostly out of the market below its average.
- Positive years: more than half.
- Win rate: low, with large winners (trend-following profile); the §9 win-rate floor of 30% may be the binding check.

## Falsification test

Rejected if, on Train, the bear-stretch check or the positive-years check fails.
