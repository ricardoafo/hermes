---
card_id: swing-b1-pullback
idea_family: pullback-in-uptrend
batch: first research batch (issue #1062)
status: DRAFT
---

# Card: buying pullbacks inside a daily uptrend

## Mechanism

Inside an established uptrend, short pullbacks tend to be bought back, so entries on a 4h oversold reading carry a
better price than breakout entries and a different return pattern (lab scan of 2026-10-06, family 6). It is the
batch's least correlated idea with v1 (inferred).

## Rules

- Trend: the daily close is above the daily EMA(200), and the daily EMA(200) is above its value 20 daily closes
  earlier.
- Entry (long only): on a 4h bar close, RSI(14) on 4h crosses back above T after closing below T.
- Exit, first to fire: 4h RSI(14) closes above 70; a protective stop at the entry fill minus M × 4h ATR(22), fixed,
  triggered by a 4h low, priced at the bar close (v2 §6.2); a time stop of 20 days; a daily close below the daily
  EMA(200).
- Sizing uses the protective stop distance.

## Free parameters (3)

| Parameter | Base | Neighbours |
| --- | --- | --- |
| T, RSI re-entry level | 35 | 30, 40 |
| M, protective stop in 4h ATRs | 2.0 | 1.5, 2.5 |
| Time stop in days | 20 | 10, 30 |

## Expected sign

- Win rate: higher than v2's; average win smaller.
- Trade count: higher than v2's.
- Bear stretch: near flat, since the trend condition is rarely true there.

## Falsification test

Rejected if, on Train, the cost-stress check or the t-statistic check fails: a pullback edge that does not survive
2× costs is too thin to trade.
