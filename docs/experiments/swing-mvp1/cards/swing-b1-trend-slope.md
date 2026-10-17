---
card_id: swing-b1-trend-slope
idea_family: donchian-breakout-4h
batch: first research batch (issue #1062)
status: DRAFT
---

# Card: v2's breakout with a rising daily trend filter

## Mechanism

The v2 post-mortem (PR #1048) infers that v2 lost in the 2021-11-11..2022-11-21 bear stretch because bear-market
rallies closed above the daily EMA(200) and triggered breakouts that failed. Requiring the daily EMA(200) itself to
be rising should keep the strategy flat through those rallies while persistent uptrends stay tradable.

## Rules

- Entry: v2 §5.1 unchanged, plus: today's daily EMA(200) is above the daily EMA(200) of S daily closes earlier.
- Exit: v2 §5.2 unchanged.

## Free parameters (3)

| Parameter | Base | Neighbours |
| --- | --- | --- |
| S, slope lookback in daily closes | 20 | 10, 40 |
| Donchian length (4h bars) | 20 | 15, 25 |
| Chandelier ATR multiple | 3.0 | 2.5, 3.5 |

## Expected sign

- Bear stretch: from FAIL toward a positive or flat return, from fewer bear-rally entries.
- Trade count: lower than v2's.
- Bull stretches: still positive, slightly lower from later entries.

## Falsification test

Rejected if, on Train, the bear-stretch check or the trade-count check still fails.

## Notes

- Same entry signal family as v1 and v2, `donchian-breakout-4h` (ADR-026 §1). Its Validation touches share their family's budget of 5.
